"""
Costo del capitale alla Damodaran, passo per passo.

1. Beta di settore ("bottom-up"): il beta di regressione di un singolo titolo è molto rumoroso. Si calcola il
   beta di ogni azienda (rendimenti settimanali degli ultimi `beta_years` anni contro il benchmark), lo si
   "depura" dal debito (beta senza debito = beta / (1 + (1 - t) D/E)), si prende la mediana del sotto-settore
   (o del settore, se il sotto-settore ha meno di `min_industry_peers` aziende), si corregge per la cassa e lo si
   riadatta al debito dell'azienda: beta = beta senza debito x (1 + (1 - t) D/E).
2. Costo del debito da un rating sintetico: copertura degli interessi (EBIT / interessi) -> rating -> spread sul
   tasso privo di rischio (tabella `valuation.synthetic_ratings` in config.yaml, aggiornabile ogni anno).
3. Premio per il rischio azionario implicito: il rendimento che eguaglia il valore dell'S&P 500 ai flussi di cassa
   che restituisce agli azionisti (dividendi + riacquisti), che crescono a `erp_growth` per 5 anni e poi al tasso
   privo di rischio. Premio = quel rendimento - tasso privo di rischio. Damodaran lo pubblica ogni mese; qui lo si
   ricava dai dati SEC dei membri dell'indice.
4. Costo dell'equity = tasso privo di rischio + beta x premio; WACC con i pesi di mercato (capitalizzazione e
   debito) e il costo del debito dopo le tasse all'aliquota marginale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_RATINGS = [  # copertura minima, rating, spread (ordine di grandezza delle tabelle di Damodaran, grandi aziende)
    [8.5, "AAA", 0.0059], [6.5, "AA", 0.0078], [5.5, "A+", 0.0108], [4.25, "A", 0.0122], [3.0, "A-", 0.0133],
    [2.5, "BBB", 0.0171], [2.25, "BB+", 0.0231], [2.0, "BB", 0.0277], [1.75, "B+", 0.0405], [1.5, "B", 0.0486],
    [1.25, "B-", 0.0594], [0.8, "CCC", 0.0946], [0.65, "CC", 0.12], [0.2, "C", 0.16], [-1e9, "D", 0.19],
]


def regression_betas(close: pd.DataFrame, benchmark: str, years: int = 2) -> pd.Series:
    """Beta di ogni colonna contro il benchmark su rendimenti settimanali (venerdì) degli ultimi `years` anni."""
    if benchmark not in close:
        return pd.Series(dtype=float)
    recent = close[close.index >= close.index[-1] - pd.DateOffset(years=years)]
    weekly = recent.resample("W-FRI").last()
    rets = np.log(weekly.where(weekly > 0)).diff().iloc[1:]
    m = rets[benchmark]
    enough = rets.count() >= 52 * years * 0.8
    cov = rets.sub(rets.mean()).mul(m - m.mean(), axis=0).sum() / (rets.count() - 1)
    return (cov / m.var()).where(enough).drop(benchmark, errors="ignore")


def unlevered(beta: float, debt: float, equity: float, tax: float) -> float:
    return beta / (1 + (1 - tax) * max(debt, 0) / equity) if equity > 0 else np.nan


def relevered(beta_u: float, debt: float, equity: float, tax: float) -> float:
    return beta_u * (1 + (1 - tax) * max(debt, 0) / equity) if equity > 0 else np.nan


# Settori ICB delle europee (Wikipedia del FTSE MIB) -> settori GICS, SOLO per i gruppi del costo del capitale: altrove
# il nome originale serve (es. "Banks" decide il profilo banca nel giudizio). Le società dell'Euro Stoxx 50 hanno già
# nomi GICS. Buzzi (cemento) = Materials come in GICS, non Industrials.
ICB_TO_GICS = {
    "Banks": "Financials", "Financial Services": "Financials", "Insurance": "Financials",
    "Industrial Goods and Services": "Industrials", "Aerospace": "Industrials", "Shipbuilding": "Industrials",
    "Construction and Materials": "Materials", "Chemicals": "Materials", "Basic Resources": "Materials",
    "Technology": "Information Technology", "Telecommunications": "Communication Services",
    "Communication": "Communication Services", "Media": "Communication Services",
    "Automobiles and Parts": "Consumer Discretionary", "Consumer Products and Services": "Consumer Discretionary",
    "Gambling": "Consumer Discretionary", "Retail": "Consumer Discretionary", "Travel and Leisure": "Consumer Discretionary",
    "Food, Beverage and Tobacco": "Consumer Staples", "Personal Care, Drug and Grocery Stores": "Consumer Staples",
}


def industry_betas(table: pd.DataFrame, tax: float, min_peers: int = 5, reference=None,
                   no_cash_groups=()) -> pd.DataFrame:
    """`table`: una riga per azienda con beta, debt, equity (capitalizzazione), cash, sector, sub_industry.
    Restituisce per ogni azienda il gruppo usato, quante aziende ha e il beta senza debito del gruppo corretto per
    la cassa. Le mediane si calcolano solo sulle aziende di `reference` (None = tutte): le altre prendono il gruppo
    del loro sotto-settore o settore, se esiste, altrimenti nessuno (chi chiama usa la mediana di tutti).
    `no_cash_groups`: gruppi senza correzione per la cassa (banche: la cassa è materia prima, non un'attività a beta
    zero accanto al business; Damodaran non la toglie per le finanziarie)."""
    t = table.copy()
    t["beta_u"] = [unlevered(b, d, e, tax) for b, d, e in zip(t["beta"], t["debt"], t["equity"])]
    ref = t if reference is None else t[t.index.isin(list(reference))]
    sub_n = ref.groupby("sub_industry")["beta_u"].count()
    big = set(sub_n[sub_n >= min_peers].index)
    t["group"] = np.where(t["sub_industry"].isin(big), t["sub_industry"], t["sector"])
    ref = t.loc[ref.index]
    stats = ref.groupby("group").agg(n=("beta_u", "count"), beta_u=("beta_u", "median"),
                                     cash=("cash", "sum"), debt=("debt", "sum"), equity=("equity", "sum"))
    # beta delle sole attività operative: la cassa ha beta zero e abbassa il beta medio dell'azienda
    stats["beta_u_ops"] = stats["beta_u"] / (1 - stats["cash"] / (stats["equity"] + stats["debt"]))
    keep = stats.index.isin(list(no_cash_groups))
    stats.loc[keep, "beta_u_ops"] = stats.loc[keep, "beta_u"]
    t.loc[~t["group"].isin(stats.index), "group"] = None
    return t[["group"]].join(stats[["n", "beta_u", "beta_u_ops"]], on="group")


def synthetic_rating(coverage: float, table: list | None = None) -> tuple[str, float]:
    table = table or DEFAULT_RATINGS
    if not np.isfinite(coverage):
        return table[5][1], table[5][2]  # copertura ignota: BBB (investment grade minimo)
    for min_cov, rating, spread in table:
        if coverage >= min_cov:
            return rating, spread
    return table[-1][1], table[-1][2]


def implied_erp(cash_yield: float, risk_free: float, growth: float = 0.05, years: int = 5) -> float:
    """Premio implicito: r tale che 1 = somma dei flussi (rendimento in cassa che cresce a `growth` per `years`
    anni, poi al tasso privo di rischio) scontati a r. Restituisce r - risk_free."""
    if not (np.isfinite(cash_yield) and cash_yield > 0):
        return np.nan

    def pv(r):
        flows = [cash_yield * (1 + growth) ** t for t in range(1, years + 1)]
        value = sum(f / (1 + r) ** t for t, f in enumerate(flows, start=1))
        return value + flows[-1] * (1 + risk_free) / ((r - risk_free) * (1 + r) ** years)

    lo, hi = risk_free + 1e-4, 0.5
    if pv(hi) > 1 or pv(lo) < 1:
        return np.nan
    for _ in range(100):
        mid = (lo + hi) / 2
        if pv(mid) > 1:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 - risk_free


def wacc(risk_free: float, beta: float, erp: float, spread: float, debt: float, equity: float,
         marginal_tax: float) -> dict:
    ke = risk_free + beta * erp
    kd = risk_free + spread
    total = equity + max(debt, 0)
    we = equity / total if total > 0 else 1.0
    return {"cost_of_equity": ke, "cost_of_debt": kd, "cost_of_debt_after_tax": kd * (1 - marginal_tax),
            "weight_equity": we, "weight_debt": 1 - we,
            "wacc": we * ke + (1 - we) * kd * (1 - marginal_tax)}
