"""
Sorgente di dati SIMULATI con la stessa interfaccia delle sorgenti reali
(prezzi Yahoo, bilanci SEC, lista S&P 500).

Serve per provare l'intero sistema senza internet (`python main.py --demo`) e per i
test automatici. I numeri sono inventati: non vanno usati per decisioni reali.
I prezzi replicano il formato di yf.download (colonne MultiIndex) e i bilanci quello
del JSON companyfacts della SEC, così vengono esercitati anche i parser reali.
NVDA fa uno split 10:1 circa un anno prima della fine: i prezzi sono rettificati (come su Yahoo),
le azioni nei 10-K depositati prima dello split no (come alla SEC).
Due ex membri dell'indice: X000 è uscito ed è stato delistato (come un'azienda acquisita: Yahoo
non ne ha nessun prezzo e la SEC non lo mappa più a un CIK), X001 è uscito ma è ancora quotato.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from modules.data_ingestor import normalize_download
from modules.sec_edgar import parse_sec

INSTANT_DEMO = ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "CashAndCashEquivalentsAtCarryingValue",
                "StockholdersEquity", "Assets", "AssetsCurrent", "LiabilitiesCurrent", "Liabilities",
                "RetainedEarningsAccumulatedDeficit", "AccountsReceivableNetCurrent", "PropertyPlantAndEquipmentNet",
                "OperatingLeaseLiability")

REAL_NAMES = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"]
DEMO_SPLITS = {"NVDA": 10.0}
# ex membri: ticker -> (giorni prima della fine in cui escono dall'indice, delistato?)
FORMER_MEMBERS = {"X000": (400, True), "X001": (250, False)}  # ticker -> rapporto, ~252 giorni di borsa prima della fine
SECTORS = ["Information Technology", "Health Care", "Financials", "Consumer Discretionary",
           "Communication Services", "Industrials", "Consumer Staples", "Energy", "Utilities",
           "Real Estate", "Materials"]


class DemoSource:
    min_expected = 1
    full_history = True  # come la SEC

    def __init__(self, seed: int = 7, end: str | None = None, years: int = 6, n_tickers: int = 40):
        self.seed = seed
        self.end = pd.Timestamp(end) if end else pd.Timestamp(datetime.now().date())
        self.years = years
        extra = [f"D{i:03d}" for i in range(max(n_tickers - len(REAL_NAMES), 0))]
        self.tickers = (REAL_NAMES + extra)[:n_tickers]
        self.former = list(FORMER_MEMBERS) if n_tickers >= len(REAL_NAMES) else []
        self._raw: pd.DataFrame | None = None
        self._splits: pd.DataFrame | None = None

    def _removal(self, ticker: str) -> pd.Timestamp:
        return self.end - pd.Timedelta(days=FORMER_MEMBERS[ticker][0])

    # ------------------------------------------------------------- universo
    def sp500_constituents(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + 1)
        n = len(self.tickers)
        added = [pd.Timestamp("1990-01-01") + pd.Timedelta(days=int(d)) for d in rng.integers(0, 12000, n)]
        # qualche titolo entra nell'indice durante il periodo di backtest
        for i in rng.choice(n, size=max(1, n // 10), replace=False):
            added[i] = self.end - pd.Timedelta(days=int(rng.integers(30, 500)))
        return pd.DataFrame({
            "ticker": self.tickers, "name": [f"Demo {t}" for t in self.tickers],
            "sector": [SECTORS[i % len(SECTORS)] for i in range(n)], "sub_industry": None,
            "date_added": added, "cik": list(range(1000, 1000 + n)),
        })

    def sec_ticker_map(self) -> dict[str, int]:
        listed = self.tickers + [t for t in self.former if not FORMER_MEMBERS[t][1]]  # i delistati non hanno CIK
        return {t: 1000 + i for i, t in enumerate(listed)}

    def sp500_changes(self) -> pd.DataFrame:
        """Stesso formato di parse_sp500_changes: le uscite degli ex membri."""
        rows = [{"date": self._removal(t), "added": None, "added_name": None, "removed": t,
                 "removed_name": f"Demo {t}", "reason": "Market capitalization changes."} for t in self.former]
        return pd.DataFrame(rows, columns=["date", "added", "added_name", "removed", "removed_name", "reason"])

    # ------------------------------------------------------------- prezzi
    def _simulate(self) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        days = pd.bdate_range(end=self.end, periods=int(252 * self.years))
        holidays = days[rng.choice(len(days), size=9 * self.years, replace=False)]
        days = days.difference(holidays)  # calendario realistico con festivi
        n = len(days)
        # mercato a regimi: fasi rialziste calme e ribassiste volatili
        regime = np.zeros(n, dtype=int)
        state = 0
        for i in range(n):
            if rng.random() < (0.01 if state == 0 else 0.03):
                state = 1 - state
            regime[i] = state
        mkt = np.where(regime == 0, rng.normal(0.0007, 0.008, n), rng.normal(-0.0012, 0.022, n))

        fields = {}
        level = np.clip(4.2 + np.cumsum(rng.normal(0, 0.03, n)), 0.5, 8.0)
        fields["^TNX"] = pd.DataFrame({"Open": level, "High": level, "Low": level, "Close": level,
                                       "Volume": np.zeros(n)}, index=days)
        spy = 400 * np.exp(np.cumsum(mkt + rng.normal(0, 0.001, n)))
        fields["SPY"] = pd.DataFrame({"Open": spy, "High": spy * 1.005, "Low": spy * 0.995, "Close": spy,
                                      "Volume": np.full(n, 8e7)}, index=days)
        for k, t in enumerate(self.tickers + self.former):
            beta = 0.7 + 0.15 * (k % 5)
            r = beta * mkt + rng.normal(0.0002, 0.01 + 0.002 * (k % 4), n)
            close = (30 + 20 * (k % 25)) * np.exp(np.cumsum(r))
            if t in self.former and FORMER_MEMBERS[t][1]:
                close = np.full(n, np.nan)  # delistato: Yahoo non ne ha più nessun prezzo
            spread = np.abs(rng.normal(0, 0.01, n)) * close
            fields[t] = pd.DataFrame({
                # apertura dentro l'escursione del giorno (low <= open <= high): stesse estrazioni casuali di prima
                "Open": np.clip(close * (1 + rng.normal(0, 0.003, n)), close - spread, close + spread),
                "High": close + spread, "Low": close - spread,
                "Close": close, "Volume": rng.integers(2_000_000, 40_000_000, n).astype(float),
            }, index=days)
        split_day = days[-252]
        for t, frame in fields.items():
            frame["Stock Splits"] = 0.0
            if t in DEMO_SPLITS:
                frame.loc[split_day, "Stock Splits"] = DEMO_SPLITS[t]
        self._splits = pd.DataFrame([(t, split_day, r) for t, r in DEMO_SPLITS.items() if t in fields],
                                    columns=["ticker", "date", "ratio"])
        raw = pd.concat(fields, axis=1).swaplevel(0, 1, axis=1).sort_index(axis=1)  # come yf.download
        raw.columns.names = ["Price", "Ticker"]
        raw.index.name = "Date"
        return raw

    def download_prices(self, tickers: list[str], start) -> pd.DataFrame:
        if self._raw is None:
            self._raw = self._simulate()
        available = [t for t in tickers if t in set(self._raw.columns.get_level_values(1))]
        if not available:
            return normalize_download(pd.DataFrame(), list(tickers))
        raw = self._raw.loc[pd.Timestamp(start):, (slice(None), available)]
        return normalize_download(raw, available)

    def fetch_splits(self, ticker: str) -> pd.DataFrame:
        if self._raw is None:
            self._raw = self._simulate()
        return self._splits[self._splits["ticker"] == ticker].reset_index(drop=True)

    # ------------------------------------------------------------- bilanci (formato SEC)
    def companyfacts(self, ticker: str) -> dict:
        """JSON nello stesso formato di data.sec.gov/api/xbrl/companyfacts (sottoinsieme)."""
        rng = np.random.default_rng(sum(map(ord, ticker)) + self.seed)
        prices = self.download_prices([ticker], self.end - pd.DateOffset(years=self.years))
        shares = float(rng.uniform(2e8, 1.5e10 / 10))
        fcf_yield, growth = rng.uniform(0.025, 0.07), rng.uniform(0.0, 0.18)
        n_years = self.years + 1
        fy_ends = [pd.Timestamp(f"{y}-12-31") for y in range(self.end.year - n_years, self.end.year)]
        first_px = prices["close"].iloc[0] if not prices.empty else 50.0
        series = {}
        for j, fy in enumerate(fy_ends):
            px = prices.loc[prices["date"] <= fy, "close"]
            mcap = (px.iloc[-1] if not px.empty else first_px) * shares
            ocf = mcap * fcf_yield * 1.25 * (1 + growth) ** j / (1 + growth) ** (n_years - 1)
            revenue = ocf / rng.uniform(0.15, 0.35)
            ni = revenue * rng.uniform(0.06, 0.28)
            debt = mcap * rng.uniform(0.02, 0.15)
            series[fy] = {
                "NetCashProvidedByUsedInOperatingActivities": ocf,
                "PaymentsToAcquirePropertyPlantAndEquipment": ocf * 0.2,
                "RevenueFromContractWithCustomerExcludingAssessedTax": revenue, "NetIncomeLoss": ni,
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": ni / 0.82,
                "IncomeTaxExpenseBenefit": ni / 0.82 * 0.18, "InterestExpense": debt * rng.uniform(0.02, 0.05),
                "WeightedAverageNumberOfDilutedSharesOutstanding": shares,
                "LongTermDebtNoncurrent": debt * 0.85, "LongTermDebtCurrent": debt * 0.15,
                "CashAndCashEquivalentsAtCarryingValue": debt * rng.uniform(0.3, 1.2),
                "StockholdersEquity": debt / rng.uniform(0.3, 2.0),
                "GrossProfit": revenue * rng.uniform(0.25, 0.6),
                "Assets": debt * rng.uniform(2.5, 6.0),
            }
            row = series[fy]  # voci derivate senza altri numeri casuali (il resto della demo non cambia)
            row.update({
                "OperatingIncomeLoss": ni / 0.82 + row["InterestExpense"],
                "DepreciationDepletionAndAmortization": ocf * 0.15,
                "PaymentsOfDividendsCommonStock": ni * 0.3, "PaymentsForRepurchaseOfCommonStock": ni * 0.2,
                "AssetsCurrent": row["Assets"] * 0.3, "LiabilitiesCurrent": row["Assets"] * 0.18,
                "Liabilities": row["Assets"] - row["StockholdersEquity"],
                "RetainedEarningsAccumulatedDeficit": row["StockholdersEquity"] * 0.7,
                "ShareBasedCompensation": revenue * 0.02, "ResearchAndDevelopmentExpense": revenue * 0.05,
                "SellingGeneralAndAdministrativeExpense": revenue * 0.15,
                "AccountsReceivableNetCurrent": revenue * 0.12, "PropertyPlantAndEquipmentNet": row["Assets"] * 0.25,
                "OperatingLeaseLiability": row["Assets"] * 0.03,
            })
        facts: dict[str, dict] = {}
        for j, fy in enumerate(fy_ends):
            filed = fy + pd.Timedelta(days=int(rng.integers(30, 75)))   # 10-K depositato 1-2 mesi dopo
            accn = f"0000{1000 + j:06d}-{fy.year % 100:02d}-{j:06d}"
            for yrs_back in (0, 1):  # ogni 10-K ripete l'anno precedente (con una piccola correzione)
                if j - yrs_back < 0:
                    continue
                period = fy_ends[j - yrs_back]
                restate = 1.0 if yrs_back == 0 else 1.01
                for concept, value in series[period].items():
                    unit = "shares" if concept.startswith("Weighted") else "USD"
                    if unit == "shares":  # come depositato: senza gli split successivi al deposito
                        later = self._splits[(self._splits["ticker"] == ticker) & (self._splits["date"] > filed)]
                        value = value / later["ratio"].prod()
                    entry = {"end": period.strftime("%Y-%m-%d"), "val": value * restate, "accn": accn,
                             "fy": fy.year, "fp": "FY", "form": "10-K", "filed": filed.strftime("%Y-%m-%d")}
                    if concept in INSTANT_DEMO:
                        pass  # valori istantanei: nessuna data di inizio
                    else:
                        entry["start"] = (period - pd.DateOffset(years=1) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    facts.setdefault(concept, {"units": {unit: []}})["units"][unit].append(entry)
        self._add_quarterly(ticker, facts, series, fy_ends, growth)
        return {"cik": self.sec_ticker_map().get(ticker), "entityName": f"Demo {ticker}", "facts": {"us-gaap": facts}}

    def _add_quarterly(self, ticker: str, facts: dict, series: dict, fy_ends: list, growth: float) -> None:
        """10-Q dei primi tre trimestri di ogni esercizio, anno in corso compreso: flussi da inizio esercizio
        (quota dell'anno) con lo stesso periodo dell'anno prima, stato patrimoniale interpolato."""
        nxt = fy_ends[-1] + pd.DateOffset(years=1)
        series = {**series, nxt: {k: v if k.startswith("Weighted") else v * (1 + growth)  # anno in corso
                                  for k, v in series[fy_ends[-1]].items()}}
        years = list(fy_ends) + [nxt]
        day = pd.Timedelta(days=1)
        for j in range(1, len(years)):
            prev_fy, fy = years[j - 1], years[j]
            for q in (1, 2, 3):
                q_end = prev_fy + pd.offsets.MonthEnd(3 * q)
                filed = q_end + pd.Timedelta(days=40)
                if filed > self.end:
                    return
                accn = f"0000{2000 + j:06d}-{q_end.year % 100:02d}-{q:06d}"
                base = {"accn": accn, "fy": fy.year, "fp": f"Q{q}", "form": "10-Q", "filed": filed.strftime("%Y-%m-%d")}
                for concept, value in series[fy].items():
                    old = series[prev_fy][concept]
                    unit = "shares" if concept.startswith("Weighted") else "USD"
                    entries = []
                    if concept in INSTANT_DEMO:
                        entries.append({"end": q_end, "val": old + (value - old) * q / 4})
                    elif unit == "shares":  # trimestre, come depositato (senza gli split successivi)
                        later = self._splits[(self._splits["ticker"] == ticker) & (self._splits["date"] > filed)]
                        entries.append({"start": q_end - pd.offsets.MonthEnd(3) + day, "end": q_end,
                                        "val": value / later["ratio"].prod()})
                    else:  # da inizio esercizio, quest'anno e l'anno prima
                        entries.append({"start": prev_fy + day, "end": q_end, "val": value * q / 4})
                        entries.append({"start": prev_fy - pd.DateOffset(years=1) + day,
                                        "end": q_end - pd.DateOffset(years=1), "val": old * q / 4})
                    for e in entries:
                        entry = {**base, **{k: v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else v
                                            for k, v in e.items()}}
                        facts.setdefault(concept, {"units": {unit: []}})["units"][unit].append(entry)

    def fetch_statements(self, ticker: str) -> pd.DataFrame:
        if ticker not in self.tickers + self.former:
            raise ValueError(f"ticker demo sconosciuto: {ticker}")
        return parse_sec(self.companyfacts(ticker), ticker)
