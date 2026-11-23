"""Test delle azioni per classe dalla copertina dei 10-Q/10-K e del limite di età del numero di azioni."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules.market_data import MarketData
from modules.share_classes import ShareClassIngestor, equivalent_shares, parse_cover


def _table(rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


SECTIONS = _table([["Document and Entity Information - shares", "6 Months Ended", ""],
                   ["Entity Registrant Name", "BERKSHIRE HATHAWAY INC.", ""],
                   ["Common Class A [Member]", "", ""], ["Document Information [Line Items]", "", ""],
                   ["Entity Common Stock, Shares Outstanding", "", "488,450"],
                   ["Common Class B [Member]", "", ""],
                   ["Entity Common Stock, Shares Outstanding", "", "1,408,035,161"],
                   ["M 1.125 Senior Notes Due 2027 [Member]", "", ""]])
COLUMNS = _table([["Document and Entity Information", "3 Months Ended", ""],
                  ["Mar. 31, 2013", "Apr. 19, 2013 Class A Common Stock", "Apr. 19, 2013 Class B Common Stock"],
                  ["Document Type", "10-Q", "", ""], ["Entity Public Float", "$ 1,000", "", ""],
                  ["Entity Common Stock, Shares Outstanding", "", "46,697,008", "2,542"]])


def test_parse_cover_both_layouts():
    assert parse_cover(SECTIONS) == [("Common Class A [Member]", 488450.0), ("Common Class B [Member]", 1408035161.0)]
    assert parse_cover(COLUMNS) == [("Class A Common Stock", 46697008.0), ("Class B Common Stock", 2542.0)]
    assert parse_cover("<html>niente</html>") == []


def test_equivalent_shares():
    brk = {"class a": 1500, "class b": 1}
    assert equivalent_shares(parse_cover(SECTIONS), brk) == pytest.approx(488450 * 1500 + 1408035161)
    assert equivalent_shares(parse_cover(COLUMNS), {"class a": 1, "class b": 2400}) == 46697008 + 2542 * 2400
    hsy = {"common": 1, "class b": 1}                              # la chiave più lunga vince: "class b", non "common"
    assert equivalent_shares([("Common Stock", 10.0), ("Class B common stock", 5.0)], hsy) == 15
    assert equivalent_shares([("Class C", 10.0)], brk) is None     # classe senza fattore: niente numero sbagliato
    assert equivalent_shares([(None, 10.0)], brk) is None


def test_last_shares_ignores_stale_counts():
    st = pd.DataFrame({"shares": [100.0, np.nan, np.nan], "revenue": [1.0, 2.0, 3.0]},
                      index=pd.to_datetime(["2024-12-31", "2025-06-30", "2025-12-31"]))
    assert MarketData.last_shares(st) == 100.0                     # un anno prima dell'ultimo bilancio: ancora valido
    old = st.copy()
    old.index = pd.to_datetime(["2011-12-31", "2025-06-30", "2025-12-31"])
    assert np.isnan(MarketData.last_shares(old))                   # 14 anni prima: vuoto, non una capitalizzazione assurda
    assert np.isnan(MarketData.last_shares(st.drop(columns="shares")))


class FakeSec:
    def get_json(self, url):
        return {"filings": {"recent": {"accessionNumber": ["0001-26-000002", "0001-26-000001", "x"],
                                       "form": ["10-Q", "10-K", "8-K"], "filingDate": ["2026-08-10", "2026-02-28", "2026-01-01"],
                                       "reportDate": ["2026-06-30", "2025-12-31", ""]}}}

    def get_text(self, url):
        return SECTIONS


def test_ingestor_writes_shares_only_for_periods_with_statements(tmp_path):
    db = DBManager(tmp_path / "t.db")
    db.upsert_financials(pd.DataFrame({"ticker": ["BRK-B"], "period_end": [pd.Timestamp("2025-12-31")],
                                       "metric": ["revenue"], "value": [1.0], "filed": [pd.Timestamp("2026-02-28")],
                                       "source": ["sec"]}))
    cfg = {"sec": {"share_classes": {"BRK-B": {"classes": {"class a": 1500, "class b": 1}}}}}
    ing = ShareClassIngestor(db, FakeSec(), cfg)
    assert ing.update({"BRK-B": 1067983}) == 2
    fin = db.load_financials(["BRK-B"])
    shares = fin[fin["metric"] == "shares"]
    # il 10-Q di giugno non ha ancora un bilancio nel database: niente riga con le sole azioni
    assert list(shares["period_end"]) == [pd.Timestamp("2025-12-31")] and list(shares["source"]) == ["sec_classes"]
    assert shares["value"].iloc[0] == pytest.approx(488450 * 1500 + 1408035161)
    assert ing.update({"BRK-B": 1067983}) == 0                     # copertine già lette: nessun download
