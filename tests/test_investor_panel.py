"""Tavolo dei grandi investitori (modules/investor_panel.py): esiti a regole, dato mancante mai passato, settore
escluso, prezzo che fa passare una regola di P/E verificato ricalcolando la metrica, sintesi, determinismo."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules import investor_panel as ip
from modules.strategies import _rules

ROOT = Path(__file__).resolve().parent.parent


def _entry(filters, ranking=(("roic", 1.0),), positions=2, sectors=None):
    d = _rules("Prova – test", "", list(ranking), filters=filters, positions=positions, sectors=sectors)
    return {"kind": "rules", "group": "Grandi investitori", "definition": d,
            "investor": {"who": "chi", "source": "fonte", "original": "metodo",
                         "translation": "Esclusi Financials: per le banche il debito non è confrontabile. Altro."}}


@pytest.fixture
def table():
    """Cinque aziende dell'indice e una fuori indice, con i numeri scelti per i casi da provare."""
    idx = ["A", "B", "C", "D", "E", "Z"]
    m = pd.DataFrame({
        "pe": [10.0, 14.0, 16.5, 30.0, np.nan, 12.0],
        "roic": [0.30, 0.20, 0.25, 0.10, 0.40, 0.35],
        "gross_margin": [0.50, 0.45, 0.60, 0.20, 0.70, 0.55],
        "price": [100.0, 50.0, 80.0, 20.0, 10.0, 40.0],
        "market_cap": [1e11, 5e10, 8e10, 2e10, 1e10, 4e10],
        "enterprise_value": [1.1e11, 5.5e10, 8e10, 2.5e10, 1e10, 4e10],
    }, index=idx)
    sectors = {"A": "Industrials", "B": "Industrials", "C": "Industrials", "D": "Financials", "E": "Industrials",
               "Z": "Industrials"}
    return m, sectors, ["A", "B", "C", "D", "E"]


def _assess(table, entry, ticker, cfg=None):
    m, sectors, index = table
    return ip.assess_investor("Prova – test", entry, ticker, m, sectors, index, cfg)


def test_all_rules_passed_and_ranking(table):
    e = _entry([{"metric": "pe", "op": "<=", "value": 15}, {"metric": "roic", "op": ">=", "value": 0.15}], positions=1)
    a = _assess(table, e, "A")
    assert [r["status"] for r in a["rules"]] == ["passed", "passed"]
    assert a["outcome"] == "compra" and a["ranking"]["position"] == 1 and a["ranking"]["passing"] == 2
    b = _assess(table, e, "B")          # passa le regole, ma A ha il ROIC più alto e ne tiene uno solo
    assert b["outcome"] == "piace" and b["ranking"]["position"] == 2 and not b["ranking"]["selected"]


def test_one_rule_missed_by_a_little_is_near(table):
    e = _entry([{"metric": "pe", "op": "<=", "value": 15}, {"metric": "roic", "op": ">=", "value": 0.15}])
    c = _assess(table, e, "C")          # P/E 16,5 contro 15: 10% sopra la soglia
    failed = [r for r in c["rules"] if r["status"] == "failed"]
    assert c["outcome"] == "quasi" and len(failed) == 1 and failed[0]["gap"] == pytest.approx(0.10)
    assert _assess(table, e, "C", {"near_miss": 0.05})["outcome"] == "no"
    d = _assess(table, e, "D")          # P/E 30 e ROIC 10%: due regole mancate
    assert d["outcome"] == "no" and "2 regole su 2" in d["reason"]


def test_missing_data_never_counts_as_passed(table):
    e = _entry([{"metric": "pe", "op": "<=", "value": 15}, {"metric": "roic", "op": ">=", "value": 0.15}])
    x = _assess(table, e, "E")          # P/E mancante, ROIC 40%
    assert [r["status"] for r in x["rules"]] == ["missing", "passed"]
    assert x["outcome"] == "dati" and "mai" in x["reason"]
    assert ip.liked_by(table[0], table[1], table[2], {"Prova – test": e})["E"] == 0


def test_excluded_sector_is_out_of_scope(table):
    e = _entry([{"metric": "roic", "op": ">=", "value": 0.05}], sectors=["Industrials"])
    d = _assess(table, e, "D")
    assert d["outcome"] == "fuori" and d["reason"].startswith("Esclusi Financials") and d["rules"] == []


def test_company_outside_index_is_ranked_hypothetically(table):
    e = _entry([{"metric": "pe", "op": "<=", "value": 15}], positions=1)
    z = _assess(table, e, "Z")          # fuori indice, ROIC 35%: davanti ad A (30%)
    assert z["outside_index"] and z["outcome"] == "compra" and z["ranking"]["position"] == 1


def test_price_that_passes_a_pe_rule_on_the_table(table):
    e = _entry([{"metric": "pe", "op": "<=", "value": 15}])
    d = _assess(table, e, "D")
    need = d["rules"][0]["need"]
    assert need["kind"] == "price" and need["price"] == pytest.approx(20.0 * 15 / 30, rel=1e-6)
    rank = _entry([{"metric": "pe", "op": "top_index", "value": 0.4}])   # 40% migliore per P/E
    r = _assess(table, rank, "D")["rules"][0]
    assert r["status"] == "failed" and r["need"]["kind"] == "price" and r["need"]["price"] < 20.0


def test_price_to_pass_is_verified_by_recomputing_the_metric(cfg, demo_db, universe):
    """Il prezzo trovato per una regola di P/E, rimesso nei prezzi, fa passare la regola ricalcolando la metrica con
    compute_metrics; poco sopra, no."""
    from modules.market_data import MarketData
    from modules.metrics import compute_metrics

    data = MarketData.from_db(demo_db, cfg, universe.tickers())
    tickers = universe.tickers()
    m = compute_metrics(data, tickers)
    t = m["pe"].dropna().loc[lambda s: s > 0].index[0]
    pe, price = float(m.at[t, "pe"]), float(m.at[t, "price"])
    rule = {"metric": "pe", "op": "<=", "value": round(pe * 0.8, 2)}
    a = ip.assess_investor("Prova – test", _entry([rule]), t, m, universe.sectors(), tickers)
    need = a["rules"][0]["need"]
    assert a["rules"][0]["status"] == "failed" and need["kind"] == "price"

    def pe_at(new_price):
        d2 = copy.copy(data)
        d2.close = data.close.copy()
        d2.close[t] = data.close[t] * (new_price / price)
        return float(compute_metrics(d2, [t]).at[t, "pe"])

    assert pe_at(need["price"] * 0.9999) <= rule["value"]
    assert pe_at(need["price"] * 1.01) > rule["value"]


def test_summary_families_obstacle_and_next_price(table):
    m, sectors, index = table
    pe15 = [{"metric": "pe", "op": "<=", "value": 15}]
    catalog = {
        "Munger – qualità": _entry([{"metric": "roic", "op": ">=", "value": 0.2}], positions=5),
        "Graham – value": _entry(pe15, positions=5),
        "Schloss – value": _entry(pe15, positions=5),
        "Altro – senza famiglia": _entry([{"metric": "gross_margin", "op": ">=", "value": 0.9}]),
    }
    p = ip.assess_panel("C", m, sectors, index, catalog)     # ROIC 25% sì, P/E 16,5 no
    s = p["summary"]
    assert [x["outcome"] for x in p["members"]] == ["compra", "quasi", "quasi", "no"]
    assert s["counts"]["compra"] == 1 and s["counts"]["quasi"] == 2
    assert s["obstacle"]["metric"] == "pe" and s["obstacle"]["count"] == 2
    assert s["next_price"]["investors"] == ["Graham", "Schloss"]
    assert s["next_price"]["price"] == pytest.approx(80 * 15 / 16.5, rel=1e-6)
    assert p["note"].startswith("Come le regole tradotte")
    p2 = ip.assess_panel("D", m, sectors, index, catalog)    # tutti no tranne nessuno: value contrario
    fam = {f["family"]: f["stance"] for f in p2["summary"]["families"]}
    assert fam["value profondo"] == "contraria" and fam["qualità"] == "contraria"


def test_real_catalog_runs_on_demo(cfg, demo_db, universe):
    """Con il catalogo vero: 20 investitori in ordine, esiti tra quelli previsti, JSON senza NaN."""
    from modules.market_data import MarketData
    from modules.metrics import compute_metrics
    from modules.strategies import CATALOG

    data = MarketData.from_db(demo_db, cfg, universe.tickers())
    m = compute_metrics(data, universe.tickers())
    p = ip.assess_panel(universe.tickers()[0], m, universe.sectors(), universe.tickers(), CATALOG)
    assert len(p["members"]) == len(ip.investor_catalog(CATALOG)) >= 18
    assert {x["outcome"] for x in p["members"]} <= set(ip.OUTCOMES)
    from api.serialize import jsonable
    json.dumps(jsonable(p), allow_nan=False)


def test_deterministic_across_hash_seeds():
    """Stesso risultato con PYTHONHASHSEED diversi (niente ordine di set che cambi gli esiti)."""
    script = (
        "import json, numpy as np, pandas as pd\n"
        "from modules import investor_panel as ip\n"
        "from modules.strategies import CATALOG\n"
        "from modules.metrics import METRICS\n"
        "rng = np.random.default_rng(7)\n"
        "idx = [f'T{i}' for i in range(60)]\n"
        "m = pd.DataFrame({k.key: rng.normal(1, 0.5, 60) for k in METRICS}, index=idx)\n"
        "m['price'] = 50.0; m['market_cap'] = 1e10; m['enterprise_value'] = 1.2e10\n"
        "sec = {t: ['Industrials', 'Financials', 'Health Care'][i % 3] for i, t in enumerate(idx)}\n"
        "out = {t: [(x['short'], x['outcome'], x['reason']) for x in ip.assess_panel(t, m, sec, idx, CATALOG)['members']]"
        " for t in idx[:6]}\n"
        "print(json.dumps(out, sort_keys=True))\n")
    runs = []
    for seed in ("1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        runs.append(subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True,
                                   check=True).stdout)
    assert runs[0] == runs[1] and len(runs[0]) > 100


def test_explore_metric_matches_the_panel(cfg, demo_db, universe):
    """La metrica di Esplora (liked_by) conta, per ogni azienda, gli investitori del tavolo con TUTTE le regole
    passate: stesso numero della scheda."""
    from modules.market_data import MarketData
    from modules.metrics import compute_metrics
    from modules.strategies import CATALOG

    data = MarketData.from_db(demo_db, cfg, universe.tickers())
    tickers = universe.tickers()
    m = compute_metrics(data, tickers)
    liked = ip.liked_by(m, universe.sectors(), tickers, CATALOG)
    for t in tickers[:8]:
        p = ip.assess_panel(t, m, universe.sectors(), tickers, CATALOG)
        all_passed = sum(1 for x in p["members"] if x["outcome"] != "fuori" and x["rules"] is not None
                         and all(r["status"] == "passed" for r in x["rules"]))
        assert liked[t] == all_passed, t


def test_european_sectors_and_outside_index_ranks(table):
    """Settori ICB delle europee tradotti in GICS; per un'azienda fuori indice la regola "tra i migliori" dà lo stesso
    esito nel tavolo e nella metrica di Esplora (calcolata senza un ciclo per azienda)."""
    m, sectors, index = table
    sectors = {**sectors, "Z": "Industrial Goods and Services"}            # ICB -> Industrials
    e = _entry([{"metric": "pe", "op": "top_index", "value": 0.5}, {"metric": "roic", "op": "top_sector", "value": 0.5}],
               sectors=["Industrials"])
    z = ip.assess_investor("Prova – test", e, "Z", m, sectors, index)
    assert z["outcome"] != "fuori" and [r["status"] for r in z["rules"]] == ["passed", "passed"]
    liked = ip.liked_by(m, sectors, index, {"Prova – test": e})
    for t in m.index:
        a = ip.assess_investor("Prova – test", e, t, m, sectors, index)
        assert liked[t] == int(a["outcome"] != "fuori" and all(r["status"] == "passed" for r in a["rules"])), t
