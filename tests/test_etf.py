"""Test di modules/etf.py: composizioni con sorgente finta (niente rete), normalizzazione dei simboli,
esposizione effettiva (look-through), sovrapposizione tra ETF e concentrazione."""
import numpy as np
import pandas as pd
import pytest

from modules import etf


def _h(rows):
    return pd.DataFrame(rows, columns=["symbol", "name", "weight"])


# formato grezzo come lo restituisce YahooFundsSource.fetch (valori presi da Yahoo, novembre 2026, arrotondati)
RAW = {
    "VWCE.DE": {"symbol": "VWCE.DE", "quote_type": "ETF", "name": "Vanguard FTSE All-World UCITS ETF",
                "currency": "EUR",
                "holdings": [{"symbol": "NVDA", "name": "NVIDIA Corp", "weight": 0.05},
                             {"symbol": "AAPL", "name": "Apple Inc", "weight": 0.045},
                             {"symbol": "2330.TW", "name": "Taiwan Semiconductor Manufacturing Co Ltd", "weight": 0.018},
                             {"symbol": "GOOGL", "name": "Alphabet Inc Class A", "weight": 0.019},
                             {"symbol": "GOOG", "name": "Alphabet Inc Class C", "weight": 0.015}],
                "sectors": {"technology": 0.33, "financial_services": 0.16, "healthcare": 0.08,
                            "communication_services": 0.08, "industrials": 0.10, "consumer_cyclical": 0.08,
                            "energy": 0.04, "basic_materials": 0.035, "consumer_defensive": 0.045,
                            "utilities": 0.0225, "realestate": 0.0165},
                "asset_classes": {"stockPosition": 0.9994, "cashPosition": 0.0, "bondPosition": 0.0,
                                  "otherPosition": 0.0003, "preferredPosition": 0.0003, "convertiblePosition": 0.0}},
    "SXR8.DE": {"symbol": "SXR8.DE", "quote_type": "ETF", "name": "iShares Core S&P 500 UCITS ETF", "currency": "EUR",
                "holdings": [{"symbol": "AAPL", "name": "Apple Inc", "weight": 0.077},
                             {"symbol": "NVDA", "name": "NVIDIA Corp", "weight": 0.076},
                             {"symbol": "MSFT", "name": "Microsoft Corp", "weight": 0.059},
                             {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc Class B", "weight": 0.016}],
                "sectors": {"technology": 0.38, "financial_services": 0.12, "healthcare": 0.10,
                            "communication_services": 0.084, "consumer_cyclical": 0.092, "industrials": 0.076,
                            "consumer_defensive": 0.049, "energy": 0.039, "utilities": 0.021, "realestate": 0.018,
                            "basic_materials": 0.016},
                "asset_classes": {"stockPosition": 0.9988, "cashPosition": 0.0013, "bondPosition": 0.0}},
    "AGGH.MI": {"symbol": "AGGH.MI", "quote_type": "ETF", "name": "iShares Core Global Aggregate Bond", "currency": "EUR",
                "holdings": [], "sectors": {}, "asset_classes": {"stockPosition": 0.0, "bondPosition": 0.9915,
                                                                 "cashPosition": 0.0085}},
    "ENI.MI": {"symbol": "ENI.MI", "quote_type": "EQUITY", "name": "Eni S.p.A.", "currency": "EUR",
               "holdings": [], "sectors": {}, "asset_classes": {}},
}


class FakeSource:
    def __init__(self, raw=RAW):
        self.raw, self.calls = raw, []

    def fetch(self, symbol):
        self.calls.append(symbol)
        return self.raw.get(symbol)


class BrokenSource:
    def fetch(self, symbol):
        raise ConnectionError("rete assente")


# ------------------------------------------------------------------ ingestione e cache
def test_ingestor_parses_caches_and_recognizes_etf():
    src, cache = FakeSource(), {}
    ing = etf.EtfIngestor(src, cache, max_age_days=30, pause=0)
    c = ing.get("VWCE.DE", today="2026-11-12")
    assert c.is_fund and c.fetched == "2026-11-12"
    assert list(c.holdings["symbol"][:2]) == ["NVDA", "AAPL"]                  # ordinate per peso
    assert c.sectors["Information Technology"] == pytest.approx(0.33)          # tradotti in GICS
    assert c.sectors["Consumer Discretionary"] == pytest.approx(0.08)
    assert c.asset_classes["other"] == pytest.approx(0.0006)
    assert c.detailed_weight == pytest.approx(0.147)
    assert ing.is_etf("ENI.MI", today="2026-11-12") is False
    assert ing.is_etf("NONESISTE", today="2026-11-12") is None
    # dalla cache finché non scade, poi di nuovo dalla sorgente
    ing.get("VWCE.DE", today="2026-12-01")
    assert src.calls.count("VWCE.DE") == 1
    ing.get("VWCE.DE", today="2026-12-20")
    assert src.calls.count("VWCE.DE") == 2
    # un'azione si ricontrolla di rado
    ing.get("ENI.MI", today="2027-02-01")
    assert src.calls.count("ENI.MI") == 1


def test_stale_cache_is_used_when_source_fails(tmp_path):
    cache = etf.JsonCache(tmp_path / "etf" / "c.json")
    etf.EtfIngestor(FakeSource(), cache, pause=0).get_many(["VWCE.DE", "ENI.MI"], today="2026-01-01")
    assert (tmp_path / "etf" / "c.json").exists()
    again = etf.JsonCache(tmp_path / "etf" / "c.json")                       # riletta dal file
    c = etf.EtfIngestor(BrokenSource(), again, pause=0).get("VWCE.DE", today="2026-11-12")
    assert c is not None and c.fetched == "2026-01-01" and len(c.holdings) == 5
    assert etf.EtfIngestor(BrokenSource(), {}, pause=0).get("VWCE.DE") is None


def test_percent_weights_become_fractions():
    raw = {"symbol": "X", "quote_type": "ETF", "holdings": [{"symbol": "A", "name": "A", "weight": 60},
                                                            {"symbol": "B", "name": "B", "weight": 30}]}
    assert etf.parse_yahoo(raw, "2026-11-12").holdings["weight"].tolist() == pytest.approx([0.6, 0.3])


# ------------------------------------------------------------------ simboli
def test_canonical_map_is_prudent():
    names = {"BRK.B": "Berkshire Hathaway Inc Class B", "BRK-B": "Berkshire Hathaway",
             "NVDA": "NVIDIA Corp", "NVDA.DE": "NVIDIA Corporation",
             "ALV": "Autoliv Inc", "ALV.DE": "Allianz SE",
             "AAPL": "Apple Inc", "AAPL.MI": None,                              # senza nome: non si unisce
             "VOD.L": "Vodafone Group", "SAP.DE": "SAP SE"}
    m, doubtful = etf.canonical_map(names)
    assert m["BRK.B"] == "BRK-B" and m["BRK-B"] == "BRK-B"
    assert m["NVDA.DE"] == "NVDA"
    assert m["ALV.DE"] == "ALV.DE"                                            # Allianz non è Autoliv
    assert m["VOD.L"] == "VOD.L" and m["SAP.DE"] == "SAP.DE"                  # .L è una borsa, non una classe
    # AAPL.MI senza nome: il nome di AAPL c'è ma quello di AAPL.MI no -> resta a parte, segnalato
    assert m["AAPL.MI"] == "AAPL.MI"
    assert {d["symbol"] for d in doubtful} == {"ALV.DE", "AAPL.MI"}
    m2, _ = etf.canonical_map(names, aliases={"AAPL.MI": "AAPL"})
    assert m2["AAPL.MI"] == "AAPL"


def test_name_key_and_country():
    assert etf.name_key("Alphabet Inc Class A") == etf.name_key("Alphabet Inc Class C") == "alphabet"
    assert etf.names_match("NVIDIA Corp", "NVIDIA Corporation")
    assert not etf.names_match("Allianz SE", "Autoliv Inc")
    assert not etf.names_match(None, "Apple Inc")
    assert etf.country_of("AAPL") == "Stati Uniti" and etf.country_of("2330.TW") == "Taiwan"
    assert etf.country_of("BRK-B") == "Stati Uniti" and etf.country_of("ENI.MI") == "Italia"
    assert etf.country_of("XYZ.QQ") == etf.UNCLASSIFIED


# ------------------------------------------------------------------ look-through
def _comps():
    ing = etf.EtfIngestor(FakeSource(), {}, pause=0)
    return ing.get_many(["VWCE.DE", "SXR8.DE", "AGGH.MI", "ENI.MI"], today="2026-11-12")


def test_look_through_sums_direct_and_etf_exposure():
    holdings, sectors, ac = etf.split_compositions(_comps())
    assert set(holdings) == {"VWCE.DE", "SXR8.DE", "AGGH.MI"}                 # ENI.MI è un'azione
    pos = pd.DataFrame({"ticker": ["VWCE.DE", "SXR8.DE", "NVDA.DE", "ENI.MI", "AGGH.MI", "XYZ"],
                        "value_eur": [10_000.0, 5_000.0, 2_000.0, 1_000.0, 2_000.0, np.nan],
                        "name": ["", "", "NVIDIA Corporation", "Eni S.p.A.", "", "Senza prezzo"]})
    sector_of = {"NVDA": "Information Technology", "ENI.MI": "Energy"}
    res = etf.look_through(pos, holdings, sectors, sector_of, asset_classes=ac,
                           company_country={"ENI.MI": "Italia"})
    s = res["summary"]
    assert s["total_eur"] == pytest.approx(20_000) and s["missing_value"] == ["XYZ"]
    c = res["companies"].set_index("symbol")
    # NVIDIA: 2.000 dirette (NVDA.DE unita a NVDA) + 10.000 x 5% + 5.000 x 7,6%
    nv = c.loc["NVDA"]
    assert nv["direct_eur"] == pytest.approx(2_000) and nv["etf_eur"] == pytest.approx(500 + 380)
    assert nv["total_eur"] == pytest.approx(2_880) and nv["weight"] == pytest.approx(2_880 / 20_000)
    assert nv["sources"].startswith("diretta 2.000 €") and "VWCE.DE 500 €" in nv["sources"]
    assert "NVDA.DE" not in c.index
    assert c.loc["AAPL", "total_eur"] == pytest.approx(450 + 385)
    assert c.loc["BRK-B", "etf_eur"] == pytest.approx(80)                     # BRK.B -> BRK-B
    assert c.loc["2330.TW", "country"] == "Taiwan"
    assert c.loc["ENI.MI", "sector"] == "Energy" and c.loc["ENI.MI", "direct_eur"] == pytest.approx(1_000)
    # GOOGL e GOOG: due simboli, una sola azienda
    bc = res["by_company"].set_index("company")
    assert bc.loc["alphabet", "symbols"] == "GOOG, GOOGL"
    assert bc.loc["alphabet", "total_eur"] == pytest.approx(10_000 * (0.019 + 0.015))
    # quota non dettagliata: VWCE 85,3%, SXR8 77,2%, AGGH tutto
    f = res["funds"].set_index("etf")
    assert f.loc["VWCE.DE", "undetailed_eur"] == pytest.approx(10_000 * (1 - 0.147))
    assert f.loc["AGGH.MI", "detailed_pct"] == 0 and f.loc["AGGH.MI", "undetailed_eur"] == pytest.approx(2_000)
    assert s["undetailed_eur"] == pytest.approx(10_000 * 0.853 + 5_000 * 0.772 + 2_000)
    assert s["detailed_eur"] + s["undetailed_eur"] == pytest.approx(s["total_eur"])
    # la somma per azienda + non dettagliato = totale
    assert res["companies"]["total_eur"].sum() + s["undetailed_eur"] == pytest.approx(s["total_eur"])


def test_look_through_sectors_and_countries_add_up():
    holdings, sectors, ac = etf.split_compositions(_comps())
    pos = {"VWCE.DE": 10_000.0, "AGGH.MI": 2_000.0, "ENI.MI": 1_000.0, "MISTERO": 500.0}
    res = etf.look_through(pos, holdings, sectors, {"ENI.MI": "Energy"}, asset_classes=ac)
    sec = res["sectors"].set_index("sector")
    assert sec["total_eur"].sum() == pytest.approx(13_500)
    assert sec["weight"].sum() == pytest.approx(1.0)
    assert sec.loc["Energy", "direct_eur"] == pytest.approx(1_000)
    assert sec.loc["Energy", "etf_eur"] > 0                                   # anche dal VWCE
    assert sec.loc[etf.BONDS, "etf_eur"] == pytest.approx(2_000 * 0.9915)     # obbligazioni dell'AGGH
    assert sec.loc[etf.CASH, "etf_eur"] == pytest.approx(2_000 * 0.0085)
    assert sec.loc[etf.UNCLASSIFIED, "direct_eur"] == pytest.approx(500)      # azione senza settore
    cty = res["countries"].set_index("country")
    assert cty["total_eur"].sum() == pytest.approx(13_500)
    assert cty.loc[etf.UNDETAILED, "etf_eur"] == pytest.approx(10_000 * 0.853 + 2_000)
    assert cty.loc["Italia", "direct_eur"] == pytest.approx(1_000)


def test_etf_without_composition_is_all_undetailed():
    res = etf.look_through({"IUSN.DE": 1_000.0, "AAPL": 500.0}, {}, {}, {"AAPL": "Information Technology"},
                           etfs={"IUSN.DE"})
    assert res["summary"]["undetailed_eur"] == pytest.approx(1_000)
    assert res["sectors"].set_index("sector").loc[etf.UNCLASSIFIED, "etf_eur"] == pytest.approx(1_000)
    assert list(res["companies"]["symbol"]) == ["AAPL"]


def test_look_through_is_deterministic():
    holdings, sectors, ac = etf.split_compositions(_comps())
    pos = {"SXR8.DE": 3_000.0, "VWCE.DE": 3_000.0, "NVDA": 100.0}
    a = etf.look_through(pos, holdings, sectors, {}, asset_classes=ac)
    b = etf.look_through(dict(reversed(list(pos.items()))), dict(reversed(list(holdings.items()))), sectors, {},
                         asset_classes=ac)
    pd.testing.assert_frame_equal(a["companies"], b["companies"])
    pd.testing.assert_frame_equal(a["sectors"], b["sectors"])


# ------------------------------------------------------------------ sovrapposizione e concentrazione
def test_overlap_is_lower_bound_with_partial_holdings():
    comps = _comps()
    o = etf.overlap(comps["VWCE.DE"], comps["SXR8.DE"])
    # in comune NVDA (min 0,05) e AAPL (min 0,045)
    assert o["overlap"] == pytest.approx(0.095)
    assert list(o["common"]["symbol"]) == ["NVDA", "AAPL"]
    assert o["lower_bound"] and "limite inferiore" in o["note"]
    assert o["coverage_a"] == pytest.approx(0.147) and o["coverage_b"] == pytest.approx(0.228)
    assert o["upper_bound"] == pytest.approx(0.095 + min(1 - 0.095, 1 - 0.153))
    assert o["overlap"] <= o["upper_bound"] <= 1


def test_overlap_complete_and_symbol_variants():
    a = _h([("BRK.B", "Berkshire Hathaway B", 0.5), ("X", "Xco", 0.5)])
    b = {"BRK-B": 0.2, "Y": 0.8}
    o = etf.overlap(a, b)
    assert o["overlap"] == pytest.approx(0.2) and not o["lower_bound"] and o["upper_bound"] == pytest.approx(0.2)
    assert etf.overlap(a, a)["overlap"] == pytest.approx(1.0)
    assert etf.sector_overlap({"IT": 0.6, "Fin": 0.4}, {"IT": 0.3, "Health": 0.7}) == pytest.approx(0.3)


def test_concentration():
    exp = pd.DataFrame({"symbol": ["A", "B", "C", "D"], "total_eur": [50.0, 30.0, 20.0, 0.0]})
    c = etf.concentration(exp, n=2)
    assert list(c["top"]["symbol"]) == ["A", "B"]
    assert c["top_weight"] == pytest.approx(0.5) and c["top_n_weight"] == pytest.approx(0.8)
    assert c["hhi"] == pytest.approx(0.25 + 0.09 + 0.04) and c["effective_n"] == pytest.approx(1 / 0.38)
    assert not c["partial"]
    # con una parte non dettagliata: pesi sul totale vero, Herfindahl limite inferiore
    p = etf.concentration(exp, n=2, total=200.0)
    assert p["top_weight"] == pytest.approx(0.25) and p["partial"] and p["known_share"] == pytest.approx(0.5)
    assert p["hhi"] < c["hhi"]
    s = etf.concentration(pd.Series({"A": 1.0, "B": 3.0}))
    assert s["top"]["symbol"].iloc[0] == "B"
    assert np.isnan(etf.concentration(exp.head(0))["hhi"])
