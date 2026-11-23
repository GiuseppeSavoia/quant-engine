"""Stime degli analisti per la scheda azienda (funzione EE): lettura, import da CSV, scarico da Yahoo e il quadro
già calcolato (multipli sui prossimi 12 mesi, confronto con lo scenario Base del DCF, tabella, revisioni). Solo
informazione: le stime non entrano nel giudizio, nelle strategie né nei backtest (modules/estimates.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import estimates as es
from modules.services.base import Service


class EstimatesService(Service):
    def estimates(self, ticker: str) -> pd.DataFrame:
        """Tutte le stime salvate del titolo (ogni scarico/import, con `as_of`)."""
        return self.db.load_estimates([ticker])

    def import_estimates_csv(self, text: str) -> tuple[int, list[str]]:
        """Importa un CSV di stime (modello: `es.parse_csv`); (righe salvate, ticker). ValueError se il file non va."""
        rows = es.parse_csv(text)
        return int(self.db.save_estimates(rows)), sorted(set(rows["ticker"]))

    def refresh_estimates(self, ticker: str) -> dict:
        """Scarica subito da Yahoo le stime di un titolo (rete)."""
        return es.refresh(self.db, [ticker], max_age_days=0, max_tickers=1, pause=0)

    def estimates_overview(self, ticker: str, online: bool = True) -> dict:
        """Quadro delle stime già calcolato per la scheda; {"empty": True} se non ce ne sono.

        Chiavi: currency, fx_notes (stime in un'altra valuta convertite o escluse), summary (`es.summary`),
        trailing_pe, dcf (crescita dello scenario Base contro il consenso, o None), table (ultima stima per voce e
        periodo, ricavi in milioni, etichette in italiano), revisions (EPS atteso per l'esercizio prossimo nel tempo).
        `online`: False = niente cambi scaricati (dati simulati)."""
        ctx = self.ctx
        est = ctx.estimates(ticker)
        if est.empty:
            return {"empty": True}
        m = ctx.metrics()
        g = lambda k: float(m.at[ticker, k]) if ticker in m.index and k in m.columns else np.nan  # noqa: E731
        px = ctx.prices(ticker)
        price = float(px["close"].iloc[-1]) if not px.empty else np.nan
        annual = self.data.statements(ticker)
        cur = ctx.currency(ticker)
        fx = None
        if online:
            from modules.europe import _yahoo_fx as fx
        est, fx_notes = es.to_currency(est, cur, fx)
        s = es.summary(est, price, g("market_cap"), g("enterprise_value"), annual.index[-1] if not annual.empty else None)
        dcf = None
        try:
            setup = ctx.valuation_setup(ticker)
        except Exception:  # noqa: BLE001 - la valutazione è un di più: il quadro delle stime resta
            setup = None
        if setup is not None and np.isfinite(s["revenue_growth_fy0"]):
            base = setup["scenarios"].get("Base") or next(iter(setup["scenarios"].values()))
            cons = float(np.nanmean([s["revenue_growth_fy0"], s["revenue_growth_fy1"]]))
            dcf = {"base_growth": float(base.growth), "consensus_growth": cons, "gap": float(base.growth - cons)}
        lat = es.latest(est)
        table = lat[lat["metric"] != "price_target"].assign(
            voce=lambda x: x["metric"].map(es.METRICS), periodo=lambda x: x["period"].map(lambda p: es.PERIODS.get(p, p)))
        big = table["metric"] == "revenue"
        for c in ("avg", "low", "high", "year_ago"):
            table[c] = np.where(big, table[c] / 1e6, table[c])
        return {"empty": False, "currency": cur, "fx_notes": fx_notes, "summary": s, "trailing_pe": g("pe"),
                "dcf": dcf, "table": table, "revisions": es.revisions(est)}
