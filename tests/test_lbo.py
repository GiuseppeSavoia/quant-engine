"""Test LBO e distanza dal default (modules/lbo.py), foglio Excel con formule e metriche del catalogo (nessuna rete)."""
import math

import numpy as np
import pytest

from modules import excel_model as em
from modules import lbo
from modules.xlsx import Sheet, col_letter
from tests.test_excel_model import evaluate

CASE = dict(ebitda=100.0, fcf=50.0, interest_expense=10.0, debt=200.0, cash=50.0, market_cap=800.0, shares=10.0,
            tax_rate=0.21, rate=0.08)


def test_fonti_impieghi_e_debito_anno_per_anno():
    r = lbo.lbo(**CASE)
    assert r.ok
    assert r.purchase_equity == pytest.approx(1040.0)          # 800 x 1,30
    assert r.uses == pytest.approx(1190.0)                     # + debito netto 150
    assert r.new_debt == pytest.approx(600.0)                  # 6 x EBITDA (copertura: 625, quota: 833)
    assert r.sponsor_equity == pytest.approx(590.0)
    assert r.ufcf == pytest.approx(50 + 10 * 0.79)
    d = 600.0
    for y in r.years:
        assert y["debito_inizio"] == pytest.approx(d)
        d = d - (r.ufcf - 0.08 * d * 0.79)
        assert y["debito_fine"] == pytest.approx(d)
    assert r.exit_multiple == pytest.approx(9.5)               # (800 + 150) / 100
    assert r.irr == pytest.approx((r.exit_equity / 590.0) ** (1 / 5) - 1)


def test_prezzo_massimo_da_il_rendimento_richiesto():
    r = lbo.lbo(**CASE)
    p = dict(lbo.DEFAULTS)
    at_max = lbo._run(r.ebitda, r.ufcf, r.max_price * 10 * 1.0, r.refinanced_debt, 950.0, 0.21, 0.08, p)
    assert at_max["irr"] == pytest.approx(0.20, abs=1e-6)
    assert r.max_price < r.price  # senza crescita, al 20% un fondo pagherebbe meno di oggi
    cheap = lbo.lbo(**{**CASE, "market_cap": 300.0})
    assert cheap.irr > r.irr and cheap.max_price > cheap.price


def test_vincoli_del_debito_e_casi_vuoti():
    tight = lbo.lbo(**{**CASE, "rate": 0.12})                  # copertura 2x: 100 / (2 x 12%) = 416,7
    assert tight.new_debt == pytest.approx(100 / 0.24)
    assert not lbo.lbo(**{**CASE, "ebitda": -5}).ok
    assert not lbo.lbo(**{**CASE, "fcf": np.nan}).ok
    assert not lbo.lbo(**CASE, financial=True).ok
    custom = lbo.lbo(**CASE, cfg={"lbo": {"premium": 0.0}})
    assert custom.purchase_equity == pytest.approx(800.0)


def test_distanza_dal_default():
    assert lbo.distance_to_default(800, 0, 0, 0.3, 0.1) == lbo.DEFAULTS["dd_cap"]   # nessun debito
    assert np.isnan(lbo.distance_to_default(800, np.nan, 0, 0.3, 0.1))              # debito ignoto: mai "sicuro"
    assert np.isnan(lbo.distance_to_default(800, 100, 0, 0.3, 0.1, financial=True))
    # E=100, F=400, sigma 60%, rendimento -40%: sigma_V = 0,2 x 0,6 + 0,8 x 0,2 = 0,28
    dd = lbo.distance_to_default(100, 400, 0, 0.6, -0.4)
    assert dd == pytest.approx((math.log(1.25) + math.log(0.6) - 0.5 * 0.28 ** 2) / 0.28)
    assert lbo.default_probability(dd) == pytest.approx(0.5 * (1 + math.erf(-dd / math.sqrt(2))))
    with_leases = lbo.distance_to_default(100, 300, 100, 0.6, -0.4)                 # i leasing sono debito
    assert with_leases == pytest.approx(dd)


def test_foglio_excel_ricalcolato_da_il_rendimento_dell_app():
    r = lbo.lbo(**CASE)
    sheet, out = em.lbo_sheet(r, lbo.settings(None), "USD")
    stripped = Sheet("LBO", cells={k: type(c)(None if c.formula else c.value, c.formula, c.style)
                                   for k, c in sheet.cells.items()})
    got = evaluate(stripped)
    row, cached = out["irr"]
    assert got[f"B{row}"] == pytest.approx(r.irr, rel=1e-9)
    for name, cell in sheet.cells.items():
        if cell.formula is not None and isinstance(cell.value, float) and math.isfinite(cell.value):
            assert got[f"{col_letter(name[1])}{name[0]}"] == pytest.approx(cell.value, rel=1e-9, abs=1e-6), name


def test_metriche_e_scheda_sulla_demo(cfg, demo_db, tmp_path):
    from modules.dashboard_data import DashboardData
    from modules.metrics import compute_metrics

    d = DashboardData(cfg, demo_db, tmp_path)
    tickers = d.index_tickers[:15]
    m = compute_metrics(d.data, tickers, cfg=cfg)
    assert m["lbo_irr"].notna().any() and m["distance_to_default"].notna().any()
    assert not any(c.startswith("_") for c in m.columns)
    t = m["lbo_irr"].dropna().index[0]
    r = d.lbo(t)
    assert r.ok and r.irr == pytest.approx(m.at[t, "lbo_irr"], rel=1e-9)  # scheda e catalogo: stesso numero


def test_stessa_regola_di_esclusione_in_scheda_e_catalogo(cfg, demo_db, tmp_path):
    """Un'azienda del settore finanziario senza voci bancarie (es. Visa) ha il test sia nel catalogo sia nella scheda."""
    from modules.dashboard_data import DashboardData
    from modules.metrics import compute_metrics

    d = DashboardData(cfg, demo_db, tmp_path)
    m = compute_metrics(d.data, d.index_tickers, cfg=cfg)
    for t in d.index_tickers:
        r = d.lbo(t)
        if r is not None and np.isfinite(m.at[t, "price"]):
            assert r.ok == bool(np.isfinite(m.at[t, "lbo_irr"])), t
