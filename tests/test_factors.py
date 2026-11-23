"""Test dei fattori classici e della ricerca sui fattori."""
import numpy as np
import pandas as pd
import pytest

from modules.factors import FACTOR_LABELS, composite_score, compute_factors
from modules.market_data import MarketData
from modules.research import FactorResearch, ic_weights


def _toy_market():
    """Tre titoli con caratteristiche note: A calmo e redditizio, B volatile e in perdita, C nel mezzo."""
    rng = np.random.default_rng(0)
    days = pd.bdate_range("2024-01-01", periods=300)
    vols = {"A": 0.005, "B": 0.03, "C": 0.015, "SPY": 0.01}
    drift = {"A": 0.0005, "B": -0.001, "C": 0.0, "SPY": 0.0003}
    rows = []
    for t, v in vols.items():
        px = 100 * np.exp(np.cumsum(rng.normal(drift[t], v, len(days))))
        rows += [(t, d, p, p, p, p, 1e6) for d, p in zip(days, px)]
    prices = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])
    fin = []
    for t, (ni, assets, eq, gp, rev, debt, ocf) in {
        "A": (20e9, 100e9, 50e9, 40e9, 80e9, 10e9, 25e9),
        "B": (-5e9, 100e9, 20e9, 10e9, 50e9, 60e9, -8e9),   # cassa peggiore degli utili
        "C": (5e9, 100e9, 40e9, 20e9, 60e9, 30e9, 6e9)}.items():
        for year, scale in ((2023, 0.9), (2024, 1.0)):
            for m, v in (("net_income", ni), ("total_assets", assets * scale), ("equity", eq), ("gross_profit", gp),
                         ("revenue", rev * scale), ("total_debt", debt), ("operating_cash_flow", ocf),
                         ("free_cash_flow", ocf * 0.8), ("shares", 1e9)):
                fin.append((t, f"{year}-12-31", m, v if m not in ("total_assets", "revenue") else v))
    fin = pd.DataFrame(fin, columns=["ticker", "period_end", "metric", "value"])
    return MarketData.from_long(prices, fin, benchmark="SPY")


def test_factor_orientation_higher_is_better():
    f = compute_factors(_toy_market(), ["A", "B", "C"])
    assert list(f.columns) == list(FACTOR_LABELS)
    assert f.loc["A", "low_vol"] > f.loc["C", "low_vol"] > f.loc["B", "low_vol"]      # meno volatile = più alto
    assert f.loc["A", "roa"] == pytest.approx(0.2) and f.loc["B", "roa"] < 0
    assert f.loc["A", "gross_profitability"] == pytest.approx(0.4)
    assert f.loc["A", "low_leverage"] > f.loc["B", "low_leverage"]                   # meno debito = più alto
    assert f.loc["A", "low_accruals"] > 0 and f.loc["B", "low_accruals"] < 0         # utili coperti da cassa
    assert f.loc["A", "revenue_growth"] == pytest.approx(1 / 0.9 - 1)
    assert f.loc["A", "low_asset_growth"] == pytest.approx(-(1 / 0.9 - 1))
    assert f.loc["A", "low_beta"] > f.loc["B", "low_beta"]


def test_composite_score_renormalizes_missing():
    f = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [3.0, np.nan, 1.0]}, index=["a", "b", "c"])
    s = composite_score(f, {"x": 0.5, "y": 0.5, "z": 1.0})                         # z non esiste: ignorato
    assert s["b"] == pytest.approx(f["x"].rank(pct=True)["b"])                     # solo x disponibile
    assert s["a"] == pytest.approx((1 / 3 + 1) / 2)
    assert composite_score(f, {}).isna().all()


def test_ic_weights_only_positive():
    summary = pd.DataFrame({"signal": ["Momentum 12-1", "ROA", "Bassa volatilità"], "horizon": [21, 21, 21],
                            "n_dates": [10, 10, 10], "ic_mean": [0.03, 0.01, -0.02], "ic_t": [2.0, 0.5, -1.0]})
    assert ic_weights(summary, 21) == pytest.approx({"mom_12_1": 0.75, "roa": 0.25})
    assert ic_weights(summary, 21, min_t=1.0) == {"mom_12_1": 1.0}
    assert ic_weights(summary, 21, exclude=("mom_12_1",)) == {"roa": 1.0}


def test_factor_research_walk_forward_on_demo(cfg, demo_data, universe):
    cfg["research"].update(start=str(demo_data.calendar[300].date()), step_days=42, horizons=[21])
    res = FactorResearch(cfg, lambda d: universe.tickers(as_of=d), universe.sectors()).run(demo_data)
    assert set(res.summary["signal"]) == set(FACTOR_LABELS.values())
    assert res.summary.set_index("signal").loc["Bassa volatilità", "n_dates"] > 0
    # la combinazione è valutata separatamente sulle due metà, divise alla data dichiarata
    assert list(res.composite["period"].str.split(" ").str[0]) == ["Prima", "Seconda"]
    first = res.factors[res.factors["date"] < pd.Timestamp(res.params["split"])]
    assert first["date"].nunique() >= 2 and res.factors["composite"].notna().any() == bool(res.weights)


def test_factor_strategy_mode(cfg, demo_data, universe):
    from modules.strategy import Strategy

    cfg["strategy"] = {"mode": "factors", "factor_weights": {"mom_12_1": 0.5, "low_vol": 0.5}}
    cfg["portfolio"].update(method="equal", max_positions=10, max_weight=0.2)
    res = Strategy(cfg, tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
    dec = res.decisions_frame()
    buys = dec[dec["action"] == "BUY"]
    assert len(buys) == 10 and set(res.weights.index) == set(buys["ticker"])
    assert res.weights.round(6).nunique() == 1                                   # pesi uguali
    ranked = dec.dropna(subset=["confidence"]).sort_values("confidence", ascending=False)
    assert set(ranked["ticker"].head(10)) == set(buys["ticker"])                 # i primi 10 per punteggio
    assert (buys["confidence"] >= buys["threshold"]).all()
    cfg["strategy"]["factor_weights"] = {}
    with pytest.raises(ValueError, match="factor_weights"):
        Strategy(cfg, tickers=universe.tickers())


def test_lab_factor_preset():
    from modules import lab
    from modules.config import default_config

    preset = lab.factor_preset({"mom_12_1": 0.7, "roa": 0.3})
    cfg = lab.apply_overrides(default_config(), preset)
    assert cfg["strategy"]["mode"] == "factors" and cfg["strategy"]["factor_weights"]["roa"] == 0.3
    assert cfg["strategy"]["factor_weights"]["low_vol"] == 0.0
    with pytest.raises(ValueError):
        lab.apply_overrides(default_config(), {"strategy.mode": "factors"})      # senza pesi
