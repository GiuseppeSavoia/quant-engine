"""Test dei lettori delle fonti esterne (SEC, Wikipedia, HTTP) su dati nel loro formato reale."""
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules.http_client import HttpError, RateLimitedClient
from modules.sec_edgar import extract_annual_facts, parse_companyfacts
from modules.universe import UniverseManager, parse_sec_tickers, parse_sp500_wikipedia, yahoo_symbol


# ---------------------------------------------------------------- SEC companyfacts
def _f(end, val, accn, filed, start=None, form="10-K", fy=None, fp="FY"):
    d = {"end": end, "val": val, "accn": accn, "fy": fy or int(filed[:4]), "fp": fp, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


A18 = "0000320193-18-000145"   # 10-K FY2018 (chiusura 2018-09-29)
A19 = "0000320193-19-000119"   # 10-K FY2019 (chiusura 2019-09-28)
Q19 = "0000320193-19-000076"   # 10-Q

COMPANYFACTS = {
    "cik": 320193, "entityName": "Apple Inc.",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _f("2018-10-19", 4_745_398_000, A18, "2018-11-05"),
            _f("2019-10-18", 4_443_265_000, A19, "2019-10-31"),
        ]}}},
        "us-gaap": {
            # etichetta vecchia nel 10-K 2018, nuova nel 10-K 2019
            "SalesRevenueNet": {"units": {"USD": [
                _f("2018-09-29", 265_595e6, A18, "2018-11-05", start="2017-10-01"),
            ]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                _f("2018-09-29", 265_595e6, A19, "2019-10-31", start="2017-10-01"),
                _f("2019-09-28", 260_174e6, A19, "2019-10-31", start="2018-09-30"),
                _f("2019-06-29", 53_809e6, Q19, "2019-07-31", start="2019-03-31", form="10-Q", fp="Q3"),
                _f("2019-09-28", 64_040e6, A19, "2019-10-31", start="2019-06-30"),  # trimestre dentro il 10-K
            ]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                _f("2018-09-29", 77_434e6, A18, "2018-11-05", start="2017-10-01"),
                _f("2018-09-29", 77_400e6, A19, "2019-10-31", start="2017-10-01"),   # corretto nel 10-K successivo
                _f("2019-09-28", 69_391e6, A19, "2019-10-31", start="2018-09-30"),
            ]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                _f("2018-09-29", 13_313e6, A18, "2018-11-05", start="2017-10-01"),
                _f("2018-09-29", 13_313e6, A19, "2019-10-31", start="2017-10-01"),
                _f("2019-09-28", 10_495e6, A19, "2019-10-31", start="2018-09-30"),
            ]}},
            "NetIncomeLoss": {"units": {"USD": [
                _f("2019-09-28", 55_256e6, A19, "2019-10-31", start="2018-09-30"),
                _f("2017-09-30", 48_351e6, A19, "2019-10-31", start="2016-09-25"),  # 3° anno del conto economico
            ]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                _f("2019-09-28", 4_648_913_000, A19, "2019-10-31", start="2018-09-30"),
            ]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [
                _f("2018-09-29", 93_735e6, A19, "2019-10-31"),
                _f("2019-09-28", 91_807e6, A19, "2019-10-31"),
                _f("2019-06-29", 84_936e6, Q19, "2019-07-31", form="10-Q", fp="Q3"),
            ]}},
            "DebtCurrent": {"units": {"USD": [
                _f("2019-09-28", 16_240e6, A19, "2019-10-31"),
            ]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
                _f("2019-09-28", 48_844e6, A19, "2019-10-31"),
                _f("2016-09-24", 20_484e6, A19, "2019-10-31"),  # saldo iniziale: non è una chiusura del documento
            ]}},
            "MarketableSecuritiesCurrent": {"units": {"USD": [
                _f("2019-09-28", 51_713e6, A19, "2019-10-31"),
            ]}},
            "StockholdersEquity": {"units": {"USD": [
                _f("2019-09-28", 90_488e6, A19, "2019-10-31"),
            ]}},
            "Assets": {"units": {"USD": [
                _f("2019-09-28", 338_516e6, A19, "2019-10-31"),
            ]}},
            "CostOfGoodsAndServicesSold": {"units": {"USD": [
                _f("2019-09-28", 161_782e6, A19, "2019-10-31", start="2018-09-30"),
            ]}},
            # saldo d'apertura nel prospetto del patrimonio netto (due anni prima): da ignorare
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [
                _f("2017-09-30", -327e6, A19, "2019-10-31"),
            ]}},
        },
    },
}


@pytest.fixture
def sec_df():
    return parse_companyfacts(COMPANYFACTS, "AAPL")


def _val(df, period, metric, filed=None):
    sub = df[(df["period_end"] == pd.Timestamp(period)) & (df["metric"] == metric)]
    if filed:
        sub = sub[sub["filed"] == pd.Timestamp(filed)]
    assert len(sub) == 1, sub
    return sub["value"].iloc[0]


def test_sec_ignores_quarters_and_10q(sec_df):
    rev = sec_df[sec_df["metric"] == "revenue"]
    assert set(rev["value"]) == {265_595e6, 260_174e6}  # niente 10-Q né trimestri dentro il 10-K


def test_sec_tag_change_across_years(sec_df):
    assert _val(sec_df, "2018-09-29", "revenue", "2018-11-05") == 265_595e6  # SalesRevenueNet
    assert _val(sec_df, "2019-09-28", "revenue") == 260_174e6               # nuova etichetta


def test_sec_keeps_versions_with_filing_dates(sec_df):
    assert _val(sec_df, "2018-09-29", "operating_cash_flow", "2018-11-05") == 77_434e6
    assert _val(sec_df, "2018-09-29", "operating_cash_flow", "2019-10-31") == 77_400e6
    assert _val(sec_df, "2019-09-28", "free_cash_flow") == 69_391e6 - 10_495e6
    assert _val(sec_df, "2019-09-28", "capex") == -10_495e6


def test_sec_balance_sheet_composites(sec_df):
    assert _val(sec_df, "2019-09-28", "total_debt") == 91_807e6 + 16_240e6
    assert _val(sec_df, "2019-09-28", "cash") == 48_844e6 + 51_713e6
    assert _val(sec_df, "2019-09-28", "equity") == 90_488e6
    assert _val(sec_df, "2019-09-28", "total_assets") == 338_516e6
    assert _val(sec_df, "2019-09-28", "gross_profit") == 260_174e6 - 161_782e6   # ricavi - costo del venduto
    # il saldo di cassa del 2016 riportato nel 10-K 2019 non è una chiusura di quel documento
    assert not ((sec_df["metric"] == "cash") & (sec_df["period_end"] == pd.Timestamp("2016-09-24"))).any()
    assert not ((sec_df["metric"] == "equity") & (sec_df["period_end"] == pd.Timestamp("2017-09-30"))).any()


def test_sec_shares_diluted_then_cover_page(sec_df):
    assert _val(sec_df, "2019-09-28", "shares") == 4_648_913_000          # diluite medie
    assert _val(sec_df, "2018-09-29", "shares", "2018-11-05") == 4_745_398_000  # dalla copertina del 10-K 2018


def test_sec_empty_payload():
    assert parse_companyfacts({"facts": {}}, "X").empty
    assert extract_annual_facts({}).empty


# ---------------------------------------------------------------- Wikipedia
WIKI_HTML = """
<html><body>
<table class="wikitable" id="changes"><tr><th>Date</th><th>Added</th></tr><tr><td>x</td><td>y</td></tr></table>
<table class="wikitable sortable sticky-header" id="constituents">
<tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td><a rel="nofollow" class="external text" href="https://www.nyse.com/quote/XNYS:MMM">MMM</a></td>
<td><a href="/wiki/3M" title="3M">3M</a></td><td>Industrials</td><td>Industrial Conglomerates</td>
<td><a href="/wiki/Saint_Paul,_Minnesota">Saint Paul, Minnesota</a></td><td>1957-03-04</td><td>0000066740</td>
<td>1902</td></tr>
<tr><td><a href="#">BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td>
<td>Multi-Sector Holdings</td><td>Omaha, Nebraska</td><td>2010-02-16</td><td>0001067983</td>
<td>1839<sup class="reference"><a href="#cite_note-1">[1]</a></sup></td></tr>
<tr><td>NEWCO</td><td>New Co</td><td>Information Technology</td><td>Software</td><td>Austin, Texas</td>
<td></td><td></td><td>2020</td></tr>
</tbody></table></body></html>
"""


def test_parse_wikipedia_constituents():
    df = parse_sp500_wikipedia(WIKI_HTML).set_index("ticker")
    assert list(df.index) == ["MMM", "BRK-B", "NEWCO"]
    assert df.loc["MMM", "cik"] == 66740 and df.loc["MMM", "sector"] == "Industrials"
    assert df.loc["BRK-B", "date_added"] == pd.Timestamp("2010-02-16")
    assert pd.isna(df.loc["NEWCO", "cik"]) and pd.isna(df.loc["NEWCO", "date_added"])


def test_parse_wikipedia_without_table_fails():
    with pytest.raises(ValueError):
        parse_sp500_wikipedia("<html><table id='other'></table></html>")


def test_sec_ticker_map_and_symbols():
    payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
               "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"}}
    assert parse_sec_tickers(payload) == {"AAPL": 320193, "BRK-B": 1067983}
    assert yahoo_symbol("brk.b ") == "BRK-B"


# ---------------------------------------------------------------- universo
class FakeProvider:
    min_expected = 1

    def __init__(self, members):
        self.members = members
        self.calls = 0

    def sp500_constituents(self):
        self.calls += 1
        return parse_sp500_wikipedia(WIKI_HTML) if self.members is None else self.members

    def sec_ticker_map(self):
        return {"NEWCO": 999}


def test_universe_refresh_membership_and_ciks(tmp_path):
    from modules.config import default_config

    cfg = default_config()
    cfg["universe"]["source"] = "sp500"
    db = DBManager(tmp_path / "u.db")
    prov = FakeProvider(None)
    um = UniverseManager(db, cfg, provider=prov)
    um.refresh()
    assert um.tickers() == ["BRK-B", "MMM", "NEWCO"]
    assert db.company_ciks()["NEWCO"] == 999          # CIK mancante preso dalla SEC
    assert um.tickers(as_of="2000-01-01") == ["MMM", "NEWCO"]  # BRK-B entrata nel 2010
    um.refresh()
    assert prov.calls == 1                             # non si riscarica prima di refresh_days

    # un titolo esce dall'indice: resta in anagrafica ma non è più nell'universo
    um2 = UniverseManager(db, cfg, provider=FakeProvider(parse_sp500_wikipedia(WIKI_HTML).iloc[:2]))
    um2.refresh(force=True)
    assert um2.tickers() == ["BRK-B", "MMM"]
    assert "NEWCO" in set(db.load_companies()["ticker"])


# ---------------------------------------------------------------- HTTP
class _Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = 0

    def get(self, url, timeout=None, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_http_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    sess = _Session([_Resp(429, headers={"Retry-After": "1"}), ConnectionError("reset"), _Resp(200, {"ok": 1})])
    client = RateLimitedClient("Test test@example.com", max_per_second=1000, session=sess)
    assert client.get_json("https://example.com") == {"ok": 1}
    assert sess.calls == 3
    assert sess.headers["User-Agent"] == "Test test@example.com"


def test_http_403_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    sess = _Session([_Resp(403)])
    client = RateLimitedClient("x y@z.it", max_per_second=1000, session=sess)
    with pytest.raises(HttpError, match="User-Agent"):
        client.get("https://example.com")
    assert sess.calls == 1


def test_http_gives_up(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    sess = _Session([_Resp(503)] * 3)
    client = RateLimitedClient("x y@z.it", max_per_second=1000, retries=2, session=sess)
    with pytest.raises(HttpError):
        client.get("https://example.com")
    assert sess.calls == 3


def test_sec_source_builds_url_and_parses():
    from modules.sec_edgar import SecSource

    class FakeHttp:
        def __init__(self):
            self.urls = []

        def get_json(self, url):
            self.urls.append(url)
            return COMPANYFACTS

    http = FakeHttp()
    df = SecSource(http, {"AAPL": 320193, "XYZ": None}).fetch_statements("AAPL")
    assert http.urls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]
    assert {"revenue", "free_cash_flow", "total_debt", "shares"} <= set(df["metric"])
    with pytest.raises(ValueError):
        SecSource(http, {"XYZ": None}).fetch_statements("XYZ")


# ---------------------------------------------------------------- errori di chi deposita
def _payload_shares_in_millions():
    """Come McDonald's dal 2024: azioni diluite dichiarate in milioni, copertina corretta."""
    K23, K24 = "0000063908-23-000010", "0000063908-24-000010"
    dur = lambda end, val, accn, filed: _f(end, val, accn, filed, start=f"{int(end[:4])}-01-01")  # noqa: E731
    return {"facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _f("2023-02-01", 731_496_951, K23, "2023-02-24"),
            _f("2024-02-01", 722_051_488, K24, "2024-02-22"),
        ]}}},
        "us-gaap": {
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                dur("2022-12-31", 741_300_000, K23, "2023-02-24"),
                dur("2022-12-31", 741.3, K24, "2024-02-22"),     # anno di confronto, stesso errore
                dur("2023-12-31", 732.3, K24, "2024-02-22"),
            ]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                dur("2023-12-31", 9_600e6, K24, "2024-02-22"),
            ]}},
            # nessuna voce complessiva di capex: si sommano le componenti
            "PaymentsToAcquireOilAndGasPropertyAndEquipment": {"units": {"USD": [
                dur("2023-12-31", 2_000e6, K24, "2024-02-22"),
            ]}},
            "PaymentsToAcquireOtherPropertyPlantAndEquipment": {"units": {"USD": [
                dur("2023-12-31", 500e6, K24, "2024-02-22"),
            ]}},
        },
    }}


def test_sec_shares_reported_in_millions_are_rescaled():
    df = parse_companyfacts(_payload_shares_in_millions(), "MCD")
    assert _val(df, "2023-12-31", "shares") == pytest.approx(732_300_000)
    assert _val(df, "2022-12-31", "shares", "2024-02-22") == pytest.approx(741_300_000)
    assert _val(df, "2022-12-31", "shares", "2023-02-24") == 741_300_000      # già corretto: invariato


def test_sec_wrong_cover_page_does_not_break_correct_shares():
    """Come Packaging Corp nel 10-K 2026: azioni giuste, copertina x1000; i 10-Q dell'anno sono giusti."""
    K, Q1, Q2, Q3 = "0000075677-26-000010", "0000075677-25-000020", "0000075677-25-000030", "0000075677-25-000040"
    payload = {"facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _f("2025-05-02", 89_980_004, Q1, "2025-05-08", form="10-Q", fp="Q1"),
            _f("2025-08-01", 89_978_783, Q2, "2025-08-07", form="10-Q", fp="Q2"),
            _f("2025-10-31", 89_977_067, Q3, "2025-11-06", form="10-Q", fp="Q3"),
            _f("2026-02-20", 89_213_394_000, K, "2026-02-26"),
        ]}}},
        "us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _f("2025-12-31", 89_600_000, K, "2026-02-26", start="2025-01-01"),
            _f("2024-12-31", 89_500_000, K, "2026-02-26", start="2024-01-01"),
        ]}}},
    }}
    df = parse_companyfacts(payload, "PKG")
    assert _val(df, "2025-12-31", "shares") == 89_600_000
    assert _val(df, "2024-12-31", "shares") == 89_500_000


def test_sec_share_scale_ignores_real_changes():
    from modules.sec_edgar import _share_scale

    assert _share_scale(716.4, 714_461_139) == 1e6
    assert _share_scale(275_000, 279_701_832) == 1e3
    assert _share_scale(2.5e9, 2.45e10) == 1.0       # split 10:1: non è un errore di unità
    assert _share_scale(4e8, 1.4e8) == 1.0           # fusione: x3, non un multiplo di 1.000
    assert _share_scale(float("nan"), 1e8) == 1.0


def test_sec_capex_from_components():
    df = parse_companyfacts(_payload_shares_in_millions(), "EOG")
    assert _val(df, "2023-12-31", "capex") == -2_500e6
    assert _val(df, "2023-12-31", "free_cash_flow") == 9_600e6 - 2_500e6


def test_sec_source_merges_extra_ciks():
    from modules.sec_edgar import SecSource

    class FakeHttp:
        def get_json(self, url):
            return COMPANYFACTS if "CIK0000034088" in url else {"facts": {"us-gaap": {}}}

    df = SecSource(FakeHttp(), {"XOM": 2115436}, {"XOM": [34088]}).fetch_statements("XOM")
    assert {"revenue", "shares"} <= set(df["metric"]) and set(df["ticker"]) == {"XOM"}
    assert SecSource(FakeHttp(), {"XOM": 2115436}).fetch_statements("XOM").empty


# ---------------------------------------------------------------- composizione storica
CHANGES_HTML = """
<table class="wikitable sortable" id="changes"><tbody>
<tr><th rowspan="2">Effective Date</th><th colspan="2">Added</th><th colspan="2">Removed</th><th rowspan="2">Reason</th>
<th rowspan="2">Refs</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>October 6, 2026</td><td>TWLO</td><td>Twilio</td><td>WBD</td><td>Warner Bros. Discovery</td>
<td>Acquired.</td><td>[2]</td></tr>
<tr><td>March 23, 2015</td><td>SLG</td><td>SL Green Realty</td><td>NBR</td><td>Nabors Industries</td>
<td>Market capitalization changes.</td></tr>
<tr><td>June 1, 2012</td><td>BRK.B</td><td>Berkshire Hathaway</td><td>OLD</td><td>Old Co</td><td>Spin-off.</td><td>[3]</td></tr>
<tr><td>January 5, 2010</td><td>OLD</td><td>Old Co</td><td></td><td></td><td>Added.</td><td>[4]</td></tr>
<tr><td>May 2, 2020</td><td>OLD</td><td>Old Co</td><td>XYZ</td><td>Xyz</td><td>Back in.</td><td>[5]</td></tr>
</tbody></table>
"""


def test_parse_sp500_changes():
    from modules.universe import parse_sp500_changes

    ch = parse_sp500_changes(CHANGES_HTML)
    assert len(ch) == 5 and ch["date"].is_monotonic_increasing                # intestazione su 2 righe ignorata
    nbr = ch[ch["removed"] == "NBR"].iloc[0]                                   # riga senza riferimento (6 celle)
    assert nbr["date"] == pd.Timestamp("2015-03-23") and nbr["added"] == "SLG"
    assert ch[ch["added"] == "BRK-B"]["removed"].iloc[0] == "OLD"             # simboli in formato Yahoo
    assert pd.isna(ch[ch["date"] == pd.Timestamp("2010-01-05")]["removed"].iloc[0])
    with pytest.raises(ValueError):
        parse_sp500_changes("<html></html>")


def test_build_membership_and_members_at():
    from modules.universe import build_membership, members_at, parse_sp500_changes

    current = pd.DataFrame({"ticker": ["TWLO", "SLG", "OLD", "MMM"], "name": ["Twilio", "SL Green", "Old Co", "3M"],
                            "date_added": pd.to_datetime(["2026-10-06", "2015-03-23", "2020-05-02", None])})
    m = build_membership(current, parse_sp500_changes(CHANGES_HTML))
    old = m[m["ticker"] == "OLD"].sort_values("start")
    # uscito nel 2012 (entrato nel 2010) e rientrato nel 2020: due intervalli
    assert list(old["start"]) == [pd.Timestamp("2010-01-05"), pd.Timestamp("2020-05-02")]
    assert old["end"].iloc[0] == pd.Timestamp("2012-06-01") and pd.isna(old["end"].iloc[1])
    assert pd.isna(m.loc[m["ticker"] == "WBD", "start"]).all()               # entrata non registrata: da sempre
    assert members_at(m, "2011-01-01") == ["MMM", "NBR", "OLD", "WBD", "XYZ"]
    assert members_at(m, "2012-06-01") == ["MMM", "NBR", "WBD", "XYZ"]       # il giorno dell'uscita è già fuori
    assert members_at(m, "2026-10-06") == ["MMM", "OLD", "SLG", "TWLO"]


# ---------------------------------------------------------------- ultimi 12 mesi dai 10-Q
def _payload_with_10q():
    """Esercizio 2023 dal 10-K, poi il 10-Q del 2° trimestre 2024 (e un 10-K/A depositato dopo)."""
    K23, KA, Q2 = "0000000001-24-000001", "0000000001-24-000099", "0000000001-24-000050"
    q = dict(form="10-Q", fp="Q2")
    return {"facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _f("2024-02-10", 50_000_000, K23, "2024-02-15"),
            _f("2024-07-25", 49_000_000, Q2, "2024-08-01", **q),
        ]}}},
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                _f("2023-12-31", 400e6, K23, "2024-02-15", start="2023-01-01"),
                _f("2023-12-31", 410e6, KA, "2024-09-01", start="2023-01-01"),          # correzione successiva
                _f("2024-06-30", 220e6, Q2, "2024-08-01", start="2024-01-01", **q),     # da inizio esercizio
                _f("2024-06-30", 115e6, Q2, "2024-08-01", start="2024-04-01", **q),     # solo il trimestre
                _f("2023-06-30", 190e6, Q2, "2024-08-01", start="2023-01-01", **q),     # stesso periodo 2023
            ]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                _f("2023-12-31", 100e6, K23, "2024-02-15", start="2023-01-01"),
                _f("2024-06-30", 60e6, Q2, "2024-08-01", start="2024-01-01", **q),
                _f("2023-06-30", 45e6, Q2, "2024-08-01", start="2023-01-01", **q),
            ]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                _f("2023-12-31", 20e6, K23, "2024-02-15", start="2023-01-01"),
                _f("2024-06-30", 12e6, Q2, "2024-08-01", start="2024-01-01", **q),
                _f("2023-06-30", 9e6, Q2, "2024-08-01", start="2023-01-01", **q),
            ]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                _f("2023-12-31", 50_500_000, K23, "2024-02-15", start="2023-01-01"),
                _f("2024-06-30", 49_800_000, Q2, "2024-08-01", start="2024-04-01", **q),   # trimestre
                _f("2024-06-30", 50_100_000, Q2, "2024-08-01", start="2024-01-01", **q),   # semestre
            ]}},
            "Assets": {"units": {"USD": [
                _f("2023-12-31", 900e6, K23, "2024-02-15"),
                _f("2023-12-31", 900e6, Q2, "2024-08-01", **q),   # confronto con la chiusura d'esercizio
                _f("2024-06-30", 1_000e6, Q2, "2024-08-01", **q),
            ]}},
        },
    }}


def test_sec_ttm_from_10q():
    from modules.sec_edgar import TTM_SOURCE, parse_sec

    df = parse_sec(_payload_with_10q(), "XYZ")
    ttm = df[df["source"] == TTM_SOURCE]
    assert set(ttm["period_end"]) == {pd.Timestamp("2024-06-30")}
    assert (ttm["filed"] == pd.Timestamp("2024-08-01")).all()
    v = ttm.set_index("metric")["value"]
    # 220 (gen-giu 2024) + 400 (2023, versione nota ad agosto, non la correzione di settembre) - 190 (gen-giu 2023)
    assert v["revenue"] == pytest.approx(430e6)
    assert v["operating_cash_flow"] == pytest.approx(115e6)
    assert v["capex"] == pytest.approx(-23e6)
    assert v["free_cash_flow"] == pytest.approx(92e6)
    assert v["total_assets"] == pytest.approx(1_000e6)   # chiusura del trimestre, non dell'esercizio
    assert v["shares"] == pytest.approx(49_800_000)      # diluite del trimestre
    annual = df[df["source"] == "sec"]
    assert set(annual["period_end"]) == {pd.Timestamp("2023-12-31")}  # i 10-Q non toccano gli annuali


def test_sec_ttm_needs_previous_year():
    from modules.sec_edgar import parse_ttm

    payload = _payload_with_10q()
    ttm = parse_ttm(payload, "XYZ", extract_annual_facts({"facts": {}}))
    # senza l'esercizio precedente niente flussi, ma lo stato patrimoniale del trimestre sì
    assert set(ttm["metric"]) == {"total_assets", "shares"}


def test_db_replace_financials_all_sources(tmp_path):
    db = DBManager(tmp_path / "t.db")
    old = pd.DataFrame({"ticker": "XYZ", "period_end": ["2022-12-31", "2023-06-30"], "metric": "revenue",
                        "value": [1.0, 2.0], "filed": ["2023-02-01", "2023-08-01"], "source": ["sec", "sec_ttm"]})
    db.upsert_financials(old)
    new = old.iloc[[0]].assign(value=3.0)
    db.replace_financials("XYZ", new, ("sec", "sec_ttm"))
    fin = db.load_financials(["XYZ"])
    assert list(fin["value"]) == [3.0]  # sparisce anche il TTM che la fonte non restituisce più


def test_sec_ttm_uses_same_label_in_all_periods():
    """10-K con una sola etichetta dei ricavi, 10-Q con due: si usa quella comune, non la preferita."""
    from modules.sec_edgar import TTM_SOURCE, parse_sec

    payload = _payload_with_10q()
    gaap = payload["facts"]["us-gaap"]
    revenues = gaap["Revenues"]["units"]["USD"]
    k10 = [f for f in revenues if f["form"] != "10-Q"]
    q10 = [f for f in revenues if f["form"] == "10-Q"]
    gaap["Revenues"]["units"]["USD"] = q10  # "Revenues" solo nel 10-Q
    contract = k10 + [{**q10[0], "val": 205e6}, {**q10[2], "val": 180e6}]
    gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = {"units": {"USD": contract}}
    df = parse_sec(payload, "XYZ")
    rev = df[(df["source"] == TTM_SOURCE) & (df["metric"] == "revenue")]["value"]
    assert list(rev) == [pytest.approx(425e6)]  # 205 + 400 - 180, non 220 + 400 - 190


def test_sec_revenue_is_largest_label():
    """Come Eaton: "Revenues" usata per una voce minore, i ricavi veri sotto un'altra etichetta."""
    payload = _payload_with_10q()
    payload["facts"]["us-gaap"]["SalesRevenueNet"] = {"units": {"USD": [
        _f("2023-12-31", 5_000e6, "0000000001-24-000001", "2024-02-15", start="2023-01-01")]}}
    annual = parse_companyfacts(payload, "XYZ")
    rev = annual[annual["metric"] == "revenue"].sort_values("filed")["value"]
    assert rev.iloc[0] == pytest.approx(5_000e6)


def test_sec_new_items_and_dividend_sign():
    """Utile operativo, circolante, dividendi e riacquisti; i pagamenti sono sempre positivi."""
    K = "0000000001-24-000001"
    payload = _payload_with_10q()
    gaap = payload["facts"]["us-gaap"]
    gaap["OperatingIncomeLoss"] = {"units": {"USD": [_f("2023-12-31", 80e6, K, "2024-02-15", start="2023-01-01")]}}
    gaap["PaymentsOfDividends"] = {"units": {"USD": [_f("2023-12-31", -12e6, K, "2024-02-15", start="2023-01-01")]}}
    gaap["PaymentsForRepurchaseOfCommonStock"] = {"units": {"USD": [
        _f("2023-12-31", 7e6, K, "2024-02-15", start="2023-01-01")]}}
    gaap["AssetsCurrent"] = {"units": {"USD": [_f("2023-12-31", 300e6, K, "2024-02-15")]}}
    gaap["LiabilitiesCurrent"] = {"units": {"USD": [_f("2023-12-31", 150e6, K, "2024-02-15")]}}
    v = parse_companyfacts(payload, "XYZ").set_index("metric")["value"]
    assert v["operating_income"] == 80e6 and v["dividends_paid"] == 12e6 and v["buybacks"] == 7e6
    assert v["current_assets"] == 300e6 and v["current_liabilities"] == 150e6


def test_sec_debt_with_finance_leases():
    """Come Home Depot nei 10-Q: debito sotto "LongTermDebtAndCapitalLeaseObligations" (+ quota corrente)."""
    from modules.sec_edgar import _total_debt

    row = pd.Series({"LongTermDebtAndCapitalLeaseObligations": 44.83, "LongTermDebtAndCapitalLeaseObligationsCurrent": 5.18,
                     "CommercialPaper": 3.5})
    assert _total_debt(row) == pytest.approx(53.51)


def test_sec_debt_balance_sheet_line_plus_short_term():
    """Come AT&T nel 10-Q: riga a lungo termine + DebtCurrent; nel 10-K anche il totale delle note,
    che non deve cambiare la misura (trimestri ed esercizi confrontabili)."""
    from modules.sec_edgar import _total_debt

    q = pd.Series({"LongTermDebtAndCapitalLeaseObligations": 150.14, "DebtCurrent": 9.48})
    k = pd.Series({"LongTermDebtAndCapitalLeaseObligations": 127.09, "DebtCurrent": 9.01, "LongTermDebt": 134.72})
    assert _total_debt(q) == pytest.approx(159.62) and _total_debt(k) == pytest.approx(136.10)
    assert _total_debt(pd.Series({"LongTermDebt": 50.0, "CommercialPaper": 2.0})) == 52.0   # solo le note


def test_sec_ttm_net_income_with_synonym_labels():
    """Come Agilent: utile netto come ProfitLoss nel 10-K e NetIncomeLoss nei 10-Q."""
    from modules.sec_edgar import TTM_SOURCE, parse_sec

    K, Q = "0000000001-24-000001", "0000000001-24-000050"
    q = dict(form="10-Q", fp="Q2")
    payload = _payload_with_10q()
    gaap = payload["facts"]["us-gaap"]
    gaap["ProfitLoss"] = {"units": {"USD": [_f("2023-12-31", 50e6, K, "2024-02-15", start="2023-01-01")]}}
    gaap["NetIncomeLoss"] = {"units": {"USD": [
        _f("2024-06-30", 30e6, Q, "2024-08-01", start="2024-01-01", **q),
        _f("2023-06-30", 20e6, Q, "2024-08-01", start="2023-01-01", **q)]}}
    df = parse_sec(payload, "XYZ")
    ni = df[(df["source"] == TTM_SOURCE) & (df["metric"] == "net_income")]["value"]
    assert list(ni) == [pytest.approx(60e6)]          # 30 + 50 - 20


def test_sec_depreciation_partial_first_label():
    """Ammortamenti: se un'etichetta di totale supera la prima di oltre il 15%, la prima era solo una parte (MCD, VLO,
    CRM, UPS nella verifica dei dati): vale la più grande. Entro il 15% resta la prima; mai sommate."""
    import numpy as np

    from modules.sec_edgar import _depreciation

    mcd = pd.Series({"DepreciationDepletionAndAmortization": 457e6, "DepreciationAndAmortization": 2199e6,
                     "Depreciation": 1600e6})
    assert _depreciation(mcd) == 2199e6
    vlo = pd.Series({"DepreciationAndAmortization": 63e6, "DepreciationAmortizationAndAccretionNet": 3158e6,
                     "Depreciation": 2300e6})
    assert _depreciation(vlo) == 3158e6
    close = pd.Series({"DepreciationDepletionAndAmortization": 1000e6, "DepreciationAndAmortization": 1100e6})
    assert _depreciation(close) == 1000e6                   # +10%: definizioni vicine, resta la prima
    ups = pd.Series({"DepreciationDepletionAndAmortization": 3000e6, "DepreciationAndAmortization": 3746e6})
    assert _depreciation(ups) == 3746e6                     # +24,9%: "circa 3 miliardi" era una cifra tonda
    only_dep = pd.Series({"Depreciation": 300e6, "OtherDepreciationAndAmortization": 50e6})
    assert _depreciation(only_dep) == 300e6                  # solo la parte "Depreciation": come prima
    three = pd.Series({"DepreciationDepletionAndAmortization": 100.0, "DepreciationAndAmortization": 300.0,
                       "DepreciationAmortizationAndAccretionNet": 200.0})
    assert _depreciation(three) == 300.0                     # la più grande, non 100 + 300 + 200
    assert np.isnan(_depreciation(pd.Series({"Revenues": 1.0})))
    # nel documento intero (parse_companyfacts)
    K = "0000000001-24-000001"
    payload = _payload_with_10q()
    gaap = payload["facts"]["us-gaap"]
    gaap["DepreciationDepletionAndAmortization"] = {"units": {"USD": [
        _f("2023-12-31", 457e6, K, "2024-02-15", start="2023-01-01")]}}
    gaap["DepreciationAndAmortization"] = {"units": {"USD": [
        _f("2023-12-31", 2199e6, K, "2024-02-15", start="2023-01-01")]}}
    v = parse_companyfacts(payload, "XYZ").set_index("metric")["value"]
    assert v["depreciation"] == 2199e6
