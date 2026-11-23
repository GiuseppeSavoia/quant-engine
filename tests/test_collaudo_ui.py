"""Difetti trovati nel collaudo da utente vero (2026-11-16) e corretti: un test per ognuno, senza rete."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
sys.path.insert(0, str(ROOT))

needs_demo = pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")


@pytest.fixture()
def dd(tmp_path):
    from data.db_manager import DBManager
    from modules.config import load_config
    from modules.dashboard_data import DashboardData

    shutil.copy(DEMO_DB, tmp_path / "demo.db")
    cfg = load_config(None)
    cfg["data"]["db_path"] = str(tmp_path / "demo.db")
    cfg["etf"] = {**cfg["etf"], "cache_dir": str(tmp_path / "etf"), "fetch_pause_seconds": 0}
    return DashboardData(cfg, DBManager(cfg["data"]["db_path"]), tmp_path)


# ---------------------------------------------------------------- copia dei dati con --config
def test_prefs_and_subprocess_follow_config(tmp_path, monkeypatch):
    """Con --config su una copia, le preferenze stanno accanto alla copia e main.py riceve la stessa config."""
    from ui import common

    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"data:\n  db_path: {tmp_path / 'copia.db'}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["app.py", "--config", str(cfg)])
    assert common.prefs_path("preferenze_x.json") == tmp_path / "preferenze_x.json"
    assert common.main_config_args() == ["--config", str(cfg.resolve())]
    monkeypatch.setattr(sys, "argv", ["app.py", "--demo"])
    assert common.prefs_path("preferenze_x.json") == common.PROJECT_ROOT / "data" / "preferenze_x.json"
    assert common.main_config_args() == ["--demo"]
    monkeypatch.setattr(sys, "argv", ["app.py"])
    assert common.main_config_args() == []


@needs_demo
def test_demo_database_detected_and_currency_fallback(dd):
    """Il database demo si riconosce anche aperto da una copia (niente quotazioni vere di Yahoo sui prezzi simulati);
    la valuta di un titolo solo nel portafoglio viene dalle sue operazioni o dal suffisso di borsa."""
    assert dd.is_demo
    assert dd.currency("AAPL") == "USD" and dd.currency("ENI.MI") == "EUR" and dd.currency("NESN.SW") == "CHF"
    dd.db.add_transaction({"ticker": "XYZ", "kind": "BUY", "date": pd.Timestamp("2026-01-05"), "quantity": 1,
                           "price": 10.0, "currency": "GBP", "fx": 0.85, "fees": 0.0, "note": None})
    assert dd.currency("XYZ") == "GBP"


# ---------------------------------------------------------------- portafoglio
def _tx(rows):
    df = pd.DataFrame(rows, columns=["ticker", "kind", "date", "quantity", "price", "currency", "fx", "fees", "note"])
    df.insert(0, "id", range(1, len(df) + 1))
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_missing_fx_listed_and_no_partial_weights():
    """Un acquisto in dollari senza cambio (né scritto né nel database) va segnalato e i pesi non sono parziali."""
    from modules import portfolio as pf

    tx = _tx([("ENI.MI", "BUY", "2026-06-01", 100, 14.0, "EUR", np.nan, 5.0, None),
              ("MSFT", "BUY", "2026-06-01", 5, 400.0, "USD", np.nan, 0.0, None)])
    miss = pf.missing_fx(tx, pd.DataFrame())
    assert list(miss["ticker"]) == ["MSFT"]
    pos, _ = pf.positions(tx, pd.DataFrame(), {"ENI.MI": 25.0, "MSFT": 100.0}, {"EUR": 1.0, "USD": 1.1})
    assert pos.set_index("ticker")["cost_eur"].isna()["MSFT"] and pos["weight"].notna().all()  # valori noti
    pos, _ = pf.positions(tx, pd.DataFrame(), {"ENI.MI": 25.0, "MSFT": 100.0}, {"EUR": 1.0, "USD": np.nan})
    assert pos["weight"].isna().all()                        # prima: ENI 100% con MSFT senza valore in euro


def test_held_quantity_for_sell_check():
    from ui.pages.portafoglio import _held_quantity

    tx = _tx([("ENI.MI", "BUY", "2026-06-01", 100, 14.0, "EUR", np.nan, 0.0, None),
              ("ENI.MI", "SELL", "2026-09-01", 40, 15.0, "EUR", np.nan, 0.0, None),
              ("ENI.MI", "BUY", "2026-10-01", 10, 15.0, "EUR", np.nan, 0.0, None)])
    assert _held_quantity(tx, "ENI.MI", "2026-09-15") == pytest.approx(60)
    assert _held_quantity(tx, "ENI.MI", "2026-10-02") == pytest.approx(70)
    assert _held_quantity(tx, "AAPL", "2026-10-02") == 0.0
    assert _held_quantity(pd.DataFrame(), "AAPL", "2026-10-02") == 0.0


# ---------------------------------------------------------------- diario
def test_review_due_follows_calendar_not_last_price():
    """Una revisione fissata a oggi compare anche se l'ultimo prezzo è di qualche giorno fa."""
    from modules import journal

    idx = pd.date_range("2026-01-01", "2026-11-02", freq="B")
    close = pd.DataFrame({"SPY": np.linspace(100, 120, len(idx)), "AMZN": np.linspace(50, 60, len(idx))}, index=idx)
    today = pd.Timestamp.today().normalize()
    j = pd.DataFrame({"ticker": ["AMZN"], "date": [today], "action": ["WATCH"], "price": [60.0],
                      "review_date": [today], "reviewed_at": [None]})
    assert bool(journal.outcomes(j, close, "SPY").loc[0, "review_due"])


def test_review_text():
    from ui.pages.diario import review_text

    assert review_text("Troppo presto", "il prezzo non è sceso") == "Troppo presto: il prezzo non è sceso"
    assert review_text(None, None) == "–" and review_text("Avevo torto", "") == "Avevo torto"


# ---------------------------------------------------------------- valutazione, grafici, testi
def test_valuation_notes():
    from ui.pages.azienda.valutazione import explain_check, outside_range_note

    assert "sopra l'intervallo" in outside_range_note({"expected": 368.3}, {"p10": 241.0, "p90": 352.0})
    assert outside_range_note({"expected": 300.0}, {"p10": 241.0, "p90": 352.0}) == ""
    assert outside_range_note({"expected": 300.0}, None) == ""
    assert "flussi negativi" in explain_check("Il 101% del valore viene da dopo il decimo anno: x")
    assert explain_check("Il 91% del valore viene da dopo il decimo anno: x").endswith(": x")


def test_chart_dates_in_italian_format():
    """Le date dei grafici non escono in inglese ("Jan 2026"): formati numerici sugli assi con date."""
    from ui.common import _layout

    idx = pd.date_range("2026-01-01", periods=30)
    fig = go.Figure(go.Scatter(x=idx, y=np.arange(30)))
    _layout(fig)
    assert fig.layout.xaxis.hoverformat == "%d/%m/%Y" and len(fig.layout.xaxis.tickformatstops) == 4
    cat = _layout(go.Figure(go.Bar(x=["2019", "2020"], y=[1, 2])))
    assert not cat.layout.xaxis.tickformatstops                # anni come categorie: invariati
    pct = go.Figure(go.Scatter(x=idx, y=np.arange(30)))
    pct.update_xaxes(tickformat="%Y")
    assert not _layout(pct).layout.xaxis.tickformatstops       # formato scelto dalla pagina: resta


def test_plural_and_alert_tone():
    from ui import components as C

    assert C.plural(1, "azienda", "aziende") == "1 azienda" and C.plural(3, "azienda", "aziende") == "3 aziende"
    assert C.plural(1200, "valore", "valori") == "1.200 valori"
    assert C.ALERT_TONES["VRD"] == "info" and "info" in C.alert_badge("VRD")


@needs_demo
def test_my_companies_table_portfolio_only_titles(dd, monkeypatch):
    """In "Oggi" un ETF o un titolo solo nel portafoglio non è "dati insufficienti": nessun giudizio, e il prezzo
    viene dalla quotazione (come nella pagina Portafoglio)."""
    from ui.pages import monitor

    monkeypatch.setattr(monitor, "_live_last", lambda tickers: {t: 169.2 for t in tickers})
    verdicts = pd.DataFrame({"verdict": ["?"], "category": ["dati_insufficienti"], "rule": [""]}, index=["VWCE.DE"])
    out = monitor.my_companies_table(dd, pd.DataFrame(columns=["last", "change_pct"]), ["VWCE.DE", "AAPL"],
                                     verdicts, {}, {}, set(), {}, {}).set_index("ticker")
    assert out.at["VWCE.DE", "category"] == monitor.NOT_JUDGED and out.at["VWCE.DE", "last"] == pytest.approx(169.2)
    assert np.isfinite(out.at["AAPL", "last"])                 # dal database
    assert "–" in monitor._verdict_cell(None, {"category": monitor.NOT_JUDGED})


# ---------------------------------------------------------------- pagine (AppTest, come test_portafoglio_ui)
@pytest.fixture(scope="module")
def app_copy(tmp_path_factory):
    """Copia della demo: una decisione con revisione oggi, un acquisto in USD senza cambio, niente rete."""
    import socket

    from data.db_manager import DBManager
    from modules import quotes

    tmp = tmp_path_factory.mktemp("collaudo")
    db_path = tmp / "demo.db"
    shutil.copy(DEMO_DB, db_path)
    db = DBManager(db_path)
    today = pd.Timestamp.today().normalize()
    db.add_decision({"ticker": "AMZN", "date": today, "action": "WATCH", "price": 100.0, "reason": "collaudo",
                     "review_date": today})
    db.add_transaction({"ticker": "MSFT", "kind": "BUY", "date": pd.Timestamp("2026-06-01"), "quantity": 5,
                        "price": 400.0, "currency": "USD", "fx": None, "fees": 0.0, "note": None})
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db_path}\n  facts_db_path: {tmp / 'facts.db'}\n"
                      f"  backup_dir: {tmp / 'backups'}\netf:\n  cache_dir: {tmp / 'etf'}\n", encoding="utf-8")

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        yield tmp


@needs_demo
def test_journal_page_review_today_and_column(app_copy):
    from tests.test_portafoglio_ui import _run, _text

    at = _run("diario")
    assert not at.exception
    text = _text(at)
    assert "Da rivedere" in text and "La tua revisione" in text
    assert at.radio[0].value is None                         # nessun giudizio scelto in anticipo


@needs_demo
def test_portfolio_page_missing_fx_and_copy_menu(app_copy):
    from tests.test_portafoglio_ui import _run, _text

    at = _run("portafoglio")
    assert not at.exception
    text = _text(at)
    assert "operazione mancante" in text and "MSFT del 01/06/2026" in text
    # copia della demo con --config: niente aggiornamento automatico né "Aggiorna dati" (toccherebbero quelli veri)
    assert not any(b.label == "Aggiorna dati" for b in at.button)
    assert "Copia dei dati simulati" in text
