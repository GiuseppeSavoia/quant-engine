"""
Backtest point-in-time della STESSA strategia usata dall'analisi live.

Regole della simulazione:
- Si scorre solo il calendario di borsa reale (giorni del benchmark): niente festivi.
- Ogni `rebalance_days` giorni la strategia gira su `MarketData.as_of(giorno)`: vede solo
  prezzi fino a quel giorno e bilanci già depositati a quella data (data `filed`).
- Con `universe_fn` l'universo cambia nel tempo (es. solo i titoli già entrati nell'S&P 500).
- Il segnale calcolato alla chiusura di un giorno viene eseguito alla chiusura del giorno
  di borsa successivo (niente esecuzione allo stesso prezzo che ha generato il segnale).
- Ribilanciamento verso i pesi obiettivo: si vende ciò che non è più selezionato o è
  sovrappesato, poi si compra. Costi = commissione + slippage sul controvalore scambiato.
- Uscite opzionali: stop-loss / take-profit (ATR) controllati sulla chiusura giornaliera;
  i livelli vengono ricalcolati a ogni ribilanciamento.
- Limiti di rischio (modules/risk.py) dentro la strategia; il limite di perdita usa la perdita del
  portafoglio simulato dal suo massimo al giorno del segnale.
- Confronto con il benchmark comprato e tenuto.
- Copertura (survivorship bias): a ogni ribilanciamento si conta quanti membri dell'indice a quella
  data hanno davvero un prezzo. Gli ex membri delistati non lo hanno: la quota mancante è la parte
  di bias che resta (in genere sono proprio i titoli andati peggio).

Semplificazioni: quote frazionarie ammesse, esecuzione al prezzo di chiusura.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from modules.strategy import Strategy
from modules.technical import average_true_range

logger = logging.getLogger(__name__)


def yearly_returns(equity: pd.Series, benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Rendimento per anno solare (il primo e l'ultimo anno possono essere parziali)."""
    def by_year(s: pd.Series) -> pd.Series:
        s = s.dropna()
        if s.empty:
            return pd.Series(dtype=float)
        last = s.groupby(s.index.year).last()
        prev = last.shift(1)
        prev.iloc[0] = s.iloc[0]
        return last / prev - 1

    out = pd.DataFrame({"strategia": by_year(equity)})
    if benchmark is not None and not benchmark.empty:
        out["benchmark"] = by_year(benchmark)
        out["differenza"] = out["strategia"] - out["benchmark"]
    out.index.name = "anno"
    return out


def performance_metrics(equity: pd.Series, risk_free: float = 0.0) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    vol = rets.std() * np.sqrt(252)
    excess = rets - risk_free / 252
    sharpe = excess.mean() / excess.std() * np.sqrt(252) if excess.std() > 1e-12 else 0.0  # equity piatta: 0
    drawdown = equity / equity.cummax() - 1
    max_dd = drawdown.min()
    return {
        "total_return": float(total),
        "cagr": float(cagr),
        "annual_volatility": float(vol),
        "sharpe_ratio": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar_ratio": float(cagr / abs(max_dd)) if max_dd < 0 else float("nan"),
        "final_value": float(equity.iloc[-1]),
    }


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    exposure: pd.Series
    trades: pd.DataFrame
    decisions: pd.DataFrame
    metrics: dict
    benchmark_metrics: dict
    params: dict = field(default_factory=dict)
    coverage: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, members, with_price
    risk: pd.DataFrame = field(default_factory=pd.DataFrame)      # cosa hanno fatto i limiti di rischio

    @property
    def yearly(self) -> pd.DataFrame:
        return yearly_returns(self.equity, self.benchmark)


class Backtester:
    def __init__(self, cfg: dict, strategy: Strategy | None = None, universe_fn=None):
        self.cfg = cfg
        self.bt = cfg["backtest"]
        self.capital = float(cfg["execution"]["capital"])
        self.strategy = strategy or Strategy(cfg)
        self.universe_fn = universe_fn  # data -> lista di ticker; None = universo fisso
        self.cost_rate = self.bt["commission"] + self.bt["slippage"]
        self.min_trade_pct = float(self.bt.get("min_trade_pct", 0.005))

    def run(self, data, start=None, end=None) -> BacktestResult:
        calendar = data.calendar
        if len(calendar) == 0:
            raise ValueError("Nessun dato di prezzo per il backtest")
        end = pd.Timestamp(end) if end is not None else calendar[-1]
        if start is None and self.bt.get("start"):
            start = self.bt["start"]  # data fissa da config (es. backtest lungo dal 2012)
        start = pd.Timestamp(start) if start is not None else end - pd.DateOffset(years=self.bt["years"])
        days = calendar[(calendar >= start) & (calendar <= end)]
        if len(days) < 2:
            raise ValueError("Periodo di backtest troppo corto")

        special = {data.benchmark, data.risk_free_ticker}
        tickers = [c for c in data.close.columns if c not in special]
        close = data.close.reindex(columns=tickers).reindex(calendar).ffill(limit=5)
        n_reb = int(self.bt["rebalance_days"])
        exec_cfg = self.cfg["execution"]

        cash = self.capital
        shares: dict[str, float] = {}
        levels: dict[str, tuple[float | None, float | None]] = {}
        pending: tuple[pd.Series, dict] | None = None
        equity, exposure, trades, decisions, coverage, risk_log = {}, {}, [], [], [], []
        peak = self.capital

        def trade(day, ticker, qty, price, reason):
            nonlocal cash
            value = qty * price
            cost = abs(value) * self.cost_rate
            cash -= value + cost
            shares[ticker] = shares.get(ticker, 0.0) + qty
            if abs(shares[ticker]) < 1e-9:
                shares.pop(ticker, None)
                levels.pop(ticker, None)
            trades.append({"date": day, "ticker": ticker, "side": "BUY" if qty > 0 else "SELL",
                           "shares": abs(qty), "price": price, "value": abs(value), "cost": cost,
                           "reason": reason})

        for i, day in enumerate(days):
            px = close.loc[day]

            # 1) esecuzione del ribilanciamento deciso ieri
            if pending is not None:
                weights, atrs = pending
                pending = None
                port_value = cash + sum(q * px[t] for t, q in shares.items() if np.isfinite(px[t]))
                # ordine fisso: con poca cassa l'ordine decide chi viene comprato (un set cambierebbe a ogni processo)
                order = sorted(set(shares) | set(weights.index))
                targets = {t: weights.get(t, 0.0) * port_value for t in order}
                min_trade = self.min_trade_pct * port_value  # evita micro-operazioni che costano solo commissioni
                for t, target in targets.items():  # prima le vendite (liberano liquidità)
                    price = px.get(t, np.nan)
                    if not np.isfinite(price) or price <= 0:
                        continue
                    diff = target / price - shares.get(t, 0.0)
                    if diff < 0 and (target == 0 or abs(diff) * price > min_trade):
                        trade(day, t, -shares[t] if target == 0 else diff, price, "ribilanciamento")
                for t, target in targets.items():
                    price = px.get(t, np.nan)
                    if not np.isfinite(price) or price <= 0:
                        continue
                    diff = target / price - shares.get(t, 0.0)
                    # la soglia minima vale per gli aggiustamenti: una posizione nuova si apre comunque
                    # (con centinaia di titoli a peso uguale ogni posizione è sotto la soglia)
                    if diff > 0 and (t not in shares or diff * price > min_trade):
                        affordable = max(cash, 0.0) / (price * (1 + self.cost_rate))
                        qty = min(diff, affordable)
                        if qty * price > 1.0:
                            trade(day, t, qty, price, "ribilanciamento")
                for t in shares:
                    atr = atrs.get(t, np.nan)
                    levels[t] = (px[t] - exec_cfg["stop_loss_atr"] * atr, px[t] + exec_cfg["take_profit_atr"] * atr) \
                        if np.isfinite(atr) and atr > 0 else (None, None)

            # 2) stop-loss / take-profit sulla chiusura
            if self.bt["use_exits"]:
                for t in list(shares):
                    sl, tp = levels.get(t, (None, None))
                    price = px.get(t, np.nan)
                    if not np.isfinite(price):
                        continue
                    if sl is not None and price <= sl:
                        trade(day, t, -shares[t], price, "stop-loss")
                    elif tp is not None and price >= tp:
                        trade(day, t, -shares[t], price, "take-profit")

            # 3) valore del portafoglio
            invested = sum(q * px[t] for t, q in shares.items() if np.isfinite(px[t]))
            equity[day] = cash + invested
            exposure[day] = invested / equity[day] if equity[day] > 0 else 0.0
            peak = max(peak, equity[day])

            # 4) segnale nei giorni di ribilanciamento (eseguito domani)
            if i % n_reb == 0 and i < len(days) - 1:
                snapshot = data.as_of(day)
                universe = self.universe_fn(day) if self.universe_fn else None
                members = universe if universe is not None else self.strategy.tickers
                priced = sum(1 for t in members if t in close.columns and np.isfinite(close.at[day, t]))
                coverage.append({"date": day, "members": len(members), "with_price": priced})
                res = self.strategy.run(snapshot, tickers=universe, drawdown=equity[day] / peak - 1)
                risk_log.append({"date": day, **res.risk.to_dict()})
                atrs = {t: average_true_range(snapshot.ohlc(t), exec_cfg["atr_period"]) for t in res.weights.index}
                pending = (res.weights, atrs)
                for d in res.decisions:
                    decisions.append({**d, "regime_probability": res.regime.probability})
                logger.debug("%s regime=%s pesi=%s", day.date(), res.regime.label, res.weights.round(3).to_dict())

        equity_s = pd.Series(equity, name="strategia")
        bench_s = self._benchmark(data, days)
        rf = self._avg_risk_free(data, days)
        trades_df = pd.DataFrame(trades)
        metrics = performance_metrics(equity_s, rf)
        metrics.update({
            "n_trades": int(len(trades_df)),
            "total_costs": float(trades_df["cost"].sum()) if not trades_df.empty else 0.0,
            "avg_exposure": float(pd.Series(exposure).mean()),
            "turnover_annual": float(trades_df["value"].sum() / equity_s.mean() /
                                     max((days[-1] - days[0]).days / 365.25, 1e-9)) if not trades_df.empty else 0.0,
            "risk_free_used": rf,
        })
        cov = pd.DataFrame(coverage)
        if not cov.empty:
            share = cov["with_price"] / cov["members"].where(cov["members"] > 0)
            metrics.update({"avg_coverage": float(share.mean()), "min_coverage": float(share.min())})
        bench_metrics = performance_metrics(bench_s, rf) if not bench_s.empty else {}
        if bench_metrics:
            metrics["excess_return_vs_benchmark"] = metrics["total_return"] - bench_metrics["total_return"]
        params = {"start": days[0].date(), "end": days[-1].date(), "n_tickers": len(tickers), **self.bt,
                  "point_in_time_universe": self.universe_fn is not None,
                  "portfolio_method": self.cfg["portfolio"]["method"], "max_weight": self.cfg["portfolio"]["max_weight"]}
        risk_df = pd.DataFrame(risk_log)
        if not risk_df.empty:
            metrics.update({"risk_vol_scaled_share": float((risk_df["vol_scale"] < 1).mean()),
                            "risk_drawdown_share": float((risk_df["drawdown_scale"] < 1).mean()),
                            "risk_sector_capped_share": float((risk_df["capped_sectors"] != "").mean())})
        return BacktestResult(equity_s, bench_s, pd.Series(exposure, name="esposizione"), trades_df,
                              pd.DataFrame(decisions), metrics, bench_metrics, params, cov, risk_df)

    def _benchmark(self, data, days) -> pd.Series:
        b = data.benchmark
        if not b or b not in data.close:
            return pd.Series(dtype=float)
        px = data.close[b].reindex(days).ffill().dropna()
        return (px / px.iloc[0] * self.capital).rename(b) if not px.empty else pd.Series(dtype=float)

    def _avg_risk_free(self, data, days) -> float:
        t = data.risk_free_ticker
        if t and t in data.close:
            s = data.close[t].reindex(days).dropna()
            if not s.empty:
                return float(s.mean() / 100)
        return float(self.cfg["valuation"]["risk_free_fallback"])
