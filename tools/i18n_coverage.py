"""Frasi del motore non ancora tradotte in inglese: chiama le richieste dell'API usate dall'interfaccia web sui dati
simulati (copia della demo) con X-Lang: en e raccoglie i testi che sembrano italiani e il catalogo
(modules/i18n/en/*.json) non conosce. Usato da tests/test_i18n.py; da riga di comando stampa l'elenco.

Uso: .venv/bin/python tools/i18n_coverage.py [--json FILE] [--tickers N]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# parole italiane frequenti (e lettere accentate): un testo che ne contiene una e non è nel catalogo è "scoperto"
ITALIAN = re.compile(
    r"\b(di|del|della|delle|dei|degli|il|lo|la|le|gli|un|una|uno|non|per|con|che|nel|nella|sul|sulla|dal|dalla|"
    r"anni|anno|prezzo|azienda|aziende|dati|regola|regole|sopra|sotto|nessun|nessuna|giudizio|mancante|ultimo|"
    r"ultima|crescita|utile|ricavi|debito|cassa|settore|soglia|valore|rendimento|margine|azioni|bilancio|"
    r"bilanci|oggi|mesi|giorni|trimestre|esercizio|più|meno|tra|fra|sono|è|ha|hanno|alto|alta|basso|bassa|"
    r"pieno|buona|buono|debole|qualità|solidità|segnali|candidata|scartata|positivo|negativo|da|ai|al|alla|"
    r"o|e|se|ma|come|quando|dove|perché|cosa|tutti|tutte|solo|sempre|mai|ancora|nuovo|nuova|rend|marg|cresc|var|"
    r"ult|prev|stima|stime|media|mediana|chiusura|massimo|minimo|anni|fonte|voce|voci)\b|[àèéìòù]", re.I)
SKIP_KEYS = {"name", "names", "title_raw", "source", "company", "issuer", "filer", "fund", "manager", "holder",
             "insider", "owner", "relationship", "fund_name", "industry", "sector_raw", "hq", "description"}


def looks_italian(text: str) -> bool:
    plain = re.sub(r"\b[A-Z]+/[A-Z]+\b|\bO'\w+", " ", text)      # P/E, EV/EBITDA, O'Neil
    return bool(ITALIAN.search(plain)) and bool(re.search(r"[a-zà-ù]{3}", plain))


def _client(source_db: str | None = None):
    """Client dell'API su una COPIA del database (la demo, o `source_db`: mai il file originale)."""
    import sqlite3

    from fastapi.testclient import TestClient

    from api.app import create_app
    from modules.config import load_config

    tmp = Path(tempfile.mkdtemp(prefix="qe_i18n_"))
    cfg = load_config(None)
    db = tmp / "copia.db"
    if source_db:
        with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as src, sqlite3.connect(db) as dst:
            src.backup(dst)
    else:
        shutil.copy(ROOT / cfg["data"].get("demo_db_path", "data/demo.db"), db)
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp / 'facts.db'}\n  backup_dir: {tmp / 'b'}\n"
                      "universe:\n  source: sp500\n", encoding="utf-8")
    return TestClient(create_app(str(config), demo=not source_db)), tmp


def requests(client, n_tickers: int, tickers: list[str] | None = None) -> list[tuple[str, str, dict | None]]:
    """(metodo, percorso, corpo) delle richieste da provare."""
    p = "/api/v1"
    tickers = tickers or [r["ticker"] for r in client.get(f"{p}/companies?limit=200").json()["results"]][:n_tickers]
    out = [("GET", x, None) for x in (
        "/health", "/today", "/my-companies", "/watchlist", "/alerts", "/method", "/portfolio", "/journal",
        "/strategies", "/strategies/overview", "/strategies/builder", "/funds", "/quality", "/custom-companies",
        "/backtest", "/auto-strategy", "/lab/options", "/lab/experiments", "/market-strip", "/pipeline", "/news",
        "/news/filings", "/news/x", "/explore/catalog",
        "/command?q=MSFT%20FA", "/command?q=ZZZZ%20QQ", "/command?q=AIUTO", "/command?q=", "/command?q=SEGUI",
        f"/compare?t={','.join(tickers[:3])}")]
    out.append(("POST", "/explore", {"universe": "sp500", "rules": [{"metric": "roic", "op": ">=", "value": 0.1}],
                                     "sectors": [], "verdicts": []}))
    from modules.strategies import CATALOG

    munger = next((e["definition"] for n, e in CATALOG.items() if n.startswith("Munger")), {})
    out += [("POST", "/method/metrics/preview", {"formula": "roe * 2"}),
            ("POST", "/method/metrics/preview", {"formula": "roe +"}),
            ("POST", "/method/metrics/preview", {"formula": "boh / 2"}),
            ("POST", "/strategies/preview", {"definition": munger}),
            ("POST", "/strategies/preview", {"definition": {"name": "x"}}),
            ("POST", "/lab/preview", {"values": {}, "period": "all"})]
    for t in tickers:
        out.append(("POST", f"/companies/{t}/valuation", {"levers": {}}))
        for sub in ("", "/financials", "/financials?view=quarterly_ttm", "/valuation", "/price", "/peers",
                    "/documents", "/estimates", "/metrics", "/dividends", "/ownership", "/research", "/investors",
                    "/auto", "/pipeline"):
            out.append(("GET", f"/companies/{t}{sub}", None))
    try:
        names = [s["name"] for s in client.get(f"{p}/strategies").json().get("strategies", [])][:6]
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        out.append(("GET", f"/strategies/detail?name={name}", None))
    return [(m, p + x, b) for m, x, b in out]


NEWS_KEYS = {"title", "summary", "link", "url"}   # titoli delle notizie: testo di terzi, mai tradotto


def _walk(obj, key=None, news=False):
    from modules.i18n import SKIP_KEYS as NEVER

    if isinstance(obj, str):
        if key not in SKIP_KEYS and key not in NEVER and not (news and key in NEWS_KEYS) and not obj.startswith("http"):
            yield obj
    elif isinstance(obj, dict):
        option = "value" in obj and "label" in obj
        for k, v in obj.items():
            if not (option and k == "value"):
                yield from _walk(v, k, news or "link" in obj)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key, news)


def _skipped_values(obj, out: dict, key=None, parent=None) -> None:
    from modules.i18n import SKIP_KEYS as NEVER

    if isinstance(obj, str):
        if key in NEVER and re.search(r"[A-Za-zÀ-ÿ]{3}", obj) and not obj.startswith("http") \
                and not (key == "name" and isinstance(parent, dict) and ("ticker" in parent or "cik" in parent)):
            out.setdefault(key, set()).add(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _skipped_values(v, out, k, obj)
    elif isinstance(obj, list):
        for v in obj:
            _skipped_values(v, out, key, parent)


def uncovered(n_tickers: int = 6, source_db: str | None = None, tickers: list[str] | None = None,
              skipped: dict | None = None) -> dict[str, list[str]]:
    """{testo italiano non tradotto: [richieste in cui compare]}. `skipped`, se dato, raccoglie i valori dei campi
    che il server non traduce mai (nome del campo -> valori), da controllare contro i dizionari del web."""
    from modules import i18n

    client, tmp = _client(source_db)
    found: dict[str, list[str]] = defaultdict(list)
    try:
        with client:
            for method, path, body in requests(client, n_tickers, tickers):
                r = client.request(method, path, json=body)
                if r.status_code >= 500 or "json" not in r.headers.get("content-type", ""):
                    continue
                news = path.startswith("/api/v1/news") or path.endswith("/documents")
                # scoperto = sembra italiano e il catalogo non lo traduce PER INTERO (frasi composte: ogni pezzo)
                if skipped is not None:
                    _skipped_values(r.json(), skipped)
                for s in set(_walk(r.json(), news=news)):
                    if looks_italian(s) and i18n.lookup(s, "en") is None:
                        found[s].append(path.split("?")[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dict(found)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--tickers", type=int, default=6)
    ap.add_argument("--db", help="database da copiare (es. data/finance_lake.db; si lavora su una copia)")
    ap.add_argument("--only", help="ticker separati da virgola")
    ap.add_argument("--skipped", action="store_true", help="elenca i valori dei campi mai tradotti assenti dal web")
    a = ap.parse_args(argv)
    skipped: dict | None = {} if a.skipped else None
    found = uncovered(a.tickers, a.db, a.only.split(",") if a.only else None, skipped)
    if skipped:
        web = {}
        for f in sorted((ROOT / "web" / "src" / "i18n" / "en").glob("*.json")):
            web.update(json.loads(f.read_text(encoding="utf-8")))
        for key, values in sorted(skipped.items()):
            miss = sorted(v for v in values if v not in web)
            if miss:
                print(f"[{key}] non nei dizionari del web: {miss[:40]}")
    if a.json:
        Path(a.json).write_text(json.dumps({k: sorted(set(v))[:5] for k, v in sorted(found.items())},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    for text, where in sorted(found.items()):
        print(f"{text!r}  <- {sorted(set(where))[0]}")
    print(f"{len(found)} testi non tradotti")
    return 0 if not found else 1


if __name__ == "__main__":
    raise SystemExit(main())
