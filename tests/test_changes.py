"""Cosa è cambiato (modules/changes.py): confronti point-in-time su dati costruiti a mano."""
import numpy as np
import pandas as pd
import pytest

from modules import changes
from modules.config import default_config
from modules.market_data import MarketData

# trimestri (ultimi 12 mesi) con la data di deposito; il margine operativo scende dal 30% al 26% nell'ultimo
QUARTERS = [("2025-03-31", "2025-05-01", 1000, 0.30), ("2025-06-30", "2025-08-01", 1030, 0.30),
            ("2025-09-30", "2025-11-01", 1060, 0.30), ("2026-03-31", "2026-05-01", 1150, 0.26)]
ANNUAL = ("2025-12-31", "2026-02-15", 1100, 0.30)


def _rows(ticker, end, filed, rev, margin, source, extra=None, skip=()):
    ebit = rev * margin
    values = {"revenue": rev, "operating_income": ebit, "gross_profit": rev * 0.5, "net_income": ebit * 0.8,
              "pretax_income": ebit, "tax_provision": ebit * 0.2, "free_cash_flow": ebit * 0.7,
              "operating_cash_flow": ebit * 0.9, "total_debt": 500.0, "cash": 200.0, "equity": 2000.0,
              "total_assets": 4000.0, "shares": 100.0, **(extra or {})}
    return [{"ticker": ticker, "period_end": end, "metric": k, "value": v, "filed": filed, "source": source}
            for k, v in values.items() if k not in skip]


def _data(quarters=QUARTERS, annual=ANNUAL, extra=None, skip=(), ticker="AAA", price=100.0):
    rows = []
    for end, filed, rev, m in quarters:
        rows += _rows(ticker, end, filed, rev, m, "sec_ttm", extra, skip)
    if annual:
        rows += _rows(ticker, *annual, "sec", extra, skip)
    days = pd.bdate_range("2024-01-01", "2026-06-30")
    px = np.full(len(days), price)
    px[days >= "2026-06-01"] = price * 1.2  # dopo il deposito il prezzo sale: P/E più alto oggi
    prices = pd.DataFrame({"ticker": ticker, "date": days, "open": px, "high": px, "low": px, "close": px,
                           "volume": 1e6})
    return MarketData.from_long(prices, pd.DataFrame(rows))


def _item(comp, key):
    return next((c for c in comp["items"] if c.key == key), None)


def test_latest_filing_against_previous_and_year_ago():
    res = changes.what_changed(_data(), "AAA", cfg=default_config())
    assert res["latest"]["period_end"] == pd.Timestamp("2026-03-31")
    assert res["latest"]["filed"] == pd.Timestamp("2026-05-01") and res["latest"]["form"] == "10-Q"
    assert res["profile"] == "general"
    prev, year = res["comparisons"]["previous"], res["comparisons"]["year_ago"]
    assert prev["period_end"] == pd.Timestamp("2025-12-31")   # noto subito prima del deposito: l'esercizio
    assert year["period_end"] == pd.Timestamp("2025-03-31")
    om = _item(year, "operating_margin")
    assert om.before == pytest.approx(0.30) and om.after == pytest.approx(0.26)
    assert om.delta == pytest.approx(-0.04) and om.relevant and om.favorable is False
    assert om.text == "Margine operativo da 30,0% a 26,0%: -4,0 punti su un anno prima"
    rev = _item(year, "revenue")
    assert rev.delta == pytest.approx(0.15) and rev.relevant and rev.favorable is True
    assert rev.text.startswith("Ricavi da 1.000 $ a 1.150 $: +15,0%")
    sh = _item(year, "shares")
    assert sh.delta == 0 and not sh.relevant and sh.favorable is None
    # riassunto: solo le rilevanti, dalla più importante, una per voce
    keys = [s["key"] for s in res["summary"]]
    assert "operating_margin" in keys and "shares" not in keys and len(keys) == len(set(keys))
    scores = [s["score"] for s in res["summary"]]
    assert scores == sorted(scores, reverse=True)
    # valutazione: stessi bilanci, prezzo del deposito (100) contro oggi (120)
    pe = next(c for c in res["valuation"] if c.key == "pe")
    assert pe.delta == pytest.approx(0.2) and pe.favorable is False
    assert "Utile netto" in changes.to_text(res) or "Margine" in changes.to_text(res)


def test_point_in_time_and_dates():
    data = _data()
    res = changes.what_changed(data, "AAA", as_of="2026-04-15")  # l'ultimo trimestre non era ancora depositato
    assert res["latest"]["period_end"] == pd.Timestamp("2025-12-31") and res["latest"]["form"] == "10-K"
    between = changes.compare_dates(data, "AAA", "2025-05-15", "2026-05-15")
    assert between["before"]["period_end"] == pd.Timestamp("2025-03-31")
    assert between["after"]["period_end"] == pd.Timestamp("2026-03-31")
    assert any(s["key"] == "operating_margin" for s in between["summary"])
    assert changes.compare_dates(data, "AAA", "2024-02-01", "2026-05-15") == {}  # allora nessun bilancio
    assert changes.what_changed(data, "ZZZ") == {}


def test_missing_items_are_omitted_not_zero():
    res = changes.what_changed(_data(skip=("free_cash_flow", "total_debt")), "AAA")
    keys = {c.key for comp in res["comparisons"].values() for c in comp["items"]} | {c.key for c in res["valuation"]}
    assert not keys & {"free_cash_flow", "fcf_yield", "net_debt"}
    assert {"revenue", "operating_margin"} <= keys


def test_annual_only_compares_year_on_year():
    annual = [("2023-12-31", "2024-03-15", 900, 0.25), ("2024-12-31", "2025-03-15", 1000, 0.25)]
    rows = []
    for end, filed, rev, m in annual:
        rows += _rows("ENI.MI", end, filed, rev, m, "esef")
    data = _data(quarters=[], annual=("2025-12-31", "2026-03-15", 1200, 0.20))
    data = MarketData.from_long(
        pd.DataFrame({"ticker": "ENI.MI", "date": data.close.index, "open": 10.0, "high": 10.0, "low": 10.0,
                      "close": 10.0, "volume": 1.0}),
        pd.concat([pd.DataFrame(rows), pd.DataFrame(_rows("ENI.MI", "2025-12-31", "2026-03-15", 1200, 0.20,
                                                          "esef"))]))
    res = changes.what_changed(data, "ENI.MI", currency="EUR")
    assert list(res["comparisons"]) == ["year_ago"]  # il periodo precedente È l'anno prima: un solo confronto
    rev = _item(res["comparisons"]["year_ago"], "revenue")
    assert rev.delta == pytest.approx(0.2) and "€" in rev.text


def test_bank_uses_sector_metrics():
    extra = {"net_interest_income": 300.0, "noninterest_expense": 400.0, "loan_provisions": 30.0}
    res = changes.what_changed(_data(extra=extra), "AAA", sector="Financials")
    assert res["profile"] == "bank"
    keys = {c.key for c in res["comparisons"]["year_ago"]["items"]}
    assert {"nim", "efficiency_ratio", "rotce"} <= keys
    assert not keys & {"gross_margin", "free_cash_flow", "cash_conversion", "net_debt"}
    assert changes.profile_of({}, "Real Estate") == "reit" and changes.profile_of({}, "Financials") == "financial"


def test_thesis_against_realized():
    note = {"fair_value": 150.0, "assumptions": {"growth": 0.30}}
    # valutazione di maggio 2025: si sapeva il trimestre al 31/03/2025 (ricavi 1.000, margine 30%); un anno dopo
    valuation = {"data_as_of": "2025-05-15",
                 "scenarios": {"Pessimista": {"growth": 0.0, "target_margin": 0.2},
                               "Base": {"growth": 0.10, "target_margin": 0.35, "margin_years": 5},
                               "Ottimista": {"growth": 0.2, "target_margin": 0.4}}}
    res = changes.what_changed(_data(), "AAA", note=note, valuation=valuation)
    th = {t["key"]: t for t in res["thesis"]}
    assert th["fair_value"]["on_track"] and "margine +25%" in th["fair_value"]["text"]   # 150 / 120 - 1
    assert th["revenue_growth"]["assumed"] == 0.10 and th["revenue_growth"]["on_track"]  # +15% in un anno, attesa +10%
    assert th["revenue_growth"]["actual"] == pytest.approx(0.15)
    assert th["fcf_growth"]["on_track"] is False and "sotto la tua ipotesi" in th["fcf_growth"]["text"]
    # margine: dopo un anno su cinque ci si aspetta 30% + (35% - 30%) / 5 = 31%; è 26%
    assert th["operating_margin"]["expected"] == pytest.approx(0.31, abs=1e-3)
    assert th["operating_margin"]["on_track"] is False                                  # 26% contro 31% atteso
    assert any(s["comparison"] == "thesis" for s in res["summary"])
    # senza tesi: niente
    assert changes.what_changed(_data(), "AAA")["thesis"] == []
    # media pesata se manca lo scenario "Base"
    lv = changes._scenario_assumptions({"scenarios": {"A": {"growth": 0.1}, "B": {"growth": 0.3}},
                                        "probabilities": {"A": 0.5, "B": 0.5}})
    assert lv["growth"] == pytest.approx(0.2)


def test_thresholds_from_config():
    cfg = default_config()
    cfg["alerts"]["change_thresholds"] = {"operating_margin": 0.10}
    res = changes.what_changed(_data(), "AAA", cfg=cfg)
    assert not _item(res["comparisons"]["year_ago"], "operating_margin").relevant


def test_italian_formats():
    assert changes.fmt_value("money", 94.93e9) == "94,9 mld $"
    assert changes.fmt_value("money", 1.2e9, "EUR") == "1,2 mld €"
    assert changes.fmt_value("pct", 0.3125) == "31,2%"
    assert changes.fmt_value("x", 28.06) == "28,1x"
    assert changes.fmt_value("shares", 15.2e9) == "15,2 mld"
    assert changes.fmt_value("pct", np.nan) == "–"


def test_thesis_margin_target_is_a_path_not_today():
    """Collaudo #19: il margine obiettivo si raggiunge in N anni. Appena salvata la valutazione (nessun bilancio
    nuovo) la tesi è "in corso", non fallita; poi si confronta col percorso atteso, e a orizzonte passato col
    margine obiettivo."""
    base = {"growth": 0.10, "target_margin": 0.35, "margin_years": 5}
    today = {"data_as_of": "2026-06-15", "scenarios": {"Base": base}}  # dopo l'ultimo deposito: niente di nuovo
    res = changes.what_changed(_data(), "AAA", valuation=today)
    th = {t["key"]: t for t in res["thesis"]}
    assert th["operating_margin"]["on_track"] is None and "in corso" in th["operating_margin"]["text"]
    assert th["revenue_growth"]["on_track"] is None
    assert not any(s["comparison"] == "thesis" for s in res["summary"])  # niente "✗" il primo giorno
    # obiettivo più basso del margine di partenza: atteso 30% - 10/5 = 28%, è 26% (entro i 3 punti): rispettata
    low = {"data_as_of": "2025-05-15", "scenarios": {"Base": {**base, "target_margin": 0.20, "growth": 0.05}}}
    th = {t["key"]: t for t in changes.what_changed(_data(), "AAA", valuation=low)["thesis"]}
    assert th["operating_margin"]["on_track"] is True and th["revenue_growth"]["on_track"] is True
    # senza punto di partenza: in corso finché l'orizzonte non è passato, poi contro l'obiettivo
    th = changes.thesis_check({}, {"data_as_of": "2025-01-01", "scenarios": {"Base": base}},
                              {"operating_margin": 0.26}, {}, 100.0, as_of=pd.Timestamp("2026-06-30"))
    assert th[0]["on_track"] is None
    th = changes.thesis_check({}, {"data_as_of": "2020-01-01", "scenarios": {"Base": base}},
                              {"operating_margin": 0.26}, {}, 100.0, as_of=pd.Timestamp("2026-06-30"))
    assert th[0]["on_track"] is False and "orizzonte passato" in th[0]["text"]
