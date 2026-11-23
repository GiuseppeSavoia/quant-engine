"""Contratto tra il nostro aspetto grafico e gli interni di Streamlit.

L'aspetto sta su tre livelli, dal più al meno stabile:
1. opzioni ufficiali del tema (`.streamlit/config.toml`, `ui/common.LIGHT_THEME`): colori, caratteri, angoli. API
   documentata: regge gli aggiornamenti.
2. HTML nostro (classi `qe-*` di `ui/components.py`: schede, tabelle, testata, verdetto, distintivi) e classi
   `st-key-<chiave>` dei contenitori con `key` (documentate da Streamlit). Stabili.
3. Rifiniture dei widget di Streamlit nel CSS (`data-testid="st..."`, classi `.stButton`...): NON sono un'API
   pubblica e possono cambiare a ogni versione. Se una sparisce l'app funziona lo stesso (vale il livello 1), ma
   perde la rifinitura.

Per il livello 3: versione di Streamlit fissata in requirements.txt (`TESTED_SERIES`) e `missing_selectors()`, che
cerca ogni selettore usato dentro il pacchetto Streamlit installato (tests/test_ui_contract.py). Aggiornare Streamlit:
alzare il tetto in requirements.txt e `TESTED_SERIES`, installare, lanciare il test, guardare le pagine.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import streamlit

TESTED_SERIES = "1.66"          # serie collaudata (stessa di requirements.txt)
UI_DIR = Path(__file__).resolve().parent

# selettori di altre librerie o della pagina, non di Streamlit
NOT_STREAMLIT = {"modebar-container"}  # barra dei comandi di Plotly

_TESTID = re.compile(r'data-testid="?([A-Za-z0-9_-]+)')
_CLASS = re.compile(r'(?<![\w-])\.(st[A-Z][A-Za-z]+|block-container|element-container|modebar-container)\b')
_ATTR = re.compile(r'\[(data-[a-z-]+)(?:[=\]])')
_KIND = re.compile(r'\[kind="(\w+)"\]')


def version_supported(version: str | None = None) -> bool:
    """La versione installata è della serie collaudata?"""
    v = version or streamlit.__version__
    return v == TESTED_SERIES or v.startswith(TESTED_SERIES + ".")


def _ui_sources() -> str:
    """CSS e JavaScript dell'interfaccia: il CSS completo dell'app più i sorgenti di ui/ (CSS delle singole pagine,
    selettori usati negli script)."""
    from ui import common

    texts = [common.APP_CSS]
    texts += [p.read_text(encoding="utf-8") for p in sorted(UI_DIR.rglob("*.py")) if p.name != Path(__file__).name]
    return "\n".join(texts)


def selectors_used(text: str | None = None) -> dict[str, set[str]]:
    """Selettori interni di Streamlit usati: data-testid, classi `.st...`, attributi `data-*` e tipi dei pulsanti."""
    text = _ui_sources() if text is None else text
    return {"testid": set(_TESTID.findall(text)),
            "class": set(_CLASS.findall(text)) - NOT_STREAMLIT,
            "attr": set(_ATTR.findall(text)) - {"data-testid"},
            "kind": set(_KIND.findall(text))}


@lru_cache(maxsize=1)
def _bundle() -> str:
    static = Path(streamlit.__file__).parent / "static"
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in static.rglob("*.js"))


def missing_selectors() -> list[str]:
    """Selettori usati che il frontend di Streamlit installato non contiene più (vuoto = tutto a posto)."""
    js = _bundle()
    used = selectors_used()
    missing = [f'data-testid="{t}"' for t in sorted(used["testid"]) if t not in js]
    missing += [f".{c}" for c in sorted(used["class"]) if c not in js]
    missing += [f"[{a}]" for a in sorted(used["attr"]) if a not in js]
    # valori di kind: stringhe nel codice minificato, tra virgolette o apici inversi
    missing += [f'[kind="{k}"]' for k in sorted(used["kind"]) if not re.search(f"[`'\"]{k}[`'\"]", js)]
    return missing
