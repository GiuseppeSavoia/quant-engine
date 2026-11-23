"""Test degli orari delle borse: apertura, pausa asiatica, fine settimana, ora legale."""
import pandas as pd

from modules import exchanges as ex


def test_open_closed_and_lunch_break():
    # mercoledì 5 novembre 2026, 14:00 UTC = 9:00 a New York (chiusa), 15:00 a Milano (aperta)
    now = pd.Timestamp("2026-11-05 14:00", tz="UTC")
    ny, mil = ex.status("NYSE", now), ex.status("MIL", now)
    assert not ny["open"] and ny["next"] == pd.Timestamp("2026-11-05 09:30", tz="America/New_York")
    assert ny["until"] == pd.Timedelta(minutes=30) and mil["open"]
    assert mil["next"] == pd.Timestamp("2026-11-05 17:30", tz="Europe/Rome")
    tokyo = ex.status("TSE", pd.Timestamp("2026-11-05 03:00", tz="UTC"))          # 12:00 a Tokyo: pausa
    assert tokyo["state"] == "PAUSA" and tokyo["next"].hour == 12 and tokyo["next"].minute == 30


def test_weekend_and_daylight_saving():
    sat = pd.Timestamp("2026-11-07 15:00", tz="UTC")
    ny = ex.status("NYSE", sat)
    assert ny["state"] == "CHIUSA" and ny["next"] == pd.Timestamp("2026-11-09 09:30", tz="America/New_York")
    # in estate New York apre alle 13:30 UTC, in inverno alle 14:30
    assert ex.status("NYSE", pd.Timestamp("2026-07-01 13:45", tz="UTC"))["open"]
    assert not ex.status("NYSE", pd.Timestamp("2026-12-01 13:45", tz="UTC"))["open"]
    assert ex.until_text(pd.Timedelta(minutes=125)) == "2h05" and ex.until_text(pd.Timedelta(days=2)) == "2 g"
    assert [b["code"] for b in ex.board(["MIL", "XXX", "NYSE"], sat)] == ["MIL", "NYSE"]


def test_easter_and_weekday_rules():
    from datetime import date
    assert [ex.easter(y) for y in (2024, 2025, 2026, 2027)] == [
        date(2024, 3, 31), date(2025, 4, 20), date(2026, 4, 5), date(2027, 3, 28)]
    assert ex.nth_weekday(2026, 11, ex.THU, 4) == date(2026, 11, 26)    # Thanksgiving
    assert ex.nth_weekday(2026, 5, ex.MON, -1) == date(2026, 5, 25)     # Memorial Day
    # la regola NYSE non recupera il Capodanno di sabato (2022) ma sposta il Natale di domenica al lunedì
    nyse_2022 = ex.holidays("NYSE", 2022)
    assert date(2022, 1, 1) not in nyse_2022 and date(2021, 12, 31) not in ex.holidays("NYSE", 2021)
    assert nyse_2022[date(2022, 12, 26)] == "Natale"
    # Londra: Santo Stefano di sabato -> lunedì 28
    assert ex.holidays("LSE", 2026)[date(2026, 12, 28)] == "Santo Stefano"


def test_christmas_new_york():
    # venerdì 25 dicembre 2026: chiusa per Natale, riapre lunedì 28
    st = ex.status("NYSE", pd.Timestamp("2026-12-25 16:00", tz="UTC"))
    assert st["state"] == "CHIUSA" and not st["open"] and st["holiday"] == "Natale"
    assert st["next"] == pd.Timestamp("2026-12-28 09:30", tz="America/New_York")
    assert st["holidays_complete"]
    # giorno normale: nessuna festività
    assert ex.status("NYSE", pd.Timestamp("2026-12-28 16:00", tz="UTC"))["holiday"] is None


def test_ferragosto_and_good_friday_milan():
    # venerdì 15 agosto 2025 Milano chiusa, riapre lunedì 18
    st = ex.status("MIL", pd.Timestamp("2025-08-15 10:00", tz="UTC"))
    assert st["state"] == "CHIUSA" and st["holiday"] == "Ferragosto"
    assert st["next"] == pd.Timestamp("2025-08-18 09:00", tz="Europe/Rome")
    # Venerdì Santo 2026 (3 aprile): la prossima apertura salta anche il Lunedì dell'Angelo
    gf = ex.status("MIL", pd.Timestamp("2026-04-03 10:00", tz="UTC"))
    assert gf["holiday"] == "Venerdì Santo" and gf["next"] == pd.Timestamp("2026-04-07 09:00", tz="Europe/Rome")
    assert ex.status("NYSE", pd.Timestamp("2026-04-03 15:00", tz="UTC"))["holiday"] == "Venerdì Santo"
    # il giorno prima del ponte: dopo la chiusura del giovedì la prossima apertura è martedì
    thu = ex.status("MIL", pd.Timestamp("2026-04-02 17:00", tz="UTC"))
    assert thu["next"] == pd.Timestamp("2026-04-07 09:00", tz="Europe/Rome")


def test_thanksgiving_and_observed_holidays():
    st = ex.status("NYSE", pd.Timestamp("2026-11-26 15:00", tz="UTC"))
    assert st["holiday"] == "Thanksgiving" and st["next"] == pd.Timestamp("2026-11-27 09:30", tz="America/New_York")
    # 4 luglio 2027 di domenica: chiusa lunedì 5
    mon = ex.status("NYSE", pd.Timestamp("2027-07-05 15:00", tz="UTC"))
    assert mon["state"] == "CHIUSA" and mon["holiday"] == "Festa dell'Indipendenza"
    # 4 luglio 2026 di sabato: chiusa venerdì 3, riapre lunedì 6
    fri = ex.status("NYSE", pd.Timestamp("2026-07-03 15:00", tz="UTC"))
    assert fri["holiday"] == "Festa dell'Indipendenza"
    assert fri["next"] == pd.Timestamp("2026-07-06 09:30", tz="America/New_York")
    # il sabato stesso è chiuso come ogni fine settimana, senza nome di festività
    assert ex.status("NYSE", pd.Timestamp("2026-07-04 15:00", tz="UTC"))["holiday"] is None


def test_partial_calendars():
    # Tokyo: Festa della Costituzione di domenica 3 maggio 2026 -> recupero mercoledì 6
    tokyo = ex.status("TSE", pd.Timestamp("2026-05-06 01:00", tz="UTC"))
    assert tokyo["state"] == "CHIUSA" and tokyo["holiday"].startswith("Festa della Costituzione")
    assert not tokyo["holidays_complete"]
    assert {c for c, e in ex.EXCHANGES.items() if not e.holidays_complete} == {"TSE", "HKEX", "SSE", "NSE", "B3", "ASX"}
    # San Paolo: Carnevale (lunedì e martedì prima delle Ceneri) calcolato dalla Pasqua
    assert ex.status("B3", pd.Timestamp("2026-02-16 15:00", tz="UTC"))["holiday"] == "Carnevale"
