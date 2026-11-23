"""
Dati per la webapp (app.py): tutto ciò che serve alle pagine, senza codice di interfaccia.

Legge solo il database e i CSV in reports/ e riusa gli stessi moduli della strategia
(MarketData, FundamentalAnalyzer, MultiFactorModel): i numeri della webapp sono quelli
usati per decidere, non una seconda versione calcolata in modo diverso.

Facciata: il lavoro è nei servizi di `modules/services/` (uno per area, senza Streamlit); qui restano gli stessi
nomi, firme e risultati di sempre. Le sottoclassi (verdict_research.PointInTimeView, verdict_history.app_data) e i
test possono ancora ridefinire un metodo: i servizi chiamano i metodi pubblici attraverso questa classe.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.services.company import FINANCIAL_ROWS, HISTORY_MULTIPLES, RATIO_ROWS, CompanyService
from modules.services.data import SUFFIX_CURRENCY, AppData, logger

__all__ = ["DashboardData", "FINANCIAL_ROWS", "RATIO_ROWS", "HISTORY_MULTIPLES", "SUFFIX_CURRENCY", "logger"]


class DashboardData(AppData):
    """Apertura, anagrafica, universi, valute, metriche e prezzi sono in AppData (modules/services/data.py); il
    resto delega al servizio dell'area (`self.services.<area>`)."""

    PEER_COLUMNS = CompanyService.PEER_COLUMNS
    PEER_COLUMNS_BY_SECTOR = CompanyService.PEER_COLUMNS_BY_SECTOR

    # ------------------------------------------------------------------ strategia (ultima analisi)
    def decisions(self) -> pd.DataFrame:
        return self.services.strategies.decisions()

    def orders(self) -> pd.DataFrame:
        return self.services.strategies.orders()

    def portfolio_risk(self) -> dict:
        return self.services.strategies.portfolio_risk()

    def decision_history(self, ticker: str) -> pd.DataFrame:
        return self.services.strategies.decision_history(ticker)

    def confidence_breakdown(self, ticker: str) -> dict | None:
        return self.services.strategies.confidence_breakdown(ticker)

    # ------------------------------------------------------------------ esplora e le mie aziende
    def explore_table(self) -> pd.DataFrame:
        return self.services.watch.explore_table()

    def watchlist(self) -> list[str]:
        return self.services.watch.watchlist()

    def follow(self, tickers, on: bool = True) -> None:
        return self.services.watch.follow(tickers, on)

    def watchlist_table(self) -> pd.DataFrame:
        return self.services.watch.watchlist_table()

    def note(self, ticker: str) -> dict:
        return self.services.watch.note(ticker)

    def save_note(self, ticker: str, **fields) -> None:
        return self.services.watch.save_note(ticker, **fields)

    def research(self, ticker: str) -> dict:
        return self.services.watch.research(ticker)

    def save_research(self, ticker: str, fields: dict) -> None:
        return self.services.watch.save_research(ticker, fields)

    # ------------------------------------------------------------------ bilanci e archivio SEC
    def financials(self, ticker: str, quarterly: bool = False) -> pd.DataFrame:
        return self.services.company.financials(ticker, quarterly)

    def financial_links(self, ticker: str, quarterly: bool = False) -> dict:
        return self.services.company.financial_links(ticker, quarterly)

    def financial_sources(self, ticker: str, quarterly: bool = False) -> pd.DataFrame:
        return self.services.company.financial_sources(ticker, quarterly)

    def quarterly(self, ticker: str) -> pd.DataFrame:
        return self.services.company.quarterly(ticker)

    def _facts_store(self):
        return self.services.company._facts_store()

    def sec_concepts(self, ticker: str) -> pd.DataFrame:
        return self.services.company.sec_concepts(ticker)

    def sec_quarters(self, ticker: str, concept_ids=None) -> pd.DataFrame:
        return self.services.company.sec_quarters(ticker, concept_ids)

    def sec_quarters_wide(self, ticker: str) -> pd.DataFrame:
        return self.services.company.sec_quarters_wide(ticker)

    # ------------------------------------------------------------------ funzioni della scheda
    def peers(self, ticker: str, n: int = 12, min_group: int = 5) -> tuple[pd.DataFrame, str, list[str]]:
        return self.services.company.peers(ticker, n, min_group)

    def dividend_history(self, ticker: str, years: int = 12) -> pd.DataFrame:
        return self.services.company.dividend_history(ticker, years)

    def company_filings(self, ticker: str) -> pd.DataFrame:
        return self.services.company.company_filings(ticker)

    def company_metrics(self, ticker: str) -> pd.DataFrame:
        return self.services.company.company_metrics(ticker)

    def historical_multiples(self, ticker: str, step_days: int = 21, years: int = 10) -> pd.DataFrame:
        return self.services.company.historical_multiples(ticker, step_days, years)

    def operating_margin_change(self, ticker: str) -> tuple[float, float]:
        return self.services.company.operating_margin_change(ticker)

    # ------------------------------------------------------------------ insider e grandi investitori
    def insider_transactions(self, ticker: str, years: int = 3) -> pd.DataFrame:
        return self.services.company.insider_transactions(ticker, years)

    def insider_summary(self, ticker: str) -> dict:
        return self.services.company.insider_summary(ticker)

    def fund_positions(self) -> pd.DataFrame:
        return self.services.company.fund_positions()

    def fund_holders(self, ticker: str) -> pd.DataFrame:
        return self.services.company.fund_holders(ticker)

    # ------------------------------------------------------------------ confronta
    def compare_table(self, tickers: list[str]) -> pd.DataFrame:
        return self.services.company.compare_table(tickers)

    def compare_prices(self, tickers: list[str], years: int) -> pd.DataFrame:
        return self.services.company.compare_prices(tickers, years)

    def compare_series(self, tickers: list[str], column: str, index_to_first: bool = False) -> pd.DataFrame:
        return self.services.company.compare_series(tickers, column, index_to_first)

    # ------------------------------------------------------------------ notizie dalla SEC
    def news(self, days: int = 3, tickers=None) -> pd.DataFrame:
        return self.services.news.news(days, tickers)

    def recent_filings(self, days: int = 14) -> pd.DataFrame:
        return self.services.news.recent_filings(days)

    # ------------------------------------------------------------------ portafoglio reale e diario delle decisioni
    def _fx_close(self) -> pd.DataFrame:
        return self.services.portfolio._fx_close()

    def portfolio_view(self, live_prices: dict[str, float] | None = None) -> dict:
        return self.services.portfolio.portfolio_view(live_prices)

    def _known_funds(self, tickers: list[str]) -> set[str]:
        return self.services.portfolio._known_funds(tickers)

    def etf_cache_path(self) -> Path:
        return self.services.portfolio.etf_cache_path()

    def portfolio_look_through(self, positions: pd.DataFrame | None = None, refresh: bool = False, source=None,
                               today=None) -> dict:
        return self.services.portfolio.portfolio_look_through(positions, refresh, source, today)

    def journal_view(self) -> pd.DataFrame:
        return self.services.portfolio.journal_view()

    # ------------------------------------------------------------------ costo del capitale e valutazione
    def _capital_structure(self, ticker: str, leases: bool = True) -> dict:
        return self.services.valuation._capital_structure(ticker, leases)

    def industry_table(self) -> pd.DataFrame:
        return self.services.valuation.industry_table()

    def implied_erp(self) -> dict:
        return self.services.valuation.implied_erp()

    def cost_of_capital(self, ticker: str, leases: bool = True) -> dict:
        return self.services.valuation.cost_of_capital(ticker, leases)

    def lbo(self, ticker: str):
        """Test LBO (modules/lbo.py) al prezzo più recente; None se il titolo non ha prezzi."""
        from modules import lbo as lbo_mod

        if ticker not in self.data.close:
            return None
        px = self.data.recent_close([ticker], 6)[ticker].dropna()
        if px.empty:
            return None
        return lbo_mod.lbo_for(self.data, ticker, float(px.iloc[-1]), self.cfg)

    def research_asset(self, ticker: str, current_rnd: float, last_end) -> dict | None:
        return self.services.valuation.research_asset(ticker, current_rnd, last_end)

    def valuation_setup(self, ticker: str, use_saved: bool = True, rnd: bool = True,
                        leases: bool = True) -> dict | None:
        return self.services.valuation.valuation_setup(ticker, use_saved, rnd, leases)

    def sector_distribution(self, ticker: str) -> dict[str, pd.Series]:
        return self.services.valuation.sector_distribution(ticker)

    def plausibility(self, ticker: str, levers) -> pd.DataFrame:
        return self.services.valuation.plausibility(ticker, levers)

    def save_valuation(self, ticker: str, setup: dict, scenarios: dict, probabilities: dict, story: str = "",
                       note: str = "", simulation: dict | None = None) -> dict:
        return self.services.valuation.save_valuation(ticker, setup, scenarios, probabilities, story, note,
                                                      simulation)

    def valuation_diary(self, ticker: str | None = None) -> pd.DataFrame:
        return self.services.valuation.valuation_diary(ticker)

    def dcf_inputs(self, ticker: str) -> dict:
        return self.services.valuation.dcf_inputs(ticker)

    def user_dcf(self, ticker: str, fcf: float, growth: float, wacc: float, terminal_growth: float, years: int,
                 net_debt: float, shares: float, price: float) -> dict:
        return self.services.valuation.user_dcf(ticker, fcf, growth, wacc, terminal_growth, years, net_debt, shares,
                                                price)

    def valuation(self, ticker: str) -> dict:
        return self.services.valuation.valuation(ticker)

    def fair_value_history(self, ticker: str, step_days: int = 21, warmup_days: int = 252) -> pd.DataFrame:
        return self.services.valuation.fair_value_history(ticker, step_days, warmup_days)

    # ------------------------------------------------------------------ il mio metodo e dati a mano
    def manual_table(self, ticker: str, kind: str = "annual", amount_unit: float = 1e6, share_unit: float = 1e6,
                     periods: int = 8) -> pd.DataFrame:
        return self.services.method.manual_table(ticker, kind, amount_unit, share_unit, periods)

    def save_manual(self, ticker: str, changes: dict, kind: str, amount_unit: float, share_unit: float,
                    days_after_close: int | None = None) -> int:
        return self.services.method.save_manual(ticker, changes, kind, amount_unit, share_unit, days_after_close)

    def save_custom_metric(self, d: dict) -> None:
        return self.services.method.save_custom_metric(d)

    def delete_custom_metric(self, key: str) -> list[str]:
        return self.services.method.delete_custom_metric(key)

    def preview_formula(self, formula: str) -> pd.Series:
        return self.services.method.preview_formula(formula)

    def checklist_items(self) -> pd.DataFrame:
        return self.services.method.checklist_items()

    def checklist(self, ticker: str) -> dict[int, float]:
        return self.services.method.checklist(ticker)

    def save_checklist(self, ticker: str, scores: dict[int, float | None]) -> None:
        return self.services.method.save_checklist(ticker, scores)

    def checklist_scores(self) -> pd.Series:
        return self.services.method.checklist_scores()

    def profile(self) -> dict:
        return self.services.method.profile()

    def save_profile(self, profile: dict) -> None:
        return self.services.method.save_profile(profile)

    # ------------------------------------------------------------------ strategie
    def strategy_catalog(self) -> dict[str, dict]:
        return self.services.strategies.strategy_catalog()

    def user_strategies(self) -> dict[str, dict]:
        return self.services.strategies.user_strategies()

    def strategy_config(self, name: str) -> tuple[dict, dict]:
        return self.services.strategies.strategy_config(name)

    def strategy_result(self, name: str, run: bool = False, latest: bool = True) -> dict | None:
        return self.services.strategies.strategy_result(name, run, latest)

    def period_table(self, result: dict, baseline: dict | None = None) -> pd.DataFrame:
        return self.services.strategies.period_table(result, baseline)

    def strategy_holdings(self, name: str) -> pd.DataFrame:
        return self.services.strategies.strategy_holdings(name)

    # ------------------------------------------------------------------ backtest, ricerca e qualità dei dati
    def backtest_runs(self) -> pd.DataFrame:
        return self.services.reports.backtest_runs()

    def _latest_csv(self, prefix: str) -> Path | None:
        return self.services.reports._latest_csv(prefix)

    def backtest_equity(self) -> pd.DataFrame:
        return self.services.reports.backtest_equity()

    def backtest_trades(self) -> pd.DataFrame:
        return self.services.reports.backtest_trades()

    def backtest_coverage(self) -> pd.DataFrame:
        return self.services.reports.backtest_coverage()

    def research_summary(self) -> pd.DataFrame:
        return self.services.reports.research_summary()

    def factor_summary(self) -> pd.DataFrame:
        return self.services.reports.factor_summary()

    def factor_composite(self) -> pd.DataFrame:
        return self.services.reports.factor_composite()

    def factor_weights(self) -> dict[str, float]:
        return self.services.reports.factor_weights()

    def quality_issues(self) -> pd.DataFrame:
        return self.services.reports.quality_issues()

    def quality_for(self, ticker: str) -> pd.DataFrame:
        return self.services.reports.quality_for(ticker)

    def trades_for(self, ticker: str) -> pd.DataFrame:
        return self.services.reports.trades_for(ticker)

