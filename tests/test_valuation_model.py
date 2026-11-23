"""Test della valutazione a leve (Damodaran), degli scenari, della simulazione, del diario e del PDF."""
import numpy as np
import pandas as pd
import pytest

from modules import valuation_model as vm

BASE = vm.Base(revenue=1000.0, margin=0.15, invested_capital=800.0, cash=100.0, debt=300.0, shares=100.0,
               price=12.0, risk_free=0.04)


def _lv(**kw):
    d = dict(growth=0.08, target_margin=0.15, sales_to_capital=1.5, wacc=0.09, terminal_wacc=0.09,
             terminal_growth=0.03, terminal_roic=0.09, tax_rate=0.2)
    d.update(kw)
    return vm.Levers(**d)


def test_no_growth_is_a_perpetuity():
    lv = _lv(growth=0.0, terminal_growth=0.0)
    r = vm.value(BASE, lv)
    nopat = 1000 * 0.15 * 0.8
    assert float(r.operating_value) == pytest.approx(nopat / 0.09, rel=1e-9)
    assert float(r.per_share) == pytest.approx((nopat / 0.09 + 100 - 300) / 100, rel=1e-9)


def test_levers_move_value_the_right_way():
    v = lambda **kw: float(vm.value(BASE, _lv(**kw)).per_share)  # noqa: E731
    assert v(target_margin=0.20) > v() > v(target_margin=0.10)
    assert v(wacc=0.08, terminal_wacc=0.08) > v() > v(wacc=0.10, terminal_wacc=0.10)
    assert v(sales_to_capital=3.0) > v()                         # meno capitale per crescere
    assert v(terminal_roic=0.20) > v()                           # vantaggio competitivo duraturo
    assert v(failure_prob=0.3) < v()


def test_vectorized_equals_scalar():
    growths = np.array([0.02, 0.08, 0.15])
    margins = np.array([0.10, 0.15, 0.20])
    vec = vm.value(BASE, _lv(growth=growths, target_margin=margins)).per_share
    one = [float(vm.value(BASE, _lv(growth=g, target_margin=m)).per_share) for g, m in zip(growths, margins)]
    assert np.allclose(vec, one)


def test_table_and_checks():
    r = vm.value(BASE, _lv(), with_table=True)
    t = r.table
    assert len(t) == 10 and t["crescita"].iloc[0] == pytest.approx(0.08) and t["crescita"].iloc[-1] == pytest.approx(0.03)
    assert t["ricavi"].iloc[0] == pytest.approx(1080.0)
    assert t["reinvestimento"].iloc[0] == pytest.approx(80 / 1.5)
    msgs = " ".join(m for _, m in vm.value(BASE, _lv(terminal_growth=0.05), with_table=True).checks)
    assert "tasso privo di rischio" in msgs                       # crescita per sempre sopra il risk-free
    msgs = " ".join(m for _, m in vm.value(BASE, _lv(sales_to_capital=20), with_table=True).checks)
    assert "capitale nuovo" in msgs                               # si cresce quasi senza investire
    err = vm.value(BASE, _lv(terminal_wacc=0.02), with_table=True).checks
    assert err[0][0] == "errore"


def test_implied_growth_round_trip():
    g = vm.implied_growth(BASE, _lv())
    assert g is not None
    assert float(vm.value(BASE, _lv(growth=g)).per_share) == pytest.approx(BASE.price, rel=1e-4)
    rich = vm.Base(**{**BASE.__dict__, "price": 1e6})
    assert vm.implied_growth(rich, _lv()) is None


def test_scenarios_and_simulation():
    sc = vm.default_scenarios(BASE, 0.08, 0.09, 0.2, 0.025, 1.5, 0.09)
    assert sc["Pessimista"].growth < sc["Base"].growth < sc["Ottimista"].growth
    res = vm.scenario_values(BASE, sc, {"Pessimista": 0.25, "Base": 0.5, "Ottimista": 0.25})
    v = res["per_scenario"]
    assert v["Pessimista"] < v["Base"] < v["Ottimista"]
    assert res["expected"] == pytest.approx(0.25 * v["Pessimista"] + 0.5 * v["Base"] + 0.25 * v["Ottimista"])
    sim = vm.simulate(BASE, sc["Pessimista"], sc["Base"], sc["Ottimista"], n=3000)
    assert sim["p10"] < sim["p50"] < sim["p90"] and 0 <= sim["prob_above_price"] <= 1
    assert v["Pessimista"] <= sim["p10"] and sim["p90"] <= v["Ottimista"]
    assert list(sim["swing"]) and set(sim["swing"]) == set(vm.SIMULATED)
    again = vm.simulate(BASE, sc["Pessimista"], sc["Base"], sc["Ottimista"], n=3000)
    assert again["p50"] == sim["p50"]                             # seme fisso: riproducibile


def test_diary_research_and_pdf(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    setup = d.valuation_setup("AAPL")
    assert setup is not None and not setup["saved"] and set(setup["scenarios"]) == {"Pessimista", "Base", "Ottimista"}
    plaus = d.plausibility("AAPL", setup["scenarios"]["Base"])
    assert {"value", "p10", "p90", "verdict"} <= set(plaus.columns) and len(plaus) >= 4
    entry = d.save_valuation("AAPL", setup, setup["scenarios"], setup["probabilities"], "storia", "nota")
    assert d.note("AAPL")["fair_value"] == pytest.approx(entry["expected_value"])   # diventa il tuo fair value
    diary = d.valuation_diary("AAPL")
    assert len(diary) == 1 and diary["return_since"].iloc[0] == pytest.approx(0.0)
    again = d.valuation_setup("AAPL")
    assert again["saved"] and again["scenarios"]["Base"].growth == pytest.approx(setup["scenarios"]["Base"].growth)
    assert not d.valuation_setup("AAPL", use_saved=False)["saved"]

    d.save_research("AAPL", {"one_liner": "Ecosistema chiuso", "story": "s", "catalysts": "c", "risks": "r",
                             "change_mind": "m", "review_date": "2026-01-01"})
    assert d.research("AAPL")["one_liner"] == "Ecosistema chiuso"
    d.follow("AAPL")
    alerts = d.watchlist_table().set_index("ticker").loc["AAPL", "alerts"]
    assert any(icon == "REV" for icon, _ in alerts)                # revisione scaduta: avviso nella home
    from modules.research_pdf import company_pdf

    pdf = company_pdf(d, "AAPL")
    assert pdf[:4] == b"%PDF" and len(pdf) > 5000
