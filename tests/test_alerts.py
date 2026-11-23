"""Motore di avvisi con storico (modules/alerts.py): regole, deduplicazione, ricomparsa, aggancio a main/updater."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import alerts
from modules.market_data import MarketData
from tests.test_changes import QUARTERS, ANNUAL, _rows


def _data():
    rows = []
    for end, filed, rev, m in QUARTERS:
        rows += _rows("AAA", end, filed, rev, m, "sec_ttm")
    rows += _rows("AAA", *ANNUAL, "sec")
    days = pd.bdate_range("2025-01-01", "2026-07-31")
    px = np.full(len(days), 100.0)
    px[(days >= "2026-06-01") & (days < "2026-07-01")] = 120.0   # sale del 20% il 1/6, torna a 100 il 1/7
    prices = pd.DataFrame({"ticker": "AAA", "date": days, "open": px, "high": px, "low": px, "close": px,
                           "volume": 1e6})
    return MarketData.from_long(prices, pd.DataFrame(rows))


@pytest.fixture
def db(tmp_path):
    return DBManager(tmp_path / "a.db")


def _cfg(cfg, **kw):
    cfg["alerts"].update(kw)
    return cfg


def test_price_target_dedup_and_reappearance(cfg, db):
    data = _data()
    db.set_price_target("AAA", buy_below=105, sell_above=None, note="prezzo d'ingresso")
    cfg = _cfg(cfg, codes=["PX-", "PX+"])
    new = alerts.evaluate(db, data, cfg, "2026-05-20")
    assert [(a["ticker"], a["code"]) for a in new] == [("AAA", "PX-")] and "105" in new[0]["message"]
    assert alerts.evaluate(db, data, cfg, "2026-05-21") == []                     # condizione ancora vera: niente
    assert alerts.evaluate(db, data, cfg, "2026-06-10") == []                     # prezzo 120: si chiude
    hist = db.load_alerts()
    assert len(hist) == 1 and hist["cleared_at"].iloc[0].startswith("2026-06-10")
    again = alerts.evaluate(db, data, cfg, "2026-07-10")                          # di nuovo a 100: ricompare
    assert [a["code"] for a in again] == ["PX-"]
    assert len(db.load_alerts()) == 2 and len(db.load_alerts(active=True)) == 1
    # soglia cambiata dall'utente: è un avviso diverso
    db.set_price_target("AAA", buy_below=110)
    assert [a["key"] for a in alerts.evaluate(db, data, cfg, "2026-07-11")] == ["110"]
    db.set_price_target("AAA", sell_above=115)
    assert [a["code"] for a in alerts.evaluate(db, data, cfg, "2026-06-15")] == ["PX+"]


def test_new_filing_with_relevant_changes(cfg, db):
    data = _data()
    db.add_to_watchlist(["AAA"])
    cfg = _cfg(cfg, codes=["CHG"])
    assert alerts.evaluate(db, data, cfg, "2026-04-20") == []        # l'esercizio è di febbraio: non più "nuovo"
    new = alerts.evaluate(db, data, cfg, "2026-05-04")
    assert len(new) == 1 and new[0]["code"] == "CHG" and new[0]["key"] == "2026-03-31"
    assert "Margine operativo da 30,0% a 26,0%" in new[0]["message"]
    assert new[0]["data"]["form"] == "10-Q" and new[0]["data"]["summary"]
    assert alerts.evaluate(db, data, cfg, "2026-05-05") == []
    stored = db.load_alerts().iloc[0]
    assert stored["data"]["period_end"] == "2026-03-31" and stored["seen_at"] is None
    db.mark_alerts_seen()
    assert db.load_alerts(unseen=True).empty


def test_moves_fair_value_reviews_events(cfg, db):
    data = _data()
    db.add_to_watchlist(["AAA"])
    db.save_note("AAA", fair_value=130.0)
    db.save_research("AAA", {"review_date": "2026-06-01"})
    db.add_decision({"ticker": "AAA", "date": "2026-01-10", "action": "WATCH", "price": 100,
                     "review_date": "2026-05-30"})
    db.add_events(pd.DataFrame([{"ticker": "AAA", "accession": "0001-26-000001", "form": "8-K",
                                 "filed": "2026-06-01", "cik": 1}]))
    db.set_event_items("0001-26-000001", "4.02,9.01", None)
    new = {a["code"]: a for a in alerts.evaluate(db, data, cfg, "2026-06-01")}
    assert {"FV", "MOV", "MOV1", "REV", "8K"} <= set(new)
    assert new["MOV1"]["message"].startswith("+20%") and new["MOV"]["key"] == "su"
    assert "4.02" in new["8K"]["data"]["items"]
    revs = db.load_alerts(ticker="AAA")
    assert set(revs.loc[revs["code"] == "REV", "key"]) == {"ricerca-2026-06-01", "diario-1"}
    # codice spento: non si valuta
    assert all(a["code"] != "52S" for a in new.values())


def test_followed_tickers_and_disabled(cfg, db):
    db.add_to_watchlist(["AAA"])
    db.add_transaction({"ticker": "BBB", "kind": "BUY", "date": "2026-01-02", "quantity": 5, "price": 10,
                        "currency": "USD"})
    db.add_transaction({"ticker": "CCC", "kind": "BUY", "date": "2026-01-02", "quantity": 5, "price": 10,
                        "currency": "USD"})
    db.add_transaction({"ticker": "CCC", "kind": "SELL", "date": "2026-02-02", "quantity": 5, "price": 12,
                        "currency": "USD"})
    db.set_price_target("DDD", buy_below=1)
    assert alerts.followed_tickers(db) == ["AAA", "BBB", "DDD"]
    cfg["alerts"]["enabled"] = False
    assert alerts.evaluate(db, _data(), cfg, "2026-06-01") == []


def test_shared_rules_match_home_texts():
    assert alerts.fair_value_alert(100, 120) == ("FV", "sotto il tuo fair value: margine +20%")
    assert alerts.fair_value_alert(100, 97) == ("FV~", "vicino al tuo fair value (-3%)")
    assert alerts.fair_value_alert(100, 120, margin=0.25) is None
    assert alerts.fair_value_alert(np.nan, 120) is None
    assert alerts.move_alert(-0.2, 0.15) == ("MOV", "-20% in un mese")
    assert alerts.review_alert(None, pd.Timestamp("2026-01-01")) is None


def test_notification_and_main_hook(cfg, db, tmp_path):
    from main import run_alerts
    from modules import updater

    cfg["data"]["db_path"] = str(tmp_path / "a.db")
    db.upsert_prices(pd.DataFrame({"ticker": ["AAA"], "date": ["2026-06-01"], "open": [1.0], "high": [1.0],
                                   "low": [1.0], "close": [1.0], "volume": [1.0]}))
    db.set_price_target("AAA", buy_below=105)
    new = run_alerts(_cfg(cfg, codes=["PX-"]), db, _data(), today=pd.Timestamp("2026-05-20 18:00"))
    assert len(new) == 1
    text = updater.summary(cfg, since="2026-05-20")
    assert "1 avvisi nuovi: AAA PX-" in text
    assert alerts.notification_text([]) == ""
    # un errore nel motore non blocca main.py
    assert run_alerts(cfg, None, _data()) == []
