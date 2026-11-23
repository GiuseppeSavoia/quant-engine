"""Colonne delle metriche, widget delle regole {metric, op, value} (Esplora, Strategie, concorrenti) e giudizi
(`modules.verdict.assess_many`) in cache per Le mie aziende, Esplora e Confronta."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import streamlit as st

from modules import screener
from modules.metrics import METRICS, METRIC_BY_KEY
from ui.common import md
from ui.components import DIGITS, NA, VERDICT_STYLE, fmt, notice, num_column

# ---------------------------------------------------------------- colonne delle metriche
METRIC_DIGITS = {"pct": 1, "x": 1, "money": 1, "int": 0, "num": 2}


def metric_label(key: str, symbol: str = "$") -> str:
    """Intestazione di colonna di una metrica con l'unità: "P/E (x)", "Capitalizzazione (mld $)"."""
    m = METRIC_BY_KEY[key]
    if m.fmt == "money":
        return f"{m.label} ({f'mld {symbol}'.strip()})"
    if m.fmt == "x":
        return f"{m.label} (x)"
    return m.label


def _metric_column(key: str, symbol: str = "$"):
    """Colonna ordinabile di una metrica per `st.dataframe` (numeri in italiano).
    `symbol` = valuta degli importi ("" se la tabella mescola valute); gli importi vanno passati in miliardi
    (il chiamante divide per 1e9, come prima). Le unità stanno nell'intestazione (`metric_label`).
    NB: con un `format` i valori mancanti si vedono "None": per tabelle con buchi usare `styled` + `num_col`."""
    m = METRIC_BY_KEY[key]
    help_text = m.help or None
    label = metric_label(key, symbol)
    if m.fmt == "pct":
        return num_column(label, "pct", help=help_text)
    if m.fmt in ("money", "x"):
        return num_column(label, "num", digits=1, help=help_text)
    if m.fmt == "int":
        return num_column(label, "int", help=help_text)
    return num_column(label, "num", digits=2, help=help_text)


def metric_kind(key: str) -> tuple[str, dict]:
    """Tipo di `fmt` per una metrica nelle tabelle (importi già in miliardi, multipli con "(x)" nell'intestazione)."""
    m = METRIC_BY_KEY.get(key)
    f = m.fmt if m else "num"
    if f == "pct":
        return "pct", {}
    if f in ("money", "x"):
        return "num", {"digits": 1}
    if f == "int":
        return "int", {}
    return "num", {"digits": 2}


def styled(df: pd.DataFrame, kinds: dict):
    """`st.dataframe` ordinabile ma scritto in italiano e senza "None": i valori restano numeri (l'ordinamento è
    numerico), il testo mostrato viene da `fmt` (Styler.format) e i mancanti sono "–".
    `kinds`: colonna -> tipo di `fmt` oppure (tipo, opzioni di fmt)."""
    funcs = {}
    df = df.copy()
    for c, spec in kinds.items():
        if c not in df.columns:
            continue
        kind, opts = (spec, {}) if isinstance(spec, str) else spec
        funcs[c] = (lambda v, k=kind, o=opts: fmt(v, k, **o))
        if kind in ("date", "date_short", "text"):
            df[c] = df[c].map(lambda v, k=kind, o=opts: fmt(v, k, **o))  # testo: niente "None"/NaT
            del funcs[c]
        elif pd.api.types.is_numeric_dtype(df[c]):
            # st.dataframe scrive "None" per i valori nulli ignorando lo Styler: il mancante diventa -inf (fmt lo
            # scrive "–"; ordinando va in fondo nell'ordine decrescente)
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float).fillna(-np.inf)
    return df.style.format(funcs, na_rep=NA)


def num_col(label: str, *, help: str | None = None, width=None, pinned: bool | None = None):  # noqa: A002
    """Colonna numerica SENZA formato, da usare con `styled`: il testo lo dà lo Styler (un `format` qui lo
    sostituirebbe, e i mancanti tornerebbero "None")."""
    return st.column_config.NumberColumn(label, help=help, width=width, pinned=pinned)


def pillar_column(pillar: str):
    """Barra 0-1 di un pilastro (valori da arrotondare a 2 decimali prima)."""
    return st.column_config.ProgressColumn(PILLAR_SHORT[pillar], help=PILLAR_HELP[pillar], min_value=0.0,
                                           max_value=1.0, format="localized", width="small")


def round_for_display(df: pd.DataFrame, columns) -> pd.DataFrame:
    """Arrotonda le colonne delle metriche alle cifre mostrate e toglie lo zero con segno (-0,0 -> 0,0): le
    colonne restano numeriche (ordinabili)."""
    out = df.copy()
    for c in columns:
        if c not in out:
            continue
        m = METRIC_BY_KEY.get(c)
        digits = METRIC_DIGITS.get(m.fmt, 2) if m else 2
        if m is not None and m.fmt == "pct":
            digits += 2  # frazioni: 0,1234 -> 12,3%
        vals = pd.to_numeric(out[c], errors="coerce").astype(float)
        out[c] = vals.round(digits) + 0.0
    return out


def _rule_unit(metric: str, op: str) -> tuple[str, float]:
    """Unità mostrata e fattore di conversione (valore mostrato = valore x fattore)."""
    if op in screener.RANK_OPS:
        return "%", 100.0
    return {"pct": ("%", 100.0), "money": ("mld $", 1e-9)}.get(METRIC_BY_KEY[metric].fmt, ("", 1.0))


def _rules_set(ns: str, rules: list[dict]):
    """Carica `rules` nei widget delle condizioni con prefisso `ns` (es. "ex" = Esplora, "sb" = strategie)."""
    for k in [k for k in st.session_state if str(k).startswith(f"{ns}r_")]:
        del st.session_state[k]
    st.session_state[f"{ns}_rules"] = [{"id": i, **r} for i, r in enumerate(rules)]
    st.session_state[f"{ns}_next"] = len(rules)


def _rules_add(ns: str):
    i = st.session_state.get(f"{ns}_next", 0)
    st.session_state[f"{ns}_rules"].append({"id": i, "metric": "pe", "op": "<=", "value": 20.0})
    st.session_state[f"{ns}_next"] = i + 1


def _rules_remove(ns: str, rule_id: int):
    st.session_state[f"{ns}_rules"] = [r for r in st.session_state[f"{ns}_rules"] if r["id"] != rule_id]


def _rules_widgets(ns: str, exclude: set[str] = frozenset()) -> list[dict]:
    """Disegna le condizioni e restituisce le regole nelle unità della metrica."""
    keys = [m.key for m in METRICS if m.key not in exclude]
    rules = []
    for r in st.session_state[f"{ns}_rules"]:
        i, k = r["id"], f"{ns}r_{r['id']}"
        if f"{k}_metric" not in st.session_state:  # prima volta: valori della regola
            unit, factor = _rule_unit(r["metric"], r["op"])
            st.session_state[f"{k}_metric"] = r["metric"]
            st.session_state[f"{k}_op"] = r["op"]
            st.session_state[f"{k}_value"] = float(r["value"]) * factor
        c1, c2, c3, cu, c4 = st.columns([4, 3, 2, 0.7, 0.9], vertical_alignment="center")
        metric = c1.selectbox("Metrica", keys, key=f"{k}_metric", label_visibility="collapsed",
                              placeholder="Scegli una metrica",
                              format_func=lambda x: f"{METRIC_BY_KEY[x].group} · {METRIC_BY_KEY[x].label}")
        op = c2.selectbox("Condizione", list(screener.OPS), key=f"{k}_op", label_visibility="collapsed",
                          placeholder="Scegli la condizione", format_func=screener.OPS.get)
        unit, factor = _rule_unit(metric, op)
        shown = c3.number_input(f"Valore ({unit})" if unit else "Valore", key=f"{k}_value",
                                label_visibility="collapsed", step=1.0 if factor != 1.0 else 0.5, format="%.2f")
        cu.markdown(md(unit) if unit else " ")
        c4.button("✕", key=f"{k}_del", on_click=_rules_remove, args=(ns, i), help="Togli questa condizione")
        rule = {"metric": metric, "op": op, "value": shown / factor}
        try:
            screener.validate_rule(rule)
            rules.append(rule)
        except ValueError as exc:
            notice(str(exc), "warn")
    return rules


# ---------------------------------------------------------------- giudizi (modules/verdict.py)
VERDICT_NOTE = ("Il giudizio è una sintesi dei criteri impostati, non una raccomandazione: nella prova sul passato "
                "(2013-2026, dati di allora) le «Candidate» non hanno battuto l'indice. Dettagli e prove nella scheda "
                "dell'azienda.")
PILLARS = ["quality", "solidity", "price", "flags", "insiders"]
PILLAR_SHORT = {"quality": "Qualità", "solidity": "Solidità", "price": "Conven.", "flags": "Allarmi",
                "insiders": "Insider"}
PILLAR_HELP = {
    "quality": "Qualità del business, da 0 a 1 (1 = eccellente: redditività alta e costante).",
    "solidity": "Solidità finanziaria, da 0 a 1 (1 = molto solida: poco debito, interessi ben coperti).",
    "price": "Pilastro Prezzo, da 0 a 1 (1 = prezzo basso rispetto a valore stimato, settore e storia).",
    "flags": "Segnali d'allarme, da 0 a 1 (1 = nessun allarme contabile o di bilancio).",
    "insiders": "Chi è dentro (insider e fondi), da 0 a 1: solo contesto, non entra nel giudizio.",
}


def verdict_text(category) -> str:
    """Etichetta corta con simbolo per le tabelle `st.dataframe` (testo, non HTML): "✓ Candidata"."""
    if not isinstance(category, str) or category not in VERDICT_STYLE:
        return ""
    sym, _, short = VERDICT_STYLE[category]
    return f"{sym} {short}"


def verdict_options() -> list[str]:
    from modules import verdict

    return list(verdict.ORDER)


def pillar_grade(pillar: str, score) -> str:
    """Voto in parole del pilastro (stesse soglie di modules/verdict.GRADES)."""
    from modules import verdict

    if score is None or (isinstance(score, float) and not np.isfinite(score)) or pd.isna(score):
        return ""
    for threshold, word in verdict.GRADES.get(pillar, []):
        if float(score) >= threshold:
            return word
    return ""


def _verdict_key(d) -> str:
    """Chiave dei giudizi: oggetto dei dati (cambia con ogni aggiornamento, dati a mano e codice), ultima data dei
    prezzi e fair value dell'utente (entrano nel pilastro Prezzo)."""
    notes = d.db.load_notes()
    fair = ""
    if not notes.empty and "fair_value" in notes:
        fair = notes[["ticker", "fair_value"]].dropna().sort_values("ticker").to_csv(index=False)
    digest = hashlib.sha1(fair.encode()).hexdigest()[:12]
    return f"{id(d)}|{d.run_id}|{d.data.last_date}|{digest}"


@st.cache_data(show_spinner=False, max_entries=16)
def _verdicts_cached(_d, key: str, tickers: tuple[str, ...]) -> pd.DataFrame:  # noqa: ARG001 - key = cache
    from modules import verdict_history

    cols = ["name", "sector", "verdict", "category", "profile", *PILLARS, "coverage", "overall", "alarms", "checks",
            "rule"]
    if not tickers:
        return pd.DataFrame(columns=cols)
    # giudizi salvati dall'ultimo aggiornamento se valgono ancora per questi dati, altrimenti assess_many
    table = verdict_history.load_or_assess(_d, list(tickers))
    return table.reindex(columns=list(dict.fromkeys(list(table.columns) + cols)))


def verdict_table(d, tickers=None) -> pd.DataFrame:
    """Giudizi (indice = ticker: verdict, category, quality, solidity, price, flags, insiders, coverage, overall,
    alarms, checks, rule) di tutte le aziende dell'app, calcolati una volta per versione dei dati; `tickers` limita
    le righe (quelle fuori dall'elenco dell'app, es. ex membri seguiti, si calcolano a parte). Già ordinata per
    giudizio."""
    key = _verdict_key(d)
    base = tuple(d.tickers)
    with st.spinner("Calcolo i giudizi…", show_time=True):
        table = _verdicts_cached(d, key, base)
        if tickers is None:
            return table
        want = list(dict.fromkeys(tickers))
        extra = tuple(t for t in want if t not in table.index)
        if extra:
            more = _verdicts_cached(d, key, extra)
            table = pd.concat([table, more]) if not table.empty else more
    return table[table.index.isin(want)] if not table.empty else table


def verdict_note() -> None:
    """La riga di cautela vicino al giudizio (filtro o colonna)."""
    st.caption(VERDICT_NOTE)


__all__ = ["_metric_column", "metric_label", "metric_kind", "styled", "round_for_display", "num_col", "pillar_column", "_rule_unit", "_rules_set", "_rules_add", "_rules_remove",
           "_rules_widgets", "VERDICT_NOTE", "PILLARS", "PILLAR_SHORT", "PILLAR_HELP", "verdict_text",
           "verdict_options", "pillar_grade", "verdict_table", "verdict_note", "DIGITS"]
