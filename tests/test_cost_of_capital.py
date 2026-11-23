"""Test del costo del capitale alla Damodaran e delle correzioni della valutazione (R&S, leasing)."""
import numpy as np
import pandas as pd
import pytest

from modules import cost_of_capital as cc
from modules.metrics import beneish


def test_regression_beta_recovers_true_beta():
    rng = np.random.default_rng(0)
    days = pd.bdate_range("2023-01-02", periods=600)
    m = rng.normal(0.0004, 0.01, len(days))
    stock = 1.5 * m + rng.normal(0, 0.004, len(days))
    close = pd.DataFrame({"SPY": 100 * np.exp(np.cumsum(m)), "X": 50 * np.exp(np.cumsum(stock))}, index=days)
    beta = cc.regression_betas(close, "SPY", years=2)
    assert beta["X"] == pytest.approx(1.5, abs=0.15) and "SPY" not in beta


def test_unlever_relever_round_trip_and_industry_groups():
    bu = cc.unlevered(1.2, debt=50, equity=100, tax=0.25)
    assert bu == pytest.approx(1.2 / (1 + 0.75 * 0.5))
    assert cc.relevered(bu, 50, 100, 0.25) == pytest.approx(1.2)
    table = pd.DataFrame({
        "beta": [1.0, 1.2, 0.8, 1.1, 0.9, 2.0, 2.2],
        "debt": [0, 10, 20, 0, 5, 0, 0], "equity": [100] * 7, "cash": [0] * 7,
        "sector": ["Tech"] * 5 + ["Energy"] * 2, "sub_industry": ["Software"] * 5 + ["Oil", "Gas"],
    }, index=list("ABCDEFG"))
    out = cc.industry_betas(table, tax=0.25, min_peers=5)
    assert out.loc["A", "group"] == "Software" and out.loc["A", "n"] == 5
    assert out.loc["F", "group"] == "Energy"                     # sotto-settori troppo piccoli: si usa il settore
    assert out.loc["F", "beta_u"] == pytest.approx(2.1)


def test_le_aziende_fuori_riferimento_non_spostano_le_mediane():
    """Le europee (beta contro l'SPY, basso) non entrano nelle mediane USA ma ne prendono quella del settore."""
    table = pd.DataFrame({
        "beta": [1.0, 1.2, 0.8, 0.2, 0.1, 0.3], "debt": [0] * 6, "equity": [100] * 6, "cash": [0] * 6,
        "sector": ["Financials"] * 3 + ["Financials", "Financials", "Banks"],
        "sub_industry": [None] * 6,
    }, index=["CB", "SCHW", "RJF", "ISP.MI", "UCG.MI", "BAMI.MI"])
    out = cc.industry_betas(table, tax=0.25, min_peers=5, reference={"CB", "SCHW", "RJF"})
    assert out.loc["CB", "beta_u"] == pytest.approx(1.0) and out.loc["CB", "n"] == 3
    assert out.loc["ISP.MI", "group"] == "Financials" and out.loc["ISP.MI", "beta_u"] == pytest.approx(1.0)
    assert pd.isna(out.loc["BAMI.MI", "group"]) and pd.isna(out.loc["BAMI.MI", "beta_u"])  # settore senza USA


def test_synthetic_rating_and_wacc():
    assert cc.synthetic_rating(12.0)[0] == "AAA" and cc.synthetic_rating(0.5)[0] == "C"
    assert cc.synthetic_rating(-3.0)[0] == "D"
    w = cc.wacc(0.04, 1.0, 0.05, 0.01, debt=25, equity=75, marginal_tax=0.25)
    assert w["cost_of_equity"] == pytest.approx(0.09)
    assert w["wacc"] == pytest.approx(0.75 * 0.09 + 0.25 * 0.05 * 0.75)


def test_implied_erp():
    erp = cc.implied_erp(0.03, 0.04, growth=0.05)
    assert 0.02 < erp < 0.06
    assert cc.implied_erp(0.04, 0.04) > erp                      # più flussi a parità di prezzo = premio più alto
    assert np.isnan(cc.implied_erp(0.0, 0.04))


def test_beneish_neutral_and_red_flags():
    cur = pd.Series({"revenue": 100.0, "gross_profit": 40.0, "net_income": 10.0, "operating_cash_flow": 10.0,
                     "depreciation": 5.0, "sga": 20.0})
    latest = pd.Series({"total_assets": 200.0, "receivables": 20.0, "current_assets": 80.0, "ppe_net": 50.0,
                        "current_liabilities": 40.0, "total_debt": 30.0})
    prev = pd.Series({**cur.to_dict(), **latest.to_dict()})
    assert beneish(cur, latest, prev) == pytest.approx(-2.48, abs=0.005)      # tutto uguale: solo la costante
    pumped = latest.copy()
    pumped["receivables"] = 60.0                                  # crediti triplicati a parità di ricavi
    cash_poor = cur.copy()
    cash_poor["operating_cash_flow"] = -20.0                       # utili non coperti da cassa
    assert beneish(cash_poor, pumped, prev) > -1.78
    assert np.isnan(beneish(cur, latest, pd.Series(dtype=float)))


def test_cost_of_capital_and_adjustments_in_dashboard(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    d = DashboardData(cfg, demo_db, tmp_path)
    c = d.cost_of_capital("AAPL")
    assert c["risk_free"] < c["wacc"] < 0.25 and c["beta"] > 0 and c["rating"]
    assert c["cost_of_equity"] == pytest.approx(c["risk_free"] + c["beta"] * c["erp"])
    erp = d.implied_erp()
    assert erp["cash_yield"] > 0
    plain = d.valuation_setup("AAPL", rnd=False, leases=False)
    full = d.valuation_setup("AAPL", rnd=True, leases=True)
    assert full["base"].debt > plain["base"].debt                # leasing come debito
    assert full["base"].invested_capital > plain["base"].invested_capital
    assert full["base"].margin != plain["base"].margin and len(full["adjustments"]) == 2
    assert full["scenarios"]["Base"].wacc == pytest.approx(full["cost_of_capital"]["wacc"])


def test_europee_beta_contro_l_indice_europeo_e_gruppo_solo_se_sensato():
    """Beta delle europee contro `europe.benchmark`; gruppo europeo solo se grande abbastanza e con la correzione per
    la cassa sensata (le banche ESEF hanno "cassa" > equity + debito); i numeri USA non cambiano."""
    from types import SimpleNamespace

    from modules.services.valuation import ValuationService

    rng = np.random.default_rng(1)
    days = pd.bdate_range("2023-01-02", periods=600)
    us_m, eu_m = rng.normal(0, 0.01, len(days)), rng.normal(0, 0.01, len(days))
    cols = {"SPY": 100 * np.exp(np.cumsum(us_m))}
    us = [f"U{i}" for i in range(6)]
    eu = [f"E{i}.PA" for i in range(6)]
    for t in us:
        cols[t] = 50 * np.exp(np.cumsum(1.0 * us_m + rng.normal(0, 0.004, len(days))))
    for t in eu:
        cols[t] = 50 * np.exp(np.cumsum(1.5 * eu_m + rng.normal(0, 0.004, len(days))))
    close = pd.DataFrame(cols, index=days)
    bench = pd.DataFrame({"ticker": "^STOXX50E", "date": days, "close": 100 * np.exp(np.cumsum(eu_m))})
    names = us + eu
    metrics = pd.DataFrame({"market_cap": 100.0}, index=names)
    companies = pd.DataFrame({"sector": "Industrials", "sub_industry": None}, index=names)

    def run(cash_eu, eu_sector="Industrials"):
        companies.loc[eu, "sector"] = eu_sector
        ctx = SimpleNamespace(_industry=None, cfg={"valuation": {"beta_years": 2, "min_industry_peers": 5},
                                                   "europe": {"benchmark": "^STOXX50E"}},
                              metrics=lambda: metrics, companies=companies, index_tickers=us,
                              foreign=pd.DataFrame({"ticker": eu}),
                              db=SimpleNamespace(load_prices=lambda t: bench))
        ctx.data = SimpleNamespace(close=close, benchmark="SPY", last_date=days[-1])
        svc = ValuationService(ctx)
        svc._capital_structure = lambda t, leases=True: {"debt": 0.0, "lease": 0.0,
                                                         "cash": cash_eu if t in eu else 0.0}
        return svc.industry_table()

    ok = run(cash_eu=10.0)
    assert ok.loc["E0.PA", "beta_benchmark"] == "^STOXX50E" and ok.loc["U0", "beta_benchmark"] == "SPY"
    assert ok.loc["E0.PA", "beta"] == pytest.approx(1.5, abs=0.15)
    assert ok.loc["E0.PA", "group"] == "Europa · Industrials" and ok.loc["E0.PA", "n"] == 6
    assert ok.loc["U0", "group"] == "Industrials" and ok.loc["U0", "beta_u_ops"] == pytest.approx(1.0, abs=0.15)
    banks = run(cash_eu=150.0)  # "cassa" più grande di equity + debito: correzione assurda, resta il gruppo USA
    assert banks.loc["E0.PA", "group"] == "Industrials"
    assert banks.loc["E0.PA", "beta_u_ops"] == pytest.approx(ok.loc["U0", "beta_u_ops"])
    # banche europee con il nome ICB: tradotto in GICS solo per il gruppo, e niente correzione per la cassa
    fin = run(cash_eu=150.0, eu_sector="Banks")
    assert fin.loc["E0.PA", "group"] == "Europa · Financials"
    assert fin.loc["E0.PA", "beta_u_ops"] == pytest.approx(fin.loc["E0.PA", "beta_u"])
    assert fin.loc["E0.PA", "beta_u"] == pytest.approx(1.5, abs=0.15)
    assert companies.loc["E0.PA", "sector"] == "Banks"  # l'anagrafica non cambia (il profilo banca dipende da lì)


def test_gruppi_senza_correzione_per_la_cassa():
    table = pd.DataFrame({"beta": [1.0] * 5, "debt": [0] * 5, "equity": [100] * 5, "cash": [60] * 5,
                          "sector": ["Financials"] * 5, "sub_industry": [None] * 5}, index=list("ABCDE"))
    corrected = cc.industry_betas(table, 0.25, 5)
    plain = cc.industry_betas(table, 0.25, 5, no_cash_groups={"Financials"})
    assert corrected.loc["A", "beta_u_ops"] == pytest.approx(2.5) and plain.loc["A", "beta_u_ops"] == pytest.approx(1.0)
