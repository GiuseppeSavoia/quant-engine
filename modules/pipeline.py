"""
Aggiornamento a passi con riepilogo (main.py).

Ogni passo ha un nome, un'etichetta in italiano, le dipendenze (`needs`: passi che devono essere riusciti, anche con
avvisi), una durata, un esito e i conteggi chiave:
  riuscito    tutto bene
  con avvisi  finito, ma con qualcosa da guardare (es. prezzi indietro per 3 titoli; errori registrati nel log)
  fallito     eccezione: i passi che ne dipendono risultano "saltato", gli altri vanno avanti
  saltato     non richiesto (es. strategia automatica facoltativa) o bloccato da una dipendenza fallita

Il riepilogo va in `<cartella del database>/logs/aggiornamento_riepilogo.json` (dati simulati:
`aggiornamento_riepilogo_demo.json`, così una prova non copre il riepilogo vero):
  {"last": ultima esecuzione, "last_update": ultima con download (non un backtest lanciato dopo),
   "history": [{run_id, started, seconds, status, headline, warnings, failures, update}, ...] (30, dalla più recente)}
ogni esecuzione = {run_id, args, demo, update, started, finished, seconds, status, headline, fatal, warnings,
failures, steps: [{name, label, needs, status, started, seconds, counts, warnings, log, error, reason}]}. Lo leggono
la webapp (menu "Dati", barra di stato) e l'aggiornamento automatico (notifica del Mac, tramite lo stato dell'updater).

Guasto simulato per i collaudi: variabile d'ambiente QE_FAIL_STEP=<nome del passo> (anche più nomi, separati da
virgole) fa fallire quel passo prima di eseguirlo.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("quant_engine")

OK, WARN, FAILED, SKIPPED = "riuscito", "con avvisi", "fallito", "saltato"
SYMBOL = {OK: "✓", WARN: "⚠", FAILED: "✗", SKIPPED: "–"}
HISTORY = 30            # esecuzioni tenute nello storico breve
MAX_LOG_LINES = 15      # righe di log (avvisi/errori) tenute per passo
MAX_CHARS = 300         # lunghezza massima di un messaggio
# solo i logger del progetto: gli avvisi delle librerie (yfinance, urllib3...) restano nel log completo
PROJECT_LOGGERS = ("quant_engine", "modules", "data", "main", "__main__")


class Skip(Exception):
    """Un passo che decide di non fare nulla (es. funzione non attiva): esito "saltato" con il motivo."""


def summary_path(db_path, demo: bool = False) -> Path:
    """Riepilogo accanto al database (cartella logs/): data/logs/aggiornamento_riepilogo.json per i dati veri."""
    name = "aggiornamento_riepilogo_demo.json" if demo else "aggiornamento_riepilogo.json"
    return Path(db_path).resolve().parent / "logs" / name


def read_summary(path) -> dict:
    """{"last": {...}, "history": [...]} o {} se il file manca o è illeggibile."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _short(text: str, limit: int = MAX_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass
class StepResult:
    name: str
    label: str
    needs: list[str] = field(default_factory=list)
    status: str = OK
    started: str | None = None
    seconds: float = 0.0
    counts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)   # avvisi scelti dal passo (brevi, per l'utente)
    log: list[str] = field(default_factory=list)        # avvisi ed errori registrati nel log durante il passo
    error: str | None = None
    reason: str | None = None                           # perché è stato saltato


class StepReport:
    """Passato alla funzione del passo: conteggi, avvisi, note."""

    def __init__(self, result: StepResult):
        self._r = result

    def count(self, key: str, value) -> None:
        self._r.counts[key] = value

    def warn(self, message: str) -> None:
        self._r.warnings.append(_short(message, 160))

    def skip(self, reason: str):
        raise Skip(reason)


class _Collector(logging.Handler):
    """Raccoglie gli avvisi e gli errori del progetto registrati durante un passo."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] in PROJECT_LOGGERS:
            self.records.append(record)


class Pipeline:
    """Esegue i passi uno alla volta (nell'ordine in cui main.py li chiama) e ne registra l'esito."""

    def __init__(self, run_id: str, args: list[str] | None = None, demo: bool = False,
                 fail_steps: str | None = None):
        self.run_id = run_id
        self.args = list(args or [])
        self.demo = demo
        self.started = datetime.now()
        self.steps: list[StepResult] = []
        self.fatal: str | None = None
        raw = os.environ.get("QE_FAIL_STEP", "") if fail_steps is None else fail_steps
        self.fail_steps = {s.strip() for s in raw.split(",") if s.strip()}

    # ------------------------------------------------------------------ esecuzione
    def result(self, name: str) -> StepResult | None:
        return next((s for s in self.steps if s.name == name), None)

    def ok(self, name: str) -> bool:
        s = self.result(name)
        return s is not None and s.status in (OK, WARN)

    def skipped(self, name: str, label: str, reason: str, needs=()) -> None:
        """Registra un passo non eseguito (non richiesto o non previsto in questa modalità)."""
        self.steps.append(StepResult(name, label, list(needs), status=SKIPPED, reason=reason))

    def step(self, name: str, label: str, fn: Callable[[StepReport], Any], needs=(), default=None):
        """Esegue `fn(report)` e ne restituisce il valore; se fallisce o è bloccato da una dipendenza fallita
        restituisce `default` e il resto dell'aggiornamento continua."""
        res = StepResult(name, label, list(needs), started=datetime.now().isoformat(timespec="seconds"))
        self.steps.append(res)
        blocked = [n for n in needs if not self.ok(n)]
        if blocked:
            res.status = SKIPPED
            res.reason = "dipende da " + ", ".join(
                f"{(self.result(n).label if self.result(n) else n)} ({self.result(n).status if self.result(n) else 'non eseguito'})"
                for n in blocked)
            logger.warning("Passo saltato: %s, %s", label, res.reason)
            return default
        collector = _Collector()
        root = logging.getLogger()
        root.addHandler(collector)
        t0 = time.perf_counter()
        value = default
        try:
            if name in self.fail_steps:
                raise RuntimeError(f"guasto simulato (QE_FAIL_STEP={name})")
            value = fn(StepReport(res))
        except Skip as exc:
            res.status, res.reason = SKIPPED, str(exc)
        except Exception as exc:  # noqa: BLE001 - un passo che fallisce non ferma gli altri
            res.status, res.error = FAILED, _short(f"{type(exc).__name__}: {exc}")
            logger.error("Passo fallito: %s: %s", label, exc, exc_info=logger.isEnabledFor(logging.DEBUG))
            value = default
        finally:
            root.removeHandler(collector)
            res.seconds = round(time.perf_counter() - t0, 2)
        lines = [r for r in collector.records if not r.getMessage().startswith(("Passo fallito:", "Passo saltato:"))]
        res.log = [_short(f"{r.levelname} {r.getMessage()}") for r in lines[:MAX_LOG_LINES]]
        if len(lines) > MAX_LOG_LINES:
            res.log.append(f"… e altre {len(lines) - MAX_LOG_LINES} righe nel log")
        if res.status == OK:
            errors = [r for r in lines if r.levelno >= logging.ERROR]
            if errors and not res.warnings:  # errori gestiti dentro il passo (es. bilanci non scaricati)
                res.warnings.append(_short(f"{len(errors)} errori nel log: {errors[0].getMessage()}", 160))
            if res.warnings:
                res.status = WARN
        return value

    # ------------------------------------------------------------------ riepilogo
    def status(self) -> str:
        if self.fatal or any(s.status == FAILED for s in self.steps):
            return FAILED
        return WARN if any(s.status == WARN for s in self.steps) else OK

    def headline(self) -> str:
        """Una riga per la notifica e la webapp: "Aggiornamento riuscito con 2 avvisi: prezzi indietro per 3
        titoli; …"."""
        failed = [s for s in self.steps if s.status == FAILED]
        warnings = [w for s in self.steps if s.status == WARN for w in s.warnings]
        what = "Aggiornamento" if "--no-update" not in self.args else "Calcolo"
        if self.fatal:
            return f"{what} non riuscito: {self.fatal}"
        parts = []
        if failed:
            parts.append(f"{len(failed)} " + ("passo fallito" if len(failed) == 1 else "passi falliti")
                         + " (" + ", ".join(s.label for s in failed) + ")")
        if warnings:
            parts.append(f"{len(warnings)} " + ("avviso" if len(warnings) == 1 else "avvisi"))
        if not parts:
            return f"{what} riuscito."
        head = f"{what} " + ("riuscito con " if not failed else "con ") + " e ".join(parts)
        return head + (": " + "; ".join(warnings[:3]) + ("…" if len(warnings) > 3 else "") if warnings else ".")

    def summary(self) -> dict:
        finished = datetime.now()
        return {
            "run_id": self.run_id, "args": self.args, "demo": self.demo,
            "update": "--no-update" not in self.args and "--only-backtest" not in self.args,
            "started": self.started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"),
            "seconds": round((finished - self.started).total_seconds(), 1),
            "status": self.status(), "headline": self.headline(), "fatal": self.fatal,
            "warnings": sum(len(s.warnings) for s in self.steps if s.status == WARN),
            "failures": sum(s.status == FAILED for s in self.steps),
            "steps": [asdict(s) for s in self.steps],
        }

    def write(self, path) -> dict:
        """Scrive il riepilogo (ultimo + storico breve) in modo atomico e lo restituisce."""
        summary = self.summary()
        path = Path(path)
        old = read_summary(path)
        brief = {k: summary[k] for k in ("run_id", "started", "seconds", "status", "headline", "warnings",
                                          "failures", "update")}
        history = [brief] + [h for h in old.get("history", []) if h.get("run_id") != self.run_id]
        # ultimo aggiornamento con download (menu "Dati"): un backtest lanciato dopo non lo copre
        last_update = summary if summary["update"] else old.get("last_update")
        data = {"last": summary, "last_update": last_update, "history": history[:HISTORY]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # il riepilogo è un di più: non deve far fallire l'aggiornamento
            logger.warning("Riepilogo dell'aggiornamento non scritto: %s", exc)
        return summary

    def log_summary(self) -> None:
        for s in self.steps:
            extra = s.error or s.reason or "; ".join(s.warnings) or ", ".join(f"{k} {v}" for k, v in s.counts.items())
            logger.info("Passo %s %-34s %6.1f s  %s", SYMBOL[s.status], s.label, s.seconds, extra)
        logger.info("%s", self.headline())


def compact(summary: dict) -> dict:
    """Versione corta del riepilogo per lo stato dell'updater (data/logs/aggiornamento_stato.json)."""
    return {"status": summary.get("status"), "headline": summary.get("headline"),
            "warnings": summary.get("warnings"), "failures": summary.get("failures"),
            "steps": [{"name": s["name"], "label": s["label"], "status": s["status"], "seconds": s["seconds"]}
                      for s in summary.get("steps", [])]}
