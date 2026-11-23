"""
Multi-Factor Model (Momentum, Quality, Volatility).

Ogni fattore ha un punteggio da 0 a 1; il totale è la media pesata dei fattori
disponibili (se un dato manca, quel fattore viene escluso e i pesi rinormalizzati,
invece di regalargli un punteggio).

Il fattore Value NON è qui: il margine di sicurezza del DCF entra già con il 40%
nel ConfidenceEngine, e contarlo due volte sbilancerebbe il sistema.

- Momentum: rendimento 12-1 mesi. 0% -> 0, +30% o più -> 1.
- Quality: media di (debito/patrimonio: 0.5 -> 1, 2.0 -> 0) e (margine netto: 0 -> 0, 15%+ -> 1).
  Patrimonio netto negativo -> componente debito = 0.
- Volatility (low-volatility anomaly): vol annua 15% o meno -> 1, 40% o più -> 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from modules.technical import momentum_12_1


@dataclass
class FactorScores:
    momentum: float | None = None
    quality: float | None = None
    volatility: float | None = None
    total: float = 0.0
    details: dict = field(default_factory=dict)


class MultiFactorModel:
    def __init__(self, weights: dict[str, float]):
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("i pesi del multi-factor devono essere positivi")
        self.weights = {k: v / total for k, v in weights.items()}

    @staticmethod
    def momentum_score(momentum: float) -> float | None:
        return float(np.clip(momentum / 0.30, 0, 1)) if np.isfinite(momentum) else None

    @staticmethod
    def quality_score(debt_to_equity: float, net_margin: float) -> float | None:
        parts = []
        if np.isfinite(debt_to_equity):
            parts.append(0.0 if debt_to_equity < 0 else float(np.clip((2.0 - debt_to_equity) / 1.5, 0, 1)))
        if np.isfinite(net_margin):
            parts.append(float(np.clip(net_margin / 0.15, 0, 1)))
        return float(np.mean(parts)) if parts else None

    @staticmethod
    def volatility_score(vol: float) -> float | None:
        return float(np.clip((0.40 - vol) / 0.25, 0, 1)) if np.isfinite(vol) else None

    def score(self, ticker: str, data) -> FactorScores:
        prices = data.prices(ticker)
        mom = momentum_12_1(prices)
        rets = data.returns(ticker).tail(252)
        vol = float(rets.std() * np.sqrt(252)) if len(rets) > 2 else float("nan")

        de, margin = float("nan"), float("nan")
        st = data.fundamentals(ticker)  # ultimi 12 mesi più recenti
        if not st.empty:
            latest = st.ffill().iloc[-1].to_dict()
            debt, equity = latest.get("total_debt", np.nan), latest.get("equity", np.nan)
            if np.isfinite(equity) and equity != 0 and np.isfinite(debt):
                de = float(debt / equity)  # negativo se il patrimonio è negativo
            ni, rev = latest.get("net_income", np.nan), latest.get("revenue", np.nan)
            if np.isfinite(ni) and np.isfinite(rev) and rev > 0:
                margin = float(ni / rev)

        scores = FactorScores(
            momentum=self.momentum_score(mom),
            quality=self.quality_score(de, margin),
            volatility=self.volatility_score(vol),
            details={"momentum_12_1": mom, "annual_vol": vol, "debt_to_equity": de, "net_margin": margin},
        )
        available = {k: getattr(scores, k) for k in self.weights if getattr(scores, k) is not None}
        if available:
            w = sum(self.weights[k] for k in available)
            scores.total = float(sum(self.weights[k] * v for k, v in available.items()) / w)
        return scores
