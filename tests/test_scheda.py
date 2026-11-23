"""Scheda azienda: pezzi puri di ui/pages/azienda/verdetto.py (numeri in italiano, prezzo soglia) e menu delle funzioni."""
from __future__ import annotations

from types import SimpleNamespace

from modules import commands
from ui.pages.azienda import scheda, verdetto as V


def test_it_text_converts_numbers_and_signs():
    assert V.it_text("Qualità 0.93 >= 0.60, ROIC 127.0%") == "Qualità 0,93 ≥ 0,60, ROIC 127,0%"
    assert V.it_text("1,234.5 mln; 10-K del 2026; 8 prove su 9") == "1.234,5 mln; 10-K del 2026; 8 prove su 9"
    assert V.it_text("") == ""


def test_threshold_from_changes():
    v = SimpleNamespace(changes=["Sotto circa 188,82 USD (-47% da oggi) il giudizio diventerebbe \"Candidata\"."])
    h = V.threshold_html(v)
    assert "sotto 188,82 USD" in h and "−47%" in h and "diventa Candidata" in h
    assert V.threshold_html(SimpleNamespace(changes=["Correggere i dati."])) == ""


def test_targets_html_and_menu():
    assert "–" in V.targets_html({}, "USD")
    assert "compra sotto" in V.targets_html({"buy_below": 55.0}, "USD") and "55,00" in V.targets_html({"buy_below": 55.0}, "USD")
    codes = [c for c, _ in commands.FUNCTIONS]
    assert set(scheda.MAIN_FUNCTIONS) <= set(codes) and scheda.DEFAULT_FUNCTION == codes[0] == "PRV"
    # i codici storici restano tutti validi
    assert {"DES", "FA", "ERN", "GP", "RV", "DCF", "DVD", "HDS", "CF", "NOTE", "AUTO"} <= set(codes)


def test_honesty_note_states_the_historical_result():
    import html

    h = html.unescape(V.honesty_html())
    assert "non è una raccomandazione" in h and "non hanno battuto l'indice" in h and "0,8%" in h  # prova storica del 23/11/2026
