"""Il modello Excel: file valido e formule che, ricalcolate da zero, danno gli stessi numeri dell'app (nessuna rete)."""
import io
import math
import re
import zipfile
from xml.dom import minidom

import pytest

from modules import excel_model as em
from modules.valuation_model import Base, Levers, value
from modules.xlsx import Sheet, col_letter, workbook_bytes

BASE = Base(revenue=200e9, margin=0.42, invested_capital=250e9, cash=80e9, debt=60e9, shares=7.4e9, price=480.0,
            risk_free=0.042)
LV = Levers(growth=0.12, target_margin=0.45, sales_to_capital=1.1, wacc=0.085, terminal_wacc=0.075,
            terminal_growth=0.035, terminal_roic=0.15, tax_rate=0.18, margin_years=4, failure_prob=0.02)


def evaluate(sheet: Sheet) -> dict[str, float]:
    """Ricalcola tutte le formule del foglio partendo SOLO dai valori non calcolati (le ipotesi): un mini-Excel per
    il sottoinsieme usato dal modello (+ - * /, confronti, MAX, MIN, IF, SUM, IFERROR, NA, SUMPRODUCT)."""
    raw = {f"{col_letter(c)}{r}": cell for (r, c), cell in sheet.cells.items()}
    cache: dict[str, float] = {}

    def get(name):
        name = name.replace("$", "")
        if name not in cache:
            cell = raw.get(name)
            if cell is None or cell.value is None and cell.formula is None:
                cache[name] = 0.0
            elif cell.formula is None:
                cache[name] = cell.value
            else:
                cache[name] = calc(cell.formula)
        return cache[name]

    def rng(a, b):
        ca, ra = re.match(r"([A-Z]+)(\d+)", a.replace("$", "")).groups()
        cb, rb = re.match(r"([A-Z]+)(\d+)", b.replace("$", "")).groups()
        assert ca == cb or ra == rb
        if ca == cb:
            return [get(f"{ca}{r}") for r in range(int(ra), int(rb) + 1)]
        cols = [col_letter(i) for i in range(1, 30)]
        return [get(f"{c}{ra}") for c in cols[cols.index(ca):cols.index(cb) + 1]]

    def calc(f):
        expr = re.sub(r"(\$?[A-Z]+\$?\d+):(\$?[A-Z]+\$?\d+)", lambda m: f"rng('{m[1]}','{m[2]}')", f)
        expr = re.sub(r"(?<!['A-Z])(\$?[A-Z]{1,2}\$?\d+)(?![\d'(])", lambda m: f"get('{m[1]}')", expr)
        expr = expr.replace("<>", "!=").replace("^", "**")
        expr = re.sub(r"(?<![<>!=])=(?!=)", "==", expr)
        env = {"get": get, "rng": rng, "MAX": max, "MIN": min, "SUM": lambda xs: sum(xs),
               "IF": lambda c, a, b: a if c else b, "NA": lambda: math.nan,
               "IFERROR": lambda a, b: b if not isinstance(a, (int, float)) or math.isnan(a) else a,
               "SUMPRODUCT": lambda a, b: sum(x * y for x, y in zip(a, b))}
        return eval(expr, env)  # noqa: S307 - solo formule generate dal modello nei test

    return {name: get(name) for name, cell in raw.items() if cell.formula is not None}


def test_le_formule_ricalcolate_danno_il_valore_dell_app():
    sheet, out = em.dcf_sheet(BASE, LV, "Prova", "USD")
    stripped = Sheet("DCF", cells={k: type(c)(None if c.formula else c.value, c.formula, c.style)
                                   for k, c in sheet.cells.items()})
    got = evaluate(stripped)
    row, cached = out["per_share"]
    assert cached == pytest.approx(float(value(BASE, LV).per_share))
    assert got[f"B{row}"] == pytest.approx(cached, rel=1e-9)
    for name, cell in sheet.cells.items():  # ogni cella calcolata coincide con il valore scritto dall'app
        if cell.formula is not None and isinstance(cell.value, float) and math.isfinite(cell.value):
            assert got[f"{col_letter(name[1])}{name[0]}"] == pytest.approx(cell.value, rel=1e-9, abs=1e-6), name


def test_le_formule_seguono_le_ipotesi():
    """Cambiare una leva nel file cambia il valore come nell'app (le formule non sono copie dei numeri)."""
    sheet, out = em.dcf_sheet(BASE, LV, "Prova", "USD")
    cells = {k: type(c)(None if c.formula else c.value, c.formula, c.style) for k, c in sheet.cells.items()}
    cells[(em.INPUT_ROWS["growth"], 2)].value = 0.05
    cells[(em.INPUT_ROWS["terminal_wacc"], 2)].value = 0.08
    got = evaluate(Sheet("DCF", cells=cells))
    from dataclasses import replace
    want = float(value(BASE, replace(LV, growth=0.05, terminal_wacc=0.08)).per_share)
    assert got[f"B{out['per_share'][0]}"] == pytest.approx(want, rel=1e-9)


def test_file_xlsx_valido_e_formule_in_inglese():
    sheet, _ = em.dcf_sheet(BASE, LV, "Prova & <test>", "EUR")
    b = workbook_bytes([sheet, em.sensitivity_sheet(BASE, LV, "EUR"),
                        em.scenarios_sheet(BASE, {"Base": LV, "Ottimista": LV}, {"Base": 0.5, "Ottimista": 0.5}, "EUR")])
    z = zipfile.ZipFile(io.BytesIO(b))
    names = set(z.namelist())
    assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
            "xl/styles.xml", "xl/worksheets/sheet1.xml"} <= names
    for n in names:
        minidom.parseString(z.read(n))  # ogni parte è XML valido
    assert b'fullCalcOnLoad="1"' in z.read("xl/workbook.xml")
    for c in sheet.cells.values():
        if c.formula:
            assert ";" not in c.formula and not re.search(r"\b(SOMMA|SE)\(", c.formula)
    with pytest.raises(ValueError):
        Sheet("x").set(1, 1, formula="SOMMA(A1:A2)")


def test_modello_completo_sulla_demo(cfg, demo_db, tmp_path):
    from modules.dashboard_data import DashboardData
    d = DashboardData(cfg, demo_db, tmp_path)
    t = next(t for t in d.index_tickers if d.valuation_setup(t) is not None)
    b = em.company_model(d, t)
    z = zipfile.ZipFile(io.BytesIO(b))
    has_lbo = bool(d.lbo(t) is not None and d.lbo(t).ok)
    assert len([n for n in z.namelist() if n.startswith("xl/worksheets/")]) == 6 + has_lbo
    wb = z.read("xl/workbook.xml").decode()
    assert ("LBO" in wb) == has_lbo
    assert "DCF" in wb and "Bilanci" in wb and "Costo del capitale" in wb


def test_formule_del_costo_del_capitale():
    from modules.cost_of_capital import relevered, wacc
    coc = {"risk_free": 0.042, "erp": 0.045, "beta_unlevered": 0.9, "debt": 50e9, "equity": 400e9,
           "marginal_tax": 0.25, "spread": 0.012, "rating": "A", "group": "Software", "peers": 15}
    beta = relevered(0.9, 50e9, 400e9, 0.25)
    w = wacc(0.042, beta, 0.045, 0.012, 50e9, 400e9, 0.25)
    coc |= {"beta": beta, **w}
    sheet = em.cost_of_capital_sheet(coc)
    stripped = Sheet("C", cells={k: type(c)(None if c.formula else c.value, c.formula, c.style)
                                 for k, c in sheet.cells.items()})
    got = evaluate(stripped)
    assert got["B9"] == pytest.approx(beta) and got["B10"] == pytest.approx(w["cost_of_equity"])
    assert got["B12"] == pytest.approx(w["cost_of_debt_after_tax"]) and got["B14"] == pytest.approx(w["wacc"])
