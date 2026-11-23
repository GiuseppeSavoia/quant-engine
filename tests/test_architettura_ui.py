"""Fase 1a del piano (docs/piano_frontend.md): le pagine disegnano, i calcoli e il database stanno nei servizi
(modules/services/), così la futura API e l'interfaccia nuova li riusano. Questo test conta cosa resta da spostare:
l'elenco ALLOWED può solo accorciarsi (togliere una voce quando quel pezzo passa ai servizi)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = sorted((ROOT / "ui" / "pages" / "azienda").glob("*.py"))  # prima pagina portata ai servizi

# moduli di calcolo che una pagina non deve chiamare direttamente (le costanti di etichette sono ammesse)
LOGIC_MODULES = ("verdict", "changes", "valuation_model", "estimates", "workflow")
_DB = re.compile(r"\.db\.([A-Za-z_]+)")
_IMPORT = re.compile(r"^\s*(?:from modules(?:\.(\w+))? import ([\w, ]+(?: as \w+)?)|import modules\.(\w+))", re.M)

# ancora da spostare: (file, cosa) -> perché è rimasto
ALLOWED = {
    ("valutazione.py", "db.RESEARCH_FIELDS"): "costante dei campi della scheda di ricerca",
    ("valutazione.py", "import valuation_model"): "valutazione a leve: prossima fetta della fase 1a",
}


def _findings(path: Path) -> set[tuple[str, str]]:
    src = path.read_text(encoding="utf-8")
    out = {(path.name, f"db.{m}") for m in _DB.findall(src)}
    for sub, names, plain in _IMPORT.findall(src):
        mod = sub or plain
        constants = sub and all(n.split(" as ")[0].strip().isupper() for n in re.split(r",\s*", names))
        if mod in LOGIC_MODULES and not constants:  # "from modules.changes import ITEMS": solo costanti
            out.add((path.name, f"import {mod}"))
        if not mod:  # from modules import a, b as c
            for name in re.split(r",\s*", names):
                base = name.split(" as ")[0].strip()
                if base in LOGIC_MODULES:
                    out.add((path.name, f"import {base}"))
    return out


def test_company_page_uses_services_only():
    found = set().union(*(_findings(p) for p in SCOPE))
    unexpected = sorted(found - set(ALLOWED))
    assert not unexpected, f"Accessi diretti nuovi dalla scheda (spostarli in modules/services): {unexpected}"
    stale = sorted(set(ALLOWED) - found)
    assert not stale, f"Voci di ALLOWED già risolte: toglierle dall'elenco {stale}"


def test_checker_catches_direct_access(tmp_path):
    f = tmp_path / "finta.py"
    f.write_text("from modules import verdict, screener\nfrom modules.changes import ITEMS as LABELS\n"
                 "x = d.db.load_alerts()\nimport modules.workflow\n", encoding="utf-8")
    assert _findings(f) == {("finta.py", "import verdict"), ("finta.py", "db.load_alerts"),
                            ("finta.py", "import workflow")}
