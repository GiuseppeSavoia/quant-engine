"""Test del laboratorio della strategia."""
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import lab
from modules.config import default_config


def test_overrides_do_not_touch_the_base():
    base = default_config()
    cfg = lab.apply_overrides(base, {"backtest.use_exits": False, "valuation.skip_sectors": ["Real Estate"]})
    assert cfg["backtest"]["use_exits"] is False and cfg["valuation"]["skip_sectors"] == ["Real Estate"]
    assert base["backtest"]["use_exits"] is True and base["valuation"]["skip_sectors"] == ["Financials"]
    with pytest.raises(KeyError):
        lab.apply_overrides(base, {"data.db_path": "x"})              # solo i parametri previsti
    with pytest.raises(ValueError):
        lab.apply_overrides(base, {"confidence.weights.regime": 0.0, "confidence.weights.value": 0.0,
                                   "confidence.weights.multi_factor": 0.0})


def test_changes_and_description():
    base = default_config()
    values = {k: lab.get_value(base, p.path) for k, p in lab.PARAM_BY_KEY.items()}
    assert lab.changes(base, values) == {}
    values.update({"portfolio.max_positions": 10, "valuation.skip_sectors": ["Financials"]})
    ch = lab.changes(base, values)
    assert ch == {"portfolio.max_positions": 10}
    assert lab.describe(base, ch) == "Titoli in portafoglio: 25 → 10"
    assert "max_positions: 10" in lab.overrides_yaml(ch)
    for preset in lab.PRESETS.values():                               # i preset sono applicabili
        lab.apply_overrides(base, preset)


def test_experiment_runs_and_is_saved(cfg, demo_data, universe, tmp_path):
    cfg["backtest"]["years"] = 1
    fn = lambda day: universe.tickers(as_of=day)  # noqa: E731
    base = lab.run_experiment(cfg, {}, demo_data, universe.tickers(), universe.sectors(), fn)
    exp = lab.run_experiment(cfg, {"backtest.use_exits": False}, demo_data, universe.tickers(),
                             universe.sectors(), fn, name="senza uscite")
    assert base.name == "configurazione attuale" and exp.name == "senza uscite"
    assert exp.config["backtest"]["use_exits"] is False and cfg["backtest"]["use_exits"] is True
    assert exp.metrics["n_trades"] < base.metrics["n_trades"]         # niente stop-loss né take-profit
    assert exp.data_as_of == str(demo_data.last_date.date())

    db = DBManager(tmp_path / "lab.db")
    ids = [db.log_experiment(base), db.log_experiment(exp)]
    saved = db.load_experiments()
    assert list(saved["id"]) == ids and saved.iloc[1]["overrides"] == {"backtest.use_exits": False}
    pd.testing.assert_series_equal(saved.iloc[1]["equity"].round(6), exp.equity.round(6),
                                   check_names=False, check_freq=False)
    db.delete_experiment(ids[0])
    assert list(db.load_experiments()["id"]) == ids[1:]


def test_periods_split_the_backtest(cfg, demo_data, universe, tmp_path):
    cfg["backtest"]["start"] = str(demo_data.calendar[300].date())
    start, end = lab.period_bounds(cfg, demo_data.calendar, "all")
    s1, e1 = lab.period_bounds(cfg, demo_data.calendar, "first")
    s2, e2 = lab.period_bounds(cfg, demo_data.calendar, "second")
    assert s1 == start and e2 == end and e1 == s2 and start < e1 < end
    exp = lab.run_experiment(cfg, {}, demo_data, universe.tickers(), universe.sectors(), period="second")
    assert exp.period == "second" and exp.equity.index[0] >= s2
    db = DBManager(tmp_path / "lab.db")
    db.log_experiment(exp)
    assert db.load_experiments().iloc[0]["period"] == "second"


def test_equal_weight_index_preset_buys_everything(cfg, demo_data, universe):
    """Centinaia di posizioni piccole (sotto la soglia minima di scambio) devono comunque essere aperte."""
    cfg["backtest"].update(years=1, min_trade_pct=0.2)        # soglia più alta del peso di ogni titolo
    preset = {**lab.PRESETS["Tutto l'indice a pesi uguali"], "portfolio.max_weight": 0.05}  # demo: solo ~30 titoli
    exp = lab.run_experiment(cfg, preset, demo_data, universe.tickers(),
                             universe.sectors(), lambda day: universe.tickers(as_of=day))
    assert exp.metrics["avg_exposure"] > 0.9 and exp.metrics["n_trades"] > len(universe.tickers())
