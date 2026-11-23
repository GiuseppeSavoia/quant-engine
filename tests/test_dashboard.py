"""Test del modulo dati della webapp (l'interfaccia Streamlit si verifica a mano)."""
import numpy as np
import pytest
import pandas as pd

from modules.dashboard_data import DashboardData
from modules.strategy import Strategy


def _dashboard(cfg, demo_db, universe, tmp_path, with_run=True):
    if with_run:  # come un'esecuzione di main.py: decisioni dell'ultima analisi nel database
        from modules.market_data import MarketData

        data = MarketData.from_db(demo_db, cfg, universe.tickers())
        res = Strategy(cfg, tickers=universe.tickers(), sectors=universe.sectors()).run(data)
        demo_db.log_decisions("20260101_000000", res.decisions)
    return DashboardData(cfg, demo_db, tmp_path)


def test_explore_table_has_one_row_per_company(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path)
    df = d.explore_table()
    assert sorted(df.index) == sorted(universe.tickers())
    assert df["market_cap"].gt(0).all() and df["name"].notna().all() and df["sector"].notna().all()
    assert not df["followed"].any()
    # lo split 10:1 della demo non deve gonfiare la capitalizzazione: azioni sulla base dei prezzi
    assert df.loc["NVDA", "pe"] < 200
    d.follow("NVDA")
    d.save_note("NVDA", fair_value=float(df.loc["NVDA", "price"]) * 1.5)
    df = d.explore_table()
    assert df.loc["NVDA", "followed"] and df.loc["NVDA", "my_margin"] == pytest.approx(0.5)


def test_financials_and_prices(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    fin = d.financials("AAPL")
    assert fin.index.is_monotonic_increasing
    assert {"revenue", "free_cash_flow", "net_margin", "revenue_growth", "debt_to_equity"} <= set(fin.columns)
    assert np.isclose(fin["net_margin"].iloc[-1], fin["net_income"].iloc[-1] / fin["revenue"].iloc[-1])
    # in coda gli ultimi 12 mesi dai 10-Q dell'anno in corso, con la crescita sullo stesso trimestre
    assert fin["ttm"].iloc[-1] and not fin["ttm"].iloc[:-1].any()
    assert fin.index[-1] > fin.index[-2] and np.isfinite(fin["revenue_growth"].iloc[-1])
    px = d.prices("AAPL")
    assert {"sma50", "sma200", "drawdown"} <= set(px.columns) and (px["drawdown"] <= 0).all()
    versions = d.financial_versions("AAPL")
    assert versions.groupby(["period_end", "metric"])["filed"].nunique().max() > 1  # più versioni dello stesso anno


def test_valuation_and_decisions(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path)
    v = d.valuation("MSFT")
    assert v["valuation"].fair_value is not None and 0 <= v["factors"].total <= 1
    assert d.run_id == "20260101_000000"
    dec = d.decisions()
    assert len(dec) == len(universe.tickers()) and dec["name"].notna().all()
    assert len(d.decision_history("MSFT")) == 1
    assert d.orders().empty and d.backtest_equity().empty   # nessun ordine né report in questa prova


def test_dashboard_without_runs(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    assert d.run_id is None and d.decisions().empty
    pd.testing.assert_frame_equal(d.research_summary(), pd.DataFrame())


def test_confidence_breakdown_explains_the_decision(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path)
    dec = d.decisions()
    evaluated = dec[dec["confidence"].notna()]
    for t in evaluated["ticker"]:
        b = d.confidence_breakdown(t)
        assert b["evaluated"]
        assert sum(b["parts"].values()) == pytest.approx(b["confidence"], abs=1e-3)  # i contributi sommano
        if b["action"] != "BUY" and b["margin_needed"] is not None:
            # con il margine richiesto (e il resto invariato) si arriva esattamente all'obiettivo
            v_needed = b["margin_needed"] / cfg["confidence"]["full_value_margin"]
            reached = b["confidence"] + b["weights"]["value"] * (v_needed - b["value_score"])
            assert reached == pytest.approx(b["target"], abs=1e-3)
    assert d.confidence_breakdown("NON_ESISTE") is None


def test_fair_value_history_is_point_in_time(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    hist = d.fair_value_history("MSFT", step_days=63)
    assert len(hist) > 5 and hist.index.is_monotonic_increasing
    assert hist.index[-1] == d.data.last_date
    # l'ultimo punto coincide con la valutazione di oggi
    assert hist["fair_value"].iloc[-1] == pytest.approx(d.valuation("MSFT")["valuation"].fair_value)
    # il fair value cambia soprattutto quando esce un nuovo 10-K
    filings = d.filing_dates("MSFT")
    assert len(filings) > 3 and filings.is_monotonic_increasing
    assert d.trades_for("MSFT").empty  # nessun report di backtest in questa prova


def test_portfolio_risk(cfg, demo_db, universe, tmp_path):
    from modules.market_data import MarketData

    data = MarketData.from_db(demo_db, cfg, universe.tickers())
    res = Strategy(cfg, tickers=universe.tickers(), sectors=universe.sectors()).run(data)
    orders = [{"ticker": t, "weight": float(w), "shares": 1, "reference_price": 1.0, "order_type": "MARKET"}
              for t, w in res.weights.items()]
    demo_db.log_decisions("20260101_000000", res.decisions)
    if orders:
        demo_db.log_orders("20260101_000000", orders)
    risk = DashboardData(cfg, demo_db, tmp_path).portfolio_risk()
    if not orders:
        assert risk == {}
        return
    assert risk["measures"]["exposure"] == pytest.approx(res.weights.sum())
    assert {"Covid 2020", "Mercato -20% (beta)"} & set(risk["stress"]["scenario"])


def test_company_metrics_and_quarterly_financials(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    cm = d.company_metrics("AAPL").set_index("key")
    assert {"value", "sector_median", "index_median", "rank_sector", "rank_index"} <= set(cm.columns)
    assert np.isfinite(cm.at["pe", "value"]) and 0 <= cm.at["pe", "rank_index"] <= 1
    q = d.financials("AAPL", quarterly=True)
    assert q["ttm"].any() and (~q["ttm"]).any()               # trimestri ed esercizi insieme
    assert {"operating_margin", "gross_margin", "revenue_growth"} <= set(q.columns)
    hist = d.historical_multiples("AAPL", step_days=63, years=3)
    assert len(hist) >= 10 and hist["pe"].notna().sum() >= 5 and hist.index.is_monotonic_increasing


def test_user_dcf_and_notes(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    inp = d.dcf_inputs("AAPL")
    assert not inp["saved"] and inp["current"] == inp["system"]
    c = inp["current"]
    res = d.user_dcf("AAPL", c["fcf"], c["growth"], c["wacc"], c["terminal_growth"], c["years"],
                     inp["net_debt"], inp["shares"], inp["price"])
    val = d.valuation("AAPL")["valuation"]
    assert res["fair_value"] == pytest.approx(val.fair_value)  # stesse ipotesi = stesso DCF del sistema
    assert res["grid"].shape == (5, 5) and res["grid"].iloc[2, 2] == pytest.approx(res["fair_value"])
    assert res["grid"].iloc[0, 2] > res["grid"].iloc[4, 2]     # WACC più alto = fair value più basso
    higher = d.user_dcf("AAPL", c["fcf"], c["growth"] + 0.05, c["wacc"], c["terminal_growth"], c["years"],
                        inp["net_debt"], inp["shares"], inp["price"])
    assert higher["fair_value"] > res["fair_value"]
    if res["implied_growth"] is not None:                       # al prezzo di oggi il fair value è il prezzo
        at_price = d.user_dcf("AAPL", c["fcf"], res["implied_growth"], c["wacc"], c["terminal_growth"],
                              c["years"], inp["net_debt"], inp["shares"], inp["price"])
        assert at_price["fair_value"] == pytest.approx(inp["price"], rel=1e-4)

    d.save_note("AAPL", fair_value=123.0, assumptions={"growth": 0.07, "wacc": 0.09})
    d.save_note("AAPL", thesis="Ecosistema e riacquisti")
    note = d.note("AAPL")
    assert note["fair_value"] == 123.0 and note["thesis"] == "Ecosistema e riacquisti"
    inp = d.dcf_inputs("AAPL")
    assert inp["saved"] and inp["current"]["growth"] == 0.07 and inp["current"]["wacc"] == 0.09
    assert inp["current"]["years"] == inp["system"]["years"]   # i campi non salvati restano quelli del sistema
    assert d.note("MSFT") == {}


def test_watchlist_and_alerts(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    assert d.watchlist() == [] and d.watchlist_table().empty
    d.follow(["AAPL", "MSFT"])
    d.follow("NVDA")
    d.follow("AAPL")                                            # già seguita: nessun doppione
    assert sorted(d.watchlist()) == ["AAPL", "MSFT", "NVDA"]
    d.follow("MSFT", on=False)
    price = float(d.data.prices("AAPL").iloc[-1])
    d.save_note("AAPL", fair_value=price * 1.2, thesis="tesi")
    d.save_note("NVDA", fair_value=price * 100)                  # niente avviso di prezzo vicino: è lontano
    table = d.watchlist_table().set_index("ticker")
    assert list(table.index) == ["AAPL", "NVDA"]
    aapl = table.loc["AAPL"]
    assert aapl["my_margin"] == pytest.approx(0.2) and aapl["has_thesis"]
    assert ("FV", "sotto il tuo fair value: margine +20%") in aapl["alerts"]
    assert aapl["last_form"] in ("10-K", "10-Q") and pd.notna(aapl["last_filing"])
    # soglie a zero: ogni movimento e ogni bilancio "recente" diventano avvisi
    d.cfg["watchlist"].update(big_move_1m=0.0, new_filing_days=10_000)
    icons = {icon for icon, _ in d.watchlist_table().set_index("ticker").loc["NVDA", "alerts"]}
    assert {"DOC", "MOV"} <= icons


def test_compare(cfg, demo_db, universe, tmp_path):
    d = _dashboard(cfg, demo_db, universe, tmp_path, with_run=False)
    tickers = ["AAPL", "MSFT", "NVDA"]
    t = d.compare_table(tickers).set_index("key")
    assert set(tickers) <= set(t.columns)
    pe = t.loc["pe", tickers].astype(float)
    assert t.at["pe", "best"] == pe.idxmin()                          # P/E: vince il più basso
    assert pd.isna(t.at["ret_1m", "best"])                            # nessun verso migliore
    px = d.compare_prices(tickers, years=1)
    assert list(px.columns) == tickers and (px.iloc[0] == 1).all()
    rev = d.compare_series(tickers, "revenue", index_to_first=True)
    assert list(rev.dropna().iloc[0]) == pytest.approx([100.0] * 3)
    margin = d.compare_series(tickers, "net_margin")
    assert margin.notna().any().all()
