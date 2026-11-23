"""Difetti rimasti aperti dal collaudo (2026-11-16) e corretti dopo: un test per ognuno, senza rete."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- #3: --demo e --config
def _db_with_run(path: Path, argv: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs (run_id TEXT, argv TEXT)")
    conn.execute("INSERT INTO runs VALUES ('20260101_000000', ?)", (argv,))
    conn.commit()
    conn.close()


def test_demo_db_path_rule(tmp_path):
    """--demo: senza --config data/demo.db; con --config il suo database, ma mai uno con dati veri né quello vero."""
    from modules.config import DEMO_DB_PATH, demo_db_path, load_config

    cfg = load_config(None)
    assert demo_db_path(cfg, None) == DEMO_DB_PATH
    # configurazione che lascia il database vero (cambia solo i parametri): si resta su data/demo.db
    yaml = tmp_path / "solo_parametri.yaml"
    yaml.write_text("portfolio:\n  max_positions: 5\n", encoding="utf-8")
    assert demo_db_path(load_config(yaml), yaml) == DEMO_DB_PATH
    # copia della demo (ultima esecuzione con --demo) o file nuovo: il database della configurazione
    copy = tmp_path / "copia.db"
    _db_with_run(copy, "['--demo', '--config', 'x.yaml']")
    yaml2 = tmp_path / "copia.yaml"
    yaml2.write_text(f"data:\n  db_path: {copy}\n", encoding="utf-8")
    assert demo_db_path(load_config(yaml2), yaml2) == copy.resolve()
    new = tmp_path / "nuovo.db"
    yaml3 = tmp_path / "nuovo.yaml"
    yaml3.write_text(f"data:\n  db_path: {new}\n", encoding="utf-8")
    assert demo_db_path(load_config(yaml3), yaml3) == new.resolve()
    # database con dati veri (ultima esecuzione senza --demo): errore, nessuna scrittura
    real = tmp_path / "veri.db"
    _db_with_run(real, "['--force-fundamentals']")
    yaml4 = tmp_path / "veri.yaml"
    yaml4.write_text(f"data:\n  db_path: {real}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dati veri"):
        demo_db_path(load_config(yaml4), yaml4)


def test_demo_db_path_follows_project_root(tmp_path, monkeypatch):
    """Il percorso della demo si calcola a ogni chiamata: test_pipeline sposta PROJECT_ROOT in una cartella temporanea
    e main.py --demo non deve scrivere nel data/demo.db vero (successo il 2026-11-16 con una costante)."""
    from modules import config as config_mod

    monkeypatch.setattr(config_mod, "PROJECT_ROOT", tmp_path)
    assert config_mod.demo_db_path(config_mod.load_config(None), None) == tmp_path / "data" / "demo.db"


def test_main_demo_refuses_real_database(tmp_path):
    """main.py --demo --config su un database con dati veri si ferma prima di aprirlo in scrittura."""
    import main

    real = tmp_path / "veri.db"
    _db_with_run(real, "['--no-update']")
    before = real.read_bytes()
    yaml = tmp_path / "veri.yaml"
    yaml.write_text(f"data:\n  db_path: {real}\n", encoding="utf-8")
    assert main.main(["--demo", "--config", str(yaml), "--no-update"]) == 1
    assert real.read_bytes() == before


def test_app_demo_with_config_uses_config_database(tmp_path, monkeypatch):
    """La webapp accetta --demo insieme a --config: preferenze accanto al database della configurazione."""
    from ui import common

    copy = tmp_path / "copia.db"
    _db_with_run(copy, "['--demo']")
    yaml = tmp_path / "copia.yaml"
    yaml.write_text(f"data:\n  db_path: {copy}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["app.py", "--demo", "--config", str(yaml)])
    assert common.prefs_path("preferenze_x.json") == tmp_path / "preferenze_x.json"
    assert common.main_config_args() == ["--demo", "--config", str(yaml.resolve())]


# ---------------------------------------------------------------- #5/#6: testo delle prove del giudizio
def test_evidence_reference_matches_outcome():
    """Il riferimento mostra le soglie dell'ESITO (dal punteggio 0,67 / 0,33), non quelle del punteggio pieno:
    "7 esercizi in utile, favorevole" non può più citare "favorevole da 8"."""
    from modules import verdict as vd

    cfg = vd.settings(None)
    good, bad = cfg["thresholds"]["profitable_years"]
    ref = vd._reference("profitable_years", good, bad, cfg)
    # il primo intero favorevole e l'ultimo contrario sono quelli che danno davvero quell'esito
    fav = int(ref.split("favorevole da ")[1].split(",")[0])
    con = int(ref.split("contrario fino a ")[1].split(";")[0])
    assert vd._score("profitable_years", fav, good, bad) >= cfg["favorable"]
    assert vd._score("profitable_years", fav - 1, good, bad) < cfg["favorable"]
    assert vd._score("profitable_years", con, good, bad) <= cfg["contrary"]
    assert vd._score("profitable_years", con + 1, good, bad) > cfg["contrary"]
    assert f"punteggio pieno da {int(good)}" in ref
    # più basso è meglio e prove che non sono mai contrarie
    assert vd._reference("net_debt_ebitda", *cfg["thresholds"]["net_debt_ebitda"], cfg).startswith("favorevole fino a")
    assert "mai contrario" in vd._reference("insider_buyers", *cfg["thresholds"]["insider_buyers"], cfg)
    # per ogni soglia lineare: il valore della soglia d'esito dà quell'esito
    for key, (g, b) in cfg["thresholds"].items():
        if key in vd.ONE_SIDED | vd.CENTERED or vd._is_integer(key) or g == b:
            continue
        f = vd._outcome_bound(key, g, b, cfg["favorable"], True)
        c = vd._outcome_bound(key, g, b, cfg["contrary"], False)
        assert vd._score(key, f, g, b) >= cfg["favorable"] - 1e-9, key
        assert vd._score(key, c, g, b) <= cfg["contrary"] + 1e-9, key


def test_verdict_numbers_use_typographic_minus():
    from modules import verdict as vd

    assert vd._pct(-0.065) == "−6,5%"
    assert vd._num(-1.5) == "−1,5"
    assert vd._pct(-0.00001) == "0,0%"  # mai "−0,0%"
    assert vd._pct(0.02, signed=True) == "+2,0%"


def test_fast_read_never_mixes_old_texts(cfg, demo_db, tmp_path, monkeypatch):
    """Le frasi del giudizio sono salvate nello storico: se cambia il sorgente di verdict.py l'impronta cambia e la
    lettura veloce ricalcola tutto (nessun testo vecchio mescolato ai nuovi)."""
    import pandas as pd

    from modules import verdict as vd
    from modules import verdict_history as vh

    app = vh.app_data(cfg, demo_db, tmp_path)
    vh.record(demo_db, cfg, d=app, run_id=app.run_id)
    tickers = list(app.tickers)
    assert vh.saved_verdicts(app, tickers)[1] == []
    # righe salvate con il testo "vecchio" (trattino): stesso giudizio, frase diversa
    old = demo_db.load_verdict_history()
    assert old["rule"].notna().all()
    source = Path(vd.__file__).resolve()
    real_read = Path.read_bytes

    def edited(self):
        data = real_read(self)
        return data + b"\n# testo cambiato\n" if self.resolve() == source else data

    monkeypatch.setattr(Path, "read_bytes", edited)
    saved, stale = vh.saved_verdicts(app, tickers)
    assert stale == tickers and saved.empty
    pd.testing.assert_frame_equal(vh.load_or_assess(app, tickers), vd.assess_many(app, tickers), check_dtype=False)


# ---------------------------------------------------------------- #12: leva dei ricavi per capitale investito
def test_sales_to_capital_label_has_no_currency():
    """La leva vale per aziende in dollari come in euro: l'etichetta non nomina una valuta."""
    from modules import valuation_model as vm
    from ui.pages.azienda import valutazione

    label = vm.LEVER_LABELS["sales_to_capital"]
    assert "euro" not in label and "dollar" not in label and "unità" in label
    assert valutazione.PLAUS_RATIO_HINT == label


# ---------------------------------------------------------------- #19: tesi "in corso" nella scheda
def test_thesis_line_counts_in_progress():
    from ui.pages.azienda.verdetto import _thesis_line

    th = [{"on_track": True, "text": "a"}, {"on_track": None, "text": "b (in corso)"},
          {"on_track": False, "text": "c (sotto la tua ipotesi)"}]
    line = _thesis_line({"thesis": th})
    assert "✓ 1 ipotesi rispettata" in line and "1 in corso" in line and "✗ 1 no (c" in line
    assert "✗" not in _thesis_line({"thesis": [{"on_track": None, "text": "x"}]})


# ---------------------------------------------------------------- #29: ricerche salvate con i filtri sul giudizio
def test_saved_screen_keeps_verdict_filters(tmp_path):
    """Il filtro per giudizio e i punteggi minimi si salvano; le ricerche salvate prima (senza la colonna) restano
    leggibili, con filtri vuoti."""
    from data.db_manager import DBManager

    old = tmp_path / "vecchio.db"
    with sqlite3.connect(old) as conn:  # tabella come prima della correzione, con una ricerca già salvata
        conn.execute("CREATE TABLE saved_screens (name TEXT PRIMARY KEY, rules TEXT, sectors TEXT, created_at TEXT)")
        conn.execute("INSERT INTO saved_screens VALUES ('Vecchia', '[{\"metric\": \"pe\", \"op\": \"<=\", "
                     "\"value\": 15.0}]', '[\"Energy\"]', '2026-11-01T10:00:00')")
    db = DBManager(old)
    screens = db.load_screens()
    assert screens["Vecchia"]["filters"] == {} and screens["Vecchia"]["sectors"] == ["Energy"]
    flt = {"verdicts": ["candidata", "aspettare"], "min_scores": {"quality": 0.6, "price": 0.4}}
    db.save_screen("Nuova", [{"metric": "roic", "op": ">=", "value": 0.1}], [], flt)
    assert db.load_screens()["Nuova"]["filters"] == flt


@pytest.mark.skipif(not (ROOT / "data" / "demo.db").exists(), reason="manca data/demo.db")
def test_esplora_saves_and_loads_verdict_filters(tmp_path):
    """Esplora: "Salva questa ricerca" salva anche giudizio e punteggi minimi, "Carica" li rimette."""
    import socket

    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    from streamlit.util import calc_hash

    from data.db_manager import DBManager
    from modules import quotes
    from tests.test_pagine_elenco import _env

    config = _env(tmp_path, dirty=False)

    def no_network(*args, **kwargs):
        raise OSError("rete disattivata nei test")

    def run(state):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
        for k, v in state.items():
            at.session_state[k] = v
        at._page_hash = calc_hash("esplora")
        return at.run()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        at = run({"ex_verdict": ["candidata"], "ex_min_quality": 0.5, "ex_save_name": "Solo candidate"})
        assert not at.exception, [e.value for e in at.exception]
        assert not any("punteggi minimi no" in str(c.value) for c in at.caption)  # avviso del collaudo tolto
        next(b for b in at.button if b.label == "Salva").click().run()
        assert not at.exception, [e.value for e in at.exception]
        db_path = __import__("yaml").safe_load(config.read_text())["data"]["db_path"]
        saved = DBManager(db_path).load_screens()["Solo candidate"]
        assert saved["filters"] == {"verdicts": ["candidata"], "min_scores": {"quality": 0.5}}

        at = run({"ex_choice": "Solo candidate"})
        next(b for b in at.button if b.label == "Carica").click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["ex_verdict"] == ["candidata"]
        assert at.session_state["ex_min_quality"] == 0.5 and at.session_state["ex_min_price"] == 0.0


# ---------------------------------------------------------------- #14: riporto delle minusvalenze
def test_tax_loss_carryforward_four_years():
    """Minusvalenze degli anni prima compensano le plusvalenze (di azioni) fino al quarto anno dopo; i guadagni
    degli ETF (redditi di capitale) restano tassati per intero."""
    import numpy as np
    import pandas as pd

    from modules import portfolio

    def real(rows):
        return pd.DataFrame([{"ticker": t, "date": pd.Timestamp(d), "quantity": 1, "proceeds_eur": 0.0,
                              "cost_eur": 0.0, "gain_eur": g} for t, d, g in rows])

    none = pd.DataFrame({"ticker": [], "gain_eur": []})
    r = real([("A", "2022-06-01", -1000.0), ("B", "2024-03-01", 600.0), ("C", "2026-02-01", 700.0)])
    t = portfolio.tax_estimate(r, none, today="2026-11-16")
    assert t["by_year"][2022]["tax"] == 0 and t["by_year"][2022]["loss_carry"] == 1000
    assert t["by_year"][2024]["loss_used"] == 600 and t["by_year"][2024]["tax"] == 0
    # nel 2026 restano 400 della perdita del 2022 (vale fino al 31/12/2026): imposta su 300
    assert t["by_year"][2026]["loss_used"] == 400
    assert t["by_year"][2026]["tax"] == pytest.approx(300 * 0.26)
    assert t["carry"] == []
    # scaduta: la perdita del 2021 non vale più nel 2026
    t = portfolio.tax_estimate(real([("A", "2021-06-01", -1000.0), ("C", "2026-02-01", 700.0)]), none,
                               today="2026-11-16")
    assert t["by_year"][2026]["loss_used"] == 0 and t["by_year"][2026]["tax"] == pytest.approx(700 * 0.26)
    # vendendo oggi: le minusvalenze ancora utilizzabili riducono l'imposta in più
    pos = pd.DataFrame({"ticker": ["D", "ETF"], "gain_eur": [500.0, 200.0]})
    t = portfolio.tax_estimate(real([("A", "2025-06-01", -300.0)]), pos, today="2026-11-16", funds={"ETF"})
    assert t["loss_carry_start"] == 300
    assert t["tax_if_sold"] == pytest.approx((500 - 300) * 0.26 + 200 * 0.26)  # l'ETF non si compensa
    assert t["carry"] == [{"year": 2025, "amount": 300.0, "expires": 2029}]
    # guadagno realizzato su un ETF: redditi di capitale, le minusvalenze non lo toccano
    t = portfolio.tax_estimate(real([("A", "2025-06-01", -300.0), ("ETF", "2026-02-01", 400.0)]), none,
                               today="2026-11-16", funds={"ETF"})
    assert t["by_year"][2026]["tax"] == pytest.approx(400 * 0.26) and t["carry"][0]["amount"] == 300
    assert not np.isnan(t["tax_if_sold"])
