"""
Il metodo dell'utente: il profilo (stile, orizzonte, regole personali), la checklist qualitativa e il voto.

La checklist raccoglie i giudizi che i numeri non danno (vantaggio competitivo, management...). Le domande e i
pesi li sceglie l'utente; ogni azienda riceve un voto da 1 a 5 per domanda. Il voto complessivo è la media
pesata delle domande a cui ha risposto. Esiste solo da quando lo dai: per questo non entra nei backtest.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_CHECKLIST = [
    ("Vantaggio competitivo duraturo",
     "Marchio, costi di cambio per i clienti, effetti di rete, costi più bassi dei concorrenti: quanto è difficile "
     "copiarla? 5 = fossato ampio e in allargamento."),
    ("Management e allocazione del capitale",
     "Reinveste bene, riacquista azioni a prezzi sensati, comunica con onestà, ha interessi allineati agli "
     "azionisti? 5 = eccellente."),
    ("Capisco il business",
     "So spiegare come guadagna, da chi dipende e cosa può andare storto? 5 = lo spiegherei a chiunque."),
    ("Potere di prezzo",
     "Può alzare i prezzi senza perdere clienti? 5 = sì, e lo ha già dimostrato."),
    ("Rischi esterni contenuti",
     "Regolamentazione, tecnologia che cambia, pochi clienti grandi, debito in scadenza. 5 = rischi bassi."),
]

STYLES = ["Qualità", "Valore (sottovalutate)", "Crescita", "Dividendi", "Riacquisti di azioni", "Momentum"]
HORIZONS = ["Meno di 1 anno", "1-3 anni", "3-5 anni", "Più di 5 anni"]
PROFILE_DEFAULTS = {
    "styles": [], "horizon": "3-5 anni", "positions": 20,
    "principles": "", "red_flags": "", "sell_rules": "",
}


def checklist_score(scores: dict[int, float], items: pd.DataFrame) -> float | None:
    """Media pesata (1-5) dei voti dati sulle domande attive; None se nessuna risposta."""
    if items.empty:
        return None
    total = weight = 0.0
    for _, item in items.iterrows():
        s = scores.get(int(item["id"]))
        if s is None or pd.isna(s) or float(item["weight"]) <= 0:
            continue
        total += float(s) * float(item["weight"])
        weight += float(item["weight"])
    return total / weight if weight > 0 else None
