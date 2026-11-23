"""Test del "mio metodo": formule delle metriche personali, registro, checklist, profilo."""
import numpy as np
import pandas as pd
import pytest

from modules import method, metrics, strategies
from modules.formulas import FormulaError, evaluate, names_in, parse


@pytest.fixture(autouse=True)
def clean_registry():
    metrics.set_custom([])
    yield
    metrics.set_custom([])


FRAME = pd.DataFrame({"roic": [0.20, 0.05, np.nan], "net_debt_ebitda": [1.0, 4.0, 2.0], "fcf_yield": [0.05, 0.08, 0.0]},
                     index=["A", "B", "C"])


def test_formula_evaluation():
    out = evaluate("fcf_yield - 0.02 * net_debt_ebitda", FRAME, metrics.BASE_KEYS)
    assert list(out.round(4)) == [0.03, 0.0, -0.04]
    assert np.isnan(evaluate("roic - 0.08", FRAME)["C"])                         # dato mancante: vuoto
    assert list(evaluate("max(roic, 0.1)", FRAME).round(2)[:2]) == [0.2, 0.1]
    assert np.isnan(evaluate("fcf_yield / 0", FRAME)["A"])                        # infinito -> vuoto
    assert np.isnan(evaluate("log(fcf_yield)", FRAME)["C"])                       # log di 0: vuoto
    assert (evaluate("2 ** 3", FRAME) == 8).all()
    assert names_in("roic - 0.02 * net_debt_ebitda") == {"roic", "net_debt_ebitda"}


@pytest.mark.parametrize("bad", [
    "__import__('os').system('ls')", "roic.real", "roic[0]", "open('x')", "roic if roic else 1",
    "roic > 0.1", "lambda: 1", "'testo'", "", "roic +", "min(roic)", "unknown_metric * 2", "True + roic",
])
def test_formula_rejects_anything_else(bad):
    with pytest.raises(FormulaError):
        parse(bad, metrics.BASE_KEYS)


def test_custom_metric_registry_and_compute(demo_data, universe):
    definition = {"key": "my_spread", "label": "ROIC oltre l'8%", "formula": "roic - 0.08", "fmt": "pct",
                  "better": "high", "description": ""}
    metrics.set_custom([definition])
    assert "my_spread" in metrics.METRIC_BY_KEY and metrics.CUSTOM_GROUP in metrics.GROUPS
    m = metrics.compute_metrics(demo_data, universe.tickers())
    assert np.allclose(m["my_spread"].dropna(), (m["roic"] - 0.08).dropna())
    r = metrics.percentile_ranks(m)
    assert "my_spread" in r                                                       # usabile nelle classifiche
    metrics.set_custom([])
    assert "my_spread" not in metrics.METRIC_BY_KEY and "my_spread" not in metrics.compute_metrics(demo_data, ["AAPL"])
    with pytest.raises(ValueError):
        metrics.validate_custom({**definition, "formula": "roic.__class__"})
    with pytest.raises(ValueError):
        metrics.validate_custom({**definition, "key": "pe"})                    # non può coprire una del catalogo
    assert metrics.custom_key("Qualità a sconto!") == "my_qualita_a_sconto"


def test_strategies_embed_custom_formulas_and_reject_judgment(cfg):
    metrics.set_custom([{"key": "my_spread", "label": "Spread", "formula": "roic - 0.08", "fmt": "pct",
                         "better": "high"}])
    d = {**strategies.default_definition(), "ranking": [{"metric": "my_spread", "weight": 1.0}]}
    c = strategies.to_config(cfg, d)
    assert c["strategy"]["rules"]["custom_metrics"][0]["formula"] == "roic - 0.08"
    metrics.set_custom([{"key": "my_spread", "label": "Spread", "formula": "roic - 0.10", "fmt": "pct",
                         "better": "high"}])
    from modules.run_info import config_hash
    assert config_hash(strategies.to_config(cfg, d)) != config_hash(c)          # formula diversa = backtest diverso
    with pytest.raises(ValueError):
        strategies.validate_definition({**d, "filters": [{"metric": "my_checklist", "op": ">=", "value": 3}]})


def test_checklist_score():
    items = pd.DataFrame({"id": [1, 2, 3], "weight": [1.0, 2.0, 0.0]})
    assert method.checklist_score({1: 5, 2: 2}, items) == pytest.approx((5 + 2 * 2) / 3)
    assert method.checklist_score({1: 4}, items) == 4                            # solo le risposte date
    assert method.checklist_score({3: 5}, items) is None                         # peso zero: non conta
    assert method.checklist_score({}, items) is None


def test_method_in_dashboard(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    items = d.checklist_items()
    assert len(items) == len(method.DEFAULT_CHECKLIST)                           # domande di partenza
    first, second = int(items["id"].iloc[0]), int(items["id"].iloc[1])
    d.save_checklist("AAPL", {first: 5.0, second: 3.0})
    assert d.metrics().at["AAPL", "my_checklist"] == pytest.approx(4.0)
    assert np.isnan(d.metrics().at["MSFT", "my_checklist"])
    d.save_checklist("AAPL", {second: None})                                     # tolto un voto
    assert d.checklist("AAPL") == {first: 5.0}

    d.save_custom_metric({"key": "my_spread", "label": "Spread", "formula": "roic - 0.08", "fmt": "pct",
                          "better": "high", "description": ""})
    assert "my_spread" in d.explore_table() and "my_spread" in d.company_metrics("AAPL")["key"].tolist()
    assert d.preview_formula("roic * 2").notna().any()
    demo_db.save_strategy({**strategies.default_definition(), "name": "Usa spread",
                           "ranking": [{"metric": "my_spread", "weight": 1.0}]})
    assert d.delete_custom_metric("my_spread") == ["Usa spread"]               # ti dice chi la usava
    assert "my_spread" not in metrics.METRIC_BY_KEY

    d.save_profile({"styles": ["Qualità"], "positions": 15, "estraneo": 1})
    prof = d.profile()
    assert prof["styles"] == ["Qualità"] and prof["positions"] == 15 and "estraneo" not in prof
    assert prof["horizon"] == method.PROFILE_DEFAULTS["horizon"]
