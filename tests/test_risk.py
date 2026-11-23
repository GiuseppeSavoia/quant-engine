"""Test dei limiti di rischio, delle misure e delle prove di stress (dati sintetici)."""
import numpy as np
import pandas as pd
import pytest

from modules.market_data import MarketData
from modules.risk import RiskManager, cap_sectors

SECTORS = {"A": "Tech", "B": "Tech", "C": "Energy", "D": "Health"}


def _data(vols: dict[str, float], days: int = 400, seed: int = 0) -> MarketData:
    """SPY con volatilità 16%; ogni titolo = SPY + rumore della volatilità indicata."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=days)
    market = rng.normal(0.0003, 0.16 / np.sqrt(252), days)
    rows = []
    for t, vol in {"SPY": 0.0, **vols}.items():
        noise = rng.normal(0, vol / np.sqrt(252), days) if vol else 0.0
        close = 100 * np.exp(np.cumsum(market + noise))
        rows.append(pd.DataFrame({"date": dates, "ticker": t, "close": close, "high": close, "low": close,
                                  "volume": 1e6}))
    return MarketData.from_long(pd.concat(rows), pd.DataFrame(), benchmark="SPY")


def test_cap_sectors_redistributes_to_other_sectors():
    w = pd.Series({"A": 0.3, "B": 0.3, "C": 0.2, "D": 0.2})
    out = cap_sectors(w, SECTORS, max_sector=0.4, max_weight=0.35)
    assert out[["A", "B"]].sum() == pytest.approx(0.4)
    assert out[["C", "D"]].tolist() == pytest.approx([0.3, 0.3])
    assert out.sum() == pytest.approx(1.0)


def test_cap_sectors_leaves_cash_when_nowhere_to_go():
    out = cap_sectors(pd.Series({"A": 0.5, "B": 0.5}), SECTORS, max_sector=0.3, max_weight=1.0)
    assert out.sum() == pytest.approx(0.3)
    assert cap_sectors(pd.Series({"A": 0.5}), SECTORS, 1.0, 1.0)["A"] == 0.5  # 1.0 = nessun tetto


def test_volatility_target_scales_down_never_up():
    data = _data({"A": 0.40, "C": 0.40})
    w = pd.Series({"A": 0.5, "C": 0.5})
    low = RiskManager({"target_volatility": 0.10, "vol_lookback_days": 252}, SECTORS)
    out, dec = low.apply(w, data)
    assert dec.volatility > 0.10 and dec.vol_scale < 1
    assert out.sum() == pytest.approx(0.10 / dec.volatility)
    high = RiskManager({"target_volatility": 0.90}, SECTORS)
    out, dec = high.apply(w, data)
    assert out.sum() == pytest.approx(1.0) and dec.vol_scale == 1.0


def test_drawdown_limit():
    data = _data({"A": 0.2})
    rm = RiskManager({"drawdown_limit": -0.15, "drawdown_exposure": 0.5}, SECTORS)
    w = pd.Series({"A": 1.0})
    assert rm.apply(w, data, drawdown=-0.10)[0].sum() == pytest.approx(1.0)
    out, dec = rm.apply(w, data, drawdown=-0.20)
    assert out.sum() == pytest.approx(0.5) and dec.drawdown_scale == 0.5 and dec.notes
    assert RiskManager({}, SECTORS).apply(w, data, drawdown=-0.9)[0].sum() == 1.0  # di default spento


def test_measure_and_stress():
    data = _data({"A": 0.0001, "C": 0.30}, days=900)
    w = pd.Series({"A": 0.4, "C": 0.4})
    cfg = {"stress_scenarios": {"Crollo": [str(data.calendar[500].date()), str(data.calendar[520].date())],
                                "Prima dei dati": ["2001-01-01", "2001-06-01"]},
           "market_shock": -0.2, "sector_shock": -0.3}
    rm = RiskManager(cfg, SECTORS)
    m = rm.measure(w, data)
    assert m["exposure"] == pytest.approx(0.8) and m["effective_positions"] == pytest.approx(2.0)
    assert 0 < m["var_1d"] < m["cvar_1d"] and m["volatility"] > 0
    assert m["beta"] == pytest.approx(0.8, abs=0.15)          # entrambi i titoli seguono il mercato
    assert m["sector_weights"].to_dict() == pytest.approx({"Tech": 0.4, "Energy": 0.4})
    st = rm.stress_test(w, data).set_index("scenario")
    assert "Prima dei dati" not in st.index                    # il benchmark non copre il periodo
    crash = st.loc["Crollo"]
    bench = data.close["SPY"]
    assert crash["benchmark"] == pytest.approx(bench.iloc[520] / bench.iloc[500] - 1)
    a = data.close["A"]
    assert crash["coverage"] == 1.0
    assert crash["portfolio"] == pytest.approx(0.4 * (a.iloc[520] / a.iloc[500] - 1)
                                               + 0.4 * (data.close["C"].iloc[520] / data.close["C"].iloc[500] - 1))
    assert st.loc["Mercato -20% (beta)", "portfolio"] == pytest.approx(m["beta"] * -0.2)


def test_backtest_records_risk_limits(cfg, demo_data, universe):
    from modules.backtester import Backtester
    from modules.strategy import Strategy

    cfg["backtest"]["years"] = 2
    cfg["risk"].update(target_volatility=0.05, max_sector_weight=0.25)
    strategy = Strategy(cfg, tickers=universe.tickers(), sectors=universe.sectors())
    bt = Backtester(cfg, strategy=strategy).run(demo_data)
    assert not bt.risk.empty and {"vol_scale", "capped_sectors", "drawdown"} <= set(bt.risk.columns)
    invested = bt.risk[bt.risk["exposure_before"] > 0]
    assert (invested["vol_scale"] < 1).any()                  # 5% è sotto la volatilità di qualunque azione
    assert (invested["exposure"] <= invested["exposure_before"] + 1e-9).all()
    assert bt.metrics["risk_vol_scaled_share"] > 0
