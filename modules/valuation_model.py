"""
Valutazione "dalla storia ai numeri", alla Damodaran: il flusso di cassa operativo (FCFF) costruito da poche leve.

Leve (per ogni scenario):
  growth            crescita annua dei ricavi negli anni 1-5; dal 6 al 10 scende in linea retta fino a
                    `terminal_growth`
  target_margin     margine operativo (EBIT / ricavi) a cui si arriva in `margin_years` anni, partendo da quello
                    di oggi, e su cui si resta
  sales_to_capital  ricavi in più per ogni unità di capitale investita (dollaro, euro...): reinvestimento
                    dell'anno = aumento dei ricavi / questo
  wacc              costo del capitale negli anni 1-5; dal 6 al 10 va in linea retta a `terminal_wacc`
  terminal_growth   crescita per sempre dopo il decimo anno (non può superare il tasso privo di rischio)
  terminal_roic     rendimento sul capitale nella fase stabile: reinvestimento = crescita / ROIC. Uguale al
                    costo del capitale = la crescita non crea valore (l'ipotesi prudente di Damodaran); più alto =
                    vantaggio competitivo duraturo
  tax_rate          aliquota sull'utile operativo
  failure_prob      probabilità che l'azienda non sopravviva; in quel caso si recupera `distress_proceeds` x il
                    capitale investito di oggi

Anno t: ricavi, margine, EBIT, NOPAT = EBIT x (1 - tasse) (niente tasse sulle perdite), reinvestimento,
FCFF = NOPAT - reinvestimento, sconto al costo del capitale cumulato. Valore finale = FCFF dell'anno 11 /
(costo del capitale finale - crescita). Valore dell'equity = valore operativo + cassa - debito.

Il motore è vettoriale: ogni leva può essere un array (una valutazione per elemento), così la simulazione
Monte Carlo fa migliaia di valutazioni in una volta.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

YEARS = 10
HIGH_GROWTH_YEARS = 5
LEVER_LABELS = {
    "growth": "Crescita dei ricavi (anni 1-5)",
    "target_margin": "Margine operativo obiettivo",
    "margin_years": "Anni per arrivarci",
    "sales_to_capital": "Ricavi per unità di capitale investito",
    "wacc": "Costo del capitale (anni 1-5)",
    "terminal_wacc": "Costo del capitale finale",
    "terminal_growth": "Crescita per sempre",
    "terminal_roic": "ROIC nella fase stabile",
    "tax_rate": "Aliquota fiscale",
    "failure_prob": "Probabilità di fallimento",
}
PCT_LEVERS = {"growth", "target_margin", "wacc", "terminal_wacc", "terminal_growth", "terminal_roic", "tax_rate",
              "failure_prob"}
SIMULATED = ["growth", "target_margin", "sales_to_capital", "wacc"]  # le leve che la simulazione fa variare


@dataclass
class Base:
    """Il punto di partenza, dai bilanci (ultimi 12 mesi) e dal prezzo di oggi."""
    revenue: float
    margin: float              # margine operativo di oggi
    invested_capital: float    # patrimonio + debito - cassa
    cash: float
    debt: float
    shares: float
    price: float
    risk_free: float


@dataclass
class Levers:
    growth: float
    target_margin: float
    sales_to_capital: float
    wacc: float
    terminal_wacc: float
    terminal_growth: float
    terminal_roic: float
    tax_rate: float = 0.21
    margin_years: int = 5
    failure_prob: float = 0.0
    distress_proceeds: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    per_share: np.ndarray | float
    equity_value: np.ndarray | float
    operating_value: np.ndarray | float
    tv_share: np.ndarray | float        # quota del valore che viene dal valore finale
    table: pd.DataFrame = field(default_factory=pd.DataFrame)
    checks: list[tuple[str, str]] = field(default_factory=list)   # (livello, messaggio)


def _path(start, end, steps: int, nd: int, total: int = YEARS) -> np.ndarray:
    """Valori anno per anno (righe) che vanno da `start` a `end` in `steps` anni e poi restano a `end`."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    t = np.arange(1, total + 1).reshape(-1, *([1] * nd))
    frac = np.clip(t / np.maximum(steps, 1), 0, 1)
    return start + (end - start) * frac


def value(base: Base, lv: Levers, with_table: bool = False) -> Result:
    g, gT = np.asarray(lv.growth, float), np.asarray(lv.terminal_growth, float)
    nd = max(np.ndim(v) for v in (lv.growth, lv.target_margin, lv.sales_to_capital, lv.wacc, lv.terminal_wacc,
                                  lv.terminal_growth, lv.terminal_roic, lv.tax_rate))  # 0 = una valutazione sola
    t = np.arange(1, YEARS + 1).reshape(-1, *([1] * nd))
    # crescita: alta per 5 anni, poi in calo lineare fino a quella finale nel decimo
    fade = np.clip((t - HIGH_GROWTH_YEARS) / (YEARS - HIGH_GROWTH_YEARS), 0, 1)
    growth = g + (gT - g) * fade
    revenue = base.revenue * np.cumprod(1 + growth, axis=0)
    margin = _path(base.margin, lv.target_margin, int(lv.margin_years), nd)
    ebit = revenue * margin
    tax = np.asarray(lv.tax_rate, float)
    nopat = np.where(ebit > 0, ebit * (1 - tax), ebit)
    prev_revenue = np.concatenate([np.broadcast_to(base.revenue, revenue[:1].shape), revenue[:-1]], axis=0)
    reinvestment = (revenue - prev_revenue) / np.asarray(lv.sales_to_capital, float)
    fcff = nopat - reinvestment
    w, wT = np.asarray(lv.wacc, float), np.asarray(lv.terminal_wacc, float)
    wacc = w + (wT - w) * fade
    discount = np.cumprod(1 + wacc, axis=0)
    pv = fcff / discount

    # fase stabile: reinvestimento coerente con la crescita (crescita = reinvestimento x ROIC)
    revenue_11 = revenue[-1] * (1 + gT)
    nopat_11 = revenue_11 * margin[-1] * (1 - tax)
    reinvest_rate = np.where(np.asarray(lv.terminal_roic, float) > 0, gT / np.asarray(lv.terminal_roic, float), 1.0)
    fcff_11 = nopat_11 * (1 - reinvest_rate)
    spread = wT - gT
    tv = np.where(spread > 0, fcff_11 / np.where(spread > 0, spread, np.nan), np.nan)
    pv_tv = tv / discount[-1]
    operating = pv.sum(axis=0) + pv_tv
    p = np.asarray(lv.failure_prob, float)
    operating = operating * (1 - p) + p * np.asarray(lv.distress_proceeds, float) * base.invested_capital
    equity = operating + base.cash - base.debt
    per_share = equity / base.shares
    tv_share = pv_tv / (pv.sum(axis=0) + pv_tv)

    res = Result(per_share=per_share, equity_value=equity, operating_value=operating, tv_share=tv_share)
    if with_table and nd == 0:
        capital = base.invested_capital + np.cumsum(reinvestment)
        prev_capital = np.concatenate([[base.invested_capital], capital[:-1]])
        res.table = pd.DataFrame({
            "anno": np.arange(1, YEARS + 1), "crescita": growth, "ricavi": revenue, "margine": margin, "ebit": ebit,
            "nopat": nopat, "reinvestimento": reinvestment, "fcff": fcff, "costo_capitale": wacc,
            "valore_attuale": pv, "capitale_investito": capital,
            "roic": np.where(prev_capital > 0, nopat / np.where(prev_capital > 0, prev_capital, np.nan), np.nan),
        })
        res.checks = checks(base, lv, res)
    return res


def checks(base: Base, lv: Levers, res: Result) -> list[tuple[str, str]]:
    """I controlli di coerenza di Damodaran sul risultato di uno scenario."""
    out = []
    if lv.terminal_wacc <= lv.terminal_growth:
        out.append(("errore", "Il costo del capitale finale deve superare la crescita per sempre."))
        return out
    if lv.terminal_growth > base.risk_free:
        out.append(("attenzione", f"Crescita per sempre {lv.terminal_growth:.1%} sopra il tasso privo di rischio "
                                  f"{base.risk_free:.1%}: alla lunga l'azienda diventerebbe più grande dell'economia."))
    share = float(res.tv_share)
    if share > 0.85:
        out.append(("attenzione", f"Il {share:.0%} del valore viene da dopo il decimo anno: la valutazione dipende "
                                  "quasi tutta da ipotesi lontane."))
    # rendimento sul capitale NUOVO: quanto cresce l'utile operativo netto rispetto a quanto si reinveste in dieci
    # anni. Il ROIC dell'anno 10 è diluito dal capitale di oggi e non vede le ipotesi incoerenti
    t = res.table
    if not t.empty:
        nopat_0 = base.revenue * base.margin * (1 - lv.tax_rate)
        invested = float(t["reinvestimento"].sum())
        marginal = (float(t["nopat"].iloc[-1]) - nopat_0) / invested if invested > 0 else np.nan
        current = nopat_0 / base.invested_capital if base.invested_capital > 0 else np.nan
        # molto meglio di quanto l'azienda rende oggi sul suo capitale (aziende come Apple rendono già moltissimo)
        if np.isfinite(marginal) and marginal > max(0.5, 1.5 * np.nan_to_num(current)):
            out.append(("attenzione", f"Rendimento sul capitale nuovo {marginal:.0%}, contro il {current:.0%} che "
                                      "l'azienda rende oggi: si cresce quasi senza investire. Forse i ricavi per euro "
                                      "investito sono troppo ottimisti."))
        elif np.isfinite(marginal) and marginal < lv.terminal_wacc and lv.growth > lv.terminal_growth:
            out.append(("attenzione", f"Rendimento sul capitale nuovo {marginal:.1%}, sotto il costo del capitale "
                                      f"{lv.terminal_wacc:.1%}: la crescita distrugge valore invece di crearne."))
    if lv.terminal_roic > lv.terminal_wacc + 0.10:
        out.append(("attenzione", f"ROIC nella fase stabile {lv.terminal_roic:.0%} contro un costo del capitale "
                                  f"{lv.terminal_wacc:.0%}: presuppone un vantaggio competitivo enorme e per sempre."))
    if float(res.equity_value) < 0:
        out.append(("attenzione", "Valore dell'equity negativo: il debito supera il valore operativo."))
    return out


def scenario_values(base: Base, scenarios: dict[str, Levers], probabilities: dict[str, float]) -> dict:
    """Valore per azione di ogni scenario e valore atteso (media pesata con le probabilità, rinormalizzate)."""
    per = {name: float(value(base, lv).per_share) for name, lv in scenarios.items()}
    total = sum(max(probabilities.get(n, 0.0), 0.0) for n in scenarios)
    expected = (sum(per[n] * max(probabilities.get(n, 0.0), 0.0) for n in scenarios) / total) if total > 0 else np.nan
    return {"per_scenario": per, "expected": expected}


def simulate(base: Base, bear: Levers, mid: Levers, bull: Levers, n: int = 5000, seed: int = 0) -> dict:
    """Monte Carlo: ogni leva di SIMULATED varia con una distribuzione triangolare tra il pessimista e l'ottimista,
    con il caso base come valore più probabile; le altre restano quelle del caso base. Le leve sono indipendenti
    (semplificazione: in realtà crescita e margini tendono a muoversi insieme)."""
    rng = np.random.default_rng(seed)
    draws = {}
    for k in SIMULATED:
        lo, mode, hi = sorted([getattr(bear, k), getattr(mid, k), getattr(bull, k)])
        mode = min(max(getattr(mid, k), lo), hi)
        draws[k] = rng.triangular(lo, mode, hi, n) if hi > lo else np.full(n, mode)
    lv = Levers(**{**mid.to_dict(), **draws})
    lv.terminal_wacc = np.maximum(np.asarray(mid.terminal_wacc), mid.terminal_growth + 0.005)
    values = np.asarray(value(base, lv).per_share, float)
    values = values[np.isfinite(values)]
    # sensibilità: quanto cambia il valore portando una leva alla volta dal pessimista all'ottimista
    swing = {}
    for k in SIMULATED:
        lo = float(value(base, Levers(**{**mid.to_dict(), k: getattr(bear, k)})).per_share)
        hi = float(value(base, Levers(**{**mid.to_dict(), k: getattr(bull, k)})).per_share)
        swing[k] = (lo, hi)
    return {
        "values": values,
        "p10": float(np.percentile(values, 10)) if len(values) else np.nan,
        "p50": float(np.percentile(values, 50)) if len(values) else np.nan,
        "p90": float(np.percentile(values, 90)) if len(values) else np.nan,
        "prob_above_price": float((values > base.price).mean()) if len(values) else np.nan,
        "swing": dict(sorted(swing.items(), key=lambda kv: -abs(kv[1][1] - kv[1][0]))),
    }


def default_scenarios(base: Base, growth: float, wacc: float, tax_rate: float, terminal_growth: float,
                      sales_to_capital: float, mature_wacc: float) -> dict[str, Levers]:
    """Proposta di partenza: caso base = l'azienda di oggi (crescita recente, margine di oggi, stessa efficienza
    del capitale), pessimista e ottimista spostati in modo simmetrico. Da cambiare con la propria storia."""
    gT = min(terminal_growth, base.risk_free)
    wT = max(min(wacc, mature_wacc), gT + 0.02)
    m = base.margin
    common = dict(terminal_growth=gT, tax_rate=tax_rate, margin_years=5)
    return {
        "Pessimista": Levers(growth=max(growth - 0.05, -0.05), target_margin=m - max(abs(m) * 0.2, 0.02),
                             sales_to_capital=sales_to_capital * 0.8, wacc=wacc + 0.01, terminal_wacc=wT + 0.01,
                             terminal_roic=wT + 0.01, **common),
        "Base": Levers(growth=growth, target_margin=m, sales_to_capital=sales_to_capital, wacc=wacc,
                       terminal_wacc=wT, terminal_roic=wT, **common),
        "Ottimista": Levers(growth=growth + 0.05, target_margin=m + max(abs(m) * 0.2, 0.02),
                            sales_to_capital=sales_to_capital * 1.2, wacc=max(wacc - 0.005, gT + 0.02),
                            terminal_wacc=max(wT - 0.005, gT + 0.02), terminal_roic=wT + 0.03, **common),
    }


def implied_growth(base: Base, lv: Levers, lo: float = -0.10, hi: float = 0.60) -> float | None:
    """Reverse: la crescita dei ricavi negli anni 1-5 che, con le altre leve invariate, dà un valore per azione
    uguale al prezzo di oggi. None se nemmeno gli estremi ci arrivano."""
    f = lambda g: float(value(base, Levers(**{**lv.to_dict(), "growth": g})).per_share) - base.price  # noqa: E731
    f_lo, f_hi = f(lo), f(hi)
    if not (np.isfinite(f_lo) and np.isfinite(f_hi)) or f_lo > 0 or f_hi < 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
