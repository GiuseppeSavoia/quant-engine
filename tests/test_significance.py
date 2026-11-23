import numpy as np
import pandas as pd

from modules import significance as sg


def _curve(mu, sd, n=1500, seed=0):
    rng = np.random.default_rng(seed)
    r = rng.normal(mu, sd, n)
    return pd.Series(100 * np.cumprod(1 + r), index=pd.bdate_range("2012-01-02", periods=n))


def test_expected_max_cresce_con_i_tentativi():
    assert sg.expected_max_sharpe(1, 1e-4) == 0.0
    assert sg.expected_max_sharpe(100, 1e-4) > sg.expected_max_sharpe(10, 1e-4) > 0


def test_dsr_edge_vero_alto_e_rumore_basso():
    forte = sg.deflated_sharpe(_curve(0.002, 0.01), 1, 0.0)
    assert forte["dsr"] > 0.95
    rumore = sg.deflated_sharpe(_curve(0.0, 0.01), 200, 1e-4)
    assert rumore["dsr"] < 0.5


def test_piu_tentativi_abbassano_il_dsr():
    eq = _curve(0.0006, 0.01)
    a = sg.deflated_sharpe(eq, 2, 1e-5)["dsr"]
    b = sg.deflated_sharpe(eq, 500, 1e-5)["dsr"]
    assert b < a


def test_dati_insufficienti_e_curva_piatta():
    corta = pd.Series([100.0, 101, 102], index=pd.bdate_range("2020-01-01", periods=3))
    assert sg.deflated_sharpe(corta, 5, 1e-4)["dsr"] is None
    assert sg.daily_sharpe(pd.Series([100.0] * 10)) == 0.0
    assert "insufficienti" in sg.verdict_text({"dsr": None})


def test_assess_usa_il_registro():
    exps = pd.DataFrame({"config_hash": ["a", "b", "b", "c"], "period": ["all"] * 4,
                         "equity": [_curve(0.0005, 0.01, seed=s) for s in range(4)]})
    out = sg.assess(_curve(0.0005, 0.01), exps)
    assert out["n_trials"] == 3 and 0 <= out["dsr"] <= 1
    assert "3 configurazioni" in sg.verdict_text(out)
    assert sg.assess(_curve(0.0005, 0.01), pd.DataFrame())["n_trials"] == 1
