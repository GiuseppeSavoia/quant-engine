"""
Il codice in memoria è ancora quello dei file? Streamlit riesegue solo app.py: i moduli già importati (modules/, ui/,
data/) restano quelli dell'avvio finché il server non riparte. Questo modulo annota, quando viene importato la prima
volta (all'avvio del server), la data di modifica di ogni file .py dell'app; `changed()` dice quali sono cambiati da
allora, così l'app può chiedere di essere riavviata invece di mescolare codice vecchio e nuovo.
"""
from __future__ import annotations

import time
from pathlib import Path

from modules.config import PROJECT_ROOT

FOLDERS = ("modules", "ui")          # cercati in profondità
TOP_FILES = ("app.py", "desktop.py", "main.py")
FLAT_FOLDERS = ("data",)             # solo i .py in cima (data/ contiene anche archivi e copie grandi)


def snapshot(root: Path = PROJECT_ROOT) -> dict[str, float]:
    """Percorso relativo -> data di modifica di ogni file .py dell'app."""
    files = [root / f for f in TOP_FILES]
    for folder in FOLDERS:
        files += (root / folder).rglob("*.py")
    for folder in FLAT_FOLDERS:
        files += (root / folder).glob("*.py")
    out = {}
    for f in files:
        try:
            out[str(f.relative_to(root))] = f.stat().st_mtime
        except OSError:
            continue
    return out


STARTED = snapshot()
_last: tuple[float, list[str]] = (0.0, [])


def changed(root: Path = PROJECT_ROOT, start: dict[str, float] | None = None, every: float = 5.0) -> list[str]:
    """File .py modificati, aggiunti o tolti dopo l'avvio (ordinati). Ricontrolla al massimo ogni `every` secondi."""
    global _last
    now = time.monotonic()
    if start is None and now - _last[0] < every:
        return _last[1]
    base = STARTED if start is None else start
    cur = snapshot(root)
    out = sorted(p for p in set(base) | set(cur) if base.get(p) != cur.get(p))
    if start is None:
        _last = (now, out)
    return out
