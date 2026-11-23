"""Genera i file che l'interfaccia web (web/) condivide con Python, così le due versioni non divergono:

- web/src/styles/tokens.css: i token di ui/common.py (TOKENS_LIGHT, TOKENS_DARK, TYPE_SCALE, FONTS, RADII), chiaro su
  :root, scuro con [data-theme="dark"] e con prefers-color-scheme quando l'aspetto segue il sistema;
- web/src/lib/fmt_cases.json: casi (valore, tipo, opzioni -> testo) calcolati con modules/formats.fmt, che i test
  TypeScript di web/src/lib/fmt.ts devono riprodurre identici.

Uso: .venv/bin/python tools/export_web_shared.py [--check]   (--check: errore se i file non sono aggiornati;
tests/test_web_shared.py lo usa).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKENS_CSS = ROOT / "web" / "src" / "styles" / "tokens.css"
FMT_CASES = ROOT / "web" / "src" / "lib" / "fmt_cases.json"


def tokens_css() -> str:
    from ui import common  # importa streamlit: va bene in uno strumento, mai nell'API

    shared = {**common.TYPE_SCALE, **common.FONTS, **common.RADII}
    series = lambda colors: {f"series-{i}": c for i, c in enumerate(colors, 1)}  # noqa: E731 - palette dei grafici
    block = lambda tokens: "\n".join(f"  --{k}: {v};" for k, v in tokens.items())  # noqa: E731
    return ("/* GENERATO da tools/export_web_shared.py dai token di ui/common.py: non modificare a mano. */\n"
            f":root {{\n{block({**common.TOKENS_LIGHT, **series(common.SERIES_LIGHT), **shared})}\n  color-scheme: light;\n}}\n"
            f":root[data-theme=\"dark\"] {{\n{block({**common.TOKENS_DARK, **series(common.SERIES_DARK)})}\n  color-scheme: dark;\n}}\n"
            "@media (prefers-color-scheme: dark) {\n"
            f"  :root:not([data-theme=\"light\"]) {{\n{block({**common.TOKENS_DARK, **series(common.SERIES_DARK)})}\n    color-scheme: dark;\n  }}\n}}\n")


# valori e opzioni che coprono le regole di fmt (decimali, scale, segni, zero senza segno, mancanti, date)
VALUES = [0, 0.0001, -0.0001, 0.125, -0.125, 2.675, 1, -1, 12.3456, -12.3456, 999.995, 1234.5, -1234.5, 1950.731,
          12345.678, 999999.5, 1e6, 2.5e9, -3.4e9, 6.63e10, 1.2e12, None, "nan", "inf", True]
OPTIONS = [{}, {"signed": True}, {"arrow": True}, {"digits": 0}, {"digits": 3}]
CURRENCIES = [None, "USD", "EUR", "GBP", "CHF"]


def fmt_cases() -> list[dict]:
    from modules.formats import fmt

    cases = []
    for kind in ("num", "int", "price", "pct", "pp", "mult", "score", "money", "shares"):
        for v in VALUES:
            pv = float(v) if v in ("nan", "inf") else v
            for opt in OPTIONS:
                cases.append({"v": v, "kind": kind, "opt": opt, "out": fmt(pv, kind, **opt)})
        if kind in ("money", "price", "num", "int"):
            for cur in CURRENCIES[1:]:
                for v in (1234.5, 6.63e10, -12.3, None):
                    cases.append({"v": v, "kind": kind, "opt": {"currency": cur}, "out": fmt(v, kind, currency=cur)})
    for v in ("2026-11-23", "2026-01-05T10:30:00", None, 12, "non una data"):
        for kind in ("date", "date_short", "text"):
            cases.append({"v": v, "kind": kind, "opt": {}, "out": fmt(v, kind)})
    return cases


def expected() -> dict[Path, str]:
    return {TOKENS_CSS: tokens_css(),
            FMT_CASES: json.dumps(fmt_cases(), ensure_ascii=False, indent=1) + "\n"}


def main(argv=None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    stale = []
    for path, text in expected().items():
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        if check:
            stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"scritto {path.relative_to(ROOT)}")
    if stale:
        print("Da rigenerare (.venv/bin/python tools/export_web_shared.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
