"""
La strategia completa, usata IDENTICA dall'analisi live e dal backtest.

Due modalità (`strategy.mode`):
- confidence (originale), per ogni data:
  1. regime di mercato (HMM sul benchmark)
  2. per ogni titolo: controllo dati vecchi, circuit breaker, DCF, multi-factor, confidenza
  3. tra i titoli promossi si tengono i migliori `max_positions` per confidenza,
     poi l'ottimizzatore di portafoglio decide i pesi
- factors: i titoli con prezzo aggiornato che passano il circuit breaker vengono ordinati per
  combinazione dei fattori classici (modules/factors.py, pesi in `strategy.factor_weights`);
  si comprano i primi `max_positions`. Niente soglie né regime: si resta sempre investiti.
  I pesi vanno scelti su un periodo e verificati su un altro (python main.py --factors).
- rules: strategie definite a regole (modules/strategies.py: catalogo e strategie dell'utente): universo,
  filtri sulle metriche del catalogo, classifica, primi N titoli. Metriche ricalcolate a ogni data.
In tutte, i pesi passano infine dai limiti di rischio (modules/risk.py: settori, volatilità, perdite).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from modules.confidence_engine import ConfidenceEngine
from modules.fundamental import FundamentalAnalyzer, Valuation
from modules.multi_factor import MultiFactorModel
from modules.portfolio_optimizer import PortfolioOptimizer
from modules.regime_detection import RegimeDetector, RegimeResult
from modules.risk import RiskDecision, RiskManager
from safety.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    as_of: pd.Timestamp
    regime: RegimeResult
    decisions: list[dict] = field(default_factory=list)
    weights: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    risk: RiskDecision = field(default_factory=RiskDecision)

    def decisions_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.decisions)


class Strategy:
    def __init__(self, cfg: dict, tickers: list[str] | None = None, sectors: dict[str, str] | None = None):
        self.cfg = cfg
        self.tickers = list(tickers) if tickers is not None else list(cfg["universe"]["tickers"])
        self.sectors = sectors or {}
        self.skip_sectors = set(cfg["valuation"].get("skip_sectors", []))
        self.max_positions = int(cfg["portfolio"].get("max_positions", 25))
        self.max_stale = pd.Timedelta(days=cfg["data"]["max_stale_days"])
        self.regime_detector = RegimeDetector(cfg["regime"])
        self.fundamental = FundamentalAnalyzer(cfg["valuation"])
        self.multi_factor = MultiFactorModel(cfg["multi_factor"]["weights"])
        self.confidence = ConfidenceEngine(cfg["confidence"])
        s = cfg["safety"]
        self.breaker = CircuitBreaker(s["max_daily_vol"], s["outlier_sigma"], s["min_history_days"])
        p = cfg["portfolio"]
        self.optimizer = PortfolioOptimizer(p["method"], p["max_weight"], p["lookback_days"])
        self.risk = RiskManager(cfg.get("risk", {}), self.sectors, p["max_weight"])
        st = cfg.get("strategy", {})
        self.mode = st.get("mode", "confidence")
        self.factor_weights = dict(st.get("factor_weights") or {})
        if self.mode not in ("confidence", "factors", "rules"):
            raise ValueError("strategy.mode deve essere 'confidence', 'factors' o 'rules'")
        self.rules = dict(st.get("rules") or {})
        if self.mode == "factors" and not any(v > 0 for v in self.factor_weights.values()):
            raise ValueError("strategy.factor_weights è vuoto: esegui `python main.py --factors` e copia i pesi")
        self.max_statement_age = int(cfg.get("factors", {}).get("max_statement_age_days", 550))

    def run(self, data, tickers: list[str] | None = None, drawdown: float = 0.0) -> StrategyResult:
        """`tickers`: universo a questa data (default: quello passato al costruttore).
        `drawdown`: perdita attuale del portafoglio dal suo massimo (per il limite di perdita; 0 se ignota)."""
        tickers = self.tickers if tickers is None else tickers
        as_of = data.last_date
        bench = data.prices(data.benchmark) if data.benchmark else pd.Series(dtype=float)
        if bench.empty:
            regime = RegimeResult("Neutral", float("nan"), "default (benchmark assente)")
        else:
            regime = self.regime_detector.detect(bench)
        result = StrategyResult(as_of=as_of, regime=regime)
        if self.mode == "factors":
            self._run_factors(data, tickers, result)
        elif self.mode == "rules":
            self._run_rules(data, tickers, result)
        else:
            self._run_confidence(data, tickers, result)
        if not result.weights.empty:
            result.weights, result.risk = self.risk.apply(result.weights, data, drawdown)
        return result

    def _run_confidence(self, data, tickers: list[str], result: StrategyResult) -> StrategyResult:
        regime, as_of = result.regime, result.as_of

        for ticker in tickers:
            result.decisions.append(self._evaluate(ticker, data, regime, as_of))

        buys = sorted((d for d in result.decisions if d["action"] == "BUY"),
                      key=lambda d: d["confidence"], reverse=True)
        for d in buys[self.max_positions:]:
            d["action"] = "SKIP"
            d["reasoning"] += f"; escluso: oltre le prime {self.max_positions} per confidenza"
        selected = [d["ticker"] for d in buys[:self.max_positions]]
        if selected:
            result.weights = self.optimizer.optimize(data.close[selected])
        return result

    def _screen(self, ticker: str, data, regime: RegimeResult, as_of) -> dict:
        """Controlli comuni alle due modalità: prezzo presente e aggiornato, circuit breaker.
        Restituisce la riga della decisione; action resta "SKIP" con `reasoning` solo se il titolo è scartato."""
        row = {"as_of": as_of, "ticker": ticker, "sector": self.sectors.get(ticker), "regime": regime.label,
               "action": "SKIP"}
        prices = data.prices(ticker)
        if prices.empty:
            row["reasoning"] = "nessun prezzo disponibile"
            return row
        row["price"] = float(prices.iloc[-1])
        if as_of is not None and as_of - prices.index[-1] > self.max_stale:
            row["reasoning"] = f"prezzo vecchio (ultimo {prices.index[-1].date()})"
            return row
        safe, msg = self.breaker.check_safety(prices.tail(80))
        if not safe:
            row.update(action="BLOCKED", reasoning=f"circuit breaker: {msg}")
        return row

    def _run_factors(self, data, tickers: list[str], result: StrategyResult) -> StrategyResult:
        from modules.factors import compute_factors, composite_score

        rows = [self._screen(t, data, result.regime, result.as_of) for t in tickers]
        ok = [r["ticker"] for r in rows if "reasoning" not in r]
        score = composite_score(compute_factors(data, ok, self.max_statement_age), self.factor_weights)
        ranked = score.dropna().sort_values(ascending=False, kind="stable")
        selected = list(ranked.index[:self.max_positions])
        cutoff = float(ranked.iloc[len(selected) - 1]) if selected else float("nan")
        position = {t: i + 1 for i, t in enumerate(ranked.index)}
        for r in rows:
            if "reasoning" in r:
                continue
            s = score.get(r["ticker"], np.nan)
            r.update(confidence=float(s) if np.isfinite(s) else None, threshold=cutoff)
            if not np.isfinite(s):
                r["reasoning"] = "fattori non calcolabili (storico o bilanci insufficienti)"
            elif r["ticker"] in selected:
                r.update(action="BUY", reasoning=f"punteggio fattori {s:.2f}: {position[r['ticker']]}° su {len(ranked)}")
            else:
                r["reasoning"] = (f"punteggio fattori {s:.2f}: {position[r['ticker']]}° su {len(ranked)}, "
                                  f"servono i primi {self.max_positions} (ultimo {cutoff:.2f})")
        result.decisions = rows
        if selected:
            result.weights = self.optimizer.optimize(data.close[selected])
        return result

    def _run_rules(self, data, tickers: list[str], result: StrategyResult) -> StrategyResult:
        from modules.metrics import compute_metrics, price_metrics
        from modules.screener import apply_rules
        from modules.strategies import PRICE_METRICS, rank_score

        r = self.rules
        if r.get("custom_metrics"):
            from modules.metrics import add_custom

            add_custom(r["custom_metrics"])
        if r.get("sectors"):
            tickers = [t for t in tickers if self.sectors.get(t) in set(r["sectors"])]
        if r.get("tickers") is not None:
            tickers = [t for t in tickers if t in set(r["tickers"])]
        rows = [self._screen(t, data, result.regime, result.as_of) for t in tickers]
        ok = [row["ticker"] for row in rows if "reasoning" not in row]
        filters, ranking = r.get("filters", []), r.get("ranking", [])
        needed = {x["metric"] for x in filters} | {x["metric"] for x in ranking}
        if needed and needed <= PRICE_METRICS:  # solo prezzi: i bilanci non servono (molto più veloce)
            m = price_metrics(data, ok)
        else:
            m = compute_metrics(data, ok, self.max_statement_age)
        passing = m.index[apply_rules(m, filters, self.sectors)] if filters else m.index
        score = rank_score(m.loc[passing], ranking)
        if score.notna().any():
            ranked = score.dropna().sort_values(ascending=False, kind="stable")
        else:  # nessuna classifica: le più grandi (le stesse di un indice)
            size = m["market_cap"] if "market_cap" in m else pd.Series(np.nan, index=m.index)
            ranked = size.reindex(passing).fillna(0).sort_values(ascending=False, kind="stable")
            score = pd.Series(np.nan, index=passing)
        selected = list(ranked.index[:self.max_positions])
        position = {t: i + 1 for i, t in enumerate(ranked.index)}
        for row in rows:
            if "reasoning" in row:
                continue
            t = row["ticker"]
            s = score.get(t, np.nan)
            row.update(confidence=float(s) if np.isfinite(s) else None)
            if t not in passing:
                row["reasoning"] = "non rispetta i filtri"
            elif t in selected:
                row.update(action="BUY", reasoning=f"passa i filtri, {position[t]}° su {len(ranked)}")
            else:
                row["reasoning"] = f"passa i filtri ma è {position.get(t, '-')}° su {len(ranked)}: " \
                                   f"servono i primi {self.max_positions}"
        result.decisions = rows
        if selected:
            result.weights = self.optimizer.optimize(data.close[selected])
        return result

    def _evaluate(self, ticker: str, data, regime: RegimeResult, as_of) -> dict:
        row = self._screen(ticker, data, regime, as_of)
        if "reasoning" in row:  # senza prezzo, prezzo vecchio o bloccato dal circuit breaker
            return row
        sector = row["sector"]

        if sector in self.skip_sectors:
            val = Valuation(ticker, row["price"], notes=[f"DCF sul free cash flow non significativo per il settore {sector}"])
        else:
            val = self.fundamental.value(ticker, data)
        fs = self.multi_factor.score(ticker, data)
        conf = self.confidence.evaluate(regime.label, val.margin, fs.total)

        row.update(
            action="BUY" if conf.is_valid else "SKIP",
            confidence=conf.score, threshold=conf.threshold,
            fair_value=val.fair_value, margin_of_safety=val.margin, wacc=val.wacc, beta=val.beta,
            growth=val.growth, implied_growth=val.implied_growth,
            value_score=conf.value_score, mf_score=fs.total,
            momentum=fs.momentum, quality=fs.quality, volatility_score=fs.volatility,
        )
        verdict = "supera" if conf.is_valid else "sotto"
        reason = f"confidenza {conf.score:.2f} {verdict} la soglia {conf.threshold:.2f} ({regime.label})"
        if val.fair_value is None and val.notes:
            reason += f"; DCF: {val.notes[-1]}"
        row["reasoning"] = reason
        return row
