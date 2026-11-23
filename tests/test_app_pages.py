"""Ogni pagina della webapp si apre senza eccezioni (Streamlit AppTest su una copia del database demo, senza rete).

AppTest non sa navigare tra pagine definite da funzioni (`AppTest.switch_page` vuole un file): si imposta
l'indirizzo della pagina come farebbe il browser (lo script hash di `st.Page` = hash del suo url_path).
"""
from __future__ import annotations

import shutil
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402

pytestmark = pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")

sys.path.insert(0, str(ROOT))
from modules import commands  # noqa: E402
from ui import nav  # noqa: E402

TIMEOUT = 120


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    """Copia del database demo in una cartella temporanea, config che la usa, niente rete."""
    tmp = tmp_path_factory.mktemp("webapp")
    db = tmp / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp / 'facts.db'}\n  backup_dir: {tmp / 'backups'}\n",
                      encoding="utf-8")

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    from modules import quotes

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)  # niente thread che scarica le quotazioni
        yield config


def _run(url_path: str | None = None, state: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    if url_path is not None:
        at._page_hash = calc_hash(url_path)  # come aprire /<url_path> nel browser
    return at.run()


def _assert_ok(at: AppTest, what: str):
    errors = [e.value for e in at.exception]
    assert not errors, f"{what}: {errors}"


def test_default_page_is_monitor(app_env):
    at = _run()
    _assert_ok(at, "Monitor")
    assert len(at.caption) > 0  # la pagina ha disegnato i suoi pannelli


@pytest.mark.parametrize("url_path", [p for p in nav.SPECS if p])
def test_page_opens(app_env, url_path):
    _assert_ok(_run(url_path), url_path)


@pytest.mark.parametrize("fn", [code for code, _ in commands.FUNCTIONS])
def test_company_functions(app_env, fn):
    _assert_ok(_run("azienda", {"co_fn": fn}), f"azienda {fn}")


def test_command_bar_switches_page(app_env):
    at = _run()
    at.text_input(key="qe_cmd").input("ESPLORA").run()
    _assert_ok(at, "comando ESPLORA")
    assert any("esplora" in t.value.lower() for t in at.title)


def test_today_page_with_my_companies_and_alerts(app_env):
    """Oggi (Monitor) con aziende seguite e avvisi: tabella con il verdetto, avvisi, "segna come visti"."""
    import yaml

    from data.db_manager import DBManager

    db = DBManager(yaml.safe_load(app_env.read_text())["data"]["db_path"])
    db.add_to_watchlist(["AAPL", "MSFT"])
    db.add_alerts([{"ticker": "AAPL", "code": "MOV", "key": "t", "message": "-12% in un mese",
                    "data": {"ret_1m": -0.12}}], "2026-11-13T10:00:00")
    try:
        at = _run()
        _assert_ok(at, "Oggi con aziende seguite")
        page = " ".join(str(getattr(e, "proto", "")) for e in at.main)
        assert "azienda?t=AAPL" in page and "qe-badge" in page
        at.button(key="mon_alerts_seen").click().run()
        _assert_ok(at, "segna gli avvisi come visti")
        assert db.load_alerts(unseen=True).empty
    finally:
        db.remove_from_watchlist(["AAPL", "MSFT"])


def test_gp_medie_disabled_with_benchmark(app_env):
    """Collaudo #10: nel confronto con l'indice (base 100) le medie non si disegnano: interruttore spento e bloccato."""
    at = _run("azienda", {"co_fn": "GP", "gp_bench": True})
    _assert_ok(at, "GP vs indice")
    for label in ("Medie", "Operazioni", "10-K"):  # anche operazioni e depositi: spenti, non accesi e bloccati
        t = [t for t in at.toggle if t.label == label]
        assert t and t[0].value is False and t[0].disabled, label
    at = _run("azienda", {"co_fn": "GP"})
    for label in ("Medie", "Operazioni", "10-K"):
        t = [t for t in at.toggle if t.label == label]
        assert t and t[0].value is True and not t[0].disabled, label


def test_pipeline_con_aziende(app_env):
    """Pipeline con aziende in fasi diverse: schede con cosa manca, storico; e la scheda azienda mostra la fase."""
    import yaml

    from data.db_manager import DBManager

    db = DBManager(yaml.safe_load(app_env.read_text())["data"]["db_path"])
    db.set_pipeline_stage("AAPL", "pronta", "prova")
    db.set_pipeline_stage("MSFT", "idea")
    try:
        at = _run("pipeline")
        _assert_ok(at, "Pipeline")
        page = " ".join(str(getattr(e, "proto", "")) for e in at.main)
        assert "qe-pipe-card" in page and "manca:" in page and "azienda?t=AAPL" in page
        at = _run("azienda", {"ticker": "AAPL"})
        _assert_ok(at, "scheda con la fase della pipeline")
    finally:
        db.remove_from_pipeline("AAPL")
        db.remove_from_pipeline("MSFT")


def test_stime_con_dati_ed_esporta(app_env):
    """Funzione Stime con stime salvate (multipli sui 12 mesi, confronto col DCF) e pannello Esporta."""
    import yaml

    from data.db_manager import DBManager
    from modules import estimates as es
    from tests.test_estimates import yahoo_raw

    db = DBManager(yaml.safe_load(app_env.read_text())["data"]["db_path"])
    db.save_estimates(es.parse_yahoo("AAPL", yahoo_raw(), "2026-11-20"))
    at = _run("azienda", {"ticker": "AAPL", "co_fn": "EE"})
    _assert_ok(at, "Stime con dati")
    page = " ".join(str(getattr(e, "proto", "")) for e in at.main)
    assert "P/E prossimi 12 mesi" in page and "Prezzo obiettivo" in page
    at.button(key="co_xlsx_make").click().run()
    _assert_ok(at, "prepara il modello Excel")
    assert at.session_state["qe_xlsx_AAPL"][:2] == b"PK"  # uno zip: il file .xlsx


def test_tavolo_dei_grandi_investitori(app_env):
    """Funzione TAV: sintesi, nota fissa "non è una raccomandazione", una riga per investitore con l'esito."""
    at = _run("azienda", {"ticker": "AAPL", "co_fn": "TAV"})
    _assert_ok(at, "Tavolo dei grandi investitori")
    page = " ".join(str(getattr(e, "proto", "")) for e in at.main)
    assert "Il tavolo in sintesi" in page and "non è una raccomandazione" in page
    labels = [e.label for e in at.expander]
    assert len(labels) >= 18 and any("Buffett" in x for x in labels)
