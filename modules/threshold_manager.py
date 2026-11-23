"""Soglie di confidenza dinamiche: più il mercato è ostile, più il sistema è selettivo."""
from __future__ import annotations


class ThresholdManager:
    def __init__(self, thresholds: dict[str, float] | None = None, default: float = 0.65):
        self.regime_map = thresholds or {"Bull": 0.50, "Neutral": 0.65, "Bear": 0.80}
        self.default_threshold = default

    def get_threshold(self, regime: str) -> float:
        return float(self.regime_map.get(regime, self.default_threshold))
