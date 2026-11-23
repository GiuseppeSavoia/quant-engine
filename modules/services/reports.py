"""Rapporti di main.py (CSV in reports/): backtest, ricerca sui segnali, fattori, qualità dei dati."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.services.base import Service


class ReportService(Service):
    def backtest_runs(self) -> pd.DataFrame:
        return self.db.load_backtest_runs()

    def _latest_csv(self, prefix: str) -> Path | None:
        files = sorted(self.ctx.reports_dir.glob(f"{prefix}_*.csv"))
        return files[-1] if files else None

    def backtest_equity(self) -> pd.DataFrame:
        path = self._latest_csv("backtest_equity")
        return pd.read_csv(path, index_col="date", parse_dates=["date"]) if path else pd.DataFrame()

    def backtest_trades(self) -> pd.DataFrame:
        path = self._latest_csv("backtest_trades")
        return pd.read_csv(path, parse_dates=["date"]) if path else pd.DataFrame()

    def backtest_coverage(self) -> pd.DataFrame:
        path = self._latest_csv("backtest_copertura")
        return pd.read_csv(path, parse_dates=["date"]) if path else pd.DataFrame()

    def research_summary(self) -> pd.DataFrame:
        path = self._latest_csv("ricerca_sintesi")
        return pd.read_csv(path) if path else pd.DataFrame()

    def factor_summary(self) -> pd.DataFrame:
        path = self._latest_csv("fattori_sintesi")
        return pd.read_csv(path) if path else pd.DataFrame()

    def factor_composite(self) -> pd.DataFrame:
        path = self._latest_csv("fattori_combinazione")
        return pd.read_csv(path) if path else pd.DataFrame()

    def factor_weights(self) -> dict[str, float]:
        path = self._latest_csv("fattori_pesi")
        return pd.read_csv(path, index_col=0)["peso"].to_dict() if path else {}

    def quality_issues(self) -> pd.DataFrame:
        path = self._latest_csv("qualita_dati")
        return pd.read_csv(path) if path else pd.DataFrame()

    def quality_for(self, ticker: str) -> pd.DataFrame:
        q = self.ctx.quality_issues()
        return q[q["ticker"] == ticker] if not q.empty else q

    def trades_for(self, ticker: str) -> pd.DataFrame:
        trades = self.ctx.backtest_trades()
        return trades[trades["ticker"] == ticker] if not trades.empty else trades

    # ------------------------------------------------------------------ esportazioni della scheda
    def export_excel(self, ticker: str) -> bytes | None:
        """Modello di valutazione in Excel con le formule (None se mancano ricavi, azioni o prezzi)."""
        from modules.excel_model import company_model

        return company_model(self.ctx, ticker) or None

    def export_pdf(self, ticker: str) -> bytes:
        """Sintesi in PDF: giudizio, tesi, numeri, concorrenti, valutazione di partenza."""
        from modules.research_pdf import company_pdf

        return company_pdf(self.ctx, ticker)
