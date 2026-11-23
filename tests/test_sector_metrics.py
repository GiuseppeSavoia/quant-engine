"""Test delle metriche di settore (banche, assicurazioni, REIT) con i numeri di JPM, Travelers e Realty Income."""
import numpy as np
import pandas as pd
import pytest

from modules.metrics import sector_metrics
from modules.sec_edgar import _flow_metrics


def test_bank_revenue_and_ratios():
    # JPM 2025 (miliardi): RevenuesNetOfInterestExpense 182,4, "Revenues" di una voce minore 0,9
    jpm = pd.Series({"Revenues": 0.9, "RevenuesNetOfInterestExpense": 182.4, "InterestIncomeExpenseNet": 95.4,
                     "NoninterestIncome": 87.0, "NoninterestExpense": 95.6, "ProvisionForLoanLeaseAndOtherLosses": 14.2})
    f = _flow_metrics(jpm)
    assert f["revenue"] == pytest.approx(182.4) and f["net_interest_income"] == pytest.approx(95.4)
    no_total = jpm.drop(["RevenuesNetOfInterestExpense", "Revenues"])
    assert _flow_metrics(no_total)["revenue"] == pytest.approx(95.4 + 87.0)    # margine + altri ricavi
    cur = pd.Series(f)
    latest = pd.Series({"goodwill": 52.7, "intangibles": 3.0})
    m = sector_metrics(cur, latest, mcap=800.0, ni=57.0, rev=182.4, equity=360.0, assets=4400.0)
    tangible = 360.0 - 52.7 - 3.0
    assert m["ptbv"] == pytest.approx(800 / tangible) and m["rotce"] == pytest.approx(57 / tangible)
    assert m["efficiency_ratio"] == pytest.approx(95.6 / 182.4) and m["nim"] == pytest.approx(95.4 / 4400)
    assert np.isnan(m["ffo_yield"]) and np.isnan(m["loss_ratio"])


def test_insurer_and_reit():
    trv = pd.Series({"premiums": 43.9, "claims": 27.2})
    assert sector_metrics(trv, pd.Series(dtype=float), 50.0, 5.0, 46.0, 30.0, 130.0)["loss_ratio"] == pytest.approx(
        27.2 / 43.9)
    # Realty Income: immobili 62 su 70 di attivo, FFO = utile + ammortamenti - plusvalenze
    o = pd.Series({"depreciation": 2.5, "real_estate_gains": 0.1})
    m = sector_metrics(o, pd.Series({"real_estate_assets": 62.0}), mcap=55.0, ni=0.9, rev=5.7, equity=39.0, assets=70.0)
    assert m["ffo_yield"] == pytest.approx((0.9 + 2.5 - 0.1) / 55) and m["p_ffo"] == pytest.approx(55 / 3.3)
    # un'azienda industriale con pochi immobili: niente FFO, niente metriche bancarie
    ind = sector_metrics(o, pd.Series({"real_estate_assets": 5.0}), 55.0, 0.9, 5.7, 39.0, 70.0)
    assert np.isnan(ind["ffo_yield"]) and np.isnan(ind["efficiency_ratio"]) and np.isnan(ind["nim"])
