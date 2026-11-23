"""Test delle aziende europee: elenco da Wikipedia, bilanci ESEF (xBRL-JSON), LEI e valute. Nessuna rete."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import europe

FTSE_HTML = "<table><tr><th>Ticker</th><th>Company</th><th>ISIN</th><th>ICB Sector</th></tr>" + "".join(
    f"<tr><td>T{i}.MI</td><td>Azienda {i}</td><td>IT000000000{i % 10}</td><td>Utilities</td></tr>" for i in range(40)) + \
    "</table>"


def _fact(concept, period, value, unit="iso4217:EUR", **dims):
    return {"value": str(value), "dimensions": {"concept": f"ifrs-full:{concept}", "entity": "scheme:LEI",
                                                "period": period, "unit": unit, **dims}}


YEAR = "2025-01-01T00:00:00/2026-01-01T00:00:00"
PREV = "2024-01-01T00:00:00/2025-01-01T00:00:00"
END, START = "2026-01-01T00:00:00", "2025-01-01T00:00:00"
ENI = {"facts": {str(i): f for i, f in enumerate([
    _fact("Revenue", YEAR, 90e9), _fact("Revenue", PREV, 88e9),
    _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 2.6e9),
    _fact("ProfitLossFromOperatingActivities", YEAR, 5.0e9),
    _fact("CashFlowsFromUsedInOperatingActivities", YEAR, 13.3e9),
    _fact("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", YEAR, 8.7e9),
    _fact("PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", YEAR, 0.3e9),
    _fact("DividendsPaidClassifiedAsFinancingActivities", YEAR, -3.0e9),
    _fact("BasicEarningsPerShare", YEAR, 0.8, unit="iso4217:EUR/xbrli:shares"),
    _fact("EquityAttributableToOwnersOfParent", END, 52.8e9), _fact("Assets", END, 146.9e9),
    _fact("NoncurrentPortionOfNoncurrentBorrowings", END, 20e9),
    _fact("CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", END, 5e9),
    _fact("CashAndCashEquivalents", END, 8.1e9), _fact("CashAndCashEquivalents", START, 10.2e9),
    _fact("Revenue", YEAR, 1e9, **{"ifrs-full:SegmentsAxis": "eni:PlenitudeMember"}),   # per segmento: ignorato
])}}


def test_parse_index_table():
    t = europe.parse_index_table(FTSE_HTML, "FTSE MIB")
    assert len(t) == 40 and t["ticker"].iloc[0] == "T0.MI" and t["currency"].iloc[0] == "EUR"
    assert t["isin"].iloc[1] == "IT0000000001" and set(t["index_name"]) == {"FTSE MIB"}


def test_esef_statements_annual_values_and_shares():
    facts, currency = europe.parse_esef_json(ENI)
    assert currency == "EUR" and not (facts["value"] == 1e9).any()
    df = europe.esef_statements(facts, "ENI.MI", pd.Timestamp("2026-03-23"))
    w = df.pivot(index="period_end", columns="metric", values="value")
    y = w.loc[pd.Timestamp("2025-12-31")]
    assert y["revenue"] == 90e9 and y["capex"] == pytest.approx(-9.0e9)              # impianti + intangibili
    assert y["free_cash_flow"] == pytest.approx(4.3e9) and y["dividends_paid"] == pytest.approx(3.0e9)
    assert y["total_debt"] == pytest.approx(25e9) and y["cash"] == pytest.approx(8.1e9)
    assert y["shares"] == pytest.approx(2.6e9 / 0.8)                                  # dall'utile per azione
    assert w.loc[pd.Timestamp("2024-12-31"), "revenue"] == 88e9                      # anche l'esercizio prima
    assert w.loc[pd.Timestamp("2024-12-31"), "cash"] == pytest.approx(10.2e9)
    assert set(df["source"]) == {"esef"} and set(df["filed"]) == {pd.Timestamp("2026-03-23")}
    usd = europe.esef_statements(facts, "STM.MI", pd.Timestamp("2026-03-23"), fx=0.9)  # bilancio in dollari
    u = usd.pivot(index="period_end", columns="metric", values="value").loc[pd.Timestamp("2025-12-31")]
    assert u["revenue"] == pytest.approx(81e9) and u["shares"] == pytest.approx(2.6e9 / 0.8)  # azioni non convertite


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_text(self, url):
        return FTSE_HTML

    def get_json(self, url):
        self.calls.append(url)
        if "gleif" in url:
            return {"data": [{"id": "LEI0" + url[-1]}]}  # ISIN IT000000000N -> LEI0N
        if "filter[country]=IT" in url:  # elenco dei bilanci per paese: un bilancio per ogni LEI dei test
            return {"data": [{"attributes": {"period_end": "2025-12-31", "published": "2026-03-23 18:59:17",
                                             "json_url": f"/LEI0{i}/2025.json"}} for i in range(10)], "links": {}}
        if "filter[country]" in url:
            return {"data": [], "links": {}}
        return ENI


def test_ingestor_universe_lei_and_statements(tmp_path):
    db = DBManager(tmp_path / "t.db")
    cfg = {"europe": {"indices": ["FTSE MIB"], "refresh_days": 7}}
    ing = europe.EuropeIngestor(db, FakeHttp(), cfg, fx_rate=lambda a, b, d: 1.0)
    comp = ing.refresh_universe()
    assert len(comp) == 40 and comp["lei"].notna().all()  # T0 e T10 hanno lo stesso ISIN finto: stesso LEI
    assert "T0.MI" in set(db.load_companies(only_index=False)["ticker"])
    assert ing.update() == 40                                                        # ogni azienda col suo LEI
    fin = db.load_financials(["T0.MI"])
    assert set(fin["source"]) == {"esef"} and (fin["metric"] == "revenue").any()
    assert ing.update() == 0                                                         # niente di nuovo: niente download
    from modules.universe import UniverseManager

    assert "T0.MI" in UniverseManager(db, {"universe": {"source": "list", "tickers": ["AAPL"]}}).data_tickers()


def test_rank_candidates_prefers_country_and_name():
    recs = [{"id": "FUND", "attributes": {"entity": {"legalName": {"name": "Adidas Pension Fund"},
                                                     "legalAddress": {"country": "LU"}}}},
            {"id": "ADS", "attributes": {"entity": {"legalName": {"name": "adidas AG"}, "legalAddress": {"country": "DE"}}}},
            {"id": "SUB", "attributes": {"entity": {"legalName": {"name": "adidas Insurance & Risk Consultants GmbH"},
                                                    "legalAddress": {"country": "DE"}}}}]
    assert europe.rank_candidates(recs, "Adidas", "DE")[0] == "ADS"


def test_match_archive_name():
    names = {"TTE": "TotalEnergies SE", "OR": "L'OREAL", "RMS": "Hermès International", "SAN": "Banco Santander, S.A.",
             "SANOFI": "Sanofi", "SGO": "Compagnie de Saint-Gobain", "ISP": "Intesa Sanpaolo S.p.A.", "X": "Total Gabon"}
    country = {k: "FR" for k in names} | {"SAN": "ES", "ISP": "IT", "X": "GA"}
    m = lambda n, c: europe.match_archive_name(n, names, country, c)  # noqa: E731
    assert m("TotalEnergies", "FR") == "TTE" and m("L'Oréal", "FR") == "OR" and m("Hermès", "FR") == "RMS"
    assert m("Banco Santander", "ES") == "SAN" and m("Sanofi", "FR") == "SANOFI" and m("Saint-Gobain", "FR") == "SGO"
    assert m("Intesa Sanpaolo", "IT") == "ISP"
    assert m("SAP", "DE") is None                           # niente bilanci nell'archivio: nessun nome simile inventato
    names2 = {"AI": "L'AIR LIQUIDE SOCIETE ANONYME POUR L'ETUDE ET L'EXPLOITATION DES PROCEDES GEORGES CLAUDE",
              "BBVA": "BANCO BILBAO VIZCAYA ARGENTARIA SOCIEDAD ANONIMA", "ING": "ING GROEP N.V.", "PH": "Pharming Group N.V."}
    c2 = {"AI": "FR", "BBVA": "ES", "ING": "NL", "PH": "NL"}
    assert europe.match_archive_name("Air Liquide", names2, c2, "FR") == "AI"
    assert europe.match_archive_name("BBVA", names2, c2, "ES") == "BBVA"
    assert europe.match_archive_name("ING Group", names2, c2, "NL") == "ING"


def test_extension_capex_only_when_ifrs_missing():
    def ext(concept, value):
        return {"value": str(value), "dimensions": {"concept": f"loreal:{concept}", "entity": "scheme:LEI",
                                                    "period": YEAR, "unit": "iso4217:EUR"}}
    facts = {"facts": {"1": _fact("CashFlowsFromUsedInOperatingActivities", YEAR, 8.3e9),
                       "2": ext("AcquisitionsDImmobilisationsCorporellesEtIncorporelles", 1.5e9),
                       "3": ext("ValeurDeCessionAcquisitionDesActionsPropres", 0.5e9),
                       "4": _fact("BasicEarningsLossPerShare", YEAR, 12.0, unit="iso4217:EUR/xbrli:shares"),
                       "5": _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 6.0e9)}}
    f, _ = europe.parse_esef_json(facts)
    y = europe.esef_statements(f, "OR.PA", pd.Timestamp("2026-03-01")).pivot(
        index="period_end", columns="metric", values="value").iloc[0]
    assert y["capex"] == pytest.approx(-1.5e9) and y["free_cash_flow"] == pytest.approx(6.8e9)
    assert y["shares"] == pytest.approx(0.5e9)                                         # da utile / utile per azione


# ---------------------------------------------------------------- voci alternative (copertura delle 69 aziende)
def _ext_fact(concept, period, value, unit="iso4217:EUR"):
    return {"value": str(value), "dimensions": {"concept": f"ext:{concept}", "entity": "scheme:LEI", "period": period,
                                                "unit": unit}}


def _statements(facts):
    """Ultimo esercizio come Series (voce -> valore)."""
    f, _ = europe.parse_esef_json({"facts": {str(i): x for i, x in enumerate(facts)}})
    w = europe.esef_statements(f, "X.MI", pd.Timestamp("2026-03-01")).pivot(
        index="period_end", columns="metric", values="value")
    return w.iloc[-1]


def test_italian_bank_schema():
    """Banca con lo schema della Banca d'Italia (Banco BPM): margine di intermediazione, costi operativi, rettifiche
    IFRS 9, patrimonio con voce propria, avviamento "di cui"; niente debito né FCF."""
    y = _statements([
        _ext_fact("NetInterestMargin", YEAR, 3.4e9), _fact("FeeAndCommissionIncomeExpense", YEAR, 2.4e9),
        _ext_fact("GrossIncome", YEAR, 6.2e9), _ext_fact("OperatingCosts", YEAR, -2.8e9),
        _fact("ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9", YEAR, 0.36e9),
        _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 2.1e9), _fact("Assets", END, 206e9),
        _fact("CashFlowsFromUsedInOperatingActivities", YEAR, -5e9),
        _fact("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", YEAR, 0.1e9),
        _ext_fact("TotalEquityAttributableToOwnersOfParent", END, 15.5e9),
        _fact("IntangibleAssetsAndGoodwill", END, 3.2e9), _ext_fact("DiCuiAvviamento", END, 1.0e9),
        _fact("DebtSecuritiesInIssue", END, 30e9), _fact("Borrowings", END, 40e9),
        _fact("InsuranceRevenue", YEAR, 0.2e9), _fact("InsuranceServiceExpensesFromInsuranceContractsIssued", YEAR, 0.1e9)])
    assert y["revenue"] == pytest.approx(6.2e9) and y["net_interest_income"] == pytest.approx(3.4e9)
    assert y["noninterest_expense"] == pytest.approx(2.8e9) and y["loan_provisions"] == pytest.approx(0.36e9)
    assert y["equity"] == pytest.approx(15.5e9) and y["goodwill"] == pytest.approx(1e9)
    assert y["intangibles"] == pytest.approx(2.2e9)
    assert "total_debt" not in y.index and "free_cash_flow" not in y.index      # banca: niente debito né FCF
    assert "premiums" not in y.index                     # la compagnia assicurativa della banca non fa un assicuratore
    assert y["operating_cash_flow"] == pytest.approx(-5e9)


def test_bank_revenue_from_components_and_not_bank():
    """Senza un totale: interessi netti + commissioni nette + dividendi (BBVA). Nexi ha interessi netti negativi:
    non è una banca, i ricavi vengono da "RevenueAndOperatingIncome" e il debito resta."""
    y = _statements([
        _fact("RevenueFromInterest", YEAR, 58e9), _fact("InterestExpense", YEAR, 32e9),
        _fact("FeeAndCommissionIncome", YEAR, 13.7e9), _fact("FeeAndCommissionExpense", YEAR, 5.5e9),
        _fact("RevenueFromDividends", YEAR, 0.1e9), _fact("AdministrativeExpense", YEAR, 12.8e9),
        _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 10.5e9)])
    assert y["net_interest_income"] == pytest.approx(26e9)
    assert y["revenue"] == pytest.approx(26e9 + 8.2e9 + 0.1e9) and y["noninterest_expense"] == pytest.approx(12.8e9)
    n = _statements([
        _fact("InterestRevenueExpense", YEAR, -0.26e9), _fact("FeeAndCommissionIncomeExpense", YEAR, 3.7e9),
        _fact("RevenueAndOperatingIncome", YEAR, 3.4e9), _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 0.17e9),
        _fact("Borrowings", END, 7e9)])
    assert n["revenue"] == pytest.approx(3.4e9) and n["total_debt"] == pytest.approx(7e9)
    assert "net_interest_income" not in n.index


def test_insurer_premiums_claims():
    y = _statements([
        _fact("InsuranceRevenue", YEAR, 56.4e9), _fact("InsuranceServiceExpensesFromInsuranceContractsIssued", YEAR, 48.4e9),
        _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 4.2e9),
        _ext_fact("CashFlowsFromUsedInOperatingActivities", YEAR, 19.7e9),
        _fact("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", YEAR, 0.4e9)])
    assert y["revenue"] == pytest.approx(56.4e9) and y["premiums"] == pytest.approx(56.4e9)
    assert y["claims"] == pytest.approx(48.4e9) and y["operating_cash_flow"] == pytest.approx(19.7e9)
    assert "free_cash_flow" not in y.index                                           # assicuratore: niente FCF
    small = _statements([_fact("Revenue", YEAR, 100e9), _fact("InsuranceRevenue", YEAR, 2e9),
                         _fact("InsuranceServiceExpensesFromInsuranceContractsIssued", YEAR, 1.5e9)])
    assert "premiums" not in small.index                          # assicurazione marginale: niente loss ratio


def test_debt_alternatives_and_leases():
    parts = _statements([_fact("NoncurrentPortionOfNoncurrentBondsIssued", END, 1.59e9),
                         _fact("NoncurrentPortionOfNoncurrentLoansReceived", END, 0.63e9),
                         _fact("CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived", END, 0.27e9),
                         _fact("NoncurrentLeaseLiabilities", END, 0.1e9), _fact("CurrentLeaseLiabilities", END, 0.05e9),
                         _fact("Revenue", YEAR, 3e9)])
    assert parts["total_debt"] == pytest.approx(2.49e9)                          # Campari: obbligazioni + prestiti
    assert parts["operating_leases"] == pytest.approx(0.15e9)                    # leasing a parte, non nel debito
    airbus = _statements([_ext_fact("LongtermFinancingLiabilities", END, 9.06e9),
                          _ext_fact("ShorttermFinancingLiabilities", END, 5.19e9),
                          _ext_fact("OtherFinancingLiabilities", END, 99e9), _fact("Revenue", YEAR, 70e9)])
    assert airbus["total_debt"] == pytest.approx(14.25e9)
    only_short = _statements([_ext_fact("CurrentInterestBearingFinancialLiabilities", END, 2.6e9),
                              _fact("Revenue", YEAR, 30e9)])
    assert "total_debt" not in only_short.index                   # solo la parte a breve con voce propria: non si sa
    no_debt = _statements([_fact("NoncurrentLeaseLiabilities", END, 36e6), _fact("Revenue", YEAR, 3e9)])
    assert no_debt["total_debt"] == 0                                             # argenx: nessun finanziamento
    with_loans = _statements([_fact("NoncurrentLeaseLiabilities", END, 0.6e9), _fact("Revenue", YEAR, 1.4e9),
                              _fact("ProceedsFromNoncurrentBorrowings", YEAR, 0.18e9)])
    assert "total_debt" not in with_loans.index                   # ha preso prestiti: debito sconosciuto, non zero


def test_shares_alternatives():
    eur_sh = "iso4217:EUR/xbrli:shares"
    out = _statements([_fact("NumberOfSharesOutstanding", END, 2.09e9, unit="xbrli:shares"),
                       _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 9.8e9),
                       _fact("BasicEarningsLossPerShare", YEAR, 4.53, unit=eur_sh)])
    assert out["shares"] == pytest.approx(2.09e9)                              # azioni in circolazione prima di utile/EPS
    wrong = _statements([_fact("NumberOfSharesOutstanding", END, 2.09e6, unit="xbrli:shares"),
                         _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 9.8e9),
                         _fact("BasicEarningsLossPerShare", YEAR, 4.9, unit=eur_sh)])
    assert wrong["shares"] == pytest.approx(2e9)                                # conteggio in scala sbagliata
    ext = _statements([_ext_fact("BasicEarningsLossPerShareOrdinary", YEAR, 5.0, unit=eur_sh),
                       _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 0.94e9)])
    assert ext["shares"] == pytest.approx(0.188e9)                               # Buzzi: utile per azione con voce propria
    cont = _statements([_fact("BasicEarningsLossPerShareFromContinuingOperations", YEAR, 0.32, unit=eur_sh),
                        _fact("ProfitLossAttributableToOwnersOfParent", YEAR, 0.464e9)])
    assert cont["shares"] == pytest.approx(1.45e9)
    loss = _statements([_fact("BasicEarningsLossPerShare", YEAR, -7.75, unit=eur_sh),
                        _fact("ProfitLossAttributableToOwnersOfParent", YEAR, -22.4e9)])
    assert loss["shares"] == pytest.approx(22.4e9 / 7.75)                         # Stellantis: perdita / perdita per azione
    issued = _statements([_fact("NumberOfSharesIssued", END, 1e9, unit="xbrli:shares"), _fact("Revenue", YEAR, 1e9)])
    assert issued["shares"] == 1e9                                               # ultima scelta: azioni emesse


def test_drop_share_outliers():
    rows = [("A.MI", pd.Timestamp(f"{y}-12-31"), "shares", v, pd.Timestamp(f"{y + 1}-03-01"), "esef")
            for y, v in [(2021, 1.3e9), (2022, 1.3e3), (2023, 1.3e9), (2024, 2.0e8)]]
    rows.append(("A.MI", pd.Timestamp("2022-12-31"), "revenue", 5.0, pd.Timestamp("2023-03-01"), "esef"))
    df = pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
    out = europe.drop_share_outliers(df)
    assert sorted(out.loc[out.metric == "shares", "value"]) == [2.0e8, 1.3e9, 1.3e9]   # raggruppamento 10:1 resta
    assert (out.metric == "revenue").any()


def test_cash_flow_alternatives():
    y = _statements([_fact("CashFlowsFromUsedInOperatingActivities", YEAR, 5.37e9),
                     _fact("PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAnd"
                           "OtherNoncurrentAssets", YEAR, 1.16e9), _fact("Revenue", YEAR, 15e9)])
    assert y["capex"] == pytest.approx(-1.16e9) and y["free_cash_flow"] == pytest.approx(4.21e9)   # Hermès
    vinci = _statements([_fact("CashFlowsFromUsedInOperatingActivities", YEAR, 11.9e9), _fact("Revenue", YEAR, 70e9),
                         _ext_fact("Purchaseofpropertyplantandequipmentandpurchaseofintangibleassetsclassifiedasinvesting"
                                   "activities", YEAR, 3.0e9),
                         _ext_fact("Proceedsfromsalesofpropertyplantandequipment", YEAR, 0.2e9)])
    assert vinci["capex"] == pytest.approx(-3.0e9)                                # voce propria in minuscolo
    air_liquide = _statements([_fact("CashFlowsFromUsedInOperatingActivities", YEAR, 6.3e9), _fact("Revenue", YEAR, 27e9),
                               _ext_fact("AcquisitionDimmobilisationsCorporellesEtIncorporellesAutresQueLeGoodwill", YEAR,
                                         3.5e9)])
    assert air_liquide["capex"] == pytest.approx(-3.5e9)
    italgas = _statements([_fact("CashFlowsFromUsedInOperations", YEAR, 1625e6),
                           _fact("IncomeTaxesPaidClassifiedAsOperatingActivities", YEAR, 264e6),
                           _fact("FinanceCostsPaidClassifiedAsOperatingActivities", YEAR, -181e6),
                           _fact("FinanceIncomeReceivedClassifiedAsOperatingActivities", YEAR, 6e6),
                           _ext_fact("CoreBusinessRevenue", YEAR, 3.5e9)])
    assert italgas["operating_cash_flow"] == pytest.approx((1625 - 264 - 181 + 6) * 1e6)  # prima di interessi e imposte
    assert italgas["revenue"] == pytest.approx(3.5e9)                               # ricavi con voce propria


def test_one_day_periods_and_sanity_checks():
    """Recordati etichetta il conto economico con il solo 31 dicembre: è l'esercizio. Ricavi sotto l'1% dell'utile
    (Brunello Cucinelli) sono un'etichetta sbagliata; un utile più grande dei ricavi (holding) no."""
    day, prev_day = "2025-12-31T00:00:00/2026-01-01T00:00:00", "2024-12-31T00:00:00/2025-01-01T00:00:00"
    rec = _statements([_fact("Revenue", day, 2.62e9), _fact("Revenue", prev_day, 2.34e9),
                       _fact("ProfitLoss", day, 0.44e9), _fact("CashFlowsFromUsedInOperatingActivities", day, 0.6e9)])
    assert rec["revenue"] == pytest.approx(2.62e9) and rec["net_income"] == pytest.approx(0.44e9)
    bc = _statements([_fact("Revenue", YEAR, 188e3), _fact("ProfitLoss", YEAR, 142e6)])
    assert "revenue" not in bc.index
    holding = _statements([_fact("Revenue", YEAR, 5.5e9), _fact("ProfitLoss", YEAR, 6.6e9)])
    assert holding["revenue"] == pytest.approx(5.5e9)
    # risultato operativo: voce propria (LVMH, prima quella non "ricorrente") o utile ante imposte + oneri finanziari
    lvmh = _statements([_ext_fact("ProfitLossFromOperatingActivitiesRecurringIncludingShareOfProfit", YEAR, 19.6e9),
                        _ext_fact("ProfitLossFromOperatingActivitiesAfterShareOfProfitLossOfAssociates", YEAR, 18.9e9),
                        _fact("Revenue", YEAR, 85e9)])
    assert lvmh["operating_income"] == pytest.approx(18.9e9)
    iveco = _statements([_fact("ProfitLossBeforeTax", YEAR, 316e6), _fact("FinanceCosts", YEAR, 337e6),
                         _fact("Revenue", YEAR, 14e9)])
    assert iveco["operating_income"] == pytest.approx(653e6)
