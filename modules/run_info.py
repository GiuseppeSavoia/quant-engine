"""
Riproducibilità: con quale codice e con quale configurazione è stato prodotto un risultato.

Ogni esecuzione registra nel database (tabella `runs`) il commit git, se c'erano modifiche
non salvate in git, la configurazione completa (e la sua impronta), gli argomenti della riga
di comando e le versioni di Python e delle librerie principali. Con queste informazioni un
report di mesi fa si può rigenerare identico.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime
from importlib import metadata
from pathlib import Path

from modules.config import PROJECT_ROOT

PACKAGES = ["numpy", "pandas", "scipy", "scikit-learn", "yfinance", "requests", "PyYAML"]


def git_state(root: Path = PROJECT_ROOT) -> tuple[str | None, bool | None]:
    """(commit abbreviato, ci sono modifiche non committate). (None, None) fuori da git."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=10, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                                capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit or None, bool(status.strip())


# chiavi che non toccano il motore né i backtest: fuori dall'impronta. `my_portfolio` (confronto del portafoglio
# reale) prima si chiamava `portfolio` e veniva sovrascritta da quella della strategia, quindi non c'era mai stata:
# lasciarla fuori tiene uguali le impronte dei risultati già salvati in `experiments`
# `investor_panel` (tavolo dei grandi investitori, 2026-11-23) è solo informazione nella scheda: non cambia nessun
# calcolo di backtest o giudizio, e con lei nell'impronta nessun risultato salvato si ritroverebbe più
HASH_IGNORED_KEYS = ("my_portfolio", "public_site", "investor_panel")


def config_hash(cfg: dict) -> str:
    """Impronta della configurazione: uguale se e solo se tutti i parametri sono uguali."""
    cfg = {k: v for k, v in cfg.items() if k not in HASH_IGNORED_KEYS}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]


def package_versions() -> dict[str, str | None]:
    out = {"python": platform.python_version()}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def collect(cfg: dict, argv: list[str] | None = None, root: Path = PROJECT_ROOT) -> dict:
    commit, dirty = git_state(root)
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "config_hash": config_hash(cfg),
        "config": cfg,
        "argv": list(argv or []),
        "versions": package_versions(),
    }


def label(info: dict) -> str:
    """Testo breve per i report, es. 'codice 1a2b3c4d5e (+ modifiche) · config 9f8e7d6c5b4a'."""
    commit = info.get("git_commit")
    code = "codice fuori da git" if commit is None else f"codice {commit}" + (" (+ modifiche)" if info.get("git_dirty") else "")
    return f"{code} · config {info.get('config_hash')}"
