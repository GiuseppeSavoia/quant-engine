"""Lavori lunghi (i backtest delle strategie) in sottofondo, per l'API: un solo thread, un lavoro alla volta, in
ordine di arrivo (sono calcoli pesanti: in parallelo non si guadagna nulla). Un lavoro già in coda o in corso con la
stessa chiave non si ripete (doppio clic, due schede aperte). Qui solo l'impianto: il lavoro scrive i risultati nel
database e chi li chiede li rilegge da lì; lo stato in memoria serve solo a dire "in coda", "in corso", "errore".

Con `inline=True` (test) il lavoro gira subito, nella chiamata a `submit`.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


class JobRunner:
    def __init__(self, inline: bool = False, keep: int = 200):
        self.inline = inline
        self.keep = keep
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}      # chiave -> stato (in ordine di arrivo)
        self._fns: dict[str, Callable[[], object]] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _public(self, job: dict) -> dict:
        out = {k: v for k, v in job.items()}
        if job["state"] == "queued":
            waiting = [k for k, j in self._jobs.items() if j["state"] == "queued"]
            out["position"] = waiting.index(job["key"]) + 1 + any(j["state"] == "running" for j in self._jobs.values())
        else:
            out["position"] = 0
        return out

    def submit(self, key: str, label: str, fn: Callable[[], object]) -> dict:
        """Mette in coda `fn` (se non c'è già un lavoro attivo con la stessa chiave) e ne restituisce lo stato."""
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job["state"] in ACTIVE:
                return self._public(job)
            self._jobs.pop(key, None)
            self._jobs[key] = {"key": key, "label": label, "state": "queued", "queued_at": self._now(),
                               "started_at": None, "finished_at": None, "error": None}
            self._fns[key] = fn
            self._trim()
        if self.inline:
            self._run(key)
        else:
            self._ensure_thread()
            self._queue.put(key)
        return self.status(key)

    def status(self, key: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(key)
            return None if job is None else self._public(job)

    def active(self) -> list[dict]:
        with self._lock:
            return [self._public(j) for j in self._jobs.values() if j["state"] in ACTIVE]

    def _trim(self) -> None:
        done = [k for k, j in self._jobs.items() if j["state"] not in ACTIVE]
        for k in done[: max(0, len(self._jobs) - self.keep)]:
            self._jobs.pop(k, None)

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="lavori", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            key = self._queue.get()
            if key is None:
                return
            self._run(key)

    def _run(self, key: str) -> None:
        with self._lock:
            fn = self._fns.pop(key, None)
            job = self._jobs.get(key)
            if fn is None or job is None:
                return
            job.update(state="running", started_at=self._now())
        try:
            fn()
            state, error = "done", None
        except Exception as exc:  # noqa: BLE001 - l'errore si mostra a chi ha chiesto il lavoro
            logger.exception("Lavoro %s fallito", key)
            state, error = "error", str(exc) or exc.__class__.__name__
        with self._lock:
            job.update(state=state, error=error, finished_at=self._now())

    def shutdown(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
