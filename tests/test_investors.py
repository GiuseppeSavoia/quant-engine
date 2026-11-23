"""Test delle strategie dei grandi investitori e delle metriche che usano (storia, trimestri, valore composito)."""
import numpy as np
import pandas as pd
import pytest

from modules import strategies
from modules.metrics import METRIC_BY_KEY, compute_metrics, history_metrics, quarterly_growth, value_composite
from modules.strategy import Strategy
from modules.superinvestors import ThirteenFIngestor


def test_investor_catalog_is_complete():
    investors = {n: e for n, e in strategies.CATALOG.items() if e.get("group") == "Grandi investitori"}
    assert len(investors) == 20 and {"Magic Formula", "Piotroski"} <= set(investors)
    for name, e in investors.items():
        sheet = e["investor"]
        assert all(sheet[k] for k in ("who", "source", "original", "translation")), name
        d = e["definition"]
        strategies.validate_definition(d)
        assert d["name"] == name
        assert all(METRIC_BY_KEY[m].better for m in [r["metric"] for r in d["ranking"]])
    pabrai = investors["Pabrai – clonare i grandi investitori"]["definition"]
    assert strategies.to_config({"strategy": {}, "portfolio": {}, "risk": {}, "backtest": {}},
                                pabrai)["backtest"]["start"] == pabrai["start"]


def _annual(years, **cols):
    idx = pd.to_datetime([f"{y}-12-31" for y in years])
    return pd.DataFrame(cols, index=idx)


def test_history_metrics_by_hand():
    years = range(2016, 2026)                                     # 10 esercizi
    a = _annual(years, net_income=[-5, 10, 12, 14, 16, 18, 20, 22, 24, 26],
                shares=[10.0] * 10, revenue=[100, 110, 120, 130, 140, 150, 160, 170, 180, 200],
                equity=[100.0] * 10, operating_income=[20, 22, 24, 26, 28, 30, 30, 30, 30, 40],
                dividends_paid=[0, 0, 0, 1, 1, 1, 1, 1, 1, 1], tax_provision=[2.0] * 10, pretax_income=[10.0] * 10,
                total_debt=[50.0] * 10, cash=[50.0] * 10)
    prices = pd.Series([1.0, 20.0, 30.0], index=pd.to_datetime(["2015-01-01", "2020-06-30", "2023-06-30"]))
    h = history_metrics(a, prices)
    assert h["profitable_years"] == 9 and h["dividend_years"] == 7
    assert h["eps_cagr_5y"] == pytest.approx((2.6 / 1.6) ** (1 / 5) - 1, rel=1e-3)   # 2020 -> 2025
    assert h["revenue_cagr_5y"] == pytest.approx((200 / 140) ** (1 / 5) - 1, rel=1e-3)
    assert h["roe_min_5y"] == pytest.approx(0.18)                                  # 2021-2025: il minimo è 18/100
    assert h["roic_avg_5y"] == pytest.approx(np.mean([30, 30, 30, 30, 40]) * 0.8 / 100)
    assert h["margin_trend"] == pytest.approx(40 / 200 - np.mean([30 / 150, 30 / 160, 30 / 170, 30 / 180][1:]))
    # P/E alle chiusure 2021-2025 con l'ultimo prezzo noto a ogni chiusura: 20 (2021-2022), poi 30
    pe = [20 * 10 / 18, 20 * 10 / 20, 30 * 10 / 22, 30 * 10 / 24, 30 * 10 / 26]
    assert h["_pe_median"] == pytest.approx(np.median(pe))
    short = history_metrics(a.tail(2), prices)                                    # storia troppo corta
    assert np.isnan(short["eps_cagr_5y"]) and np.isnan(short["roe_min_5y"]) and short["profitable_years"] == 2


def test_quarterly_growth_and_acceleration():
    q = pd.Series([10, 10, 10, 10, 12, 15], index=pd.to_datetime(
        ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"]), dtype=float)
    g, acc = quarterly_growth(q, pd.Timestamp("2025-08-15"))
    assert g == pytest.approx(0.5) and acc == pytest.approx(0.5 - 0.2)
    assert np.isnan(quarterly_growth(q, pd.Timestamp("2026-03-01"))[0])          # 10-Q successivo mancante
    loss = q.copy()
    loss.iloc[1] = -3                                                            # un anno prima in perdita
    assert np.isnan(quarterly_growth(loss, pd.Timestamp("2025-08-15"))[0])


def test_value_composite_prefers_cheap():
    m = pd.DataFrame({"pe": [10, 20, 30], "pb": [1, 2, 3], "ps": [1, 2, np.nan], "ev_ebitda": [5, 8, 12],
                      "p_fcf": [np.nan, np.nan, np.nan], "shareholder_yield": [0.06, 0.03, 0.01]},
                     index=["A", "B", "C"])
    v = value_composite(m)
    assert v["A"] == pytest.approx(1.0) and v["A"] > v["B"] > v["C"]
    assert np.isnan(value_composite(m[["pe", "pb"]])["A"])                       # meno di 3 misure


def test_investor_strategies_run_on_demo(cfg, demo_data, universe):
    m = compute_metrics(demo_data, universe.tickers())
    for key in ("eps_cagr_5y", "roe_min_5y", "profitable_years", "ps", "value_composite", "from_52w_low"):
        assert m[key].notna().any(), key
    for name, e in strategies.CATALOG.items():
        if e.get("group") != "Grandi investitori":
            continue
        c = strategies.to_config(cfg, e["definition"])
        res = Strategy(c, tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
        assert res.weights.sum() <= 1 + 1e-9 and (res.weights >= 0).all(), name
        assert len(res.weights) <= e["definition"]["portfolio"]["positions"], name


class FakeSubmissions:
    def get_json(self, url):
        if url.endswith("submissions-001.json"):
            return {"form": ["13F-HR", "13F-HR"], "accessionNumber": ["a3", "a4"], "filingDate": ["2014", "2013"],
                    "reportDate": ["r3", "r4"]}
        return {"filings": {"recent": {"form": ["13F-HR", "4", "13F-HR"], "accessionNumber": ["a1", "x", "a2"],
                                       "filingDate": ["2016", "2015", "2015"], "reportDate": ["r1", "", "r2"]},
                            "files": [{"name": "CIK0000000001-submissions-001.json"}]}}


def test_thirteenf_reads_older_submission_pages():
    ing = ThirteenFIngestor(None, FakeSubmissions(), {})
    assert [a for a, _, _ in ing._filings(1, 3)] == ["a1", "a2", "a3"]
    assert [a for a, _, _ in ing._filings(1, 2)] == ["a1", "a2"]                 # bastano i recenti: niente altro
