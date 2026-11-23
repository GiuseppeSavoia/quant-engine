"""Test delle formule: valori attesi calcolati a mano."""
import numpy as np
import pandas as pd
import pytest

from modules.config import default_config
from modules.confidence_engine import ConfidenceEngine
from modules.execution_engine import ExecutionEngine
from modules.fundamental import FundamentalAnalyzer
from modules.multi_factor import MultiFactorModel
from modules.portfolio_optimizer import PortfolioOptimizer, cap_weights
from modules.regime_detection import GaussianHMM, RegimeDetector
from safety.circuit_breaker import CircuitBreaker


@pytest.fixture
def fa():
    return FundamentalAnalyzer(default_config()["valuation"])


# ---------------------------------------------------------------- fondamentali
def test_wacc_formula(fa):
    # E=800, D=200, Re=10%, Rd=5%, T=25% -> 0.8*0.10 + 0.2*0.05*0.75 = 0.0875
    assert fa.wacc(800, 200, 0.10, 0.05, 0.25) == pytest.approx(0.0875)


def test_wacc_floor(fa):
    assert fa.wacc(100, 0, 0.03, 0.02, 0.2) == pytest.approx(0.06)


def test_dcf_equals_gordon_when_growth_is_terminal(fa):
    # con crescita costante = terminale il DCF coincide con il modello di Gordon
    fcf, g, wacc, net_debt, shares = 100.0, 0.025, 0.09, 200.0, 10.0
    gordon = fcf * (1 + g) / (wacc - g)
    expected = (gordon - net_debt) / shares
    assert fa.dcf_per_share(fcf, g, wacc, net_debt, shares) == pytest.approx(expected, rel=1e-9)


def test_dcf_increases_with_growth(fa):
    vals = [fa.dcf_per_share(100, g, 0.09, 0, 10) for g in (0.0, 0.05, 0.10)]
    assert vals[0] < vals[1] < vals[2]


def test_dcf_rejects_wacc_below_terminal(fa):
    with pytest.raises(ValueError):
        fa.dcf_per_share(100, 0.05, 0.02, 0, 10)


def test_reverse_dcf_roundtrip(fa):
    price = fa.dcf_per_share(100, 0.08, 0.09, 50, 10)
    assert fa.implied_growth(price, 100, 0.09, 50, 10) == pytest.approx(0.08, abs=1e-5)


def test_reverse_dcf_out_of_range(fa):
    assert fa.implied_growth(1e9, 100, 0.09, 0, 10) is None


def test_beta_estimate():
    rng = np.random.default_rng(0)
    m = rng.normal(0.0004, 0.01, 600)
    s = 1.5 * m + rng.normal(0, 0.004, 600)
    idx = pd.bdate_range("2022-01-03", periods=601)
    mp = pd.Series(100 * np.exp(np.r_[0, np.cumsum(m)]), index=idx)
    sp = pd.Series(50 * np.exp(np.r_[0, np.cumsum(s)]), index=idx)
    assert FundamentalAnalyzer.estimate_beta(sp, mp) == pytest.approx(1.5, abs=0.1)


def test_growth_estimate_clipped(fa):
    idx = pd.to_datetime(["2021-12-31", "2022-12-31", "2023-12-31"])
    g, _ = fa.estimate_growth(pd.Series([100, 200, 400], index=idx))  # CAGR 100%
    assert g == pytest.approx(fa.cfg["max_growth"])
    g2, note = fa.estimate_growth(pd.Series([100], index=idx[:1]))
    assert g2 == fa.cfg["default_growth"] and "default" in note


# ---------------------------------------------------------------- punteggi
def test_confidence_scale_and_thresholds():
    ce = ConfidenceEngine(default_config()["confidence"])
    best = ce.evaluate("Bull", 0.5, 1.0)
    assert best.score == pytest.approx(1.0) and best.is_valid
    worst = ce.evaluate("Bear", None, 0.0)
    assert worst.score == 0.0 and not worst.is_valid
    # stesso punteggio, soglia diversa a seconda del regime
    assert ce.evaluate("Bull", 0.15, 0.5).threshold < ce.evaluate("Bear", 0.15, 0.5).threshold


def test_multifactor_scores():
    assert MultiFactorModel.momentum_score(0.15) == pytest.approx(0.5)
    assert MultiFactorModel.momentum_score(-0.2) == 0.0
    assert MultiFactorModel.volatility_score(0.15) == pytest.approx(1.0)
    assert MultiFactorModel.volatility_score(0.40) == pytest.approx(0.0)
    assert MultiFactorModel.quality_score(0.5, 0.15) == pytest.approx(1.0)
    assert MultiFactorModel.quality_score(-1.0, 0.15) == pytest.approx(0.5)  # patrimonio negativo
    assert MultiFactorModel.quality_score(np.nan, np.nan) is None


def test_multifactor_weights_normalized():
    mf = MultiFactorModel({"momentum": 2, "quality": 1, "volatility": 1})
    assert sum(mf.weights.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------- regime
def test_hmm_recovers_regimes():
    rng = np.random.default_rng(1)
    calm = rng.normal(0.001, 0.006, 400)
    crash = rng.normal(-0.003, 0.03, 150)
    calm2 = rng.normal(0.001, 0.006, 300)
    prices = pd.Series(100 * np.exp(np.cumsum(np.r_[calm, crash, calm2])),
                       index=pd.bdate_range("2020-01-01", periods=850))
    det = RegimeDetector({"n_states": 2, "lookback_days": 1000, "vol_window": 20, "min_observations": 250})
    assert det.detect(prices).label == "Bull"
    assert det.detect(prices.iloc[:530]).label == "Bear"


def test_hmm_is_deterministic():
    X = np.random.default_rng(3).normal(size=(300, 2))
    a = GaussianHMM(3).fit(X).predict(X)
    b = GaussianHMM(3).fit(X).predict(X)
    assert (a == b).all()


def test_regime_fallback_on_short_history():
    det = RegimeDetector(default_config()["regime"])
    res = det.detect(pd.Series(np.linspace(100, 110, 100), index=pd.bdate_range("2024-01-01", periods=100)))
    assert res.method == "rule_based"


# ---------------------------------------------------------------- sicurezza
def test_circuit_breaker():
    cb = CircuitBreaker()
    rng = np.random.default_rng(2)
    normal = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 80)))
    assert cb.check_safety(normal)[0]
    crash = normal.copy()
    crash[-1] = crash[-2] * 0.85  # -15% in un giorno
    assert not cb.check_safety(crash)[0]
    assert cb.check_safety(normal.reshape(-1, 1))[0]  # formato 2D (yfinance recente)
    assert not cb.check_safety(normal[:10])[0]  # storico corto: si blocca


# ---------------------------------------------------------------- portafoglio
def test_cap_weights():
    w = cap_weights(pd.Series({"A": 0.7, "B": 0.2, "C": 0.1}), 0.4)
    assert w.max() <= 0.4 + 1e-12 and w.sum() == pytest.approx(1.0)
    w2 = cap_weights(pd.Series({"A": 1.0, "B": 1.0}), 0.2, total=0.4)
    assert w2.sum() == pytest.approx(0.4)


@pytest.mark.parametrize("method", ["risk_parity", "min_variance"])
def test_optimizer_constraints(method):
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2023-01-02", periods=300)
    close = pd.DataFrame({t: 100 * np.exp(np.cumsum(rng.normal(0, s, 300)))
                          for t, s in [("LOW", 0.005), ("MID", 0.01), ("HIGH", 0.03), ("X", 0.012)]}, index=idx)
    w = PortfolioOptimizer(method, max_weight=0.35).optimize(close)
    assert (w >= 0).all() and (w <= 0.35 + 1e-6).all()
    assert w.sum() == pytest.approx(1.0, abs=1e-4)
    if method == "risk_parity":
        assert w["LOW"] >= w["MID"] >= w["HIGH"]


def test_optimizer_single_asset_respects_cap():
    idx = pd.bdate_range("2023-01-02", periods=200)
    close = pd.DataFrame({"A": np.linspace(100, 120, 200)}, index=idx)
    assert PortfolioOptimizer("risk_parity", 0.2).optimize(close)["A"] == pytest.approx(0.2)


# ---------------------------------------------------------------- esecuzione
def test_execution_order():
    ee = ExecutionEngine(default_config()["execution"])
    o = ee.build_order("AAA", 0.10, 100.0, atr=2.0, annual_vol=0.2, avg_dollar_volume=5e8, capital=100_000)
    assert o.shares == 100 and o.order_type == "MARKET"
    assert o.stop_loss == pytest.approx(96.0) and o.take_profit == pytest.approx(106.0)
    assert ee.build_order("AAA", 0.10, 100.0, 2.0, 0.5, 5e8).order_type == "LIMIT"
    assert ee.build_order("AAA", 0.0, 100.0, 2.0, 0.2, 5e8) is None


def test_dcf_refuses_stale_free_cash_flow(fa):
    """Se il capex sparisce dagli ultimi bilanci (voce cambiata), il DCF non deve usare il FCF di anni prima."""
    from modules.market_data import MarketData

    days = pd.bdate_range("2023-01-02", "2026-06-30")
    prices = pd.DataFrame({"ticker": "VZ", "date": days, "open": 40.0, "high": 40.0, "low": 40.0, "close": 40.0,
                           "volume": 1e6})
    rows = []
    for y in range(2018, 2026):
        rows += [("VZ", f"{y}-12-31", "revenue", 130e9), ("VZ", f"{y}-12-31", "shares", 4.2e9)]
        if y <= 2020:
            rows.append(("VZ", f"{y}-12-31", "free_cash_flow", 20e9))
    fin = pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value"])
    val = fa.value("VZ", MarketData.from_long(prices, fin))
    assert val.fair_value is None and "non aggiornato" in val.notes[-1]


def _fin(rows):
    return pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])


def test_market_data_annual_ttm_and_combined_views():
    from modules.market_data import MarketData

    fin = _fin([
        ("X", "2022-12-31", "revenue", 100.0, "2023-02-15", "sec"),
        ("X", "2023-06-30", "revenue", 110.0, "2023-08-01", "sec_ttm"),
        ("X", "2023-12-31", "revenue", 120.0, "2024-02-15", "sec"),
        ("X", "2024-06-30", "revenue", 132.0, "2024-08-01", "sec_ttm"),
    ])
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", "2024-09-30"), "ticker": "X", "close": 10.0,
                       "high": 10.0, "low": 10.0, "volume": 1})
    data = MarketData.from_long(px, fin)
    assert list(data.statements("X")["revenue"]) == [100.0, 120.0]
    assert list(data.ttm_statements("X")["revenue"]) == [110.0, 132.0]
    full = data.fundamentals("X")
    assert list(full["revenue"]) == [100.0, 110.0, 120.0, 132.0]
    assert data.year_ago(full)["revenue"] == 110.0           # stesso trimestre dell'anno prima
    early = data.as_of("2024-07-31")                          # il 10-Q non era ancora depositato
    assert early.fundamentals("X").index[-1] == pd.Timestamp("2023-12-31")
    assert early.year_ago(early.fundamentals("X"))["revenue"] == 100.0


def test_fcf_history_appends_latest_ttm_only(fa):
    from modules.market_data import MarketData

    fin = _fin([
        ("X", "2022-12-31", "free_cash_flow", 10.0, "2023-02-15", "sec"),
        ("X", "2023-06-30", "free_cash_flow", 11.0, "2023-08-01", "sec_ttm"),
        ("X", "2023-12-31", "free_cash_flow", 12.0, "2024-02-15", "sec"),
        ("X", "2024-06-30", "free_cash_flow", 14.0, "2024-08-01", "sec_ttm"),
    ])
    px = pd.DataFrame({"date": pd.bdate_range("2023-01-02", "2024-09-30"), "ticker": "X", "close": 10.0,
                       "high": 10.0, "low": 10.0, "volume": 1})
    data = MarketData.from_long(px, fin)
    assert list(fa.fcf_history(data, "X")) == [10.0, 12.0, 14.0]   # il TTM di metà 2023 contava due volte
    assert list(fa.fcf_history(data.as_of("2024-03-01"), "X")) == [10.0, 12.0]
