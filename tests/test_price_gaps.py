"""Prezzi rimasti indietro (guasto del 2026-11): 89 titoli dell'indice quotati dopo l'inizio dello storico (ABBV,
META, TSLA...) sono rimasti fermi al 2026-10-30 per due settimane perché, dopo il download completo, il codice li
trattava come "storico troppo corto già chiesto" e non li richiedeva più, senza alcun errore.

Sorgente finta, nessuna rete: ogni ticker ha una data di quotazione; `poison` fa fallire l'intera richiesta (come
un simbolo che manda in errore yfinance), `empty` non restituisce mai dati (delistato)."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from data.db_manager import DBManager
from modules.data_ingestor import DataIngestor
from modules.data_quality import stale_prices_summary


class FakeYahoo:
    def __init__(self, now, listed: dict[str, str], poison=(), empty=()):
        self.now = pd.Timestamp(now)
        self.listed = {t: pd.Timestamp(d) for t, d in listed.items()}
        self.poison, self.empty = set(poison), set(empty)
        self.calls: list[tuple[list[str], pd.Timestamp]] = []

    def download_prices(self, tickers, start):
        self.calls.append((list(tickers), pd.Timestamp(start)))
        if self.poison & set(tickers):
            raise RuntimeError("richiesta rifiutata")
        frames = []
        for t in tickers:
            if t in self.empty or t not in self.listed:
                continue
            days = pd.bdate_range(max(pd.Timestamp(start), self.listed[t]), self.now)
            close = 100.0 + np.arange(len(days)) * 0.01
            frames.append(pd.DataFrame({"ticker": t, "date": days, "open": close, "high": close, "low": close,
                                        "close": close, "volume": 1e6, "split": 0.0}))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def requested(self, ticker):
        return [s for t, s in self.calls if ticker in t]


def _ingestor(cfg, src):
    return DataIngestor(DBManager(cfg["data"]["db_path"]), cfg, price_source=src, sleep=lambda s: None)


def test_recent_listing_keeps_updating_after_full_history(cfg):
    """Il guasto: storico di 18 anni, titolo quotato nel 2013. Dopo il primo download completo deve continuare
    ad aggiornarsi ogni giorno (prima restava fermo per sempre al giorno del download completo)."""
    cfg["data"]["history_years"] = 18
    src = FakeYahoo("2026-10-30", {"SPY": "2000-01-03", "AAPL": "2000-01-03", "ABBV": "2013-01-02"})
    ing = _ingestor(cfg, src)
    tickers = ["SPY", "AAPL", "ABBV"]
    ing.update_prices(tickers, today=src.now.to_pydatetime())
    assert ing.db.last_price_dates()["ABBV"] == pd.Timestamp("2026-10-30")

    src.now = pd.Timestamp("2026-11-12")
    src.calls.clear()
    res = ing.update_prices(tickers, today=src.now.to_pydatetime())
    last = ing.db.last_price_dates()
    assert last["ABBV"] == pd.Timestamp("2026-11-12")            # prima: fermo al 2026-10-30
    assert all(s > pd.Timestamp("2026-10-01") for s in src.requested("ABBV"))  # incrementale, non lo storico
    assert ing.db.first_price_dates()["ABBV"] == pd.Timestamp("2013-01-02")    # storico intatto
    assert res["behind"] == []


def test_failed_block_is_retried_in_smaller_pieces(cfg, caplog):
    """Un simbolo che fa fallire tutta la richiesta non deve lasciare senza prezzi gli altri del blocco."""
    cfg["data"]["price_chunk_size"] = 10
    names = [f"T{i:02d}" for i in range(10)]
    src = FakeYahoo("2026-11-12", {t: "2020-01-02" for t in names + ["SPY"]}, poison=["T07"])
    ing = _ingestor(cfg, src)
    with caplog.at_level(logging.WARNING, logger="modules.data_ingestor"):
        res = ing.update_prices(names + ["SPY"], today=src.now.to_pydatetime())
    last = ing.db.last_price_dates()
    assert set(last) == set(names + ["SPY"]) - {"T07"}           # tutti gli altri arrivano
    assert ["T07"] in [c for c, _ in src.calls]                  # ridotto fino al singolo ticker
    assert res["behind"] == ["T07"]
    msg = [r.getMessage() for r in caplog.records if "Prezzi indietro" in r.getMessage()]
    assert msg and "1 titoli dell'indice" in msg[0] and "T07" in msg[0] and "mai" in msg[0]


def test_lagging_tickers_catch_up_from_their_own_last_day(cfg, caplog):
    """Un blocco che per alcuni giorni non arriva: avviso a fine aggiornamento, poi recupero dal giorno in cui
    ciascun titolo si è fermato (non dall'ultimo giorno del database)."""
    universe = {"SPY": "2000-01-03", "AAA": "2000-01-03", "BBB": "2000-01-03", "CCC": "2000-01-03"}
    src = FakeYahoo("2026-10-30", universe)
    ing = _ingestor(cfg, src)
    tickers = list(universe)
    ing.update_prices(tickers, today=src.now.to_pydatetime())

    # due settimane in cui Yahoo non dà BBB e CCC
    src.empty = {"BBB", "CCC"}
    for day in ["2026-11-05", "2026-11-12"]:
        src.now = pd.Timestamp(day)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="modules.data_ingestor"):
            res = ing.update_prices(tickers, today=src.now.to_pydatetime())
        assert res["behind"] == ["BBB", "CCC"]
        warn = [r for r in caplog.records if "Prezzi indietro" in r.getMessage()]
        assert warn and warn[0].levelno == logging.WARNING
        assert "2 titoli dell'indice" in warn[0].getMessage() and "2026-10-30 (2)" in warn[0].getMessage()

    # torna tutto: chi era indietro riparte dal SUO ultimo giorno e recupera i giorni mancanti
    src.empty = set()
    src.now = pd.Timestamp("2026-11-13")
    src.calls.clear()
    res = ing.update_prices(tickers, today=src.now.to_pydatetime())
    assert res["behind"] == []
    assert min(src.requested("BBB")) <= pd.Timestamp("2026-10-26")
    px = ing.db.load_prices(["BBB"])
    days = set(pd.to_datetime(px["date"]))
    assert set(pd.bdate_range("2026-10-26", "2026-11-13")) <= days  # nessun buco


def test_long_dead_ticker_is_not_requested_every_run(cfg):
    """Un ex membro delistato (ultimo prezzo mesi fa, Yahoo non lo ha più) si riprova solo ogni tanto."""
    db = DBManager(cfg["data"]["db_path"])
    days = pd.bdate_range("2026-01-02", "2026-06-30")
    db.upsert_prices(pd.DataFrame({"ticker": "GONE", "date": days, "close": 10.0}))
    src = FakeYahoo("2026-11-12", {"SPY": "2000-01-03"}, empty=["GONE"])
    db.upsert_prices(pd.DataFrame({"ticker": "SPY", "date": pd.bdate_range("2000-01-03", "2026-11-05"),
                                   "close": 100.0}))
    ing = DataIngestor(db, cfg, price_source=src, sleep=lambda s: None)
    today = pd.Timestamp.now().normalize().to_pydatetime()
    src.now = pd.Timestamp(today)
    ing.update_prices(["SPY", "GONE"], today=today)
    assert src.requested("GONE")                       # la prima volta si prova
    src.calls.clear()
    ing.update_prices(["SPY", "GONE"], today=today)
    assert not src.requested("GONE")                   # poi si aspetta price_dead_retry_days
    assert src.requested("SPY")


def test_stale_prices_summary_groups_by_day():
    issues = pd.DataFrame({
        "ticker": ["ABBV", "META", "TSLA", "ENR.DE"],
        "check": ["prezzo vecchio"] * 4,
        "severity": ["errore"] * 4,
        "detail": ["ultimo prezzo il 2026-10-30"] * 3 + ["ultimo prezzo il 2026-11-05"],
    })
    line = stale_prices_summary(issues)
    assert line.startswith("4 titoli con il prezzo vecchio")
    assert "2026-11-05 (1), 2026-10-30 (3)" in line and "ABBV" in line
    assert stale_prices_summary(issues.iloc[0:0]) == ""
