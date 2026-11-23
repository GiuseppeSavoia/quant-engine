"""
Aggiornamento automatico dei dati sul Mac (launchd): ogni sera dopo la chiusura di Wall Street prezzi di
chiusura, 10-Q/10-K appena depositati, insider e 13F, analisi.

    .venv/bin/python tools/schedule.py attiva       installa e avvia
    .venv/bin/python tools/schedule.py disattiva    ferma e rimuove
    .venv/bin/python tools/schedule.py stato        attivo o no, ultime righe del registro
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import scheduler, updater  # noqa: E402
from modules.config import load_config  # noqa: E402


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "stato"
    cfg = load_config()
    if cmd == "attiva":
        path = scheduler.install(cfg)
        print(f"Aggiornamento automatico attivo ({path}).")
    elif cmd == "disattiva":
        print("Aggiornamento automatico disattivato." if scheduler.uninstall() else "Non era attivo.")
    elif cmd == "stato":
        print("Attivo." if scheduler.installed() else "Non attivo.")
        ok, why = updater.due(cfg)
        print(("Al prossimo controllo aggiorna: " if ok else "Al prossimo controllo non aggiorna: ") + why)
        print("\n".join(scheduler.log_tail(lines=8)) or "(registro vuoto)")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
