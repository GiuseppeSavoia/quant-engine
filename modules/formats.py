"""Formati dei numeri in italiano senza Streamlit (`fmt`, `it_number`, `tone_of`...): UNICA definizione, usata dalla
webapp (`ui/components.py` li importa da qui e li riesporta) e dai moduli che girano fuori dalla webapp (sito
pubblico, rapporti).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

import numpy as np
import pandas as pd

NA = "–"            # valore mancante (en dash)
MINUS = "−"         # meno tipografico per i numeri negativi
CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF", "JPY": "¥"}

# decimali predefiniti per tipo di numero (§3.4)
DIGITS = {"num": 2, "int": 0, "price": 2, "pct": 1, "pp": 1, "mult": 1, "score": 2, "money": 1, "shares": 1}
KINDS = tuple(DIGITS) + ("text", "date", "date_short")


# ================================================================== numeri
def _missing(v) -> bool:
    if v is None or v is pd.NA or v is pd.NaT:
        return True
    try:
        return bool(pd.isna(v)) if not isinstance(v, (list, tuple, dict, set, np.ndarray)) else False
    except (TypeError, ValueError):
        return False


def _num(v) -> float | None:
    """Numero finito o None (None, NaN, inf, testo non numerico)."""
    if isinstance(v, bool):
        return float(v)
    if _missing(v) or isinstance(v, (pd.Timestamp, datetime, date)):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def it_number(text: str) -> str:
    """Converte un numero già formattato all'inglese in italiano: "1,234.5" -> "1.234,5"; "-" iniziale -> "−"."""
    out = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return MINUS + out[1:] if out.startswith("-") else out.replace("(-", "(" + MINUS).replace(" -", " " + MINUS)


def _symbol(currency) -> str:
    if currency is True:
        return "$"
    if not currency:
        return ""
    return CURRENCY_SYMBOL.get(str(currency), str(currency))


MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
LANGS = ("it", "en")


def _date(v, short: bool, lang: str = "it") -> str:
    if _missing(v):
        return NA
    if isinstance(v, (int, float, np.number)) and not isinstance(v, bool):
        return NA  # un numero non è una data (inf, NaN già esclusi sopra)
    try:
        d = pd.Timestamp(v)
    except (TypeError, ValueError, OverflowError):
        return NA
    if pd.isna(d):
        return NA
    if lang == "en":   # "13 Nov 2026" / "13 Nov 26": giorno e mese mai ambigui
        return f"{d.day} {MONTHS_EN[d.month - 1]} {d:%y}" if short else f"{d.day} {MONTHS_EN[d.month - 1]} {d:%Y}"
    return f"{d:%d/%m/%y}" if short else f"{d:%d/%m/%Y}"


def fmt(v, kind: str = "num", *, digits: int | None = None, currency: str | bool | None = None,
        signed: bool = False, arrow: bool = False, na: str = NA, lang: str = "it") -> str:
    """Il valore come stringa in formato italiano (o inglese con lang="en": 1,234.56 · 12.3% · +1.2 pp · $66.3B ·
    €12.5M · $1,234 · 13 Nov 2026; la valuta va davanti, CHF con uno spazio).

    kind:
      num     2 decimali (1.234,56)            int    intero (1.234)
      price   2 decimali (60,28; + valuta)     score  0-1 con 2 decimali (0,78)
      pct     frazione -> 12,3%                pp     frazione -> "+1,2 punti" (differenza di percentuali)
      mult    multiplo -> 12,3x                money  importo scalato: 66,3 mld $ / 12,5 mln $ / 1.234 $ / 12,34 $
      shares  come money, senza valuta         date   13/11/2026      date_short 13/11/26      text  str(v)
    signed: "+" davanti ai positivi (variazioni, margini); lo zero non ha mai segno ("0,0%", non "-0,0%").
    arrow:  ▲ / ▼ / ■ davanti (implica il segno). currency: codice ("USD", "EUR"), simbolo o True (= "$").
    """
    if kind == "text":
        return na if _missing(v) else str(v)
    if kind in ("date", "date_short"):
        return na if _missing(v) else _date(v, kind == "date_short", lang)
    x = _num(v)
    if x is None:
        return na
    d = DIGITS.get(kind, 2) if digits is None else int(digits)
    unit, suffix = "", ""
    if kind == "pct":
        x, suffix = x * 100, "%"
    elif kind == "pp":
        x, suffix = x * 100, (" pp" if lang == "en" else " punti")
        signed = True
    elif kind == "mult":
        suffix = "x"
    elif kind in ("money", "shares"):
        for div, name in (((1e12, "T"), (1e9, "B"), (1e6, "M")) if lang == "en" else
                          ((1e12, " T"), (1e9, " mld"), (1e6, " mln"))):
            if abs(x) >= div:
                x, unit = x / div, name
                break
        else:
            d = digits if digits is not None else (0 if abs(x) >= 1000 or x == 0 else 2)
    r = round(x, d)
    if r == 0:
        r = 0.0  # niente "-0,0"
    body = f"{abs(r):,.{d}f}" if lang == "en" else it_number(f"{abs(r):,.{d}f}")
    sign = MINUS if r < 0 else ("+" if (signed or arrow) and r > 0 else "")
    sym = _symbol(currency) if kind in ("money", "price", "num", "int") else ""
    if lang == "en":
        lead = (sym + " " if len(sym) > 1 else sym) if sym else ""
        text = f"{sign}{lead}{body}{unit}{suffix}"
    else:
        text = f"{sign}{body}{unit}{suffix}" + (f" {sym}" if sym else "")
    if arrow:
        text = ("▲ " if r > 0 else "▼ " if r < 0 else "■ ") + text
    return text


def _no_signed_zero(text: str) -> str:
    """"-0,0x" / "+0,00%" -> "0,0x" / "0,00%" (uno zero non ha segno)."""
    return text.lstrip("+-" + MINUS) if not re.search(r"[1-9]", text) else text


def tone_of(v) -> str:
    """"pos", "neg" o "flat" secondo il segno (per il colore di una variazione)."""
    x = _num(v)
    return "flat" if x is None or x == 0 else ("pos" if x > 0 else "neg")


# numeri all'inglese nei testi di altri moduli (valutazione, strategia, qualità dei dati): a schermo in italiano.
# NON per i testi del giudizio: modules/verdict.py scrive già in italiano e convertire di nuovo guasterebbe "1.234,5"
_EN_NUMBER = re.compile(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+")


def it_text(text: str) -> str:
    """"ROIC 12.5% >= 0.60, 1,234.5" -> "ROIC 12,5% ≥ 0,60, 1.234,5" (solo i numeri con decimali o migliaia)."""
    if not text:
        return text
    out = _EN_NUMBER.sub(lambda m: it_number(m.group(0)), str(text))
    return out.replace(">=", "≥").replace("<=", "≤")


# ================================================================== italiano -> inglese (testi già formattati)
# Un valore uscito da fmt(..., lang="it") ricostruito in inglese: serve alla traduzione dei testi del motore
# (modules/i18n.py), che riceve frasi già scritte in italiano. Solo il valore INTERO (mai pezzi di frase).
_SYMBOLS = "|".join(re.escape(x) for x in sorted(set(CURRENCY_SYMBOL.values()), key=len, reverse=True))
_IT_VALUE = re.compile(
    r"^(?P<arrow>[▲▼■] )?(?P<sign>[+−-])?(?P<int>\d{1,3}(?:\.\d{3})*|\d+)(?:,(?P<dec>\d+))?"
    r"(?P<unit> T| mld| mln)?(?P<suffix>%|x| punti)?(?: (?P<sym>" + _SYMBOLS + r"|[A-Z]{3}))?$")
_IT_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{2}|\d{4})$")
MONTHS_IT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
_IT_MONTH = re.compile(r"^(" + "|".join(MONTHS_IT) + r") (\d{2}|\d{4})$")
_UNIT_EN = {" T": "T", " mld": "B", " mln": "M"}


def en_value(text: str) -> str | None:
    """"66,3 mld $" -> "$66.3B", "12,3%" -> "12.3%", "+1,2 punti" -> "+1.2 pp", "13/11/2026" -> "13 Nov 2026";
    None se il testo non è un valore formattato da fmt (allora la traduzione lo tratta come testo)."""
    t = str(text).strip()
    m = _IT_DATE.match(t)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{dd} {MONTHS_EN[mm - 1]} {yy}"
        return None
    m = re.match(r"^(\d{2})/(\d{2})$", t)          # "13/11" (giorno e mese degli avvisi)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
        return f"{int(m.group(1))} {MONTHS_EN[int(m.group(2)) - 1]}"
    m = _IT_MONTH.match(t)                     # "set 26" (mese e anno brevi delle tabelle)
    if m:
        return f"{MONTHS_EN[MONTHS_IT.index(m.group(1))]} {m.group(2)}"
    if t.isdigit():
        return t                               # "2019", "15": interi senza separatori (anni, conteggi) invariati
    m = _IT_VALUE.match(t)
    if not m or ("." in m.group("int") and m.group("dec") is None and not m.group("unit") and not m.group("suffix")
                 and not m.group("sym") and len(m.group("int")) < 5):
        # "1.5" (testo in inglese, non un migliaio italiano) resta com'è; "1.234" e più lunghi sono migliaia
        return None if not m else (None if len(m.group("int").split(".")[-1]) != 3 else _rebuild(m))
    return _rebuild(m)


def _rebuild(m) -> str:
    body = f"{int(m.group('int').replace('.', '')):,}" + (f".{m.group('dec')}" if m.group("dec") else "")
    sign = MINUS if m.group("sign") in ("−", "-") else (m.group("sign") or "")
    unit = _UNIT_EN.get(m.group("unit") or "", "")
    suffix = {" punti": " pp"}.get(m.group("suffix") or "", m.group("suffix") or "")
    sym = m.group("sym") or ""
    lead = (sym + " " if len(sym) > 1 else sym) if sym else ""
    return f"{m.group('arrow') or ''}{sign}{lead}{body}{unit}{suffix}"
