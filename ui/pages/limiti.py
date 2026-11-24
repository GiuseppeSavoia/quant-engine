"""Pagina "Metodo e limiti": tutte le cautele in un posto solo (le pagine ne mostrano una sola riga, nella barra in
fondo). Stesso contenuto della pagina /limiti dell'interfaccia nuova; il testo sulla prova storica del giudizio è
quello della scheda (ui/pages/azienda/verdetto.HONEST_LONG)."""
from __future__ import annotations

import html

import streamlit as st

from ui.components import panel
from ui.pages.azienda.verdetto import HONEST_LONG

SECTIONS = [
    ("Strategie e backtest", [
        "Ogni backtest usa a ogni data solo i bilanci già depositati e la composizione dell'indice di allora; gli ordini "
        "si eseguono il giorno dopo, con costi.",
        "Più configurazioni si provano sugli stessi dati, più la migliore è anche la più fortunata: per questo lo Sharpe "
        "è corretto per i tentativi. Le aziende uscite dall'indice e poi sparite mancano in parte (survivorship bias), e "
        "il 2012-2026 è stato favorevole alle grandi aziende di qualità."]),
    ("Il tavolo dei grandi investitori", [
        "Mostra come le regole tradotte dal metodo pubblicato di ogni investitore leggono i numeri di oggi. Non è il loro "
        "giudizio: manca tutto ciò che non si misura (management, vantaggio competitivo, tempismo) e i mercati diversi "
        "dall'S&P 500. Non entra nel giudizio né nelle strategie."]),
    ("I dati", [
        "Prezzi: chiusure giornaliere. Bilanci: depositi XBRL alla SEC (10-K, 10-Q) ed ESEF per le europee; dove "
        "mancano, valori di riserva segnati nella scheda. Stime degli analisti: solo informazione, mai nel giudizio. "
        "Notizie: titoli da feed pubblici, con un ritardo tipico da 1 a 15 minuti. La qualità di ogni aggiornamento è "
        "nella pagina Qualità dei dati."]),
    ("Cosa non fa", [
        "Non è collegato a nessun broker e non invia ordini: quelli della strategia automatica sono simulati. Non conosce "
        "la tua situazione, i tuoi obiettivi né il tuo profilo di rischio. Le decisioni d'investimento restano tue."]),
]


def _paragraphs(items: list[str]) -> str:
    return "".join(f'<p class="qe-limits">{html.escape(t)}</p>' for t in items)


def page_limits():
    st.title("Metodo e limiti")
    st.caption("Come lavora QuantEngine, cosa sa e cosa non sa. Vale per ogni pagina: è un'analisi a criteri fissi, "
               "non consulenza finanziaria né una raccomandazione.")
    left, right = st.columns(2, gap="medium")
    with left, panel("Il giudizio della scheda"):
        st.html(_paragraphs(HONEST_LONG[:3]))
    for i, (title, items) in enumerate(SECTIONS):
        with (right if i % 2 == 0 else left), panel(title):
            st.html(_paragraphs(items))
