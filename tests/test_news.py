"""Notizie dai feed RSS/Atom (modules/news.py): lettura, date, deduplicazione, ticker, memoria, thread. Nessuna rete:
feed finti e una funzione `fetch` finta."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import news  # noqa: E402
from modules.config import default_config  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def rfc822(d: datetime) -> str:
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(items: list[tuple[str, str, datetime]], extra: str = "") -> bytes:
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{rfc822(d)}</pubDate>{extra}</item>"
                   for t, l, d in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed finto</title>'
            f"<language>en-us</language>{body}</channel></rss>").encode()


ATOM_FEED = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="it">
  <title>Comunicati</title>
  <entry><title type="html">Tassi &amp;amp; inflazione: &lt;b&gt;decisione&lt;/b&gt;</title>
    <link rel="alternate" href="https://example.org/a?utm_source=rss"/>
    <updated>{(NOW - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')}</updated></entry>
  <entry><title>Senza data</title><link href="https://example.org/b"/></entry>
</feed>""".encode()


# ---------------------------------------------------------------- lettura
def test_parse_rss_and_dates():
    ch, items = news.parse_feed(rss([("Apple &amp; Nvidia rise", "https://x.com/1", NOW)]))
    assert ch["title"] == "Feed finto" and ch["language"] == "en-us"
    assert items[0]["title"] == "Apple & Nvidia rise"
    assert items[0]["published"] == NOW and items[0]["published"].tzinfo == timezone.utc


def test_parse_atom_cleans_html_and_entities():
    ch, items = news.parse_feed(ATOM_FEED)
    assert ch["language"] == "it"
    assert items[0]["title"] == "Tassi & inflazione: decisione"
    assert items[0]["link"].startswith("https://example.org/a")
    assert items[1]["published"] is None


def test_parse_google_news_publisher():
    xml = (b'<rss version="2.0"><channel><title>G</title><item><title>Stocks fall - Reuters</title>'
           b'<link>https://news.google.com/rss/articles/abc</link><pubDate>Fri, 13 Nov 2026 01:00:01 GMT</pubDate>'
           b'<source url="https://reuters.com">Reuters</source></item></channel></rss>')
    _, items = news.parse_feed(xml)
    assert items[0]["title"] == "Stocks fall" and items[0]["publisher"] == "Reuters"


def test_parse_rdf():
    xml = b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>RDF</title></channel>
      <item><title>Uno</title><link>https://e.org/1</link><dc:date>2026-11-12T10:00:00+01:00</dc:date></item></rdf:RDF>"""
    _, items = news.parse_feed(xml)
    assert items[0]["published"] == datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,expected", [
    ("Fri, 13 Nov 2026 02:02:47 UT", datetime(2026, 11, 13, 2, 2, 47, tzinfo=timezone.utc)),   # Business Wire
    ("Thu, 12 Nov 2026 21:30:45 -0500", datetime(2026, 11, 13, 2, 30, 45, tzinfo=timezone.utc)),  # Seeking Alpha
    ("2026-11-13 02:19:14", datetime(2026, 11, 13, 2, 19, 14, tzinfo=timezone.utc)),            # Investing.com
    ("Tue, 10 Nov 2026 17:45:00 +0100", datetime(2026, 11, 10, 16, 45, tzinfo=timezone.utc)),   # BCE
    ("non è una data", None), ("", None),
])
def test_parse_date(text, expected):
    assert news.parse_date(text) == expected


def test_canonical_link_drops_tracking():
    a = news.canonical_link("https://www.Example.com/news/1/?utm_source=rss&.tsrc=rss&id=5#top")
    assert a == news.canonical_link("https://example.com/news/1?id=5")


# ---------------------------------------------------------------- ticker
NAMES = {"AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation", "TGT": "Target Corporation", "META": "Meta Platforms, Inc.",
         "GOOGL": "Alphabet Inc. (Class A)", "GOOG": "Alphabet Inc. (Class C)", "ALL": "The Allstate Corporation",
         "ENI.MI": "Eni S.p.A.", "WMB": "The Williams Companies, Inc.", "COF": "Capital One Financial Corp."}


def test_short_name():
    assert news.short_name("Apple Inc.") == "Apple"
    assert news.short_name("The Williams Companies, Inc.") == "Williams"
    assert news.short_name("Alphabet Inc. (Class A)") == "Alphabet"
    assert news.short_name("Eni S.p.A.") == "Eni"


@pytest.mark.parametrize("title,expected", [
    ("Apple's new iPhone lifts suppliers", ["AAPL"]),
    ("Meta shares jump after earnings", ["META"]),                    # prima parola di "Meta Platforms"
    ("Alphabet to invest in data centers", ["GOOG"]),                 # due classi, un solo ticker
    ("Keurig (NASDAQ: KDP) prices offering", []),                     # fuori dall'universo
    ("Analysts see Target price of $200 for NVDA", ["NVDA"]),         # "Target" da solo non basta
    ("Retailer Target beats estimates", []),
    ("Fed's Williams says work to do", []),                           # nome comune
    ("Avatar Capital Partners closes fund", []),
    ("ALL eyes on the Fed", []),                                      # sigla comune
    ("APPLE AND NVDA SOAR", []),                                       # tutto in maiuscolo: niente ticker nudi
    ("Eni, utile in calo nel trimestre", ["ENI.MI"]),
    ("$AAPL breaks out", ["AAPL"]),
    ("pineapple prices rise", []),
])
def test_matcher(title, expected):
    m = news.TickerMatcher(NAMES, {"GOOGL": ["Google"]})
    assert m.match(title) == expected


def test_matcher_aliases_only_for_universe():
    m = news.TickerMatcher(NAMES, {"GOOGL": ["Google"], "XYZ": ["Xyz Corp"]})
    assert m.match("Google cloud deal") == ["GOOGL"]
    assert m.match("Xyz Corp results") == []


# ---------------------------------------------------------------- memoria
def item(title, link, published=None, tickers=(), source="S"):
    return {"published": published or NOW, "first_seen": NOW, "title": title, "link": link, "source": source,
            "feed": source, "group": "mercati", "lang": "en", "tickers": list(tickers), "feed_tickers": list(tickers)}


def test_store_dedup_by_link_and_title(tmp_path):
    s = news.NewsStore(tmp_path / "n.json")
    added = s.add([item("Nvidia CEO resigns in blow to AI boom", "https://a.com/1?utm_source=x", tickers=["NVDA"])])
    assert len(added) == 1
    assert s.add([item("Altro titolo", "https://www.a.com/1")]) == []                          # stesso link
    assert s.add([item("Nvidia CEO resigns, in blow to AI boom", "https://b.com/9", tickers=["AAPL"])]) == []  # quasi
    assert s.items[0]["tickers"] == ["AAPL", "NVDA"]                                            # ticker uniti
    assert len(s.add([item("Nvidia CFO named interim chief executive", "https://c.com/2")])) == 1
    assert len(s.add([item("Short", "https://d.com/1"), item("Shorts", "https://d.com/2")])) == 2  # brevi: solo esatti


def test_store_persists_and_expires(tmp_path):
    path = tmp_path / "news" / "n.json"
    s = news.NewsStore(path, keep_days=2)
    s.add([item("Recente", "https://a.com/1"), item("Vecchia", "https://a.com/2", NOW - timedelta(days=5))])
    s.save()
    again = news.NewsStore(path, keep_days=2)
    assert [i["title"] for i in again.items] == ["Recente"]
    assert again.add([item("Recente", "https://a.com/1")]) == []   # al riavvio non torna "nuova"
    df = again.frame()
    assert list(df.columns) == news.COLUMNS and str(df["published"].dt.tz) == "UTC"


def test_store_corrupt_file(tmp_path):
    path = tmp_path / "n.json"
    path.write_text("{non json", encoding="utf-8")
    assert news.NewsStore(path).items == []


# ---------------------------------------------------------------- thread e fetch
class FakeNet:
    """Risposte per URL: (status, headers, contenuto) o un'eccezione; registra le intestazioni ricevute."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        r = self.responses[url]
        r = r.pop(0) if isinstance(r, list) else r
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_feed(net, clock, specs, tmp_path, names=None):
    store = news.NewsStore(tmp_path / "n.json")
    f = news.NewsFeed(specs, store, news.TickerMatcher(names or NAMES), pause=0, fetch=net, clock=clock)
    return f


def test_feed_refresh_etag_backoff(tmp_path):
    good = rss([("Apple unveils new chip", "https://a.com/1", NOW - timedelta(minutes=3))])
    net = FakeNet({
        "https://ok": [(200, {"ETag": '"v1"', "Last-Modified": "Fri, 13 Nov 2026 01:00:00 GMT"}, good),
                       (304, {}, b""), (304, {}, b"")],
        "https://down": OSError("rete giù"),
        "https://bad": (200, {}, b"<html>non un feed"),
    })
    clock = Clock()
    specs = [news.FeedSpec("Buono", "https://ok", "en", every=120), news.FeedSpec("Giù", "https://down", every=120),
             news.FeedSpec("Rotto", "https://bad", every=120)]
    f = make_feed(net, clock, specs, tmp_path)
    assert f.refresh() == 1
    table, updated, status = f.snapshot()
    assert table.loc[0, "tickers"] == "AAPL" and table.loc[0, "source"] == "Buono" and updated is not None
    st = status.set_index("feed")
    assert st.loc["Buono", "ok"] and not st.loc["Giù", "ok"] and "XML" in st.loc["Rotto", "error"]
    assert (tmp_path / "n.json").exists()                                    # salvate su file
    # nessun feed scaduto: niente richieste
    n_calls = len(net.calls)
    assert f.refresh() == 0 and len(net.calls) == n_calls
    # dopo 120 s il feed buono si rilegge con ETag/If-Modified-Since; quello giù aspetta di più (240 s)
    clock.t += 121
    f.refresh()
    urls = [u for u, _ in net.calls[n_calls:]]
    assert urls == ["https://ok"]
    assert net.calls[-1][1]["If-None-Match"] == '"v1"' and "If-Modified-Since" in net.calls[-1][1]
    assert "uso personale" in net.calls[-1][1]["User-Agent"] and "@" not in net.calls[-1][1]["User-Agent"]
    clock.t += 120
    f.refresh()
    st = f.status().set_index("feed")
    assert st.loc["Giù", "failures"] == 2                                     # riprovato, ancora giù
    assert st.loc["Buono", "ok"]


def test_company_feeds_and_known_tickers(tmp_path):
    cfg = default_config()["news"]
    cfg["company"].update(yahoo_url="https://y/?s={symbol}", google_url="https://g/?q={query}")
    specs = news.company_feeds(cfg, {"AAPL": "Apple Inc.", "ENI.MI": "Eni S.p.A."})
    assert [(s.name, s.tickers) for s in specs] == [("Yahoo Finance", ("AAPL",)), ("Yahoo Finance", ("ENI.MI",)),
                                                    ("Google News", ("ENI.MI",))]
    assert specs[2].url == "https://g/?q=%22Eni%22" and specs[2].lang == "it" and specs[0].group == "aziende"
    net = FakeNet({specs[0].url: (200, {}, rss([("Supplier deal announced", "https://s.com/1", NOW)])),
                   specs[1].url: (200, {}, rss([])), specs[2].url: (200, {}, rss([]))})
    f = make_feed(net, Clock(), [], tmp_path)
    f.add_feeds(specs, "aziende")
    f.refresh()
    table, _, _ = f.snapshot()
    assert table.loc[0, "tickers"] == "AAPL"                                # il ticker del feed, anche senza nome
    assert list(news.latest(table, "AAPL")["link"]) == ["https://s.com/1"]
    assert news.latest(table, ["MSFT"]).empty


def test_set_universe_relinks_stored(tmp_path):
    net = FakeNet({"https://ok": (200, {}, rss([("Tesla opens factory", "https://t.com/1", NOW)]))})
    f = make_feed(net, Clock(), [news.FeedSpec("F", "https://ok")], tmp_path)
    f.refresh()
    assert f.snapshot()[0].loc[0, "tickers"] == ""
    f.set_universe({**NAMES, "TSLA": "Tesla, Inc."})
    assert f.snapshot()[0].loc[0, "tickers"] == "TSLA"


def test_future_dates_clamped_and_old_dropped(tmp_path):
    net = FakeNet({"https://ok": (200, {}, rss([("Dal futuro", "https://f.com/1", NOW + timedelta(hours=3)),
                                                ("Vecchissima", "https://f.com/2", NOW - timedelta(days=30))]))})
    f = make_feed(net, Clock(), [news.FeedSpec("F", "https://ok")], tmp_path)
    f.refresh()
    table = f.snapshot()[0]
    assert list(table["title"]) == ["Dal futuro"]
    assert table.loc[0, "published"] <= pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=1)


def test_general_feeds_from_config():
    cfg = {"every_seconds": 60, "feeds": [{"name": "A", "url": "https://a", "lang": "it", "group": "italia"},
                                          {"name": "B", "url": "https://b", "every": 300},
                                          {"name": "C", "url": "https://c", "enabled": False}, {"name": "D"}]}
    specs = news.general_feeds(cfg)
    assert [(s.name, s.every, s.group) for s in specs] == [("A", 60, "italia"), ("B", 300, "mercati")]


def test_config_has_news_defaults():
    from modules.config import load_config

    cfg = load_config()
    assert cfg["news"]["every_seconds"] >= 60 and cfg["news"]["feeds"]
    assert all("url" in f and "name" in f for f in cfg["news"]["feeds"])


# ---------------------------------------------------------------- account X
@pytest.mark.parametrize("text,expected", [
    ("@elonmusk", "elonmusk"), ("Reuters_Biz", "Reuters_Biz"), ("https://x.com/federalreserve?lang=it", "federalreserve"),
    ("twitter.com/ecb/", "ecb"), ("nome con spazi", None), ("troppo_lungo_12345", None), ("", None),
    ("<script>", None), ("https://x.com/", None),
])
def test_x_username(text, expected):
    assert news.x_username(text) == expected


def test_x_accounts_file(tmp_path):
    path = tmp_path / "preferenze_x.json"
    assert news.load_x_accounts(path, ["@ecb", "bad name", "ECB"]) == ["ecb"]   # default di config, puliti
    assert news.save_x_accounts(path, ["ecb", "federalreserve", "Ecb", "x y"]) == ["ecb", "federalreserve"]
    assert news.load_x_accounts(path, ["altro"]) == ["ecb", "federalreserve"]  # il file vince sul default
    path.write_text("rotto", encoding="utf-8")
    assert news.load_x_accounts(path, ["ecb"]) == ["ecb"]


def test_x_timeline_html():
    h = news.x_timeline_html("ecb", dark=True, height=500)
    assert 'class="twitter-timeline"' in h and "https://twitter.com/ecb" in h and 'data-theme="dark"' in h
    assert "platform.twitter.com/widgets.js" in h and 'data-height="500"' in h
    assert 'data-theme="light"' in news.x_timeline_html("ecb", dark=False)
    with pytest.raises(ValueError):
        news.x_timeline_html('a" onload="x')
