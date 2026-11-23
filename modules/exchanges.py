"""
Orari delle borse mondiali: aperta o chiusa adesso, ora locale, tra quanto apre o chiude.

Orari regolari di contrattazione continua (senza aste di apertura e chiusura), giorni feriali; le borse asiatiche hanno
la pausa di metà giornata. Quali mostrare: `monitor.exchanges` in config.yaml (codici qui sotto).

Festività (`holidays(code, anno)`): calcolate con regole, senza rete. Date fisse, Pasqua (algoritmo gregoriano) e le
feste che ne derivano (Venerdì Santo, Lunedì dell'Angelo, Carnevale, Ascensione, Pentecoste, Corpus Domini), regole
"n-esimo lunedì" (USA, Canada, Regno Unito, Giappone, Australia) e lo spostamento delle feste che cadono nel fine
settimana dove la borsa lo applica (NYSE: sabato -> venerdì, domenica -> lunedì, ma il 1° gennaio di sabato non si
recupera; Londra, Toronto, Sydney: al lunedì/martedì successivi). Le borse europee (Milano, Xetra, Parigi, Zurigo)
non recuperano nulla.
Calendario completo (salvo chiusure straordinarie decise di volta in volta, es. lutti nazionali o eventi della
corona, e le chiusure anticipate, che qui non si vedono): NYSE, TSX, LSE, XETRA, EPA, MIL, SIX. Calendario PARZIALE
(`holidays_complete: False` nello stato): Tokyo, Hong Kong, Shanghai, Mumbai, San Paolo, Sydney, dove molte chiusure
seguono il calendario lunare o sono decise anno per anno (Capodanno cinese, equinozi giapponesi, Diwali...): ci sono
solo le feste a regola certa, negli altri giorni la borsa può risultare "aperta" anche se è chiusa.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

import pandas as pd


@dataclass(frozen=True)
class Exchange:
    code: str
    name: str            # come si mostra (maiuscolo, breve)
    tz: str
    sessions: tuple      # ((apertura, chiusura), ...) ore locali "HH:MM"
    holidays_complete: bool = True  # False = ci sono solo le festività a regola certa (vedi docstring del modulo)


EXCHANGES = {e.code: e for e in [
    Exchange("NYSE", "NEW YORK", "America/New_York", (("09:30", "16:00"),)),
    Exchange("TSX", "TORONTO", "America/Toronto", (("09:30", "16:00"),)),
    Exchange("B3", "SAN PAOLO", "America/Sao_Paulo", (("10:00", "17:00"),), False),
    Exchange("LSE", "LONDRA", "Europe/London", (("08:00", "16:30"),)),
    Exchange("XETRA", "FRANCOFORTE", "Europe/Berlin", (("09:00", "17:30"),)),
    Exchange("EPA", "PARIGI", "Europe/Paris", (("09:00", "17:30"),)),
    Exchange("MIL", "MILANO", "Europe/Rome", (("09:00", "17:30"),)),
    Exchange("SIX", "ZURIGO", "Europe/Zurich", (("09:00", "17:30"),)),
    Exchange("TSE", "TOKYO", "Asia/Tokyo", (("09:00", "11:30"), ("12:30", "15:30")), False),
    Exchange("HKEX", "HONG KONG", "Asia/Hong_Kong", (("09:30", "12:00"), ("13:00", "16:00")), False),
    Exchange("SSE", "SHANGHAI", "Asia/Shanghai", (("09:30", "11:30"), ("13:00", "15:00")), False),
    Exchange("NSE", "MUMBAI", "Asia/Kolkata", (("09:15", "15:30"),), False),
    Exchange("ASX", "SYDNEY", "Australia/Sydney", (("10:00", "16:00"),), False),
]}
DEFAULT = ["NYSE", "TSX", "LSE", "XETRA", "EPA", "MIL", "SIX", "TSE", "HKEX", "SSE", "ASX"]


# ------------------------------------------------------------------ festività
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def easter(year: int) -> date:
    """Domenica di Pasqua (calendario gregoriano, algoritmo anonimo di Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-esimo `weekday` (0 = lunedì) del mese; n = -1 per l'ultimo."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _next_monday(d: date) -> date:
    """Sabato o domenica -> lunedì successivo; un giorno feriale resta com'è."""
    return d + timedelta(days=(7 - d.weekday()) % 7) if d.weekday() >= SAT else d


def _nyse_observed(d: date) -> date:
    """Regola NYSE: festa di sabato -> venerdì prima, di domenica -> lunedì dopo."""
    return d - timedelta(days=1) if d.weekday() == SAT else d + timedelta(days=1) if d.weekday() == SUN else d


def _christmas_boxing(year: int) -> dict[date, str]:
    """Natale e Santo Stefano con i giorni sostitutivi in stile britannico (Londra, Toronto, Sydney): se cadono nel
    fine settimana la chiusura passa ai primi giorni feriali successivi (Natale di sabato -> lunedì 27 e martedì 28)."""
    out, day = {}, date(year, 12, 25)
    for name in ("Natale", "Santo Stefano"):
        while day.weekday() >= SAT or day in out:
            day += timedelta(days=1)
        out[day] = name
        day = max(day + timedelta(days=1), date(year, 12, 26))
    return out


def _sunday_to_next(days: dict[date, str]) -> dict[date, str]:
    """Festa di domenica -> primo giorno successivo non festivo (Giappone, Hong Kong)."""
    out = dict(days)
    for d, name in sorted(days.items()):
        if d.weekday() == SUN:
            sub = d + timedelta(days=1)
            while sub in out:
                sub += timedelta(days=1)
            out[sub] = f"{name} (recupero)"
    return out


def _fixed(year: int, items: list[tuple[int, int, str]]) -> dict[date, str]:
    return {date(year, m, d): name for m, d, name in items}


def _nyse(y: int) -> dict[date, str]:
    e = easter(y)
    out = {
        nth_weekday(y, 1, MON, 3): "Martin Luther King Day",
        nth_weekday(y, 2, MON, 3): "Presidents' Day",
        e - timedelta(days=2): "Venerdì Santo",
        nth_weekday(y, 5, MON, -1): "Memorial Day",
        _nyse_observed(date(y, 7, 4)): "Festa dell'Indipendenza",
        nth_weekday(y, 9, MON, 1): "Labor Day",
        nth_weekday(y, 11, THU, 4): "Thanksgiving",
        _nyse_observed(date(y, 12, 25)): "Natale",
    }
    if date(y, 1, 1).weekday() != SAT:  # il 1° gennaio di sabato non si recupera il venerdì (anno precedente)
        out[_nyse_observed(date(y, 1, 1))] = "Capodanno"
    if y >= 2022:
        out[_nyse_observed(date(y, 6, 19))] = "Juneteenth"
    return out


def _tsx(y: int) -> dict[date, str]:
    e = easter(y)
    out = {
        _next_monday(date(y, 1, 1)): "Capodanno",
        e - timedelta(days=2): "Venerdì Santo",
        # lunedì prima del 25 maggio (l'ultimo lunedì di maggio cade sempre dal 25 in poi)
        nth_weekday(y, 5, MON, -1) - timedelta(days=7): "Victoria Day",
        _next_monday(date(y, 7, 1)): "Canada Day",
        nth_weekday(y, 8, MON, 1): "Civic Holiday",
        nth_weekday(y, 9, MON, 1): "Labour Day",
        nth_weekday(y, 10, MON, 2): "Thanksgiving",
        **_christmas_boxing(y),
    }
    if y >= 2008:
        out[nth_weekday(y, 2, MON, 3)] = "Family Day"
    return out


def _lse(y: int) -> dict[date, str]:
    e = easter(y)
    return {
        _next_monday(date(y, 1, 1)): "Capodanno",
        e - timedelta(days=2): "Venerdì Santo",
        e + timedelta(days=1): "Lunedì dell'Angelo",
        nth_weekday(y, 5, MON, 1): "Festa di inizio maggio",
        nth_weekday(y, 5, MON, -1): "Festa di primavera",
        nth_weekday(y, 8, MON, -1): "Festa di fine estate",
        **_christmas_boxing(y),
    }


def _easter_pair(y: int) -> dict[date, str]:
    e = easter(y)
    return {e - timedelta(days=2): "Venerdì Santo", e + timedelta(days=1): "Lunedì dell'Angelo"}


def _xetra(y: int) -> dict[date, str]:
    return {**_fixed(y, [(1, 1, "Capodanno"), (5, 1, "Festa del lavoro"), (12, 24, "Vigilia di Natale"),
                         (12, 25, "Natale"), (12, 26, "Santo Stefano"), (12, 31, "San Silvestro")]), **_easter_pair(y)}


def _epa(y: int) -> dict[date, str]:
    return {**_fixed(y, [(1, 1, "Capodanno"), (5, 1, "Festa del lavoro"), (12, 25, "Natale"),
                         (12, 26, "Santo Stefano")]), **_easter_pair(y)}


def _mil(y: int) -> dict[date, str]:
    return {**_fixed(y, [(1, 1, "Capodanno"), (5, 1, "Festa del lavoro"), (8, 15, "Ferragosto"),
                         (12, 24, "Vigilia di Natale"), (12, 25, "Natale"), (12, 26, "Santo Stefano"),
                         (12, 31, "San Silvestro")]), **_easter_pair(y)}


def _six(y: int) -> dict[date, str]:
    e = easter(y)
    return {**_fixed(y, [(1, 1, "Capodanno"), (1, 2, "San Bertoldo"), (5, 1, "Festa del lavoro"),
                         (8, 1, "Festa nazionale svizzera"), (12, 24, "Vigilia di Natale"), (12, 25, "Natale"),
                         (12, 26, "Santo Stefano"), (12, 31, "San Silvestro")]),
            **_easter_pair(y), e + timedelta(days=39): "Ascensione", e + timedelta(days=50): "Lunedì di Pentecoste"}


def _tse(y: int) -> dict[date, str]:
    # mancano gli equinozi (astronomici, fissati ogni anno) e i "giorni del cittadino" tra due feste
    out = _fixed(y, [(1, 1, "Capodanno"), (1, 2, "Vacanze di inizio anno"), (1, 3, "Vacanze di inizio anno"),
                     (2, 11, "Fondazione della nazione"), (4, 29, "Giorno di Showa"), (5, 3, "Festa della Costituzione"),
                     (5, 4, "Giorno del verde"), (5, 5, "Festa dei bambini"), (11, 3, "Festa della cultura"),
                     (11, 23, "Festa del lavoro"), (12, 31, "Fine dell'anno")])
    out[nth_weekday(y, 1, MON, 2)] = "Giorno della maggiore età"
    out[nth_weekday(y, 9, MON, 3)] = "Rispetto per gli anziani"
    if y not in (2020, 2021):  # anni delle Olimpiadi: date spostate per legge
        out[nth_weekday(y, 7, MON, 3)] = "Giorno del mare"
        out[nth_weekday(y, 10, MON, 2)] = "Giornata dello sport"
        if y >= 2016:
            out[date(y, 8, 11)] = "Giorno della montagna"
    if y >= 2020:
        out[date(y, 2, 23)] = "Compleanno dell'imperatore"
    return _sunday_to_next(out)


def _hkex(y: int) -> dict[date, str]:
    # mancano Capodanno lunare, Qingming, Buddha, Barche del drago, metà autunno, Chung Yeung (calendario lunare)
    out = _fixed(y, [(1, 1, "Capodanno"), (5, 1, "Festa del lavoro"), (7, 1, "Festa della regione"),
                     (10, 1, "Festa nazionale"), (12, 25, "Natale"), (12, 26, "Santo Stefano")])
    return {**_sunday_to_next(out), **_easter_pair(y)}


def _sse(y: int) -> dict[date, str]:
    # solo i giorni festivi di legge: i "ponti" di Capodanno lunare, maggio e ottobre si decidono ogni anno
    return _fixed(y, [(1, 1, "Capodanno"), (5, 1, "Festa del lavoro"), (10, 1, "Festa nazionale"),
                      (10, 2, "Festa nazionale"), (10, 3, "Festa nazionale")])


def _nse(y: int) -> dict[date, str]:
    # mancano Holi, Diwali, Eid e le altre feste a calendario lunare (la borsa le pubblica ogni anno)
    return {**_fixed(y, [(1, 26, "Festa della Repubblica"), (4, 14, "Ambedkar Jayanti"),
                         (5, 1, "Festa del Maharashtra"), (8, 15, "Festa dell'Indipendenza"),
                         (10, 2, "Gandhi Jayanti"), (12, 25, "Natale")]),
            easter(y) - timedelta(days=2): "Venerdì Santo"}


def _b3(y: int) -> dict[date, str]:
    e = easter(y)
    out = _fixed(y, [(1, 1, "Capodanno"), (4, 21, "Tiradentes"), (5, 1, "Festa del lavoro"),
                     (9, 7, "Festa dell'Indipendenza"), (10, 12, "Nostra Signora Aparecida"), (11, 2, "Defunti"),
                     (11, 15, "Proclamazione della Repubblica"), (12, 24, "Vigilia di Natale"), (12, 25, "Natale"),
                     (12, 31, "San Silvestro")])
    out.update({e - timedelta(days=48): "Carnevale", e - timedelta(days=47): "Carnevale",
                e - timedelta(days=2): "Venerdì Santo", e + timedelta(days=60): "Corpus Domini"})
    if y >= 2024:
        out[date(y, 11, 20)] = "Coscienza nera"
    return out


def _asx(y: int) -> dict[date, str]:
    return {_next_monday(date(y, 1, 1)): "Capodanno", _next_monday(date(y, 1, 26)): "Australia Day",
            **_easter_pair(y), date(y, 4, 25): "Anzac Day",
            nth_weekday(y, 6, MON, 2): "Compleanno del re", **_christmas_boxing(y)}


_RULES = {"NYSE": _nyse, "TSX": _tsx, "LSE": _lse, "XETRA": _xetra, "EPA": _epa, "MIL": _mil, "SIX": _six,
          "TSE": _tse, "HKEX": _hkex, "SSE": _sse, "NSE": _nse, "B3": _b3, "ASX": _asx}


@lru_cache(maxsize=None)
def _holidays_cached(code: str, year: int) -> tuple:
    return tuple(sorted(_RULES[code](year).items())) if code in _RULES else ()


def holidays(code: str, year: int) -> dict[date, str]:
    """Giorni di chiusura per festività della borsa `code` nell'anno (data locale -> nome in italiano), solo quelli
    che cadono in un giorno feriale (i sabati e le domeniche sono chiusi comunque)."""
    return {d: n for d, n in _holidays_cached(code, year) if d.weekday() < SAT and d.year == year}


def holiday_name(code: str, day) -> str | None:
    """Nome della festività se la borsa è chiusa per festa quel giorno (data locale), altrimenti None."""
    day = pd.Timestamp(day).date()
    return holidays(code, day.year).get(day)


def _at(day, hhmm: str, tz: str) -> pd.Timestamp:
    # combine + tz_localize: un Timedelta sommato al giorno sbaglierebbe di un'ora al cambio dell'ora legale
    return pd.Timestamp(f"{pd.Timestamp(day).date()} {hhmm}").tz_localize(tz)


def _intervals(ex: Exchange, local_day) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    day = pd.Timestamp(local_day).date()
    if day.weekday() >= 5 or holiday_name(ex.code, day):
        return []
    return [(_at(local_day, a, ex.tz), _at(local_day, b, ex.tz)) for a, b in ex.sessions]


def status(code: str, now: pd.Timestamp | None = None) -> dict:
    """Stato di una borsa: {code, name, local (ora locale), open (bool), state ("APERTA", "PAUSA", "CHIUSA"),
    next (prossima apertura o chiusura, festivi saltati), until (Timedelta fino a `next`), hours (orari in testo),
    holiday (nome della festività di oggi, ora locale, o None), holidays_complete (False = calendario parziale)}."""
    ex = EXCHANGES[code]
    now = pd.Timestamp.now(tz="UTC") if now is None else (now.tz_localize("UTC") if now.tzinfo is None else now)
    local = now.tz_convert(ex.tz)
    today = _intervals(ex, local)
    state, nxt = "CHIUSA", None
    for i, (a, b) in enumerate(today):
        if a <= local < b:
            state, nxt = "APERTA", b
            break
        if local < a:
            state = "PAUSA" if i > 0 else "CHIUSA"
            nxt = a
            break
    if nxt is None:  # dopo la chiusura, nel fine settimana o in un festivo: prima apertura dei giorni seguenti
        day = local.normalize()
        for _ in range(21):  # basta anche per la settimana d'oro di Shanghai o Capodanno + fine settimana
            day += pd.Timedelta(days=1)
            iv = _intervals(ex, day)
            if iv:
                nxt = iv[0][0]
                break
    hours = " / ".join(f"{a}-{b}" for a, b in ex.sessions)
    return {"code": code, "name": ex.name, "local": local, "open": state == "APERTA", "state": state, "next": nxt,
            "until": nxt - local, "hours": hours, "holiday": holiday_name(code, local),
            "holidays_complete": ex.holidays_complete}


def board(codes: list[str] | None = None, now: pd.Timestamp | None = None) -> list[dict]:
    """Stato di più borse, nell'ordine dato (codici sconosciuti ignorati)."""
    return [status(c, now) for c in (codes or DEFAULT) if c in EXCHANGES]


def until_text(td: pd.Timedelta) -> str:
    """'2h05' / '45 min' / '3 g'."""
    minutes = int(td.total_seconds() // 60)
    if minutes >= 48 * 60:
        return f"{minutes // 1440} g"
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}"
    return f"{minutes} min"
