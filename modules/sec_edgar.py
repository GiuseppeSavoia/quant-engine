"""
Bilanci annuali dalla SEC (EDGAR, API XBRL "companyfacts"), fonte ufficiale e gratuita,
più i valori degli ultimi 12 mesi (TTM) a ogni trimestre, ricavati dai 10-Q (vedi `parse_ttm`).

  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Il file contiene ogni valore depositato dall'azienda nei suoi report, con:
  start/end  periodo a cui si riferisce il valore
  val        valore
  accn       numero del documento depositato (un 10-K, un 10-Q, ...)
  form       tipo di documento
  filed      data di deposito: il giorno in cui il dato è diventato pubblico

Come vengono letti:
- Solo documenti annuali (10-K e varianti). I campi fy/fp descrivono il DOCUMENTO e non il
  periodo, quindi un valore è "annuale" se il periodo dura 330-400 giorni.
- I valori patrimoniali (istantanei) si tengono solo alle ultime due chiusure d'esercizio del documento
  (quelle dello stato patrimoniale).
- Ogni metrica è calcolata DOCUMENTO PER DOCUMENTO: un 10-K riporta anche gli anni precedenti
  (eventualmente corretti), che diventano nuove versioni dello stesso periodo con la loro data
  di deposito. In un backtest, a ogni data si usa l'ultima versione già pubblicata.
- Le aziende usano etichette diverse per la stessa voce (e le cambiano negli anni):
  per ogni metrica c'è un elenco di etichette in ordine di preferenza.

Errori di chi deposita: alcune aziende dichiarano il numero di azioni in milioni o migliaia invece
che in unità (es. McDonald's dal 2024: "716,4" invece di 716.400.000), e a volte l'errore è sulla
copertina (es. Packaging Corp nel 10-K 2026: 89 miliardi di azioni). Il riferimento è quindi la
MEDIANA delle azioni di copertina (dei:EntityCommonStockSharesOutstanding) di tutti i documenti,
10-Q compresi, depositati entro un anno: se le azioni di un documento ne differiscono di circa
1.000 o 1.000.000 di volte, vengono riportate in unità.

Spese per investimenti (capex): se manca la voce complessiva si sommano le componenti dichiarate
(es. EOG: impianti petroliferi + altri impianti). Alcune aziende usano solo voci proprie
(estensioni XBRL, non presenti in companyfacts): per loro il free cash flow resta mancante.

Approssimazioni: il debito totale è ricostruito dalle sue componenti (escluso il leasing
operativo); le banche e le assicurazioni hanno schemi di bilancio diversi e per loro il DCF
sul free cash flow non è significativo.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# etichette (concept) per metrica, in ordine di preferenza
# ricavi: si prende la voce PIÙ GRANDE tra quelle presenti (è la prima riga del conto economico). In ordine di
# preferenza alcune aziende hanno "Revenues" quasi a zero per una voce minore (es. Eaton, Flowserve) e i REIT
# hanno ricavi da contratti piccoli rispetto agli affitti
REVENUE = ["Revenues", "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
           "RealEstateRevenueNet", "OperatingLeaseLeaseIncome"]
NET_INCOME = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
OPERATING_CF = ["NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
         "PaymentsForCapitalImprovements"]
# componenti del capex, sommate solo se manca una voce complessiva
CAPEX_PARTS = ["PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsToAcquireOilAndGasPropertyAndEquipment",
               "PaymentsToAcquireOtherProductiveAssets"]  # es. Verizon dal 2021
# software sviluppato o comprato, voce separata dal capex in molte aziende (es. Roper, Zimmer, Henry Schein): è un
# investimento come gli impianti e va sottratto dal free cash flow (come fanno Yahoo e gli analisti)
CAPEX_SOFTWARE = ["PaymentsToDevelopSoftware", "PaymentsForSoftware"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
TAX = ["IncomeTaxExpenseBenefit"]
# utile ante imposte diviso tra Stati Uniti ed estero (nota sulle imposte): la somma è il totale. Alcune aziende solo
# americane (es. PulteGroup, NVR, Camden) usano "Domestic" come riga del conto economico, senza un totale
PRETAX_PARTS = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign"]
# utile dopo le imposte delle attività in funzionamento (con le minoranze, poi della capogruppo) e risultato delle
# attività cessate: per ricostruire l'utile ante imposte di chi non lo etichetta (Phillips 66, Welltower nei 10-K,
# Casey's nei 10-K)
AFTER_TAX_CONTINUING = ["IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
                        "IncomeLossFromContinuingOperations"]
DISCONTINUED = ["IncomeLossFromDiscontinuedOperationsNetOfTax",
                "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"]
# interessi passivi a conto economico
INTEREST = ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]
# altre righe degli interessi LORDI, per chi non usa le prime: "Interest and debt expense" (ConocoPhillips, DuPont,
# Phillips 66, Occidental), interessi tra i costi operativi (REIT come Realty Income e Invitation Homes, Coherent),
# "interessi sui prestiti" (Welltower). Livello a parte (vedi LEVELS): chi ha già una delle prime non cambia, nemmeno
# negli ultimi 12 mesi (Medtronic: InterestExpense = oneri netti nel 10-K, InterestExpenseBorrowings = lordi)
INTEREST_MORE = ["InterestAndDebtExpense", "InterestExpenseOperating", "InterestExpenseBorrowings"]
# interessi NETTI (proventi meno oneri, positivi = proventi): molte aziende dichiarano solo questi (Baker Hughes,
# Textron, Newmont, Nike, TJX, Genuine Parts, Emerson). Servono solo all'EBIT approssimato, MAI alla copertura degli
# interessi (che vuole gli oneri lordi). "InterestIncomeExpenseNet" è anche il margine di interesse delle banche:
# per chi ha le voci bancarie (NONINTEREST_*) non si usa
INTEREST_NET = ["InterestIncomeExpenseNonoperatingNet", "InterestRevenueExpenseNet", "InterestIncomeExpenseNet"]
# interessi PAGATI (informativa del rendiconto finanziario): ultima scelta per gli oneri quando il conto economico non
# li riporta con un'etichetta standard (es. GE usa una voce propria, assente da companyfacts). Sugli esercizi 2023-26
# dell'S&P 500 con entrambe le voci: pagati / oneri mediana 0,96, l'82% entro il ±20%
INTEREST_PAID = ["InterestPaidNet", "InterestPaid"]
DILUTED_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]
# per i fattori di qualità: margine lordo (o ricavi - costo del venduto) e attivo totale
GROSS_PROFIT = ["GrossProfit"]
COST_OF_REVENUE = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
ASSETS = ["Assets"]
# per valutazioni e punteggi di qualità (EV/EBIT, ROIC, Piotroski, Altman) e per la remunerazione degli azionisti
OPERATING_INCOME = ["OperatingIncomeLoss"]
DEPRECIATION = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet", "Depreciation"]
# etichette del TOTALE degli ammortamenti (l'ultima di DEPRECIATION, "Depreciation", esclude le amortization). Alcune
# aziende usano la prima per una parte sola (MCD: 457 mln contro 2.199 del rendiconto finanziario; VLO 63 contro
# 3.158; CRM 1.200 contro 3.631): se un'altra etichetta di totale supera la prima di oltre il 15% vale la più grande.
# Mai sommate: sono modi diversi di dire lo stesso totale. Verifica del 2026-11-16 (archivio SEC, tutte le 622
# aziende; Yahoo su 41): cambiano 879 valori di 47 aziende; dei 55 confrontabili con Yahoo 54 diventano uguali
# (prima 0), nessuno peggiora. Con il 25% restava UPS (3.000 "circa" contro 3.746: +24,9%); "Depreciation" tra i
# candidati peggiorava PNC, WRB, AXON, ES. Restano sbagliate le aziende con il totale sotto un'etichetta propria
# (GE, FISV, KDP, INTC, CINF, BEN...).
DEPRECIATION_TOTALS = DEPRECIATION[:3]
DEPRECIATION_GAP = 1.15
DIVIDENDS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfOrdinaryDividends",
             "DividendsCommonStockCash"]  # l'ultima: dividendi dichiarati (prospetto del patrimonio netto)
BUYBACKS = ["PaymentsForRepurchaseOfCommonStock"]
CURRENT_ASSETS = ["AssetsCurrent"]
CURRENT_LIABILITIES = ["LiabilitiesCurrent"]
LIABILITIES = ["Liabilities"]
RETAINED_EARNINGS = ["RetainedEarningsAccumulatedDeficit"]
# metriche di settore: banche (margine di interesse, costi, accantonamenti), assicurazioni (premi, sinistri), REIT
# (plusvalenze sugli immobili, patrimonio immobiliare), avviamento e intangibili per il patrimonio tangibile
NET_INTEREST = ["InterestIncomeExpenseNet"]
NONINTEREST_INCOME = ["NoninterestIncome"]
NONINTEREST_EXPENSE = ["NoninterestExpense"]
LOAN_PROVISIONS = ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses", "ProvisionForCreditLosses"]
PREMIUMS = ["PremiumsEarnedNet"]
CLAIMS = ["PolicyholderBenefitsAndClaimsIncurredNet", "IncurredClaimsPropertyCasualtyAndLiability"]
REAL_ESTATE_GAINS = ["GainsLossesOnSalesOfInvestmentRealEstate", "GainLossOnSaleOfRealEstateInvestmentProperty",
                     "GainLossOnSaleOfProperties"]
GOODWILL = ["Goodwill"]
INTANGIBLES = ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"]
REAL_ESTATE_ASSETS = ["RealEstateInvestmentPropertyNet", "RealEstateGrossAtCarryingValue",
                      "RealEstateInvestmentPropertyAtCost"]
# qualità degli utili e correzioni di Damodaran (R&S come investimento, leasing come debito)
STOCK_COMP = ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]
RND = ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]
SGA = ["SellingGeneralAndAdministrativeExpense"]
RECEIVABLES = ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]
PPE_NET = ["PropertyPlantAndEquipmentNet",
           "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"]
OPERATING_LEASES = ["OperatingLeaseLiability"]
EQUITY = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash"]
SHORT_INVEST = ["MarketableSecuritiesCurrent", "ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                # voce generica (es. NVIDIA, tutta a breve): accettata solo se cassa + titoli <= attività correnti
                "AvailableForSaleSecuritiesDebtSecurities"]
# debito dichiarato con voci "di strumento" invece che di bilancio (es. Realty Income: NotesPayable 25 mld; Pulte:
# SeniorNotes; CME: UnsecuredLongTermDebt): la prima presente, più debito garantito e linee di credito
DEBT_NOTES = ["NotesPayable", "SeniorNotes", "UnsecuredLongTermDebt", "UnsecuredDebt", "LongTermNotesPayable",
              "SeniorLongTermNotes", "ConvertibleNotesPayable",
              "DebtInstrumentCarryingAmount"]  # somma degli strumenti dalle note: ultima scelta
DEBT_NOTES_EXTRA = ["SecuredDebt", "LongTermLineOfCredit", "LineOfCredit"]
ALT_DEBT_MIN_RATIO = 1.25  # le voci di strumento sostituiscono il bilancio solo se lo superano di almeno il 25%
DEBT_EXTRA_LABELS = ["DebtAndCapitalLeaseObligations", "NotesPayableCurrent", "OtherLongTermDebtCurrent"]
DEBT_PARTS = ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent",
              "ShortTermBorrowings", "CommercialPaper", "LongTermDebtAndCapitalLeaseObligations",
              "LongTermDebtAndCapitalLeaseObligationsCurrent", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
              "DebtLongtermAndShorttermCombinedAmount"]

DURATION_CONCEPTS = (REVENUE + NET_INCOME + OPERATING_CF + CAPEX + CAPEX_PARTS + CAPEX_SOFTWARE + PRETAX + PRETAX_PARTS
                     + TAX + AFTER_TAX_CONTINUING + DISCONTINUED + INTEREST + INTEREST_MORE + INTEREST_PAID
                     + [c for c in INTEREST_NET if c not in NET_INTEREST]
                     + GROSS_PROFIT + COST_OF_REVENUE + OPERATING_INCOME + DEPRECIATION + DIVIDENDS + BUYBACKS
                     + STOCK_COMP + RND + SGA + DILUTED_SHARES + NET_INTEREST + NONINTEREST_INCOME + NONINTEREST_EXPENSE
                     + LOAN_PROVISIONS + PREMIUMS + CLAIMS + REAL_ESTATE_GAINS)
# voci che servono solo a riempire buchi (utile ante imposte, interessi): non decidono le date dei periodi di un
# documento (gli interessi pagati, informativa supplementare, a volte hanno date sbagliate: un 10-Q di Expand
# Energy/Chesapeake del 2012 con il trimestre successivo faceva sparire gli ultimi 12 mesi)
NOT_FOR_DATES = set(PRETAX_PARTS + AFTER_TAX_CONTINUING + DISCONTINUED + INTEREST_MORE + INTEREST_PAID + INTEREST_NET) \
    - set(NET_INTEREST)
INSTANT_CONCEPTS = (EQUITY + CASH + SHORT_INVEST + DEBT_PARTS + DEBT_NOTES + DEBT_NOTES_EXTRA + DEBT_EXTRA_LABELS
                    + GOODWILL + INTANGIBLES + REAL_ESTATE_ASSETS + ASSETS + CURRENT_ASSETS + CURRENT_LIABILITIES
                    + LIABILITIES + RETAINED_EARNINGS + RECEIVABLES + PPE_NET + OPERATING_LEASES)
DEI_SHARES = "EntityCommonStockSharesOutstanding"

MIN_DAYS, MAX_DAYS = 330, 400


def _facts(payload: dict, taxonomy: str, concept: str, unit: str) -> list[dict]:
    try:
        return payload["facts"][taxonomy][concept]["units"][unit]
    except (KeyError, TypeError):
        return []


def _annual_form(form: str | None) -> bool:
    return bool(form) and str(form).upper().startswith("10-K")


def extract_annual_facts(payload: dict) -> pd.DataFrame:
    """Tutti i valori annuali utili, in formato lungo: concept, period_end, accn, filed, value."""
    records = []
    for concept in DURATION_CONCEPTS:
        unit = "shares" if concept in DILUTED_SHARES else "USD"
        for f in _facts(payload, "us-gaap", concept, unit):
            if not _annual_form(f.get("form")) or "start" not in f or "end" not in f:
                continue
            days = (pd.Timestamp(f["end"]) - pd.Timestamp(f["start"])).days
            if MIN_DAYS <= days <= MAX_DAYS:
                records.append((concept, f["end"], f["accn"], f["filed"], f["val"], "duration"))
    for concept in INSTANT_CONCEPTS:
        for f in _facts(payload, "us-gaap", concept, "USD"):
            if _annual_form(f.get("form")) and "start" not in f and "end" in f:
                records.append((concept, f["end"], f["accn"], f["filed"], f["val"], "instant"))
    for f in _facts(payload, "dei", DEI_SHARES, "shares"):
        if "end" not in f:
            continue
        if _annual_form(f.get("form")):
            records.append((DEI_SHARES, f["end"], f["accn"], f["filed"], f["val"], "cover"))
        # riferimento per gli errori di unità: copertine di tutti i documenti (anche trimestrali)
        records.append((DEI_SHARES, f["end"], f["accn"], f["filed"], f["val"], "cover_ref"))
    df = pd.DataFrame(records, columns=["concept", "period_end", "accn", "filed", "value", "kind"])
    if df.empty:
        return df
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


def _first(row: pd.Series, concepts: list[str]) -> float:
    for c in concepts:
        v = row.get(c, np.nan)
        if pd.notna(v):
            return float(v)
    return np.nan


def _depreciation(row: pd.Series) -> float:
    """Ammortamenti: la prima etichetta di totale (DEPRECIATION_TOTALS), o la più grande se la supera di oltre il
    15% (DEPRECIATION_GAP: la prima era solo una parte); senza etichette di totale, la sola "Depreciation"."""
    values = [float(row.get(c)) for c in DEPRECIATION_TOTALS if pd.notna(row.get(c, np.nan))]
    if not values:
        return _first(row, DEPRECIATION[3:])
    big = max(values)
    return big if big > DEPRECIATION_GAP * values[0] else values[0]


def _nz(row: pd.Series, concept: str) -> float:
    v = row.get(concept, np.nan)
    return float(v) if pd.notna(v) else 0.0


def _revenue(row: pd.Series) -> float:
    values = [row.get(c, np.nan) for c in REVENUE]
    values = [float(v) for v in values if pd.notna(v)]
    # banche: ricavi = margine di interesse + altri ricavi (se non c'è RevenuesNetOfInterestExpense)
    nii, other = _first(row, NET_INTEREST), _first(row, NONINTEREST_INCOME)
    if pd.notna(nii) and pd.notna(other):
        values.append(nii + other)
    return max(values) if values else np.nan


def _gross_profit(row: pd.Series) -> float:
    gp = _first(row, GROSS_PROFIT)
    if pd.notna(gp):
        return gp
    revenue, cost = _revenue(row), _first(row, COST_OF_REVENUE)
    return revenue - cost if pd.notna(revenue) and pd.notna(cost) else np.nan


def _capex(row: pd.Series) -> float:
    """Investimenti in impianti (voce complessiva, o somma delle componenti) più il software capitalizzato."""
    total = _first(row, CAPEX)
    if pd.isna(total):
        parts = [row.get(c, np.nan) for c in CAPEX_PARTS]
        total = float(np.nansum(np.abs(parts))) if any(pd.notna(v) for v in parts) else np.nan
    software = [row.get(c, np.nan) for c in CAPEX_SOFTWARE]
    if any(pd.notna(v) for v in software):
        total = (abs(total) if pd.notna(total) else 0.0) + float(np.nansum(np.abs(software)))
    return total


def _share_scale(value: float, reference: float) -> float:
    """Fattore (1, 1e3, 1e6 o inversi) che riporta `value` all'ordine di grandezza di `reference`
    quando la differenza è quasi esattamente un multiplo di 1.000 (errore di unità), altrimenti 1."""
    if not (np.isfinite(value) and np.isfinite(reference)) or value <= 0 or reference <= 0:
        return 1.0
    gap = np.log10(reference / value)
    k = int(round(gap / 3))
    if k == 0 or abs(gap - 3 * k) > np.log10(3):
        return 1.0
    return 10.0 ** (3 * k)


def _cover_reference(facts: pd.DataFrame) -> pd.DataFrame:
    """Azioni di copertina per documento, 10-Q compresi (somma delle classi): accn, filed, value."""
    cover = facts[facts["kind"] == "cover_ref"]
    if cover.empty:
        return pd.DataFrame(columns=["accn", "filed", "value"])
    # stessa data di copertina per tutte le classi: si sommano quelle dell'ultima data del documento
    last = cover.groupby("accn")["period_end"].transform("max")
    cover = cover[cover["period_end"] == last]
    return cover.groupby("accn", as_index=False).agg(filed=("filed", "min"), value=("value", "sum"))


COVER_WINDOW = pd.Timedelta(days=400)


def _scale_for_document(shares: float, filed, ref: pd.DataFrame) -> float:
    """Confronta con la mediana delle copertine depositate entro un anno (resiste a una copertina sbagliata)."""
    if ref.empty:
        return 1.0
    near = ref[(ref["filed"] - pd.Timestamp(filed)).abs() <= COVER_WINDOW]
    if near.empty:
        return 1.0
    return _share_scale(shares, float(near["value"].median()))


# debito a lungo termine come riga dello stato patrimoniale (esclusa la quota entro l'anno): presente sia nei
# 10-K sia nei 10-Q, quindi trimestri ed esercizi restano confrontabili
DEBT_NONCURRENT = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]
DEBT_CURRENT_LTD = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]
# totali (lungo termine con la quota corrente), di solito nelle note: usati solo senza la riga di bilancio
DEBT_TOTALS = ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
               "DebtAndCapitalLeaseObligations"]  # es. Air Products
# voci della sola parte a breve: se un 10-Q ha solo queste, il debito a lungo è dichiarato con voci proprie
DEBT_SHORT_ONLY = ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper",
                   "LongTermDebtAndCapitalLeaseObligationsCurrent", "NotesPayableCurrent", "OtherLongTermDebtCurrent"]


def _balance_sheet_debt(row: pd.Series) -> float:
    """Debito finanziario totale dalle voci di bilancio (escluso il leasing operativo).
    1. riga a lungo termine del bilancio + debito a breve: `DebtCurrent` se c'è (comprende già la quota corrente
       del lungo termine), altrimenti quota corrente + prestiti a breve + commercial paper;
    2. altrimenti un totale dalle note + prestiti a breve + commercial paper;
    3. altrimenti un totale complessivo dichiarato, o la somma delle sole voci a breve."""
    has = lambda c: pd.notna(row.get(c, np.nan))  # noqa: E731
    short = _nz(row, "ShortTermBorrowings") + _nz(row, "CommercialPaper")
    noncurrent = _first(row, DEBT_NONCURRENT)
    if pd.notna(noncurrent):
        if has("DebtCurrent"):
            return noncurrent + _nz(row, "DebtCurrent")
        return noncurrent + np.nan_to_num(_first(row, DEBT_CURRENT_LTD)) + short
    total = _first(row, DEBT_TOTALS)
    if pd.notna(total):
        return total + short
    if has("DebtLongtermAndShorttermCombinedAmount"):
        return _nz(row, "DebtLongtermAndShorttermCombinedAmount")
    parts = ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]
    if not has("DebtCurrent") and not has("LongTermDebtCurrent"):
        parts.append("LongTermDebtAndCapitalLeaseObligationsCurrent")
    return sum(_nz(row, c) for c in parts) if any(has(c) for c in parts) else np.nan


def _total_debt(row: pd.Series, quarterly: bool = False) -> float:
    """Debito dalle voci di bilancio (`_balance_sheet_debt`) o, se più grande, dalle voci "di strumento" (note,
    obbligazioni senior, debito non garantito): alcune aziende dichiarano il debito solo così, e le voci di bilancio
    darebbero quasi zero. Il più grande dei due, non la somma: le due strade descrivono lo stesso debito.
    `quarterly`: in un 10-Q con le sole voci a breve (es. Baxter, Con Edison: il lungo termine ha etichette proprie)
    il debito è sconosciuto, non quasi zero: vuoto, e vale l'ultimo bilancio annuale."""
    standard = _balance_sheet_debt(row)
    notes = _first(row, DEBT_NOTES)
    long_labels = DEBT_NONCURRENT + DEBT_TOTALS + DEBT_NOTES + ["DebtLongtermAndShorttermCombinedAmount"]
    if quarterly and pd.isna(notes) and not any(pd.notna(row.get(c, np.nan)) for c in long_labels):
        return np.nan
    if pd.isna(notes):
        return standard
    alt = notes + sum(_nz(row, c) for c in DEBT_NOTES_EXTRA) + _nz(row, "ShortTermBorrowings") + _nz(row, "CommercialPaper")
    if pd.isna(standard):
        return alt
    # solo se il bilancio è chiaramente incompleto: per piccole differenze (es. Walmart, +2%) le voci di strumento
    # contano due volte i prestiti a breve
    return alt if alt > ALT_DEBT_MIN_RATIO * max(standard, 0.0) else standard


FLOW_METRICS = ["revenue", "net_income", "operating_cash_flow", "capex", "free_cash_flow", "pretax_income",
                "tax_provision", "interest_expense", "gross_profit", "operating_income", "depreciation",
                "dividends_paid", "buybacks", "stock_compensation", "rnd", "sga", "net_interest_income",
                "noninterest_expense", "loan_provisions", "premiums", "claims", "real_estate_gains"]


# livelli delle voci di APPROX_METRICS (EBIT, interessi passivi, utile ante imposte): negli ultimi 12 mesi si prova
# un livello alla volta (in comune ai tre periodi, poi periodo per periodo) e il successivo solo per ciò che resta vuoto.
# 0 = le etichette di sempre; 1 = altre etichette esatte (INTEREST_MORE, Stati Uniti + estero); 2 = approssimazioni
EXACT, MORE_LABELS, APPROX = 0, 1, 2
LEVELS = (EXACT, MORE_LABELS, APPROX)
APPROX_METRICS = ("operating_income", "interest_expense", "pretax_income")


def _pretax(r: pd.Series, level: int = APPROX, present: set | None = None) -> float:
    """Utile prima delle imposte: il totale dichiarato; dal livello 1 Stati Uniti + estero (o i soli Stati Uniti se
    l'estero non c'è: aziende solo americane che usano quella riga nel conto economico); al livello APPROX utile
    dopo le imposte delle attività in funzionamento + imposte (l'identità del conto economico), solo se le imposte
    sono dichiarate; con l'utile netto solo se non ci sono attività cessate in nessuno dei periodi."""
    pretax = _first(r, PRETAX)
    if pd.notna(pretax) or level < MORE_LABELS:
        return pretax
    domestic, foreign = (r.get(c, np.nan) for c in PRETAX_PARTS)
    if pd.notna(domestic):
        return float(domestic) + (float(foreign) if pd.notna(foreign) else 0.0)
    tax = _first(r, TAX)
    if level < APPROX or pd.isna(tax):
        return np.nan
    after_tax = _first(r, AFTER_TAX_CONTINUING)
    if pd.isna(after_tax):
        present = set(r.index[r.notna()]) if present is None else present
        if any(c in present for c in DISCONTINUED):
            return np.nan
        after_tax = _first(r, PASS_THROUGH_PROFIT)
    return after_tax + tax if pd.notna(after_tax) else np.nan


def _gross_interest(r: pd.Series, level: int = APPROX) -> float:
    return _first(r, INTEREST + INTEREST_MORE if level >= MORE_LABELS else INTEREST)


# profitto da cui ripartire per chi non dichiara né utile ante imposte né imposte (entità che non pagano imposte sul
# reddito: REIT come Invitation Homes, BXP, Alexandria). ProfitLoss comprende le minoranze, come gli interessi
PASS_THROUGH_PROFIT = ["ProfitLoss", "NetIncomeLoss"]


def _operating_income(r: pd.Series, level: int = APPROX, present: set | None = None) -> float:
    """Utile operativo (EBIT). In ordine:
    1. la riga dichiarata (OperatingIncomeLoss);
    2. se manca (molte aziende energetiche, REIT, alcune industriali: XOM, COP, SLB, SHW, O...) utile prima delle
       imposte + interessi passivi lordi (dal livello 1 anche con INTEREST_MORE e Stati Uniti + estero).
    Al livello APPROX (solo se le strade esatte non bastano):
    3. utile prima delle imposte - interessi NETTI (proventi meno oneri: l'EBIT esclude anche i proventi finanziari),
       non per le banche (per loro quella voce è il margine di interesse);
    4. utile prima delle imposte + interessi pagati (rendiconto finanziario);
    5. chi non dichiara né utile ante imposte né imposte (REIT): utile netto + interessi (lordi, poi pagati).
    `present`: etichette presenti in almeno uno dei periodi combinati (ultimi 12 mesi); default: quelle di `r`."""
    ebit = _first(r, OPERATING_INCOME)
    if pd.notna(ebit):
        return ebit
    pretax, interest = _pretax(r, level, present), _gross_interest(r, level)
    if pd.notna(pretax) and pd.notna(interest):
        return pretax + abs(interest)
    if level < APPROX:
        return np.nan
    present = set(r.index[r.notna()]) if present is None else present
    paid = _first(r, INTEREST_PAID)
    if pd.notna(pretax):
        net = _first(r, INTEREST_NET)
        bank = any(c in present for c in NONINTEREST_INCOME + NONINTEREST_EXPENSE)
        if pd.notna(net) and not bank:
            return pretax - net
        return pretax + abs(paid) if pd.notna(paid) else np.nan
    taxed = any(c in present for c in TAX + PRETAX + PRETAX_PARTS)
    profit, gross = _first(r, PASS_THROUGH_PROFIT), interest if pd.notna(interest) else paid
    if not taxed and pd.notna(profit) and pd.notna(gross):
        return profit + abs(gross)
    return np.nan


def _interest_expense(r: pd.Series, level: int = APPROX) -> float:
    """Interessi passivi lordi dal conto economico; al livello APPROX, se mancano, gli interessi pagati."""
    interest = _gross_interest(r, level)
    if pd.isna(interest) and level >= APPROX:
        paid = _first(r, INTEREST_PAID)
        return abs(paid) if pd.notna(paid) else np.nan
    return interest


def _flow_metrics(r: pd.Series, level: int = APPROX, present: set | None = None) -> dict[str, float]:
    """Metriche di periodo (conto economico e flussi di cassa) dai valori di un documento.
    `level` (vedi LEVELS) vale solo per le voci di APPROX_METRICS; in un documento singolo l'ordine delle etichette
    basta (default: tutti i livelli), negli ultimi 12 mesi si sale di livello solo per ciò che resta vuoto."""
    ocf, capex = _first(r, OPERATING_CF), _capex(r)
    return {
        "revenue": _revenue(r),
        "net_income": _first(r, NET_INCOME),
        "operating_cash_flow": ocf,
        "capex": -abs(capex) if pd.notna(capex) else np.nan,  # negativo, come in yfinance
        "free_cash_flow": ocf - abs(capex) if pd.notna(ocf) and pd.notna(capex) else np.nan,
        "pretax_income": _pretax(r, level, present),
        "tax_provision": _first(r, TAX),
        "interest_expense": _interest_expense(r, level),
        "gross_profit": _gross_profit(r),
        "operating_income": _operating_income(r, level, present),
        "depreciation": _depreciation(r),
        # uscite di cassa verso gli azionisti, positive (alcune aziende le dichiarano con il segno)
        "dividends_paid": abs(_first(r, DIVIDENDS)),
        "buybacks": abs(_first(r, BUYBACKS)),
        "stock_compensation": abs(_first(r, STOCK_COMP)),
        "rnd": abs(_first(r, RND)),
        "sga": abs(_first(r, SGA)),
        "net_interest_income": _first(r, NET_INTEREST),
        "noninterest_expense": _first(r, NONINTEREST_EXPENSE),
        "loan_provisions": _first(r, LOAN_PROVISIONS),
        "premiums": _first(r, PREMIUMS),
        "claims": _first(r, CLAIMS),
        "real_estate_gains": _first(r, REAL_ESTATE_GAINS),
    }


def _balance_metrics(r: pd.Series, quarterly: bool = False) -> dict[str, float]:
    """Metriche patrimoniali (istantanee)."""
    cash = _first(r, CASH)
    short = _first(r, SHORT_INVEST)
    current = _first(r, CURRENT_ASSETS)
    if pd.notna(short) and pd.notna(cash) and pd.notna(current) and cash + short > current * 1.001:
        short = np.nan  # titoli in buona parte a lungo termine (voce generica): non sono liquidità
    return {
        "equity": _first(r, EQUITY),
        "total_assets": _first(r, ASSETS),
        "cash": cash + np.nan_to_num(short) if pd.notna(cash) else np.nan,
        "total_debt": _total_debt(r, quarterly),
        "current_assets": _first(r, CURRENT_ASSETS),
        "current_liabilities": _first(r, CURRENT_LIABILITIES),
        "total_liabilities": _first(r, LIABILITIES),
        "retained_earnings": _first(r, RETAINED_EARNINGS),
        "receivables": _first(r, RECEIVABLES),
        "ppe_net": _first(r, PPE_NET),
        "operating_leases": _first(r, OPERATING_LEASES),
        "goodwill": _first(r, GOODWILL),
        "intangibles": _first(r, INTANGIBLES),
        "real_estate_assets": _first(r, REAL_ESTATE_ASSETS),
    }


def parse_companyfacts(payload: dict, ticker: str, facts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Converte il JSON companyfacts nelle metriche normalizzate del sistema
    (ticker, period_end, metric, value, filed, source). `facts`: già estratti con extract_annual_facts."""
    facts = extract_annual_facts(payload) if facts is None else facts
    out_cols = ["ticker", "period_end", "metric", "value", "filed", "source"]
    if facts.empty:
        return pd.DataFrame(columns=out_cols)

    durations = facts[facts["kind"] == "duration"]
    # chiusure d'esercizio presenti in ciascun documento (anno corrente + anni di confronto)
    # chiusure dello stato patrimoniale di ciascun documento: le ultime due (anno corrente e precedente).
    # Le date più vecchie compaiono solo nel prospetto del patrimonio netto, a volte con etichette
    # sbagliate (es. Agilent, 10-K 2025: patrimonio 2023 = -327 milioni)
    dated = durations[~durations["concept"].isin(NOT_FOR_DATES)]  # date dei periodi dalle voci principali
    fy_ends = dated.groupby("accn")["period_end"].agg(lambda s: set(sorted(set(s))[-2:]))
    current_end = dated.groupby("accn")["period_end"].max()

    keep = facts["kind"] == "duration"
    inst = facts["kind"] == "instant"
    at_fy_end = np.array([pe in fy_ends.get(a, set()) for a, pe in zip(facts["accn"], facts["period_end"])], dtype=bool)
    keep |= inst & at_fy_end
    data = facts[keep].copy()

    # azioni dalla copertina del 10-K (data diversa dalla chiusura): assegnate all'esercizio del documento
    cover = facts[facts["kind"] == "cover"].copy()
    if not cover.empty:
        cover["period_end"] = cover["accn"].map(current_end)
        cover = cover.dropna(subset=["period_end"])
        cover = cover.groupby(["accn", "period_end", "filed", "concept"], as_index=False)["value"].sum()
        data = pd.concat([data, cover], ignore_index=True)

    filed_by_accn = facts.groupby("accn")["filed"].min()
    wide = data.pivot_table(index=["period_end", "accn"], columns="concept", values="value", aggfunc="last")
    reference = _cover_reference(facts)
    # un fattore per documento, deciso sull'esercizio corrente: lo stesso errore vale per gli anni di confronto
    doc_scale = {}
    for (period_end, accn), r in wide.iterrows():
        shares = _first(r, DILUTED_SHARES + [DEI_SHARES])
        if period_end == current_end.get(accn) and pd.notna(shares):
            doc_scale[accn] = _scale_for_document(shares, filed_by_accn[accn], reference)

    rows = []
    for (period_end, accn), r in wide.iterrows():
        metrics = {**_flow_metrics(r), **_balance_metrics(r), "shares": _first(r, DILUTED_SHARES + [DEI_SHARES])}
        if pd.notna(metrics["shares"]):
            scale = doc_scale.get(accn)
            if scale is None:  # documento senza dati dell'esercizio corrente: confronto diretto
                scale = _scale_for_document(metrics["shares"], filed_by_accn[accn], reference)
            if scale != 1.0:
                logger.debug("Azioni di %s nel documento %s in scala sbagliata: x%g", ticker, accn, scale)
                metrics["shares"] *= scale
        filed = filed_by_accn[accn]
        for metric, value in metrics.items():
            if pd.notna(value):
                rows.append((ticker, period_end, metric, float(value), filed, "sec"))

    out = pd.DataFrame(rows, columns=out_cols)
    return out.drop_duplicates(["ticker", "period_end", "metric", "filed"], keep="last").reset_index(drop=True)


# ------------------------------------------------------------------ ultimi 12 mesi (TTM) dai 10-Q
TTM_SOURCE = "sec_ttm"
NO_MIXED_LABELS = {"revenue", "gross_profit"}  # negli ultimi 12 mesi solo con la stessa etichetta in tutti i periodi
YTD_MAX_DAYS = 300                    # un 10-Q copre al massimo 9 mesi dall'inizio dell'esercizio
QUARTER_DAYS = (80, 100)              # durata di un trimestre
TOLERANCE = pd.Timedelta(days=10)     # esercizi da 52/53 settimane: le date si spostano di qualche giorno


def _quarterly_form(form: str | None) -> bool:
    return bool(form) and str(form).upper().startswith("10-Q")


def extract_quarterly_facts(payload: dict) -> pd.DataFrame:
    """Valori dei 10-Q in formato lungo: concept, start, end, accn, filed, value, kind."""
    records = []
    for concept in DURATION_CONCEPTS:
        unit = "shares" if concept in DILUTED_SHARES else "USD"
        for f in _facts(payload, "us-gaap", concept, unit):
            if _quarterly_form(f.get("form")) and "start" in f and "end" in f:
                records.append((concept, f["start"], f["end"], f["accn"], f["filed"], f["val"], "duration"))
    for concept in INSTANT_CONCEPTS:
        for f in _facts(payload, "us-gaap", concept, "USD"):
            if _quarterly_form(f.get("form")) and "start" not in f and "end" in f:
                records.append((concept, None, f["end"], f["accn"], f["filed"], f["val"], "instant"))
    for f in _facts(payload, "dei", DEI_SHARES, "shares"):
        if "end" in f:  # tutte le copertine: riferimento per gli errori di unità delle azioni
            records.append((DEI_SHARES, None, f["end"], f["accn"], f["filed"], f["val"], "cover_ref"))
    df = pd.DataFrame(records, columns=["concept", "start", "end", "accn", "filed", "value", "kind"])
    if df.empty:
        return df
    for col in ("start", "end", "filed"):
        df[col] = pd.to_datetime(df[col])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


def parse_ttm(payload: dict, ticker: str, annual_facts: pd.DataFrame) -> pd.DataFrame:
    """Valori degli ultimi 12 mesi alla chiusura di ogni trimestre, dai 10-Q.

    Un 10-Q riporta i flussi "da inizio esercizio" (YTD) dell'anno in corso e dello stesso periodo
    dell'anno prima. Ultimi 12 mesi = YTD di quest'anno + ultimo esercizio completo - YTD dell'anno prima.
    L'esercizio completo è l'ultima versione già depositata alla data del 10-Q (point-in-time).
    Il conto si fa ETICHETTA PER ETICHETTA, solo con le etichette presenti in tutti e tre i periodi:
    se il 10-Q usa "Revenues" e il 10-K un'altra voce, mescolarle darebbe numeri senza senso.
    Lo stato patrimoniale e le azioni sono quelli alla chiusura del trimestre.
    `annual_facts`: valori dei 10-K (extract_annual_facts), anche di più CIK della stessa azienda.
    Righe con `source` = TTM_SOURCE e `filed` = data di deposito del 10-Q."""
    out_cols = ["ticker", "period_end", "metric", "value", "filed", "source"]
    q = extract_quarterly_facts(payload)
    if q.empty:
        return pd.DataFrame(columns=out_cols)
    ann = (annual_facts[annual_facts["kind"] == "duration"] if not annual_facts.empty
           else pd.DataFrame(columns=["concept", "period_end", "filed", "value"]))
    annual_ends = pd.DatetimeIndex(sorted(ann.loc[~ann["concept"].isin(NOT_FOR_DATES), "period_end"].unique()))
    reference = _cover_reference(q.rename(columns={"end": "period_end"}))
    empty = q.iloc[:0]
    covers = dict(tuple(q[q["kind"] == "cover_ref"].groupby("accn")))
    inst = dict(tuple(q[q["kind"] == "instant"].groupby("accn")))
    dur = q[q["kind"] == "duration"].copy()
    dur["days"] = (dur["end"] - dur["start"]).dt.days

    rows = []
    for accn, d in dur.groupby("accn"):
        dated = d[~d["concept"].isin(NOT_FOR_DATES)]  # come negli esercizi: date dalle voci principali
        if dated.empty:
            continue
        filed, cur_end = d["filed"].min(), dated["end"].max()
        if len(annual_ends) and np.min(np.abs(annual_ends - cur_end)) <= TOLERANCE:
            continue  # chiusura d'esercizio: vale il 10-K
        at_end = d[d["end"] == cur_end]
        metrics: dict[str, float] = {}
        ytd = at_end[at_end["days"] <= YTD_MAX_DAYS]
        if not ytd.empty and not ann.empty:
            ytd_days = ytd["days"].max()
            ytd = ytd[ytd["days"] == ytd_days]
            prev_end = cur_end - pd.DateOffset(years=1)
            prev = d[((d["end"] - prev_end).abs() <= TOLERANCE) & ((d["days"] - ytd_days).abs() <= 10)]
            fy_end = ytd["start"].iloc[0] - pd.Timedelta(days=1)
            fy = (ann[((ann["period_end"] - fy_end).abs() <= TOLERANCE) & (ann["filed"] <= filed)]
                  .sort_values("filed").groupby("concept")["value"].last())
            cur = ytd.groupby("concept")["value"].last()
            prev_v = prev.groupby("concept")["value"].last()
            # utile ante imposte "Stati Uniti" / "estero": nel 10-Q spesso è la riga del conto economico (= totale),
            # nel 10-K la sola parte americana della nota sulle imposte. Combinarle etichetta per etichetta darebbe
            # numeri senza senso: si usano solo periodo per periodo (sotto)
            common = cur.index.intersection(fy.index).intersection(prev_v.index).difference(PRETAX_PARTS)
            present = set(cur.index) | set(fy.index) | set(prev_v.index)
            # un livello di etichette alla volta (LEVELS: prima quelle di sempre, in comune e poi periodo per periodo;
            # poi le altre etichette esatte, infine le approssimazioni) e solo per ciò che resta vuoto
            for level in LEVELS:
                found = _flow_metrics(cur[common] + fy[common] - prev_v[common], level, present)
                metrics.update({m: v for m, v in found.items() if pd.isna(metrics.get(m, np.nan))})
                # senza un'etichetta comune si combinano le metriche già calcolate per periodo: per le voci con
                # sinonimi quasi identici va bene (es. Agilent: ProfitLoss nel 10-K, NetIncomeLoss nei 10-Q),
                # non per ricavi e margine lordo, dove etichette diverse sono spesso voci diverse
                by_period = [_flow_metrics(v, level, present) for v in (cur, fy, prev_v)]
                for m in FLOW_METRICS:
                    if pd.isna(metrics[m]) and m not in NO_MIXED_LABELS:
                        metrics[m] = by_period[0][m] + by_period[1][m] - by_period[2][m]
            if pd.isna(metrics["free_cash_flow"]):
                metrics["free_cash_flow"] = metrics["operating_cash_flow"] + metrics["capex"]
            for m in ("revenue", "gross_profit"):  # etichette diverse tra 10-Q e 10-K possono dare assurdità
                if pd.notna(metrics[m]) and metrics[m] <= 0:
                    metrics[m] = np.nan
        balance = inst.get(accn, empty)
        balance = balance[balance["end"] == cur_end]
        if not balance.empty:
            metrics.update(_balance_metrics(balance.groupby("concept")["value"].last(), quarterly=True))
        q_shares = at_end[at_end["concept"].isin(DILUTED_SHARES) & at_end["days"].between(*QUARTER_DAYS)]
        cover = covers.get(accn, empty)
        shares = (float(q_shares["value"].iloc[-1]) if not q_shares.empty
                  else float(cover.loc[cover["end"] == cover["end"].max(), "value"].sum()) if not cover.empty
                  else np.nan)
        if np.isfinite(shares) and shares > 0:
            metrics["shares"] = shares * _scale_for_document(shares, filed, reference)
        for metric, value in metrics.items():
            if pd.notna(value):
                rows.append((ticker, cur_end, metric, float(value), filed, TTM_SOURCE))

    out = pd.DataFrame(rows, columns=out_cols)
    return out.drop_duplicates(["ticker", "period_end", "metric", "filed"], keep="last").reset_index(drop=True)


def parse_sec(payload: dict, ticker: str, annual_facts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Esercizi completi dai 10-K + ultimi 12 mesi a ogni trimestre dai 10-Q.
    `annual_facts`: valori dei 10-K di tutti i CIK dell'azienda (default: solo questo documento)."""
    facts = extract_annual_facts(payload)
    annual = parse_companyfacts(payload, ticker, facts)
    ttm = parse_ttm(payload, ticker, facts if annual_facts is None else annual_facts)
    return pd.concat([annual, ttm], ignore_index=True) if not ttm.empty else annual


class SecSource:
    """Sorgente bilanci SEC. `cik_map`: ticker -> CIK.
    `extra_ciks`: ticker -> altri CIK della stessa azienda (es. dopo una riorganizzazione in una nuova
    holding, che ha un CIK nuovo senza lo storico dei 10-K): i bilanci di tutti i CIK vengono uniti."""

    full_history = True  # companyfacts contiene ogni valore mai depositato: i bilanci si sostituiscono
    sources = ("sec", TTM_SOURCE)  # entrambe sostituite a ogni download (anche se un'azienda non ha 10-Q)

    def __init__(self, http, cik_map: dict[str, int | None], extra_ciks: dict[str, list[int]] | None = None):
        self.http = http
        self.cik_map = cik_map
        self.extra_ciks = extra_ciks or {}

    def _payloads(self, ticker: str) -> list[dict]:
        cik = self.cik_map.get(ticker)
        if cik is None or pd.isna(cik):
            raise ValueError(f"CIK sconosciuto per {ticker}")
        ciks = list(dict.fromkeys([int(cik), *map(int, self.extra_ciks.get(ticker, []))]))
        return [self.http.get_json(COMPANYFACTS_URL.format(cik=c)) for c in ciks]

    def fetch_statements(self, ticker: str) -> pd.DataFrame:
        return self._statements(ticker, self._payloads(ticker))

    def fetch_bundle(self, ticker: str) -> dict:
        """Con un solo download: bilanci (esercizi e ultimi 12 mesi), voci per singolo trimestre e tutti i
        valori depositati (archivio completo, con le descrizioni delle etichette)."""
        from modules.sec_quarterly import extract_all_facts, quarterly_values, standard_quarters

        payloads = self._payloads(ticker)
        parts = [extract_all_facts(p) for p in payloads]
        facts = pd.concat([f for f, _ in parts], ignore_index=True).drop_duplicates()
        labels = pd.concat([lab for _, lab in parts], ignore_index=True).drop_duplicates(
            ["taxonomy", "concept", "unit"])
        return {"statements": self._statements(ticker, payloads), "facts": facts, "labels": labels,
                "quarterly": standard_quarters(quarterly_values(facts), ticker, facts)}

    def _statements(self, ticker: str, payloads: list[dict]) -> pd.DataFrame:
        # l'esercizio precedente di un 10-Q può stare nel CIK vecchio (es. XOM dopo la nuova holding)
        annual_facts = None
        if len(payloads) > 1:
            parts = [f for f in map(extract_annual_facts, payloads) if not f.empty]
            annual_facts = pd.concat(parts, ignore_index=True) if parts else extract_annual_facts({})
        frames = [parse_sec(p, ticker, annual_facts) for p in payloads]
        frames = [f for f in frames if not f.empty]
        if len(frames) <= 1:
            return frames[0] if frames else parse_companyfacts({}, ticker)
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates(["ticker", "period_end", "metric", "filed", "source"],
                                   keep="last").reset_index(drop=True)
