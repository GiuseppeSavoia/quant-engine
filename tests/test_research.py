"""Test di ricerca dei segnali, qualità dei dati, backup e riproducibilità delle esecuzioni."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import run_info
from modules.config import default_config
from modules.data_quality import DataQualityChecker, summary
from modules.market_data import MarketData
from modules.research import SignalResearch, analyze, forward_returns, verdict
from modules.strategy import Strategy


# ---------------------------------------------------------------- ricerca dei segnali
def _random_close(n_days=400, n_tickers=60, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-01", periods=n_days)
    rets = rng.normal(0.0003, 0.02, (n_days, n_tickers))
    return pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=days,
                        columns=[f"T{i:02d}" for i in range(n_tickers)])


def test_forward_returns_start_the_day_after_the_signal():
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=pd.bdate_range("2024-01-01", periods=4))
    fwd = forward_returns(close, close.index[:2], horizon=1)
    assert fwd.loc[close.index[0], "A"] == pytest.approx(0.10)  # da 110 (giorno dopo) a 121
    assert fwd.loc[close.index[1], "A"] == pytest.approx(0.10)
    assert np.isnan(forward_returns(close, close.index[2:], horizon=1)["A"]).all()  # oltre la fine dei dati


def _signals_from(close, step, horizon, make):
    dates = close.index[:-(horizon + 2)][::step]
    fwd = forward_returns(close, dates, horizon)
    rows = []
    for d in dates:
        for t, v in make(fwd.loc[d]).items():
            rows.append({"date": d, "ticker": t, "sector": "S" + t[-1], "momentum": v})
    df = pd.DataFrame(rows)
    for c in ["quality", "volatility_score", "margin_of_safety", "mf_score", "confidence"]:
        df[c] = np.nan
    return df


def test_perfect_signal_is_detected():
    close = _random_close()
    sig = _signals_from(close, 21, 21, lambda f: f)  # il punteggio È il rendimento futuro
    summ, quant, ic = analyze(sig, close, horizons=[21], step=21)
    row = summ[summ["signal"] == "Momentum 12-1"].iloc[0]
    assert row["ic_mean"] == pytest.approx(1.0)
    assert row["ic_hit_rate"] == 1.0 and row["verdict"] == "solido"
    assert row["ic_sector_neutral"] > 0.9 and row["monotonicity"] == pytest.approx(1.0)
    q = quant[quant["signal"] == "Momentum 12-1"].sort_values("quantile")["mean_return"]
    assert q.is_monotonic_increasing and row["spread"] > 0
    # i segnali senza valori non producono numeri inventati
    assert summ[summ["signal"] == "Quality"]["n_dates"].iloc[0] == 0


def test_random_signal_shows_no_evidence():
    close = _random_close(n_days=700)
    rng = np.random.default_rng(1)
    sig = _signals_from(close, 21, 21, lambda f: pd.Series(rng.normal(size=len(f)), index=f.index))
    summ, _, _ = analyze(sig, close, horizons=[21], step=21)
    row = summ[summ["signal"] == "Momentum 12-1"].iloc[0]
    assert abs(row["ic_mean"]) < 0.05 and abs(row["ic_t"]) < 3


def test_verdict_thresholds():
    assert verdict(3.5, 3, 2) == "solido"
    assert verdict(2.5, 3, 2).startswith("promettente")
    assert verdict(-2.5, 3, 2) == "funziona al contrario"
    assert verdict(0.5, 3, 2) == "nessuna evidenza"
    assert verdict(float("nan"), 3, 2) == "dati insufficienti"


def test_research_on_demo_uses_strategy_pipeline(cfg, demo_data, universe):
    cfg["research"].update(years=1, step_days=42, horizons=[21])
    strategy = Strategy(cfg, tickers=universe.tickers(), sectors=universe.sectors())
    res = SignalResearch(cfg, strategy).run(demo_data)
    assert set(res.summary["signal"]) == {"Momentum 12-1", "Quality", "Bassa volatilità", "Margine DCF",
                                          "Multi-factor", "Confidenza"}
    assert res.signals["date"].nunique() == res.params["n_dates"]
    assert res.signals["momentum"].notna().any()


# ---------------------------------------------------------------- qualità dei dati
def _quality_data():
    days = pd.bdate_range("2026-01-01", "2026-06-30")
    rows = []
    for t in ["OK", "JUMP", "SPLIT", "OLD"]:
        for i, d in enumerate(days):
            if t == "OLD" and d > pd.Timestamp("2026-05-01"):
                continue
            px = 100.0 + 0.1 * i
            if t in ("JUMP", "SPLIT") and d >= pd.Timestamp("2026-03-02"):
                px /= 4  # split 4:1 non rettificato
            rows.append((t, d, px, px * 1.01, px * 0.99, px, 1e6))
    prices = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])
    fin = []
    for t in ["OK", "JUMP", "SPLIT"]:
        for y, sh in ((2024, 1e9), (2025, 1e9 if t == "OK" else 4e9)):
            for m, v in (("free_cash_flow", 1e8), ("shares", sh), ("revenue", 1e9), ("net_income", 1e8),
                         ("equity", 5e8)):
                fin.append((t, pd.Timestamp(f"{y}-12-31"), m, v, pd.Timestamp(f"{y + 1}-02-15"), "sec"))
    fin = pd.DataFrame(fin, columns=["ticker", "period_end", "metric", "value", "filed", "source"])
    return MarketData.from_long(prices, fin)


def test_quality_checks_find_known_problems():
    data = _quality_data()
    splits = pd.DataFrame({"ticker": ["SPLIT"], "date": [pd.Timestamp("2026-03-02")], "ratio": [4.0]})
    cfg = default_config()
    issues = DataQualityChecker(cfg["quality"], max_stale_days=5).run(data, ["OK", "JUMP", "SPLIT", "OLD", "NONE"],
                                                                      splits)
    found = set(zip(issues["ticker"], issues["check"]))
    assert ("NONE", "prezzi assenti") in found
    assert ("OLD", "prezzo vecchio") in found and ("OLD", "bilanci assenti") in found
    assert ("JUMP", "salto di prezzo") in found
    assert ("SPLIT", "salto di prezzo") not in found          # salto spiegato da uno split noto
    assert ("JUMP", "azioni discontinue") in found
    assert not any(t == "OK" for t, _ in found)
    s = summary(issues)
    assert s.iloc[0]["severity"] == "errore"                  # prima gli errori


# ---------------------------------------------------------------- backup e riproducibilità
def test_backup_once_per_day_and_pruned(tmp_path):
    db = DBManager(tmp_path / "lake.db")
    dest = tmp_path / "bk"
    first = db.backup(dest, keep=2, now=datetime(2026, 1, 1, 9))
    assert first is not None and first.exists()
    assert db.backup(dest, keep=2, now=datetime(2026, 1, 1, 18)) is None  # già fatto oggi
    db.backup(dest, keep=2, now=datetime(2026, 1, 2, 9))
    db.backup(dest, keep=2, now=datetime(2026, 1, 3, 9))
    names = sorted(p.name for p in dest.glob("*.db"))
    assert names == ["lake_20260102_090000.db", "lake_20260103_090000.db"]
    assert DBManager(dest / names[-1]).load_prices().empty  # la copia è un database valido
    assert db.backup(dest, keep=0) is None


def test_run_info_and_logging(tmp_path):
    cfg = default_config()
    assert run_info.config_hash(cfg) == run_info.config_hash(default_config())
    changed = default_config()
    changed["backtest"]["years"] = 5
    assert run_info.config_hash(changed) != run_info.config_hash(cfg)
    assert run_info.git_state(tmp_path) == (None, None)       # cartella fuori da git
    info = run_info.collect(cfg, ["--demo"], root=tmp_path)
    assert info["versions"]["python"] and "codice fuori da git" in run_info.label(info)
    db = DBManager(tmp_path / "r.db")
    db.log_run("r1", info)
    import sqlite3
    row = sqlite3.connect(tmp_path / "r.db").execute("SELECT config_hash, argv FROM runs").fetchone()
    assert row == (info["config_hash"], '["--demo"]')
