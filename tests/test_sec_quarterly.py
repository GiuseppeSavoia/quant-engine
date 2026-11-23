"""Test dei trimestri singoli, dell'archivio completo dei valori SEC, dell'indice dei depositi e
dell'aggiornamento automatico. Nessuna rete: JSON companyfacts e indici SEC finti."""
import plistlib
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager, FactsStore
from modules import scheduler, sec_filings, updater
from modules import sec_quarterly as sq
from modules.data_ingestor import DataIngestor
from modules.http_client import HttpError

FY_START = "2025-01-01"
Q1, Q2, Q3, FY = "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"
F_Q1, F_Q2, F_Q3, F_FY = "2025-05-01", "2025-08-01", "2025-11-01", "2026-02-15"


def _f(start, end, val, accn, form, filed):
    d = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def _payload() -> dict:
    """Esercizio 2025: ricavi dichiarati per trimestre (tranne il quarto), flussi di cassa solo da inizio
    esercizio, utile per azione e un valore istantaneo. Il 10-Q del secondo trimestre rivede il primo."""
    revenues = [_f(FY_START, Q1, 100, "q1", "10-Q", F_Q1),
                _f("2025-04-01", Q2, 110, "q2", "10-Q", F_Q2), _f(FY_START, Q2, 210, "q2", "10-Q", F_Q2),
                _f("2025-07-01", Q3, 120, "q3", "10-Q", F_Q3), _f(FY_START, Q3, 330, "q3", "10-Q", F_Q3),
                _f(FY_START, FY, 460, "k", "10-K", F_FY)]
    ocf = [_f(FY_START, Q1, 30, "q1", "10-Q", F_Q1), _f(FY_START, Q2, 70, "q2", "10-Q", F_Q2),
           _f(FY_START, Q3, 100, "q3", "10-Q", F_Q3), _f(FY_START, FY, 150, "k", "10-K", F_FY),
           _f(FY_START, Q1, 32, "q2", "10-Q", F_Q2)]  # primo trimestre rivisto nel 10-Q successivo
    capex = [_f(FY_START, Q1, 10, "q1", "10-Q", F_Q1), _f(FY_START, Q2, 20, "q2", "10-Q", F_Q2),
             _f(FY_START, Q3, 30, "q3", "10-Q", F_Q3), _f(FY_START, FY, 45, "k", "10-K", F_FY)]
    eps = [_f(FY_START, Q3, 3.3, "q3", "10-Q", F_Q3), _f(FY_START, FY, 4.6, "k", "10-K", F_FY)]
    shares = [_f("2025-04-01", Q2, 50e6, "q2", "10-Q", F_Q2)]
    cash = [_f(None, Q1, 500, "q1", "10-Q", F_Q1), _f(None, FY, 650, "k", "10-K", F_FY)]
    news = [_f(FY_START, Q1, 999, "8k", "8-K", F_Q1)]  # comunicato: non è un bilancio
    usd = lambda facts, label="x": {"label": label, "units": {"USD": facts}}  # noqa: E731
    return {"facts": {
        "us-gaap": {"Revenues": usd(revenues + news, "Ricavi"),
                    "NetCashProvidedByUsedInOperatingActivities": usd(ocf),
                    "PaymentsToAcquirePropertyPlantAndEquipment": usd(capex),
                    "CashAndCashEquivalentsAtCarryingValue": usd(cash),
                    "EarningsPerShareDiluted": {"label": "EPS", "units": {"USD/shares": eps}},
                    "WeightedAverageNumberOfDilutedSharesOutstanding": {"label": "azioni", "units": {"shares": shares}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"label": "copertina", "units": {
            "shares": [_f(None, "2025-07-20", 50e6, "q2", "10-Q", F_Q2)]}}},
    }}


def _last(q, concept):
    s = q[q["concept"] == concept].sort_values("filed").drop_duplicates("period_end", keep="last")
    return s.set_index("period_end")["value"]


def test_quarterly_values_direct_differences_and_versions():
    facts, labels = sq.extract_all_facts(_payload())
    assert "8-K" not in set(facts["form"]) and (labels.set_index("concept").at["Revenues", "label"] == "Ricavi")
    q = sq.quarterly_values(facts)
    rev = _last(q, "Revenues")
    assert rev.to_dict() == {pd.Timestamp(Q1): 100, pd.Timestamp(Q2): 110, pd.Timestamp(Q3): 120,
                             pd.Timestamp(FY): 130}                                 # quarto = 460 - 330
    ocf = _last(q, "NetCashProvidedByUsedInOperatingActivities")
    assert ocf[pd.Timestamp(Q2)] == 38 and ocf[pd.Timestamp(Q3)] == 30 and ocf[pd.Timestamp(FY)] == 50
    # il primo trimestre ha due versioni: 30 depositato a maggio, 32 ad agosto (point-in-time)
    q1 = q[(q["concept"] == "NetCashProvidedByUsedInOperatingActivities") & (q["period_end"] == Q1)]
    assert list(q1["value"]) == [30, 32] and list(q1["filed"]) == [pd.Timestamp(F_Q1), pd.Timestamp(F_Q2)]
    # il secondo trimestre calcolato ad agosto usa il primo come era noto ad agosto (32): 70 - 32
    assert q[(q["concept"] == "Revenues") & (q["period_end"] == Q2)]["how"].tolist() == ["trimestre"]
    assert q[(q["concept"] == "Revenues") & (q["period_end"] == FY)]["how"].tolist() == ["differenza"]
    assert q[q["concept"] == "EarningsPerShareDiluted"].empty                     # gli utili per azione non si sottraggono
    assert _last(q, "CashAndCashEquivalentsAtCarryingValue")[pd.Timestamp(FY)] == 650


def test_standard_quarters_compute_fcf_and_shares():
    facts, _ = sq.extract_all_facts(_payload())
    std = sq.standard_quarters(sq.quarterly_values(facts), "AAA", facts)
    last = std.sort_values("filed").drop_duplicates(["period_end", "metric"], keep="last") \
        .pivot(index="period_end", columns="metric", values="value")
    assert last.at[pd.Timestamp(FY), "revenue"] == 130
    assert last.at[pd.Timestamp(Q3), "free_cash_flow"] == 30 - 10
    assert last.at[pd.Timestamp(FY), "capex"] == -15
    assert last.at[pd.Timestamp(Q2), "shares"] == 50e6 and np.isnan(last.at[pd.Timestamp(FY), "shares"])
    first = std[(std["period_end"] == Q1) & (std["metric"] == "operating_cash_flow")]
    assert list(first["value"]) == [30, 32] and set(std["source"]) == {sq.QUARTERLY_SOURCE}


def test_facts_store_round_trip(tmp_path):
    facts, labels = sq.extract_all_facts(_payload())
    store = FactsStore(tmp_path / "facts.db")
    assert store.replace("AAA", facts, labels) == len(facts)
    store.replace("AAA", facts, labels)                                  # sostituisce, non duplica
    assert store.tickers() == {"AAA"}
    concepts = store.concepts("AAA")
    rev = concepts[concepts["concept"] == "Revenues"].iloc[0]
    assert rev["n"] == 6 and rev["label"] == "Ricavi" and rev["last_end"] == FY
    back = store.load("AAA", [int(rev["concept_id"])])
    assert len(back) == 6 and back["end"].max() == pd.Timestamp(FY)
    assert len(store.load("AAA")) == len(facts)


INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------
10-Q             APPLE INC                                                     320193      20261103    edgar/data/320193/0000320193-26-000101.txt
10-K/A           SOMEONE ELSE                                                  999         20261103    edgar/data/999/0000000999-26-000001.txt
8-K              APPLE INC                                                     320193      20261103    edgar/data/320193/0000320193-26-000100.txt
"""

ATOM = """<feed><entry>
<title>10-Q - JPMORGAN CHASE &amp; CO (0000019617) (Filer)</title>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-04 &lt;b&gt;AccNo:&lt;/b&gt; 0000019617-26-000555</summary>
<category scheme="https://www.sec.gov/" label="form type" term="10-Q"/>
<id>urn:tag:sec.gov,2008:accession-number=0000019617-26-000555</id>
</entry><entry>
<title>10-Q - OLD CORP (0000000777) (Filer)</title>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-03 &lt;b&gt;AccNo:&lt;/b&gt; 0000000777-26-000001</summary>
<category scheme="https://www.sec.gov/" label="form type" term="10-Q"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000777-26-000001</id>
</entry></feed>"""


def test_parse_index_and_current_feed():
    idx = sec_filings.parse_form_index(INDEX)
    assert list(idx["form"]) == ["10-Q", "10-K/A", "8-K"] and idx["accession"].iloc[0] == "0000320193-26-000101"
    cur = sec_filings.parse_current(ATOM)
    assert list(cur["cik"]) == [19617, 777] and cur["filed"].iloc[0] == pd.Timestamp("2026-11-04")


class FakeSec:
    def __init__(self, days: dict):
        self.days, self.calls = days, []

    def get_json(self, url):
        self.calls.append(url)
        assert url.endswith("/2026/QTR4/index.json")
        return {"directory": {"item": [{"name": f"form.{d}.idx"} for d in self.days] + [{"name": "x.idx"}]}}

    def get_text(self, url):
        self.calls.append(url)
        if "getcurrent" in url:
            return ATOM if "type=10-Q" in url else "<feed></feed>"
        for day, text in self.days.items():
            if f"form.{day}.idx" in url:
                return text
        raise HttpError(f"404 non trovato: {url}")


def test_filing_watcher_index_holidays_and_settle(tmp_path):
    db = DBManager(tmp_path / "t.db")
    cfg = {"sec": {"filings_lookback_days": 6, "filings_index_wait_days": 4}}
    http = FakeSec({"20261103": INDEX})
    now = pd.Timestamp("2026-11-04 18:00", tz="America/New_York")         # mercoledì
    pending = sec_filings.FilingWatcher(db, http, cfg).check({"AAPL": 320193, "JPM": [19617], "X": None}, now=now)
    assert sorted(pending["ticker"]) == ["AAPL", "JPM"]                   # 8-K e aziende non seguite esclusi
    events = db.load_events()
    assert list(events["accession"]) == ["0000320193-26-000100"] and events["items"].isna().all()   # 8-K a parte
    done = db.fetched_tickers(sec_filings.INDEX_KIND)
    assert "2026-11-03" in done and "2026-10-29" in done                   # festivo: indice assente da 6 giorni
    assert "2026-11-02" not in done                                        # forse non ancora pubblicato
    assert not any("form.2026110" in u and "20261103" not in u for u in http.calls)  # chiesti solo gli indici pubblicati
    again = sec_filings.FilingWatcher(db, http, cfg).check({"AAPL": 320193}, now=now)
    assert len(again) == 2                                                 # nessun doppione
    db.settle_filings("AAPL", {"0000320193-26-000101"}, give_up_days=10)
    db.settle_filings("JPM", set(), give_up_days=10)
    status = db.load_filings().set_index("ticker")["status"]
    assert status["AAPL"] == "caricato" and status["JPM"] == "da caricare"


class FakeBundleSource:
    cik_map = {"AAA": 1, "BBB": 2}
    full_history = True
    sources = ("sec", "sec_ttm")

    def __init__(self):
        self.fetched = []

    def fetch_bundle(self, ticker):
        self.fetched.append(ticker)
        facts, labels = sq.extract_all_facts(_payload())
        facts = facts.assign(accn=facts["accn"].replace({"q3": "0000000001-26-000003"}))
        stmt = pd.DataFrame({"ticker": [ticker], "period_end": [pd.Timestamp(FY)], "metric": ["revenue"],
                             "value": [460.0], "filed": [pd.Timestamp(F_FY)], "source": ["sec"]})
        return {"statements": stmt, "facts": facts, "labels": labels,
                "quarterly": sq.standard_quarters(sq.quarterly_values(facts), ticker, facts)}


def test_ingestor_fills_archive_then_refreshes_only_new_filings(cfg, tmp_path):
    db = DBManager(cfg["data"]["db_path"])
    store = FactsStore(cfg["data"]["facts_db_path"])
    src = FakeBundleSource()
    ing = DataIngestor(db, cfg, fundamentals_source=src, facts_store=store, max_workers=1)
    ing.update_fundamentals(["AAA", "BBB"])
    assert sorted(src.fetched) == ["AAA", "BBB"] and store.tickers() == {"AAA", "BBB"}
    assert not db.load_quarterly(["AAA"]).empty
    src.fetched.clear()
    ing.update_fundamentals(["AAA", "BBB"])
    assert src.fetched == []                                              # niente di nuovo: nessun download
    db.add_filings(pd.DataFrame({"ticker": ["BBB"], "accession": ["0000000001-26-000003"], "form": ["10-Q"],
                                 "filed": [pd.Timestamp(F_Q3)]}))
    ing.update_fundamentals(["AAA", "BBB"], refresh=["BBB"])
    assert src.fetched == ["BBB"] and db.pending_filings().empty           # deposito trovato nei dati: caricato


def test_dashboard_quarterly_and_archive(cfg, demo_db, universe, tmp_path):
    from modules.dashboard_data import DashboardData

    facts, labels = sq.extract_all_facts(_payload())
    demo_db.replace_quarterly("AAPL", sq.standard_quarters(sq.quarterly_values(facts), "AAPL", facts))
    FactsStore(cfg["data"]["facts_db_path"]).replace("AAPL", facts, labels)
    d = DashboardData(cfg, demo_db, tmp_path)
    q = d.quarterly("AAPL")
    assert q.at[pd.Timestamp(FY), "revenue"] == 130 and q.at[pd.Timestamp(Q3), "fcf_margin"] == pytest.approx(20 / 120)
    assert not d.sec_concepts("AAPL").empty and d.sec_concepts("MSFT").empty
    wide = d.sec_quarters_wide("AAPL")
    assert wide.set_index("concept").at["Revenues", FY] == 130 and "label" in wide


def test_scheduled_update_runs_once_after_each_close(tmp_path):
    ny = "America/New_York"
    # mercoledì 15:00 a New York: l'ultima chiusura è martedì
    assert updater.last_close(pd.Timestamp("2026-11-04 15:00", tz=ny), 30) == pd.Timestamp("2026-11-03 16:30", tz=ny)
    # lunedì mattina: venerdì
    assert updater.last_close(pd.Timestamp("2026-11-02 09:00", tz=ny), 30) == pd.Timestamp("2026-10-30 16:30", tz=ny)
    cfg = {"schedule": {"after_close_minutes": 30}}
    now = pd.Timestamp("2026-11-04 17:00", tz=ny)
    assert updater.due(cfg, tmp_path, now)[0]                              # mai aggiornato
    local = now.tz_convert(datetime.now().astimezone().tzinfo).tz_localize(None)
    updater._write_status(tmp_path, {"pid": 1, "args": [], "started": local.isoformat(),
                                     "finished": local.isoformat(), "returncode": 0})
    assert not updater.due(cfg, tmp_path, now)[0]                          # già fatto dopo la chiusura
    updater._write_status(tmp_path, {"pid": 1, "args": ["--no-update"], "started": local.isoformat(),
                                     "finished": local.isoformat(), "returncode": 0})
    assert updater.due(cfg, tmp_path, now)[0]                              # senza download non conta


def test_launch_agent_plist(tmp_path):
    path = scheduler.install({"schedule": {"check_every_minutes": 60}}, root=tmp_path, path=tmp_path / "a.plist",
                             python="/py", load=False)
    agent = plistlib.loads(path.read_bytes())
    assert agent["ProgramArguments"] == ["/py", "-m", "modules.updater", "--scheduled"]
    assert agent["StartInterval"] == 3600 and agent["RunAtLoad"] and agent["WorkingDirectory"] == str(tmp_path)
    assert scheduler.installed(path) and scheduler.uninstall(path, unload=False) and not path.exists()


HEADER = """<SEC-HEADER>0000320193-26-000100.hdr.sgml : 20261103
<ACCEPTANCE-DATETIME>20261103163015
<TYPE>8-K
<ITEMS>2.02
<ITEMS>9.01
<FILING-DATE>20261103
"""


def test_event_items_and_descriptions(tmp_path):
    db = DBManager(tmp_path / "t.db")
    db.add_events(pd.DataFrame({"ticker": ["AAPL"], "accession": ["0000320193-26-000100"], "form": ["8-K"],
                                "filed": [pd.Timestamp("2026-11-03")], "cik": [320193]}))

    class Http:
        def get_text(self, url):
            assert url.endswith("/320193/000032019326000100/0000320193-26-000100.hdr.sgml")
            return HEADER

    assert sec_filings.FilingWatcher(db, Http(), {}).fill_items() == 1
    ev = db.load_events(["AAPL"]).iloc[0]
    assert ev["items"] == "2.02,9.01" and ev["accepted"] == pd.Timestamp("2026-11-03 16:30:15")
    assert sec_filings.describe_items(ev["items"]) == "Risultati del trimestre o dell'esercizio"
    assert sec_filings.describe_items("9.01") == "Allegati (comunicati, bilanci)"
    assert sec_filings.important("4.02,9.01") and not sec_filings.important(ev["items"])
    assert sec_filings.FilingWatcher(db, Http(), {}).fill_items() == 0              # già lette
