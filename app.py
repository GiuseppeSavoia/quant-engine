"""
QUANT_ENGINE — webapp locale per esplorare strategia, aziende, bilanci e prezzi.

Avvio:
  .venv/bin/streamlit run app.py              dati reali (data/finance_lake.db)
  .venv/bin/streamlit run app.py -- --demo    dati simulati (data/demo.db)

La webapp legge soltanto: database e report vengono prodotti da main.py. Il pulsante "Aggiorna dati"
lancia main.py in un processo separato (modules/updater.py); dopo una nuova esecuzione i dati si ricaricano.

Questo file è solo il punto d'ingresso; l'interfaccia sta in ui/:
  ui/common.py   tema, token CSS e funzioni condivise (data(), grafici, nomi storici dei componenti)
  ui/components.py  formati in italiano (fmt, fmt_table) e componenti HTML (tabella fitta, testata, verdetto...)
  ui/rules.py    colonne delle metriche e widget delle regole (Esplora, Strategie)
  ui/nav.py      elenco delle pagine (indirizzo, titolo, icona), menu e tasti funzione
  ui/shell.py    cornice fissa: ricerca/comandi, tasti F1-F8, briciole, menu "Dati", barra di stato
  ui/pages/      una pagina per file; la scheda azienda in ui/pages/azienda/ (una sezione per gruppo di funzioni)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

ASSETS = Path(__file__).resolve().parent / "assets"
st.set_page_config(page_title="QuantEngine", page_icon=str(ASSETS / "icon.svg"), layout="wide",
                   initial_sidebar_state="collapsed")

common = importlib.import_module("ui.common")
# Le costanti del tema (TERMINAL, SERIES, UP/DOWN, CSS...) si calcolano quando si importa ui.common, mentre prima
# (tutto in app.py) si ricalcolavano a ogni esecuzione. Se l'aspetto è cambiato (interruttore nel menu "Dati",
# o il file delle preferenze) si rimportano i moduli di ui/, così tutte le pagine vedono le costanti nuove.
if common.TERMINAL != common._terminal_wanted():
    for _name in [m for m in sys.modules if m == "ui" or m.startswith("ui.")]:
        del sys.modules[_name]
    common = importlib.import_module("ui.common")
common._apply_theme(common.TERMINAL)

from ui import nav, shell  # noqa: E402

if st.session_state.pop("ui_reload", False):
    st.html("<script>window.location.reload()</script>", unsafe_allow_javascript=True)
st.logo(str(ASSETS / ("logo_dark.svg" if common.TERMINAL else "logo_light.svg")),
        size="large")  # marchio a sinistra del menu in alto (con icon_image Streamlit mostrerebbe solo l'icona)
st.html(common.APP_CSS)  # token e componenti per i due aspetti (ui/common.py)
_navigation = nav.navigation()
_crumbs = shell._command_bar(_navigation)
shell._keyboard()
shell._status_bar()
_navigation.run()
shell.finish(_navigation, _crumbs)
