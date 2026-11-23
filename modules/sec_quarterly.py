"""
Tutti i valori depositati alla SEC e i trimestri singoli (dallo stesso JSON "companyfacts" di sec_edgar.py).

- `extract_all_facts`: ogni valore dei 10-K e 10-Q (tutte le etichette: us-gaap, dei, ifrs, ...), in formato
  lungo, per l'archivio completo (`data/sec_facts.db`, classe `FactsStore`).
- `quarterly_values`: per ogni etichetta, il valore del singolo trimestre:
    - il trimestre dichiarato (durata 80-100 giorni), se c'è;
    - altrimenti la differenza tra due valori "da inizio esercizio" con lo stesso inizio: 6 mesi - 3 mesi,
      9 mesi - 6 mesi, esercizio - 9 mesi (il quarto trimestre, che quasi nessuno deposita a parte). Il
      rendiconto finanziario dei 10-Q è sempre "da inizio esercizio", quindi serve quasi sempre. Solo per gli
      importi in valuta: utili per azione e medie delle azioni non si sottraggono;
    - i valori istantanei (stato patrimoniale) alla data di chiusura.
  Point-in-time: ogni valore ha la data di deposito del documento che lo rende calcolabile; i documenti
  successivi (confronti con l'anno prima, dati rivisti) aggiungono una versione solo se il valore cambia.
- `standard_quarters`: le voci normalizzate del sistema (ricavi, utile, free cash flow, debito, ...) per
  trimestre, con le stesse etichette e regole dei bilanci annuali (`sec_edgar._flow_metrics`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.sec_edgar import (DEI_SHARES, DILUTED_SHARES, DURATION_CONCEPTS, INSTANT_CONCEPTS, _balance_metrics,
                               _cover_reference, _flow_metrics, _scale_for_document)

REPORT_FORMS = ("10-K", "10-Q")       # comprese le varianti (10-K/A, 10-KT, 10-Q/A)
QUARTER_DAYS = (80, 100)              # durata di un trimestre (anche negli esercizi da 52/53 settimane)
CUMULATIVE_MAX_DAYS = 400             # "da inizio esercizio": fino all'esercizio intero
QUARTERLY_SOURCE = "sec_q"
FACT_COLUMNS = ["taxonomy", "concept", "unit", "start", "end", "value", "accn", "form", "filed"]
LABEL_COLUMNS = ["taxonomy", "concept", "unit", "label"]
QUARTER_COLUMNS = ["taxonomy", "concept", "unit", "period_end", "value", "filed", "how"]
KEY = ["taxonomy", "concept", "unit"]


def _report_form(form) -> bool:
    return str(form or "").upper().startswith(REPORT_FORMS)


def extract_all_facts(payload: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(valori, etichette): ogni valore dei 10-K/10-Q del JSON companyfacts e la descrizione di ogni etichetta."""
    rows, labels = [], []
    for taxonomy, concepts in ((payload or {}).get("facts") or {}).items():
        for concept, body in (concepts or {}).items():
            for unit, facts in (body.get("units") or {}).items():
                n = len(rows)
                for f in facts:
                    if _report_form(f.get("form")) and "end" in f and f.get("val") is not None:
                        rows.append((taxonomy, concept, unit, f.get("start"), f["end"], f["val"], f.get("accn"),
                                     f.get("form"), f.get("filed")))
                if len(rows) > n:
                    labels.append((taxonomy, concept, unit, body.get("label") or concept))
    df = pd.DataFrame(rows, columns=FACT_COLUMNS)
    if df.empty:
        return df, pd.DataFrame(labels, columns=LABEL_COLUMNS)
    for col in ("start", "end", "filed"):
        df[col] = pd.to_datetime(df[col])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value", "filed"]).drop_duplicates().reset_index(drop=True)
    return df, pd.DataFrame(labels, columns=LABEL_COLUMNS)


def _additive(unit: str) -> bool:
    """Importi che si possono sottrarre (valute), non rapporti (USD/shares) né numeri di azioni."""
    u = str(unit).lower()
    return "/" not in u and u not in ("shares", "pure")


def _only_changes(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Tiene la prima versione di ogni periodo e le successive solo se il valore cambia."""
    df = df.sort_values(keys + ["filed"], kind="stable")
    prev = df.groupby(keys, sort=False)["value"].shift()
    changed = prev.isna() | ((df["value"] - prev).abs() > 1e-9 * prev.abs().clip(lower=1.0))
    return df[changed]


def quarterly_values(facts: pd.DataFrame) -> pd.DataFrame:
    """Valore di ogni etichetta per singolo trimestre, con le versioni nel tempo (vedi docstring del modulo)."""
    if facts.empty:
        return pd.DataFrame(columns=QUARTER_COLUMNS)
    dur = facts[facts["start"].notna()].copy()
    dur["days"] = (dur["end"] - dur["start"]).dt.days
    parts = []
    direct = dur[dur["days"].between(*QUARTER_DAYS)]
    parts.append(direct.assign(period_end=direct["end"], how="trimestre"))

    cum = dur[dur["days"].between(QUARTER_DAYS[0], CUMULATIVE_MAX_DAYS) & dur["unit"].map(_additive)]
    later = cum[cum["days"] > QUARTER_DAYS[1]].assign(_id=lambda d: np.arange(len(d)))
    if not later.empty:
        base = cum[KEY + ["start", "end", "value", "filed"]]
        m = later.merge(base, on=KEY + ["start"], suffixes=("", "_b"))
        gap = (m["end"] - m["end_b"]).dt.days
        m = m[gap.between(*QUARTER_DAYS) & (m["filed_b"] <= m["filed"])]
        # per ogni valore cumulato, l'ultima versione del periodo precedente già pubblicata
        m = m.sort_values("filed_b", kind="stable").groupby("_id").tail(1)
        parts.append(m.assign(value=m["value"] - m["value_b"], period_end=m["end"], how="differenza"))

    inst = facts[facts["start"].isna()]
    parts.append(inst.assign(period_end=inst["end"], how="fine periodo"))
    out = pd.concat([p[QUARTER_COLUMNS] for p in parts if not p.empty], ignore_index=True)
    if out.empty:
        return pd.DataFrame(columns=QUARTER_COLUMNS)
    # nello stesso documento il trimestre dichiarato vale più della differenza
    out["_rank"] = out["how"].map({"trimestre": 0, "fine periodo": 0, "differenza": 1})
    out = (out.sort_values(["_rank"], kind="stable")
           .drop_duplicates(KEY + ["period_end", "filed"], keep="first").drop(columns="_rank"))
    return _only_changes(out, KEY + ["period_end"]).reset_index(drop=True)


def concept_quarters(quarters: pd.DataFrame, taxonomy: str, concept: str, unit: str) -> pd.DataFrame:
    """Serie trimestrale (ultima versione depositata) di un'etichetta: period_end, value, filed, how, versions."""
    q = quarters[(quarters["taxonomy"] == taxonomy) & (quarters["concept"] == concept) & (quarters["unit"] == unit)]
    if q.empty:
        return pd.DataFrame(columns=["period_end", "value", "filed", "how", "versions"])
    q = q.sort_values(["period_end", "filed"])
    versions = q.groupby("period_end").size().rename("versions")
    last = q.groupby("period_end").tail(1).set_index("period_end")
    return last[["value", "filed", "how"]].join(versions).reset_index()


# etichette usate dalle voci normalizzate, con l'unità attesa
_STANDARD = {c: "USD" for c in DURATION_CONCEPTS + INSTANT_CONCEPTS}
_STANDARD.update({c: "shares" for c in DILUTED_SHARES})


def standard_quarters(quarters: pd.DataFrame, ticker: str, facts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Voci normalizzate (come nei bilanci annuali: ricavi, utile, FCF, debito, ...) per singolo trimestre.
    Una riga per versione: quando un documento successivo cambia una delle etichette di un trimestre, la voce
    si ricalcola con la data di deposito di quel documento. `shares` = media diluita del trimestre, corretta
    per gli errori di unità con le azioni di copertina (`facts`); manca per il quarto trimestre (nei 10-K c'è
    solo la media dell'esercizio)."""
    out_cols = ["ticker", "period_end", "metric", "value", "filed", "source"]
    if quarters.empty:
        return pd.DataFrame(columns=out_cols)
    q = quarters[(quarters["taxonomy"] == "us-gaap") & quarters["concept"].isin(_STANDARD.keys())]
    q = q[q["unit"] == q["concept"].map(_STANDARD)]
    reference = pd.DataFrame(columns=["accn", "filed", "value"])
    if facts is not None and not facts.empty:
        cover = facts[(facts["taxonomy"] == "dei") & (facts["concept"] == DEI_SHARES)]
        reference = _cover_reference(cover.rename(columns={"end": "period_end"}).assign(kind="cover_ref"))
    rows = []
    for period_end, g in q.groupby("period_end"):
        state: dict[str, float] = {}
        last: dict[str, float] = {}
        for filed, gg in g.groupby("filed", sort=True):
            state.update(zip(gg["concept"], gg["value"]))
            row = pd.Series(state, dtype=float)
            metrics = {**_flow_metrics(row), **_balance_metrics(row, quarterly=True)}
            shares = row.get(DILUTED_SHARES[0], np.nan)
            if pd.notna(shares) and shares > 0:
                metrics["shares"] = shares * _scale_for_document(shares, filed, reference)
            for metric, value in metrics.items():
                if pd.isna(value):
                    continue
                prev = last.get(metric)
                if prev is None or abs(value - prev) > 1e-9 * max(abs(prev), 1.0):
                    rows.append((ticker, period_end, metric, float(value), filed, QUARTERLY_SOURCE))
                    last[metric] = value
    return pd.DataFrame(rows, columns=out_cols)
