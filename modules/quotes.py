"""
Quotazioni della giornata per la pagina Monitor: indici e mercati (config.yaml, sezione monitor) e i titoli
dell'S&P 500. Yahoo, con circa 15 minuti di ritardo durante la seduta; non entrano nel database né nei backtest.

- `quote_table`: ultimo prezzo, chiusura precedente e variazione da una tabella di chiusure giornaliere (la
  stessa forma per i dati di Yahoo e per i prezzi del database, usati finché le quotazioni non arrivano).
- `QuoteFeed`: scarica i ~500 titoli in un thread, ogni `every_open` secondi a mercato aperto e ogni
  `every_closed` a mercato chiuso (scaricarli tutti richiede ~45 secondi: la pagina non aspetta).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
NY = "America/New_York"


def market_open(now: pd.Timestamp | None = None) -> bool:
    """Borsa di New York aperta (giorni feriali 9:30-16:00; i festivi non si distinguono)."""
    now = (now if now is not None else pd.Timestamp.now(tz="UTC")).tz_convert(NY)
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


def quote_table(close: pd.DataFrame) -> pd.DataFrame:
    """Da chiusure giornaliere (righe = date, colonne = simboli): last, prev, change, change_pct, date.
    L'ultima riga può essere la seduta in corso. Simboli senza due prezzi esclusi."""
    rows = {}
    for sym in close.columns:
        s = close[sym].dropna()
        if len(s) < 2:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        rows[sym] = {"last": last, "prev": prev, "change": last - prev,
                     "change_pct": last / prev - 1 if prev else np.nan, "date": s.index[-1]}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["last", "prev", "change", "change_pct", "date"])


def download_closes(symbols: list[str], days: str = "5d") -> pd.DataFrame:
    """Chiusure giornaliere degli ultimi giorni da Yahoo (l'ultima = seduta in corso, se aperta)."""
    import yfinance as yf

    df = yf.download(list(symbols), period=days, interval="1d", auto_adjust=True, progress=False, threads=True)
    if df is None or df.empty:
        return pd.DataFrame()
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": symbols[0]})
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


class QuoteFeed:
    """Quotazioni dei titoli aggiornate in un thread; `snapshot()` restituisce l'ultima tabella e quando è arrivata."""

    def __init__(self, symbols: list[str], every_open: int = 300, every_closed: int = 3600, fetch=download_closes):
        self.symbols = list(symbols)
        self.every_open, self.every_closed = every_open, every_closed
        self.fetch = fetch
        self._table = pd.DataFrame()
        self._updated: datetime | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def refresh(self) -> None:
        try:
            table = quote_table(self.fetch(self.symbols))
        except Exception as exc:  # noqa: BLE001 - Yahoo che non risponde non deve fermare la pagina
            logger.warning("Quotazioni non aggiornate: %s", exc)
            return
        if not table.empty:
            with self._lock:
                self._table, self._updated = table, datetime.now()

    def _loop(self) -> None:
        while True:
            self.refresh()
            time.sleep(self.every_open if market_open() else self.every_closed)

    def start(self) -> "QuoteFeed":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="quote-feed", daemon=True)
            self._thread.start()
        return self

    def snapshot(self) -> tuple[pd.DataFrame, datetime | None]:
        with self._lock:
            return self._table.copy(), self._updated
