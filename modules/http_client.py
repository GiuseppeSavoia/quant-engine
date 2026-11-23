"""
Client HTTP condiviso: limite di richieste al secondo, tentativi ripetuti, timeout.

- Limite globale tra tutti i thread (la SEC ammette al massimo 10 richieste/secondo).
- Su errori temporanei (429, 5xx, rete) ritenta con attese crescenti e rispetta Retry-After.
- Su 403 dalla SEC il motivo più comune è un User-Agent mancante o generico.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    pass


class RateLimitedClient:
    def __init__(self, user_agent: str, max_per_second: float = 8.0, retries: int = 4,
                 timeout: float = 30.0, backoff: float = 2.0, session=None):
        import requests

        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, **kwargs):
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
            except Exception as exc:  # errori di rete, timeout
                last_error = exc
                resp = None
            if resp is not None:
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 404:
                    raise HttpError(f"404 non trovato: {url}")
                if resp.status_code == 403:
                    raise HttpError(f"403 accesso negato: {url} (User-Agent con nome ed email impostato?)")
                if resp.status_code not in RETRY_STATUS:
                    raise HttpError(f"HTTP {resp.status_code}: {url}")
                last_error = HttpError(f"HTTP {resp.status_code}")
                retry_after = resp.headers.get("Retry-After")
            else:
                retry_after = None
            if attempt < self.retries:
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff ** attempt
                logger.debug("Tentativo %d fallito per %s (%s): riprovo tra %.1fs", attempt + 1, url, last_error, delay)
                time.sleep(delay)
        raise HttpError(f"Richiesta fallita dopo {self.retries + 1} tentativi: {url} ({last_error})")

    def get_json(self, url: str):
        return self.get(url).json()

    def get_text(self, url: str) -> str:
        return self.get(url).text
