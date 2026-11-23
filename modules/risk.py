"""
Rischio del portafoglio: limiti applicati ai pesi e misure per il report.

Limiti (`RiskManager.apply`), nell'ordine, dopo che l'ottimizzatore ha deciso i pesi:
1. Tetto per settore (`max_sector_weight`): un settore sopra il tetto viene ridotto in proporzione e
   l'eccesso va ai titoli degli altri settori (senza superare il tetto per titolo); ciò che non trova
   posto resta liquido. 1.0 = nessun tetto.
2. Volatilità obiettivo (`target_volatility`): la volatilità del portafoglio è stimata sui rendimenti
   degli ultimi `vol_lookback_days` giorni dei titoli scelti, con i pesi di oggi. Se supera l'obiettivo,
   tutti i pesi si riducono in proporzione (mai leva: l'esposizione non sale sopra quella di partenza).
   0 = disattivata.
3. Controllo delle perdite (`drawdown_limit`, negativo): se il portafoglio ha perso più di così dal suo
   massimo, l'esposizione si moltiplica per `drawdown_exposure`. Serve il valore del portafoglio nel
   tempo: nel backtest c'è, nell'analisi giornaliera no (manca ancora un registro delle posizioni),
   quindi lì la regola non scatta. 0 = disattivato.

Misure (`measure`): esposizione, volatilità e beta stimati, VaR/CVaR storici (con i pesi di oggi sui
rendimenti dell'ultimo anno), concentrazione e pesi per settore.
Prove di stress (`stress_test`): il portafoglio di oggi attraverso crisi passate (rendimento vero di
ogni titolo nel periodo, oppure beta x mercato se allora non era quotato) e shock ipotetici.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252
UNKNOWN_SECTOR = "Sconosciuto"


def _sector_of(tickers, sectors: dict[str, str]) -> pd.Series:
    return pd.Series({t: sectors.get(t) or UNKNOWN_SECTOR for t in tickers}, dtype=object)


def cap_sectors(weights: pd.Series, sectors: dict[str, str], max_sector: float, max_weight: float) -> pd.Series:
    """Riduce i settori sopra `max_sector` e ridistribuisce l'eccesso sugli altri, nel rispetto del tetto
    per titolo `max_weight`. Il totale non cresce mai; ciò che non trova posto resta liquido."""
    w = weights.astype(float).copy()
    if w.empty or max_sector >= 1.0:
        return w
    sec = _sector_of(w.index, sectors)
    for _ in range(50):
        sums = w.groupby(sec).sum()
        over = sums[sums > max_sector + 1e-9]
        if over.empty:
            break
        excess = 0.0
        for s, total in over.items():
            names = sec.index[sec == s]
            w[names] *= max_sector / total
            excess += total - max_sector
        sums = w.groupby(sec).sum()
        room = sums.index[sums < max_sector - 1e-9]
        eligible = w[sec.isin(room) & (w < max_weight - 1e-12) & (w > 0)]
        if eligible.empty:
            break  # nessun altro settore può ricevere: l'eccesso resta liquido
        w[eligible.index] = np.minimum(eligible + excess * eligible / eligible.sum(), max_weight)
    return w


def portfolio_returns(weights: pd.Series, data, lookback_days: int) -> pd.Series:
    """Rendimenti giornalieri (log) che il portafoglio di oggi avrebbe avuto negli ultimi giorni.
    Un titolo senza prezzo in un giorno conta zero quel giorno (come liquidità)."""
    names = [t for t in weights.index if t in data.close.columns]
    if not names:
        return pd.Series(dtype=float)
    close = data.recent_close(names, lookback_days + 1)
    rets = np.log(close.where(close > 0)).diff().iloc[1:].fillna(0.0)
    return rets @ weights[names]


@dataclass
class RiskDecision:
    """Cosa hanno fatto i limiti di rischio a un ribilanciamento."""
    exposure_before: float = 0.0
    exposure: float = 0.0
    capped_sectors: list[str] = field(default_factory=list)
    volatility: float = float("nan")      # stimata, prima della riduzione per la volatilità obiettivo
    vol_scale: float = 1.0
    drawdown: float = 0.0
    drawdown_scale: float = 1.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"exposure_before": self.exposure_before, "exposure": self.exposure,
                "capped_sectors": ", ".join(self.capped_sectors), "volatility": self.volatility,
                "vol_scale": self.vol_scale, "drawdown": self.drawdown, "drawdown_scale": self.drawdown_scale}


class RiskManager:
    def __init__(self, cfg_risk: dict, sectors: dict[str, str] | None = None, max_weight: float = 1.0,
                 benchmark_fallback_beta: float = 1.0):
        r = cfg_risk or {}
        self.cfg = r
        self.sectors = sectors or {}
        self.max_weight = max_weight
        self.max_sector = float(r.get("max_sector_weight", 1.0))
        self.target_vol = float(r.get("target_volatility", 0.0))
        self.vol_lookback = int(r.get("vol_lookback_days", 63))
        self.dd_limit = float(r.get("drawdown_limit", 0.0))
        self.dd_exposure = float(r.get("drawdown_exposure", 0.5))
        self.report_lookback = int(r.get("report_lookback_days", TRADING_DAYS))
        self.var_level = float(r.get("var_confidence", 0.95))
        self.fallback_beta = benchmark_fallback_beta

    # ------------------------------------------------------------------ limiti
    def apply(self, weights: pd.Series, data, drawdown: float = 0.0) -> tuple[pd.Series, RiskDecision]:
        dec = RiskDecision(exposure_before=float(weights.sum()), drawdown=float(drawdown))
        w = weights.astype(float)
        if w.empty:
            return w, dec
        if self.max_sector < 1.0:
            before = w.groupby(_sector_of(w.index, self.sectors)).sum()
            w = cap_sectors(w, self.sectors, self.max_sector, self.max_weight)
            dec.capped_sectors = sorted(before.index[before > self.max_sector + 1e-9])
            if dec.capped_sectors:
                dec.notes.append(f"settori ridotti al {self.max_sector:.0%}: {', '.join(dec.capped_sectors)}")
        if self.target_vol > 0:
            port = portfolio_returns(w, data, self.vol_lookback)
            if len(port) >= 20:
                dec.volatility = float(port.std() * np.sqrt(TRADING_DAYS))
                if dec.volatility > self.target_vol:
                    dec.vol_scale = self.target_vol / dec.volatility
                    w = w * dec.vol_scale
                    dec.notes.append(f"volatilità stimata {dec.volatility:.1%} sopra l'obiettivo "
                                     f"{self.target_vol:.0%}: esposizione x{dec.vol_scale:.2f}")
        if self.dd_limit < 0 and drawdown <= self.dd_limit:
            dec.drawdown_scale = self.dd_exposure
            w = w * self.dd_exposure
            dec.notes.append(f"perdita dal massimo {drawdown:.1%} oltre il limite {self.dd_limit:.0%}: "
                             f"esposizione x{self.dd_exposure:.2f}")
        w = w[w > 1e-6]
        dec.exposure = float(w.sum())
        return w.sort_values(ascending=False), dec

    # ------------------------------------------------------------------ misure
    def betas(self, tickers, data) -> pd.Series:
        from modules.fundamental import FundamentalAnalyzer

        bench = data.returns(data.benchmark) if data.benchmark else pd.Series(dtype=float)
        if bench.empty:
            return pd.Series(self.fallback_beta, index=list(tickers), dtype=float)
        return pd.Series({t: FundamentalAnalyzer.beta_from_returns(data.returns(t), bench, self.report_lookback)
                          for t in tickers}, dtype=float)

    def measure(self, weights: pd.Series, data) -> dict:
        """Misure del portafoglio di oggi (pesi sul capitale; il resto è liquidità)."""
        w = weights.astype(float)
        out = {"exposure": float(w.sum()), "positions": int((w > 0).sum())}
        if w.empty or w.sum() <= 0:
            return out
        port = portfolio_returns(w, data, self.report_lookback)
        q = 1 - self.var_level
        if len(port) >= 20:
            var = -float(np.quantile(port, q))
            cvar = -float(port[port <= -var].mean())
            out.update(volatility=float(port.std() * np.sqrt(TRADING_DAYS)), var_1d=var, cvar_1d=cvar,
                       var_1m=var * np.sqrt(21), worst_day=-float(port.min()))
        betas = self.betas(w.index, data)
        out["beta"] = float((w * betas).sum())
        invested = w / w.sum()
        out["effective_positions"] = float(1 / (invested ** 2).sum())
        out["largest_weight"] = float(w.max())
        sector_w = w.groupby(_sector_of(w.index, self.sectors)).sum().sort_values(ascending=False)
        out["sector_weights"] = sector_w
        out["largest_sector"] = (str(sector_w.index[0]), float(sector_w.iloc[0]))
        return out

    # ------------------------------------------------------------------ stress
    def stress_test(self, weights: pd.Series, data) -> pd.DataFrame:
        """Rendimento del portafoglio di oggi in crisi passate e con shock ipotetici.
        `coverage`: quota del capitale investito con prezzi veri nel periodo (il resto: beta x mercato)."""
        w = weights.astype(float)
        rows = []
        if w.empty:
            return pd.DataFrame(columns=["scenario", "start", "end", "portfolio", "benchmark", "coverage"])
        betas = self.betas(w.index, data)
        b = data.benchmark
        bench_px = data.close[b].dropna() if b and b in data.close else pd.Series(dtype=float)
        for name, (start, end) in (self.cfg.get("stress_scenarios") or {}).items():
            start, end = pd.Timestamp(start), pd.Timestamp(end)
            window = bench_px.loc[start:end]
            if len(window) < 2 or window.index[0] - start > pd.Timedelta(days=10):
                continue  # il benchmark non copre il periodo
            bench_ret = float(window.iloc[-1] / window.iloc[0] - 1)
            port, covered = 0.0, 0.0
            for t, wt in w.items():
                px = data.close[t].loc[start:end].dropna() if t in data.close else pd.Series(dtype=float)
                if len(px) >= 2 and px.index[0] - window.index[0] <= pd.Timedelta(days=10) \
                        and window.index[-1] - px.index[-1] <= pd.Timedelta(days=10):
                    port += wt * float(px.iloc[-1] / px.iloc[0] - 1)
                    covered += wt
                else:  # allora non era quotato: si usa la sensibilità al mercato di oggi
                    port += wt * float(betas.get(t, self.fallback_beta)) * bench_ret
            rows.append({"scenario": name, "start": window.index[0].date(), "end": window.index[-1].date(),
                         "portfolio": port, "benchmark": bench_ret, "coverage": covered / w.sum()})
        shock = float(self.cfg.get("market_shock", -0.20))
        rows.append({"scenario": f"Mercato {shock:+.0%} (beta)", "start": None, "end": None,
                     "portfolio": float((w * betas).sum() * shock), "benchmark": shock, "coverage": float("nan")})
        sector_shock = float(self.cfg.get("sector_shock", -0.30))
        sector_w = w.groupby(_sector_of(w.index, self.sectors)).sum()
        top = sector_w.idxmax()
        rows.append({"scenario": f"{top} {sector_shock:+.0%} (settore più pesante)", "start": None, "end": None,
                     "portfolio": float(sector_w[top] * sector_shock), "benchmark": float("nan"),
                     "coverage": float("nan")})
        return pd.DataFrame(rows)
