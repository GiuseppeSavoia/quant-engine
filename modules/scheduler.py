"""
Aggiornamento automatico sul Mac con launchd (il pianificatore di macOS): un "LaunchAgent" dell'utente avvia
`python -m modules.updater --scheduled` ogni `schedule.check_every_minutes` minuti e all'accesso. Il
programma decide da solo se serve aggiornare (una volta per ogni chiusura di Wall Street, vedi
`updater.due`), quindi i controlli in più costano solo l'avvio di Python.

Si attiva e disattiva dalla barra laterale della webapp o con `python tools/schedule.py [attiva|disattiva|stato]`.
File: ~/Library/LaunchAgents/com.quantengine.aggiornamento.plist; registro in data/logs/pianificato.log.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.quantengine.aggiornamento"
AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def python_path(root: Path = ROOT) -> str:
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def agent(cfg: dict, root: Path = ROOT, python: str | None = None) -> dict:
    """Contenuto del file .plist del LaunchAgent."""
    every = int(cfg.get("schedule", {}).get("check_every_minutes", 60))
    log = str(root / "data" / "logs" / "pianificato.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [python or python_path(root), "-m", "modules.updater", "--scheduled"],
        "WorkingDirectory": str(root),
        "StartInterval": every * 60,
        "RunAtLoad": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(cfg: dict, root: Path = ROOT, path: Path = AGENT_PATH, python: str | None = None,
            load: bool = True) -> Path:
    """Scrive il LaunchAgent e lo carica (se c'era già, lo sostituisce)."""
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if load and path.exists():
        _launchctl("bootout", f"gui/{os.getuid()}", str(path))
    with open(path, "wb") as fh:
        plistlib.dump(agent(cfg, root, python), fh)
    if load:
        res = _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        if res.returncode != 0:
            raise RuntimeError(f"launchctl non ha caricato l'aggiornamento automatico: {res.stderr.strip()}")
    return path


def uninstall(path: Path = AGENT_PATH, unload: bool = True) -> bool:
    """Ferma e rimuove il LaunchAgent. False se non c'era."""
    if not path.exists():
        return False
    if unload:
        _launchctl("bootout", f"gui/{os.getuid()}", str(path))
    path.unlink()
    return True


def installed(path: Path = AGENT_PATH) -> bool:
    return path.exists()


def log_tail(root: Path = ROOT, lines: int = 5) -> list[str]:
    try:
        with open(root / "data" / "logs" / "pianificato.log", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in fh.readlines()[-lines:]]
    except OSError:
        return []
