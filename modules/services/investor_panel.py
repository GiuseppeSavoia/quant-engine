"""Tavolo dei grandi investitori nella scheda: esiti per investitore e sintesi (modules/investor_panel.py) sui dati
della scheda, con il risultato storico di ogni strategia già salvato (mai ricalcolato qui) e le sue cautele."""
from __future__ import annotations

import pandas as pd

from modules import investor_panel as ip
from modules import strategies
from modules.services.base import Service

HISTORY_NOTE = ("Risultati 2012-2026 delle stesse regole sull'S&P 500 di allora: decine di configurazioni provate sugli "
                "stessi dati, fondi e investitori scelti oggi (selezione a posteriori). Un backtest non è una previsione.")


class InvestorPanelService(Service):
    def _history(self, name: str, baseline: dict | None) -> dict | None:
        """Backtest già salvato della strategia (anche se con dati di un giorno prima): rendimento annuo su tutto il
        periodo e sulle due metà, Sharpe. None se non è mai stato fatto (si fa dalla pagina Strategie)."""
        res = self.ctx.strategy_result(name)
        if res is None or res["equity"].empty:
            return None
        t = self.ctx.period_table(res, baseline)
        own = t[t["what"] == "Strategia"] if not t.empty else t

        def get(prefix: str, col: str):
            r = own[own["period"].str.startswith(prefix)] if not own.empty else own
            return float(r[col].iloc[0]) if not r.empty else None

        base2 = t[(t["what"] == strategies.BASELINE) & t["period"].str.startswith("2a")] if not t.empty else t
        return {"since": int(res["equity"].index[0].year), "stale": bool(res.get("stale")),
                "cagr": get("Tutto", "cagr"), "first": get("1a", "cagr"), "second": get("2a", "cagr"),
                "sharpe": get("Tutto", "sharpe_ratio"),
                "baseline_second": float(base2["cagr"].iloc[0]) if not base2.empty else None}

    def investor_panel(self, ticker: str) -> dict:
        """Il tavolo per un'azienda: esiti, sintesi, nota fissa, data dei dati, risultati storici delle strategie."""
        ctx = self.ctx
        panel = ip.assess_panel(ticker, ctx.metrics(), ctx.sectors(), list(ctx.index_tickers), strategies.CATALOG,
                                self.cfg.get("investor_panel"), ctx.currency(ticker))
        baseline = ctx.strategy_result(strategies.BASELINE)
        for m in panel["members"]:
            m["history"] = self._history(m["name"], baseline)
        panel.update(currency=ctx.currency(ticker), data_as_of=pd.Timestamp(self.data.last_date),
                     name=ctx.info(ticker).get("name") or "", history_note=HISTORY_NOTE)
        return panel
