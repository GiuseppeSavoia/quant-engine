"""
Fattori azionari classici, calcolati point-in-time su un'istantanea di MarketData.

Ogni fattore è orientato in modo che un valore PIÙ ALTO sia quello che la letteratura associa a
rendimenti futuri migliori (es. bassa volatilità = -volatilità). Così la ricerca e la combinazione
non devono ricordare il segno di ciascuno.

Valore (prezzo basso rispetto ai fondamentali):
  earnings_yield   utile netto / capitalizzazione
  fcf_yield        free cash flow / capitalizzazione
  book_to_market   patrimonio netto / capitalizzazione (solo patrimonio positivo)
  sales_to_price   ricavi / capitalizzazione
Qualità e investimenti (Novy-Marx, Fama-French):
  gross_profitability  margine lordo / attivo totale
  roa              utile netto / attivo totale
  roe              utile netto / patrimonio netto (solo patrimonio positivo)
  low_leverage     -(debito / attivo totale)
  low_accruals     -((utile netto - cash flow operativo) / attivo totale): utili "di cassa"
  low_asset_growth -(crescita dell'attivo totale): chi investe meno rende di più
Crescita:
  revenue_growth   crescita dei ricavi sull'anno precedente
Prezzo:
  mom_12_1         rendimento da 12 a 1 mese fa
  mom_6_1          rendimento da 6 a 1 mese fa
  reversal_1m      -(rendimento dell'ultimo mese): il breve periodo tende a invertire
  low_vol          -(volatilità annua dell'ultimo anno)
  low_beta         -(beta verso il benchmark nell'ultimo anno)
  small_size       -(log della capitalizzazione)

I bilanci usati sono gli ultimi 12 mesi all'ultimo trimestre già depositato (10-Q, o l'esercizio completo
se più recente), se non più vecchi di `max_statement_age_days` (altrimenti i fattori di bilancio restano
vuoti per quel titolo). Le crescite confrontano con lo stesso trimestre dell'anno prima.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Factor:
    key: str
    label: str
    group: str


FACTORS: list[Factor] = [
    Factor("earnings_yield", "Utili / prezzo", "Valore"),
    Factor("fcf_yield", "FCF / prezzo", "Valore"),
    Factor("book_to_market", "Patrimonio / prezzo", "Valore"),
    Factor("sales_to_price", "Ricavi / prezzo", "Valore"),
    Factor("gross_profitability", "Margine lordo / attivo", "Qualità"),
    Factor("roa", "ROA", "Qualità"),
    Factor("roe", "ROE", "Qualità"),
    Factor("low_leverage", "Basso debito", "Qualità"),
    Factor("low_accruals", "Utili di cassa", "Qualità"),
    Factor("low_asset_growth", "Bassa crescita dell'attivo", "Investimenti"),
    Factor("revenue_growth", "Crescita dei ricavi", "Crescita"),
    Factor("mom_12_1", "Momentum 12-1", "Prezzo"),
    Factor("mom_6_1", "Momentum 6-1", "Prezzo"),
    Factor("reversal_1m", "Inversione 1 mese", "Prezzo"),
    Factor("low_vol", "Bassa volatilità", "Rischio"),
    Factor("low_beta", "Basso beta", "Rischio"),
    Factor("small_size", "Piccola capitalizzazione", "Dimensione"),
]
FACTOR_LABELS = {f.key: f.label for f in FACTORS}


def _ratio(a: pd.Series, b: pd.Series, positive_denominator: bool = True) -> pd.Series:
    den = b.where(b > 0) if positive_denominator else b.where(b != 0)
    return a / den


def price_factors(data, tickers: list[str]) -> pd.DataFrame:
    """Fattori di prezzo per tutti i titoli in una volta (matrici, niente cicli per titolo)."""
    cols = [t for t in tickers if t in data.close.columns]
    c = data.recent_close(cols, TRADING_DAYS + 1)
    out = pd.DataFrame(index=pd.Index(tickers, name="ticker"))
    if len(c) < 30:
        return out
    last = c.iloc[-1]
    fresh = last.notna()  # titolo quotato il giorno dell'istantanea
    n = len(c)

    def back(k: int) -> pd.Series:
        return c.iloc[-k - 1] if n > k else pd.Series(np.nan, index=c.columns)

    out["mom_12_1"] = back(21) / back(TRADING_DAYS) - 1
    out["mom_6_1"] = back(21) / back(126) - 1
    out["reversal_1m"] = -(last / back(21) - 1)
    logret = np.log(c.where(c > 0)).diff().iloc[1:]
    enough = logret.count() >= 200
    out["low_vol"] = -(logret.std() * np.sqrt(TRADING_DAYS)).where(enough)
    b = data.benchmark
    if b and b in data.close:
        rb = np.log(data.recent_close([b], TRADING_DAYS + 1)[b]).diff().iloc[1:].reindex(logret.index)
        cov = logret.sub(logret.mean()).mul(rb - rb.mean(), axis=0).sum() / (logret.count() - 1)
        out["low_beta"] = -(cov / rb.var()).where(enough)
    return out.where(fresh.reindex(out.index, fill_value=False), np.nan)


def fundamental_factors(data, tickers: list[str], prices: pd.Series, max_age_days: int = 550) -> pd.DataFrame:
    rows = {}
    as_of = data.last_date
    for t in tickers:
        st = data.fundamentals(t)  # ultimi 12 mesi all'ultimo trimestre depositato
        price = prices.get(t, np.nan)
        if st.empty or not np.isfinite(price) or (as_of - st.index[-1]).days > max_age_days:
            continue
        cur = st.iloc[-1]
        prev = data.year_ago(st)  # stesso trimestre dell'anno prima: crescita su 12 mesi
        shares = data.last_shares(st)
        rows[t] = {
            "mcap": price * shares if np.isfinite(shares) and shares > 0 else np.nan,
            **{k: cur.get(k, np.nan) for k in ("net_income", "free_cash_flow", "revenue", "equity", "total_assets",
                                               "gross_profit", "operating_cash_flow", "total_debt")},
            "prev_assets": prev.get("total_assets", np.nan), "prev_revenue": prev.get("revenue", np.nan),
        }
    f = pd.DataFrame.from_dict(rows, orient="index", dtype=float)
    out = pd.DataFrame(index=pd.Index(tickers, name="ticker"))
    if f.empty:
        return out
    mcap, assets, equity = f["mcap"], f["total_assets"], f["equity"]
    out["earnings_yield"] = _ratio(f["net_income"], mcap)
    out["fcf_yield"] = _ratio(f["free_cash_flow"], mcap)
    out["book_to_market"] = _ratio(equity.where(equity > 0), mcap)
    out["sales_to_price"] = _ratio(f["revenue"], mcap)
    out["gross_profitability"] = _ratio(f["gross_profit"], assets)
    out["roa"] = _ratio(f["net_income"], assets)
    out["roe"] = _ratio(f["net_income"], equity)
    out["low_leverage"] = -_ratio(f["total_debt"], assets)
    out["low_accruals"] = -_ratio(f["net_income"] - f["operating_cash_flow"], assets)
    out["low_asset_growth"] = -(_ratio(assets, f["prev_assets"]) - 1)
    out["revenue_growth"] = _ratio(f["revenue"], f["prev_revenue"]) - 1
    out["small_size"] = -np.log(mcap.where(mcap > 0))
    return out


def compute_factors(data, tickers: list[str], max_age_days: int = 550) -> pd.DataFrame:
    """Tutti i fattori dei titoli indicati all'ultima data dell'istantanea. Righe = ticker."""
    pf = price_factors(data, tickers)
    cols = [t for t in tickers if t in data.close.columns]
    prices = data.recent_close(cols, 1).iloc[-1] if len(data.close) else pd.Series(dtype=float)
    ff = fundamental_factors(data, tickers, prices, max_age_days)
    out = pf.join(ff, how="left")
    return out.reindex(columns=[f.key for f in FACTORS]).replace([np.inf, -np.inf], np.nan)


def composite_score(factors: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Media pesata dei ranghi percentuali (0-1) dei fattori. Un fattore mancante per un titolo
    non conta: i pesi si rinormalizzano su quelli disponibili."""
    w = {k: v for k, v in weights.items() if v > 0 and k in factors}
    if not w:
        return pd.Series(np.nan, index=factors.index)
    ranks = factors[list(w)].rank(pct=True)
    weight = pd.Series(w)
    num = ranks.mul(weight).sum(axis=1, min_count=1)
    den = ranks.notna().mul(weight).sum(axis=1)
    return (num / den.where(den > 0)).rename("composite")
