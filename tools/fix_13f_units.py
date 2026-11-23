"""
Corregge i 13F salvati con l'unità sbagliata (migliaia invece di dollari o viceversa; regola in
modules/superinvestors.unit_factors). Idempotente: dopo la correzione i depositi non risultano più sbagliati.

    .venv/bin/python tools/fix_13f_units.py --dry-run     # sola lettura: elenca cosa cambierebbe
    .venv/bin/python tools/fix_13f_units.py               # backup del database, poi scrive

Le nuove ingestioni si correggono da sole (ThirteenFIngestor.update).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db_manager import DBManager  # noqa: E402
from modules.config import load_config  # noqa: E402
from modules.superinvestors import fix_units  # noqa: E402
from tools.verdict_tracking import ReadOnlyDB  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Corregge l'unità dei valori 13F nel database")
    p.add_argument("--dry-run", action="store_true", help="non scrive nulla: elenca i depositi da correggere")
    p.add_argument("--config", default=None)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config(a.config)
    path = cfg["data"]["db_path"]
    if a.dry_run:
        out = fix_units(ReadOnlyDB(path), dry_run=True)
    else:
        db = DBManager(path)
        db.backup(cfg["data"]["backup_dir"], int(cfg["data"].get("backup_keep", 7)))
        out = fix_units(db)
    if out.empty:
        print("Nessun deposito 13F con l'unità sbagliata.")
        return 0
    print(f"{len(out)} depositi {'da correggere' if a.dry_run else 'corretti'}:")
    for r in out.itertuples():
        print(f"  {r.fund_name:<28} {r.report_date.date()}  {r.accession}  x{r.factor:g}  "
              f"{r.value_before / 1e9:,.3f} -> {r.value_after / 1e9:,.3f} mld $")
    return 0


if __name__ == "__main__":
    sys.exit(main())
