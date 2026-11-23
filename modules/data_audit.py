"""
Verifica a campione dei dati contro fonti indipendenti (tools/verify_data.py, report in reports/).

- Bilanci: gli esercizi completi del database (fonte SEC, ultima versione depositata, azioni come depositate) contro i
  bilanci annuali di Yahoo (altro fornitore, altre regole di normalizzazione) per gli ultimi ~4 esercizi.
- Prezzi: i rendimenti giornalieri del database contro quelli di Nasdaq (seconda fonte), e l'ultima chiusura.

Le differenze non sono tutte errori nostri: Yahoo mette i leasing nel debito, normalizza alcune voci, a volte usa
dati rivisti. Per questo ogni confronto ha una tolleranza e il report separa "uguale", "piccola differenza" e
"grande differenza", da guardare a mano sul 10-K.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

# metrica del sistema -> (prospetto Yahoo, righe in ordine di preferenza)
YAHOO_ROWS: dict[str, tuple[str, list[str]]] = {
    "revenue": ("inc", ["Total Revenue", "Operating Revenue"]),
    "net_income": ("inc", ["Net Income Common Stockholders", "Net Income"]),
    "operating_income": ("inc", ["Operating Income"]),
    "operating_cash_flow": ("cf", ["Operating Cash Flow"]),
    "capex": ("cf", ["Capital Expenditure"]),
    "free_cash_flow": ("cf", ["Free Cash Flow"]),
    "dividends_paid": ("cf", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
    "buybacks": ("cf", ["Repurchase Of Capital Stock", "Common Stock Payments"]),
    "total_debt": ("bs", ["Total Debt"]),
    "cash": ("bs", ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]),
    "equity": ("bs", ["Stockholders Equity"]),
    "total_assets": ("bs", ["Total Assets"]),
    "shares": ("inc", ["Diluted Average Shares"]),
}
# altre voci, usate solo dai bilanci di riserva (modules/yahoo_fundamentals.py), non dal confronto
EXTRA_ROWS: dict[str, tuple[str, list[str]]] = {
    "gross_profit": ("inc", ["Gross Profit"]),
    "depreciation": ("cf", ["Depreciation And Amortization", "Depreciation Amortization Depletion"]),
    "interest_expense": ("inc", ["Interest Expense", "Interest Expense Non Operating"]),
    "pretax_income": ("inc", ["Pretax Income"]),
    "tax_provision": ("inc", ["Tax Provision"]),
    "stock_compensation": ("cf", ["Stock Based Compensation"]),
    "current_assets": ("bs", ["Current Assets"]),
    "current_liabilities": ("bs", ["Current Liabilities"]),
    "total_liabilities": ("bs", ["Total Liabilities Net Minority Interest"]),
    "retained_earnings": ("bs", ["Retained Earnings"]),
    "goodwill": ("bs", ["Goodwill"]),
    "receivables": ("bs", ["Accounts Receivable", "Receivables"]),
    "ppe_net": ("bs", ["Net PPE"]),
    "rnd": ("inc", ["Research And Development"]),
    "sga": ("inc", ["Selling General And Administration"]),
    "net_interest_income": ("inc", ["Net Interest Income"]),
}
ABSOLUTE = {"depreciation", "interest_expense", "stock_compensation", "rnd", "sga"}  # sempre positive come nella SEC
SMALL, LARGE = 0.02, 0.10   # differenza relativa: fino al 2% uguale, oltre il 10% grande
POSITIVE = {"dividends_paid", "buybacks"}  # nel sistema sono uscite positive, in Yahoo negative


def yahoo_annual(ticker: str) -> dict[str, pd.DataFrame]:
    """Prospetti annuali di Yahoo (righe = voci, colonne = chiusure d'esercizio)."""
    import yfinance as yf

    t = yf.Ticker(ticker.replace(".", "-"))
    return {"inc": t.income_stmt, "cf": t.cashflow, "bs": t.balance_sheet}


def yahoo_value(stmts: dict[str, pd.DataFrame], metric: str, col) -> float:
    """Valore di `metric` nella colonna `col` dei prospetti di Yahoo, con i segni del sistema (NaN se manca)."""
    kind, rows = YAHOO_ROWS[metric] if metric in YAHOO_ROWS else EXTRA_ROWS[metric]
    df = stmts.get(kind)
    if df is None or df.empty or col not in df.columns:
        return np.nan
    for row in rows:
        if row in df.index and pd.notna(df.at[row, col]):
            v = float(df.at[row, col])
            if metric == "total_debt" and "Capital Lease Obligations" in df.index:
                lease = df.at["Capital Lease Obligations", col]  # Yahoo li somma al debito, il sistema no
                v -= float(lease) if pd.notna(lease) else 0.0
            return abs(v) if metric in POSITIVE or metric in ABSOLUTE else v
    return np.nan


_yahoo_value = yahoo_value


def classify(ours: float, theirs: float) -> tuple[float, str]:
    """(differenza relativa, esito): uguale, piccola, grande, manca da noi, manca in Yahoo."""
    if pd.isna(ours) and pd.isna(theirs):
        return np.nan, "assente in entrambi"
    if pd.isna(ours):
        return np.nan, "manca da noi"
    if pd.isna(theirs):
        return np.nan, "manca in Yahoo"
    scale = max(abs(ours), abs(theirs))
    if scale == 0:
        return 0.0, "uguale"
    diff = (ours - theirs) / scale
    return diff, "uguale" if abs(diff) <= SMALL else "piccola" if abs(diff) <= LARGE else "grande"


def compare_statements(ours: pd.DataFrame, stmts: dict[str, pd.DataFrame], ticker: str,
                       tolerance_days: int = 10) -> pd.DataFrame:
    """`ours`: esercizi del database (righe = chiusure, colonne = metriche). Una riga per (esercizio, metrica)."""
    rows = []
    cols = sorted({c for df in stmts.values() if df is not None and not df.empty for c in df.columns})
    for col in cols:
        col = pd.Timestamp(col)
        near = ours.index[np.abs((ours.index - col).days) <= tolerance_days] if not ours.empty else []
        if len(near) == 0:
            continue
        mine = ours.loc[near[-1]]
        for metric in YAHOO_ROWS:
            o = float(mine.get(metric, np.nan))
            y = yahoo_value(stmts, metric, col)
            diff, verdict = classify(o, y)
            if verdict != "assente in entrambi":
                rows.append({"ticker": ticker, "period_end": near[-1], "metric": metric, "ours": o, "yahoo": y,
                             "diff": diff, "verdict": verdict})
    return pd.DataFrame(rows, columns=["ticker", "period_end", "metric", "ours", "yahoo", "diff", "verdict"])


NASDAQ_URL = "https://api.nasdaq.com/api/quote/{t}/historical?assetclass=stocks&fromdate={start}&limit=400"
# il servizio di Nasdaq risponde solo a client che si presentano come un browser (niente dati personali)
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                                "(KHTML, like Gecko) Version/17.0 Safari/605.1.15", "Accept": "application/json"}


def nasdaq_closes(ticker: str, start: str, get_json=None) -> pd.Series:
    """Chiusure giornaliere da Nasdaq (seconda fonte, non rettificate per i dividendi) dall'inizio `start`."""
    import requests

    url = NASDAQ_URL.format(t=ticker.replace("-", ".").upper(), start=start)
    data = (get_json or (lambda u: requests.get(u, headers=NASDAQ_HEADERS, timeout=20).json()))(url)
    rows = ((data or {}).get("data") or {}).get("tradesTable", {}).get("rows") or []
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(r["date"]): float(str(r["close"]).replace("$", "").replace(",", "")) for r in rows})
    return s.sort_index()


def compare_prices(ours: pd.Series, other: pd.Series, days: int = 250, jump: float = 0.02) -> dict:
    """Rendimenti giornalieri degli ultimi `days` giorni comuni: quanti differiscono di più di `jump` (giorni con
    un salto in una sola fonte: split o dato sbagliato), la massima differenza e lo scarto dell'ultima chiusura.
    Le fonti rettificano i dividendi in modo diverso: per questo si confrontano i rendimenti, non i livelli."""
    both = pd.concat([ours.rename("ours"), other.rename("other")], axis=1).dropna().iloc[-(days + 1):]
    if len(both) < 20:
        return {"days": len(both), "bad_days": np.nan, "max_diff": np.nan, "last_diff": np.nan, "worst_day": None}
    r = both.pct_change().dropna()
    d = (r["ours"] - r["other"]).abs()
    return {"days": len(r), "bad_days": int((d > jump).sum()), "max_diff": float(d.max()),
            "worst_day": d.idxmax().date().isoformat(),
            "last_diff": float(both["ours"].iloc[-1] / both["other"].iloc[-1] - 1)}


def sample(tickers_by_sector: dict[str, list[str]], per_sector: int, must: list[str], seed: int = 7) -> list[str]:
    """Campione stratificato per settore (riproducibile) più i casi difficili da controllare sempre."""
    rng = np.random.default_rng(seed)
    out = list(dict.fromkeys(must))
    for sector in sorted(tickers_by_sector):
        pool = sorted(t for t in tickers_by_sector[sector] if t not in out)
        out += list(rng.choice(pool, size=min(per_sector, len(pool)), replace=False)) if pool else []
    return out
