"""
Il giudizio funziona su dati NUOVI? (vedi modules/verdict_tracking.py)

    .venv/bin/python tools/verdict_tracking.py [--min-age 21] [--universe sp500|europe|tutti] [--demo]

Legge il database in SOLA LETTURA (connessione `mode=ro`: non crea nemmeno le tabelle) e scrive soltanto in reports/:
verdetto_in_avanti_AAAAMMGG.md (rapporto) e verdetto_in_avanti_AAAAMMGG.csv (ogni giudizio con i rendimenti successivi).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.db_manager import DBManager  # noqa: E402
from modules import verdict_tracking as vt  # noqa: E402
from modules.config import DEMO_DB_PATH, PROJECT_ROOT, load_config  # noqa: E402
from tools.verdict_backtest import label, load_data, md_table, num, pct  # noqa: E402

logger = logging.getLogger("verdict_tracking")


class ReadOnlyDB(DBManager):
    """DBManager che apre il file in sola lettura e non tocca lo schema."""

    def __init__(self, db_path):  # noqa: D107 - niente _init_db: nessuna scrittura
        self.db_path = str(db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()


def report(res: vt.TrackingResult, stats: dict) -> str:
    p = res.params
    lines = ["# Il giudizio su dati nuovi (misura in avanti)", "",
             f"Generato il {datetime.now():%d/%m/%Y %H:%M}. Universo: {p['universe']}. Solo giudizi salvati da almeno "
             f"{p['min_age_days']} giorni prima del {p['today']:%d/%m/%Y}.", ""]
    lines += [f"> {w}" for w in res.warnings] + [""]
    lines += [f"Storico dei giudizi: {stats.get('rows', 0)} righe, {stats.get('dates', 0)} date dei dati "
              f"({stats.get('first') or '–'} → {stats.get('last') or '–'}), {stats.get('tickers', 0)} aziende.", ""]
    if res.observations.empty:
        lines.append("Niente da misurare per ora.")
        return "\n".join(lines) + "\n"
    lines += [f"Misurati: {p['observations']} giudizi su {p['dates']} date ({p['first']} → {p['last']}), passo "
              f"{p['step_days']} giorni di borsa, impronte della configurazione: {p.get('fingerprints', 1)}.", "",
              "## Quanta storia c'è", ""]
    per = res.periods.assign(Orizzonte=lambda x: x["months"].map(lambda m: f"{m} mesi"),
                             Basta=lambda x: x["enough"].map({True: "sì", False: "no"}))
    lines += [md_table(per[["Orizzonte", "obs", "dates", "independent_periods", "Basta"]].rename(columns={
        "obs": "Osservazioni", "dates": "Date", "independent_periods": "Periodi indipendenti"})), ""]
    if not res.categories.empty:
        c = res.categories
        tab = pd.DataFrame({"Giudizio": c["category"].map(label), "Mesi": c["months"], "Oss.": c["obs"],
                            "Date": c["dates"], "Rend. medio": c["mean"].map(lambda x: pct(x, 1, True)),
                            "Eccesso vs pesi uguali": c["excess_ew"].map(lambda x: pct(x, 1, True)),
                            "Eccesso vs SPY": c["excess_spy"].map(lambda x: pct(x, 1, True)),
                            "Batte l'indice": c["beat_ew"].map(lambda x: pct(x, 0)),
                            "t": c["t_excess_ew"].map(num)})
        lines += ["## Per giudizio", "", md_table(tab), ""]
    if not res.ic.empty and "ic_mean" in res.ic:
        ic = res.ic.dropna(subset=["ic_mean"])
        if not ic.empty:
            tab = pd.DataFrame({"Pilastro": ic["signal"], "Mesi": ic["months"], "Date": ic["n_dates"],
                                "IC medio": ic["ic_mean"].map(lambda x: num(x, 3)), "t": ic["ic_t"].map(num),
                                "Spread Q5-Q1": ic["spread"].map(lambda x: pct(x, 1, True))})
            lines += ["## Per pilastro (IC)", "", md_table(tab), ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-age", type=int, default=None, help="giorni minimi dal giudizio (default da config)")
    ap.add_argument("--universe", default="sp500", help="sp500, europe, custom o tutti")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    reports = PROJECT_ROOT / "reports"
    if args.demo:
        cfg["data"]["db_path"] = str(DEMO_DB_PATH)
        reports = reports / "demo"
    db = ReadOnlyDB(cfg["data"]["db_path"])
    ts = vt.tracking_settings(cfg)
    history = db.load_verdict_history(with_rule=False)
    data, _, comp = load_data(cfg, db)
    sectors = comp["sector"].dropna().to_dict() if "sector" in comp else {}
    res = vt.forward_study(history, data, min_age_days=args.min_age if args.min_age is not None else ts["min_age_days"],
                           universe=None if args.universe == "tutti" else args.universe, sectors=sectors,
                           min_independent_periods=int(ts["min_independent_periods"]))
    reports.mkdir(parents=True, exist_ok=True)
    stem = reports / f"verdetto_in_avanti_{datetime.now():%Y%m%d}"
    stem.with_suffix(".md").write_text(report(res, db.verdict_history_stats()), encoding="utf-8")
    if not res.observations.empty:
        res.observations.to_csv(stem.with_suffix(".csv"), index=False)
    logger.info("Rapporto: %s", stem.with_suffix(".md"))
    for w in res.warnings[1:]:
        logger.warning("%s", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
