"""Coda dei lavori lunghi (modules/jobs.py): uno alla volta, niente doppioni attivi, errori conservati."""
import threading

from modules.jobs import JobRunner


def test_inline_runs_and_records_errors():
    r = JobRunner(inline=True)
    calls = []
    assert r.submit("a", "A", lambda: calls.append(1))["state"] == "done" and calls == [1]

    def boom():
        raise ValueError("dati mancanti")

    st = r.submit("b", "B", boom)
    assert st["state"] == "error" and st["error"] == "dati mancanti"
    assert r.submit("a", "A", lambda: calls.append(2))["state"] == "done" and calls == [1, 2]  # finito: si può rifare
    assert r.active() == [] and r.status("zzz") is None


def test_single_flight_and_queue_position():
    r = JobRunner()
    gate, started = threading.Event(), threading.Event()
    calls = []

    def slow():
        started.set()
        gate.wait(5)
        calls.append("slow")

    first = r.submit("k1", "uno", slow)
    assert first["state"] in ("queued", "running")
    assert started.wait(5)
    again = r.submit("k1", "uno", lambda: calls.append("doppio"))   # stesso lavoro: non si ripete
    assert again["state"] == "running"
    second = r.submit("k2", "due", lambda: calls.append("k2"))
    assert second["state"] == "queued" and second["position"] == 2
    assert [j["key"] for j in r.active()] == ["k1", "k2"]
    gate.set()
    for _ in range(100):
        if not r.active():
            break
        threading.Event().wait(0.05)
    assert calls == ["slow", "k2"] and r.status("k2")["state"] == "done"
    r.shutdown()
