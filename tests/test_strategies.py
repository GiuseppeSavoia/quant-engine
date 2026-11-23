"""Test delle strategie a regole: definizioni, modalità rules del motore, backtest e risultati salvati."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import strategies
from modules.strategy import Strategy


def test_catalog_definitions_are_valid():
    for name, entry in strategies.CATALOG.items():
        if entry["kind"] == "rules":
            d = entry["definition"]
            strategies.validate_definition(d)
            assert d["name"] == name and d["description"]
            assert len(strategies.describe(d)) == (5 if d.get("start") else 4)
    with pytest.raises(ValueError):
        strategies.validate_definition({**strategies.default_definition(),
                                        "ranking": [{"metric": "ret_1m", "weight": 1}]})  # nessun verso migliore
    bad = strategies.default_definition()
    bad["portfolio"]["positions"] = 0
    with pytest.raises(ValueError):
        strategies.validate_definition(bad)


def test_rank_score_orientation_and_missing():
    m = pd.DataFrame({"pe": [10.0, 20.0, 30.0], "roic": [0.1, np.nan, 0.3]}, index=["A", "B", "C"])
    s = strategies.rank_score(m, [{"metric": "pe", "weight": 1.0}, {"metric": "roic", "weight": 1.0}])
    assert s["A"] == pytest.approx((1.0 + 0.5) / 2)       # P/E migliore, ROIC peggiore
    assert s["B"] == pytest.approx(2 / 3)                 # senza ROIC conta solo il P/E
    assert s["C"] == pytest.approx((1 / 3 + 1.0) / 2)
    assert strategies.rank_score(m, []).isna().all()


def test_to_config(cfg):
    d = strategies.CATALOG["Magic Formula"]["definition"]
    c = strategies.to_config(cfg, d)
    assert c["strategy"]["mode"] == "rules" and c["strategy"]["rules"]["sectors"] == strategies.NON_FINANCIAL
    assert c["portfolio"]["max_positions"] == 30 and c["backtest"]["use_exits"] is False
    assert c["risk"]["target_volatility"] == 0 and cfg["strategy"].get("mode") != "rules"  # base intatta
    mine = {**strategies.default_definition(), "universe": {"sectors": [], "watchlist_only": True}}
    assert strategies.to_config(cfg, mine, ["MSFT", "AAPL"])["strategy"]["rules"]["tickers"] == ["AAPL", "MSFT"]


def test_rules_mode_selects_by_filters_and_ranking(cfg, demo_data, universe):
    d = strategies.default_definition()
    d["filters"] = [{"metric": "pe", "op": "<=", "value": 40}]
    d["ranking"] = [{"metric": "fcf_yield", "weight": 1.0}]
    d["portfolio"].update(positions=5, max_weight=0.3, max_sector=1.0)
    c = strategies.to_config(cfg, d)
    res = Strategy(c, tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
    dec = pd.DataFrame(res.decisions).set_index("ticker")
    buys = dec[dec["action"] == "BUY"]
    assert len(buys) == 5 and set(res.weights.index) == set(buys.index)
    from modules.metrics import compute_metrics
    m = compute_metrics(demo_data, list(buys.index))
    assert (m["pe"] <= 40).all()                          # tutti rispettano il filtro
    left_out = dec[dec["reasoning"].str.startswith("passa i filtri ma", na=False)]
    assert len(left_out) > 0 and buys["confidence"].min() >= left_out["confidence"].max()   # i primi 5 per punteggio
    # universo: un solo settore / solo una lista di titoli
    sector = universe.sectors()["AAPL"]
    d2 = {**d, "universe": {"sectors": [sector], "watchlist_only": False}}
    res2 = Strategy(strategies.to_config(cfg, d2), tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
    assert {universe.sectors()[t] for t in res2.weights.index} <= {sector}
    d3 = {**d, "filters": [], "universe": {"sectors": [], "watchlist_only": True}}
    res3 = Strategy(strategies.to_config(cfg, d3, ["AAPL", "MSFT"]), tickers=universe.tickers(),
                    sectors=universe.sectors()).run(demo_data)
    assert set(res3.weights.index) <= {"AAPL", "MSFT"}


def test_rules_mode_price_only_and_no_ranking(cfg, demo_data, universe):
    d = strategies.CATALOG["Momentum"]["definition"]
    res = Strategy(strategies.to_config(cfg, d), tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
    assert len(res.weights) > 0 and res.weights.sum() == pytest.approx(1.0, abs=1e-6)
    base = strategies.CATALOG[strategies.BASELINE]["definition"]
    res = Strategy(strategies.to_config(cfg, base), tickers=universe.tickers(), sectors=universe.sectors()).run(demo_data)
    w = res.weights
    assert w.sum() == pytest.approx(1.0, abs=1e-6) and w.max() == pytest.approx(w.min())   # pesi uguali, tutto investito


def test_user_strategies_in_db(tmp_path):
    db = DBManager(tmp_path / "t.db")
    d = {**strategies.default_definition(), "name": "Mia"}
    db.save_strategy(d)
    db.save_strategy({**d, "description": "aggiornata"})
    assert db.load_strategies()["Mia"]["description"] == "aggiornata"
    db.delete_strategy("Mia")
    assert db.load_strategies() == {}


def test_strategy_results_are_saved_and_reused(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    cfg["backtest"]["start"] = str((pd.Timestamp("2026-10-30") - pd.DateOffset(years=2)).date())
    d = DashboardData(cfg, demo_db, tmp_path)
    assert d.strategy_result("Momentum") is None                  # non ancora fatto
    first = d.strategy_result("Momentum", run=True)
    again = d.strategy_result("Momentum")
    assert again["id"] == first["id"] and not first["equity"].empty
    base = d.strategy_result(strategies.BASELINE, run=True)
    table = d.period_table(first, base)
    assert set(table["what"]) == {"Strategia", "Indice a pesi uguali", "SPY"} and len(table) == 9
    full = table[table["period"].str.startswith("Tutto") & (table["what"] == "Strategia")].iloc[0]
    assert full["total_return"] == pytest.approx(first["metrics"]["total_return"], rel=1e-9)
    hold = d.strategy_holdings("Momentum")
    assert not hold.empty and hold["weight"].sum() == pytest.approx(1.0, abs=1e-6)
    # una strategia dell'utente: definizione diversa = backtest diverso
    mine = {**strategies.default_definition(), "name": "Mia", "ranking": [{"metric": "roic", "weight": 1.0}]}
    demo_db.save_strategy(mine)
    assert "Mia" in d.user_strategies() and d.strategy_result("Mia") is None
    assert d.strategy_config("Mia")[1]["definition"]["ranking"][0]["metric"] == "roic"
    assert demo_db.count_variants() >= 2
