"""Test dell'aggiornamento lanciato dalla webapp (con un main.py finto, niente rete)."""
import sys
import time

from data.db_manager import DBManager
from modules import updater


def _fake_project(tmp_path, body: str):
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "__init__.py").write_text("")
    (tmp_path / "modules" / "updater.py").write_text(open(updater.__file__, encoding="utf-8").read())
    (tmp_path / "main.py").write_text(body)
    return tmp_path


def _wait(root, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline and updater.is_running(root):
        time.sleep(0.1)


def test_update_runs_main_and_records_result(tmp_path):
    root = _fake_project(tmp_path, "import sys, time\nprint('argomenti', sys.argv[1:])\ntime.sleep(1)\n")
    status = updater.start(["--demo", "--backtest"], root, python=sys.executable)
    assert status["running"] and updater.is_running(root)
    try:
        updater.start([], root, python=sys.executable)
        raise AssertionError("doveva rifiutare un secondo aggiornamento")
    except RuntimeError:
        pass
    _wait(root)
    status = updater.read_status(root)
    assert not status["running"] and status["returncode"] == 0 and status["finished"]
    assert any("['--demo', '--backtest']" in line for line in updater.log_tail(root))


def test_failed_and_interrupted_updates(tmp_path):
    root = _fake_project(tmp_path, "raise SystemExit(3)\n")
    updater.start([], root, python=sys.executable)
    _wait(root)
    assert updater.read_status(root)["returncode"] == 3
    updater._write_status(root, {"pid": 999999, "started": "2026-01-01T10:00:00", "finished": None})
    status = updater.read_status(root)
    assert status["interrupted"] and not status["running"]   # processo sparito senza registrare la fine


def test_cache_key_ignores_run_in_progress(tmp_path):
    db = DBManager(tmp_path / "t.db")
    for run_id in ("20261101_090000", "20261102_100000"):
        db.log_run(run_id, {})
    status = {"started": "2026-11-02T10:00:00"}
    assert db.last_run_id() == "20261102_100000"
    assert db.last_run_id(updater.run_id_before(status)) == "20261101_090000"


def test_progress_line_is_short(tmp_path):
    log = tmp_path / "data" / "logs" / "aggiornamento.log"
    log.parent.mkdir(parents=True)
    log.write_text("12:00:01 INFO    Download bilanci per 629 ticker...\n"
                   "12:00:05 INFO    Bilanci: 50/629 (17s)\n\n")
    assert updater.progress_line(tmp_path) == "Bilanci: 50/629 (17s)"
    log.write_text("12:00:01 INFO    " + "x" * 500 + "\n")
    assert len(updater.progress_line(tmp_path)) == 110
