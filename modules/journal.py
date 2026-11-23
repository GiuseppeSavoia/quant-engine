"""
Diario delle decisioni: ogni acquisto, vendita o "non compro" con il perché, cosa farebbe cambiare idea e una data
di revisione. Con il senno di poi si misura il proprio giudizio: il prezzo è andato dove pensavi? meglio dell'indice?

Azioni: BUY (compro), SELL (vendo), PASS (guardo e non compro), WATCH (aspetto un prezzo migliore).
Una decisione "ha avuto ragione" se il titolo ha fatto meglio del benchmark per BUY/WATCH, peggio per SELL/PASS.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ACTIONS = {"BUY": "Compro", "SELL": "Vendo", "PASS": "Non compro", "WATCH": "Aspetto un prezzo migliore"}
# per queste azioni "aver ragione" vuol dire che il titolo è andato peggio del benchmark
BEARISH = {"SELL", "PASS"}
MIN_DAYS_FOR_VERDICT = 90  # prima di 3 mesi il confronto dice poco


def outcomes(journal: pd.DataFrame, close: pd.DataFrame, benchmark: str | None, today=None) -> pd.DataFrame:
    """Per ogni decisione: rendimento del titolo e del benchmark da allora, differenza, se "aveva ragione" (solo dopo
    MIN_DAYS_FOR_VERDICT giorni) e se la revisione è scaduta."""
    if journal.empty:
        return journal.assign(ret=[], bench_ret=[], excess=[], right=[], days=[], review_due=[])
    # due "oggi": i rendimenti si fermano all'ultimo prezzo, la revisione segue il calendario (con i prezzi indietro
    # di qualche giorno una revisione fissata per oggi non compariva; collaudo 2026-11-16)
    calendar_today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    today = pd.Timestamp(today) if today is not None else (close.index[-1] if not close.empty else pd.Timestamp.today())
    rows = []
    for r in journal.itertuples(index=False):
        # titolo senza prezzi nel database (es. un ETF appena comprato): serie vuota CON indice di date
        px = close[r.ticker].dropna() if r.ticker in close else pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        start = px[px.index >= r.date]
        ret = px.iloc[-1] / (r.price if pd.notna(r.price) and r.price > 0 else start.iloc[0]) - 1 \
            if not start.empty else np.nan
        bench_ret = np.nan
        if benchmark and benchmark in close:
            b = close[benchmark].dropna()
            b0 = b[b.index >= r.date]
            bench_ret = b.iloc[-1] / b0.iloc[0] - 1 if not b0.empty else np.nan
        excess = ret - bench_ret if np.isfinite(ret) and np.isfinite(bench_ret) else np.nan
        days = (today - r.date).days
        right = None
        if np.isfinite(excess) and days >= MIN_DAYS_FOR_VERDICT:
            right = bool(excess < 0) if r.action in BEARISH else bool(excess > 0)
        due = pd.notna(r.review_date) and r.review_date <= calendar_today and not isinstance(r.reviewed_at, str)
        rows.append({"ret": ret, "bench_ret": bench_ret, "excess": excess, "right": right, "days": days,
                     "review_due": bool(due)})
    return pd.concat([journal.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def hit_rate(out: pd.DataFrame) -> dict:
    """Quante decisioni giudicabili (almeno 3 mesi) hanno avuto ragione, in totale e per tipo."""
    judged = out[out["right"].notna()] if not out.empty else out
    if judged.empty:
        return {"judged": 0, "right": 0, "rate": np.nan, "by_action": {}}
    by = judged.groupby("action")["right"].agg(["count", "mean"])
    return {"judged": int(len(judged)), "right": int(judged["right"].sum()), "rate": float(judged["right"].mean()),
            "by_action": {a: (int(r["count"]), float(r["mean"])) for a, r in by.iterrows()}}
