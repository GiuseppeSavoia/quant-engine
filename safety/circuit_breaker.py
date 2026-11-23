"""
Circuit breaker: blocca l'operatività su un titolo in condizioni anomale.

1. Storico troppo corto -> blocco (in caso di dubbio non si opera).
2. Volatilità degli ultimi 5 rendimenti giornalieri sopra soglia -> blocco.
3. Ultimo rendimento oltre `outlier_sigma` deviazioni standard rispetto ai 60 precedenti
   (es. flash crash o dato errato) -> blocco.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class CircuitBreaker:
    def __init__(self, max_daily_vol: float = 0.05, outlier_sigma: float = 3.0, min_history: int = 20):
        self.max_daily_vol = max_daily_vol
        self.outlier_sigma = outlier_sigma
        self.min_history = min_history

    def check_safety(self, prices) -> tuple[bool, str]:
        arr = np.asarray(pd.DataFrame(prices).to_numpy(dtype=float)).ravel()
        arr = arr[np.isfinite(arr) & (arr > 0)]
        if len(arr) < self.min_history:
            return False, f"Storico insufficiente ({len(arr)} prezzi < {self.min_history}): operatività bloccata"

        returns = np.diff(arr) / arr[:-1]
        recent_vol = float(np.std(returns[-5:]))
        if recent_vol > self.max_daily_vol:
            return False, f"Volatilità estrema: {recent_vol:.2%} giornaliera > {self.max_daily_vol:.2%}"

        history = returns[-61:-1]
        sd = float(np.std(history)) if len(history) >= 10 else 0.0
        if sd > 0:
            z = abs(returns[-1] - float(np.mean(history))) / sd
            if z > self.outlier_sigma:
                return False, f"Movimento anomalo: ultimo rendimento {returns[-1]:+.2%} (z = {z:.1f})"
        return True, "OK"
