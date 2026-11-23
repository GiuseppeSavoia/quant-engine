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


def _date(v, short: bool) -> str:
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
    return f"{d:%d/%m/%y}" if short else f"{d:%d/%m/%Y}"


def fmt(v, kind: str = "num", *, digits: int | None = None, currency: str | bool | None = None,
        signed: bool = False, arrow: bool = False, na: str = NA) -> str:
    """Il valore come stringa in formato italiano.

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
        return na if _missing(v) else _date(v, kind == "date_short")
    x = _num(v)
    if x is None:
        return na
    d = DIGITS.get(kind, 2) if digits is None else int(digits)
    unit, suffix = "", ""
    if kind == "pct":
        x, suffix = x * 100, "%"
    elif kind == "pp":
        x, suffix = x * 100, " punti"
        signed = True
    elif kind == "mult":
        suffix = "x"
    elif kind in ("money", "shares"):
        for div, name in ((1e12, " T"), (1e9, " mld"), (1e6, " mln")):
            if abs(x) >= div:
                x, unit = x / div, name
                break
        else:
            d = digits if digits is not None else (0 if abs(x) >= 1000 or x == 0 else 2)
    r = round(x, d)
    if r == 0:
        r = 0.0  # niente "-0,0"
    body = it_number(f"{abs(r):,.{d}f}")
    sign = MINUS if r < 0 else ("+" if (signed or arrow) and r > 0 else "")
    sym = _symbol(currency) if kind in ("money", "price", "num", "int") else ""
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
