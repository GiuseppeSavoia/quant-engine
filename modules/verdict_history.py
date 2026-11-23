"""
Storico dei giudizi (modules/verdict.py): a fine aggiornamento (main.py) il giudizio di TUTTO l'universo dell'app
(indice, europee, aziende aggiunte dall'utente, aziende seguite) viene salvato nella tabella `verdict_history`, una
riga per (data dei dati, ticker). Serve a tre cose:

1. sapere quando il giudizio di un'azienda CAMBIA (avviso VRD in modules/alerts.py, regole in `verdict_changes`);
2. misurare il giudizio su dati NUOVI, cioè fuori campione davvero (modules/verdict_tracking.py);
3. aprire le pagine senza ricalcolare (`load_or_assess`: stessa tabella di `verdict.assess_many`).

Una seconda esecuzione con la stessa data dei dati sostituisce le righe (niente doppioni). `verdict_runs` registra
ogni scrittura: esecuzione, data dei dati, impronta della configurazione, impronta dei dati inseriti a mano.

Impronta (`config_fingerprint`): parametri del giudizio (`verdict:`), della valutazione (`valuation:`), finestra degli
insider e il codice dei moduli che fanno il giudizio. Se cambia, le righe salvate non valgono più per le pagine e un
cambio di giudizio dovuto solo a questo non fa scattare l'avviso.

Dimensione: ~600 aziende x ~252 date di borsa = ~150 mila righe l'anno. Il testo della regola (la parte più pesante)
resta solo per gli ultimi `verdict.history.keep_rule_days` giorni: categoria e punteggi restano per sempre.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from modules import verdict as vd

logger = logging.getLogger(__name__)

HISTORY_FORMAT = 1  # cambia se cambia il modo di scrivere le righe: le righe vecchie non valgono per le pagine
# colonna del database -> colonna di assess_many (il pilastro Prezzo si chiama price_pillar: "price" è il prezzo)
PILLAR_COLUMNS = {vd.QUALITY: "quality", vd.SOLIDITY: "solidity", vd.PRICE: "price_pillar", vd.FLAGS: "flags",
                  vd.INSIDERS: "insiders"}
TABLE_COLUMNS = ["name", "sector", "verdict", "category", "profile", vd.QUALITY, vd.SOLIDITY, vd.PRICE, vd.FLAGS,
                 vd.INSIDERS, "coverage", "overall", "alarms", "checks", "rule"]
# nomi brevi per i messaggi degli avvisi
SHORT = {vd.CANDIDATE: "Candidata all'acquisto", vd.WAIT: "Aspettare", vd.NEUTRAL: "Nessun segnale netto",
         vd.VALUE_TRAP: "Prezzo basso, qualità dubbia", vd.AVOID: "Da evitare", vd.NO_DATA: "Dati insufficienti"}
PILLAR_SHORT = {vd.QUALITY: "Qualità", vd.SOLIDITY: "Solidità", vd.PRICE: "Prezzo", vd.FLAGS: "Allarmi"}
# moduli il cui codice decide il giudizio: se cambiano, le righe salvate non valgono più
CODE_MODULES = ("verdict.py", "metrics.py", "cost_of_capital.py", "valuation_model.py", "dashboard_data.py",
                "services/base.py", "services/data.py", "services/valuation.py", "services/reports.py")
NO_RUN = "-"  # database senza esecuzioni registrate (nessuna analisi completa ancora)
DEFAULTS = {"enabled": True, "keep_rule_days": 30}
ALERT_DEFAULTS = {"verdict_days": 30, "verdict_nodata_days": 3, "verdict_min_pillar_change": 0.01}


# ------------------------------------------------------------------------------------------------ impronte
def history_settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("verdict", {}) or {}).get("history", {})}


def _code_digest() -> str:
    h = hashlib.sha1()
    here = Path(__file__).resolve().parent
    for name in CODE_MODULES:
        try:
            h.update((here / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()


def config_fingerprint(cfg: dict) -> str:
    """Impronta di ciò che decide il giudizio oltre ai dati: parametri e codice (12 caratteri)."""
    s = vd.settings(cfg)
    s.pop("history", None)  # storico e misura in avanti non cambiano il giudizio
    s.pop("tracking", None)
    payload = {"verdict": s, "valuation": (cfg or {}).get("valuation"),
               "insider_days": ((cfg or {}).get("insiders") or {}).get("window_days"),
               "format": HISTORY_FORMAT, "code": _code_digest()}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _day(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def _num(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _fair_values(db) -> dict[str, float]:
    notes = db.load_notes()
    if notes.empty or "fair_value" not in notes:
        return {}
    return pd.to_numeric(notes.set_index("ticker")["fair_value"], errors="coerce").dropna().to_dict()


# ------------------------------------------------------------------------------------------------ scrittura
def app_data(cfg: dict, db, reports_dir, data=None):
    """I dati dell'app (DashboardData) riusando, se c'è, la MarketData già caricata da main.py (niente seconda
    lettura del database)."""
    from modules.dashboard_data import DashboardData

    if data is None:
        return DashboardData(cfg, db, reports_dir)

    class _Given(DashboardData):
        def _load_data(self, db_, cfg_, tickers):  # noqa: ARG002 - stessi ticker di main.py (data_tickers)
            return data

    return _Given(cfg, db, reports_dir)


def universe_of(d, db=None) -> list[str]:
    """Tutto l'universo dell'app (indice, europee, aziende aggiunte) + le aziende seguite, in portafoglio o con una
    soglia di prezzo che hanno dei prezzi (es. ex membri dell'indice)."""
    from modules.alerts import followed_tickers

    out = list(d.tickers)
    try:
        cols = set(d.data.close.columns)
        out += [t for t in followed_tickers(db or d.db) if t in cols]
    except Exception as exc:  # noqa: BLE001 - le aziende seguite sono un di più
        logger.warning("Storico dei giudizi: aziende seguite non lette (%s)", exc)
    return list(dict.fromkeys(out))


def rows_from(verdicts: dict, d, run_id: str | None, fingerprint: str, fair: dict) -> list[dict]:
    as_of = _day(d.data.last_date)
    index, foreign = set(d.index_tickers), set(d.foreign["ticker"]) if not d.foreign.empty else set()
    custom = set(d.custom_companies["ticker"]) if not d.custom_companies.empty else set()
    rows = []
    for t, v in verdicts.items():
        group = "sp500" if t in index else "europe" if t in foreign else "custom" if t in custom else "other"
        rows.append({
            "as_of": as_of, "ticker": t, "run_id": run_id, "config_hash": fingerprint, "universe": group,
            "category": v.category, "profile": v.profile,
            **{col: _num(v.score(p)) for p, col in PILLAR_COLUMNS.items()},
            "coverage": _num(v.coverage), "overall": _num(v.overall), "alarms": len(v.alarms),
            "checks": len(v.checks), "errors": len(v.data_issues), "price": _num(v.price),
            "user_fair_value": _num(fair.get(t)), "rule": v.rule,
        })
    return rows


def record(db, cfg: dict, data=None, reports_dir=None, run_id: str | None = None, d=None,
           tickers: list[str] | None = None, now: datetime | None = None) -> dict:
    """Giudica tutto l'universo e salva lo storico della data dei dati. `db` = dove scrivere (di solito lo stesso
    database di `d`). Restituisce un riassunto (data, righe, secondi, quante per categoria)."""
    t0 = perf_counter()
    if d is None:
        d = app_data(cfg, db, reports_dir if reports_dir is not None else Path("."), data)
    run_id = run_id or d.run_id or NO_RUN
    tickers = universe_of(d, db) if tickers is None else list(tickers)
    _, verdicts = vd.assess_many(d, tickers, return_verdicts=True)
    fp = config_fingerprint(cfg)
    rows = rows_from(verdicts, d, run_id, fp, _fair_values(d.db))
    seconds = perf_counter() - t0
    as_of = _day(d.data.last_date)
    run = {"run_id": run_id, "as_of": as_of, "config_hash": fp, "user_data": d.db.user_data_version(),
           "written_at": (now or datetime.now()).isoformat(timespec="seconds"), "tickers": len(rows),
           "seconds": round(seconds, 2)}
    n = db.save_verdict_history(rows, run)
    keep = history_settings(cfg).get("keep_rule_days")
    pruned = 0
    if keep is not None and int(keep) >= 0:
        pruned = db.prune_verdict_rules(_day(pd.Timestamp(as_of) - pd.Timedelta(days=int(keep))))
    counts = pd.Series([r["category"] for r in rows]).value_counts().to_dict() if rows else {}
    return {"as_of": as_of, "rows": n, "missing": len(tickers) - n, "seconds": perf_counter() - t0,
            "assess_seconds": seconds, "by_category": counts, "pruned_rules": pruned, "config_hash": fp}


# ------------------------------------------------------------------------------------------------ lettura veloce
def _table(rows: pd.DataFrame) -> pd.DataFrame:
    """Righe dello storico -> colonne di assess_many (senza nome e settore)."""
    t = pd.DataFrame({
        "ticker": rows["ticker"].to_numpy(), "verdict": rows["category"].map(vd.LABELS).to_numpy(),
        "category": rows["category"].to_numpy(), "profile": rows["profile"].to_numpy(),
        **{p: pd.to_numeric(rows[col], errors="coerce").to_numpy(dtype=float) for p, col in PILLAR_COLUMNS.items()},
        "coverage": pd.to_numeric(rows["coverage"], errors="coerce").to_numpy(dtype=float),
        "overall": pd.to_numeric(rows["overall"], errors="coerce").to_numpy(dtype=float),
        "alarms": pd.to_numeric(rows["alarms"], errors="coerce").fillna(0).astype(int).to_numpy(),
        "checks": pd.to_numeric(rows["checks"], errors="coerce").fillna(0).astype(int).to_numpy(),
        "rule": rows["rule"].to_numpy(),
    })
    for c in [*PILLAR_COLUMNS, "overall"]:  # come assess_many: una colonna tutta vuota resta di None
        if t[c].isna().all():
            t[c] = pd.Series([None] * len(t), index=t.index, dtype=object)
    return t


def _same_number(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def saved_verdicts(d, tickers: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """(giudizi salvati ancora validi per i dati aperti, ticker da ricalcolare). Validi se: stessa esecuzione dei
    dati (`d.run_id`), stessa data dei dati, stessa impronta della configurazione e del codice, stessi dati inseriti a
    mano, e per ogni titolo lo stesso fair value dell'utente (entra nel pilastro Prezzo)."""
    tickers = list(dict.fromkeys(tickers))
    run_id = d.run_id or NO_RUN
    run = d.db.verdict_run(run_id) if hasattr(d.db, "verdict_run") else None
    if not run or d.data.last_date is None or run.get("as_of") != _day(d.data.last_date):
        return pd.DataFrame(), tickers
    if run.get("config_hash") != config_fingerprint(d.cfg) or run.get("user_data") != d.db.user_data_version():
        return pd.DataFrame(), tickers
    rows = d.db.load_verdict_history(tickers=tickers, run_id=run_id, as_of=run["as_of"])
    if rows.empty:
        return pd.DataFrame(), tickers
    fair = _fair_values(d.db)
    ok = rows["rule"].notna() & np.array([_same_number(fair.get(t), v) for t, v in
                                           zip(rows["ticker"], rows["user_fair_value"])], dtype=bool)
    rows = rows[ok]
    have = set(rows["ticker"])
    return _table(rows), [t for t in tickers if t not in have]


def _finish(table: pd.DataFrame, d) -> pd.DataFrame:
    """Nome, settore e ordine come assess_many."""
    comp = d.companies
    names = comp["name"].to_dict() if "name" in comp else {}
    sectors = d.sectors()
    table = table.copy()
    table.insert(1, "name", table["ticker"].map(names))
    table.insert(2, "sector", table["ticker"].map(sectors))
    table["_order"] = table["category"].map({c: i for i, c in enumerate(vd.ORDER)})
    return table.sort_values(["_order", "overall", "ticker"], ascending=[True, False, True]) \
        .drop(columns="_order").set_index("ticker")


def load_or_assess(d, tickers: list[str] | None = None) -> pd.DataFrame:
    """Come `verdict.assess_many(d, tickers)` (stessa tabella, stesso ordine), ma legge i giudizi salvati
    dall'ultimo aggiornamento quando valgono ancora per i dati aperti; ricalcola solo quelli mancanti o non più
    validi (fair value cambiato, configurazione o codice diversi...)."""
    tickers = list(dict.fromkeys(d.index_tickers if tickers is None else tickers))
    try:
        saved, stale = saved_verdicts(d, tickers)
    except Exception as exc:  # noqa: BLE001 - lo storico è una scorciatoia: senza, si ricalcola come prima
        logger.warning("Giudizi salvati non letti (%s): ricalcolo", exc)
        saved, stale = pd.DataFrame(), tickers
    if not stale:
        return _finish(saved, d)
    computed = vd.assess_many(d, stale)
    if saved.empty:
        return computed
    computed = computed.reset_index().drop(columns=["name", "sector"])
    return _finish(pd.concat([saved, computed], ignore_index=True), d)


# ------------------------------------------------------------------------------------------------ cambi di giudizio
def _sc(x) -> str:
    x = _num(x)
    return "–" if x is None else f"{x:.2f}".replace(".", ",")


def _pillar_text(prev, cur, min_change: float) -> str:
    moves = []
    for p, col in PILLAR_COLUMNS.items():
        if p == vd.INSIDERS:
            continue
        a, b = _num(prev[col]), _num(cur[col])
        if a is None and b is None:
            continue
        delta = abs((b or 0.0) - (a or 0.0)) if a is not None and b is not None else 1.0
        if delta >= min_change:
            moves.append((delta, p, a, b))
    moves.sort(key=lambda m: (-m[0], m[1]))
    parts = [f"{PILLAR_SHORT[p]} {_sc(a)} → {_sc(b)}" for _, p, a, b in moves[:2]]
    for col, word in (("alarms", "allarmi gravi"), ("checks", "allarmi da verificare")):
        a, b = int(_num(prev[col]) or 0), int(_num(cur[col]) or 0)
        if a != b:
            parts.append(f"{word} {a} → {b}")
    return ", ".join(parts[:3])


def verdict_changes(rows: pd.DataFrame, today, days: int = 30, nodata_days: int = 3,
                    min_change: float = 0.01) -> list[tuple[str, str, dict]]:
    """Avvisi VRD di UN titolo dal suo storico (righe in ordine di data): lista di (chiave, messaggio, dati).

    Regole contro il rumore:
    - "Dati insufficienti" è un BUCO, non un giudizio: le righe senza giudizio si saltano e si confronta l'ultimo
      giudizio vero con quello vero precedente. Un giorno senza prezzo aggiornato o con un dato mancante non fa
      scattare nulla, e se dopo il buco il giudizio è lo stesso di prima non c'è avviso.
    - Si avvisa del passaggio a "Dati insufficienti" solo se dura da almeno `nodata_days` date dei dati di fila.
    - Un cambio che coincide con un cambio di configurazione o di codice del giudizio (impronta diversa) non conta:
      è cambiato il metro, non l'azienda.
    - L'avviso resta attivo finché il nuovo giudizio dura, al massimo `days` giorni dalla data del cambio; se il
      giudizio torna indietro, l'avviso si chiude e il ritorno è un avviso nuovo.
    """
    if rows is None or rows.empty:
        return []
    today = pd.Timestamp(today)
    rows = rows[rows["as_of"] <= today].sort_values("as_of")
    if rows.empty:
        return []
    nodata = (rows["category"] == vd.NO_DATA).to_numpy()
    trail = len(nodata) - (np.flatnonzero(~nodata)[-1] + 1) if (~nodata).any() else len(nodata)
    real = rows[~nodata]
    out = []
    if trail >= max(1, int(nodata_days)) and not real.empty:
        start, last, prev = rows.iloc[-trail], rows.iloc[-1], real.iloc[-1]
        if prev["config_hash"] == last["config_hash"]:
            reason = str(last["rule"]) if isinstance(last.get("rule"), str) else ""
            text = (f"giudizio da {SHORT[prev['category']]} a {SHORT[vd.NO_DATA]} da {trail} aggiornamenti"
                    + (f": {reason}" if reason else ""))
            out.append((f"{prev['category']}>{vd.NO_DATA}@{_day(start['as_of'])}", text,
                        {"from": prev["category"], "to": vd.NO_DATA, "since": _day(start["as_of"]),
                         "updates": int(trail)}))
        return out
    if len(real) < 2:
        return out
    cats = real["category"].to_numpy()
    changes = np.flatnonzero(cats[1:] != cats[:-1])
    if not len(changes):
        return out
    i = int(changes[-1]) + 1
    prev, cur = real.iloc[i - 1], real.iloc[i]
    if (today - cur["as_of"]).days > int(days) or prev["config_hash"] != cur["config_hash"]:
        return out
    gap = int(((rows["as_of"] > prev["as_of"]) & (rows["as_of"] < cur["as_of"])).sum())
    detail = _pillar_text(prev, cur, min_change)
    text = f"giudizio da {SHORT[prev['category']]} a {SHORT[cur['category']]}" + (f" ({detail})" if detail else "")
    if gap:
        text += f", dopo {gap} aggiornament{'o' if gap == 1 else 'i'} senza dati sufficienti"
    data = {"from": prev["category"], "to": cur["category"], "date": _day(cur["as_of"]),
            "previous_date": _day(prev["as_of"]),
            "pillars": {p: [_num(prev[c]), _num(cur[c])] for p, c in PILLAR_COLUMNS.items()},
            "price": [_num(prev["price"]), _num(cur["price"])]}
    out.append((f"{prev['category']}>{cur['category']}@{_day(cur['as_of'])}", text, data))
    return out


def change_conditions(db, tickers: list[str], cfg: dict, today) -> dict[str, list[tuple[str, str, dict]]]:
    """Avvisi VRD veri il giorno `today` per `tickers` (parametri in `alerts:` di config.yaml)."""
    a = {**ALERT_DEFAULTS, **(cfg.get("alerts", {}) or {})}
    today = pd.Timestamp(today)
    days = int(a["verdict_days"])
    # abbastanza storia per ritrovare il giudizio di prima anche dopo un buco lungo
    since = _day(today - pd.Timedelta(days=days + 180))
    hist = db.load_verdict_history(tickers=tickers, since=since, until=_day(today))
    out = {}
    for t, g in hist.groupby("ticker", sort=False):
        hits = verdict_changes(g, today, days, int(a["verdict_nodata_days"]), float(a["verdict_min_pillar_change"]))
        if hits:
            out[t] = hits
    return out
