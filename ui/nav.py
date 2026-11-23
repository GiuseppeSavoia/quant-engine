"""Pagine dell'app: indirizzo, titolo, icona e funzione che la disegna, in un posto solo.

Gli oggetti `st.Page` si creano a ogni esecuzione (come quando stava tutto in app.py) con `page(url_path)`:
`st.switch_page` e `st.page_link` riconoscono la pagina dall'indirizzo, quindi un oggetto nuovo vale quanto
quello passato a `st.navigation`. Le funzioni delle pagine si importano solo quando la pagina si apre: così i
moduli delle pagine possono importare questo modulo senza import circolari.
"""
from __future__ import annotations

import importlib

import streamlit as st

# url_path -> (modulo in ui/pages, funzione, titolo, icona). "" = Oggi (il Monitor), la pagina predefinita.
SPECS: dict[str, tuple[str, str, str, str]] = {
    "": ("monitor", "page_monitor", "Oggi", ":material/monitoring:"),
    "notizie": ("notizie", "page_news", "Notizie", ":material/newspaper:"),
    "mie-aziende": ("mie_aziende", "page_home", "Le mie aziende", ":material/star:"),
    "pipeline": ("pipeline", "page_pipeline", "Pipeline", ":material/view_kanban:"),
    "portafoglio": ("portafoglio", "page_portfolio", "Portafoglio", ":material/account_balance_wallet:"),
    "diario": ("diario", "page_journal", "Diario delle decisioni", ":material/menu_book:"),
    "strategie": ("strategie", "page_strategies", "Strategie", ":material/explore:"),
    "strategia-automatica": ("strategia_automatica", "page_strategy", "Strategia automatica", ":material/smart_toy:"),
    "esplora": ("esplora", "page_explore", "Esplora", ":material/search:"),
    "azienda": ("azienda.scheda", "page_company", "Scheda azienda", ":material/description:"),
    "confronta": ("confronta", "page_compare", "Confronta", ":material/compare_arrows:"),
    "metodo": ("metodo", "page_method", "Il mio metodo", ":material/psychology:"),
    "dati": ("inserisci_dati", "page_data_entry", "Inserisci dati", ":material/edit_note:"),
    "fondi": ("fondi", "page_funds", "Grandi investitori", ":material/account_balance:"),
    "backtest": ("backtest", "page_backtest", "Backtest e ricerca", ":material/science:"),
    "qualita": ("qualita", "page_quality", "Qualità dei dati", ":material/fact_check:"),
    "laboratorio": ("laboratorio", "page_lab", "Laboratorio", ":material/tune:"),
}
# menu in alto: due tendine
MENU = {
    "Aziende": ["", "mie-aziende", "pipeline", "portafoglio", "diario", "azienda", "notizie", "esplora", "confronta", "fondi",
                "metodo", "dati"],
    "Strategie e ricerca": ["strategie", "strategia-automatica", "backtest", "laboratorio", "qualita"],
}
# tasti funzione: solo le 8 pagine del percorso principale "apro -> cerco -> capisco se comprare" (le altre stanno
# nel menu in alto). Anche sulla tastiera: F1-F8 oppure Alt+1...8 (su Mac i tasti F chiedono fn), vedi shell._keyboard
FKEYS = [("F1", "", "Oggi"), ("F2", "mie-aziende", "Le mie aziende"), ("F3", "azienda", "Scheda"),
         ("F4", "esplora", "Esplora"), ("F5", "confronta", "Confronta"), ("F6", "portafoglio", "Portafoglio"),
         ("F7", "diario", "Diario"), ("F8", "notizie", "Notizie")]


def section_of(url_path: str) -> str:
    """Tendina del menu che contiene la pagina ("Aziende", "Strategie e ricerca")."""
    return next((name for name, paths in MENU.items() if url_path in paths), "")


def _runner(module: str, func: str):
    """Funzione della pagina importata al momento (il nome resta quello originale: Streamlit lo usa per la
    pagina predefinita)."""
    def run():
        getattr(importlib.import_module(f"ui.pages.{module}"), func)()

    run.__name__ = run.__qualname__ = func
    return run


def page(url_path: str) -> st.Page:
    module, func, title, icon = SPECS[url_path]
    if url_path == "":
        return st.Page(_runner(module, func), title=title, icon=icon, default=True)
    return st.Page(_runner(module, func), title=title, icon=icon, url_path=url_path)


def navigation():
    """Menu in alto (niente barra laterale); restituisce la pagina da eseguire con `.run()` (`.url_path` e `.title`
    dicono alla cornice dove si è)."""
    return st.navigation(position="top", pages={section: [page(p) for p in paths] for section, paths in MENU.items()})
