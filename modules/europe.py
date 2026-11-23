"""
Aziende europee: FTSE MIB e Euro Stoxx 50 (config.yaml, sezione europe).

- Elenco da Wikipedia (simbolo Yahoo già con il suffisso della borsa: ENI.MI, ADS.DE, ASML.AS...), aggiornato ogni
  `refresh_days` giorni.
- Codice LEI (identifica l'azienda nei bilanci europei) dal registro GLEIF: dall'ISIN se c'è (FTSE MIB), altrimenti dal
  nome, tenendo il primo candidato che ha davvero bilanci nell'archivio.
- Bilanci annuali dall'archivio pubblico dei bilanci ESEF (filings.xbrl.org): obbligatori dal 2021, in XBRL con le
  voci IFRS. Ogni bilancio dà l'esercizio e quello prima, con la data di pubblicazione (se manca: chiusura + 120 giorni).
  Solo annuali: niente trimestri, quindi niente ultimi 12 mesi aggiornati né backtest lunghi.
- Importi nella valuta di quotazione: se il bilancio è in un'altra valuta (es. STMicroelectronics in dollari) si
  converte al cambio di fine esercizio (Yahoo).
"""
from __future__ import annotations

import io
import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

INDEX_URLS = {"FTSE MIB": "https://en.wikipedia.org/wiki/FTSE_MIB",
              "Euro Stoxx 50": "https://en.wikipedia.org/wiki/Euro_Stoxx_50"}
GLEIF_ISIN = "https://api.gleif.org/api/v1/lei-records?filter[isin]={isin}"
GLEIF_NAME = "https://api.gleif.org/api/v1/lei-records?filter[fulltext]={name}&page[size]=10"
ESEF_COUNTRY = "https://filings.xbrl.org/api/filings?filter[country]={country}&page[size]=500&page[number]={page}"
ESEF_ENTITIES = "https://filings.xbrl.org/api/entities?page[size]=200&page[number]={page}"
# paesi in cui depositano le aziende dei due indici (alcune italiane depositano nei Paesi Bassi o in Lussemburgo)
ESEF_COUNTRIES = ["IT", "DE", "FR", "NL", "ES", "BE", "FI", "IE", "LU", "AT", "PT"]
ESEF_BASE = "https://filings.xbrl.org"
SOURCE = "esef"
# valuta di quotazione dal suffisso Yahoo
LISTING_CURRENCY = {"MI": "EUR", "PA": "EUR", "DE": "EUR", "AS": "EUR", "MC": "EUR", "BR": "EUR", "HE": "EUR",
                    "IR": "EUR", "LS": "EUR", "VI": "EUR", "F": "EUR", "L": "GBP", "SW": "CHF", "CO": "DKK",
                    "ST": "SEK", "OL": "NOK"}

# voce del sistema -> voci IFRS in ordine di preferenza
FLOW = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers", "RevenueFromSaleOfGoods", "RevenueAndOtherOperatingIncome",
                "RevenueAndOperatingIncome"],  # l'ultima: Nordea e Nexi (ricavi + altri proventi operativi)
    "net_income": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "operating_income": ["ProfitLossFromOperatingActivities"],
    "pretax_income": ["ProfitLossBeforeTax"],
    "tax_provision": ["IncomeTaxExpenseContinuingOperations"],
    "interest_expense": ["InterestExpense", "FinanceCosts"],
    "gross_profit": ["GrossProfit"],
    "depreciation": ["DepreciationAndAmortisationExpense",
                     "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities",
                            "CashFlowsFromUsedInOperatingActivitiesContinuingOperations"],  # Axa
    "dividends_paid": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid",
                       "DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities"],
    "buybacks": ["PaymentsToAcquireOrRedeemEntitysShares"],
    "rnd": ["ResearchAndDevelopmentExpense"],
}
CAPEX = ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PurchaseOfPropertyPlantAndEquipment"]
CAPEX_INTANGIBLE = ["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"]
# impianti + immateriali in una voce sola (Sanofi, Hermès, Saint-Gobain, Nokia, Campari, INWIT): solo se mancano le due
# voci separate, per non contare due volte gli immateriali
CAPEX_COMBINED = ["PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"]
# investimenti con una voce propria dell'azienda (TotalEnergies, L'Oréal e Air Liquide in francese, LVMH "investimenti
# operativi", Airbus, Vinci in minuscolo): letti solo se mancano le voci IFRS, e mai voci che contengono incassi,
# cessioni, contributi o acquisizioni di società
EXT_CAPEX = re.compile(r"^(Purchases?|Acquisitions?|PaymentsFor|PaymentsToAcquire|InvestmentsIn)(Of)?(PropertyPlant|Tangible"
                       r"|IntangibleAssets(And)?PropertyPlant|FixedAssets)|^OperatingInvestments|^CapitalExpenditures?"
                       r"|^Acquisitions?D[A-Z]?Immobilisations", re.I)
EXT_NOT = re.compile(r"(?i:Proceeds|Sale|Disposal|Cession|Subsidiar|Business|Financ|Securities|Grant)|Net")
# flusso di cassa operativo con una voce propria (Generali, Technoprobe): solo con questo nome esatto
EXT_OPERATING_CASH_FLOW = re.compile(r"^(Ext)?CashFlowsFromUsedInOperatingActivities$", re.I)
# "cassa generata dalle operazioni" (prima di interessi e imposte): il flusso operativo si ricostruisce togliendo
# interessi e imposte pagati e aggiungendo interessi e dividendi incassati, se classificati nelle attività operative
# (Italgas, Leonardo). I segni nei bilanci ESEF non sono affidabili: pagati sempre in negativo, incassati in positivo.
CASH_FROM_OPERATIONS = ["CashFlowsFromUsedInOperations"]
OPERATING_PAID = ["IncomeTaxesPaidClassifiedAsOperatingActivities", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities",
                  "InterestPaidClassifiedAsOperatingActivities", "FinanceCostsPaidClassifiedAsOperatingActivities"]
OPERATING_RECEIVED = ["InterestReceivedClassifiedAsOperatingActivities", "DividendsReceivedClassifiedAsOperatingActivities",
                      "FinanceIncomeReceivedClassifiedAsOperatingActivities"]
# risultato operativo con una voce propria (LVMH, Safran, Saint-Gobain, Leonardo): prima le voci senza "ricorrente"
EXT_OPERATING_INCOME = re.compile(r"^(ProfitLossFromOperatingActivities|OperatingProfitLoss|ResultatOperationnel)", re.I)
BALANCE = {
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "total_assets": ["Assets"],
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "total_liabilities": ["Liabilities"],
    "cash": ["CashAndCashEquivalents"],
    "receivables": ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"],
    "ppe_net": ["PropertyPlantAndEquipment"],
    "retained_earnings": ["RetainedEarnings"],
    "goodwill": ["Goodwill", "ext:DiCuiAvviamento", "ext:OfWhichGoodwill"],  # banche italiane: "di cui avviamento"
    "intangibles": ["IntangibleAssetsOtherThanGoodwill"],
}
# patrimonio netto delle banche italiane (schema della Banca d'Italia): solo il totale con voce propria
EXT_EQUITY_OWNERS = ["ext:TotalEquityAttributableToOwnersOfParent", "ext:EquityAttributableToOwnersOfParent"]
EXT_EQUITY_TOTAL = ["ext:TotalEquity"]  # meno le quote di terzi
INTANGIBLES_WITH_GOODWILL = ["IntangibleAssetsAndGoodwill"]
DEBT_TOTAL = ["Borrowings"]
DEBT_LONG = ["NoncurrentPortionOfNoncurrentBorrowings", "LongtermBorrowings", "NoncurrentFinancialLiabilities"]
DEBT_SHORT = ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "ShorttermBorrowings",
              "CurrentPortionOfLongtermBorrowings"]
# debito per strumento (obbligazioni + finanziamenti, es. Campari): si sommano, solo se mancano le voci sopra
DEBT_LONG_PARTS = ["NoncurrentPortionOfNoncurrentBondsIssued", "NoncurrentPortionOfNoncurrentLoansReceived",
                   "NoncurrentPortionOfOtherNoncurrentBorrowings"]  # Vinci: obbligazioni + altri finanziamenti
DEBT_SHORT_PARTS = ["CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
                    "CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived"]
# debito con voci proprie (Airbus "financing liabilities", Axa "financing debt"): solo se mancano le voci IFRS, mai
# leasing, derivati, "altre" passività o movimenti di cassa; e solo un totale oppure entrambe le parti (lunga e breve)
EXT_DEBT = re.compile(r"Borrowings?$|FinancingLiabilities$|InterestBearingFinancialLiabilities$|Financ(ial|ing)Debts?$", re.I)
EXT_DEBT_NOT = re.compile(r"Lease|Other|Derivative|RelatedPart|Proceeds|Repayment|Increase|Decrease|Payment|SubjectTo"
                          r"|Cost|Expense|Asset|Instruments", re.I)
# leasing (IFRS 16) a parte, come i leasing operativi delle aziende USA (`operating_leases`): non nel debito
LEASES = ["LeaseLiabilities"]
LEASES_PARTS = ["NoncurrentLeaseLiabilities", "CurrentLeaseLiabilities"]
# movimenti di cassa sui finanziamenti: se ci sono, un bilancio senza voci di debito NON vuol dire "nessun debito"
BORROWING_FLOWS = re.compile(r"(Proceeds|Repayments?)(From|Of)(Noncurrent|Current|Shortterm|Longterm)?(Borrowings|Bonds"
                             r"|Loans|Debentures)|IncreaseDecreaseInCurrentBorrowings|Borrowings$", re.I)
SHARES = ["AdjustedWeightedAverageShares", "WeightedAverageShares"]
SHARES_OUTSTANDING = ["NumberOfSharesOutstanding"]               # a fine esercizio
SHARES_ISSUED = ["NumberOfSharesIssued", "NumberOfSharesIssuedAndFullyPaid"]  # comprende le azioni proprie: ultima scelta
EPS = ["DilutedEarningsLossPerShare", "BasicEarningsLossPerShare", "DilutedEarningsPerShare", "BasicEarningsPerShare",
       "BasicAndDilutedEarningsLossPerShare"]
# utile per azione con una voce propria (Buzzi "...Ordinary", INWIT) e, ultima scelta, delle sole attività in
# funzionamento (Hera, Iberdrola: differenza trascurabile se non ci sono attività cessate)
EXT_EPS = re.compile(r"^(Basic|Diluted|BasicAndDiluted|BasicDiluted)Earnings(Loss)?PerShares?(Ordinary)?$", re.I)
EPS_CONTINUING = ["DilutedEarningsLossPerShareFromContinuingOperations", "BasicEarningsLossPerShareFromContinuingOperations"]
SHARES_MAX_RATIO = 2.0  # un numero di azioni che differisce più di così da utile / utile per azione è in scala sbagliata

# Banche (riconosciute dai conti: margine di interesse positivo e almeno il 25% di interessi + commissioni nette;
# Nexi e Adyen, che hanno commissioni ma interessi negativi o trascurabili, restano aziende normali).
# Nomi delle metriche come per le banche USA (sec_edgar.py), usati da metrics.sector_metrics.
NET_INTEREST = ["InterestRevenueExpense", "ext:NetInterestMargin", "ext:MargineDiInteresse", "ext:NetInterestIncome"]
INTEREST_INCOME = ["RevenueFromInterest", "ext:InterestIncomeAndSimilarRevenues", "ext:InteressiAttiviEProventiAssimilati",
                   "InterestRevenueCalculatedUsingEffectiveInterestMethod"]
INTEREST_EXPENSE = ["InterestExpense", "ext:InterestExpenseAndSimilarCharges", "ext:InteressiPassiviEOneriAssimilati"]
NET_FEES = ["FeeAndCommissionIncomeExpense"]
FEE_INCOME, FEE_EXPENSE = ["FeeAndCommissionIncome"], ["FeeAndCommissionExpense"]
BANK_MIN_NII_SHARE = 0.25
# ricavi totali di una banca o di un'altra finanziaria (margine di intermediazione, "produit net bancaire", "gross
# income"): voci proprie con questi nomi esatti; per le banche senza, interessi netti + commissioni nette + dividendi
# (limite inferiore: mancano risultato di negoziazione e altri proventi). Solo per chi ha interessi o commissioni:
# "GrossIncome" di un'industriale potrebbe essere il margine lordo.
EXT_TOTAL_INCOME = ["ext:MargineDiIntermediazione", "ext:GrossIncome", "ext:NetBankingIncome", "ext:ProduitNetBancaire",
                    "ext:RevenuesFromContinuingActivities", "ext:TotalOperatingIncome", "ext:NetOperatingRevenue"]
BANK_OTHER_INCOME = ["RevenueFromDividends"]
# costi operativi delle banche (spese amministrative + ammortamenti + altri oneri): prima il totale
BANK_COSTS = ["ext:OperatingCosts", "ext:CostiOperativi", "OperatingExpense", "AdministrativeExpense",
              "GeneralAndAdministrativeExpense"]
LOAN_PROVISIONS = ["ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9",
                   "ext:NetLossesRecoveriesOnCreditImpairment"]
# ricavi di un'industriale con voce propria (Italgas "ricavi del core business"): nome esatto, solo se manca l'IFRS
EXT_REVENUE = re.compile(r"^(Total)?(CoreBusiness)?(Revenues?|NetSales)$", re.I)
# assicurazioni (IFRS 17): premi = ricavi assicurativi, sinistri = costi del servizio assicurativo (comprendono le spese
# attribuibili ai contratti: il loss ratio è più vicino a un combined ratio). Non per le banche con una compagnia
# (BNP, Nordea, Banco BPM): il rapporto descriverebbe un'attività secondaria.
PREMIUMS = ["InsuranceRevenue"]
CLAIMS = ["InsuranceServiceExpensesFromInsuranceContractsIssued"]
INSURANCE_MIN_SHARE = 0.5
MIN_DAYS, MAX_DAYS = 330, 400
REVENUE_MIN_OF_PROFIT = 0.01


def parse_index_table(html: str, index: str) -> pd.DataFrame:
    """Componenti di un indice europeo dalla pagina Wikipedia: ticker (simbolo Yahoo), name, isin, country, sector."""
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        cols = {str(c).lower(): c for c in t.columns}
        if "ticker" in cols and ("company" in cols or "name" in cols) and len(t) >= 20:
            out = pd.DataFrame({
                "ticker": t[cols["ticker"]].astype(str).str.strip(),
                "name": t[cols.get("company", cols.get("name"))].astype(str).str.strip(),
                "isin": t[cols["isin"]].astype(str).str.strip() if "isin" in cols else None,
                "country": t[cols["registered office"]] if "registered office" in cols else
                ("Italy" if index == "FTSE MIB" else None),
                "sector": t[cols.get("icb sector", cols.get("sector"))] if ("icb sector" in cols or "sector" in cols)
                else None})
            out = out[out["ticker"].str.contains(r"\.", regex=True)]  # solo simboli Yahoo con la borsa
            out["index_name"] = index
            out["currency"] = [LISTING_CURRENCY.get(t.rsplit(".", 1)[-1], "EUR") for t in out["ticker"]]
            return out.reset_index(drop=True)
    raise ValueError(f"Tabella dei componenti di {index} non trovata su Wikipedia")


def _period(text: str) -> tuple[pd.Timestamp | None, pd.Timestamp]:
    """Periodo xBRL-JSON: "AAAA-MM-GGT00:00:00" (istante) o "inizio/fine"; la fine è esclusiva (mezzanotte del
    giorno dopo), quindi si toglie un giorno."""
    if "/" in text:
        a, b = text.split("/")
        return pd.Timestamp(a[:10]), pd.Timestamp(b[:10]) - pd.Timedelta(days=1)
    return None, pd.Timestamp(text[:10]) - pd.Timedelta(days=1)


def parse_esef_json(payload: dict) -> tuple[pd.DataFrame, str | None]:
    """Valori senza dimensioni di un bilancio ESEF (xBRL-JSON): concept (senza prefisso), start, end, value, unit; e la
    valuta principale del bilancio."""
    rows = []
    for fact in (payload or {}).get("facts", {}).values():
        dims = fact.get("dimensions", {})
        if set(dims) - {"concept", "entity", "period", "unit", "language"}:
            continue  # valori per segmento, area, componente del patrimonio...: non servono
        concept = str(dims.get("concept", ""))
        if "period" not in dims or ":" not in concept:
            continue
        # voci proprie dell'azienda con il prefisso "ext:" (servono solo per gli investimenti, vedi EXT_CAPEX)
        name = concept.split(":", 1)[1] if concept.startswith("ifrs-full:") else "ext:" + concept.split(":", 1)[1]
        try:
            value = float(fact.get("value"))
        except (TypeError, ValueError):
            continue
        start, end = _period(dims["period"])
        rows.append((name, start, end, value, str(dims.get("unit", ""))))
    df = pd.DataFrame(rows, columns=["concept", "start", "end", "value", "unit"]).drop_duplicates()
    money = df["unit"].str.startswith("iso4217:") & ~df["unit"].str.contains("/")
    currency = df.loc[money, "unit"].str.split(":").str[1].mode()
    return df, (currency.iloc[0] if len(currency) else None)


def _first(values: dict, names: list[str]) -> float:
    for n in names:
        v = values.get(n)
        if v is not None and np.isfinite(v):
            return v
    return np.nan


def _sum(values: dict, names: list[str]) -> float:
    """Somma delle voci presenti (NaN se non ce n'è nessuna)."""
    found = [values[n] for n in names if values.get(n) is not None and np.isfinite(values[n])]
    return float(sum(found)) if found else np.nan


def _ext(values: dict, pattern: re.Pattern, exclude: re.Pattern | None = None) -> list[str]:
    """Voci proprie dell'azienda (prefisso "ext:") il cui nome corrisponde, in ordine alfabetico (deterministico)."""
    return sorted(k for k in values if k.startswith("ext:") and pattern.search(k[4:])
                  and not (exclude is not None and exclude.search(k[4:])))


def _bank_items(flows: dict) -> dict:
    """Margine di interesse e commissioni nette (NaN se non ci sono)."""
    nii = _first(flows, NET_INTEREST)
    if not np.isfinite(nii):
        inc, exp = _first(flows, INTEREST_INCOME), _first(flows, INTEREST_EXPENSE)
        nii = inc - abs(exp) if np.isfinite(inc) and np.isfinite(exp) else np.nan
    fees = _first(flows, NET_FEES)
    if not np.isfinite(fees):
        inc, exp = _first(flows, FEE_INCOME), _first(flows, FEE_EXPENSE)
        fees = inc - abs(exp) if np.isfinite(inc) and np.isfinite(exp) else np.nan
    return {"nii": nii, "fees": fees}


def is_bank(flows: dict) -> bool:
    b = _bank_items(flows)
    nii, fees = b["nii"], b["fees"]
    return bool(np.isfinite(nii) and np.isfinite(fees) and nii > 0 and nii >= BANK_MIN_NII_SHARE * (nii + max(fees, 0)))


def _operating_cash_flow(flows: dict) -> float:
    cfo = _first(flows, FLOW["operating_cash_flow"])
    if np.isfinite(cfo):
        return cfo
    cfo = _first(flows, _ext(flows, EXT_OPERATING_CASH_FLOW))
    if np.isfinite(cfo):
        return cfo
    ops = _first(flows, CASH_FROM_OPERATIONS)
    if not np.isfinite(ops):
        return np.nan
    paid = sum(abs(flows[k]) for k in OPERATING_PAID if k in flows and np.isfinite(flows[k]))
    # imposte pagate: una sola delle due voci (sono sinonimi)
    taxes = [k for k in OPERATING_PAID[:2] if k in flows and np.isfinite(flows[k])]
    if len(taxes) == 2:
        paid -= abs(flows[taxes[1]])
    received = sum(abs(flows[k]) for k in OPERATING_RECEIVED if k in flows and np.isfinite(flows[k]))
    return ops - paid + received


def _capex(flows: dict) -> float:
    capex, intangible = _first(flows, CAPEX), _first(flows, CAPEX_INTANGIBLE)
    if not (np.isfinite(capex) or np.isfinite(intangible)):
        capex = _first(flows, CAPEX_COMBINED)
    if not (np.isfinite(capex) or np.isfinite(intangible)):
        capex = _first(flows, _ext(flows, EXT_CAPEX, EXT_NOT))
    if np.isfinite(capex) or np.isfinite(intangible):
        return -(abs(np.nan_to_num(capex)) + abs(np.nan_to_num(intangible)))
    return np.nan


def _debt(bal: dict, flows: dict, bank: bool) -> float:
    """Debito finanziario lordo, leasing esclusi. Banche: NaN (depositi e obbligazioni sono la materia prima del loro
    lavoro, non un finanziamento: niente valore d'impresa né debito / patrimonio, come per gli analisti)."""
    if bank:
        return np.nan
    total = _first(bal, DEBT_TOTAL)
    if np.isfinite(total):
        return total
    long_, short = _first(bal, DEBT_LONG), _first(bal, DEBT_SHORT)
    if not np.isfinite(long_):
        long_ = _sum(bal, DEBT_LONG_PARTS)
    if not np.isfinite(short):
        short = _sum(bal, DEBT_SHORT_PARTS)
    if np.isfinite(long_) or np.isfinite(short):
        return np.nan_to_num(long_) + np.nan_to_num(short)
    ext = _ext(bal, EXT_DEBT, EXT_DEBT_NOT)
    longs = [k for k in ext if re.match(r"(Longterm|Noncurrent)", k[4:], re.I)]
    shorts = [k for k in ext if re.match(r"(Shortterm|Current)", k[4:], re.I)]
    totals = [k for k in ext if k not in longs and k not in shorts]
    if totals:
        return _first(bal, totals)
    if longs and shorts:
        return _first(bal, longs) + _first(bal, shorts)
    # nessuna voce di debito, ma lo stato patrimoniale ha i leasing e nel rendiconto non ci sono movimenti di
    # finanziamenti: l'azienda non ha debito finanziario (argenx)
    if np.isfinite(_leases(bal)) and not any(np.isfinite(flows[k]) and flows[k] != 0
                                             for k in flows if BORROWING_FLOWS.search(k.removeprefix("ext:"))):
        return 0.0
    return np.nan


def _leases(bal: dict) -> float:
    total = _first(bal, LEASES)
    return total if np.isfinite(total) else _sum(bal, LEASES_PARTS)


def _shares(flows: dict, bal: dict, ni: float) -> float:
    """Azioni: media ponderata (diluita, poi base), azioni in circolazione a fine esercizio, utile / utile per azione,
    azioni emesse. Un conteggio diretto lontano più di 2 volte da utile / utile per azione è in scala sbagliata."""
    eps = _first(flows, EPS)
    if not np.isfinite(eps):
        eps = _first({**bal, **flows}, _ext({**bal, **flows}, EXT_EPS))  # INWIT la dichiara come istante
    if not np.isfinite(eps):
        eps = _first(flows, EPS_CONTINUING)
    implied = ni / eps if (np.isfinite(eps) and np.isfinite(ni) and abs(eps) >= 0.01 and ni != 0
                           and np.sign(ni) == np.sign(eps)) else np.nan
    direct = _first(flows, SHARES)
    if not (np.isfinite(direct) and direct > 0):
        direct = _first(bal, SHARES_OUTSTANDING)
    if np.isfinite(direct) and direct > 0:
        if np.isfinite(implied) and not (1 / SHARES_MAX_RATIO <= direct / implied <= SHARES_MAX_RATIO):
            return implied
        return direct
    if np.isfinite(implied):
        return implied
    issued = _first(bal, SHARES_ISSUED)
    return issued if np.isfinite(issued) and issued > 0 else np.nan


def _annual_facts(facts: pd.DataFrame) -> pd.DataFrame:
    """Valori di durata annuale. Se il bilancio non ne ha nessuno ma ha valori di durata "un giorno" (Recordati dal
    2022 etichetta tutto il conto economico con il solo 31 dicembre), quelli sono l'esercizio."""
    days = (facts["end"] - facts["start"]).dt.days
    annual = facts[facts["start"].notna() & days.between(MIN_DAYS, MAX_DAYS)]
    if annual.empty:
        annual = facts[facts["start"].notna() & (days == 0)]
    return annual


def esef_statements(facts: pd.DataFrame, ticker: str, filed: pd.Timestamp, fx: float = 1.0) -> pd.DataFrame:
    """Voci del sistema per gli esercizi presenti nel bilancio (di solito l'ultimo e quello prima), in formato lungo
    con `filed` = pubblicazione. `fx`: moltiplicatore verso la valuta di quotazione (non per azioni e utile per azione)."""
    out_cols = ["ticker", "period_end", "metric", "value", "filed", "source"]
    if facts.empty:
        return pd.DataFrame(columns=out_cols)
    annual = _annual_facts(facts)
    instant = facts[facts["start"].isna()]
    rows = []
    for end in sorted(annual["end"].unique()):
        flows = annual[annual["end"] == end].groupby("concept")["value"].last().to_dict()
        bal = instant[(instant["end"] - end).abs() <= pd.Timedelta(days=3)].groupby("concept")["value"].last().to_dict()
        bank = is_bank(flows)
        m = {k: _first(flows, v) for k, v in FLOW.items()}
        m["operating_cash_flow"] = _operating_cash_flow(flows)
        if not np.isfinite(m["operating_income"]):
            m["operating_income"] = _first(flows, sorted(_ext(flows, EXT_OPERATING_INCOME),
                                                         key=lambda k: ("recurring" in k.lower(), k)))
        m.update(_financial_items(flows, bal, m, bank))
        if not np.isfinite(m["revenue"]) and not bank:
            m["revenue"] = _first(flows, _ext(flows, EXT_REVENUE))
        # controllo: ricavi sotto l'1% dell'utile sono un'etichetta sbagliata (Brunello Cucinelli 2025: "Revenue"
        # senza dimensioni = 188 mila euro, i ricavi veri sono etichettati come "parti correlate"). Solo con utile
        # positivo e con un margine così largo: una holding (Prosus) può avere un utile più grande dei ricavi
        if np.isfinite(m["revenue"]) and np.isfinite(m["net_income"]) and m["net_income"] > 0 \
                and 0 <= m["revenue"] < REVENUE_MIN_OF_PROFIT * m["net_income"]:
            m["revenue"] = np.nan
        # risultato operativo = utile ante imposte + oneri finanziari se manca (come per le aziende USA); non per le banche
        if not np.isfinite(m["operating_income"]) and not bank and np.isfinite(m["pretax_income"]) \
                and np.isfinite(m["interest_expense"]):
            m["operating_income"] = m["pretax_income"] + abs(m["interest_expense"])
        capex = _capex(flows)
        # banche e assicurazioni: niente FCF (il flusso operativo comprende depositi, prestiti e investimenti dei
        # premi: Intesa 2022 dava un FCF di 101 miliardi su 20 di ricavi)
        financial_company = bank or np.isfinite(m.get("premiums", np.nan))
        if np.isfinite(capex):
            m["capex"] = capex
            if np.isfinite(m["operating_cash_flow"]) and not financial_company:
                m["free_cash_flow"] = m["operating_cash_flow"] + capex
        for k in ("dividends_paid", "buybacks", "rnd", "interest_expense"):
            m[k] = abs(m[k]) if np.isfinite(m[k]) else np.nan
        m.update({k: _first(bal, v) for k, v in BALANCE.items()})
        if not np.isfinite(m["equity"]):
            owners = _first(bal, EXT_EQUITY_OWNERS)
            total = _first(bal, EXT_EQUITY_TOTAL)
            m["equity"] = owners if np.isfinite(owners) else (
                total - np.nan_to_num(_first(bal, ["NoncontrollingInterests", "ext:TotalNoncontrollingInterests"]))
                if np.isfinite(total) else np.nan)
        if not np.isfinite(m["intangibles"]):
            both = _first(bal, INTANGIBLES_WITH_GOODWILL)
            if np.isfinite(both):
                m["intangibles"] = both - np.nan_to_num(m["goodwill"])
        m["total_debt"] = _debt(bal, flows, bank)
        m["operating_leases"] = _leases(bal)
        amounts = {k: v * fx for k, v in m.items() if np.isfinite(v)}
        shares = _shares(flows, bal, m.get("net_income", np.nan))
        if np.isfinite(shares) and shares > 0:
            amounts["shares"] = shares
        rows += [(ticker, pd.Timestamp(end), k, float(v), filed, SOURCE) for k, v in amounts.items()]
    return pd.DataFrame(rows, columns=out_cols)


SHARES_MAX_JUMP = 20.0  # azioni più di 20 volte lontane dalla mediana dell'azienda: utile per azione in scala sbagliata


def drop_share_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Toglie i numeri di azioni in scala sbagliata confrontando gli esercizi della stessa azienda: alcuni bilanci
    dichiarano l'utile per azione in millesimi o in milioni (AB InBev 2023, BBVA 2018, Generali 2020, Poste 2022,
    TotalEnergies 2020: azioni 100-1.000.000 volte fuori). Mediana dei logaritmi, tolleranza `SHARES_MAX_JUMP`
    (un raggruppamento 10:1 come Fincantieri 2024 resta). Con un solo esercizio non si può controllare."""
    sh = df["metric"] == "shares"
    if sh.sum() < 2:
        return df
    logs = np.log(df.loc[sh, "value"].clip(lower=1))
    center = logs.groupby(df.loc[sh, "ticker"]).transform("median")
    bad = (logs - center).abs() > np.log(SHARES_MAX_JUMP)
    return df.drop(index=bad[bad].index)


def _financial_items(flows: dict, bal: dict, m: dict, bank: bool) -> dict:
    """Ricavi e voci di settore di banche, assicurazioni e altre finanziarie."""
    out = {}
    b = _bank_items(flows)
    financial = np.isfinite(b["nii"]) or np.isfinite(b["fees"])
    if not np.isfinite(m["revenue"]) and financial:
        total = _first(flows, EXT_TOTAL_INCOME)
        if not np.isfinite(total) and bank:
            total = b["nii"] + np.nan_to_num(b["fees"]) + np.nan_to_num(_first(flows, BANK_OTHER_INCOME))
        out["revenue"] = total
    if bank:
        out["net_interest_income"] = b["nii"]
        costs = _first(flows, BANK_COSTS)
        out["noninterest_expense"] = abs(costs) if np.isfinite(costs) else np.nan
        prov = _first(flows, LOAN_PROVISIONS)
        out["loan_provisions"] = abs(prov) if np.isfinite(prov) else np.nan
    else:
        premiums, claims = _first(flows, PREMIUMS), _first(flows, CLAIMS)
        # solo se l'assicurazione è l'attività principale (almeno metà dei ricavi, o ricavi non dichiarati)
        rev = m["revenue"]
        if np.isfinite(premiums) and premiums > 0 and not (np.isfinite(rev) and premiums < INSURANCE_MIN_SHARE * rev):
            out["premiums"] = premiums
            out["claims"] = abs(claims) if np.isfinite(claims) else np.nan
            if not np.isfinite(m["revenue"]) and not np.isfinite(out.get("revenue", np.nan)):
                out["revenue"] = premiums  # ricavi di un assicuratore senza "Revenue": i ricavi assicurativi
    return out


COUNTRY_CODES = {"Italy": "IT", "Germany": "DE", "France": "FR", "Netherlands": "NL", "Spain": "ES", "Belgium": "BE",
                 "Finland": "FI", "Ireland": "IE", "Luxembourg": "LU", "Austria": "AT", "Portugal": "PT"}


# forme societarie e parole che non distinguono un'azienda dall'altra
LEGAL_WORDS = {"se", "sa", "ag", "nv", "spa", "plc", "kgaa", "oyj", "abp", "bv", "sca", "sicaf", "sapa", "co", "and",
               "the", "societa", "per", "azioni", "compagnie", "de", "group", "groupe", "groep", "holding", "holdings", "ab",
               "sociedad", "anonima", "societe", "anonyme"}
# sigle con cui Wikipedia chiama aziende che nei bilanci hanno il nome per esteso
NAME_ALIASES = {"BBVA": "Banco Bilbao Vizcaya Argentaria"}


def normalize_name(name: str) -> str:
    """Nome confrontabile: minuscolo, senza accenti, punteggiatura e forme societarie ("L'Oréal S.A." -> "loreal")."""
    import unicodedata

    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", s)  # s.p.a. -> spa, n.v. -> nv
    words = [w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 1 and w not in LEGAL_WORDS]  # L'Oréal -> oreal
    return " ".join(words)


def match_archive_name(name: str, names: dict[str, str], country_of: dict[str, str], country: str) -> str | None:
    """LEI dell'emittente dell'archivio con lo stesso nome: uguale dopo la normalizzazione, oppure (solo se unico) un
    nome che contiene l'altro parola per parola ("Hermès" -> "Hermès International"). Prima lo stesso paese."""
    import difflib

    target = normalize_name(NAME_ALIASES.get(str(name), name))
    if not target:
        return None
    ordered = sorted(names.items(), key=lambda kv: country_of.get(kv[0]) != country)
    exact = [lei for lei, n in ordered if normalize_name(n) == target]
    if exact:
        return exact[0]
    padded = f" {target} "
    contains = [lei for lei, n in ordered if country_of.get(lei) == country and len(target) >= 4
                and (padded in f" {normalize_name(n)} " or f" {normalize_name(n)} " in padded) and normalize_name(n)]
    if len(contains) == 1:
        return contains[0]
    close = [(difflib.SequenceMatcher(None, target, normalize_name(n)).ratio(), lei) for lei, n in ordered
             if country_of.get(lei) == country]
    close = sorted(close, reverse=True)
    if close and close[0][0] >= 0.9 and (len(close) == 1 or close[1][0] < close[0][0] - 0.05):
        return close[0][1]
    return None


def rank_candidates(records: list[dict], name: str, country: str) -> list[str]:
    """LEI dei risultati GLEIF ordinati: stesso paese della sede prima, poi nome legale più simile al nome cercato."""
    import difflib

    def score(rec):
        ent = rec.get("attributes", {}).get("entity", {})
        legal = str(ent.get("legalName", {}).get("name", ""))
        same = ent.get("legalAddress", {}).get("country") == country or ent.get("jurisdiction") == country
        sim = difflib.SequenceMatcher(None, name.lower(), legal.lower().split(",")[0]).ratio()
        return (same, sim)

    return [rec["id"] for rec in sorted(records, key=score, reverse=True)]


class EuropeIngestor:
    def __init__(self, db, http, cfg: dict, fx_rate=None, archive_http=None):
        self.db, self.http = db, http
        # l'archivio ESEF risponde 429 se lo si interroga troppo in fretta: connessione a parte, più lenta
        self.archive = archive_http or http
        self.cfg = cfg.get("europe", {})
        self.fx_rate = fx_rate or _yahoo_fx

    # ------------------------------------------------------------ elenco e codici LEI
    def refresh_universe(self, force: bool = False) -> pd.DataFrame:
        """Componenti degli indici in config (al massimo ogni `refresh_days`); LEI cercato per i nuovi."""
        known = self.db.load_foreign()
        last = self.db.last_fetched("_europe", "universe")
        days = int(self.cfg.get("refresh_days", 7))
        if not force and not known.empty and last is not None and (pd.Timestamp.now() - last).days < days:
            return known
        frames = []
        for index in self.cfg.get("indices", list(INDEX_URLS)):
            try:
                frames.append(parse_index_table(self.http.get_text(INDEX_URLS[index]), index))
            except Exception as exc:  # noqa: BLE001 - un elenco che non si legge non ferma l'altro
                logger.warning("Elenco di %s non letto: %s", index, exc)
        if not frames:
            return known
        comp = pd.concat(frames, ignore_index=True)
        comp = comp.groupby("ticker", as_index=False).agg(
            name=("name", "first"), isin=("isin", "first"), country=("country", "first"), sector=("sector", "first"),
            currency=("currency", "first"), index_name=("index_name", lambda s: ", ".join(sorted(set(s)))))
        old_lei = known.dropna(subset=["lei"]).set_index("ticker")["lei"].to_dict() if not known.empty else {}
        comp["lei"] = [old_lei.get(t) or self._find_lei(r) for t, r in zip(comp["ticker"], comp.itertuples())]
        self.db.upsert_foreign(comp)
        self.db.mark_fetched("_europe", "universe")
        logger.info("Europa: %d aziende (%s), %d con il codice LEI", len(comp), ", ".join(self.cfg.get("indices", [])),
                    comp["lei"].notna().sum())
        return self.db.load_foreign()

    def _find_lei(self, r) -> str | None:
        try:
            if isinstance(r.isin, str) and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", r.isin):
                data = self.http.get_json(GLEIF_ISIN.format(isin=r.isin)).get("data") or []
                if data:
                    return data[0]["id"]
            name = re.sub(r"\s*\(.*?\)", "", str(r.name))
            # prima i nomi degli emittenti dell'archivio (nessuna richiesta per azienda, niente omonimi di altri paesi)
            lei = match_archive_name(name, self._archive_names(), self._country_of, COUNTRY_CODES.get(str(r.country), ""))
            if lei:
                return lei
            data = self.http.get_json(GLEIF_NAME.format(name=name)).get("data") or []
            # la ricerca per testo dà anche fondi e filiali: prima lo stesso paese della sede, poi il nome più
            # simile; il primo che ha bilanci nell'archivio (l'indice dell'archivio è già scaricato: nessuna richiesta)
            for lei in rank_candidates(data, name, COUNTRY_CODES.get(str(r.country), "")):
                if self._filings(lei):
                    return lei
        except Exception as exc:  # noqa: BLE001
            logger.debug("LEI di %s non trovato: %s", r.name, exc)
        return None

    def _archive_index(self) -> dict[str, list[dict]]:
        """Tutti i bilanci dell'archivio nei paesi che servono, per LEI (il nome del file comincia con il LEI): poche
        decine di richieste invece di una per azienda (l'archivio risponde 429 se lo si interroga troppo)."""
        if getattr(self, "_index", None) is None:
            index: dict[str, list[dict]] = {}
            for country in self.cfg.get("esef_countries", ESEF_COUNTRIES):
                page = 1
                while True:
                    try:
                        data = self.archive.get_json(ESEF_COUNTRY.format(country=country, page=page))
                    except Exception as exc:  # noqa: BLE001 - un paese che non risponde non ferma gli altri
                        logger.warning("Archivio ESEF, %s pagina %d non letta: %s", country, page, exc)
                        break
                    for f in data.get("data") or []:
                        a = f.get("attributes", {})
                        if a.get("json_url"):
                            index.setdefault(a["json_url"].strip("/").split("/")[0], []).append({**a, "_country": country})
                    if not (data.get("links") or {}).get("next"):
                        break
                    page += 1
            self._index = index
            logger.info("Archivio ESEF: %d emittenti con bilanci", len(index))
        return self._index

    @property
    def _country_of(self) -> dict[str, str]:
        return {lei: fs[0]["_country"] for lei, fs in self._archive_index().items() if fs and "_country" in fs[0]}

    def _archive_names(self) -> dict[str, str]:
        """Nome di ogni emittente che ha bilanci nell'archivio (elenco degli emittenti: ~40 pagine, una volta)."""
        if getattr(self, "_names", None) is None:
            index, names, page = self._archive_index(), {}, 1
            while True:
                try:
                    data = self.archive.get_json(ESEF_ENTITIES.format(page=page))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Archivio ESEF, emittenti pagina %d non letta: %s", page, exc)
                    break
                for e in data.get("data") or []:
                    a = e.get("attributes", {})
                    if a.get("identifier") in index:
                        names[a["identifier"]] = a.get("name", "")
                if not (data.get("links") or {}).get("next") or not data.get("data"):
                    break
                page += 1
            self._names = names
            logger.info("Archivio ESEF: nomi di %d emittenti", len(names))
        return self._names

    def _filings(self, lei: str) -> list[dict]:
        return self._archive_index().get(lei, [])

    # ------------------------------------------------------------ bilanci
    def update(self) -> int:
        """Bilanci ESEF nuovi delle aziende con LEI; riscrive tutti i bilanci di un'azienda quando ne arriva uno."""
        comp = self.db.load_foreign()
        done = self.db.fetched_tickers("esef_filing")
        n = 0
        for r in comp.dropna(subset=["lei"]).itertuples():
            filings = self._filings(r.lei)
            ids = {f["json_url"] for f in filings}
            if not filings or ids <= done:
                continue
            frames = []
            for f in sorted(filings, key=lambda f: f["period_end"]):
                try:
                    facts, currency = parse_esef_json(self.archive.get_json(ESEF_BASE + f["json_url"]))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Bilancio ESEF di %s %s non letto: %s", r.ticker, f["period_end"], exc)
                    continue
                period_end = pd.Timestamp(f["period_end"])
                filed = pd.Timestamp(f["published"][:10]) if f.get("published") else period_end + pd.Timedelta(days=120)
                fx = 1.0 if not currency or currency == r.currency else self.fx_rate(currency, r.currency, period_end)
                frames.append(esef_statements(facts, r.ticker, filed, fx))
            if frames:
                df = pd.concat(frames, ignore_index=True)
                df = df.sort_values("filed").drop_duplicates(["ticker", "period_end", "metric", "filed"], keep="last")
                df = drop_share_outliers(df)
                self.db.replace_financials(r.ticker, df, SOURCE)
                self.db.mark_fetched_many(sorted(ids), "esef_filing")
                n += 1
        logger.info("Europa: bilanci aggiornati per %d aziende", n)
        return n


def _yahoo_fx(src: str, dst: str, day: pd.Timestamp) -> float:
    """Quanti `dst` vale un `src` a fine giornata `day` (Yahoo, es. USDEUR=X)."""
    import yfinance as yf

    df = yf.download(f"{src}{dst}=X", start=day - pd.Timedelta(days=10), end=day + pd.Timedelta(days=1),
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"cambio {src}/{dst} non disponibile al {day.date()}")
    close = df["Close"]
    close = close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close
    return float(close.dropna().iloc[-1])
