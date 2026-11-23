"""Test della verifica storica del giudizio (modules/verdict_research.py): database demo e dati sintetici, senza rete.
Nessuno sguardo al futuro, rendimenti successivi, portafogli, determinismo."""
import numpy as np
import pandas as pd
import pytest

from modules import verdict as vd
from modules import verdict_research as vr
from modules.market_data import MarketData


@pytest.fixture
def setup(cfg, demo_db, universe):
    data = MarketData.from_db(demo_db, cfg, universe.data_tickers())
    comp = demo_db.load_companies(only_index=False).set_index("ticker")
    return cfg, data, universe, comp


def _run(setup, dates, data=None):
    cfg, d, universe, comp = setup
    v, info = vr.collect_verdicts(cfg, data if data is not None else d, dates,
                                  lambda day: universe.tickers(as_of=day), comp)
    return v.sort_values(["date", "ticker"]).reset_index(drop=True), info


def test_quarter_dates():
    cal = pd.bdate_range("2020-01-01", "2021-02-15")
    d = vr.quarter_dates(cal, "2020-01-01")
    assert list(d.strftime("%Y-%m-%d")) == ["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31"]
    # trimestre che finisce di sabato: l'ultimo giorno di borsa prima
    cal2 = pd.bdate_range("2023-01-01", "2023-10-10")
    assert pd.Timestamp("2023-09-29") in vr.quarter_dates(cal2, "2023-01-01")


def test_no_look_ahead(setup):
    """Bilanci depositati e prezzi dopo la data non cambiano il giudizio di quella data."""
    cfg, data, universe, comp = setup
    day = vr.quarter_dates(data.calendar, "2024-01-01")[0]
    base, _ = _run(setup, [day])
    assert base["category"].isin(vd.LABELS).all() and len(base) > 5
    fin = data.financials
    future = fin[fin["filed"] <= day].copy()
    future["filed"] = day + pd.Timedelta(days=5)          # nuove versioni dei bilanci, depositate DOPO
    future["value"] = future["value"] * -3.0
    close = data.close.copy()
    close.loc[close.index > day] *= 10                   # prezzi futuri stravolti
    altered = MarketData(close=close, high=data.high, low=data.low, volume=data.volume,
                         financials=pd.concat([fin, future]).sort_values("filed", kind="stable")
                         .reset_index(drop=True),
                         benchmark=data.benchmark, risk_free_ticker=data.risk_free_ticker)
    after, _ = _run(setup, [day], altered)
    cols = ["ticker", "category", vd.QUALITY, vd.SOLIDITY, vd.PRICE, vd.FLAGS]
    pd.testing.assert_frame_equal(base[cols], after[cols])
    # e invece un deposito PRIMA della data lo cambia (il test vede davvero i bilanci)
    future["filed"] = day - pd.Timedelta(days=1)
    before = MarketData(close=data.close, high=data.high, low=data.low, volume=data.volume,
                        financials=pd.concat([fin, future]).sort_values("filed", kind="stable").reset_index(drop=True),
                        benchmark=data.benchmark, risk_free_ticker=data.risk_free_ticker)
    changed, _ = _run(setup, [day], before)
    assert not changed[cols].equals(base[cols])


def test_deterministic_and_members_of_the_day(setup):
    cfg, data, universe, comp = setup
    dates = vr.quarter_dates(data.calendar, "2023-01-01")[:3]
    a, info = _run(setup, dates)
    b, _ = _run(setup, dates)
    pd.testing.assert_frame_equal(a.drop(columns=[]), b)
    for d in dates:
        assert set(a.loc[a["date"] == d, "ticker"]) == set(universe.tickers(as_of=d))
    assert len(info["per_date"]) == 3 and (info["per_date"]["members"] > 0).all()
    assert info["per_date"]["erp"].notna().all()


def test_forward_returns_and_delisting():
    idx = pd.bdate_range("2020-01-01", periods=10)
    close = pd.DataFrame({"A": np.arange(1, 11, dtype=float), "B": [10.0] * 5 + [np.nan] * 5}, index=idx)
    data = MarketData(close=close.assign(SPY=100.0 + np.arange(10)), high=close, low=close, volume=close,
                      financials=pd.DataFrame(), benchmark="SPY")
    c = vr.prepare_close(data)
    assert c["B"].iloc[-1] == 10.0  # delistato: resta l'ultimo prezzo
    v = pd.DataFrame({"date": [idx[1]] * 2, "ticker": ["A", "B"], "has_price": [True, True], "category": ["x", "y"]})
    out = vr.attach_forward_returns(v, c, vr.benchmark_close(data), {1: 3})
    # dal giorno dopo (prezzo 3) a 3 giorni più avanti (prezzo 6): +100%
    assert out.loc[0, "r_1"] == pytest.approx(1.0)
    assert out.loc[1, "r_1"] == pytest.approx(0.0)
    assert out.loc[0, "ew_1"] == pytest.approx(0.5)
    assert out.loc[0, "spy_1"] == pytest.approx(105 / 102 - 1)
    t = vr.category_table(out, {1: 3})
    a = t.set_index("category").loc["x"]
    assert a["excess_ew"] == pytest.approx(0.5) and a["beat_ew"] == 1.0
    # oltre la fine dei dati: vuoto
    late = vr.attach_forward_returns(v.assign(date=idx[8]), c, vr.benchmark_close(data), {1: 3})
    assert late["r_1"].isna().all()


def test_group_portfolio():
    idx = pd.bdate_range("2020-01-01", periods=12)
    close = pd.DataFrame({"A": np.linspace(10, 21, 12), "B": [10.0] * 12}, index=idx)
    v = pd.DataFrame({"date": [idx[0], idx[0], idx[5], idx[5]], "ticker": ["A", "B", "A", "B"],
                      "category": [vd.CANDIDATE, vd.AVOID, vd.AVOID, vd.CANDIDATE], "has_price": True})
    p = vr.group_portfolio(v, close, vr.category_selector(vd.CANDIDATE), cost_per_side=0.0)
    # A dal giorno 1 al giorno 6 (11 -> 16), poi B fermo
    assert p.equity.iloc[0] == pytest.approx(1.0)
    assert p.equity.iloc[-1] == pytest.approx(16 / 11)
    assert p.holdings.tolist() == [1, 1]
    net = vr.group_portfolio(v, close, vr.category_selector(vd.CANDIDATE), cost_per_side=0.01)
    assert net.equity_net.iloc[-1] < p.equity.iloc[-1]


def test_full_study_on_demo(setup):
    cfg, data, universe, comp = setup
    dates = vr.quarter_dates(data.calendar, "2022-01-01")
    v, info = _run(setup, dates)
    res = vr.analyze_study(v, data, info, horizons={3: 63, 12: 252})
    assert not res.categories.empty and set(res.categories["months"]) <= {3, 12}
    assert (res.categories["obs"] > 0).all()
    assert set(res.categories_halves["half"]) == {"Prima metà", "Seconda metà"}
    assert vr.SHORT_LABELS[vd.CANDIDATE] in res.portfolios
    agree = vr.category_agreement(v, v)
    assert np.trace(agree.to_numpy()) == agree.to_numpy().sum()
