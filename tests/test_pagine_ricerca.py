"""Pagine di ricerca (Strategie, Strategia automatica, Backtest, Laboratorio, Qualità, Grandi investitori): si aprono
sulla copia della demo, senza "None"/"nan" né formati inglesi a schermo, con i testi italiani (docs/revisione_grafica.md
§4.5, compito T8). Stesso schema di tests/test_app_pages.py (AppTest, rete disattivata)."""
from __future__ import annotations

import re
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

PAGES = ["strategie", "strategia-automatica", "backtest", "laboratorio", "qualita", "fondi"]
TITLES = {"strategie": "Strategie", "strategia-automatica": "Strategia automatica",
          "backtest": "Backtest e ricerca", "laboratorio": "Laboratorio della strategia", "qualita": "Qualità dei dati",
          "fondi": "Grandi investitori"}
BAD = re.compile(r"\bNone\b|\bnan\b|\bNaN\b|-0[.,]0+x|Choose options|run_id|as_of\b")


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ricerca")
    db = tmp / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp / 'facts.db'}\n  backup_dir: {tmp / 'backups'}\n",
                      encoding="utf-8")

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    from modules import quotes, updater

    def no_start(*args, **kwargs):
        raise AssertionError("i test non devono avviare main.py")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        mp.setattr(updater, "start", no_start)
        yield config


def _run(url_path: str, state: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at._page_hash = calc_hash(url_path)
    return at.run()


def _texts(at: AppTest) -> str:
    """Testo visibile prodotto dalla pagina: markdown, didascalie, titoli e HTML di st.html (senza la cornice)."""
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption] + [e.value for e in at.title]
    parts += [e.value for e in at.subheader]
    for node in at.get("html"):
        body = getattr(node, "proto", None)
        parts.append(getattr(body, "body", "") or "")
    return "\n".join(str(p) for p in parts)


@pytest.mark.parametrize("url_path", PAGES)
def test_page_opens_clean(app_env, url_path):
    at = _run(url_path)
    assert not [e.value for e in at.exception], url_path
    assert TITLES[url_path] in [t.value for t in at.title], url_path  # la pagina è stata davvero disegnata
    text = _texts(at)
    bad = BAD.findall(re.sub(r"<style.*?</style>", "", text, flags=re.S))
    assert not bad, f"{url_path}: {bad}"
    assert not at.info and not at.warning, f"{url_path}: st.info/st.warning usati al posto di empty_state/notice"


def test_multiselect_placeholders_italian(app_env):
    for url in ("strategia-automatica", "qualita"):
        at = _run(url)
        for ms in at.multiselect:
            assert "Choose" not in (ms.placeholder or ""), (url, ms.label)


def test_automatic_strategy_says_not_the_verdict(app_env):
    text = _texts(_run("strategia-automatica"))
    assert "Giudizio della scheda azienda" in text and "simulati" in text


def test_strategy_builder_opens(app_env):
    at = _run("strategie", {"st_view": "Crea o modifica"})
    assert not [e.value for e in at.exception]
    assert any(b.label == "Salva la strategia" for b in at.button)


def test_run_label():
    from ui.pages.backtest import run_label

    assert run_label("20261101_141723") == "01/11/2026 14:17"
    assert run_label(None) == "–"


def test_lab_overrides_guard():
    from ui.pages.laboratorio import _lab_overrides

    assert _lab_overrides({}) and _lab_overrides(None)
    assert not _lab_overrides({"strategy": {"mode": "rules"}})


def _fake_positions():
    import pandas as pd

    rows = []
    for cik, fund in (("1", "Fondo Uno"), ("2", "Fondo Due")):
        for t, ch, w in (("AAPL", "nuova", 0.6), ("MSFT", "aumentata", 0.3), (None, "ridotta", 0.1)):
            rows.append({"fund_cik": cik, "fund_name": fund, "report_date": pd.Timestamp("2026-09-30"),
                         "issuer": f"EMITTENTE {t or 'X'}", "ticker": t, "change": ch, "weight": w,
                         "value": w * 2e9, "shares": w * 1e7, "shares_change": None if ch == "nuova" else 0.12})
    rows.append({**rows[0], "change": "uscita", "weight": 0.0, "value": 0.0, "shares": 0.0, "shares_change": -1.0,
                 "ticker": "NVDA"})
    return pd.DataFrame(rows)


def _fake_filings():
    import pandas as pd

    return pd.DataFrame({"filed": [pd.Timestamp("2026-11-01"), pd.Timestamp("2026-10-30")], "ticker": ["AAPL", "MSFT"],
                         "form": ["10-Q", "10-K"], "status": ["caricato", "da caricare"],
                         "loaded_at": [pd.Timestamp("2026-11-01 20:00"), None], "accession": ["0001", "0002"]})


def test_pages_with_funds_and_filings(app_env, monkeypatch):
    """Percorsi che la demo non ha: portafogli 13F e depositi SEC recenti (dati finti)."""
    from modules.dashboard_data import DashboardData

    monkeypatch.setattr(DashboardData, "fund_positions", lambda self: _fake_positions())
    monkeypatch.setattr(DashboardData, "recent_filings", lambda self, days=14: _fake_filings())
    for url in ("fondi", "qualita"):
        at = _run(url)
        assert not [e.value for e in at.exception], url
        text = _texts(at)
        assert not BAD.findall(re.sub(r"<style.*?</style>", "", text, flags=re.S)), url
        assert "./azienda?t=AAPL" in text, url  # ticker cliccabili verso la scheda
