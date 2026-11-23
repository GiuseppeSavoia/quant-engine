"""Buchi di lettura dei bilanci SEC che lasciavano il giudizio senza "Solidità": EBIT, interessi, utile ante imposte.
Dati sintetici con le etichette XBRL usate davvero dalle aziende citate (nessuna rete)."""
import numpy as np
import pandas as pd
import pytest

from modules.sec_edgar import TTM_SOURCE, _flow_metrics, parse_sec

PRETAX = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"
DOMESTIC = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"
FOREIGN = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign"


def test_gross_interest_labels_used_only_after_the_old_ones():
    # ConocoPhillips, DuPont, Phillips 66: "Interest and debt expense"
    cop = pd.Series({PRETAX: 100.0, "InterestAndDebtExpense": 8.0})
    f = _flow_metrics(cop)
    assert f["interest_expense"] == pytest.approx(8.0) and f["operating_income"] == pytest.approx(108.0)
    # Realty Income, Invitation Homes, Coherent: interessi tra i costi operativi; Welltower: sui prestiti
    assert _flow_metrics(pd.Series({PRETAX: 10.0, "InterestExpenseOperating": 9.0}))["interest_expense"] == 9.0
    assert _flow_metrics(pd.Series({PRETAX: 10.0, "InterestExpenseBorrowings": 6.0}))["interest_expense"] == 6.0
    # chi ha già una delle etichette di prima non cambia (Occidental: InterestExpenseDebt prima della voce nuova)
    oxy = pd.Series({PRETAX: 100.0, "InterestExpenseDebt": 9.4, "InterestAndDebtExpense": 6.2})
    assert _flow_metrics(oxy)["interest_expense"] == pytest.approx(9.4)


def test_ebit_from_net_interest_but_never_coverage():
    # Baker Hughes: solo interessi netti (negativi = oneri netti) -> EBIT = ante imposte + 328
    bkr = pd.Series({PRETAX: 2684.0, "InterestIncomeExpenseNet": -328.0})
    f = _flow_metrics(bkr)
    assert f["operating_income"] == pytest.approx(3012.0)
    assert np.isnan(f["interest_expense"])            # gli interessi netti non sono gli oneri lordi
    # TJX: proventi netti -> l'EBIT esclude anche i proventi finanziari
    tjx = pd.Series({PRETAX: 3739.0, "InterestRevenueExpenseNet": 66.0})
    assert _flow_metrics(tjx)["operating_income"] == pytest.approx(3673.0)
    # le etichette lorde hanno la precedenza sui netti
    both = pd.Series({PRETAX: 100.0, "InterestExpense": 10.0, "InterestIncomeExpenseNonoperatingNet": -4.0})
    assert _flow_metrics(both)["operating_income"] == pytest.approx(110.0)
    # banca: "InterestIncomeExpenseNet" è il margine di interesse, non si sottrae all'utile
    bank = pd.Series({PRETAX: 50.0, "InterestIncomeExpenseNet": 90.0, "NoninterestExpense": 60.0})
    assert np.isnan(_flow_metrics(bank)["operating_income"])
    # senza approssimazioni restano vuoti
    assert np.isnan(_flow_metrics(bkr, level=0)["operating_income"])


def test_interest_paid_is_the_last_resort():
    # GE: oneri finanziari con una voce propria (assente da companyfacts), solo gli interessi pagati
    ge = pd.Series({PRETAX: 10_000.0, "InterestPaidNet": 882.0})
    f = _flow_metrics(ge)
    assert f["interest_expense"] == pytest.approx(882.0) and f["operating_income"] == pytest.approx(10_882.0)
    assert np.isnan(_flow_metrics(ge, level=0)["interest_expense"])
    # con gli oneri a conto economico i pagati non contano
    casy = pd.Series({PRETAX: 100.0, "InterestExpense": 96.6, "InterestPaidNet": 110.5})
    assert _flow_metrics(casy)["interest_expense"] == pytest.approx(96.6)
    # interessi netti prima dei pagati per l'EBIT (Newmont ha entrambi)
    nem = pd.Series({PRETAX: 500.0, "InterestIncomeExpenseNonoperatingNet": -229.0, "InterestPaidNet": 233.0})
    assert _flow_metrics(nem)["operating_income"] == pytest.approx(729.0)
    assert _flow_metrics(nem)["interest_expense"] == pytest.approx(233.0)


def test_pretax_from_domestic_and_foreign():
    # GE (nota sulle imposte): Stati Uniti + estero = totale, usato solo senza la riga totale
    assert _flow_metrics(pd.Series({DOMESTIC: 6659.0, FOREIGN: 3341.0}))["pretax_income"] == pytest.approx(10_000.0)
    assert _flow_metrics(pd.Series({PRETAX: 9_999.0, DOMESTIC: 6659.0, FOREIGN: 3341.0}))["pretax_income"] == 9_999.0
    # PulteGroup: solo "Domestic" come riga del conto economico (azienda solo americana)
    phm = pd.Series({DOMESTIC: 1697.0, "InterestExpense": 0.6})
    f = _flow_metrics(phm)
    assert f["pretax_income"] == pytest.approx(1697.0) and f["operating_income"] == pytest.approx(1697.6)
    assert np.isnan(_flow_metrics(pd.Series({FOREIGN: 5.0}))["pretax_income"])  # il solo estero non è il totale


def test_pass_through_entities_without_taxes():
    # Invitation Homes: niente utile ante imposte né imposte (REIT) -> utile netto con le minoranze + interessi
    invh = pd.Series({"ProfitLoss": 590.0, "NetIncomeLoss": 588.0, "InterestExpenseOperating": 353.0})
    assert _flow_metrics(invh)["operating_income"] == pytest.approx(943.0)
    # chi dichiara le imposte ma non l'utile ante imposte: utile + imposte + interessi (le imposte non sono zero)
    taxed = invh.copy()
    taxed["IncomeTaxExpenseBenefit"] = 100.0
    assert _flow_metrics(taxed)["operating_income"] == pytest.approx(590.0 + 100.0 + 353.0)
    # negli ultimi 12 mesi conta se le imposte compaiono in uno qualunque dei periodi combinati
    assert np.isnan(_flow_metrics(invh, present={"ProfitLoss", "IncomeTaxExpenseBenefit"})["operating_income"])


def _f(end, val, accn, filed, start=None, form="10-K"):
    d = {"end": end, "val": val, "accn": accn, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


def _ttm_payload(pretax_10k: list[dict], pretax_10q_label: str, extra: dict | None = None) -> dict:
    """Esercizio 2023 dal 10-K, 10-Q del 2° trimestre 2024 con il semestre 2024 e 2023."""
    K, Q = "0000000001-24-000001", "0000000001-24-000050"
    q = dict(form="10-Q")
    gaap = {
        pretax_10q_label: {"units": {"USD": [
            _f("2024-06-30", 60.0, Q, "2024-08-01", start="2024-01-01", **q),
            _f("2023-06-30", 50.0, Q, "2024-08-01", start="2023-01-01", **q),
        ]}},
        "Revenues": {"units": {"USD": [
            _f("2023-12-31", 1000.0, K, "2024-02-15", start="2023-01-01"),
            _f("2024-06-30", 520.0, Q, "2024-08-01", start="2024-01-01", **q),
            _f("2023-06-30", 500.0, Q, "2024-08-01", start="2023-01-01", **q),
        ]}},
    }
    for label, val in pretax_10k:
        gaap.setdefault(label, {"units": {"USD": []}})["units"]["USD"].append(
            _f("2023-12-31", val, K, "2024-02-15", start="2023-01-01"))
    for label, facts in (extra or {}).items():
        gaap.setdefault(label, {"units": {"USD": []}})["units"]["USD"].extend(facts)
    return {"facts": {"us-gaap": gaap}}


def test_ttm_never_mixes_domestic_note_with_domestic_line():
    """10-Q con "Domestic" come riga del conto economico (= totale), 10-K con il totale e la nota Stati Uniti/estero:
    gli ultimi 12 mesi = 60 + 120 (totale del 10-K) - 50, non 60 + 80 (solo la parte americana) - 50."""
    payload = _ttm_payload([(PRETAX, 120.0), (DOMESTIC, 80.0), (FOREIGN, 40.0)], DOMESTIC)
    df = parse_sec(payload, "XYZ")
    v = df[(df["source"] == TTM_SOURCE)].set_index("metric")["value"]
    assert v["pretax_income"] == pytest.approx(130.0)


def test_ttm_prefers_exact_labels_before_approximations():
    """Interessi lordi solo nel 10-K, interessi pagati in tutti i periodi: i lordi non bastano (mancano nel 10-Q) né in
    comune né periodo per periodo, quindi negli ultimi 12 mesi si usano i pagati, mentre l'esercizio resta sui lordi."""
    K, Q = "0000000001-24-000001", "0000000001-24-000050"
    q = dict(form="10-Q")
    paid = [_f("2023-12-31", 9.0, K, "2024-02-15", start="2023-01-01"),
            _f("2024-06-30", 5.0, Q, "2024-08-01", start="2024-01-01", **q),
            _f("2023-06-30", 4.0, Q, "2024-08-01", start="2023-01-01", **q)]
    gross = [_f("2023-12-31", 10.0, K, "2024-02-15", start="2023-01-01")]
    payload = _ttm_payload([(PRETAX, 120.0)], PRETAX, {"InterestPaidNet": paid, "InterestExpense": gross})
    df = parse_sec(payload, "XYZ")
    ttm = df[df["source"] == TTM_SOURCE].set_index("metric")["value"]
    assert ttm["interest_expense"] == pytest.approx(10.0)      # pagati: 5 + 9 - 4
    assert ttm["operating_income"] == pytest.approx(130.0 + 10.0)
    annual = df[df["source"] == "sec"].set_index("metric")["value"]
    assert annual["interest_expense"] == pytest.approx(10.0)   # nel 10-K valgono gli oneri lordi
    assert annual["operating_income"] == pytest.approx(130.0)


def test_ttm_keeps_the_old_interest_labels_when_they_work():
    """Medtronic: nel 10-K InterestExpense (oneri netti, 719) e InterestExpenseBorrowings (lordi, 916), nei 10-Q
    InterestExpenseNonoperating e InterestExpenseBorrowings. Gli ultimi 12 mesi restano sulle etichette di sempre
    (combinate periodo per periodo, come prima), coerenti con l'esercizio: la voce nuova non li cambia."""
    K, Q = "0000000001-24-000001", "0000000001-24-000050"
    q = dict(form="10-Q")
    extra = {
        "InterestExpense": [_f("2023-12-31", 719.0, K, "2024-02-15", start="2023-01-01")],
        "InterestExpenseNonoperating": [_f("2024-06-30", 370.0, Q, "2024-08-01", start="2024-01-01", **q),
                                        _f("2023-06-30", 330.0, Q, "2024-08-01", start="2023-01-01", **q)],
        "InterestExpenseBorrowings": [_f("2023-12-31", 916.0, K, "2024-02-15", start="2023-01-01"),
                                      _f("2024-06-30", 470.0, Q, "2024-08-01", start="2024-01-01", **q),
                                      _f("2023-06-30", 430.0, Q, "2024-08-01", start="2023-01-01", **q)],
    }
    df = parse_sec(_ttm_payload([(PRETAX, 120.0)], PRETAX, extra), "XYZ")
    ttm = df[df["source"] == TTM_SOURCE].set_index("metric")["value"]
    assert ttm["interest_expense"] == pytest.approx(370.0 + 719.0 - 330.0)
    assert df[df["source"] == "sec"].set_index("metric")["value"]["interest_expense"] == pytest.approx(719.0)


def test_misdated_interest_paid_does_not_move_the_quarter():
    """Un 10-Q (Expand Energy 2012) con gli interessi pagati datati al trimestre dopo: il trimestre resta quello delle
    voci principali e gli ultimi 12 mesi non spariscono."""
    Q = "0000000001-24-000050"
    wrong = [_f("2024-09-30", 12.0, Q, "2024-08-01", start="2024-07-01", form="10-Q")]
    df = parse_sec(_ttm_payload([(PRETAX, 120.0)], PRETAX, {"InterestPaid": wrong}), "XYZ")
    assert set(df.loc[df["source"] == TTM_SOURCE, "period_end"]) == {pd.Timestamp("2024-06-30")}


def test_pretax_from_after_tax_income_plus_taxes():
    # Phillips 66: niente utile ante imposte nei 10-Q, sì l'utile delle attività in funzionamento e le imposte
    psx = pd.Series({"IncomeLossFromContinuingOperations": 2000.0, "IncomeTaxExpenseBenefit": 500.0,
                     "InterestAndDebtExpense": 800.0})
    f = _flow_metrics(psx)
    assert f["pretax_income"] == pytest.approx(2500.0) and f["operating_income"] == pytest.approx(3300.0)
    assert np.isnan(_flow_metrics(psx, level=1)["pretax_income"])        # è un'approssimazione: ultimo livello
    # Casey's: solo utile netto e imposte -> va bene solo senza attività cessate
    casy = pd.Series({"NetIncomeLoss": 550.0, "IncomeTaxExpenseBenefit": 170.0, "InterestExpense": 97.0})
    assert _flow_metrics(casy)["operating_income"] == pytest.approx(817.0)
    disc = casy.copy()
    disc["IncomeLossFromDiscontinuedOperationsNetOfTax"] = 40.0
    assert np.isnan(_flow_metrics(disc)["pretax_income"])
    # senza imposte dichiarate nessuna ricostruzione dell'utile ante imposte
    assert np.isnan(_flow_metrics(pd.Series({"IncomeLossFromContinuingOperations": 2000.0}))["pretax_income"])
