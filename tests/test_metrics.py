"""Test del catalogo delle metriche (numeri calcolati a mano su dati sintetici)."""
import numpy as np
import pandas as pd
import pytest

from modules.market_data import MarketData
from modules.metrics import METRICS, compute_metrics, format_value, percentile_ranks


def _fin(ticker, period_end, filed, source, **values):
    return [(ticker, period_end, k, float(v), filed, source) for k, v in values.items()]


def _data(price: float = 50.0) -> MarketData:
    dates = pd.bdate_range("2023-01-02", "2024-09-30")
    close = np.linspace(40, price, len(dates))
    px = pd.concat([pd.DataFrame({"date": dates, "ticker": t, "close": close * k, "high": close * k,
                                  "low": close * k, "volume": 1e6}) for t, k in (("X", 1.0), ("SPY", 8.0))])
    rows = (
        _fin("X", "2020-12-31", "2021-02-15", "sec", revenue=700)
        + _fin("X", "2023-06-30", "2023-08-01", "sec_ttm", revenue=900, net_income=90, free_cash_flow=80, shares=10)
        + _fin("X", "2023-12-31", "2024-02-15", "sec", revenue=950, net_income=95, shares=10)
        + _fin("X", "2024-06-30", "2024-08-01", "sec_ttm",
               revenue=1000, net_income=100, free_cash_flow=60, operating_cash_flow=90, operating_income=150,
               depreciation=50, gross_profit=400, pretax_income=125, tax_provision=25, interest_expense=-10,
               dividends_paid=20, buybacks=10, shares=9.5, total_debt=300, cash=100, equity=400,
               total_assets=1000, current_assets=250, current_liabilities=125)
    )
    fin = pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
    return MarketData.from_long(px, fin, benchmark="SPY")


def test_metrics_by_hand():
    m = compute_metrics(_data(), ["X"]).loc["X"]
    mcap, ev = 50 * 9.5, 50 * 9.5 + 300 - 100
    assert m["market_cap"] == pytest.approx(mcap) and m["enterprise_value"] == pytest.approx(ev)
    assert m["pe"] == pytest.approx(mcap / 100) and m["fcf_yield"] == pytest.approx(60 / mcap)
    assert m["ev_ebit"] == pytest.approx(ev / 150) and m["ev_ebitda"] == pytest.approx(ev / 200)
    assert m["shareholder_yield"] == pytest.approx(30 / mcap)
    assert m["operating_margin"] == pytest.approx(0.15) and m["gross_margin"] == pytest.approx(0.4)
    assert m["roic"] == pytest.approx(150 * (1 - 0.2) / (400 + 300 - 100))   # tasse 25/125 = 20%
    assert m["revenue_growth"] == pytest.approx(1000 / 900 - 1)             # stesso trimestre dell'anno prima
    assert m["fcf_growth"] == pytest.approx(60 / 80 - 1)
    assert m["share_change"] == pytest.approx(9.5 / 10 - 1)                 # riacquisti
    assert m["current_ratio"] == pytest.approx(2.0) and m["interest_coverage"] == pytest.approx(15.0)
    assert m["net_debt_ebitda"] == pytest.approx(200 / 200)
    assert m["revenue_cagr_3y"] != m["revenue_cagr_3y"]                     # servono 4 esercizi: vuoto
    assert m["beta"] == pytest.approx(1.0, abs=1e-6)                        # stesso andamento del benchmark
    assert m["statement_date"] == pd.Timestamp("2024-06-30")


def test_metrics_are_point_in_time():
    data = _data()
    early = compute_metrics(data.as_of("2024-07-15"), ["X"]).loc["X"]      # il 10-Q di giugno non è ancora uscito
    assert early["statement_date"] == pd.Timestamp("2023-12-31")
    assert early["pe"] == pytest.approx(early["price"] * 10 / 95)


def test_meaningless_ratios_are_empty():
    data = _data()
    fin = data.financials.copy()
    fin.loc[(fin["metric"] == "net_income") & (fin["period_end"] == "2024-06-30"), "value"] = -50.0
    fin.loc[(fin["metric"] == "equity"), "value"] = -10.0
    m = compute_metrics(MarketData(data.close, data.high, data.low, data.volume, fin, benchmark="SPY"), ["X"]).loc["X"]
    assert np.isnan(m["pe"]) and np.isnan(m["roe"]) and np.isnan(m["pb"]) and np.isnan(m["debt_to_equity"])
    assert m["earnings_yield"] < 0                                          # il rendimento invece ha senso


def test_ranks_and_format():
    df = pd.DataFrame({"pe": [10.0, 20.0, 30.0], "roe": [0.1, 0.3, 0.2]}, index=["A", "B", "C"])
    r = percentile_ranks(df)
    assert r.loc["A", "pe"] == 1.0 and r.loc["B", "roe"] == 1.0            # P/E basso = meglio
    by_sector = percentile_ranks(df, {"A": "S1", "B": "S1", "C": "S2"})
    assert by_sector.loc["C", "pe"] == 1.0                                  # unica del suo settore
    assert format_value("pe", 12.34) == "12.3x" and format_value("roe", 0.1234) == "12.3%"
    assert format_value("market_cap", 2.5e12) == "2.5 T$" and format_value("pe", np.nan) == "–"
    assert len({m.key for m in METRICS}) == len(METRICS)


def test_metrics_on_demo(demo_data, universe):
    m = compute_metrics(demo_data, universe.tickers())
    assert len(m) == len(universe.tickers())
    for key in ("pe", "ev_ebit", "roic", "current_ratio", "dividend_yield", "revenue_growth", "volatility"):
        assert m[key].notna().mean() > 0.8, key


def test_piotroski_by_hand():
    from modules.metrics import piotroski

    prev = pd.Series({"net_income": 50.0, "total_assets": 1000.0, "total_debt": 300.0, "current_assets": 200.0,
                      "current_liabilities": 200.0, "gross_profit": 300.0, "revenue": 900.0, "shares": 100.0})
    cur = pd.Series({"net_income": 80.0, "operating_cash_flow": 120.0, "gross_profit": 360.0, "revenue": 1000.0})
    latest = pd.Series({"total_assets": 1000.0, "total_debt": 250.0, "current_assets": 260.0,
                        "current_liabilities": 200.0, "shares": 98.0})
    assert piotroski(cur, latest, prev) == 9.0                       # tutto in miglioramento
    worse = latest.copy()
    worse.update({"total_debt": 400.0, "shares": 110.0})              # più debito, nuove azioni
    assert piotroski(cur, worse, prev) == 7.0
    no_gross = cur.drop("gross_profit")                               # 8 controlli su 9: riportato a 9
    assert piotroski(no_gross, latest, prev) == 9.0
    assert np.isnan(piotroski(cur, latest, pd.Series(dtype=float)))   # senza l'anno prima: troppi controlli mancanti


def test_altman_by_hand():
    from modules.metrics import altman_z

    latest = pd.Series({"total_assets": 1000.0, "current_assets": 400.0, "current_liabilities": 200.0,
                        "retained_earnings": 300.0, "total_liabilities": 500.0, "equity": 500.0})
    cur = pd.Series({"operating_income": 100.0, "revenue": 1200.0})
    z = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * (1500 / 500) + 1.0 * 1.2
    assert altman_z(latest, cur, 1500.0) == pytest.approx(z)
    assert altman_z(latest.drop("total_liabilities"), cur, 1500.0) == pytest.approx(z)   # passività = attivo - PN
    assert np.isnan(altman_z(latest, cur.drop("operating_income"), 1500.0))


def test_partial_last_day_keeps_prices():
    """Aggiornamento a mercato aperto: l'ultimo giorno solo per alcuni titoli; gli altri usano il prezzo precedente."""
    from modules.metrics import price_metrics

    dates = pd.bdate_range("2025-01-01", "2026-10-30")
    px = pd.concat([pd.DataFrame({"date": dates, "ticker": t, "close": 10.0, "high": 10.0, "low": 10.0, "volume": 1})
                    for t in ("A", "B")])
    extra = pd.DataFrame({"date": [pd.Timestamp("2026-11-02")], "ticker": ["A"], "close": [11.0], "high": [11.0],
                          "low": [11.0], "volume": [1]})
    data = MarketData.from_long(pd.concat([px, extra]), pd.DataFrame())
    pm = price_metrics(data, ["A", "B"])
    assert pm.at["A", "price"] == 11.0 and pm.at["B", "price"] == 10.0


def test_fx_ahead_does_not_empty_metrics(tmp_path):
    """Un cambio (EURUSD=X) scaricato 10 giorni più avanti dei titoli non sposta l'ultima data: prima tutte le
    metriche di prezzo diventavano vuote (oltre 5 sedute senza prezzo). Un titolo europeo con una seduta in più
    del benchmark tiene il suo ultimo prezzo; l'istantanea parquet e `as_of` restano coerenti."""
    from modules.factors import compute_factors
    from modules.metrics import price_metrics

    base = _data()
    last = base.last_date
    fx_dates = pd.bdate_range(last - pd.Timedelta(days=30), last + pd.Timedelta(days=14))
    assert len(fx_dates[fx_dates > last]) >= 10
    fx = pd.DataFrame({"date": fx_dates, "ticker": "EURUSD=X", "close": 1.08, "high": 1.08, "low": 1.08,
                       "volume": 0})
    eu_day = last + pd.offsets.BDay(1)
    eu = pd.DataFrame({"date": list(base.close.index[-300:]) + [eu_day], "ticker": "ENI.MI", "close": 14.0,
                       "high": 14.0, "low": 14.0, "volume": 1})
    eu.loc[eu["date"] == eu_day, "close"] = 15.0
    long = base.close.stack().rename("close").reset_index()
    long.columns = ["date", "ticker", "close"]
    long = long.assign(high=long["close"], low=long["close"], volume=1.0)
    data = MarketData.from_long(pd.concat([long, fx, eu]), base.financials, benchmark="SPY")

    assert data.last_date == eu_day                     # i cambi non contano, il titolo europeo sì
    pm = price_metrics(data, ["X", "SPY", "ENI.MI"])
    assert pm.at["X", "price"] == pytest.approx(50.0) and pm.at["ENI.MI", "price"] == 15.0
    m = compute_metrics(data, ["X"]).loc["X"]
    assert np.isfinite(m["pe"]) and np.isfinite(m["ret_1y"]) and np.isfinite(m["volatility"])
    assert compute_metrics(base, ["X"]).loc["X", "pe"] == pytest.approx(m["pe"])
    only_fx = MarketData.from_long(pd.concat([long, fx]), base.financials, benchmark="SPY")
    assert only_fx.last_date == last
    assert np.isfinite(compute_factors(only_fx, ["X"]).loc["X", "earnings_yield"])   # fattori della strategia
    assert data.as_of(data.last_date).last_date == data.last_date
    data.save_snapshot(tmp_path / "snap", "k")
    again = MarketData.load_snapshot(tmp_path / "snap", "k")
    assert again.last_date == data.last_date
    assert compute_metrics(again, ["X"]).loc["X", "pe"] == pytest.approx(m["pe"])
