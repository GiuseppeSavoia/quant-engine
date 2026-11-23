"""Funzioni della scheda azienda (DCF, FA, ERN, HDS, NOTE, AUTO): pezzi puri di presentazione e apertura con AppTest su
una copia della demo, senza rete, controllando che a schermo non compaiano None/nan e che il risultato del DCF
stia prima delle leve."""
from __future__ import annotations

import re
import shutil
import socket
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ui.pages.azienda import bilanci as B, ricerca as R, valutazione as V

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "demo.db"


# ================================================================== pezzi puri
def test_plausibility_view_units_and_missing():
    plaus = pd.DataFrame([
        {"lever": "Crescita dei ricavi (vs crescita annua a 3 anni del settore)", "value": 0.285, "p10": 0.01,
         "p50": 0.05, "p90": 0.12, "percentile": 0.95, "verdict": "raro: solo il 5% delle aziende del settore fa di più"},
        {"lever": "Ricavi per unità di capitale investito", "value": 1.5, "p10": 0.5, "p50": 1.0, "p90": 2.0, "percentile": 0.6,
         "verdict": "comune nel settore"},
        {"lever": "Margine operativo obiettivo", "value": 0.2, "p10": np.nan, "p50": np.nan, "p90": np.nan,
         "percentile": np.nan, "verdict": "settore troppo piccolo per un confronto"},
    ])
    view, compared = V.plausibility_view(plaus)
    assert compared and len(view) == 2  # la riga senza settore non c'è (c'è la frase dello stato vuoto)
    assert view.iloc[0]["La tua ipotesi"] == "28,5%" and view.iloc[0]["Posizione"] == "95%"
    assert view.iloc[1]["La tua ipotesi"] == "1,50"
    text = view.to_string()
    assert "None" not in text and "nan" not in text
    small = plaus.iloc[[2]]
    view, compared = V.plausibility_view(small)
    assert not compared and view.empty


def test_result_items_and_sensitivity():
    items = V.valuation_result_items({"expected": 120.0, "per_scenario": {"Pessimista": 80.0, "Base": 120.0,
                                                                         "Ottimista": float("nan")}},
                                     100.0, {"Pessimista": 0.25, "Base": 0.5, "Ottimista": 0.25},
                                     {"p10": 90.0, "p90": 150.0, "prob_above_price": 0.7}, None)
    html = " ".join(f"{k} {v}" for k, v in items)
    assert "120,00 $" in html and "+20,0%" in html and "90 – 150 $" in html and "Base 50%" in html
    assert "None" not in html and "nan" not in html and "–" in html  # ottimista e crescita implicita mancanti
    grid = pd.DataFrame([[90.0, 110.0], [np.nan, 130.0]], index=[0.08, 0.09], columns=[0.05, 0.075])
    s = V.sensitivity_view(grid, 100.0)
    assert list(s.index) == ["WACC 8,0%", "WACC 9,0%"] and s.columns[1] == "crescita 7,5%"
    assert s.iloc[0, 0] == "90 ▼" and s.iloc[0, 1] == "110 ▲" and s.iloc[1, 0] == "–"


def test_cost_of_capital_rows_italian():
    c = {"risk_free": 0.0425, "erp": 0.028, "erp_computed": True, "group": "Software", "peers": 30,
         "beta_unlevered": 1.1, "beta_regression": float("nan"), "beta": 1.15, "cost_of_equity": 0.0747,
         "coverage": float("nan"), "debt": 0.0, "rating": "AAA", "spread": 0.0063, "cost_of_debt": 0.0488,
         "cost_of_debt_after_tax": 0.0366, "marginal_tax": 0.25, "weight_equity": 0.98, "weight_debt": 0.02,
         "wacc": 0.0564, "mature_wacc": 0.0705}
    rows = V.cost_of_capital_rows(c)
    text = rows.to_string()
    assert "4,25%" in text and "nessun debito" in text and "AAA, +0,63%" in text and "nan" not in text


def test_period_labels_and_shares_axis():
    idx = pd.to_datetime(["2023-12-31", "2024-09-28", "2025-06-30"])
    fin = pd.DataFrame({"ttm": [False, False, True], "shares": [1110e6, 1105e6, 1099e6]}, index=idx)
    assert B._period_labels(fin, quarterly=False) == ["2023", "set 2024", "12 mesi a giu 25"]
    assert B._period_labels(fin, quarterly=True) == ["dic 23", "set 24", "giu 25"]
    s = B.shares_summary(fin["shares"], ["2023", "2024", "2025"])
    assert "in calo" in s and "1.110,0" in s and "−1,0%" in s
    fig = B._bars(fin, ["a", "b", "c"], [("shares", "Azioni")])
    assert fig.layout.yaxis.rangemode == "tozero"


def test_financial_table_italian():
    idx = pd.to_datetime(["2024-12-31"])
    fin = pd.DataFrame({"revenue": [1236.7e6], "operating_margin": [0.256], "debt_to_equity": [np.nan],
                        "shares": [1099.9e6], "ttm": [False]}, index=idx)
    t = B.financial_table(fin, ["2024"])
    vals = dict(zip(t.index, t["2024"]))
    assert "1.237" in vals.values() and "25,6%" in vals.values() and "1.099,9" in vals.values()
    assert all(v != "nan" and v != "None" for v in vals.values())


def test_decision_history_view_readable():
    hist = pd.DataFrame({"run_id": ["r1"], "as_of": ["2026-11-02"], "action": ["BUY"], "confidence": [0.6909],
                         "threshold": [0.6], "regime": ["Bull"], "price": [138.5858], "fair_value": [None],
                         "margin_of_safety": [0.123], "reasoning": ["conf 0.69 >= 0.60"]})
    v = R.decision_history_view(hist)
    assert "run_id" not in v.columns and list(v.columns)[:3] == ["Data", "Decisione", "Confidenza"]
    row = v.iloc[0]
    assert row["Confidenza"] == "0,69" and row["Prezzo"] == "138,59" and row["Fair value DCF"] == "–"
    assert row["Margine DCF"] == "+12,3%" and row["Decisione"] == "▲ BUY" and "≥" in row["Motivo"]


# ================================================================== AppTest sulla demo
pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.util import calc_hash  # noqa: E402


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    if not DEMO_DB.exists():
        pytest.skip("manca data/demo.db (python main.py --demo)")
    tmp = tmp_path_factory.mktemp("scheda")
    db = tmp / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp / 'facts.db'}\n  backup_dir: {tmp / 'backups'}\n",
                      encoding="utf-8")

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    from modules import quotes

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        yield config


def _run(fn: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.session_state["co_fn"] = fn
    at.session_state["ticker"] = "AAPL"
    at._page_hash = calc_hash("azienda")
    return at.run()


def _texts(at: AppTest) -> list[str]:
    out = []
    for kind in ("markdown", "caption", "subheader", "text", "html", "metric", "expander"):
        try:
            els = at.get(kind)
        except Exception:  # noqa: BLE001
            continue
        for e in els:
            p = getattr(e, "proto", None)
            out.append(str(getattr(p, "body", "") or getattr(p, "label", "") or getattr(e, "value", "")))
    return out


BAD = re.compile(r"\bNone\b|\bnan\b|\bNaN\b|Choose options")


@pytest.mark.parametrize("fn", ["DCF", "FA", "ERN", "HDS", "NOTE", "AUTO"])
def test_function_renders_clean(app_env, fn):
    at = _run(fn)
    assert not [e.value for e in at.exception], fn
    texts = _texts(at)
    bad = [t[:120] for t in texts if BAD.search(re.sub(r"<style>.*?</style>", "", t, flags=re.S))]
    assert not bad, f"{fn}: {bad}"


def test_dcf_result_before_levers(app_env):
    at = _run("DCF")
    html = [str(e.proto.body) for e in at.get("html")]
    idx = next(i for i, h in enumerate(html) if "Risultato della tua valutazione" in h)
    assert "Valore per azione" in html[idx] and "Margine sul prezzo" in html[idx]
    assert any(".st-key-dcf_result" in h and "sticky" in h for h in html)  # riga fissa sotto la cornice


def test_auto_says_experimental(app_env):
    at = _run("AUTO")
    html = " ".join(str(e.proto.body) for e in at.get("html"))
    assert "sperimentale" in html and "Verdetto" in html


def test_sensitivity_html_symbol_and_colour():
    grid = pd.DataFrame([[90.0, 110.0]], index=[0.08], columns=[0.05, 0.075])
    h = V.sensitivity_html(V.sensitivity_view(grid, 100.0))
    assert '<span class="neg">90 ▼</span>' in h and '<span class="pos">110 ▲</span>' in h and "WACC 8,0%" in h


def test_why_text_and_price_chart_settings_in_italian():
    """"Perché questa decisione" con i numeri in italiano (0,67 e non 0.67); soglie del grafico dei prezzi da
    config (`price_chart:`), niente numeri fissi né st.info/st.warning nel file."""
    import inspect

    from modules.config import DEFAULTS, load_config
    from ui.pages.azienda import prezzo as P

    b = {"parts": {"regime": 0.15, "value": 0.4, "multi_factor": 0.12},
         "weights": {"regime": 0.2, "value": 0.4, "multi_factor": 0.4}, "confidence": 0.67, "regime": "Bull",
         "action": "SKIP", "threshold": 0.8, "max_possible": 0.9, "target": 0.85, "margin_now": None,
         "margin_needed": 0.35, "value_needed": 0.5, "mf_needed": 0.55, "mf_score": 0.3}
    text = P._why_text(b)
    assert "0,67" in text and "0,15/0,20" in text and "0,85" in text and "35%" in text and "40%" in text
    assert not re.search(r"\d\.\d", text), text
    src = inspect.getsource(P)
    assert "st.info(" not in src and "st.warning(" not in src and ":.2f}" not in src and ":.0%}" not in src
    assert load_config(None)["price_chart"] == DEFAULTS["price_chart"]
    idx = pd.bdate_range("2015-01-01", "2026-01-01")
    px = pd.DataFrame({"close": 1.0}, index=idx)
    assert P.bar_frequency(px, "MAX") == "M" and P.bar_frequency(px, "MAX", 20) == "W"
    assert P.bar_frequency(px, "1A") == "D"
    assert P.chart_settings({"price_chart": {"max_missing_open": 0.1}})["fair_value_range"] == [0.5, 2.0]
