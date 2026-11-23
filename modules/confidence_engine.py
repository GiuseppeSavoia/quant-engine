"""
ConfidenceEngine: voto pesato dei segnali, tutto su scala 0-1.

  confidenza = 0.30 * regime + 0.40 * valore + 0.30 * multi-factor
  regime: Bull = 1, Neutral = 0.5, Bear = 0
  valore: margine di sicurezza DCF, lineare da 0% (0) a `full_value_margin` (1); 0 se non calcolabile

Il trade è valido se la confidenza supera la soglia del regime corrente (ThresholdManager).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from modules.threshold_manager import ThresholdManager

REGIME_SCORE = {"Bull": 1.0, "Neutral": 0.5, "Bear": 0.0}


@dataclass
class ConfidenceResult:
    score: float
    threshold: float
    is_valid: bool
    regime_score: float
    value_score: float
    mf_score: float


class ConfidenceEngine:
    def __init__(self, cfg_confidence: dict):
        w = cfg_confidence["weights"]
        total = sum(w.values())
        self.weights = {k: v / total for k, v in w.items()}
        self.full_value_margin = cfg_confidence["full_value_margin"]
        self.thresholds = ThresholdManager(cfg_confidence["thresholds"], cfg_confidence["default_threshold"])

    def value_score(self, margin: float | None) -> float:
        if margin is None or not np.isfinite(margin):
            return 0.0
        return float(np.clip(margin / self.full_value_margin, 0, 1))

    def evaluate(self, regime: str, margin: float | None, mf_score: float) -> ConfidenceResult:
        r = REGIME_SCORE.get(regime, 0.5)
        v = self.value_score(margin)
        m = float(np.clip(mf_score or 0.0, 0, 1))
        score = self.weights["regime"] * r + self.weights["value"] * v + self.weights["multi_factor"] * m
        threshold = self.thresholds.get_threshold(regime)
        return ConfidenceResult(round(score, 4), threshold, score >= threshold, r, v, m)
