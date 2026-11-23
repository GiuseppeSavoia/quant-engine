"""
Valutazione intrinseca (approccio Damodaran semplificato).

- Beta: regressione dei rendimenti del titolo su quelli del benchmark.
- WACC: CAPM per il costo dell'equity, interessi/debito per il costo del debito.
- DCF per azione: FCF proiettato per N anni con crescita che sfuma linearmente
  verso quella terminale, valore terminale di Gordon, meno il debito netto,
  diviso per il numero di azioni.
- Reverse DCF: la crescita che il prezzo attuale "sconta" (stesso modello, risolto per g).

Limite noto: i bilanci sono nella valuta di rendicontazione dell'azienda. Per titoli
esteri quotati in USD (ADR) il confronto con il prezzo non è omogeneo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from modules.technical import log_returns

logger = logging.getLogger(__name__)


@dataclass
class Valuation:
    ticker: str
    price: float
    fair_value: float | None = None
    margin: float | None = None          # fair_value / price - 1
    wacc: float | None = None
    cost_of_equity: float | None = None
    beta: float | None = None
    growth: float | None = None
    implied_growth: float | None = None
    fcf_base: float | None = None
    net_debt: float | None = None       # debito - cassa (ultimo bilancio)
    shares: float | None = None
    notes: list[str] = field(default_factory=list)


class FundamentalAnalyzer:
    def __init__(self, cfg_valuation: dict):
        self.cfg = cfg_valuation
        self.erp = cfg_valuation["equity_risk_premium"]
        self.terminal_growth = cfg_valuation["terminal_growth"]
        self.years = int(cfg_valuation["projection_years"])
        self.wacc_floor = cfg_valuation["wacc_floor"]
        self.max_fcf_lag = pd.Timedelta(days=int(cfg_valuation.get("max_fcf_lag_days", 400)))

    # ------------------------------------------------------------ mattoni
    @staticmethod
    def estimate_beta(stock_prices: pd.Series, market_prices: pd.Series, window: int = 504) -> float:
        return FundamentalAnalyzer.beta_from_returns(log_returns(stock_prices), log_returns(market_prices), window)

    @staticmethod
    def beta_from_returns(stock_ret: pd.Series, market_ret: pd.Series, window: int = 504) -> float:
        s, m = stock_ret.align(market_ret, join="inner")
        s, m = s.to_numpy()[-window:], m.to_numpy()[-window:]
        ok = np.isfinite(s) & np.isfinite(m)
        s, m = s[ok], m[ok]
        if len(s) < 60 or np.var(m) == 0:
            return 1.0
        beta = np.cov(s, m, ddof=1)[0, 1] / np.var(m, ddof=1)
        return float(np.clip(beta, 0.3, 3.0))

    def cost_of_equity(self, risk_free: float, beta: float) -> float:
        return risk_free + beta * self.erp

    def wacc(self, market_cap: float, total_debt: float, cost_of_equity: float,
             cost_of_debt: float, tax_rate: float) -> float:
        """WACC = E/V * Re + D/V * Rd * (1 - T), con un minimo prudenziale."""
        if market_cap <= 0:
            raise ValueError("market cap non positiva")
        total_debt = max(total_debt, 0.0)
        value = market_cap + total_debt
        raw = market_cap / value * cost_of_equity + total_debt / value * cost_of_debt * (1 - tax_rate)
        return max(raw, self.wacc_floor)

    def estimate_growth(self, fcf_history: pd.Series) -> tuple[float, str]:
        """CAGR del FCF tra il primo e l'ultimo anno positivi, limitato a [min, max]."""
        fcf = fcf_history.dropna()
        fcf = fcf[fcf > 0]
        lo, hi, default = self.cfg["min_growth"], self.cfg["max_growth"], self.cfg["default_growth"]
        if len(fcf) < 2:
            return default, "crescita di default (storico FCF insufficiente)"
        years = (fcf.index[-1] - fcf.index[0]).days / 365.25
        if years < 0.9:
            return default, "crescita di default (storico FCF troppo breve)"
        cagr = (fcf.iloc[-1] / fcf.iloc[0]) ** (1 / years) - 1
        return float(np.clip(cagr, lo, hi)), f"crescita da CAGR FCF {cagr:.1%} su {years:.1f} anni"

    def dcf_per_share(self, fcf: float, growth: float, wacc: float, net_debt: float, shares: float,
                      terminal_growth: float | None = None, years: int | None = None) -> float:
        g_t = self.terminal_growth if terminal_growth is None else terminal_growth
        n = self.years if years is None else years
        if wacc <= g_t:
            raise ValueError(f"WACC ({wacc:.2%}) deve superare la crescita terminale ({g_t:.2%})")
        if shares <= 0:
            raise ValueError("numero di azioni non positivo")
        rates = np.linspace(growth, g_t, n) if n > 1 else np.array([growth])
        flows = fcf * np.cumprod(1 + rates)
        discount = (1 + wacc) ** np.arange(1, n + 1)
        pv_flows = float((flows / discount).sum())
        terminal = flows[-1] * (1 + g_t) / (wacc - g_t)
        enterprise_value = pv_flows + terminal / discount[-1]
        return (enterprise_value - net_debt) / shares

    def implied_growth(self, price: float, fcf: float, wacc: float, net_debt: float, shares: float,
                       lo: float = -0.30, hi: float = 0.60, terminal_growth: float | None = None,
                       years: int | None = None) -> float | None:
        """Reverse DCF: crescita iniziale che rende il valore per azione uguale al prezzo.
        Il valore è crescente in g, quindi la bisezione converge. None se fuori intervallo."""
        f = lambda g: self.dcf_per_share(fcf, g, wacc, net_debt, shares, terminal_growth, years) - price  # noqa: E731
        f_lo, f_hi = f(lo), f(hi)
        if f_lo > 0 or f_hi < 0:
            return None
        for _ in range(100):
            mid = (lo + hi) / 2
            if f(mid) < 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-6:
                break
        return (lo + hi) / 2

    @staticmethod
    def fcf_history(data, ticker: str) -> pd.Series:
        """FCF degli esercizi completi, più gli ultimi 12 mesi se più recenti dell'ultimo esercizio.
        (Solo l'ultimo TTM: quelli intermedi si sovrappongono agli esercizi e conterebbero due volte.)"""
        annual = data.statements(ticker)
        fcf = annual["free_cash_flow"].dropna() if "free_cash_flow" in annual else pd.Series(dtype=float)
        ttm = data.ttm_statements(ticker)
        if "free_cash_flow" in ttm:
            last = ttm["free_cash_flow"].dropna().tail(1)
            if not last.empty and (fcf.empty or last.index[-1] > fcf.index[-1]):
                fcf = pd.concat([fcf, last])
        return fcf

    # ------------------------------------------------------------ valutazione
    def value(self, ticker: str, data) -> Valuation:
        prices = data.prices(ticker)
        price = float(prices.iloc[-1]) if not prices.empty else float("nan")
        val = Valuation(ticker=ticker, price=price)
        st = data.fundamentals(ticker)  # esercizi completi + ultimi 12 mesi dai trimestrali
        if st.empty or "free_cash_flow" not in st or "shares" not in st:
            val.notes.append("bilanci mancanti: DCF non calcolabile")
            return val
        if not np.isfinite(price) or price <= 0:
            val.notes.append("prezzo non disponibile")
            return val

        latest = st.ffill().iloc[-1].to_dict()
        shares = data.last_shares(st)
        if np.isfinite(shares) and shares > 0:
            # ingredienti del DCF anche quando il DCF non si può fare (servono al DCF regolabile della webapp)
            total_debt, cash = _num(latest, "total_debt", 0.0), _num(latest, "cash", 0.0)
            net_debt = total_debt - cash
            coe, wacc, beta = self._discount_rate(ticker, data, latest, price, shares, total_debt)
            val.net_debt, val.shares, val.wacc, val.cost_of_equity, val.beta = net_debt, shares, wacc, coe, beta
        fcf_hist = self.fcf_history(data, ticker)
        if not fcf_hist.empty and st.index[-1] - fcf_hist.index[-1] > self.max_fcf_lag:
            # es. l'azienda ha cambiato la voce del capex: meglio nessun DCF che un DCF su dati di anni fa
            val.notes.append(f"free cash flow non aggiornato (ultimo esercizio {fcf_hist.index[-1].year}, "
                             f"bilanci fino al {st.index[-1].year}): DCF non calcolato")
            return val
        fcf_base = float(fcf_hist.tail(3).mean()) if not fcf_hist.empty else float("nan")
        val.fcf_base = fcf_base
        if not np.isfinite(shares) or shares <= 0:
            val.notes.append("numero di azioni non disponibile")
            return val
        if not np.isfinite(fcf_base) or fcf_base <= 0:
            val.notes.append("FCF medio non positivo: DCF non applicabile")
            return val

        wacc, net_debt = val.wacc, val.net_debt
        growth, growth_note = self.estimate_growth(fcf_hist)
        val.notes.append(growth_note)
        val.growth = growth
        try:
            fair = self.dcf_per_share(fcf_base, growth, wacc, net_debt, shares)
        except ValueError as exc:
            val.notes.append(str(exc))
            return val

        val.fair_value = fair
        val.margin = fair / price - 1
        val.implied_growth = self.implied_growth(price, fcf_base, wacc, net_debt, shares)
        return val

    def _discount_rate(self, ticker: str, data, latest: dict, price: float, shares: float,
                       total_debt: float) -> tuple[float, float, float]:
        """Costo dell'equity (CAPM), WACC e beta."""
        rf = data.risk_free(self.cfg["risk_free_fallback"])
        bench_ret = data.returns(data.benchmark) if data.benchmark else pd.Series(dtype=float)
        beta = (self.beta_from_returns(data.returns(ticker), bench_ret, self.cfg["beta_window_days"])
                if not bench_ret.empty else 1.0)

        interest = abs(_num(latest, "interest_expense"))
        cost_of_debt = interest / total_debt if total_debt > 0 and np.isfinite(interest) else np.nan
        cost_of_debt = float(np.clip(cost_of_debt, 0.02, 0.12)) if np.isfinite(cost_of_debt) else rf + 0.015

        pretax = _num(latest, "pretax_income")
        tax = _num(latest, "tax_provision")
        tax_rate = tax / pretax if np.isfinite(pretax) and pretax > 0 and np.isfinite(tax) else np.nan
        tax_rate = float(np.clip(tax_rate, 0.0, 0.35)) if np.isfinite(tax_rate) else self.cfg["default_tax_rate"]

        coe = self.cost_of_equity(rf, beta)
        return coe, self.wacc(price * shares, total_debt, coe, cost_of_debt, tax_rate), beta


def _num(row: dict, key: str, default: float = float("nan")) -> float:
    """Valore numerico da una riga di bilancio, con default se assente o NaN."""
    try:
        value = float(row.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default
