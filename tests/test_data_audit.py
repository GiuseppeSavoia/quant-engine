"""Test della verifica a campione (nessuna rete: prospetti e prezzi finti)."""
import numpy as np
import pandas as pd
import pytest

from modules import data_audit as da


def test_classify_tolerances():
    assert da.classify(100.0, 101.0)[1] == "uguale"
    assert da.classify(100.0, 95.0)[1] == "piccola"
    assert da.classify(100.0, 50.0) == (pytest.approx(0.5), "grande")
    assert da.classify(np.nan, 5.0)[1] == "manca da noi" and da.classify(5.0, np.nan)[1] == "manca in Yahoo"
    assert da.classify(0.0, 0.0) == (0.0, "uguale")


def test_compare_statements_matches_periods_and_definitions():
    col = pd.Timestamp("2025-09-27")
    stmts = {"inc": pd.DataFrame({col: [1000.0, 90.0]}, index=["Total Revenue", "Net Income Common Stockholders"]),
             "cf": pd.DataFrame({col: [-40.0, -10.0]}, index=["Cash Dividends Paid", "Capital Expenditure"]),
             "bs": pd.DataFrame({col: [130.0, 30.0]}, index=["Total Debt", "Capital Lease Obligations"])}
    ours = pd.DataFrame({"revenue": [1000.0], "net_income": [60.0], "dividends_paid": [40.0], "capex": [-10.0],
                         "total_debt": [100.0]}, index=[pd.Timestamp("2025-09-30")])   # 3 giorni di scarto: stesso esercizio
    res = da.compare_statements(ours, stmts, "X").set_index("metric")
    assert res.at["revenue", "verdict"] == "uguale" and res.at["net_income", "verdict"] == "grande"
    assert res.at["dividends_paid", "verdict"] == "uguale"          # Yahoo negativo, sistema positivo
    assert res.at["total_debt", "verdict"] == "uguale"              # Yahoo comprende i leasing: tolti
    assert res.at["shares", "verdict"] == "assente in entrambi" if "shares" in res.index else True


def test_compare_prices_finds_one_source_jumps():
    days = pd.bdate_range("2026-01-01", periods=60)
    a = pd.Series(np.linspace(100, 120, 60), index=days)
    b = a.copy()
    b.iloc[30:] *= 1.05                                              # salto solo in una fonte
    res = da.compare_prices(a, b)
    assert res["bad_days"] == 1 and res["worst_day"] == days[30].date().isoformat()
    assert res["last_diff"] == pytest.approx(1 / 1.05 - 1)
    assert np.isnan(da.compare_prices(a.iloc[:5], b.iloc[:5])["bad_days"])


def test_sample_is_stratified_and_reproducible():
    by = {"Tech": list("ABCDEF"), "Energy": ["X", "Y"]}
    s1, s2 = da.sample(by, 2, ["Z"]), da.sample(by, 2, ["Z"])
    assert s1 == s2 and s1[0] == "Z" and len(s1) == 5
    assert sum(t in by["Tech"] for t in s1) == 2 and sum(t in by["Energy"] for t in s1) == 2


def test_debt_cash_and_capex_fixes_found_by_the_audit():
    from modules.sec_edgar import _balance_metrics, _flow_metrics

    # Realty Income: debito solo come NotesPayable (25 mld) e debito garantito; le voci di bilancio quasi zero
    o = pd.Series({"NotesPayable": 25.0, "SecuredDebt": 0.04, "LongTermDebt": 0.5, "CashAndCashEquivalentsAtCarryingValue": 1.0})
    assert _balance_metrics(o)["total_debt"] == pytest.approx(25.04)
    # azienda normale: le voci di "strumento" più piccole del bilancio non cambiano nulla
    normal = pd.Series({"LongTermDebtNoncurrent": 90.0, "DebtCurrent": 10.0, "SeniorNotes": 80.0})
    assert _balance_metrics(normal)["total_debt"] == pytest.approx(100.0)
    # NVIDIA: titoli a breve con la voce generica, accettati perché stanno nelle attività correnti
    nvda = pd.Series({"CashAndCashEquivalentsAtCarryingValue": 10.0, "AvailableForSaleSecuritiesDebtSecurities": 40.0,
                      "AssetsCurrent": 120.0})
    assert _balance_metrics(nvda)["cash"] == pytest.approx(50.0)
    # voce generica che comprende titoli a lungo termine (più delle attività correnti): solo la cassa
    apple_like = nvda.copy()
    apple_like["AvailableForSaleSecuritiesDebtSecurities"] = 200.0
    assert _balance_metrics(apple_like)["cash"] == pytest.approx(10.0)
    # Roper: software capitalizzato sommato al capex
    rop = pd.Series({"NetCashProvidedByUsedInOperatingActivities": 100.0,
                     "PaymentsToAcquirePropertyPlantAndEquipment": 47.0, "PaymentsToDevelopSoftware": 58.0})
    f = _flow_metrics(rop)
    assert f["capex"] == pytest.approx(-105.0) and f["free_cash_flow"] == pytest.approx(-5.0)

    # Exxon: niente riga di utile operativo, EBIT = utile prima delle imposte + interessi
    xom = pd.Series({"IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 50.0,
                     "InterestExpense": 1.0})
    assert _flow_metrics(xom)["operating_income"] == pytest.approx(51.0)
    # Baxter: nel 10-Q solo la quota a breve (il lungo termine ha voci proprie): debito sconosciuto, non 0
    bax_q = pd.Series({"LongTermDebtAndCapitalLeaseObligationsCurrent": 0.8, "ShortTermBorrowings": 0.0})
    assert np.isnan(_balance_metrics(bax_q, quarterly=True)["total_debt"])
    assert _balance_metrics(bax_q)["total_debt"] == pytest.approx(0.8)            # in un 10-K resta com'è
    apd = pd.Series({"DebtAndCapitalLeaseObligations": 17.7, "ShortTermBorrowings": 0.035})
    assert _balance_metrics(apd)["total_debt"] == pytest.approx(17.735)
    # Walmart: voci di strumento appena più grandi (+2%) del bilancio: resta il bilancio
    wmt = pd.Series({"LongTermDebtNoncurrent": 35.0, "DebtCurrent": 5.5, "DebtInstrumentCarryingAmount": 40.5,
                     "ShortTermBorrowings": 0.8})
    assert _balance_metrics(wmt)["total_debt"] == pytest.approx(40.5)
