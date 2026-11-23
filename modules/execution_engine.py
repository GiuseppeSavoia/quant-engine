"""
Execution engine in modalità PAPER TRADING.

Trasforma i pesi del portafoglio in ordini simulati (quantità, tipo di ordine,
stop-loss e take-profit basati sull'ATR) e li registra nel database.
NON è collegato a nessun broker: nessun ordine reale viene inviato.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class PaperOrder:
    ticker: str
    side: str
    order_type: str
    shares: float
    weight: float
    reference_price: float
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    est_slippage: float
    status: str = "PAPER"

    def to_dict(self) -> dict:
        return asdict(self)


class ExecutionEngine:
    def __init__(self, cfg_execution: dict):
        self.cfg = cfg_execution

    def order_type(self, annual_vol: float, avg_dollar_volume: float) -> str:
        """Alta volatilità o bassa liquidità -> LIMIT (controllo del prezzo), altrimenti MARKET."""
        if (np.isfinite(annual_vol) and annual_vol > self.cfg["limit_order_vol"]) or \
           (np.isfinite(avg_dollar_volume) and avg_dollar_volume < self.cfg["min_dollar_volume"]):
            return "LIMIT"
        return "MARKET"

    def exits(self, price: float, atr: float, side: str = "LONG") -> tuple[float | None, float | None]:
        if not np.isfinite(atr) or atr <= 0:
            return None, None
        sl, tp = self.cfg["stop_loss_atr"] * atr, self.cfg["take_profit_atr"] * atr
        if side.upper() == "LONG":
            return price - sl, price + tp
        return price + sl, price - tp

    @staticmethod
    def estimate_slippage(order_value: float, avg_dollar_volume: float) -> float:
        """Modello lineare: ogni 1% del volume medio giornaliero costa circa lo 0,1%."""
        if not np.isfinite(avg_dollar_volume) or avg_dollar_volume <= 0:
            return float("nan")
        return order_value / avg_dollar_volume * 0.1

    def build_order(self, ticker: str, weight: float, price: float, atr: float,
                    annual_vol: float, avg_dollar_volume: float, capital: float | None = None) -> PaperOrder | None:
        capital = self.cfg["capital"] if capital is None else capital
        if not np.isfinite(price) or price <= 0 or weight <= 0:
            return None
        shares = math.floor(capital * weight / price)
        if shares <= 0:
            return None
        otype = self.order_type(annual_vol, avg_dollar_volume)
        limit = round(price * (1 + self.cfg["max_slippage"]), 2) if otype == "LIMIT" else None
        sl, tp = self.exits(price, atr)
        return PaperOrder(
            ticker=ticker, side="BUY", order_type=otype, shares=shares, weight=weight,
            reference_price=round(price, 4), limit_price=limit,
            stop_loss=None if sl is None else round(sl, 2), take_profit=None if tp is None else round(tp, 2),
            est_slippage=self.estimate_slippage(shares * price, avg_dollar_volume),
        )
