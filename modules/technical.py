"""Indicatori tecnici di base condivisi dagli altri moduli."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def log_returns(prices: pd.Series) -> pd.Series:
    prices = pd.Series(prices, dtype=float).dropna()
    prices = prices[prices > 0]
    return np.log(prices / prices.shift(1)).dropna()


def annualized_volatility(prices: pd.Series, window: int = TRADING_DAYS) -> float:
    r = log_returns(prices).tail(window)
    return float(r.std() * np.sqrt(TRADING_DAYS)) if len(r) > 2 else float("nan")


def momentum_12_1(prices: pd.Series) -> float:
    """Rendimento da 12 mesi fa a 1 mese fa (si salta l'ultimo mese, che tende a invertire).
    Con meno di un anno di dati usa lo storico disponibile (minimo ~3 mesi)."""
    prices = pd.Series(prices, dtype=float).dropna()
    if len(prices) < 84:
        return float("nan")
    start_idx = max(0, len(prices) - TRADING_DAYS)
    start = prices.iloc[start_idx]
    end = prices.iloc[-22]
    return float(end / start - 1) if start > 0 else float("nan")


def average_true_range(ohlc: pd.DataFrame, period: int = 14) -> float:
    """ATR semplice (media mobile del True Range)."""
    if ohlc.empty or len(ohlc) < period + 1:
        return float("nan")
    close = ohlc["close"]
    high = ohlc["high"].fillna(close) if "high" in ohlc else close
    low = ohlc["low"].fillna(close) if "low" in ohlc else close
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def average_dollar_volume(ohlc: pd.DataFrame, window: int = 20) -> float:
    if ohlc.empty or "volume" not in ohlc:
        return float("nan")
    return float((ohlc["close"] * ohlc["volume"]).tail(window).mean())


def rule_based_regime(prices: pd.Series, window: int = 50) -> str:
    """Regime semplice (pendenza della media mobile), usato come riserva se l'HMM non è applicabile."""
    prices = pd.Series(prices, dtype=float).dropna()
    if len(prices) < window * 2:
        return "Neutral"
    ma = prices.rolling(window).mean()
    slope = (ma.iloc[-1] / ma.iloc[-window] - 1)
    if slope > 0.03 and prices.iloc[-1] > ma.iloc[-1]:
        return "Bull"
    if slope < -0.03 and prices.iloc[-1] < ma.iloc[-1]:
        return "Bear"
    return "Neutral"
