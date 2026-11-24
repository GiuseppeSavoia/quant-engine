"""Aspetto della webapp: Scuro (predefinito dal 2026-11-24), Chiaro, Segui il sistema (ui/common.py, menu "Dati" in ui/shell.py)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui import common

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    path = tmp_path / "preferenze_interfaccia.json"
    monkeypatch.setattr(common, "prefs_path", lambda name: tmp_path / name)
    monkeypatch.setattr(common.st, "session_state", {})
    return path


def test_appearance_default_legacy_and_saved(prefs):
    assert common.appearance() == "scuro"                       # predefinito: l'aspetto del prodotto
    prefs.write_text('{"terminal": true}', encoding="utf-8")     # vecchio interruttore: scelta esplicita
    assert common.appearance() == "scuro"
    prefs.write_text('{"terminal": false}', encoding="utf-8")
    assert common.appearance() == "chiaro"
    prefs.write_text("illeggibile", encoding="utf-8")
    assert common.appearance() == "scuro"
    common.set_appearance("chiaro")
    assert json.loads(prefs.read_text(encoding="utf-8")) == {"aspetto": "chiaro"}
    assert common.st.session_state["ui_reload"] is True           # il browser applica il tema solo ricaricando
    common.set_appearance("viola")                                 # valore sconosciuto: ignorato
    assert common.appearance() == "chiaro"


def test_system_scheme_from_cookie(prefs, monkeypatch):
    common.set_appearance("sistema")
    monkeypatch.setattr(common.st, "context", SimpleNamespace(cookies={"qe_scheme": "light"}))
    assert common.system_scheme() == "light"
    assert common._terminal_wanted() is False                      # sistema + Mac chiaro
    monkeypatch.setattr(common.st, "context", SimpleNamespace(cookies={"qe_scheme": "dark"}))
    assert common._terminal_wanted() is True
    monkeypatch.setattr(common.st, "context", SimpleNamespace(cookies={"qe_scheme": "<script>"}))
    assert common.system_scheme() is None and common._terminal_wanted() is True   # ignoto: scuro, l'aspetto del prodotto
    common.set_appearance("chiaro")                                # scelta fissa: il cookie non conta
    monkeypatch.setattr(common.st, "context", SimpleNamespace(cookies={"qe_scheme": "dark"}))
    assert common._terminal_wanted() is False
    common.set_appearance("scuro")
    assert common._terminal_wanted() is True


def test_scheme_script_reloads_only_on_mismatch():
    js = common.scheme_script("sistema", True)
    assert '{mode: "sistema", rendered: "dark"}' in js
    assert "prefers-color-scheme: dark" in js and "qe_scheme=" in js
    assert "15000" in js and "sessionStorage" in js                 # niente ricarichi a catena
    assert '{mode: "sistema", rendered: "light"}' in common.scheme_script("sistema", False)


def test_apply_theme_switches_server_options():
    from streamlit import config as st_config

    try:
        common._apply_theme(False)
        assert st_config.get_option("theme.base") == "light"
        assert st_config.get_option("theme.primaryColor") == common.CLASSIC_THEME["primaryColor"]
        assert st_config.get_option("theme.backgroundColor") == common.LIGHT_THEME["backgroundColor"]
    finally:
        common._apply_theme(True)
    assert st_config.get_option("theme.base") == "dark"
    assert st_config.get_option("theme.backgroundColor") == common.TOKENS_DARK["bg"]   # scuro di config.toml


DEMO_DB = ROOT / "data" / "demo.db"


@pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")
@pytest.mark.parametrize("choice, dark", [("chiaro", False), ("scuro", True), ("sistema", True)])
def test_data_menu_selector(tmp_path, monkeypatch, choice, dark):
    """Nell'app: il selettore a tre voci mostra la scelta salvata accanto al database e il tema segue (con
    "Segui il sistema" e nessun cookie, come nei test: scuro); lo script del cookie solo con "Segui il sistema"."""
    import socket

    from streamlit import config as st_config
    from streamlit.testing.v1 import AppTest

    from modules import quotes

    db = tmp_path / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp_path / 'facts.db'}\n", encoding="utf-8")
    (tmp_path / "preferenze_interfaccia.json").write_text(json.dumps({"aspetto": choice}), encoding="utf-8")

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    monkeypatch.setattr(sys, "argv", ["app.py", "--config", str(config)])
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(quotes, "download_closes", no_network)
    monkeypatch.setattr(quotes.QuoteFeed, "start", lambda self: self)
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
        assert not at.exception
        sel = at.get("button_group")
        assert any(getattr(w, "key", None) == "ui_appearance" for w in sel)
        assert at.session_state["ui_appearance"] == choice
        assert (st_config.get_option("theme.base") == "dark") is dark
        scripts = " ".join(h.proto.body for h in at.get("html"))
        assert ("prefers-color-scheme" in scripts) is (choice == "sistema")
    finally:
        sys.modules["ui.common"]._apply_theme(True)   # tema del server come all'avvio per gli altri test
