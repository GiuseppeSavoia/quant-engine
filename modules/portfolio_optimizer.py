"""
Allocazione del capitale tra i titoli selezionati (solo posizioni lunghe).

- risk_parity: pesi inversamente proporzionali alla volatilità ("naive risk parity",
  ignora le correlazioni). Semplice e robusto con pochi titoli.
- equal: pesi uguali (con il tetto max_weight).
- min_variance: portafoglio a minima varianza con covarianza stimata via Ledoit-Wolf
  (più stabile della covarianza campionaria) e vincoli 0 <= w <= max_weight.

In entrambi i casi nessun titolo supera `max_weight`; ciò che non si può allocare
rimane liquido (es. 2 titoli con max 20% -> 40% investito, 60% liquidità).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def cap_weights(weights: pd.Series, max_weight: float, total: float = 1.0) -> pd.Series:
    """Applica il tetto per titolo ridistribuendo l'eccesso sugli altri (in proporzione)."""
    w = weights.clip(lower=0).astype(float)
    if w.sum() <= 0:
        return w
    w = w / w.sum() * total
    for _ in range(len(w) + 1):
        over = w > max_weight + 1e-12
        if not over.any():
            break
        excess = float((w[over] - max_weight).sum())
        w[over] = max_weight
        free = ~over & (w < max_weight - 1e-12)
        if not free.any() or w[free].sum() <= 0:
            break  # tutti al tetto: l'eccesso resta liquido
        w[free] += excess * w[free] / w[free].sum()
    return w.clip(upper=max_weight)


class PortfolioOptimizer:
    def __init__(self, method: str = "risk_parity", max_weight: float = 0.20, lookback_days: int = 252):
        if method not in ("risk_parity", "min_variance", "equal"):
            raise ValueError("method deve essere 'risk_parity', 'min_variance' o 'equal'")
        self.method = method
        self.max_weight = max_weight
        self.lookback = lookback_days

    def optimize(self, close: pd.DataFrame) -> pd.Series:
        """close: prezzi di chiusura (colonne = ticker selezionati). Ritorna i pesi (somma <= 1)."""
        if close is None or close.empty:
            return pd.Series(dtype=float)
        rets = np.log(close / close.shift(1)).tail(self.lookback)
        valid = [c for c in rets.columns if rets[c].count() >= 60]
        dropped = set(rets.columns) - set(valid)
        if dropped:
            logger.warning("Storico insufficiente per l'ottimizzazione, esclusi: %s", sorted(dropped))
        if not valid:
            return pd.Series(dtype=float)
        rets = rets[valid].dropna()
        target_total = min(1.0, len(valid) * self.max_weight)

        if self.method == "equal":
            weights = cap_weights(pd.Series(1.0, index=valid), self.max_weight, target_total)
        elif self.method == "min_variance" and len(valid) > 1 and len(rets) >= 60:
            weights = self._min_variance(rets, target_total)
        else:
            vol = rets.std() * np.sqrt(252)
            vol = vol.replace(0, np.nan).dropna()
            weights = cap_weights(1.0 / vol, self.max_weight, target_total)
        return weights[weights > 1e-6].sort_values(ascending=False)

    def _min_variance(self, rets: pd.DataFrame, total: float) -> pd.Series:
        from scipy.optimize import minimize
        from sklearn.covariance import LedoitWolf

        cov = LedoitWolf().fit(rets.to_numpy()).covariance_ * 252
        n = cov.shape[0]
        x0 = np.full(n, total / n)
        res = minimize(
            lambda w: w @ cov @ w,
            x0,
            jac=lambda w: 2 * cov @ w,
            bounds=[(0.0, self.max_weight)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - total}],
            method="SLSQP",
        )
        if not res.success:
            logger.warning("Ottimizzazione min-variance non convergente (%s): uso risk parity", res.message)
            vol = rets.std() * np.sqrt(252)
            return cap_weights(1.0 / vol, self.max_weight, total)
        return pd.Series(np.clip(res.x, 0, self.max_weight), index=rets.columns)
