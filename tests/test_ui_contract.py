"""Contratto tra l'aspetto grafico e gli interni di Streamlit (ui/streamlit_contract.py): ogni selettore interno usato
dal CSS esiste nel frontend di Streamlit installato, la versione è quella collaudata e fissata in requirements.txt."""
from __future__ import annotations

import re
from pathlib import Path

import streamlit

from ui import common
from ui import streamlit_contract as contract

ROOT = Path(__file__).resolve().parent.parent


def test_selectors_exist_in_installed_streamlit():
    missing = contract.missing_selectors()
    assert not missing, ("Selettori del CSS che Streamlit " + streamlit.__version__ + " non ha più (rifiniture perse, "
                         "aggiornare il CSS in ui/common.py): " + ", ".join(missing))


def test_checker_sees_what_the_css_uses_and_flags_unknown_ones(monkeypatch):
    used = contract.selectors_used()
    assert {"stVerticalBlock", "stHtml", "stPageLink-NavLink", "stHeader"} <= used["testid"] | used["class"]
    assert "data-tag" in used["attr"] and "secondary" in used["kind"]
    monkeypatch.setattr(contract, "_ui_sources", lambda: '[data-testid="stQuestoNonEsiste"] .stNemmenoQuesto {}')
    assert contract.missing_selectors() == ['data-testid="stQuestoNonEsiste"', ".stNemmenoQuesto"]


def test_version_is_the_tested_one_and_pinned():
    assert contract.version_supported(), f"Streamlit {streamlit.__version__}: collaudata la {contract.TESTED_SERIES}.x"
    assert contract.version_supported("1.66.0") and not contract.version_supported("1.67.0")
    assert not contract.version_supported("1.6")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    line = next(x for x in req.splitlines() if x.startswith("streamlit"))
    major, minor = map(int, contract.TESTED_SERIES.split("."))
    assert f">={major}.{minor}" in line and f"<{major}.{minor + 1}" in line, line


def test_css_survives_html_sanitizer():
    """st.html passa da DOMPurify: un "minore di" nello stile fa scartare tutto il blocco <style>."""
    body = common.APP_CSS.removeprefix("<style>").removesuffix("</style>")
    assert "<" not in body
    assert not re.search(r"</?script", common.APP_CSS)
