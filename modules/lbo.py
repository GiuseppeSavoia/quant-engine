"""
Test LBO e distanza dal default (Merton): due modi di guardare un'azienda presi dai corsi di private equity, M&A,
Black-Scholes e dalla contabilità di Damodaran.

Test LBO ("un fondo di private equity potrebbe comprarla a questo prezzo?")
    Un acquirente finanziario paga la capitalizzazione di oggi più un premio, rifinanzia il debito esistente e
    prende a prestito il massimo che l'EBITDA sostiene (multiplo massimo, copertura minima degli interessi, quota
    minima di capitale proprio). Per `years` anni tutto il flusso di cassa ripaga il debito; all'uscita rivende al
    multiplo EV/EBITDA di mercato di oggi (mai sopra `exit_multiple_max`). Ipotesi prudenti e fissate a priori:
    EBITDA e flusso di cassa FERMI (nessuna crescita), nessun miglioramento dei margini, nessuna commissione.
    Ne escono il rendimento annuo del fondo (IRR) e il prezzo per azione più alto che darebbe `target_irr`.
    Un IRR alto al prezzo di oggi = l'azienda costa poco rispetto alla cassa che produce e al debito che regge
    (il "pavimento" del prezzo usato dalle banche d'affari). Con EBITDA fermo e uscita al multiplo di oggi il
    risultato somiglia a un rendimento del FCF sull'EV con leva: è soprattutto una lente di lettura, non un segnale
    provato (vedi tools/metric_research.py).

Distanza dal default (Merton 1974, versione "naive" di Bharath e Shumway 2008)
    Il capitale di un'azienda indebitata è un'opzione call sull'attivo con prezzo d'esercizio = debito (Damodaran,
    valutazione con opzioni; Black-Scholes). Quante deviazioni standard separano il valore dell'attivo dal debito tra
    un anno: sotto 1,5-2 il mercato prezza un rischio di insolvenza serio; probabilità = N(-DD).
    Debito = debito finanziario + leasing (Damodaran: i leasing sono debito).

Niente per banche, assicurazioni e REIT: il loro "debito" (depositi, riserve tecniche, mutui sugli immobili) non si
legge così. Un dato mancante lascia il risultato vuoto, mai "sicuro".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Parametri fissati PRIMA di guardare qualunque risultato (2026-11-23). Si cambiano con un blocco `lbo:` in
# config.yaml (che entra nell'impronta della configurazione solo se presente).
DEFAULTS = {
    "premium": 0.30,            # premio sul prezzo di borsa (tipico delle offerte pubbliche USA: 25-40%)
    "max_leverage": 6.0,        # debito massimo / EBITDA (LBO tipici: 5-6x)
    "min_coverage": 2.0,        # EBITDA / interessi minimo (vincolo dei finanziatori, corso M&A)
    "min_equity_share": 0.30,   # quota minima di capitale del fondo sul prezzo totale
    "spread": 0.035,            # costo del debito = Treasury 10 anni del giorno + spread (prestiti a leva)
    "rate_fallback": 0.042,     # Treasury se il dato manca
    "years": 5,                 # durata dell'investimento
    "target_irr": 0.20,         # rendimento richiesto da un fondo di private equity
    "exit_multiple_max": 12.0,  # tetto al multiplo EV/EBITDA di uscita
    "dd_cap": 10.0,             # distanza dal default massima (debito nullo = lontanissimo)
}


def settings(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update((cfg or {}).get("lbo", {}) or {})
    return out


def _f(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return np.nan
    return x if math.isfinite(x) else np.nan


@dataclass
class LBOResult:
    ok: bool
    reason: str = ""
    rate: float = np.nan
    ebitda: float = np.nan
    ufcf: float = np.nan
    entry_multiple: float = np.nan     # EV/EBITDA di mercato di oggi (senza premio)
    exit_multiple: float = np.nan
    purchase_equity: float = np.nan    # capitalizzazione x (1 + premio)
    refinanced_debt: float = np.nan    # debito esistente - cassa
    uses: float = np.nan
    new_debt: float = np.nan
    sponsor_equity: float = np.nan
    exit_ev: float = np.nan
    exit_debt: float = np.nan
    exit_equity: float = np.nan
    irr: float = np.nan
    max_price: float = np.nan          # prezzo per azione che dà target_irr
    price: float = np.nan
    shares: float = np.nan
    tax: float = np.nan
    years: list[dict] = field(default_factory=list)

    @property
    def upside(self) -> float:
        return self.max_price / self.price - 1 if self.ok and self.price > 0 and np.isfinite(self.max_price) else np.nan


def _run(ebitda, ufcf, offer_equity, net_debt, market_ev, tax, rate, p) -> dict:
    uses = offer_equity + net_debt
    debt0 = min(p["max_leverage"] * ebitda, ebitda / (p["min_coverage"] * rate),
                (1 - p["min_equity_share"]) * uses)
    debt0 = max(debt0, 0.0)
    equity0 = uses - debt0
    debt, rows = debt0, []
    for t in range(1, int(p["years"]) + 1):
        interest = rate * max(debt, 0.0)
        lfcf = ufcf - interest * (1 - tax)
        start = debt
        debt = debt - lfcf  # tutto il flusso ripaga il debito (negativo = cassa accumulata)
        rows.append({"anno": t, "debito_inizio": start, "interessi": interest, "flusso_levered": lfcf,
                     "debito_fine": debt})
    exit_multiple = min(market_ev / ebitda, p["exit_multiple_max"])
    exit_ev = exit_multiple * ebitda
    exit_equity = exit_ev - debt
    if equity0 <= 0:
        irr = np.nan
    elif exit_equity <= 0:
        irr = -1.0
    else:
        irr = (exit_equity / equity0) ** (1 / p["years"]) - 1
    return {"uses": uses, "debt0": debt0, "equity0": equity0, "exit_multiple": exit_multiple, "exit_ev": exit_ev,
            "exit_debt": debt, "exit_equity": exit_equity, "irr": irr, "rows": rows}


def lbo(*, ebitda, fcf, interest_expense, debt, cash, market_cap, shares, tax_rate, rate, financial=False,
        cfg: dict | None = None) -> LBOResult:
    """Test LBO alle ipotesi di `settings(cfg)`. `fcf` = free cash flow (dopo gli interessi pagati: il flusso per un
    acquirente senza debito si ottiene riaggiungendo gli interessi al netto delle tasse). `rate` = costo del debito."""
    p = settings(cfg)
    ebitda, fcf, mcap, shares = _f(ebitda), _f(fcf), _f(market_cap), _f(shares)
    interest = abs(_f(interest_expense)) if np.isfinite(_f(interest_expense)) else 0.0
    debt_n = _f(debt) if np.isfinite(_f(debt)) else 0.0
    cash_n = _f(cash) if np.isfinite(_f(cash)) else 0.0
    tax = _f(tax_rate) if np.isfinite(_f(tax_rate)) else 0.21
    rate = _f(rate) if np.isfinite(_f(rate)) else p["rate_fallback"] + p["spread"]
    if financial:
        return LBOResult(False, "banche, assicurazioni e REIT: il test LBO non si applica")
    if not (np.isfinite(ebitda) and ebitda > 0):
        return LBOResult(False, "EBITDA mancante o negativo")
    if not np.isfinite(fcf):
        return LBOResult(False, "free cash flow mancante")
    if not (np.isfinite(mcap) and mcap > 0 and np.isfinite(shares) and shares > 0):
        return LBOResult(False, "prezzo o numero di azioni mancante")
    ufcf = fcf + interest * (1 - tax)
    net_debt = debt_n - cash_n
    market_ev = mcap + net_debt
    if market_ev <= 0:
        return LBOResult(False, "enterprise value negativo (più cassa che capitalizzazione e debito)")
    price = mcap / shares
    offer = mcap * (1 + p["premium"])
    r = _run(ebitda, ufcf, offer, net_debt, market_ev, tax, rate, p)

    # prezzo d'offerta (per azione) che dà esattamente target_irr: l'IRR scende al salire del prezzo
    def irr_at(px: float) -> float:
        v = _run(ebitda, ufcf, px * shares, net_debt, market_ev, tax, rate, p)["irr"]
        return v if np.isfinite(v) else np.inf  # capitale del fondo nullo = rendimento illimitato
    lo, hi = 0.0, price * 20
    max_price = np.nan
    if irr_at(hi) < p["target_irr"] < irr_at(lo if lo > 0 else 1e-9 * price):
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if irr_at(mid) > p["target_irr"] else (lo, mid)
        max_price = (lo + hi) / 2
    elif irr_at(hi) >= p["target_irr"]:
        max_price = hi
    return LBOResult(True, rate=rate, ebitda=ebitda, ufcf=ufcf, entry_multiple=market_ev / ebitda,
                     exit_multiple=r["exit_multiple"], purchase_equity=offer, refinanced_debt=net_debt,
                     uses=r["uses"], new_debt=r["debt0"], sponsor_equity=r["equity0"], exit_ev=r["exit_ev"],
                     exit_debt=r["exit_debt"], exit_equity=r["exit_equity"], irr=r["irr"], max_price=max_price,
                     price=price, shares=shares, tax=tax, years=r["rows"])


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def distance_to_default(market_cap, debt, leases, volatility, ret_1y, financial=False,
                        cap: float = DEFAULTS["dd_cap"]) -> float:
    """DD di Merton "naive" (Bharath e Shumway 2008), orizzonte 1 anno. Vuota se manca il debito o la volatilità."""
    e, f, sv, mu = _f(market_cap), _f(debt), _f(volatility), _f(ret_1y)
    if financial or not (np.isfinite(e) and e > 0 and np.isfinite(f) and f >= 0 and np.isfinite(sv) and sv > 0):
        return np.nan
    f += _f(leases) if np.isfinite(_f(leases)) and _f(leases) > 0 else 0.0
    if f <= 0:
        return cap
    mu = math.log1p(mu) if np.isfinite(mu) and mu > -1 else 0.0  # rendimento dell'ultimo anno, logaritmico
    sd = 0.05 + 0.25 * sv
    v = e + f
    s_v = e / v * sv + f / v * sd
    dd = (math.log(v / f) + mu - 0.5 * s_v ** 2) / s_v
    return float(np.clip(dd, -cap, cap))


def default_probability(dd: float) -> float:
    return _norm_cdf(-dd) if np.isfinite(_f(dd)) else np.nan


def is_financial(cur, latest) -> bool:
    """Banca, assicurazione o REIT riconosciuti dalle voci di bilancio (come in metrics.sector_metrics)."""
    g = lambda row, k: _f(row.get(k, np.nan)) if row is not None else np.nan  # noqa: E731
    # margine di interesse lo dichiarano anche le industriali: banca solo se c'è anche "noninterest expense"
    bank = np.isfinite(g(cur, "net_interest_income")) and np.isfinite(g(cur, "noninterest_expense"))
    if bank or np.isfinite(g(cur, "premiums")):
        return True
    re_assets, assets = g(latest, "real_estate_assets"), g(latest, "total_assets")
    return bool(np.isfinite(re_assets) and np.isfinite(assets) and assets > 0 and re_assets / assets >= 0.30)


def debt_rate(data, p: dict) -> float:
    """Costo del debito alla data di `data`: Treasury 10 anni (ultimo valore noto, in %) + spread."""
    t = getattr(data, "risk_free_ticker", None)
    try:
        if t and t in data.close:
            s = data.close[t].dropna()
            if not s.empty:
                return float(s.iloc[-1]) / 100 + p["spread"]
    except Exception:  # noqa: BLE001
        pass
    return p["rate_fallback"] + p["spread"]


# settori dove il test va letto con cautela (la scheda lo dice); l'esclusione vera resta quella delle voci di bilancio,
# uguale per catalogo, scheda e prova storica
CAUTION_SECTORS = {"Financials", "Real Estate", "Banks", "Insurance", "Financial Services",
                   "Real Estate Investment Trusts"}


def lbo_for(data, ticker: str, price: float, cfg: dict | None = None, max_age_days: int = 550) -> LBOResult:
    """Test LBO di `ticker` con gli stessi dati di metrics.fundamental_metrics (ultimi 12 mesi, patrimoniale più
    recente, azioni da `last_shares`), al prezzo dato e al costo del debito della data dei dati."""
    p = settings(cfg)
    st = data.fundamentals(ticker)
    if st.empty or data.last_date is None or (data.last_date - st.index[-1]).days > max_age_days:
        return LBOResult(False, "bilanci mancanti o troppo vecchi")
    cur, latest = st.iloc[-1], st.ffill().iloc[-1]
    g = lambda row, k: _f(row.get(k, np.nan))  # noqa: E731
    shares = data.last_shares(st)
    price = _f(price)
    mcap = price * shares if np.isfinite(price) and np.isfinite(_f(shares)) and shares > 0 else np.nan
    ebit, da = g(cur, "operating_income"), g(cur, "depreciation")
    tax = g(cur, "tax_provision") / g(cur, "pretax_income") if g(cur, "pretax_income") > 0 else np.nan
    tax = float(np.clip(tax, 0, 0.35)) if np.isfinite(tax) else 0.21
    financial = is_financial(cur, latest)
    return lbo(ebitda=ebit + da, fcf=g(cur, "free_cash_flow"), interest_expense=g(cur, "interest_expense"),
               debt=g(latest, "total_debt"), cash=g(latest, "cash"), market_cap=mcap, shares=shares, tax_rate=tax,
               rate=debt_rate(data, p), financial=financial, cfg=cfg)
