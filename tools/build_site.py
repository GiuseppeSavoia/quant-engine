"""
Genera il sito pubblico statico (modules/public_site.py) dal database, aperto in SOLA LETTURA.

    .venv/bin/python tools/build_site.py                      # dati veri -> build/sito
    .venv/bin/python tools/build_site.py --demo               # demo con dati d'esempio -> build/sito-demo
    .venv/bin/python tools/build_site.py --out build/prova --config altra.yaml [--json contesto.json]

Con --demo si lavora su una COPIA temporanea del database demo, in cui si aggiungono un portafoglio, un diario, note,
due fondi 13F dimostrativi e qualche acquisto di insider (dati inventati, con la scritta "Dati simulati" sul sito):
il database demo non cambia. Guardarlo: .venv/bin/python -m http.server 8551 --directory build/sito-demo
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.db_manager import DBManager  # noqa: E402
from modules import public_site  # noqa: E402
from modules.config import PROJECT_ROOT, demo_db_path, load_config  # noqa: E402

logger = logging.getLogger("build_site")


def _close(db: DBManager, ticker: str, day) -> float:
    with db._conn() as conn:
        row = conn.execute("SELECT close FROM price_history WHERE ticker = ? AND date >= ? ORDER BY date LIMIT 1",
                           (ticker, pd.Timestamp(day).strftime("%Y-%m-%d"))).fetchone()
    return round(float(row[0]), 2) if row else 100.0


def seed_demo(db: DBManager) -> None:
    """Dati dell'utente d'esempio in un database demo (da usare SOLO su una copia)."""
    with db._conn() as conn:
        last = pd.Timestamp(conn.execute("SELECT MAX(date) FROM price_history WHERE ticker = 'SPY'").fetchone()[0])
        days = [r[0] for r in conn.execute("SELECT date FROM price_history WHERE ticker = 'SPY' ORDER BY date")]
        # cambio EURUSD simulato (la demo non ne ha): serve ai conti in euro del portafoglio
        rows = [("EURUSD=X", dd, 1.08 + 0.04 * np.sin(i / 90), 1.08 + 0.04 * np.sin(i / 90))
                for i, dd in enumerate(days)]
        conn.executemany("INSERT OR REPLACE INTO price_history (ticker, date, close, adj_close) VALUES (?, ?, ?, ?)",
                         rows) if _has_adj(conn) else conn.executemany(
            "INSERT OR REPLACE INTO price_history (ticker, date, close) VALUES (?, ?, ?)", [r[:3] for r in rows])
    d = lambda months: (last - pd.DateOffset(months=months)).strftime("%Y-%m-%d")  # noqa: E731
    db.add_to_watchlist(["AAPL", "MSFT", "NVDA", "D004", "D015"])
    trades = [("AAPL", "BUY", 20, 30, 0), ("MSFT", "BUY", 18, 15, 0), ("D004", "BUY", 14, 40, 0),
              ("AAPL", "BUY", 9, 10, 0), ("D004", "SELL", 5, 20, 0)]
    ids = {}
    for t, kind, months, qty, _ in trades:
        ids[(t, kind, months)] = db.add_transaction({"ticker": t, "kind": kind, "date": d(months), "quantity": qty,
                                                     "price": _close(db, t, d(months)), "currency": "USD",
                                                     "fx": None, "fees": 4.0, "note": ""})
    db.add_transaction({"ticker": "MSFT", "kind": "DIV", "date": d(3), "quantity": 15, "price": 0.83,
                        "currency": "USD", "fx": None, "fees": 0, "note": ""})
    decisions = [
        ("AAPL", 20, "BUY", "Qualità alta e costante, ritorno sul capitale sopra il 25% da anni; prezzo nella media.",
         "Margine operativo sotto il 25% per due trimestri o riacquisti fermi."),
        ("MSFT", 18, "BUY", "Ricavi ricorrenti in crescita e debito basso.", "Crescita dei ricavi sotto il 5%."),
        ("D004", 14, "BUY", "Prezzo basso rispetto al flusso di cassa.", "Se il flusso di cassa cala per due anni."),
        ("NVDA", 12, "PASS", "Ottima azienda ma prezzo che sconta una crescita del 30% l'anno per dieci anni.",
         "Un prezzo che sconti meno del 15% di crescita."),
        ("D004", 5, "SELL", "Il flusso di cassa è calato: la tesi non regge più.", "—"),
        ("D015", 1, "WATCH", "Bilanci solidi, aspetto un prezzo più basso.", "Un prezzo più basso del 15%."),
    ]
    for t, months, action, reason, change in decisions:
        db.add_decision({"ticker": t, "date": d(months), "action": action, "price": _close(db, t, d(months)),
                         "reason": reason, "change_mind": change, "review_date": d(months - 6) if months > 6 else None})
    db.save_note("AAPL", thesis="Ecosistema chiuso, clienti fedeli, servizi in crescita.\nRischio: regolatori.",
                 fair_value=_close(db, "AAPL", d(0)) * 1.1)
    db.save_note("MSFT", thesis="Cloud e software per le aziende: ricavi ricorrenti.", fair_value=None)
    db.save_research("AAPL", {"one_liner": "Ricavi ricorrenti dai servizi sopra una base di clienti fedele.",
                              "story": "La crescita viene dai servizi; l'hardware resta il punto d'ingresso.",
                              "catalysts": "Nuovi prodotti; riacquisti.", "risks": "Antitrust; dipendenza dalla Cina.",
                              "change_mind": "Margine dei servizi in calo per due anni.", "review_date": d(-3)})
    # due fondi 13F dimostrativi (nomi inventati: mai portafogli finti sotto nomi veri)
    rows = []
    for cik, name, picks in ((9000001, "Fondo dimostrativo A", ["AAPL", "MSFT", "NVDA", "D004", "D010"]),
                             (9000002, "Fondo dimostrativo B", ["MSFT", "D015", "D020", "D025"])):
        for q, (report, filed) in enumerate([(d(10), d(8)), (d(7), d(5)), (d(4), d(2))]):
            held = picks[: 3 + q] if q < 2 else picks[1:]
            for i, t in enumerate(held):
                rows.append({"fund_cik": cik, "fund_name": name, "accession": f"{cik}-{q}", "report_date": report,
                             "filed": filed, "issuer": f"Demo {t}", "cusip": f"C{t}", "title": "COM",
                             "value": 1e8 * (i + 1) * (1 + 0.2 * q), "shares": 1e6 * (i + 1) * (1 + 0.1 * q * (i % 2)),
                             "ticker": t})
    db.upsert_thirteenf(pd.DataFrame(rows))
    ins = pd.DataFrame([
        {"accession": "demo-ins-1", "line": "1", "ticker": "MSFT", "owner": "Mario Rossi", "owner_cik": "1",
         "relationship": "Director", "title": "Consigliere", "trans_date": d(1), "filed": d(1), "code": "P",
         "shares": 2000, "price": 100.0, "value": 200000.0, "acquired": True, "owned_after": 5000, "planned": False},
        {"accession": "demo-ins-2", "line": "1", "ticker": "AAPL", "owner": "Anna Bianchi", "owner_cik": "2",
         "relationship": "Officer", "title": "Direttrice finanziaria", "trans_date": d(2), "filed": d(2), "code": "P",
         "shares": 1000, "price": 150.0, "value": 150000.0, "acquired": True, "owned_after": 9000, "planned": False},
    ])
    db.upsert_insiders(ins)
    db.add_alerts([{"ticker": "NVDA", "code": "VRD", "key": "demo", "message":
                    "giudizio da Aspettare a Nessun segnale netto (Prezzo 0,31 → 0,45)", "data": {}}],
                  created_at=f"{d(1)}T20:15:00")


def _has_adj(conn) -> bool:
    return "adj_close" in {r[1] for r in conn.execute("PRAGMA table_info(price_history)")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Genera il sito pubblico statico")
    ap.add_argument("--out", default=None, help="cartella di uscita (default public_site.out_dir, demo: build/sito-demo)")
    ap.add_argument("--demo", action="store_true", help="dati simulati + dati d'esempio su una copia del database demo")
    ap.add_argument("--config", default=None)
    ap.add_argument("--json", default=None, help="salva anche il contesto (dati del sito) in questo file JSON")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    tmp = None
    if args.demo:
        tmp = Path(tempfile.mkdtemp(prefix="sito-demo-"))
        copy = tmp / "demo.db"
        shutil.copyfile(demo_db_path(cfg, args.config), copy)
        db = DBManager(copy)
        seed_demo(db)
        db = public_site.ReadOnlyDB(copy)
        cfg["data"]["db_path"] = str(copy)
        cfg.setdefault("superinvestors", {})["funds"] = []  # i fondi dimostrativi del database
        cfg.setdefault("public_site", {})["funds"] = []
        out = args.out or "build/sito-demo"
    else:
        db = public_site.ReadOnlyDB(cfg["data"]["db_path"])
        out = args.out
    try:
        if args.json:
            from modules import verdict_history as vh

            d = vh.app_data(cfg, db, PROJECT_ROOT / "reports")
            Path(args.json).write_text(json.dumps(public_site.collect(cfg, db, d=d), ensure_ascii=False, indent=1),
                                       encoding="utf-8")
            res = public_site.build(cfg, db, out, d=d)
        else:
            res = public_site.build(cfg, db, out)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"Sito: {res['pages']} file in {Path(res['out_dir']).relative_to(PROJECT_ROOT) if Path(res['out_dir']).is_relative_to(PROJECT_ROOT) else res['out_dir']} "
          f"({res['companies']} aziende, {res['alerts']} avvisi, {res['seconds']:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
