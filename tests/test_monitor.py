"""Test del Monitor: quotazioni, funzioni della scheda (concorrenti, dividendi, depositi) e notizie (8-K)."""
import numpy as np
import pandas as pd
import pytest

from modules import quotes


def test_quote_table_and_market_hours():
    close = pd.DataFrame({"A": [10.0, 11.0], "B": [5.0, np.nan], "C": [np.nan, np.nan]},
                         index=pd.to_datetime(["2026-11-03", "2026-11-04"]))
    q = quotes.quote_table(close)
    assert list(q.index) == ["A"] and q.at["A", "change_pct"] == pytest.approx(0.1)   # B ha un solo prezzo
    ny = "America/New_York"
    assert quotes.market_open(pd.Timestamp("2026-11-04 10:00", tz=ny))
    assert not quotes.market_open(pd.Timestamp("2026-11-04 16:30", tz=ny))
    assert not quotes.market_open(pd.Timestamp("2026-11-07 11:00", tz=ny))           # sabato


def test_quote_feed_keeps_last_good_snapshot():
    calls = []

    def fetch(symbols):
        calls.append(symbols)
        if len(calls) == 2:
            raise ConnectionError("Yahoo non risponde")
        return pd.DataFrame({s: [1.0, 2.0] for s in symbols}, index=pd.to_datetime(["2026-11-03", "2026-11-04"]))

    feed = quotes.QuoteFeed(["X", "Y"], fetch=fetch)
    assert feed.snapshot()[0].empty and feed.snapshot()[1] is None
    feed.refresh()
    table, when = feed.snapshot()
    assert set(table.index) == {"X", "Y"} and when is not None
    feed.refresh()                                                     # errore: resta l'ultima tabella buona
    assert set(feed.snapshot()[0].index) == {"X", "Y"}


def test_company_functions_data(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    demo_db.add_events(pd.DataFrame({"ticker": ["AAPL", "AAPL"], "accession": ["0000000001-26-000001", "x-old"],
                                     "form": ["8-K", "8-K"], "filed": [pd.Timestamp.now().normalize(),
                                                                       pd.Timestamp("2020-01-02")],
                                     "cik": [320193, 320193]}))
    demo_db.set_event_items("0000000001-26-000001", "4.02,9.01", pd.Timestamp.now())
    d = DashboardData(cfg, demo_db, tmp_path)
    peers, group, cols = d.peers("AAPL")
    assert group and "AAPL" in set(peers["ticker"]) and "market_cap" in cols
    div = d.dividend_history("AAPL")
    assert {"dividends", "payout", "dividend_yield"} <= set(div.columns)
    f = d.company_filings("AAPL")
    assert "8-K" in set(f["form"]) and f["url"].iloc[0].startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    news = d.news(3)
    assert list(news["ticker"]) == ["AAPL"] and news["important"].iloc[0]          # 4.02: bilanci non affidabili
    assert news["what"].iloc[0] == "Bilanci passati non più affidabili"
    assert d.news(3, tickers=["MSFT"]).empty


# ---------------------------------------------------------------- pagina "Oggi" (ui/pages/monitor.py)
def test_market_strip_html_formats_in_italian():
    from ui.pages.monitor import market_strip_html

    q = pd.DataFrame({"last": [5123.456, 4.12], "change": [10.0, -0.05], "change_pct": [0.0123, np.nan]},
                     index=["^GSPC", "^TNX"])
    out = market_strip_html({"^GSPC": "S&P 500", "^TNX": "Tasso 10A", "X": "assente"}, q, {"^TNX"})
    assert "5.123,46" in out and "+1,23%" in out and "▲" in out
    assert "4,12%" in out and "−5 pb" in out and "▼" in out
    assert "assente" not in out and "None" not in out and "nan" not in out
    assert market_strip_html({"^GSPC": "S&P 500"}, pd.DataFrame(), set()) == ""


def test_exchanges_table_shows_holidays_and_partial_calendars():
    from modules import exchanges
    from ui.pages.monitor import exchanges_table_html

    christmas = pd.Timestamp("2026-12-25 15:00", tz="Europe/Rome")
    board = exchanges.board(["NYSE", "MIL", "TSE"], now=christmas)
    out = exchanges_table_html(board, {"NYSE": ["^GSPC", "S&P 500"], "MIL": ["FTSEMIB.MI", "FTSE MIB"]},
                               pd.DataFrame())
    assert "chiusa per" in out and "Natale" in out
    assert "TOKYO*" in out or "*" in out          # Tokyo ha un calendario parziale
    assert "festività non considerate" not in out and "None" not in out


def test_my_companies_table_order_and_margins(cfg, demo_db, tmp_path):
    from modules.dashboard_data import DashboardData
    from ui.pages.monitor import MY_COLUMNS, my_companies_table
    from ui.components import dense_table_html

    d = DashboardData(cfg, demo_db, tmp_path)
    q = pd.DataFrame({"last": [100.0, 50.0], "change_pct": [0.01, -0.02]}, index=["AAPL", "MSFT"])
    verdicts = pd.DataFrame({"verdict": ["Da evitare", "Candidata all'acquisto"], "category": ["evitare", "candidata"],
                             "rule": ["x", "y"]}, index=["AAPL", "MSFT"])
    t = my_companies_table(d, q, ["AAPL", "MSFT", "AMZN"], verdicts, fair={"AAPL": 120.0}, buy={"MSFT": 40.0},
                           held={"MSFT"}, unseen={"AAPL": 2}, one_year={})
    assert list(t["ticker"])[:2] == ["AAPL", "MSFT"]        # prima chi ha avvisi non visti, poi per verdetto
    a = t.set_index("ticker")
    assert a.at["AAPL", "margin"] == pytest.approx(0.2) and a.at["MSFT", "buy_gap"] == pytest.approx(0.25)
    assert a.at["AMZN", "category"] == "dati_insufficienti"
    out = dense_table_html(t, MY_COLUMNS)
    assert "./azienda?t=AAPL" in out and "Candidata" in out and "Evitare" in out and "ptf" in out
    assert "+20,0%" in out and "40,00" in out and "None" not in out and "nan" not in out


def test_colonne_facoltative_nascoste_se_vuote_e_verdetto_dopo_il_nome():
    import numpy as np
    import pandas as pd

    from ui.pages.monitor import MY_COLUMNS, visible_columns
    assert list(MY_COLUMNS)[:3] == ["ticker", "name", "category"]
    empty = pd.DataFrame({"ticker": ["A"], "margin": [np.nan], "buy_below": [np.nan]})
    assert "margin" not in visible_columns(empty) and "buy_below" not in visible_columns(empty)
    some = pd.DataFrame({"ticker": ["A", "B"], "margin": [np.nan, 0.1], "buy_below": [np.nan, np.nan]})
    assert "margin" in visible_columns(some) and "buy_below" not in visible_columns(some)
