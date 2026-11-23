"""Le mie aziende, Esplora e Confronta (docs/revisione_grafica.md §4.3-4.5) con dati: aziende seguite, soglie di prezzo,
storico degli avvisi, filtri per giudizio; e con dati sporchi (una data di prezzo più recente delle altre, che lascia
le metriche vuote) le pagine non devono andare in eccezione. Copia del database demo, niente rete."""
from __future__ import annotations

import shutil
import socket
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402

pytestmark = pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")
sys.path.insert(0, str(ROOT))

TIMEOUT = 120


def _env(tmp: Path, dirty: bool) -> Path:
    db = tmp / "demo.db"
    shutil.copy(DEMO_DB, db)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM price_history WHERE ticker = 'EURUSD=X'")
        if dirty:  # un cambio con una data più recente di tutte le azioni: l'ultima data va avanti, i prezzi "scadono"
            last = conn.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
            later = (pd.Timestamp(last) + pd.Timedelta(days=11)).strftime("%Y-%m-%d")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(price_history)")]
            row = {c: None for c in cols}
            row.update(ticker="EURUSD=X", date=later, close=1.1)
            if "adj_close" in row:
                row["adj_close"] = 1.1
            conn.execute(f"INSERT INTO price_history ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         [row[c] for c in cols])
    from data.db_manager import DBManager

    m = DBManager(db)
    m.add_to_watchlist(["AAPL", "MSFT", "NVDA"])
    m.set_price_target("AAPL", 70.0, 120.0, "prova")
    ids = m.add_alerts([
        {"ticker": "AAPL", "code": "PX-", "key": "70", "message": "prezzo 60.28 sotto la tua soglia d'acquisto 70.00"},
        {"ticker": "NVDA", "code": "MOV", "key": "giu", "message": "-18% in un mese", "data": {"ret_1m": -0.18}},
    ], "2026-11-10T18:00:00")
    m.clear_alerts([ids[1]], "2026-11-12T18:00:00")
    config = tmp / "config.yaml"
    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) if (ROOT / "config.yaml").exists() else {}
    cfg = cfg or {}
    cfg.setdefault("data", {}).update(db_path=str(db), facts_db_path=str(tmp / "facts.db"),
                                      backup_dir=str(tmp / "backups"))
    config.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return config


@pytest.fixture(scope="module", params=[False, True], ids=["pulito", "sporco"])
def app_env(request, tmp_path_factory):
    config = _env(tmp_path_factory.mktemp("elenco"), request.param)

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    from modules import quotes

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        yield config


def _run(url_path: str, state: dict | None = None) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at._page_hash = calc_hash(url_path)
    return at.run()


def _ok(at: AppTest, what: str):
    errors = [e.value for e in at.exception]
    assert not errors, f"{what}: {errors}"


def _text(at: AppTest) -> str:
    parts = [str(v) for e in at.main if (v := getattr(e, "value", None)) is not None]
    return " ".join(parts)


def test_mie_aziende(app_env):
    at = _run("mie-aziende")
    _ok(at, "Le mie aziende")
    assert any("mie aziende" in t.value.lower() for t in at.title)
    assert at.dataframe, "manca la tabella delle aziende seguite"
    assert "None" not in _text(at)


def test_mie_aziende_segna_visti(app_env):
    at = _run("mie-aziende")
    buttons = [b for b in at.button if str(b.label).startswith("Segna come visti")]
    assert buttons, "manca il pulsante per segnare gli avvisi come visti"
    buttons[0].click().run()
    _ok(at, "segna come visti")


def test_esplora_filtri(app_env):
    at = _run("esplora", {"ex_verdict": ["candidata"], "ex_min_quality": 0.5})
    _ok(at, "Esplora con filtri sul giudizio")
    assert any("aziende su" in s.value for s in at.subheader)


def test_confronta(app_env):
    at = _run("confronta", {"cmp": ["AAPL", "MSFT", "NVDA"]})
    _ok(at, "Confronta")
    assert "None" not in _text(at)


def test_parse_price():
    from ui.pages.mie_aziende import _parse_price

    assert _parse_price("70,50") == (70.5, True)
    assert _parse_price("1.234,5") == (1234.5, True)
    assert _parse_price("70.5") == (70.5, True)
    assert _parse_price("") == (None, True)
    assert _parse_price("abc") == (None, False)


def test_styled_niente_none():
    from ui.rules import styled

    df = pd.DataFrame({"a": [1.234, np.nan, -0.0001], "b": [0.1, None, 0.2]})
    html = styled(df, {"a": ("num", {"digits": 1}), "b": "pct"}).to_html()
    assert "None" not in html and "nan" not in html.lower()
    assert "1,2" in html and "–" in html and "10,0%" in html
    assert "−0,0" not in html  # niente zero col segno


def test_verdict_text():
    from ui.rules import verdict_text

    assert verdict_text("candidata") == "✓ Candidata"
    assert verdict_text(None) == ""
