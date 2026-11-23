"""Portafoglio: esposizione vera dentro gli ETF (DashboardData.portfolio_look_through) e pagine dei moduli
(Portafoglio, Diario, Inserisci dati, Il mio metodo) con AppTest su una copia della demo, senza rete."""
from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
pytestmark = pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")
sys.path.insert(0, str(ROOT))

from tests.test_etf import RAW, FakeSource  # noqa: E402

TODAY = "2026-11-13"


def _tx(db, ticker, qty, price, currency="EUR", kind="BUY", day="2025-03-10"):
    return db.add_transaction({"ticker": ticker, "kind": kind, "date": pd.Timestamp(day), "quantity": qty,
                               "price": price, "currency": currency, "fx": 1.08 if currency == "USD" else None,
                               "fees": 1.0, "note": None})


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


def _positions(d, values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"ticker": list(values), "value_eur": list(values.values()),
                         "name": [d.info(t).get("name") or "" for t in values]})


def test_look_through_never_downloads_without_request(dd):
    pos = _positions(dd, {"AAPL": 1000.0, "VWCE.DE": 3000.0})
    src = FakeSource()
    out = dd.portfolio_look_through(pos, source=src, today=TODAY)
    assert src.calls == []                                   # cache vuota: niente scarico all'apertura
    assert out["status"] == "empty" and out["to_download"] == ["VWCE.DE"]
    assert out["look"]["summary"]["undetailed_eur"] == pytest.approx(3000.0)
    assert dd.etf_cache_path().name == "composizioni_demo.json"   # la demo non tocca la cache vera


def test_look_through_refresh_then_cache_then_expiry(dd):
    pos = _positions(dd, {"AAPL": 1000.0, "VWCE.DE": 3000.0, "SXR8.DE": 2000.0})
    src = FakeSource()
    out = dd.portfolio_look_through(pos, refresh=True, source=src, today=TODAY)
    assert sorted(src.calls) == ["SXR8.DE", "VWCE.DE"]       # AAPL è un'azienda nota: non si chiede a Yahoo
    assert out["status"] == "ok" and not out["to_download"]
    comp = out["look"]["companies"].set_index("symbol")
    # AAPL = diretta 1000 + 4,5% di 3000 + 7,7% di 2000
    assert comp.loc["AAPL", "total_eur"] == pytest.approx(1000 + 135 + 154)
    assert len(out["overlaps"]) == 1 and out["overlaps"][0]["lower_bound"]
    ov = out["overlaps"][0]
    assert ov["overlap"] <= ov["upper_bound"]
    # seconda apertura: dalla cache, nessuno scarico
    src2 = FakeSource()
    dd.portfolio_look_through(pos, source=src2, today=TODAY)
    assert src2.calls == []
    # copia scaduta (oltre 30 giorni): si riscarica da sola
    src3 = FakeSource()
    dd.portfolio_look_through(pos, source=src3, today="2027-01-20")
    assert sorted(src3.calls) == ["SXR8.DE", "VWCE.DE"]


def test_aliases_from_config(dd):
    raw = json.loads(json.dumps(RAW))
    raw["VWCE.DE"]["holdings"].append({"symbol": "NVD.DE", "name": "NVIDIA Corp", "weight": 0.01})
    dd.cfg["etf"]["aliases"] = {"NVD.DE": "NVDA"}
    out = dd.portfolio_look_through(_positions(dd, {"VWCE.DE": 1000.0}), refresh=True, source=FakeSource(raw),
                                    today=TODAY)
    syms = set(out["look"]["companies"]["symbol"])
    assert "NVD.DE" not in syms and "NVDA" in syms


def test_etf_settings_defaults_and_config():
    from modules import etf

    s = etf.settings({})
    assert s["max_age_days"] == etf.CACHE_MAX_AGE_DAYS and s["cache_dir"] == etf.CACHE_DIR
    s = etf.settings({"etf": {"cache_dir": "data/cache/x", "max_age_days": 7, "aliases": {"APC.DE": "AAPL"}}})
    assert s["cache_dir"] == ROOT / "data" / "cache" / "x" and s["max_age_days"] == 7
    assert s["aliases"] == {"APC.DE": "AAPL"}


# ---------------------------------------------------------------- pagine (AppTest)
pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402


@pytest.fixture(scope="module")
def app_db(tmp_path_factory):
    """Copia della demo con alcune operazioni (un'azione in USD, un ETF senza prezzi, un dividendo, una vendita),
    cache delle composizioni finta, niente rete."""
    from data.db_manager import DBManager
    from modules import etf, quotes

    tmp = tmp_path_factory.mktemp("portafoglio")
    db_path = tmp / "demo.db"
    shutil.copy(DEMO_DB, db_path)
    db = DBManager(db_path)
    _tx(db, "AAPL", 10, 50.0, "USD", day="2025-01-10")
    _tx(db, "AAPL", 2, 60.0, "USD", kind="SELL", day="2025-06-10")
    _tx(db, "AAPL", 8, 0.25, "USD", kind="DIV", day="2025-08-10")
    _tx(db, "VWCE.DE", 5, 120.0, "EUR", day="2025-02-10")
    _tx(db, "SXR8.DE", 3, 500.0, "EUR", day="2025-02-11")
    db.add_decision({"ticker": "AAPL", "date": pd.Timestamp("2025-01-10"), "action": "BUY", "price": 50.0,
                     "reason": "prova", "review_date": pd.Timestamp("2025-07-10")})
    cache = etf.JsonCache(tmp / "etf" / "composizioni_demo.json")
    etf.EtfIngestor(FakeSource(), cache, pause=0).get_many(["VWCE.DE", "SXR8.DE"], today=pd.Timestamp.today())
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
        yield db


def _run(url_path: str, state: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at._page_hash = calc_hash(url_path)
    return at.run()


def _text(at: AppTest) -> str:
    """Tutto il testo della pagina: markdown, didascalie, HTML e tabelle."""
    out: list[str] = []

    def walk(node):
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            for c in children.values():
                walk(c)
        v = getattr(node, "value", None)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, pd.DataFrame):
            out.append(v.to_string())
        body = getattr(getattr(node, "proto", None), "body", None)
        if isinstance(body, str):
            out.append(body)

    walk(at._tree)
    return " ".join(out)


@pytest.mark.parametrize("url_path", ["portafoglio", "diario", "dati", "metodo"])
def test_form_pages_open_without_none(app_db, url_path):
    at = _run(url_path)
    assert not [e.value for e in at.exception], url_path
    text = _text(at)
    assert "None" not in text and "nan" not in text.split()


def test_portfolio_tabs_and_tax_note(app_db):
    at = _run("portafoglio")
    labels = [t.label for t in at.tabs]
    assert labels[:3] == ["Posizioni", "Esposizione vera", "Sovrapposizioni ETF"]
    assert any(t.label.startswith("Operazioni") for t in at.tabs)


def test_portfolio_delete_asks_confirmation(app_db):
    n = len(app_db.load_transactions())
    tx_id = int(app_db.load_transactions()["id"].max())
    at = _run("portafoglio", {"port_del": tx_id})
    assert not at.exception
    assert len(app_db.load_transactions()) == n               # solo la richiesta di conferma
    at.button(key="port_del_no").click().run()
    assert len(app_db.load_transactions()) == n
    assert "port_del" not in at.session_state


def test_journal_outcomes_with_ticker_without_prices():
    """Una decisione su un titolo non ancora nel database (es. un ETF appena comprato) non rompe il diario."""
    import numpy as np

    from modules import journal

    idx = pd.date_range("2025-01-01", periods=200, freq="B")
    close = pd.DataFrame({"SPY": np.linspace(100, 120, 200), "AAPL": np.linspace(50, 60, 200)}, index=idx)
    j = pd.DataFrame({"ticker": ["VWCE.DE", "AAPL"], "date": pd.to_datetime(["2025-02-03", "2025-02-03"]),
                      "action": ["BUY", "BUY"], "price": [np.nan, np.nan], "review_date": [pd.NaT, pd.NaT],
                      "reviewed_at": [None, None]})
    out = journal.outcomes(j, close, "SPY")
    assert np.isnan(out.loc[0, "ret"]) and out.loc[0, "right"] is None
    assert out.loc[1, "ret"] > 0


def test_value_history_with_ticker_without_prices():
    """Un titolo inserito ma senza prezzi nel database non manda in errore l'andamento del portafoglio."""
    import numpy as np

    from modules import portfolio as pf

    idx = pd.date_range("2025-01-01", periods=50, freq="B")
    close = pd.DataFrame({"SPY": np.linspace(100, 110, 50), "AAPL": np.linspace(50, 55, 50)}, index=idx)
    tx = pd.DataFrame({"id": [1, 2], "ticker": ["AAPL", "VWCE.DE"], "kind": ["BUY", "BUY"],
                       "date": pd.to_datetime(["2025-01-02", "2025-01-03"]), "quantity": [10.0, 5.0],
                       "price": [50.0, 120.0], "currency": ["EUR", "EUR"], "fx": [None, None], "fees": [0.0, 0.0]})
    h = pf.value_history(tx, close, pd.DataFrame(index=idx), "SPY", "EUR")
    assert not h.empty and h["invested"].iloc[-1] == pytest.approx(1100.0)


def test_my_portfolio_config_is_separate_from_strategy(dd):
    """`my_portfolio` (confronto del portafoglio reale) non si scontra più con `portfolio` (strategia simulata):
    prima config.yaml e DEFAULTS avevano due chiavi `portfolio:` e il benchmark scelto andava perso."""
    import yaml

    from modules import run_info
    from modules.config import DEFAULTS, load_config

    class Strict(yaml.SafeLoader):
        pass

    def no_duplicates(loader, node, deep=False):
        keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
        assert len(keys) == len(set(keys)), f"chiavi doppie: {sorted(k for k in keys if keys.count(k) > 1)}"
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_duplicates)
    yaml.load((ROOT / "config.yaml").read_text(encoding="utf-8"), Loader=Strict)
    assert DEFAULTS["my_portfolio"]["benchmark_currency"] and "method" in DEFAULTS["portfolio"]
    cfg = load_config(None)
    assert cfg["portfolio"]["method"] and "benchmark" not in cfg["portfolio"]      # strategia invariata
    assert cfg["my_portfolio"]["benchmark"]
    other = {**cfg, "my_portfolio": {"benchmark": "FTSEMIB.MI", "benchmark_currency": "EUR"}}
    assert run_info.config_hash(other) == run_info.config_hash(cfg)               # impronte dei risultati salvate
    _tx(dd.db, "AAPL", 10, 150.0, "USD")
    dd.cfg["my_portfolio"] = {"benchmark": "MSFT", "benchmark_currency": "USD"}
    assert dd.portfolio_view()["benchmark"] == "MSFT"


# ---------------------------------------------------------------- collaudo #24 e #15: scheda che resta, modifica
def test_portfolio_tab_stays_after_action(app_db):
    """Dopo un'azione (qui: annulla l'eliminazione, che fa st.rerun) la pagina resta sulla scheda aperta."""
    tx_id = int(app_db.load_transactions()["id"].max())
    at = _run("portafoglio", {"port_tab": "Operazioni", "port_del": tx_id})
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Posizioni", "Esposizione vera", "Sovrapposizioni ETF", "Operazioni",
                                          "Dividendi e tasse"]  # etichette fisse: lo stato non si perde col numero
    at.button(key="port_del_no").click().run()
    assert not at.exception
    assert at.session_state["port_tab"] == "Operazioni"


def test_portfolio_edit_transaction_logic(app_db):
    """Modifica di un'operazione: conferma con i campi cambiati, rifiuto se lascia scoperta una vendita, salvataggio."""
    from modules import portfolio as pf
    from ui.pages import portafoglio as page

    tx = app_db.load_transactions()
    buy = tx[(tx["ticker"] == "AAPL") & (tx["kind"] == "BUY")].iloc[0]
    old = {f: buy[f] for f in page.EDIT_FIELDS}
    new = {**old, "quantity": 12.0, "price": 51.5}
    assert page.edit_changes(old, new) == ["Quantità: 10 → 12", "Prezzo: 50,00 → 51,50"]
    assert page.edit_error(tx, int(buy["id"]), new) is None
    # comprare 1 invece di 10 lascia scoperta la vendita di 2 di giugno
    err = page.edit_error(tx, int(buy["id"]), {**old, "quantity": 1.0})
    assert err and "supera le azioni possedute" in err
    assert page.edit_error(tx, int(buy["id"]), {**old, "ticker": ""}).startswith("Mancano")
    app_db.update_transaction(int(buy["id"]), new)
    after = app_db.load_transactions().set_index("id").loc[int(buy["id"])]
    assert after["quantity"] == 12 and after["price"] == 51.5 and len(app_db.load_transactions()) == len(tx)
    app_db.update_transaction(int(buy["id"]), old)  # rimette com'era per gli altri test
    assert pf.oversold(app_db.load_transactions()) == []


def test_oversold_checks_whole_history():
    from modules import portfolio as pf

    tx = pd.DataFrame({"id": [1, 2, 3], "ticker": ["A", "A", "A"], "kind": ["BUY", "SELL", "SELL"],
                       "date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
                       "quantity": [10.0, 6.0, 6.0]})
    bad = pf.oversold(tx)
    assert len(bad) == 1 and bad[0]["held"] == 4 and bad[0]["sold"] == 6
    assert pf.oversold(tx.iloc[:2]) == []
