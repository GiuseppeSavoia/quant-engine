"""Test di integrazione: database, ingestione, pipeline completa, backtest senza look-ahead."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules.backtester import Backtester
from modules.data_ingestor import DataIngestor, normalize_download, normalize_statements
from modules.demo_data import DemoSource
from modules.strategy import Strategy


# ---------------------------------------------------------------- database
def test_legacy_schema_is_preserved(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE financials (id INTEGER PRIMARY KEY, ticker TEXT, period TEXT, metric TEXT, "
                 "value REAL, date TEXT)")
    conn.execute("INSERT INTO financials (ticker, period, metric, value, date) VALUES ('AAPL','FY23','FCF',1,'2023-09-30')")
    conn.commit()
    conn.close()
    DBManager(path)
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "legacy_financials" in tables and "financials" in tables
    assert conn.execute("SELECT COUNT(*) FROM legacy_financials").fetchone()[0] == 1
    conn.close()


def test_v1_financials_migrated_with_estimated_filing_date(tmp_path):
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE financials (ticker TEXT NOT NULL, period_end TEXT NOT NULL, metric TEXT NOT NULL, "
                 "value REAL, PRIMARY KEY (ticker, period_end, metric))")
    conn.execute("INSERT INTO financials VALUES ('AAPL', '2024-09-28', 'free_cash_flow', 1.0e11)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db = DBManager(path)
    fin = db.load_financials(["AAPL"])
    assert len(fin) == 1 and fin["filed"].iloc[0] == pd.Timestamp("2024-12-27")
    assert fin["source"].iloc[0] == "yahoo"


def test_price_upsert_is_idempotent(tmp_path):
    db = DBManager(tmp_path / "t.db")
    df = pd.DataFrame({"ticker": ["A", "A"], "date": ["2024-01-02", "2024-01-03"], "open": [1, 2],
                       "high": [1, 2], "low": [1, 2], "close": [1.0, 2.0], "volume": [10, 20]})
    db.upsert_prices(df)
    db.upsert_prices(df.assign(close=[1.5, 2.5]))
    out = db.load_prices(["A"])
    assert len(out) == 2 and out["close"].tolist() == [1.5, 2.5]


# ---------------------------------------------------------------- normalizzazione yfinance
def test_normalize_download_multiindex_both_orders():
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="Date")
    cols = pd.MultiIndex.from_product([["Close", "High", "Low", "Open", "Volume"], ["AAA", "BBB"]],
                                      names=["Price", "Ticker"])
    raw = pd.DataFrame(np.arange(20, dtype=float).reshape(2, 10) + 1, index=idx, columns=cols)
    a = normalize_download(raw, ["AAA", "BBB"])
    b = normalize_download(raw.swaplevel(0, 1, axis=1), ["AAA", "BBB"])
    assert len(a) == 4 and set(a["ticker"]) == {"AAA", "BBB"}
    pd.testing.assert_frame_equal(a.sort_values(["ticker", "date"]).reset_index(drop=True),
                                  b.sort_values(["ticker", "date"]).reset_index(drop=True))


def test_normalize_download_tz_aware_single():
    idx = pd.DatetimeIndex(["2024-01-02 00:00", "2024-01-03 00:00"]).tz_localize("America/New_York")
    raw = pd.DataFrame({"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [1.0, np.nan], "Volume": [5, 6]},
                       index=idx)
    out = normalize_download(raw, ["AAA"])
    assert len(out) == 1 and out["date"].dt.tz is None


def test_normalize_statements_derives_fcf():
    ends = pd.to_datetime(["2024-12-31", "2023-12-31"])
    cf = pd.DataFrame({ends[0]: [120.0, -20.0], ends[1]: [100.0, -30.0]},
                      index=["Operating Cash Flow", "Capital Expenditure"])
    out = normalize_statements("AAA", [cf])
    fcf = out[out["metric"] == "free_cash_flow"].set_index("period_end")["value"]
    assert fcf[ends[0]] == 100.0 and fcf[ends[1]] == 70.0


# ---------------------------------------------------------------- ingestione
def test_incremental_update_downloads_only_new_days(cfg):
    calls = []

    class Spy(DemoSource):
        def download_prices(self, tickers, start):
            calls.append((tuple(tickers), pd.Timestamp(start)))
            return super().download_prices(tickers, start)

    db = DBManager(cfg["data"]["db_path"])
    ing = DataIngestor(db, cfg, price_source=Spy(end="2026-10-30"), sleep=lambda s: None)
    tickers = ["AAPL", "MSFT", "SPY"]
    ing.update_prices(tickers, today=pd.Timestamp("2026-10-30").to_pydatetime())
    ing.update_prices(tickers, today=pd.Timestamp("2026-11-02").to_pydatetime())
    assert len(calls) == 2
    assert calls[1][1] >= pd.Timestamp("2026-10-19")  # secondo giro: solo gli ultimi giorni


def test_prices_are_chunked_and_missing_tickers_retried(cfg):
    calls = []

    class Flaky(DemoSource):
        def download_prices(self, tickers, start):
            calls.append(list(tickers))
            if len(calls) == 1:  # primo tentativo: Yahoo "limita" e restituisce solo metà dei ticker
                tickers = tickers[: len(tickers) // 2]
            return super().download_prices(tickers, start)

    cfg["data"]["price_chunk_size"] = 10
    db = DBManager(cfg["data"]["db_path"])
    src = Flaky(end="2026-10-30", n_tickers=25)
    DataIngestor(db, cfg, price_source=src, sleep=lambda s: None).update_prices(src.tickers)
    assert max(len(c) for c in calls) <= 10
    assert set(db.last_price_dates()) == set(src.tickers)  # i mancanti sono arrivati al nuovo tentativo


def test_full_history_source_replaces_stale_rows(cfg, demo_db, demo):
    """Una riga che la fonte non produce più (es. un valore che il lettore ora scarta) deve sparire."""
    stale = pd.DataFrame({"ticker": ["AAPL"], "period_end": ["2019-12-31"], "metric": ["equity"], "value": [-327e6],
                          "filed": ["2026-01-15"], "source": ["sec"]})
    demo_db.upsert_financials(stale)
    res = DataIngestor(demo_db, cfg, fundamentals_source=demo).update_fundamentals(["AAPL"], force=True)
    assert res == {"AAPL": True}
    fin = demo_db.load_financials(["AAPL"])
    assert not ((fin["metric"] == "equity") & (fin["value"] == -327e6)).any()
    assert len(fin) > 0


def test_fundamentals_not_refetched_when_fresh(cfg, demo_db, demo):
    ing = DataIngestor(demo_db, cfg, fundamentals_source=demo)
    assert ing.update_fundamentals(demo.tickers) == {}


# ---------------------------------------------------------------- split
class SplitSource:
    """Un titolo da 100 $ che fa uno split 4:1 il 2026-06-01 (poi vale 25 $). Come Yahoo,
    i prezzi restituiti sono rettificati per gli split avvenuti fino a `now`."""
    split_day = pd.Timestamp("2026-06-01")

    def __init__(self, now):
        self.now = pd.Timestamp(now)
        self.calls = []

    def download_prices(self, tickers, start):
        self.calls.append(pd.Timestamp(start))
        days = pd.bdate_range(pd.Timestamp(start), self.now)
        factor = 4.0 if self.now >= self.split_day else 1.0
        close = np.where(days < self.split_day, 100.0 / factor, 25.0)
        return pd.DataFrame({"ticker": "XYZ", "date": days, "open": close, "high": close, "low": close,
                             "close": close, "volume": 1e6, "split": np.where(days == self.split_day, 4.0, 0.0)})

    def fetch_splits(self, ticker):
        return pd.DataFrame({"ticker": [ticker], "date": [self.split_day], "ratio": [4.0]})


def test_longer_history_is_downloaded_once(cfg):
    """Aumentando history_years si scarica la parte mancante; un ticker che Yahoo non ha non si richiede più."""
    calls = []

    class Spy(DemoSource):
        def download_prices(self, tickers, start):
            calls.append((sorted(tickers), pd.Timestamp(start)))
            return super().download_prices(tickers, start)

    db = DBManager(cfg["data"]["db_path"])
    src = Spy(end="2026-10-30", years=6)
    today = pd.Timestamp("2026-10-30").to_pydatetime()
    cfg["data"]["history_years"] = 2
    ing = DataIngestor(db, cfg, price_source=src, sleep=lambda s: None)
    ing.update_prices(["AAPL", "X000"], today=today)                     # X000: delistato, Yahoo non lo ha
    assert db.first_price_dates()["AAPL"] > pd.Timestamp("2024-10-01")
    calls.clear()
    ing.update_prices(["AAPL", "X000"], today=today)
    assert all("X000" not in t for t, _ in calls)                         # non viene richiesto di nuovo
    cfg["data"]["history_years"] = 5
    calls.clear()
    ing.update_prices(["AAPL", "X000"], today=today)
    assert (["AAPL", "X000"], pd.Timestamp("2021-10-30")) in [(t, s.normalize()) for t, s in calls]
    assert db.first_price_dates()["AAPL"] < pd.Timestamp("2021-11-15")    # storico allungato
    calls.clear()
    ing.update_prices(["AAPL"], today=today)
    assert all(s > pd.Timestamp("2026-01-01") for _, s in calls)          # poi di nuovo solo incrementale


def test_new_split_reloads_whole_history(cfg):
    db = DBManager(cfg["data"]["db_path"])
    src = SplitSource("2026-05-15")
    ing = DataIngestor(db, cfg, price_source=src, sleep=lambda s: None)
    ing.update_prices(["XYZ"], today=src.now.to_pydatetime())
    first_day = db.load_prices(["XYZ"])["date"].min()
    assert set(db.load_prices(["XYZ"])["close"]) == {100.0}

    src.now = pd.Timestamp("2026-06-10")
    res = ing.update_prices(["XYZ"], today=src.now.to_pydatetime())
    assert res["reloaded"] == ["XYZ"]
    px = db.load_prices(["XYZ"])
    assert set(px["close"]) == {25.0}                  # niente falso crollo del -75%
    assert px["date"].min() == first_day                # stessa profondità di prima
    assert db.load_splits(["XYZ"])["ratio"].tolist() == [4.0]


def test_split_backfill_repairs_unadjusted_archive(cfg):
    db = DBManager(cfg["data"]["db_path"])
    days = pd.bdate_range("2026-05-01", "2026-06-10")
    close = np.where(days < SplitSource.split_day, 100.0, 25.0)  # archivio a cavallo dello split, non rettificato
    db.upsert_prices(pd.DataFrame({"ticker": "XYZ", "date": days, "close": close}))
    ing = DataIngestor(db, cfg, price_source=SplitSource("2026-06-10"), sleep=lambda s: None)
    assert ing.update_splits(["XYZ"], today=pd.Timestamp("2026-06-10").to_pydatetime()) == ["XYZ"]
    assert set(db.load_prices(["XYZ"])["close"]) == {25.0}
    assert ing.update_splits(["XYZ"]) == []             # lo storico degli split si scarica una volta sola


def test_split_backfill_keeps_adjusted_archive(cfg):
    db = DBManager(cfg["data"]["db_path"])
    days = pd.bdate_range("2026-05-01", "2026-06-10")
    db.upsert_prices(pd.DataFrame({"ticker": "XYZ", "date": days, "close": 25.0}))
    src = SplitSource("2026-06-10")
    assert DataIngestor(db, cfg, price_source=src, sleep=lambda s: None).update_splits(["XYZ"]) == []
    assert src.calls == []
    assert len(db.load_splits()) == 1


def test_shares_adjusted_for_later_splits():
    from modules.market_data import adjust_shares_for_splits

    fin = pd.DataFrame({
        "ticker": ["A", "A", "A", "A", "B"],
        "metric": ["shares", "shares", "shares", "revenue", "shares"],
        "value": [100.0, 100.0, 1000.0, 5.0, 100.0],
        "filed": pd.to_datetime(["2020-01-01", "2023-01-01", "2025-01-01", "2020-01-01", "2020-01-01"]),
        "source": ["sec", "yahoo", "sec", "sec", "sec"],
    })
    splits = pd.DataFrame({"ticker": ["A", "A"], "date": pd.to_datetime(["2021-06-01", "2024-06-01"]),
                           "ratio": [4.0, 2.5]})
    out = adjust_shares_for_splits(fin, splits)
    assert out["value"].tolist() == [1000.0, 100.0, 1000.0, 5.0, 100.0]
    # prima dei due split x10; Yahoo, ricavi e titoli senza split invariati


def test_demo_split_does_not_distort_valuation(demo_db, demo_data):
    raw = demo_db.load_financials(["NVDA"])
    raw_shares = raw[raw["metric"] == "shares"]["value"]
    assert raw_shares.max() / raw_shares.min() > 9      # alla SEC: base vecchia prima dello split
    split_day = demo_db.load_splits(["NVDA"])["date"].iloc[0]
    for day in (split_day - pd.Timedelta(days=30), demo_data.last_date):
        shares = demo_data.as_of(day).statements("NVDA")["shares"].dropna()
        assert shares.max() / shares.min() < 1.02       # sulla base dei prezzi rettificati


# ---------------------------------------------------------------- pipeline
def test_strategy_runs_end_to_end(cfg, demo_data, universe):
    tickers = universe.tickers()
    res = Strategy(cfg, tickers=tickers, sectors=universe.sectors()).run(demo_data.as_of(demo_data.last_date))
    assert res.regime.label in {"Bull", "Neutral", "Bear"}
    assert [d["ticker"] for d in res.decisions] == tickers
    assert all(d["action"] in {"BUY", "SKIP", "BLOCKED"} for d in res.decisions)
    valued = [d for d in res.decisions if d.get("fair_value")]
    assert valued, "almeno un titolo deve avere un fair value"
    assert all(np.isfinite(d["fair_value"]) and d["wacc"] > 0 for d in valued)
    assert res.weights.sum() <= 1.0 + 1e-9
    # i titoli finanziari non vengono valutati con il DCF
    fin = [d for d in res.decisions if d.get("sector") == "Financials"]
    assert fin and all(d.get("fair_value") is None for d in fin)


def test_max_positions(cfg, demo_data, universe):
    cfg["confidence"]["thresholds"] = {"Bull": 0.0, "Neutral": 0.0, "Bear": 0.0}
    cfg["portfolio"]["max_positions"] = 5
    res = Strategy(cfg, tickers=universe.tickers()).run(demo_data.as_of(demo_data.last_date))
    assert sum(d["action"] == "BUY" for d in res.decisions) == 5
    assert len(res.weights) <= 5


def test_point_in_time_snapshot_uses_filing_dates(demo_data):
    day = demo_data.calendar[-300]
    snap = demo_data.as_of(day)
    assert snap.close.index.max() <= day
    assert (snap.financials["filed"] <= day).all()
    # con più versioni dello stesso periodo vale l'ultima già depositata
    full = demo_data.financials
    t = full["ticker"].iloc[0]
    sub = full[(full["ticker"] == t) & (full["metric"] == "free_cash_flow")]
    versions = sub.groupby("period_end")["filed"].nunique()
    period = versions[versions > 1].index[0]
    v = sub[sub["period_end"] == period].sort_values("filed")
    first, second = v.iloc[0], v.iloc[1]
    before = demo_data.as_of(second["filed"] - pd.Timedelta(days=1)).statements(t)
    after = demo_data.as_of(second["filed"]).statements(t)
    assert before.loc[period, "free_cash_flow"] == pytest.approx(first["value"])
    assert after.loc[period, "free_cash_flow"] == pytest.approx(second["value"])


def test_backtest_has_no_lookahead_and_valid_output(cfg, demo_data, universe):
    seen = []

    class Recording(Strategy):
        def run(self, data, tickers=None, drawdown=0.0):
            seen.append((data.last_date, data.close.index.max(),
                         data.financials["filed"].max() if not data.financials.empty else None,
                         list(tickers or [])))
            return super().run(data, tickers, drawdown)

    from modules.universe import members_at

    cfg["backtest"]["years"] = 2
    strategy = Recording(cfg, tickers=universe.tickers())
    bt = Backtester(cfg, strategy=strategy, universe_fn=lambda d: universe.tickers(as_of=d)).run(demo_data)
    assert seen
    membership = universe.db.load_membership()
    removed = membership.dropna(subset=["end"]).set_index("ticker")["end"]
    for as_of, max_price_date, max_filed, tickers in seen:
        assert max_price_date <= as_of
        assert max_filed is None or max_filed <= as_of
        assert tickers == members_at(membership, as_of)                       # composizione storica
        for t, end in removed.items():                                         # ex membri solo prima dell'uscita
            assert (t in tickers) == (as_of < end)
    # l'ex membro delistato non ha prezzi: la copertura dell'universo lo misura
    before = bt.coverage[bt.coverage["date"] < removed["X000"]]
    assert (before["with_price"] < before["members"]).all()
    assert 0 < bt.metrics["min_coverage"] < 1 and bt.metrics["avg_coverage"] < 1
    # solo giorni di borsa veri, nessun buco o valore anomalo nell'equity
    assert bt.equity.index.isin(demo_data.calendar).all()
    assert bt.equity.notna().all() and (bt.equity > 0).all()
    assert bt.equity.pct_change().abs().max() < 0.25
    for key in ("total_return", "sharpe_ratio", "max_drawdown", "n_trades"):
        assert key in bt.metrics
    if not bt.trades.empty:
        first_signal = seen[0][0]
        assert bt.trades["date"].min() > first_signal  # eseguito il giorno dopo il segnale


def test_main_demo_runs(tmp_path, monkeypatch):
    import main as entry
    from modules import config as config_mod

    monkeypatch.setattr(entry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    assert entry.main(["--demo", "--backtest", "--strategy"]) == 0  # la strategia automatica è facoltativa
    assert entry.main(["--demo", "--no-update", "--research"]) == 0  # seconda esecuzione solo dal database
    assert list((tmp_path / "reports" / "demo").glob("ricerca_*.html"))
    assert list((tmp_path / "reports" / "demo").glob("qualita_dati_*.csv"))
    assert list((tmp_path / "reports" / "demo").glob("analisi_*.html"))
    assert list((tmp_path / "reports" / "demo").glob("backtest_*.html"))


_DETERMINISM_SCRIPT = """
import sys, logging
sys.path.insert(0, {root!r})
logging.disable(logging.WARNING)
from data.db_manager import DBManager
from modules.config import default_config
from modules.data_ingestor import DataIngestor
from modules.demo_data import DemoSource
from modules.market_data import MarketData
from modules.strategy import Strategy
from modules.backtester import Backtester
cfg = default_config()
cfg["universe"]["tickers"] = DemoSource(end="2026-10-30", n_tickers=30).tickers
cfg["portfolio"]["max_weight"] = 0.5      # pochi titoli molto pesati: la cassa finisce prima degli acquisti
db = DBManager({db!r})
src = DemoSource(end="2026-10-30", n_tickers=30)
DataIngestor(db, cfg, price_source=src, fundamentals_source=src, sleep=lambda s: None).update_all(src.tickers)
data = MarketData.from_db(db, cfg, src.tickers)
bt = Backtester(cfg, strategy=Strategy(cfg, tickers=src.tickers)).run(data)
print(bt.equity.round(6).to_json())
"""


def test_backtest_is_deterministic_across_processes(tmp_path):
    """Lo stesso backtest deve dare lo stesso risultato in ogni processo (niente ordine dei set)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent.parent)
    outputs = []
    for seed in ("1", "2"):
        script = _DETERMINISM_SCRIPT.format(root=root, db=str(tmp_path / f"d{seed}.db"))
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300,
                             env={**os.environ, "PYTHONHASHSEED": seed})
        assert res.returncode == 0, res.stderr[-2000:]
        outputs.append(res.stdout)
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------- apertura (candele)
def test_open_prices_flow_from_db_to_snapshot(demo_data, tmp_path):
    from modules.market_data import MarketData
    data = demo_data
    t = next(c for c in data.close.columns if c not in (data.benchmark, data.risk_free_ticker))
    # l'apertura arriva dal database come gli altri prezzi, sullo stesso calendario, ed è dentro l'escursione
    assert not data.open.empty and t in data.open
    o, lo, hi = data.open[t].dropna(), data.low[t], data.high[t]
    assert len(o) == len(data.close[t].dropna())
    assert ((o >= lo.reindex(o.index) - 1e-9) & (o <= hi.reindex(o.index) + 1e-9)).all()
    ohlc = data.ohlc(t)
    assert list(ohlc.columns[:4]) == ["open", "high", "low", "close"]
    # point-in-time: anche l'apertura si ferma alla data
    cut = data.close.index[len(data.close) // 2]
    assert data.as_of(cut).open.index.max() == cut
    # istantanea parquet: l'apertura c'è; una copia vecchia senza il file dell'apertura si ricostruisce
    folder = tmp_path / "snap"
    data.save_snapshot(folder, "k1")
    loaded = MarketData.load_snapshot(folder, "k1")
    pd.testing.assert_frame_equal(loaded.open, data.open)
    (folder / "open.parquet").unlink()
    assert MarketData.load_snapshot(folder, "k1") is None


def test_market_data_without_open_still_works():
    from modules.market_data import MarketData
    days = pd.bdate_range("2024-01-01", periods=5)
    long = pd.DataFrame({"ticker": "X", "date": days, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 10.0})
    data = MarketData.from_long(long, pd.DataFrame())
    assert data.open.empty and "open" not in data.ohlc("X") and len(data.as_of(days[2]).close) == 3
