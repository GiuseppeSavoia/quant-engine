"""
Aggiornamento dei dati lanciato dalla webapp: esegue `main.py` in un processo separato.

La webapp resta in sola lettura: a scrivere nel database è sempre main.py, come da terminale.
Il processo è staccato dalla webapp (continua anche se la finestra si chiude) e lascia:
  data/logs/aggiornamento.log          tutto l'output di main.py
  data/logs/aggiornamento_stato.json   pid, comando, inizio, fine, codice di uscita, `summary` = esito dei passi
                                       (scritto da main.py, versione corta di aggiornamento_riepilogo.json)
Un solo aggiornamento alla volta.

    python -m modules.updater --run [argomenti di main.py]    (uso interno: avvolge main.py)
    python -m modules.updater --scheduled                       (aggiornamento automatico, da launchd)

Aggiornamento automatico (`scheduled`): launchd lo avvia ogni ora (e all'accensione); parte solo se dopo l'ultima
chiusura di Wall Street (16:00 a New York + `schedule.after_close_minutes`) non c'è già stato un aggiornamento
riuscito, quindi una volta al giorno, e recupera appena il Mac si riaccende se a quell'ora era spento.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path) -> tuple[Path, Path]:
    logs = root / "data" / "logs"
    return logs / "aggiornamento_stato.json", logs / "aggiornamento.log"


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_status(root: Path = ROOT) -> dict:
    """Stato dell'ultimo aggiornamento: {} se mai fatto. `running` è vero solo se il processo è vivo;
    un processo morto senza scrivere la fine (es. computer spento) risulta interrotto."""
    status_path, _ = _paths(root)
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    status["running"] = status.get("finished") is None and _alive(status.get("pid"))
    if status.get("finished") is None and not status["running"]:
        status["interrupted"] = True
    return status


def run_id_before(status: dict) -> str | None:
    """Gli identificativi delle esecuzioni sono date (AAAAMMGG_hhmmss): quelle precedenti all'aggiornamento
    in corso sono complete, quella che sta scrivendo no."""
    started = status.get("started")
    return datetime.fromisoformat(started).strftime("%Y%m%d_%H%M%S") if started else None


def is_running(root: Path = ROOT) -> bool:
    return read_status(root).get("running", False)


def log_tail(root: Path = ROOT, lines: int = 8) -> list[str]:
    _, log_path = _paths(root)
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-lines:]]
    except OSError:
        return []


def progress_line(root: Path = ROOT, max_chars: int = 110) -> str:
    """Ultima riga del log senza ora e livello, accorciata (es. "Bilanci: 300/629 (100s)")."""
    for line in reversed(log_tail(root, 5)):
        parts = line.split(maxsplit=2)
        text = parts[2] if len(parts) == 3 and parts[0].count(":") == 2 else line.strip()
        if text:
            return text if len(text) <= max_chars else text[:max_chars - 1] + "…"
    return ""


def _write_status(root: Path, status: dict) -> None:
    status_path, _ = _paths(root)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in status.items() if k not in ("running", "interrupted")},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(status_path)  # scrittura atomica: la webapp non legge mai un file a metà


def start(main_args: list[str], root: Path = ROOT, python: str | None = None) -> dict:
    """Avvia main.py con `main_args` in un processo staccato. Errore se un aggiornamento è già in corso."""
    if is_running(root):
        raise RuntimeError("Un aggiornamento è già in corso")
    _, log_path = _paths(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen([python or sys.executable, "-m", "modules.updater", "--run", *main_args],
                                cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    status = {"pid": proc.pid, "args": main_args, "started": datetime.now().isoformat(timespec="seconds"),
              "finished": None, "returncode": None}
    _write_status(root, status)
    return read_status(root)


def _run(main_args: list[str], root: Path = ROOT) -> int:
    """Dentro il processo staccato: esegue main.py e registra la fine."""
    rc = subprocess.call([sys.executable, str(root / "main.py"), *main_args], cwd=root)
    for _ in range(50):  # se main.py fallisce subito, lo stato iniziale potrebbe non essere ancora scritto
        if read_status(root).get("started"):
            break
        time.sleep(0.1)
    status = read_status(root)
    status.update(pid=os.getpid(), finished=datetime.now().isoformat(timespec="seconds"), returncode=rc)
    _write_status(root, status)
    return rc


NY = "America/New_York"


def close_time(day, after_minutes: int) -> pd.Timestamp:
    """Chiusura di Wall Street del giorno `day` (16:00 a New York, ora legale compresa) + `after_minutes`."""
    return pd.Timestamp(datetime.combine(day, dtime(16))).tz_localize(NY) + pd.Timedelta(minutes=after_minutes)


def last_close(now: pd.Timestamp, after_minutes: int) -> pd.Timestamp:
    """Ultima chiusura di Wall Street (giorno feriale) + `after_minutes` prima di `now` (con fuso orario).
    I festivi di borsa non si distinguono: quel giorno l'aggiornamento non trova prezzi nuovi."""
    day = now.tz_convert(NY).date()
    while True:
        close = close_time(day, after_minutes)
        if day.weekday() < 5 and close <= now:
            return close
        day -= timedelta(days=1)


def due(cfg: dict, root: Path = ROOT, now: pd.Timestamp | None = None) -> tuple[bool, str]:
    """(serve un aggiornamento automatico?, perché)."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    close = last_close(now, int(cfg.get("schedule", {}).get("after_close_minutes", 30)))
    local_tz = datetime.now().astimezone().tzinfo
    close_local = close.tz_convert(local_tz)
    status = read_status(root)
    if status.get("running"):
        return False, "un aggiornamento è già in corso"
    args = status.get("args") or []
    counts = status.get("returncode") == 0 and not {"--demo", "--no-update", "--only-backtest"} & set(args)
    if counts and status.get("finished"):
        finished = pd.Timestamp(status["finished"]).tz_localize(local_tz)
        if finished >= close_local:
            return False, f"dati già aggiornati dopo la chiusura del {close_local:%d/%m %H:%M}"
    return True, f"ultima chiusura {close_local:%d/%m %H:%M} (ora locale)"


def _notify(text: str) -> None:
    """Notifica del Mac (Centro notifiche); altrove non fa nulla."""
    if sys.platform != "darwin":
        return
    safe = text.replace("\\", "").replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "QUANT_ENGINE"'],
                   capture_output=True, timeout=10, check=False)


def scheduled(root: Path = ROOT) -> int:
    """Aggiornamento automatico: main.py in primo piano (launchd aspetta la fine), se serve."""
    from modules.config import load_config

    cfg = load_config()
    ok, why = due(cfg, root)
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    if not ok:
        print(f"{stamp} nessun aggiornamento: {why}", flush=True)
        return 0
    print(f"{stamp} aggiornamento automatico ({why})", flush=True)
    main_args = [str(a) for a in cfg.get("schedule", {}).get("args") or []]
    _, log_path = _paths(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _write_status(root, {"pid": os.getpid(), "args": main_args, "started": datetime.now().isoformat(timespec="seconds"),
                         "finished": None, "returncode": None, "scheduled": True})
    with open(log_path, "w", encoding="utf-8") as log:
        rc = subprocess.call([sys.executable, str(root / "main.py"), *main_args], cwd=root, stdout=log,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    status = read_status(root)
    status.update(finished=datetime.now().isoformat(timespec="seconds"), returncode=rc)
    _write_status(root, status)
    print(f"{datetime.now():%Y-%m-%d %H:%M} fine, codice {rc}", flush=True)
    _notify(notification(cfg, status, rc))
    return rc


def notification(cfg: dict, status: dict, rc: int) -> str:
    """Testo della notifica: l'esito dei passi scritto da main.py nello stato (modules/pipeline.py: "Aggiornamento
    riuscito con 2 avvisi: prezzi indietro per 3 titoli…") e, se è andato a buon fine, prezzi, bilanci e avvisi
    nuovi."""
    head = ((status or {}).get("summary") or {}).get("headline") or ""
    if rc != 0:
        return (head or f"Aggiornamento non riuscito (codice {rc}).") + " Vedi data/logs/aggiornamento.log"
    rest = summary(cfg, since=(status or {}).get("started"))
    return f"{head} {rest}".strip() if head else rest


def summary(cfg: dict, since: str | None = None) -> str:
    """Una riga per la notifica: data degli ultimi prezzi, bilanci nuovi caricati oggi e avvisi nuovi registrati da
    `since` (inizio dell'aggiornamento; default oggi)."""
    from data.db_manager import DBManager

    try:
        db = DBManager(cfg["data"]["db_path"])
        last = max(db.last_price_dates().values(), default=None)
        loaded = db.load_filings(since=(datetime.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d"))
        today = loaded[(loaded["status"] == "caricato") & loaded["loaded_at"].fillna("").str.startswith(
            datetime.now().strftime("%Y-%m-%d"))]
    except Exception:  # noqa: BLE001 - la notifica non deve far fallire l'aggiornamento
        return "Dati aggiornati."
    text = f"Prezzi al {last:%d/%m/%Y}." if last is not None else "Dati aggiornati."
    if not today.empty:
        names = ", ".join(sorted(today["ticker"].unique())[:8])
        text += f" Bilanci nuovi: {names}" + ("…" if today["ticker"].nunique() > 8 else "")
    try:
        from modules.alerts import notification_text

        new = db.load_alerts(since=since or datetime.now().strftime("%Y-%m-%d"))
        if not new.empty:
            new = new.iloc[::-1]  # dal più vecchio, nell'ordine di valutazione; i cambi di giudizio (VRD) per primi
            new = pd.concat([new[new["code"] == "VRD"], new[new["code"] != "VRD"]])
            text += " " + notification_text(new)
    except Exception:  # noqa: BLE001 - la notifica non deve far fallire l'aggiornamento
        pass
    return text


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--run":
        sys.exit(_run(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--scheduled":
        sys.exit(scheduled())
    print(__doc__)
