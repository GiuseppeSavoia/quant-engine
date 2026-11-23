"""Aggiornamento a passi (modules/pipeline.py, main.py): esito per passo, dipendenze, riepilogo su file, stato
dell'updater e notifica; strategia automatica facoltativa; pannello "Ultimo aggiornamento" della webapp."""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

import pytest

from modules import pipeline, updater
from modules.pipeline import FAILED, OK, SKIPPED, WARN, Pipeline

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------------------ Pipeline
def test_steps_status_dependencies_and_headline(tmp_path):
    p = Pipeline("20261117_220000", [], fail_steps="")
    assert p.step("a", "Passo A", lambda r: r.count("righe", 3) or 7) == 7
    p.step("b", "Passo B", lambda r: r.warn("prezzi indietro per 3 titoli"))

    def boom(r):
        raise RuntimeError("rete giù")

    assert p.step("c", "Passo C", boom, default=[]) == []
    assert p.step("d", "Passo D", lambda r: 1, needs=("c",), default="x") == "x"   # dipende da un passo fallito
    assert p.step("e", "Passo E", lambda r: 2, needs=("b",)) == 2                   # "con avvisi" basta
    p.step("f", "Passo F", lambda r: r.skip("non richiesto"))
    p.skipped("g", "Passo G", "facoltativo")
    st = {s.name: s for s in p.steps}
    assert [st[n].status for n in "abcdefg"] == [OK, WARN, FAILED, SKIPPED, OK, SKIPPED, SKIPPED]
    assert st["a"].counts == {"righe": 3} and "rete giù" in st["c"].error
    assert "Passo C (fallito)" in st["d"].reason and st["f"].reason == "non richiesto"
    assert p.status() == FAILED
    assert p.headline() == ("Aggiornamento con 1 passo fallito (Passo C) e 1 avviso: prezzi indietro per 3 titoli")

    q = Pipeline("x", [], fail_steps="")
    q.step("a", "A", lambda r: r.warn("uno"))
    q.step("b", "B", lambda r: r.warn("due"))
    assert q.status() == WARN and q.headline() == "Aggiornamento riuscito con 2 avvisi: uno; due"
    ok = Pipeline("y", ["--no-update"], fail_steps="")
    ok.step("a", "A", lambda r: None)
    assert ok.status() == OK and ok.headline() == "Calcolo riuscito."


def test_logged_errors_turn_into_warnings_and_fail_env(monkeypatch):
    log = logging.getLogger("modules.qualcosa")
    p = Pipeline("x", [], fail_steps="")

    def swallowed(r):  # come i passi che registrano un errore e vanno avanti (es. bilanci non scaricati per X)
        log.warning("solo un avviso nel log")
        log.error("Bilanci non scaricati per XYZ: 404")

    p.step("bilanci", "Bilanci", swallowed)
    p.step("quieto", "Quieto", lambda r: logging.getLogger("modules.x").warning("avviso cronico"))
    s = {x.name: x for x in p.steps}
    assert s["bilanci"].status == WARN and "Bilanci non scaricati per XYZ" in s["bilanci"].warnings[0]
    assert len(s["bilanci"].log) == 2
    assert s["quieto"].status == OK and s["quieto"].log == ["WARNING avviso cronico"]  # i WARNING restano dettagli
    logging.getLogger("yfinance").error("libreria esterna")  # fuori dai passi e non del progetto: ignorato
    monkeypatch.setenv("QE_FAIL_STEP", "prezzi, avvisi")
    p2 = Pipeline("z", [])
    p2.step("prezzi", "Prezzi", lambda r: None)
    p2.step("altro", "Altro", lambda r: None)
    assert [x.status for x in p2.steps] == [FAILED, OK] and "guasto simulato" in p2.steps[0].error


def test_summary_file_keeps_last_update_and_history(tmp_path):
    path = pipeline.summary_path(tmp_path / "finance_lake.db")
    assert path == (tmp_path / "logs" / "aggiornamento_riepilogo.json").resolve()
    assert pipeline.summary_path(tmp_path / "demo.db", demo=True).name == "aggiornamento_riepilogo_demo.json"
    assert pipeline.read_summary(path) == {}
    a = Pipeline("20261117_100000", [], fail_steps="")
    a.step("prezzi", "Prezzi", lambda r: r.warn("prezzi indietro per 2 titoli"))
    a.write(path)
    b = Pipeline("20261117_110000", ["--no-update", "--only-backtest"], fail_steps="")
    b.step("backtest", "Backtest", lambda r: None)
    out = b.write(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["last"]["run_id"] == "20261117_110000" and out["update"] is False
    assert data["last_update"]["run_id"] == "20261117_100000"          # il backtest non copre l'aggiornamento
    assert data["last_update"]["status"] == WARN and data["last_update"]["warnings"] == 1
    assert [h["run_id"] for h in data["history"]] == ["20261117_110000", "20261117_100000"]
    step = data["last_update"]["steps"][0]
    assert {"name", "label", "needs", "status", "seconds", "counts", "warnings", "log", "error", "reason"} <= set(step)
    short = pipeline.compact(data["last_update"])
    assert short["headline"].startswith("Aggiornamento riuscito con 1 avviso") and short["steps"][0]["name"] == "prezzi"


def test_notification_uses_step_summary(monkeypatch):
    monkeypatch.setattr(updater, "summary", lambda cfg, since=None: "Prezzi al 16/11/2026.")
    status = {"started": "2026-11-17T22:00:00",
              "summary": {"headline": "Aggiornamento riuscito con 2 avvisi: prezzi indietro per 3 titoli (A, B, C)"}}
    assert updater.notification({}, status, 0) == ("Aggiornamento riuscito con 2 avvisi: prezzi indietro per 3 "
                                                  "titoli (A, B, C) Prezzi al 16/11/2026.")
    failed = {"summary": {"headline": "Aggiornamento con 1 passo fallito (Prezzi e split)."}}
    assert updater.notification({}, failed, 2).startswith("Aggiornamento con 1 passo fallito (Prezzi e split).")
    assert updater.notification({}, {}, 3) == "Aggiornamento non riuscito (codice 3). Vedi data/logs/aggiornamento.log"
    assert updater.notification({}, {}, 0) == "Prezzi al 16/11/2026."


# ------------------------------------------------------------------------------------------------ main.py
@pytest.fixture
def demo_project(tmp_path, monkeypatch):
    import main as entry
    from modules import config as config_mod

    monkeypatch.setattr(entry, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    return entry, tmp_path


def _summary(root: Path) -> dict:
    return json.loads((root / "data" / "logs" / "aggiornamento_riepilogo_demo.json").read_text(encoding="utf-8"))


def test_main_failed_step_does_not_stop_the_others(demo_project, monkeypatch):
    entry, root = demo_project
    monkeypatch.setenv("QE_FAIL_STEP", "giudizi")
    assert entry.main(["--demo"]) == 0
    last = _summary(root)["last"]
    steps = {s["name"]: s for s in last["steps"]}
    assert steps["giudizi"]["status"] == FAILED and "guasto simulato" in steps["giudizi"]["error"]
    assert steps["avvisi"]["status"] in (OK, WARN)            # dopo lo storico dei giudizi, ma non ne dipende
    assert steps["qualita"]["status"] in (OK, WARN) and steps["dati"]["status"] == OK
    assert steps["strategia"]["status"] == SKIPPED and "run_on_update" in steps["strategia"]["reason"]
    assert steps["sito_pubblico"]["status"] == SKIPPED
    assert last["status"] == FAILED and "Storico dei giudizi" in last["headline"]
    reports = root / "reports" / "demo"
    assert list(reports.glob("qualita_dati_*.csv"))         # il controllo di qualità non dipende più dalla strategia
    assert not list(reports.glob("analisi_*.html"))          # strategia automatica non eseguita

    # i prezzi che falliscono: l'analisi va avanti sui dati di ieri, codice 2 (l'aggiornamento automatico riprova)
    monkeypatch.setenv("QE_FAIL_STEP", "prezzi")
    assert entry.main(["--demo"]) == 2
    steps = {s["name"]: s for s in _summary(root)["last"]["steps"]}
    assert steps["prezzi"]["status"] == FAILED and steps["giudizi"]["status"] == OK

    # senza dati non si può fare nulla: codice 1, i passi che ne dipendono saltati, riepilogo scritto lo stesso
    monkeypatch.setenv("QE_FAIL_STEP", "dati")
    assert entry.main(["--demo", "--no-update"]) == 1
    data = _summary(root)
    assert data["last"]["status"] == FAILED and data["last"]["fatal"]
    assert data["last_update"]["run_id"] != data["last"]["run_id"]  # --no-update non è un aggiornamento
    assert len(data["history"]) == 3


def test_main_strategy_is_optional(demo_project, monkeypatch):
    from data.db_manager import DBManager

    entry, root = demo_project
    monkeypatch.delenv("QE_FAIL_STEP", raising=False)
    assert entry.main(["--demo"]) == 0
    db = DBManager(root / "data" / "demo.db")
    assert db.latest_run_id() is None                         # nessuna decisione senza --strategy
    first_run = db.last_run_id()
    assert first_run is not None                              # la chiave di cache della webapp avanza lo stesso
    assert entry.main(["--demo", "--no-update", "--strategy"]) == 0
    assert db.latest_run_id() == db.last_run_id() and db.last_run_id() > first_run
    steps = {s["name"]: s for s in _summary(root)["last"]["steps"]}
    assert steps["strategia"]["status"] == OK and "ordini simulati" in steps["strategia"]["counts"]
    assert list((root / "reports" / "demo").glob("analisi_*.html"))


# ------------------------------------------------------------------------------------------------ webapp
DEMO_DB = ROOT / "data" / "demo.db"


@pytest.mark.skipif(not DEMO_DB.exists(), reason="manca data/demo.db (python main.py --demo)")
def test_data_menu_and_status_bar_show_last_update(tmp_path, monkeypatch):
    import socket

    from streamlit.testing.v1 import AppTest

    from modules import quotes

    db = tmp_path / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp_path / 'facts.db'}\n", encoding="utf-8")
    p = Pipeline("20261117_223000", ["--demo"], demo=True, fail_steps="")
    p.step("prezzi", "Prezzi e split", lambda r: r.warn("prezzi indietro per 3 titoli dell'indice (A, B, C)"))
    p.step("giudizi", "Storico dei giudizi", lambda r: None)
    p.write(pipeline.summary_path(db, demo=True))

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    monkeypatch.setattr(sys, "argv", ["app.py", "--config", str(config)])
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(quotes, "download_closes", no_network)
    monkeypatch.setattr(quotes.QuoteFeed, "start", lambda self: self)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    texts = " ".join([*(c.value for c in at.caption), *(h.proto.body for h in at.get("html"))])
    assert "Ultimo aggiornamento" in texts
    assert "prezzi indietro per 3 titoli" in texts            # riga di sintesi nel menu "Dati"
    assert "agg. " in texts and "1 avviso" in texts           # barra di stato
    assert "Storico dei giudizi" in texts                     # tabella dei passi


def test_main_writes_summary_into_updater_status(demo_project, monkeypatch, tmp_path):
    """Lanciato dall'updater (pulsante o aggiornamento automatico), main.py mette il riepilogo corto nello stato: la
    notifica del Mac lo legge da lì."""
    import os

    entry, _ = demo_project
    store = {"pid": 4242, "started": "2026-11-17T22:00:00", "finished": None}
    monkeypatch.setattr(updater, "read_status", lambda root=None: dict(store))
    monkeypatch.setattr(updater, "_write_status", lambda root, status: store.update(status))
    monkeypatch.setattr(os, "getppid", lambda: 4242)
    monkeypatch.setenv("QE_FAIL_STEP", "avvisi")
    assert entry.main(["--demo"]) == 0
    short = store["summary"]
    assert short["status"] == FAILED and short["headline"].startswith("Aggiornamento con 1 passo fallito (Avvisi")
    assert {"prezzi", "giudizi", "avvisi"} <= {s["name"] for s in short["steps"]}
    assert updater.notification({}, store, 0).startswith(short["headline"])


def test_due_esecuzioni_nello_stesso_secondo_hanno_identificativi_diversi(demo_project, monkeypatch):
    """Orologio fermo: il secondo main.py aspetta il secondo successivo invece di riusare l'identificativo."""
    import types
    from datetime import datetime, timedelta

    entry, root = demo_project
    clock = {"now": datetime(2026, 11, 22, 15, 0, 0, 500_000)}

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003
            return clock["now"]

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    monkeypatch.setattr(entry, "datetime", FrozenDatetime)
    monkeypatch.setattr(entry, "time", types.SimpleNamespace(sleep=sleep))
    entry.main(["--demo"])
    entry.main(["--demo", "--no-update"])  # stesso secondo dell'orologio fermo
    runs = [s["run_id"] for s in _summary(root)["history"]]
    assert sorted(runs) == ["20261122_150000", "20261122_150001"]
