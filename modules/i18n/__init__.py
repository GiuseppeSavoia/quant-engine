"""Traduzione in inglese dei testi del motore, al momento di servirli (API, `?lang`/intestazione X-Lang).

Il motore scrive in italiano (giudizio, tavolo, avvisi, catalogo delle metriche, strategie...) e così restano
Streamlit, i test, lo storico dei giudizi e tutto ciò che è salvato o entra in un'impronta: chiavi, codici e nomi
delle strategie NON cambiano mai. L'inglese si applica solo all'uscita, frase per frase:

- catalogo `modules/i18n/en/*.json`: {"testo italiano": "English text"}. Una chiave con `{0}`, `{1}`... è un modello:
  la frase deve combaciare TUTTA (mai pezzi o parole sostituiti dentro una frase), le parti variabili si traducono a
  loro volta (valore formattato da `formats.fmt` -> formato inglese, voce del catalogo, altro modello);
- frasi composte con separatori fissi (" · ", "; ", " — ", a capo): se l'intera frase non è nel catalogo si traducono
  i pezzi;
- testo non riconosciuto = lasciato com'è (nomi di aziende, note dell'utente, titoli delle notizie).

`tests/test_i18n.py` controlla che le frasi del motore sui dati simulati siano tutte nel catalogo.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from modules.formats import en_value

LANGS = ("it", "en")
CATALOG_DIR = Path(__file__).resolve().parent
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_SEPARATORS = ("\n", " · ", "; ", " — ", " – ", " | ", ", ")
_STRICT_SEPARATORS = (", ",)      # elenchi: si traducono a pezzi solo se TUTTI i pezzi sono noti
# campi che contengono dati dell'utente o di terzi, o identificativi che il web rimanda all'API (nomi delle strategie:
# il web li mostra con t()), mai tradotti
# codici confrontati dal web (esiti, stati, fasi, categorie...): restano quelli del motore, il web li mostra con t()
SKIP_KEYS = frozenset({"outcome", "status", "state", "stage", "severity", "level", "category", "kind", "change_kind",
                       "better", "op", "page", "section", "order_type", "profile", "tone", "change", "metric",
                       "group_key", "universe", "currency", "fmt", "type", "group", "groups", "universes", "missing", "reserved",
                       "styles", "style", "horizons", "horizon", "units", "unit", "outcomes",
                       "name", "ticker", "tickers", "url", "href", "link", "cik", "accession", "accn",
                       "thesis", "risks", "headline", "author", "feed", "symbol", "code", "key", "id", "email",
                       "kill_criteria", "what_would_change", "comment", "body", "user_note"})


def normalize(lang: str | None) -> str:
    lang = (lang or "").strip().lower()[:2]
    return lang if lang in LANGS else "it"


@lru_cache(maxsize=None)
def _catalog(lang: str) -> tuple[dict[str, str], list[tuple[re.Pattern, str, int]]]:
    exact: dict[str, str] = {}
    templates: list[tuple[re.Pattern, str, int]] = []
    folder = CATALOG_DIR / lang
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        for it, en in data.items():
            if not isinstance(en, str) or not en:
                continue
            if _PLACEHOLDER.search(it):
                parts = _PLACEHOLDER.split(it)
                rx, order = "", []
                for i, piece in enumerate(parts):
                    if i % 2 == 0:
                        rx += re.escape(piece)
                    else:
                        rx += f"(?P<p{piece}>.+?)"
                        order.append(piece)
                literal = sum(len(p) for p in parts[::2])
                templates.append((re.compile(rf"^{rx}$", re.S), en, literal))
            else:
                exact[it] = en
    templates.sort(key=lambda x: -x[2])        # prima i modelli più specifici
    return exact, templates


def reload() -> None:
    """Rilegge il catalogo (dopo averlo modificato, nei test e negli strumenti)."""
    _catalog.cache_clear()
    _translate_cached.cache_clear()


def lookup(text: str, lang: str = "en") -> str | None:
    """Traduzione di un testo intero, o None se il catalogo non lo conosce (né come valore, voce o modello)."""
    if lang == "it" or not text:
        return text
    return _translate_cached(text, lang, 0, True)


def translate(text: str, lang: str = "en") -> str:
    """Il testo tradotto, o il testo così com'è se non è nel catalogo."""
    if lang == "it" or not isinstance(text, str) or not text.strip():
        return text
    out = _translate_cached(text, lang, 0, False)
    return text if out is None else out


@lru_cache(maxsize=200_000)
def _translate_cached(text: str, lang: str, depth: int, strict: bool) -> str | None:
    stripped = text.strip()
    if not stripped:
        return text
    lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    exact, templates = _catalog(lang)
    hit = exact.get(stripped)
    if hit is not None:
        return lead + hit + trail
    v = en_value(stripped)
    if v is not None:
        return lead + v + trail
    if not re.search(r"[A-Za-zÀ-ÿ]", stripped):
        return text            # simboli, numeri non formattati: invariati
    if depth < 4:
        # prima i modelli le cui parti variabili non contengono separatori, poi i pezzi di una frase composta,
        # infine i modelli con qualunque parte variabile ("2 regole su 3 · Oggi" = due frasi, non un modello)
        hit = _template(stripped, templates, lang, depth, clean=True)
        if hit is not None:
            return lead + hit + trail
        partial = None
        for sep in _SEPARATORS:
            if sep in stripped:
                pieces = stripped.split(sep)
                done = [_translate_cached(p, lang, depth + 1, True) if p.strip() else p for p in pieces]
                if all(d is not None for d in done):          # ogni pezzo noto: frase composta tradotta
                    return lead + sep.join(done) + trail
                if partial is None and not strict and sep not in _STRICT_SEPARATORS \
                        and any(d is not None and d != p for d, p in zip(done, pieces)):
                    partial = sep.join(p if d is None else d for d, p in zip(done, pieces))
        hit = _template(stripped, templates, lang, depth, clean=False)
        if hit is not None:
            return lead + hit + trail
        if partial is not None:                                # ultima risorsa: i pezzi noti
            return lead + partial + trail
    return None if strict else text


def _template(text: str, templates, lang: str, depth: int, clean: bool) -> str | None:
    for rx, en, _ in templates:
        m = rx.match(text)
        if not m:
            continue
        groups = m.groupdict()
        if clean and any(sep in val for val in groups.values() for sep in _SEPARATORS):
            continue
        vals = {}
        for name, val in groups.items():
            sub = _translate_cached(val, lang, depth + 1, False)
            vals[name[1:]] = val if sub is None else sub
        return _PLACEHOLDER.sub(lambda g: vals.get(g.group(1), g.group(0)), en)
    return None


def translate_obj(obj: Any, lang: str = "en", _key: str | None = None, skip: frozenset = frozenset()) -> Any:
    """Traduce ricorsivamente i testi di una risposta JSON (dict/list/str); chiavi e campi in SKIP_KEYS (e `skip`)
    intatti."""
    if lang == "it":
        return obj
    if isinstance(obj, str):
        return obj if (_key in SKIP_KEYS or _key in skip) else translate(obj, lang)
    if isinstance(obj, dict):
        # opzioni {value, label}: il valore è ciò che il web rimanda all'API, si traduce solo l'etichetta
        option = "value" in obj and "label" in obj and isinstance(obj.get("value"), str)
        inner = skip | NEWS_SKIP if "link" in obj else skip          # notizia (titolo con link): testo di terzi
        return {k: (v if option and k == "value" else translate_obj(v, lang, k, inner)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [translate_obj(v, lang, _key, skip) for v in obj]
    return obj


NEWS_SKIP = frozenset({"title", "summary"})     # titoli delle notizie: testo di terzi (/news e documenti)


def catalog_keys(lang: str = "en") -> set[str]:
    exact, templates = _catalog(lang)
    return set(exact) | {rx.pattern for rx, _, _ in templates}
