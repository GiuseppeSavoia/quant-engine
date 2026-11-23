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

    # ------------------------------------------------------------------ il mio metodo
    def method_page(self) -> dict:
        return self.services.method.method_page()

    def formula_preview(self, formula: str, better: str | None = None, top: int = 10) -> dict:
        return self.services.method.formula_preview(formula, better, top)

    def create_custom_metric(self, label: str, formula: str, fmt: str = "num", better: str | None = None,
                             description: str = "") -> dict:
        return self.services.method.create_custom_metric(label, formula, fmt, better, description)

    def save_checklist_question(self, question: str, help_text: str = "", weight: float = 1.0,
                                item_id: int | None = None) -> None:
        return self.services.method.save_checklist_question(question, help_text, weight, item_id)

    def remove_checklist_question(self, item_id: int) -> None:
        return self.services.method.remove_checklist_question(item_id)

    # ------------------------------------------------------------------ portafoglio (pagina web)
    def portfolio_page(self) -> dict:
        return self.services.portfolio.portfolio_page()

    def add_transaction(self, ticker: str, kind: str, day, quantity: float, price: float, currency: str | None = None,
                        fx: float | None = None, fees: float | None = None, note: str | None = None,
                        reason: str | None = None) -> int:
        return self.services.portfolio.add_transaction(ticker, kind, day, quantity, price, currency, fx, fees, note,
                                                       reason)

    def delete_transaction(self, tx_id: int) -> None:
        return self.services.portfolio.delete_transaction(tx_id)

    # ------------------------------------------------------------------ diario delle decisioni
    def journal_benchmark(self) -> str:
        return self.services.portfolio.journal_benchmark()

    def journal_page(self) -> dict:
        return self.services.portfolio.journal_page()

    def add_journal_decision(self, ticker: str, action: str, reason: str, change_mind: str | None = None,
                             price: float | None = None, review_date=None) -> int:
        return self.services.portfolio.add_journal_decision(ticker, action, reason, change_mind, price, review_date)

    def review_journal_decision(self, dec_id: int, verdict: str, note: str = "") -> None:
        return self.services.portfolio.review_journal_decision(dec_id, verdict, note)

    def delete_journal_decision(self, dec_id: int) -> None:
        return self.services.portfolio.delete_journal_decision(dec_id)

    # ------------------------------------------------------------------ grafico del prezzo
    def price_chart(self, ticker: str, period: str = "1A", style: str = "Auto", bench: bool = False) -> dict:
        return self.services.price_chart.price_chart(ticker, period, style, bench)

    # ------------------------------------------------------------------ oggi
    def today(self, only_mine_documents: bool = False) -> dict:
        return self.services.today.today(only_mine_documents)

    # ------------------------------------------------------------------ confronta
    def compare_metrics(self, tickers: list[str]) -> pd.DataFrame:
        return self.services.compare.compare_metrics(tickers)

    def compare(self, tickers: list[str], years: int = 5) -> dict:
        return self.services.compare.compare(tickers, years)

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

    # soglie di prezzo, avvisi e versione dei dati dell'utente
    def price_target(self, ticker: str) -> dict:
        return self.services.watch.price_target(ticker)

    def set_price_target(self, ticker: str, buy_below: float | None = None, sell_above: float | None = None,
                         note: str | None = None) -> None:
        return self.services.watch.set_price_target(ticker, buy_below, sell_above, note)

    def delete_price_target(self, ticker: str) -> None:
        return self.services.watch.delete_price_target(ticker)

    def active_alerts(self, ticker: str) -> pd.DataFrame:
        return self.services.watch.active_alerts(ticker)

    def mark_alerts_seen(self, ids) -> None:
        return self.services.watch.mark_alerts_seen(ids)

    def user_data_version(self) -> str:
        return self.services.watch.user_data_version()

    def verdict_overview(self, tickers=None) -> pd.DataFrame:
        return self.services.watch.verdict_overview(tickers)

    def my_companies(self) -> dict:
        return self.services.watch.my_companies()

    def alert_history(self, state: str = "attivi", limit: int = 500) -> list[dict]:
        return self.services.watch.alert_history(state, limit)

    # ------------------------------------------------------------------ giudizio e cosa è cambiato
    def company_verdict(self, ticker: str):
        return self.services.judgment.company_verdict(ticker)

    def thesis_inputs(self, ticker: str) -> tuple[dict, dict | None]:
        return self.services.judgment.thesis_inputs(ticker)

    def what_changed(self, ticker: str, sector=None, currency: str = "USD", note: dict | None = None,
                     valuation: dict | None = None) -> dict:
        return self.services.judgment.what_changed(ticker, sector, currency, note, valuation)

    # ------------------------------------------------------------------ stime degli analisti
    def estimates(self, ticker: str) -> pd.DataFrame:
        return self.services.estimates.estimates(ticker)

    def import_estimates_csv(self, text: str) -> tuple[int, list[str]]:
        return self.services.estimates.import_estimates_csv(text)

    def refresh_estimates(self, ticker: str) -> dict:
        return self.services.estimates.refresh_estimates(ticker)

    def estimates_overview(self, ticker: str, online: bool = True) -> dict:
        return self.services.estimates.estimates_overview(ticker, online)

    # ------------------------------------------------------------------ testata e valutazione a leve (API, scheda)
    # ------------------------------------------------------------------ il mio metodo
    def method_page(self) -> dict:
        return self.services.method.method_page()

    def formula_preview(self, formula: str, better: str | None = None, top: int = 10) -> dict:
        return self.services.method.formula_preview(formula, better, top)

    def create_custom_metric(self, label: str, formula: str, fmt: str = "num", better: str | None = None,
                             description: str = "") -> dict:
        return self.services.method.create_custom_metric(label, formula, fmt, better, description)

    def save_checklist_question(self, question: str, help_text: str = "", weight: float = 1.0,
                                item_id: int | None = None) -> None:
        return self.services.method.save_checklist_question(question, help_text, weight, item_id)

    def remove_checklist_question(self, item_id: int) -> None:
        return self.services.method.remove_checklist_question(item_id)

    # ------------------------------------------------------------------ diario delle decisioni
    def journal_benchmark(self) -> str:
        return self.services.portfolio.journal_benchmark()

    def journal_page(self) -> dict:
        return self.services.portfolio.journal_page()

    def add_journal_decision(self, ticker: str, action: str, reason: str, change_mind: str | None = None,
                             price: float | None = None, review_date=None) -> int:
        return self.services.portfolio.add_journal_decision(ticker, action, reason, change_mind, price, review_date)

    def review_journal_decision(self, dec_id: int, verdict: str, note: str = "") -> None:
        return self.services.portfolio.review_journal_decision(dec_id, verdict, note)

    def delete_journal_decision(self, dec_id: int) -> None:
        return self.services.portfolio.delete_journal_decision(dec_id)

    # ------------------------------------------------------------------ grafico del prezzo
    def price_chart(self, ticker: str, period: str = "1A", style: str = "Auto", bench: bool = False) -> dict:
        return self.services.price_chart.price_chart(ticker, period, style, bench)

    # ------------------------------------------------------------------ oggi
    def today(self, only_mine_documents: bool = False) -> dict:
        return self.services.today.today(only_mine_documents)

    # ------------------------------------------------------------------ confronta
    def compare_metrics(self, tickers: list[str]) -> pd.DataFrame:
        return self.services.compare.compare_metrics(tickers)

    def compare(self, tickers: list[str], years: int = 5) -> dict:
        return self.services.compare.compare(tickers, years)

    # ------------------------------------------------------------------ esplora
    def explore_universes(self) -> list[str]:
        return self.services.explore.explore_universes()

    def explore_catalog(self) -> dict:
        return self.services.explore.explore_catalog()

    def explore(self, universe: str = "S&P 500", rules: list[dict] | None = None, sectors: list[str] | None = None,
                verdicts: list[str] | None = None, min_scores: dict | None = None, text: str = "",
                sort: str = "auto", columns: list[str] | None = None) -> dict:
        return self.services.explore.explore(universe, rules, sectors, verdicts, min_scores, text, sort, columns)

    def save_screen(self, name: str, rules: list[dict], sectors: list[str] | None = None,
                    filters: dict | None = None) -> None:
        return self.services.explore.save_screen(name, rules, sectors, filters)

    def delete_screen(self, name: str) -> None:
        return self.services.explore.delete_screen(name)

    def financial_statements(self, ticker: str, view: str = "annual") -> dict:
        return self.services.statements.financial_statements(ticker, view)

    def company_peers(self, ticker: str) -> dict:
        return self.services.company.company_peers(ticker)

    def company_documents(self, ticker: str, news_limit: int = 10, filings_limit: int = 150) -> dict:
        return self.services.company.company_documents(ticker, news_limit, filings_limit)

    def company_header(self, ticker: str, profile: str = "general") -> dict:
        return self.services.company.company_header(ticker, profile)

    def lever_valuation(self, ticker: str, levers: dict | None = None, probabilities: dict | None = None) -> dict | None:
        return self.services.valuation.lever_valuation(ticker, levers, probabilities)

    def save_lever_valuation(self, ticker: str, levers: dict | None = None, probabilities: dict | None = None,
                             story: str = "", note: str = "") -> dict:
        return self.services.valuation.save_lever_valuation(ticker, levers, probabilities, story, note)

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

    # ------------------------------------------------------------------ il mio metodo
    def method_page(self) -> dict:
        return self.services.method.method_page()

    def formula_preview(self, formula: str, better: str | None = None, top: int = 10) -> dict:
        return self.services.method.formula_preview(formula, better, top)

    def create_custom_metric(self, label: str, formula: str, fmt: str = "num", better: str | None = None,
                             description: str = "") -> dict:
        return self.services.method.create_custom_metric(label, formula, fmt, better, description)

    def save_checklist_question(self, question: str, help_text: str = "", weight: float = 1.0,
                                item_id: int | None = None) -> None:
        return self.services.method.save_checklist_question(question, help_text, weight, item_id)

    def remove_checklist_question(self, item_id: int) -> None:
        return self.services.method.remove_checklist_question(item_id)

    # ------------------------------------------------------------------ diario delle decisioni
    def journal_benchmark(self) -> str:
        return self.services.portfolio.journal_benchmark()

    def journal_page(self) -> dict:
        return self.services.portfolio.journal_page()

    def add_journal_decision(self, ticker: str, action: str, reason: str, change_mind: str | None = None,
                             price: float | None = None, review_date=None) -> int:
        return self.services.portfolio.add_journal_decision(ticker, action, reason, change_mind, price, review_date)

    def review_journal_decision(self, dec_id: int, verdict: str, note: str = "") -> None:
        return self.services.portfolio.review_journal_decision(dec_id, verdict, note)

    def delete_journal_decision(self, dec_id: int) -> None:
        return self.services.portfolio.delete_journal_decision(dec_id)

    # ------------------------------------------------------------------ grafico del prezzo
    def price_chart(self, ticker: str, period: str = "1A", style: str = "Auto", bench: bool = False) -> dict:
        return self.services.price_chart.price_chart(ticker, period, style, bench)

    # ------------------------------------------------------------------ oggi
    def today(self, only_mine_documents: bool = False) -> dict:
        return self.services.today.today(only_mine_documents)

    # ------------------------------------------------------------------ confronta
    def compare_table(self, tickers: list[str]) -> pd.DataFrame:
        return self.services.company.compare_table(tickers)

    def compare_prices(self, tickers: list[str], years: int) -> pd.DataFrame:
        return self.services.company.compare_prices(tickers, years)

    def compare_series(self, tickers: list[str], column: str, index_to_first: bool = False) -> pd.DataFrame:
        return self.services.company.compare_series(tickers, column, index_to_first)

    # ------------------------------------------------------------------ notizie (SEC, feed, X)
    def news(self, days: int = 3, tickers=None) -> pd.DataFrame:
        return self.services.news.news(days, tickers)

    def recent_filings(self, days: int = 14) -> pd.DataFrame:
        return self.services.news.recent_filings(days)

    def news_stream(self, view: str = "all", ticker: str | None = None, groups=None, lang: str | None = None,
                    query: str | None = None, limit: int = 250) -> dict:
        return self.services.news.news_stream(view, ticker, groups, lang, query, limit)

    def filings_page(self, days: int = 7, mine: bool = False, important_only: bool = False, kinds=None) -> dict:
        return self.services.news.filings_page(days, mine, important_only, kinds)

    def x_accounts(self) -> dict:
        return self.services.news.x_accounts()

    def add_x_account(self, text: str) -> list[str]:
        return self.services.news.add_x_account(text)

    def remove_x_account(self, user: str) -> list[str]:
        return self.services.news.remove_x_account(user)

    def x_embed_html(self, user: str, dark: bool = False) -> str:
        return self.services.news.x_embed_html(user, dark)

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

    # ------------------------------------------------------------------ tavolo dei grandi investitori (solo informazione)
    def investor_panel(self, ticker: str) -> dict:
        return self.services.investor_panel.investor_panel(ticker)

    # ------------------------------------------------------------------ fondi, qualità, dati a mano (interfaccia nuova)
    def funds_page(self, fund_cik: str | None = None) -> dict:
        return self.services.data_admin.funds_page(fund_cik)

    def quality_page(self, days: int = 14) -> dict:
        return self.services.data_admin.quality_page(days)

    def manual_page(self, ticker: str, kind: str = "annual", unit: str = "milioni", share_unit: str = "milioni") -> dict:
        return self.services.data_admin.manual_page(ticker, kind, unit, share_unit)

    def save_manual_values(self, ticker: str, changes: dict, kind: str = "annual", unit: str = "milioni",
                           share_unit: str = "milioni", days_after_close: int | None = None) -> int:
        return self.services.data_admin.save_manual_values(ticker, changes, kind, unit, share_unit, days_after_close)

    def delete_manual_values(self, ticker: str, rows: list[dict]) -> int:
        return self.services.data_admin.delete_manual_values(ticker, rows)

    def custom_companies_view(self) -> dict:
        return self.services.data_admin.custom_companies_view()

    def add_custom_company(self, symbol: str, name: str = "", sector: str | None = None,
                           currency: str | None = "USD") -> str:
        return self.services.data_admin.add_custom_company(symbol, name, sector, currency)

    def remove_custom_company(self, ticker: str) -> None:
        return self.services.data_admin.remove_custom_company(ticker)

    # ------------------------------------------------------------------ backtest e laboratorio (interfaccia nuova)
    def backtest_page(self, max_points: int = 500) -> dict:
        return self.services.lab.backtest_page(max_points)

    def lab_options(self) -> dict:
        return self.services.lab.lab_options()

    def lab_preview(self, values: dict, period: str = "all") -> dict:
        return self.services.lab.lab_preview(values, period)

    def lab_run_key(self, values: dict, period: str = "all", with_research: bool = False) -> str:
        return self.services.lab.lab_run_key(values, period, with_research)

    def run_lab_experiment(self, values: dict, name: str = "", period: str = "all",
                           with_research: bool = False) -> list[int]:
        return self.services.lab.run_lab_experiment(values, name, period, with_research)

    def lab_experiments(self) -> dict:
        return self.services.lab.lab_experiments()

    def lab_compare(self, ids: list[int], max_points: int = 500) -> dict:
        return self.services.lab.lab_compare(ids, max_points)

    def delete_experiment(self, exp_id: int) -> None:
        return self.services.lab.delete_experiment(exp_id)

    def auto_strategy_page(self) -> dict:
        return self.services.lab.auto_strategy_page()

    # ------------------------------------------------------------------ funzioni "Altro" della scheda (interfaccia nuova)
    def company_metrics_view(self, ticker: str) -> dict:
        return self.services.dossier.company_metrics_view(ticker)

    def company_dividends(self, ticker: str) -> dict:
        return self.services.dossier.company_dividends(ticker)

    def company_ownership(self, ticker: str, everything: bool = False, limit: int = 300) -> dict:
        return self.services.dossier.company_ownership(ticker, everything, limit)

    def company_research(self, ticker: str) -> dict:
        return self.services.dossier.company_research(ticker)

    def save_company_research(self, ticker: str, fields: dict, scores: dict[int, float | None] | None = None,
                              thesis: str | None = None) -> None:
        return self.services.dossier.save_company_research(ticker, fields, scores, thesis)

    def company_auto(self, ticker: str, versions_limit: int = 500) -> dict:
        return self.services.dossier.company_auto(ticker, versions_limit)

    # ------------------------------------------------------------------ esportazioni della scheda
    def export_excel(self, ticker: str) -> bytes | None:
        return self.services.reports.export_excel(ticker)

    def export_pdf(self, ticker: str) -> bytes:
        return self.services.reports.export_pdf(ticker)

    # ------------------------------------------------------------------ pipeline (dall'idea alla decisione)
    def pipeline_stage(self, ticker: str) -> dict:
        return self.services.pipeline.pipeline_stage(ticker)

    def set_pipeline_stage(self, ticker: str, stage: str, note: str | None = None) -> None:
        return self.services.pipeline.set_pipeline_stage(ticker, stage, note)

    def remove_from_pipeline(self, ticker: str) -> None:
        return self.services.pipeline.remove_from_pipeline(ticker)

    def pipeline_board(self) -> dict:
        return self.services.pipeline.pipeline_board()

    def strategy_label(self, name: str) -> dict:
        return self.services.strategies.strategy_label(name)

    def strategies_list(self) -> list[dict]:
        return self.services.strategies.strategies_list()

    def strategy_run_key(self, name: str) -> str:
        return self.services.strategies.strategy_run_key(name)

    def run_strategy_backtest(self, name: str) -> None:
        return self.services.strategies.run_strategy_backtest(name)

    def strategy_page(self, name: str, max_points: int = 500) -> dict:
        return self.services.strategies.strategy_page(name, max_points)

    def strategy_holdings_view(self, name: str) -> dict:
        return self.services.strategies.strategy_holdings_view(name)

    def strategies_overview(self) -> dict:
        return self.services.strategies.strategies_overview()

    def strategy_builder_options(self) -> dict:
        return self.services.strategies.strategy_builder_options()

    def strategy_preview(self, definition: dict) -> dict:
        return self.services.strategies.strategy_preview(definition)

    def save_user_strategy(self, definition: dict, editing: str | None = None) -> str:
        return self.services.strategies.save_user_strategy(definition, editing)

    def delete_user_strategy(self, name: str) -> None:
        return self.services.strategies.delete_user_strategy(name)

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

