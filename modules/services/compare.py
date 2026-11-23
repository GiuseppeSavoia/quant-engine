"""Confronta (da 2 a 5 aziende): giudizio e pilastri di ognuna, metriche del catalogo affiancate con la migliore di ogni
riga, grafici nel tempo (prezzo, ricavi, margine operativo, P/E). Una sola definizione per l'interfaccia web
(api/: /compare) e per la webapp Streamlit (ui/pages/confronta.py e ui/rules.py importano da qui)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.metrics import GROUPS, METRICS
from modules.services.base import Service

MAX_COMPARE = 5
PILLAR_ROWS = [("quality", "Qualità del business"), ("solidity", "Solidità finanziaria"), ("price", "Prezzo"),
               ("flags", "Segnali d'allarme"), ("insiders", "Chi è dentro (solo contesto)")]
PRICE_YEARS = (1, 3, 5, 10)


def pillar_grade(pillar: str, score) -> str:
    """Voto in parole del pilastro (stesse soglie di modules/verdict.GRADES); "" se manca il punteggio."""
    from modules import verdict

    try:
        x = float(score)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(x):
        return ""
    for threshold, word in verdict.GRADES.get(pillar, []):
        if x >= threshold:
            return word
    return ""


def _frame(df: pd.DataFrame, tickers: list[str], max_points: int = 400) -> dict:
    """Serie nel tempo come {dates, series: {ticker: valori}}; al massimo `max_points` date (le altre saltate a
    intervalli regolari, l'ultima sempre tenuta)."""
    if df is None or df.empty or df.dropna(how="all").empty:
        return {"dates": [], "series": {}}
    df = df.dropna(how="all")
    if len(df) > max_points:
        step = int(np.ceil(len(df) / max_points))
        keep = list(range(0, len(df), step))
        if keep[-1] != len(df) - 1:
            keep.append(len(df) - 1)
        df = df.iloc[keep]
    return {"dates": list(df.index), "series": {t: df[t].tolist() for t in tickers if t in df and df[t].notna().any()}}


class CompareService(Service):
    def compare_metrics(self, tickers: list[str]) -> pd.DataFrame:
        """`compare_table` (indice = metrica) senza mai andare in eccezione: se mancano metriche o aziende nella
        tabella delle metriche (dati incompleti) la ricostruisce con i buchi."""
        try:
            return self.ctx.compare_table(tickers).set_index("key")
        except (KeyError, ValueError):
            pass
        m = self.ctx.metrics().reindex(index=tickers)
        rows = []
        for metric in METRICS:
            vals = pd.to_numeric(m[metric.key], errors="coerce") if metric.key in m else pd.Series(np.nan, index=tickers)
            best = None
            if metric.better and vals.notna().sum() >= 2:
                best = vals.idxmax() if metric.better == "high" else vals.idxmin()
            rows.append({"key": metric.key, "group": metric.group, "label": metric.label, "better": metric.better,
                         **vals.astype(float).to_dict(), "best": best})
        return pd.DataFrame(rows).set_index("key")

    def _pe_history(self, tickers: list[str]) -> pd.DataFrame:
        """P/E nel tempo di ogni azienda (multipli ricalcolati con i bilanci noti a ogni data: costa qualche secondo
        per azienda, quindi tenuto in memoria sul contesto)."""
        cache = self.ctx.__dict__.setdefault("_multiples_cache", {})
        cols = {}
        for t in tickers:
            if t not in cache:
                try:
                    cache[t] = self.ctx.historical_multiples(t)
                except Exception:  # noqa: BLE001 - un'azienda senza storia non ferma il confronto
                    cache[t] = pd.DataFrame()
            hist = cache[t]
            if isinstance(hist, pd.DataFrame) and "pe" in hist:
                cols[t] = hist["pe"]
        return pd.DataFrame(cols)

    def compare(self, tickers: list[str], years: int = 5) -> dict:
        """Il confronto pronto da mostrare. ValueError se le aziende non sono da 2 a 5 o non si conoscono."""
        from modules.metrics import METRIC_BY_KEY

        ctx = self.ctx
        known = {t.upper(): t for t in ctx.tickers}
        out_t = []
        for t in tickers:
            k = known.get(str(t).upper())
            if k is None:
                raise ValueError(f"Azienda sconosciuta: {t}")
            if k not in out_t:
                out_t.append(k)
        tickers = out_t
        if not 2 <= len(tickers) <= MAX_COMPARE:
            raise ValueError(f"Scegli da 2 a {MAX_COMPARE} aziende.")
        if years not in PRICE_YEARS:
            raise ValueError(f"Periodo del prezzo non previsto: {years} (ammessi: {', '.join(map(str, PRICE_YEARS))})")
        v = ctx.verdict_overview(tickers).reindex(tickers)
        try:
            targets = self.db.load_price_targets()
            targets = targets.set_index("ticker") if not targets.empty else pd.DataFrame(columns=["buy_below"])
        except Exception:  # noqa: BLE001
            targets = pd.DataFrame(columns=["buy_below"])
        companies = []
        for t in tickers:
            px = self.data.prices(t)
            last = float(px.iloc[-1]) if len(px) else None
            fv = ctx.note(t).get("fair_value")
            fv = float(fv) if fv is not None and pd.notna(fv) else None
            buy = targets["buy_below"].get(t) if t in targets.index else None
            pillars = []
            for key, label in PILLAR_ROWS:
                s = v.at[t, key] if key in v.columns else None
                s = None if s is None or pd.isna(s) else float(s)
                pillars.append({"key": key, "label": label, "score": s, "grade": pillar_grade(key, s)})
            companies.append({
                "ticker": t, "name": ctx.info(t).get("name") or "", "currency": ctx.currency(t),
                "category": v.at[t, "category"] if "category" in v.columns and isinstance(v.at[t, "category"], str) else None,
                "verdict": v.at[t, "verdict"] if "verdict" in v.columns and isinstance(v.at[t, "verdict"], str) else None,
                "coverage": v.at[t, "coverage"] if "coverage" in v.columns else None, "pillars": pillars,
                "price": last, "fair_value": fv, "fair_margin": fv / last - 1 if fv and last else None,
                "buy_below": float(buy) if buy is not None and pd.notna(buy) else None,
            })
        table = self.compare_metrics(tickers)
        groups = []
        for g in GROUPS:
            sub = table[table["group"] == g] if not table.empty else table
            if sub.empty:
                continue
            groups.append({"group": g, "rows": [
                {"key": key, "label": r["label"], "fmt": METRIC_BY_KEY[key].fmt if key in METRIC_BY_KEY else "num",
                 "better": r["better"], "help": METRIC_BY_KEY[key].help if key in METRIC_BY_KEY else "",
                 "values": {t: (float(r[t]) if t in r and pd.notna(r[t]) and np.isfinite(float(r[t])) else None)
                            for t in tickers},
                 "best": r["best"] if isinstance(r["best"], str) else None}
                for key, r in sub.iterrows()]})
        charts = [
            {"key": "price", "title": "Prezzo", "note": "1 = inizio del periodo", "kind": "num",
             **_frame(ctx.compare_prices(tickers, years), tickers)},
            {"key": "revenue", "title": "Ricavi degli ultimi 12 mesi", "note": "100 = prima data comune", "kind": "num",
             **_frame(ctx.compare_series(tickers, "revenue", index_to_first=True), tickers)},
            {"key": "operating_margin", "title": "Margine operativo", "note": "ultimi 12 mesi", "kind": "pct",
             **_frame(ctx.compare_series(tickers, "operating_margin"), tickers)},
            {"key": "pe", "title": "P/E nel tempo", "note": "con i bilanci noti a ogni data", "kind": "mult",
             **_frame(self._pe_history(tickers), tickers)},
        ]
        return {"tickers": tickers, "as_of": self.data.last_date, "years": years, "price_years": list(PRICE_YEARS),
                "companies": companies, "groups": groups, "charts": charts}
