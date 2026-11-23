"""Apertura dei dati dell'app (`DashboardData`) senza Streamlit, per l'API (api/) e per chi altro la vuole.

Stesse regole di ui/common.data()/_load (che passerà a questo modulo dopo il commit della grafica):
- con `demo` vale il database demo della configurazione (`demo_db_path`: mai i dati veri), report in reports/demo;
- chiave = ultima esecuzione di main.py (`runs`) + dati scritti dall'utente + data di modifica del codice dei servizi;
  mentre un aggiornamento è in corso si resta sull'esecuzione precedente (`updater.run_id_before`);
- un solo contesto per chiave, costruito sotto lucchetto (le richieste dell'API arrivano da più thread).
"""
from __future__ import annotations

import threading
from pathlib import Path

from data.db_manager import DBManager
from modules import dashboard_data, updater
from modules.config import PROJECT_ROOT, demo_db_path, load_config
from modules.dashboard_data import DashboardData


def db_path_for(cfg: dict, config_path: str | None, demo: bool) -> Path:
    """Database da aprire: con `demo` quello demo della configurazione (ValueError se punterebbe ai dati veri)."""
    path = demo_db_path(cfg, config_path) if demo else Path(cfg["data"]["db_path"])
    return path if path.is_absolute() else PROJECT_ROOT / path


def code_mtime() -> float:
    """Ultima modifica della facciata `dashboard_data.py` e dei servizi (`modules/services/*.py`)."""
    files = [Path(dashboard_data.__file__), *(Path(dashboard_data.__file__).parent / "services").glob("*.py")]
    return max(f.stat().st_mtime for f in files)


def data_key(db_path: Path, project_root: Path = PROJECT_ROOT) -> str:
    """Versione dei dati: cambia dopo un aggiornamento riuscito o una scrittura dell'utente, non durante."""
    status = updater.read_status(project_root)
    before = updater.run_id_before(status) if status.get("running") else None
    db = DBManager(db_path)
    return f"{db.last_run_id(before) or db_path.stat().st_mtime}|{db.user_data_version()}|{code_mtime()}"


def build(config_path: str | None = None, demo: bool = False) -> DashboardData:
    """Un contesto nuovo (qualche secondo: carica prezzi e bilanci)."""
    cfg = load_config(config_path)
    reports = PROJECT_ROOT / "reports"
    if demo:
        cfg["data"]["db_path"] = str(db_path_for(cfg, config_path, demo=True))
        reports = reports / "demo"
    return DashboardData(cfg, DBManager(cfg["data"]["db_path"]), reports)


class ContextCache:
    """Il contesto della chiave corrente; si ricostruisce solo quando la chiave cambia."""

    def __init__(self, config_path: str | None = None, demo: bool = False):
        self.config_path, self.demo = config_path, demo
        cfg = load_config(config_path)
        self.db_path = db_path_for(cfg, config_path, demo)
        self._lock = threading.Lock()
        self._key: str | None = None
        self._ctx: DashboardData | None = None

    def get(self) -> DashboardData:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database non trovato: {self.db_path} (serve un primo aggiornamento dei dati)")
        key = data_key(self.db_path)
        with self._lock:
            if self._ctx is None or key != self._key:
                self._ctx, self._key = build(self.config_path, self.demo), key
            return self._ctx
