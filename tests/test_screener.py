"""Test dei filtri di Esplora (riusati dalle strategie create dall'utente)."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules.metrics import METRIC_BY_KEY
from modules.screener import PRESETS, apply_rules, describe_rule, validate_rule

M = pd.DataFrame({
    "pe": [10.0, 20.0, np.nan, 40.0],
    "roic": [0.30, 0.10, 0.25, np.nan],
    "ret_1m": [0.1, -0.1, 0.0, 0.2],
}, index=["A", "B", "C", "D"])
SECTORS = {"A": "Tech", "B": "Tech", "C": "Energy", "D": "Energy"}


def test_value_rules_and_missing_data():
    assert list(M.index[apply_rules(M, [{"metric": "pe", "op": "<=", "value": 20}])]) == ["A", "B"]
    both = [{"metric": "pe", "op": "<=", "value": 30}, {"metric": "roic", "op": ">=", "value": 0.2}]
    assert list(M.index[apply_rules(M, both)]) == ["A"]          # C senza P/E e D senza ROIC non passano
    assert apply_rules(M, []).all()


def test_rank_rules_and_sectors():
    best_index = apply_rules(M, [{"metric": "roic", "op": "top_index", "value": 0.3}])
    assert list(M.index[best_index]) == ["A"]
    best_sector = apply_rules(M, [{"metric": "roic", "op": "top_sector", "value": 0.4}], SECTORS)
    assert list(M.index[best_sector]) == ["A", "C"]                # la migliore di ogni settore
    low_pe = apply_rules(M, [{"metric": "pe", "op": "top_index", "value": 0.3}])
    assert list(M.index[low_pe]) == ["A"]                          # P/E: più basso = migliore
    assert list(M.index[apply_rules(M, [], SECTORS, only_sectors=["Energy"])]) == ["C", "D"]


def test_invalid_rules():
    with pytest.raises(ValueError):
        validate_rule({"metric": "boh", "op": ">=", "value": 1})
    with pytest.raises(ValueError):
        validate_rule({"metric": "pe", "op": "~", "value": 1})
    with pytest.raises(ValueError):                                # rendimento a 1 mese: nessun verso migliore
        validate_rule({"metric": "ret_1m", "op": "top_index", "value": 0.2})
    with pytest.raises(ValueError):
        validate_rule({"metric": "pe", "op": "top_index", "value": 1.5})


def test_presets_are_valid_and_described():
    for name, preset in PRESETS.items():
        assert preset["description"] and preset["rules"], name
        for rule in preset["rules"]:
            validate_rule(rule)
            assert METRIC_BY_KEY[rule["metric"]].label in describe_rule(rule)
    assert describe_rule({"metric": "roic", "op": ">=", "value": 0.15}) == "ROIC >= 15.0%"
    assert describe_rule({"metric": "roic", "op": "top_sector", "value": 0.25}) == "ROIC: nel 25% migliore del settore"


def test_saved_screens(tmp_path):
    db = DBManager(tmp_path / "t.db")
    rules = [{"metric": "pe", "op": "<=", "value": 15.0}]
    db.save_screen("Economiche", rules, ["Energy"])
    db.save_screen("Economiche", rules + [{"metric": "roic", "op": ">=", "value": 0.1}])   # sovrascrive
    screens = db.load_screens()
    assert list(screens) == ["Economiche"] and len(screens["Economiche"]["rules"]) == 2
    assert screens["Economiche"]["sectors"] == []
    db.delete_screen("Economiche")
    assert db.load_screens() == {}


def test_presets_on_demo(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    table = d.explore_table()
    for preset in PRESETS.values():
        mask = apply_rules(table, preset["rules"], d.sectors())
        assert mask.dtype == bool and len(mask) == len(table)
