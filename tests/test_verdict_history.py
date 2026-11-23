"""Storico dei giudizi (modules/verdict_history.py), avviso VRD, lettura veloce e misura in avanti
(modules/verdict_tracking.py). Solo database temporanei / copia della demo."""
import numpy as np
import pandas as pd
import pytest

from data.db_manager import DBManager
from modules import alerts
from modules import verdict as vd
from modules import verdict_history as vh
from modules import verdict_tracking as vt


@pytest.fixture
def app(cfg, demo_db, tmp_path):
    return vh.app_data(cfg, demo_db, tmp_path)


def _same(a, b):
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_record_and_fast_read_match_assess_many(cfg, demo_db, app):
    res = vh.record(demo_db, cfg, d=app, run_id=app.run_id)
    tickers = list(app.tickers)
    assert res["rows"] == len(tickers) and res["missing"] == 0
    hist = demo_db.load_verdict_history()
    assert len(hist) == len(tickers) and hist["as_of"].nunique() == 1
    assert set(hist["universe"]) <= {"sp500", "europe", "custom", "other"}
    # stessa data dei dati: la seconda scrittura sostituisce, niente doppioni
    vh.record(demo_db, cfg, d=app, run_id=app.run_id)
    assert len(demo_db.load_verdict_history()) == len(tickers)

    full = vd.assess_many(app, tickers)
    saved, stale = vh.saved_verdicts(app, tickers)
    assert stale == [] and len(saved) == len(tickers)
    _same(vh.load_or_assess(app, tickers), full)
    _same(vh.load_or_assess(app, tickers[:5]), vd.assess_many(app, tickers[:5]))

    # fair value dell'utente cambiato: solo quel titolo si ricalcola, il risultato è quello di assess_many
    t = tickers[0]
    demo_db.save_note(t, fair_value=1000.0)
    _, stale = vh.saved_verdicts(app, tickers)
    assert stale == [t]
    _same(vh.load_or_assess(app, tickers), vd.assess_many(app, tickers))

    # configurazione del giudizio diversa: niente lettura veloce
    app.cfg = {**app.cfg, "verdict": {"quality_good": 0.9}}
    assert vh.saved_verdicts(app, tickers)[1] == tickers
    # esecuzione diversa (dati di un altro aggiornamento): idem
    app.cfg = cfg
    app.run_id = "altro"
    assert vh.saved_verdicts(app, tickers)[1] == tickers


def test_fingerprint_ignores_history_settings(cfg):
    a = vh.config_fingerprint(cfg)
    c2 = {**cfg, "verdict": {"history": {"keep_rule_days": 1}, "tracking": {"min_age_days": 5}}}
    assert vh.config_fingerprint(c2) == a
    assert vh.config_fingerprint({**cfg, "verdict": {"price_good": 0.7}}) != a


def test_rule_text_pruned_after_keep_days(tmp_path):
    db = DBManager(tmp_path / "h.db")
    rows = [_row("AAA", d, vd.WAIT) for d in ("2026-01-02", "2026-03-02")]
    db.save_verdict_history(rows, {"run_id": "r", "as_of": "2026-03-02"})
    assert db.prune_verdict_rules("2026-02-01") == 1
    h = db.load_verdict_history()
    assert h["rule"].isna().tolist() == [True, False]
    assert db.verdict_history_stats()["rows"] == 2


# ------------------------------------------------------------------------------------------------ avviso VRD
def _row(t, day, cat, price_pillar=0.5, quality=0.7, cfg_hash="h1", **kw):
    return {"as_of": day, "ticker": t, "run_id": "r" + day, "config_hash": cfg_hash, "universe": "sp500",
            "category": cat, "profile": "general", "quality": quality, "solidity": 0.6, "price_pillar": price_pillar,
            "flags": 0.8, "insiders": None, "coverage": 0.9, "overall": 0.6, "alarms": 0, "checks": 0, "errors": 0,
            "price": 100.0, "user_fair_value": None, "rule": f"regola {cat}", **kw}


def _hist(rows):
    df = pd.DataFrame(rows)
    df["as_of"] = pd.to_datetime(df["as_of"])
    return df


def test_change_message_and_rules_against_noise():
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    h = _hist([_row("AAPL", days[0], vd.WAIT, 0.43), _row("AAPL", days[1], vd.WAIT, 0.45),
               _row("AAPL", days[2], vd.CANDIDATE, 0.61)])
    [(key, msg, data)] = vh.verdict_changes(h, days[2])
    assert key == f"{vd.WAIT}>{vd.CANDIDATE}@2026-06-03"
    assert msg == "giudizio da Aspettare a Candidata all'acquisto (Prezzo 0,45 → 0,61)"
    assert data["from"] == vd.WAIT and data["pillars"]["price"] == [0.45, 0.61]
    assert vh.verdict_changes(h, days[1]) == []                 # prima del cambio: niente
    assert vh.verdict_changes(h, "2026-07-10", days=30) == []   # oltre 30 giorni dal cambio: chiuso
    assert len(vh.verdict_changes(h, "2026-06-20")) == 1         # finché dura il nuovo giudizio resta lo stesso avviso

    # buco di un giorno (prezzo non aggiornato) e poi lo stesso giudizio: niente
    hole = _hist([_row("X", days[0], vd.WAIT), _row("X", days[1], vd.NO_DATA), _row("X", days[2], vd.WAIT)])
    assert vh.verdict_changes(hole, days[2]) == []
    assert vh.verdict_changes(hole, days[1]) == []
    # buco e poi un giudizio diverso: avviso, confrontato con il giudizio di prima del buco
    hole2 = _hist([_row("X", days[0], vd.WAIT), _row("X", days[1], vd.NO_DATA), _row("X", days[2], vd.CANDIDATE)])
    [(key, msg, _)] = vh.verdict_changes(hole2, days[2])
    assert key.startswith(f"{vd.WAIT}>{vd.CANDIDATE}") and "dopo 1 aggiornamento senza dati sufficienti" in msg
    # dati insufficienti da 3 date di fila: avviso (con il motivo); da 2: no
    nd = _hist([_row("X", days[0], vd.WAIT), _row("X", days[1], vd.NO_DATA), _row("X", days[2], vd.NO_DATA),
                _row("X", days[3], vd.NO_DATA, rule="Problemi nei dati (prezzo vecchio)")])
    assert vh.verdict_changes(nd, days[2], nodata_days=3) == []
    [(key, msg, _)] = vh.verdict_changes(nd, days[3], nodata_days=3)
    assert key == f"{vd.WAIT}>{vd.NO_DATA}@{days[1]}" and "da 3 aggiornamenti" in msg and "prezzo vecchio" in msg
    # cambio dovuto a una configurazione / codice diversi: niente
    cfgchg = _hist([_row("X", days[0], vd.WAIT), _row("X", days[1], vd.CANDIDATE, cfg_hash="h2")])
    assert vh.verdict_changes(cfgchg, days[1]) == []
    # allarme nuovo senza pilastri mossi: lo dice il messaggio
    al = _hist([_row("X", days[0], vd.CANDIDATE), _row("X", days[1], vd.AVOID, alarms=1)])
    assert "allarmi gravi 0 → 1" in vh.verdict_changes(al, days[1])[0][1]


def test_vrd_alert_through_engine(cfg, tmp_path):
    from tests.test_alerts import _data

    db = DBManager(tmp_path / "a.db")
    db.add_to_watchlist(["AAA"])
    cfg["alerts"]["codes"] = ["VRD"]
    db.save_verdict_history([_row("AAA", "2026-05-18", vd.WAIT, 0.43), _row("AAA", "2026-05-19", vd.CANDIDATE, 0.61),
                             _row("BBB", "2026-05-18", vd.WAIT), _row("BBB", "2026-05-19", vd.AVOID)],
                            {"run_id": "r", "as_of": "2026-05-19"})
    data = _data()
    new = alerts.evaluate(db, data, cfg, "2026-05-19 18:00")
    assert [(a["ticker"], a["code"]) for a in new] == [("AAA", "VRD")]       # BBB non è seguita
    assert new[0]["message"] == "giudizio da Aspettare a Candidata all'acquisto (Prezzo 0,43 → 0,61)"
    db.save_verdict_history([_row("AAA", "2026-05-20", vd.CANDIDATE, 0.62)], {"run_id": "r2", "as_of": "2026-05-20"})
    assert alerts.evaluate(db, data, cfg, "2026-05-20 18:00") == []          # stesso cambio: non si ripete
    assert len(db.load_alerts(active=True)) == 1
    db.save_verdict_history([_row("AAA", "2026-05-21", vd.WAIT, 0.50)], {"run_id": "r3", "as_of": "2026-05-21"})
    back = alerts.evaluate(db, data, cfg, "2026-05-21 18:00")                # torna indietro: avviso nuovo
    assert [a["key"] for a in back] == [f"{vd.CANDIDATE}>{vd.WAIT}@2026-05-21"]
    assert len(db.load_alerts(active=True)) == 1 and len(db.load_alerts()) == 2
    assert "VRD" in alerts.notification_text(back)


def test_vrd_engine_failure_keeps_active_alerts(cfg, tmp_path, monkeypatch):
    from tests.test_alerts import _data

    db = DBManager(tmp_path / "a.db")
    db.add_to_watchlist(["AAA"])
    cfg["alerts"]["codes"] = ["VRD"]
    db.save_verdict_history([_row("AAA", "2026-05-18", vd.WAIT), _row("AAA", "2026-05-19", vd.CANDIDATE)],
                            {"run_id": "r", "as_of": "2026-05-19"})
    assert len(alerts.evaluate(db, _data(), cfg, "2026-05-19")) == 1

    def boom(*a, **k):
        raise RuntimeError("storico illeggibile")
    monkeypatch.setattr(vh, "change_conditions", boom)
    assert alerts.evaluate(db, _data(), cfg, "2026-05-20") == []
    assert len(db.load_alerts(active=True)) == 1                              # non chiuso per un errore


def test_main_writes_history_before_alerts(cfg, demo_db, demo_data, tmp_path):
    from main import run_verdict_history

    res = run_verdict_history(cfg, demo_db, demo_data, "20261102_180000", tmp_path)
    assert res and res["rows"] > 0
    assert demo_db.verdict_run("20261102_180000")["tickers"] == res["rows"]
    cfg["verdict"] = {"history": {"enabled": False}}
    assert run_verdict_history(cfg, demo_db, demo_data, "x", tmp_path) is None


# ------------------------------------------------------------------------------------------------ misura in avanti
def _fake_history(data, n_dates=80):
    """Giudizi finti a ogni giorno di borsa: il pilastro Prezzo è il rendimento dei 21 giorni DOPO (segnale perfetto)
    solo per controllare che i conti girino; le categorie alternano Candidata / Aspettare."""
    close = data.close.drop(columns=[c for c in (data.benchmark, data.risk_free_ticker) if c in data.close])
    days = close.index[-(n_dates + 30):-30]
    fwd = close.shift(-22) / close.shift(-1) - 1
    rows = []
    for d in days:
        for t in close.columns:
            if not np.isfinite(close.at[d, t]):
                continue
            s = float(fwd.at[d, t])
            rows.append({"as_of": d, "ticker": t, "config_hash": "h", "universe": "sp500",
                         "category": vd.CANDIDATE if s > 0 else vd.WAIT, "profile": "general", "quality": 0.5,
                         "solidity": 0.5, "price_pillar": s, "flags": 0.5, "overall": s, "price": close.at[d, t]})
    return pd.DataFrame(rows)


def test_forward_study_measures_and_warns(demo_data):
    hist = _fake_history(demo_data)
    res = vt.forward_study(hist, demo_data, horizons={1: 21, 3: 63}, min_names=5)
    assert not res.observations.empty and res.params["step_days"] == 1
    assert set(res.categories["category"]) == {vd.CANDIDATE, vd.WAIT}
    one = res.categories[res.categories["months"] == 1].set_index("category")
    assert one.loc[vd.CANDIDATE, "excess_ew"] > 0 > one.loc[vd.WAIT, "excess_ew"]
    ic = res.ic.set_index(["signal", "months"])
    assert ic.loc[("Prezzo", 1), "ic_mean"] > 0.9
    # pochi mesi: l'avvertenza c'è sempre e nessun orizzonte "basta"
    assert not res.enough and any("non dice nulla" in w.lower() or "nessun numero" in w for w in res.warnings)
    assert res.periods.set_index("months").loc[1, "independent_periods"] < 20
    # solo i giudizi abbastanza vecchi
    young = vt.forward_study(hist, demo_data, today=hist["as_of"].min(), min_age_days=21)
    assert young.observations.empty and any("almeno 21 giorni" in w for w in young.warnings)
    assert vt.forward_study(pd.DataFrame(), demo_data).observations.empty


def test_tracking_report_text(demo_data):
    from tools.verdict_tracking import report

    res = vt.forward_study(_fake_history(demo_data), demo_data, horizons={1: 21}, min_names=5)
    text = report(res, {"rows": 10, "dates": 2, "tickers": 5, "first": "2026-01-01", "last": "2026-01-02"})
    assert "Per giudizio" in text and "Per pilastro" in text and "non dice nulla" in text.lower()
