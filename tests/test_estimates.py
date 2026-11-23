"""Stime degli analisti: lettura di Yahoo e del CSV, multipli sui prossimi 12 mesi, aggiornamento (nessuna rete)."""
import numpy as np
import pandas as pd
import pytest

from modules import estimates as es

TODAY = pd.Timestamp("2026-11-23")


def yahoo_raw(eps0=20.0, eps1=24.0):
    idx = ["0q", "+1q", "0y", "+1y"]
    eps = pd.DataFrame({"avg": [4.8, 4.9, eps0, eps1], "low": [4.7] * 4, "high": [5.0] * 4,
                        "yearAgoEps": [4.1, 4.2, 17.3, eps0], "numberOfAnalysts": [24, 24, 32, 33],
                        "growth": [0.17, 0.15, 0.16, 0.21], "currency": ["USD"] * 4}, index=idx)
    rev = pd.DataFrame({"avg": [96e9, 99e9, 396e9, 478e9], "low": [95e9] * 4, "high": [98e9] * 4,
                        "numberOfAnalysts": [37, 36, 47, 54], "yearAgoRevenue": [81e9, 83e9, 332e9, 396e9],
                        "growth": [0.19, 0.20, 0.19, 0.21], "currency": ["USD"] * 4}, index=idx)
    return {"earnings_estimate": eps, "revenue_estimate": rev,
            "analyst_price_targets": {"current": 484.5, "high": 870.0, "low": 440.0, "mean": 636.1, "median": 631.0}}


def test_yahoo_e_multipli_sui_prossimi_12_mesi():
    est = es.parse_yahoo("MSFT", yahoo_raw(), TODAY)
    assert set(est["metric"]) == {"eps", "revenue", "price_target"} and len(est) == 9
    # esercizio chiuso il 30/06/2026: a fine novembre è passato ~40% dell'esercizio in corso
    f = es.fiscal_fraction(pd.Timestamp("2026-06-30"), TODAY)
    assert f == pytest.approx(146 / 365.25, abs=0.01)
    s = es.summary(est, price=484.5, market_cap=3.6e12, enterprise_value=3.58e12,
                   last_annual_end=pd.Timestamp("2026-06-30"), today=TODAY)
    ntm = (1 - f) * 20 + f * 24
    assert s["eps_ntm"] == pytest.approx(ntm) and s["pe_ntm"] == pytest.approx(484.5 / ntm)
    assert s["upside"] == pytest.approx(636.1 / 484.5 - 1) and s["peg"] == pytest.approx(s["pe_ntm"] / 21)
    # bilancio annuale non ancora caricato da oltre un anno: si va avanti all'esercizio giusto
    assert 0 <= es.fiscal_fraction(pd.Timestamp("2025-06-30"), TODAY) <= 1


def test_csv_e_priorita_sul_dato_di_yahoo():
    csv = "ticker;metric;period;value;source\nmsft;EPS;0y;21.5;FactSet\nMSFT;revenue;+1y;480000000000;FactSet\n"
    rows = es.parse_csv(csv, today=TODAY)
    assert list(rows["ticker"]) == ["MSFT", "MSFT"] and rows["avg"].iloc[0] == 21.5
    both = pd.concat([es.parse_yahoo("MSFT", yahoo_raw(), TODAY), rows], ignore_index=True)
    lat = es.latest(both)
    assert lat[(lat["metric"] == "eps") & (lat["period"] == "0y")]["source"].iloc[0] == "FactSet"
    with pytest.raises(ValueError, match="colonne mancanti"):
        es.parse_csv("ticker,metric\nA,eps\n")
    with pytest.raises(ValueError, match="metriche non riconosciute"):
        es.parse_csv("ticker,metric,period,value\nA,ebitda,0y,1\n")
    with pytest.raises(ValueError, match="non numerici"):
        es.parse_csv("ticker,metric,period,value\nA,eps,0y,abc\n")


def test_refresh_salva_storico_e_non_riscarica_le_recenti(demo_db):
    calls = []

    def fetcher(t):
        calls.append(t)
        if t == "BAD":
            raise ValueError("niente")
        return yahoo_raw(eps1=24.0 if len(calls) < 3 else 25.0)
    r = es.refresh(demo_db, ["AAPL", "BAD"], max_age_days=3, fetcher=fetcher, today=TODAY, pause=0)
    assert r["aggiornate"] == 1 and len(r["errori"]) == 1
    es.refresh(demo_db, ["AAPL"], max_age_days=3, fetcher=fetcher, today=TODAY + pd.Timedelta(days=1), pause=0)
    assert calls.count("AAPL") == 1  # stime di ieri: ancora buone
    es.refresh(demo_db, ["AAPL"], max_age_days=3, fetcher=fetcher, today=TODAY + pd.Timedelta(days=5), pause=0)
    rev = es.revisions(demo_db.load_estimates(["AAPL"]))
    assert list(rev.values) == [24.0, 25.0]  # due scarichi = una revisione al rialzo
    assert np.isfinite(rev.iloc[-1])


def test_stime_in_altra_valuta_convertite_o_escluse():
    est = es.parse_yahoo("TEN.MI", yahoo_raw(), TODAY)  # stime in USD, titolo quotato in EUR
    conv, notes = es.to_currency(est, "EUR", fx=lambda s, d, day: 0.9)
    e = conv[(conv["metric"] == "eps") & (conv["period"] == "0y")]
    assert e["avg"].iloc[0] == pytest.approx(18.0) and set(conv[conv["metric"] == "eps"]["currency"]) == {"EUR"}
    assert notes and "convertite in EUR" in notes[0]
    none, notes = es.to_currency(est, "EUR", fx=None)
    assert set(none["metric"]) == {"price_target"} and "esclusi" in notes[0]
    s = es.summary(none, 30.0, 1e10, 1e10, pd.Timestamp("2025-12-31"), TODAY)
    assert not np.isfinite(s["pe_ntm"])  # multiplo vuoto, non sbagliato
    same, notes = es.to_currency(es.parse_yahoo("MSFT", yahoo_raw(), TODAY), "USD", fx=None)
    assert len(same) == 9 and notes == []
