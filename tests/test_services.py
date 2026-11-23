"""Servizi dei dati dell'app (modules/services/): ognuno usabile da solo su un database demo temporaneo, senza
Streamlit né DashboardData; la facciata DashboardData dà gli stessi risultati e rispetta i metodi ridefiniti."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from modules.dashboard_data import DashboardData
from modules.services import AppData
from modules.services.company import CompanyService
from modules.services.data import SERVICES, routes
from modules.services.my_method import MethodService
from modules.services.news import NewsService
from modules.services.portfolio import PortfolioService
from modules.services.reports import ReportService
from modules.services.strategies import StrategyService
from modules.services.valuation import ValuationService
from modules.services.watch import WatchService


@pytest.fixture
def app(cfg, demo_db, tmp_path):
    cfg["etf"] = {**cfg.get("etf", {}), "cache_dir": str(tmp_path / "etf"), "fetch_pause_seconds": 0}
    return AppData(cfg, demo_db, tmp_path)


def test_no_streamlit_and_routes_are_unique():
    """Nessun servizio importa Streamlit; ogni metodo pubblico appartiene a un solo servizio ed è nella facciata."""
    import importlib

    for module, _ in SERVICES.values():
        src = importlib.import_module(module).__file__
        assert "streamlit" not in open(src, encoding="utf-8").read()
    r = routes()  # solleva un errore se un nome è in due servizi
    assert {"peers", "cost_of_capital", "portfolio_view", "watchlist_table", "strategy_result", "news",
            "quality_issues", "checklist_scores"} <= set(r)
    assert all(callable(getattr(DashboardData, name)) for name in r)


def test_company_service_alone(app):
    svc = CompanyService(app)
    t = app.index_tickers[0]
    fin = svc.financials(t)
    assert not fin.empty and {"revenue_growth", "net_margin", "ttm"} <= set(fin.columns)
    assert not svc.financials(t, quarterly=True).empty
    table, label, cols = svc.peers(t)
    assert t in set(table["ticker"]) and label and cols
    cm = svc.company_metrics(t)
    assert len(cm) > 20 and {"value", "sector_median", "rank_index"} <= set(cm.columns)
    assert not svc.dividend_history(t).empty
    assert svc.sec_concepts(t).empty  # nessun archivio SEC nei test
    assert set(svc.compare_table(app.index_tickers[:3]).columns) >= set(app.index_tickers[:3])
    assert isinstance(svc.insider_summary(t), dict) and "365" in svc.insider_summary(t)
    assert not svc.historical_multiples(t, step_days=63, years=2).empty


def test_valuation_service_alone(app):
    svc = ValuationService(app)
    t = app.index_tickers[0]
    erp = svc.implied_erp()
    assert 0 < erp["erp"] < 0.2 and app._erp is erp  # la cache sta sul contesto, come prima
    assert not svc.industry_table().empty and app._industry is not None
    coc = svc.cost_of_capital(t)
    assert np.isfinite(coc["wacc"]) and coc["risk_free"] == erp["risk_free"]
    setup = svc.valuation_setup(t)
    assert setup is not None and set(setup["scenarios"]) == {"Pessimista", "Base", "Ottimista"}
    assert len(svc.plausibility(t, setup["scenarios"]["Base"])) >= 4
    dcf = svc.dcf_inputs(t)  # legge le note dal servizio "le mie aziende" passando dal contesto
    assert dcf["saved"] is False and "wacc" in dcf["system"]
    assert svc.valuation(t)["valuation"] is not None


def test_watch_and_method_services_alone(app):
    w, m = WatchService(app), MethodService(app)
    t = app.index_tickers[1]
    w.follow(t)
    w.save_note(t, fair_value=10.0, thesis="tesi")
    assert w.watchlist() == [t] and w.note(t)["fair_value"] == 10.0
    table = w.watchlist_table()
    assert list(table["ticker"]) == [t] and table["has_thesis"].iloc[0]
    ex = w.explore_table()
    assert ex.loc[t, "followed"] and ex.loc[t, "my_fair_value"] == 10.0
    assert m.profile() and not m.checklist_items().empty
    assert m.manual_table(t).shape[0] > 5
    m.save_custom_metric({"key": "my_double_roic", "label": "ROIC x2", "formula": "roic * 2",
                          "group": "Personali", "better": "high", "kind": "pct"})
    assert app._metrics is None  # il catalogo si ricalcola
    assert "my_double_roic" in app.metrics().columns
    assert m.delete_custom_metric("my_double_roic") == []


def test_portfolio_news_reports_strategies_alone(app):
    p, n, r, s = PortfolioService(app), NewsService(app), ReportService(app), StrategyService(app)
    assert p.portfolio_view()["tx"].empty
    t = app.index_tickers[0]
    app.db.add_transaction({"ticker": t, "kind": "BUY", "date": pd.Timestamp("2025-01-06"), "quantity": 2,
                            "price": 100.0, "currency": "USD", "fx": 1.05, "fees": 0.0, "note": None})
    view = p.portfolio_view()
    assert list(view["positions"]["ticker"]) == [t]
    assert p.portfolio_look_through()["status"] == "no_etf"
    assert p.etf_cache_path().name.startswith("composizioni_")
    assert isinstance(n.news(days=3), pd.DataFrame) and isinstance(n.recent_filings(), pd.DataFrame)
    assert r.backtest_equity().empty and r.factor_weights() == {} and r.quality_for(t).empty
    assert s.decisions().empty and s.orders().empty and s.portfolio_risk() == {}
    cat = s.strategy_catalog()
    assert cat and s.strategy_result(next(iter(cat))) is None


def test_app_routes_names_to_services(app):
    """AppData da solo risponde con i nomi dei metodi della webapp; gli altri attributi mancanti restano errori."""
    t = app.index_tickers[0]
    assert_frame_equal(app.peers(t)[0], app.services.company.peers(t)[0])
    assert app.services.company is app.services.company  # creato una volta
    with pytest.raises(AttributeError):
        app.non_esiste
    with pytest.raises(AttributeError):
        app._privato


def test_facade_matches_services(cfg, demo_db, tmp_path):
    cfg["etf"] = {**cfg.get("etf", {}), "cache_dir": str(tmp_path / "etf"), "fetch_pause_seconds": 0}
    d = DashboardData(cfg, demo_db, tmp_path)
    app = AppData(cfg, demo_db, tmp_path)
    t = d.index_tickers[2]
    assert_frame_equal(d.explore_table(), WatchService(app).explore_table())
    assert_frame_equal(d.company_metrics(t), CompanyService(app).company_metrics(t))
    assert d.cost_of_capital(t) == pytest.approx(ValuationService(app).cost_of_capital(t), nan_ok=True)


def test_facade_overrides_reach_services(cfg, demo_db, tmp_path, monkeypatch):
    """Un metodo ridefinito nella facciata (sottoclasse o test) vale anche dentro i servizi."""
    fake = pd.DataFrame({"ticker": ["AAA", "AAA"], "weight": [0.1, 0.3], "fund": ["x", "y"]})
    monkeypatch.setattr(DashboardData, "fund_positions", lambda self: fake)
    d = DashboardData(cfg, demo_db, tmp_path)
    assert list(d.fund_holders("AAA")["weight"]) == [0.3, 0.1]

    class Fixed(DashboardData):
        def implied_erp(self):
            return {"erp": 0.05, "computed": False, "risk_free": 0.04}

    f = Fixed(cfg, demo_db, tmp_path)
    assert f.cost_of_capital(f.index_tickers[0])["erp"] == 0.05
