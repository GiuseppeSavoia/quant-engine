"""API HTTP (api/app.py, fase 1 di docs/piano_frontend.md) su una copia privata del database demo: JSON valido per
ogni azienda (niente NaN), stessi numeri dei servizi usati dalla webapp, scritture dell'utente andata e ritorno,
nessun import di Streamlit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import API_PREFIX, create_app  # noqa: E402
from api.serialize import jsonable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(cfg, demo_db, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"  backup_dir: {tmp_path / 'b'}\nuniverse:\n  source: sp500\n", encoding="utf-8")
    with TestClient(create_app(str(config))) as c:
        yield c


def get(client, path, **kw):
    r = client.get(API_PREFIX + path, **kw)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def test_api_does_not_import_streamlit():
    code = ("import sys, api.app; bad = [m for m in sys.modules if m == 'streamlit' or m.startswith('streamlit.') "
            "or m == 'ui' or m.startswith('ui.')]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_jsonable_handles_engine_types():
    import numpy as np
    import pandas as pd

    df = pd.DataFrame({"a": [1.0, np.nan], "d": [pd.Timestamp("2026-11-23"), pd.NaT]}, index=["x", "y"])
    out = jsonable({"df": df, "inf": np.inf, "i": np.int64(3), "b": np.bool_(True), "arr": np.array([1.5, np.nan]),
                    "t": (1, 2), "ts": pd.Timestamp("2026-11-23 10:30")})
    assert out["df"] == [{"index": "x", "a": 1.0, "d": "2026-11-23"}, {"index": "y", "a": None, "d": None}]
    assert out["inf"] is None and out["i"] == 3 and out["b"] is True and out["arr"] == [1.5, None]
    assert out["t"] == [1, 2] and out["ts"] == "2026-11-23T10:30:00"
    json.dumps(out, allow_nan=False)


def test_health_search_and_unknown_ticker(client):
    assert get(client, "/health")["ok"] is True
    res = get(client, "/companies", params={"q": "d00", "limit": 5})
    assert 0 < len(res["results"]) <= 5 and all(r["ticker"].upper().startswith("D00") for r in res["results"])
    assert client.get(API_PREFIX + "/companies/NONESISTE").status_code == 404


def test_every_company_returns_valid_json(client, demo_db):
    """Tutte le aziende della demo: JSON senza NaN (Starlette lo rifiuterebbe), testata e giudizio coerenti."""
    from modules.dashboard_data import DashboardData

    tickers = [r["ticker"] for r in get(client, "/companies", params={"limit": 200})["results"]]
    assert len(tickers) >= 20
    d = client.app.state.contexts.get()
    assert isinstance(d, DashboardData)
    categories = set()
    for t in tickers:
        body = get(client, f"/companies/{t.lower()}")  # anche in minuscolo
        json.dumps(body, allow_nan=False)
        h = body["header"]
        assert h["ticker"] == t and len(h["key_metrics"]) == 3
        if body["verdict"] is not None:
            categories.add(body["verdict"]["category"])
            assert set(body["verdict"]["pillars"]) and body["verdict"]["label"]
    assert categories  # almeno un giudizio calcolato
    # stessi numeri del servizio usato dalla webapp
    t = tickers[0]
    v = d.company_verdict(t)
    body = get(client, f"/companies/{t}")
    assert body["verdict"]["category"] == v.category and body["header"]["price"] == pytest.approx(
        float(d.prices(t)["close"].iloc[-1]))


def test_financials_estimates_and_valuation(client):
    from modules import valuation_model as vm

    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    fin = get(client, f"/companies/{t}/financials")
    assert fin["empty"] is False and fin["rows"][0]["key"] == "revenue"
    assert len(fin["rows"][0]["values"]) == len(fin["periods"]) - fin["table_from"]
    assert {c["key"] for c in fin["charts"]} >= {"results", "margins"}
    assert all(len(s["values"]) == len(fin["periods"]) for c in fin["charts"] for s in c["series"])
    for view in ("quarterly_ttm", "quarterly_single"):
        assert get(client, f"/companies/{t}/financials", params={"view": view})["view"] == view
    assert client.get(API_PREFIX + f"/companies/{t}/financials", params={"view": "boh"}).status_code == 422
    assert get(client, f"/companies/{t}/estimates")["empty"] is True
    val = get(client, f"/companies/{t}/valuation")
    d = client.app.state.contexts.get()
    setup = d.valuation_setup(t)
    expected = vm.scenario_values(setup["base"], setup["scenarios"], setup["probabilities"])["expected"]
    assert val["expected"] == pytest.approx(expected)
    assert set(val["scenarios"]) == set(setup["scenarios"]) and "values" not in (val["simulation"] or {})
    # leve scelte dal client: più crescita nel Base = valore del Base più alto; probabilità riportate al 100%
    g = val["levers"]["Base"]["growth"]
    r = client.post(API_PREFIX + f"/companies/{t}/valuation",
                    json={"levers": {"Base": {"growth": g + 0.05}}, "probabilities": {"Base": 2, "Pessimista": 1,
                                                                                       "Ottimista": 1}})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["scenarios"]["Base"]["per_share"] > val["scenarios"]["Base"]["per_share"]
    assert sum(up["probabilities"].values()) == pytest.approx(1.0) and up["notes"]
    bad = client.post(API_PREFIX + f"/companies/{t}/valuation", json={"levers": {"Base": {"fantasia": 1}}})
    assert bad.status_code == 422 and "Leve sconosciute" in bad.json()["detail"]
    assert client.post(API_PREFIX + f"/companies/{t}/valuation",
                       json={"levers": {"Marte": {"growth": 0.1}}}).status_code == 422


def test_user_writes_round_trip(client):
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    assert client.put(API_PREFIX + f"/watchlist/{t.lower()}").json()["tickers"] == [t]
    assert get(client, "/watchlist")["tickers"] == [t]
    assert client.delete(API_PREFIX + f"/watchlist/{t}").json()["tickers"] == []
    r = client.put(API_PREFIX + f"/companies/{t}/price-target", json={"buy_below": 50, "sell_above": 40})
    assert r.status_code == 422 and "più bassa" in r.json()["detail"]
    assert client.put(API_PREFIX + f"/companies/{t}/price-target", json={}).status_code == 422
    assert client.put(API_PREFIX + f"/companies/{t}/price-target", json={"buy_below": -1}).status_code == 422
    ok = client.put(API_PREFIX + f"/companies/{t}/price-target", json={"buy_below": 40, "note": "prova"})
    assert ok.status_code == 200 and ok.json()["price_target"]["buy_below"] == 40.0
    assert get(client, f"/companies/{t}")["header"]["price_target"]["buy_below"] == 40.0
    assert client.delete(API_PREFIX + f"/companies/{t}/price-target").json()["price_target"] == {}


def test_server_refuses_network_host():
    from api.__main__ import main

    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def test_company_without_verdict_still_answers(client, monkeypatch):
    """Giudizio non calcolabile (es. dati insufficienti o errore): la scheda risponde lo stesso, con il motivo."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    d = client.app.state.contexts.get()

    def broken(ticker):
        raise RuntimeError("dati mancanti")

    monkeypatch.setattr(d, "company_verdict", broken)
    body = get(client, f"/companies/{t}")
    assert body["verdict"] is None and body["verdict_error"] == "dati mancanti"
    assert body["header"]["profile"] == "general"


def test_serves_compiled_web_app(cfg, demo_db, tmp_path):
    """L'API serve anche l'interfaccia compilata: file veri, index.html per gli indirizzi dell'app, mai fuori
    dalla cartella; gli indirizzi dell'API sconosciuti restano 404 JSON."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>QuantEngine</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "segreto.txt").write_text("no", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n",
                      encoding="utf-8")
    with TestClient(create_app(str(config), preload=False, web_dist=dist)) as c:
        assert "QuantEngine" in c.get("/").text
        assert "QuantEngine" in c.get("/azienda/AAPL").text           # indirizzo dell'app -> index.html
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert "immutable" in c.get("/assets/app.js").headers["cache-control"]   # file con impronta: cache lunga
        assert c.get("/azienda/AAPL").headers["cache-control"] == "no-cache"     # la pagina: mai in cache
        assert c.get("/../segreto.txt").text != "no" and c.get("/%2e%2e/segreto.txt").text != "no"
        assert c.get("/favicon.ico").status_code == 404                # file inesistente: 404, non la pagina
        r = c.get(API_PREFIX + "/non-esiste")
        assert r.status_code == 404 and "sconosciuto" in r.json()["detail"]
    with TestClient(create_app(str(config), preload=False, web_dist=tmp_path / "manca")) as c:
        assert c.get("/").status_code == 404


def test_valuation_details_and_save(client):
    """Quello che serve alla pagina della valutazione: etichette delle leve, controlli, confronto col settore, anno
    per anno, istogramma; salvare scrive il diario e il fair value dell'utente."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    val = get(client, f"/companies/{t}/valuation")
    keys = [m["key"] for m in val["lever_meta"]]
    assert keys[0] == "growth" and "failure_prob" in keys and all(m["label"] for m in val["lever_meta"])
    assert {m["key"] for m in val["lever_meta"] if m["pct"]} >= {"growth", "wacc"}
    assert len(val["year_table"]) == 10 and "fcff" in val["year_table"][0]
    h = val["simulation"]["histogram"]
    assert len(h["edges"]) == len(h["counts"]) + 1 and sum(h["counts"]) == val["simulation"]["n"]
    assert val["simulation"]["swing"][0]["label"]
    assert isinstance(val["plausibility"], list)
    r = client.post(API_PREFIX + f"/companies/{t}/valuation/save",
                    json={"levers": {"Base": {"growth": 0.08}}, "story": "storia di prova"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fair_value"] == pytest.approx(body["saved"]["expected_value"])
    assert get(client, f"/companies/{t}")["header"]["fair_value"] == pytest.approx(body["fair_value"])
    assert get(client, f"/companies/{t}/valuation")["saved"] is True   # si riparte dalla valutazione salvata
    assert client.post(API_PREFIX + f"/companies/{t}/valuation/save",
                       json={"levers": {"Base": {"x": 1}}}).status_code == 422


def test_my_companies_page(client):
    """Le mie aziende: vuota = suggerimenti; con aziende seguite = righe ordinate per giudizio, soglie e margini,
    avvisi (anche quelli registrati) che si possono segnare come visti."""
    empty = get(client, "/my-companies")
    assert empty["count"] == 0 and empty["rows"] == [] and len(empty["suggestions"]) > 0
    tickers = [s["ticker"] for s in empty["suggestions"][:2]]
    for t in tickers:
        client.put(API_PREFIX + f"/watchlist/{t}")
    client.put(API_PREFIX + f"/companies/{tickers[0]}/price-target", json={"buy_below": 1.0})
    d = client.app.state.contexts.get()
    ids = d.db.add_alerts([{"ticker": tickers[0], "code": "PX-", "key": "k1", "message": "price 1,234.5 below 2.5"}],
                          created_at="2026-11-20T10:00:00")
    page = get(client, "/my-companies")
    json.dumps(page, allow_nan=False)
    assert page["count"] == 2 and {r["ticker"] for r in page["rows"]} == set(tickers)
    row = next(r for r in page["rows"] if r["ticker"] == tickers[0])
    assert row["buy_below"] == 1.0 and row["buy_margin"] == pytest.approx(1.0 / row["price"] - 1)
    assert row["category"] and "pillar_quality" in row and row["price"] > 1   # il prezzo, non il pilastro Prezzo
    assert row["price"] == pytest.approx(float(d.prices(tickers[0])["close"].iloc[-1]), rel=1e-6)
    registered = [a for a in page["alerts"] if a["id"] == ids[0]]
    assert registered and registered[0]["unseen"] and registered[0]["tone"] == "pos"
    assert registered[0]["message"] == "price 1.234,5 below 2,5"       # numeri in italiano
    assert get(client, "/alerts", params={"state": "non_visti"})["alerts"][0]["id"] == ids[0]
    assert client.post(API_PREFIX + "/alerts/seen", json={"ids": ids}).json() == {"seen": 1}
    assert get(client, "/alerts", params={"state": "non_visti"})["alerts"] == []
    assert client.get(API_PREFIX + "/alerts", params={"state": "boh"}).status_code == 422


def test_explore(client):
    """Esplora: catalogo per i filtri, condizioni (anche "tra i migliori del settore"), giudizi, punteggi minimi,
    testo, ordinamento; condizioni non valide = 422; ricerche salvate andata e ritorno."""
    cat = get(client, "/explore/catalog")
    assert {m["key"] for m in cat["metrics"]} >= {"roic", "pe", "market_cap"} and cat["presets"] and cat["universes"][0] == "S&P 500"
    roic = next(m for m in cat["metrics"] if m["key"] == "roic")
    assert roic["unit"] == "%" and roic["factor"] == 100.0
    def post(body):
        r = client.post(API_PREFIX + "/explore", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        json.dumps(out, allow_nan=False)
        return out
    everything = post({})
    assert everything["count"] == everything["total"] > 10 and everything["sorted_by"] == "capitalizzazione"
    caps = [r["values"]["market_cap"] for r in everything["rows"] if r["values"]["market_cap"] is not None]
    assert caps == sorted(caps, reverse=True)
    some = post({"rules": [{"metric": "roic", "op": ">=", "value": 0.15}]})
    assert 0 < some["count"] < everything["count"] and some["sorted_by"] == "ROIC"
    assert all(r["values"]["roic"] >= 0.15 for r in some["rows"]) and some["rules"][0].startswith("ROIC")
    top = post({"rules": [{"metric": "roic", "op": "top_sector", "value": 0.25}]})
    assert 0 < top["count"] < everything["count"]
    cats = everything["verdict_counts"]
    first = next(iter(cats))
    only = post({"verdicts": [first], "sort": "verdict"})
    assert only["count"] == cats[first] and all(r["category"] == first for r in only["rows"])
    hi = post({"min_scores": {"quality": 0.9}})
    assert all(r["pillar_quality"] >= 0.9 for r in hi["rows"])
    t = everything["rows"][0]["ticker"]
    assert post({"text": t.lower()})["rows"][0]["ticker"] == t
    bad = client.post(API_PREFIX + "/explore", json={"rules": [{"metric": "boh", "op": ">=", "value": 1}]})
    assert bad.status_code == 422 and "sconosciuta" in bad.json()["detail"]
    assert client.post(API_PREFIX + "/explore", json={"universe": "Marte"}).status_code == 422
    assert client.put(API_PREFIX + "/screens/Vuota", json={}).status_code == 422
    ok = client.put(API_PREFIX + "/screens/Le mie qualità", json={"rules": [{"metric": "roic", "op": ">=", "value": 0.2}],
                                                                   "verdicts": [first]})
    assert ok.status_code == 200
    saved = get(client, "/explore/catalog")["saved"]
    assert saved[0]["name"] == "Le mie qualità" and saved[0]["filters"]["verdicts"] == [first]
    client.delete(API_PREFIX + "/screens/Le mie qualità")
    assert get(client, "/explore/catalog")["saved"] == []


def test_compare(client):
    """Confronta: da 2 a 5 aziende (anche separate da virgole, minuscole), giudizi, metriche con la migliore della
    riga coerente con il verso, grafici con le stesse date per ogni serie; errori chiari."""
    rows = get(client, "/companies", params={"limit": 3})["results"]
    a, b, c = (r["ticker"] for r in rows)
    out = get(client, "/compare", params={"t": f"{a.lower()},{b}", "years": 3})
    json.dumps(out, allow_nan=False)
    assert out["tickers"] == [a, b] and len(out["companies"]) == 2
    assert [p["key"] for p in out["companies"][0]["pillars"]] == ["quality", "solidity", "price", "flags", "insiders"]
    rows_all = [r for g in out["groups"] for r in g["rows"]]
    assert any(r["key"] == "roic" for r in rows_all)
    for r in rows_all:
        vals = {t: v for t, v in r["values"].items() if v is not None}
        if r["best"] and len(vals) >= 2:
            best = max(vals, key=vals.get) if r["better"] == "high" else min(vals, key=vals.get)
            assert vals[r["best"]] == vals[best], r["key"]
    price = next(ch for ch in out["charts"] if ch["key"] == "price")
    assert price["dates"] and all(len(v) == len(price["dates"]) for v in price["series"].values())
    assert get(client, "/compare", params=[("t", a), ("t", b), ("t", c)])["tickers"] == [a, b, c]
    assert client.get(API_PREFIX + "/compare", params={"t": a}).status_code == 422
    assert client.get(API_PREFIX + "/compare", params={"t": f"{a},NONESISTE"}).status_code == 422
    assert client.get(API_PREFIX + "/compare", params={"t": f"{a},{b}", "years": 7}).status_code == 422


def test_peers_and_documents(cfg, demo_db, tmp_path):
    """Concorrenti (gruppo, l'azienda evidenziata, mediane) e documenti: 8-K con le voci e il segno per quelle da
    guardare subito, notizie lette dalla copia salvata dei feed (cartella dei test, niente rete)."""
    from datetime import datetime, timezone

    import pandas as pd

    from modules import news as news_mod

    cache = tmp_path / "news"
    store = news_mod.NewsStore(cache / "notizie.json")
    now = datetime.now(timezone.utc)
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"news:\n  cache_dir: {cache}\n", encoding="utf-8")
    with TestClient(create_app(str(config))) as c:
        t = get(c, "/companies", params={"limit": 1})["results"][0]["ticker"]
        store.add([{"published": now, "first_seen": now, "source": "Fonte di prova", "feed": "prova", "group": "mercati",
                    "title": f"{t} annuncia i conti", "link": "https://example.com/a", "tickers": [t], "lang": "it"}])
        store.save()
        demo_db.add_events(pd.DataFrame([{"ticker": t, "accession": "0000000000-26-000001", "form": "8-K",
                                          "filed": "2026-11-20", "cik": 12345}]))
        demo_db.set_event_items("0000000000-26-000001", "4.02", pd.Timestamp("2026-11-20 16:05"))
        peers = get(c, f"/companies/{t}/peers")
        json.dumps(peers, allow_nan=False)
        assert peers["empty"] is False and peers["group"] and sum(r["me"] for r in peers["rows"]) == 1
        assert {v["key"] for v in peers["versus"]} == {col["key"] for col in peers["columns"]}
        docs = get(c, f"/companies/{t}/documents")
        json.dumps(docs, allow_nan=False)
        assert docs["news"][0]["title"].startswith(t) and docs["news"][0]["source"] == "Fonte di prova"
        f = docs["filings"][0]
        assert f["form"] == "8-K" and f["important"] is True and f["url"].startswith("https://www.sec.gov/")



def test_news_page(cfg, demo_db, tmp_path):
    """Notizie: flusso dalla copia salvata (filtri per le mie aziende, ticker, gruppo, lingua, testo; niente rete),
    8-K con le voci e il filtro "da guardare", account X salvati accanto al database e riquadro ufficiale."""
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    from modules import news as news_mod

    cache = tmp_path / "news"
    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"news:\n  cache_dir: {cache}\n  x_accounts: []\n", encoding="utf-8")
    with TestClient(create_app(str(config))) as c:
        empty = get(c, "/news")
        assert empty["saved_at"] is None and empty["items"] == [] and empty["groups"][0] == "mercati"
        a, b = [r["ticker"] for r in get(c, "/companies", params={"limit": 2})["results"]]
        now = datetime.now(timezone.utc)
        store = news_mod.NewsStore(cache / "notizie.json")
        store.add([
            {"published": now, "first_seen": now, "source": "Agenzia", "feed": "f1", "group": "mercati",
             "title": f"{a} alza le stime per l'anno", "link": "https://example.com/1", "tickers": [a], "lang": "it"},
            {"published": now - timedelta(hours=1), "first_seen": now, "source": "Wire", "feed": "f2",
             "group": "comunicati", "title": f"{b} announces a new buyback program", "link": "https://example.com/2",
             "tickers": [b], "lang": "en"}])
        store.save()
        allnews = get(c, "/news")
        json.dumps(allnews, allow_nan=False)
        assert allnews["total"] == 2 and allnews["saved_at"] and allnews["items"][0]["tickers"] == [a]
        assert get(c, "/news", params={"view": "mine"})["items"] == []          # nessuna azienda seguita
        c.put(f"{API_PREFIX}/watchlist/{b}")
        assert [i["tickers"] for i in get(c, "/news", params={"view": "mine"})["items"]] == [[b]]
        assert get(c, "/news", params={"view": "ticker", "ticker": a.lower()})["matched"] == 1
        assert get(c, "/news", params={"group": ["comunicati"]})["items"][0]["source"] == "Wire"
        assert get(c, "/news", params={"lang": "it", "q": "STIME"})["matched"] == 1
        assert get(c, "/news", params={"q": "nulla di simile"})["matched"] == 0
        assert c.get(API_PREFIX + "/news", params={"lang": "fr"}).status_code == 422

        demo_db.add_events(pd.DataFrame([{"ticker": a, "accession": "0000000000-26-000009", "form": "8-K",
                                          "filed": now.strftime("%Y-%m-%d"), "cik": 12345}]))
        demo_db.set_event_items("0000000000-26-000009", "2.02,4.02", pd.Timestamp.now())
        f = get(c, "/news/filings", params={"days": 3})
        json.dumps(f, allow_nan=False)
        assert f["companies"] == 1 and f["items"][0]["codes"] == ["2.02", "4.02"] and f["items"][0]["important"]
        assert any(x["code"] == "4.02" for x in f["item_labels"])
        assert get(c, "/news/filings", params={"item": ["5.02"]})["items"] == []
        assert get(c, "/news/filings", params={"mine": True})["items"] == []    # segue solo b
        assert c.get(API_PREFIX + "/news/filings", params={"days": 0}).status_code == 422

        assert get(c, "/news/x")["accounts"] == []
        assert c.post(API_PREFIX + "/news/x", json={"account": "https://x.com/ecb"}).json()["accounts"] == ["ecb"]
        dup = c.post(API_PREFIX + "/news/x", json={"account": "@ECB"})
        assert dup.status_code == 422 and "già" in dup.json()["detail"]
        assert c.post(API_PREFIX + "/news/x", json={"account": "nome non valido"}).status_code == 422
        assert (Path(cfg["data"]["db_path"]).parent / "preferenze_x.json").exists()
        page = c.get(API_PREFIX + "/news/x/ecb/embed", params={"dark": True})
        assert page.status_code == 200 and 'data-theme="dark"' in page.text and "platform.twitter.com" in page.text
        assert c.get(API_PREFIX + "/news/x/a%22b/embed").status_code == 422
        assert c.delete(API_PREFIX + "/news/x/ECB").json()["accounts"] == []



def test_strategies(cfg, demo_db, tmp_path):
    """Strategie: elenco, scheda senza risultato, backtest in coda (qui subito: coda in linea) con il confronto con
    l'indice a pesi uguali e le cautele, cosa terrebbe oggi, panoramica; strategie mie con nomi strani (spazi,
    accenti, "/"), anteprima, voto della checklist vietato, nome riservato. I backtest non cambiano la chiave dei dati."""
    from modules import app_context
    from modules.jobs import JobRunner

    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"universe:\n  source: sp500\nbacktest:\n  start: '2025-06-01'\n", encoding="utf-8")
    with TestClient(create_app(str(config), jobs=JobRunner(inline=True))) as c:
        items = get(c, "/strategies")["items"]
        names = [i["name"] for i in items]
        assert names[0] == "Indice a pesi uguali" and "Bassa volatilità" in names
        assert any(i["group"] == "Grandi investitori" for i in items)
        before = get(c, "/strategies/detail", params={"name": "Momentum"})
        assert before["report"] is None and before["job"] is None and before["rules"][0]["label"] == "Universo"
        assert c.get(API_PREFIX + "/strategies/detail", params={"name": "Nessuna"}).status_code == 404

        demo_db.log_run("20261123-000000", {"started_at": "2026-11-23"})  # come nei database veri: c'è un'esecuzione
        key = app_context.data_key(Path(cfg["data"]["db_path"]))
        job = c.post(API_PREFIX + "/strategies/run", json={"name": "Momentum"}).json()
        assert job["state"] == "done", job
        assert app_context.data_key(Path(cfg["data"]["db_path"])) == key     # niente ricaricamento dei dati
        page = get(c, "/strategies/detail", params={"name": "Momentum"})
        json.dumps(page, allow_nan=False)
        rep = page["report"]
        assert page["job"]["state"] == "done" and rep["stale"] is False
        assert {p["what"] for p in rep["periods"]} >= {"Strategia", "Indice a pesi uguali"}
        assert [s["key"] for s in rep["curves"]["series"]][:2] == ["strategy", "baseline"]
        assert rep["curves"]["series"][0]["values"][0] == 1 and len(rep["curves"]["dates"]) <= 500
        assert rep["variants"] >= 2 and rep["years"] and "turnover_annual" in rep["facts"]
        hold = get(c, "/strategies/holdings", params={"name": "Momentum"})
        assert hold["rows"] and 0 < hold["invested"] <= 1.0001
        over = get(c, "/strategies/overview")
        assert {r["name"] for r in over["rows"]} == {"Momentum", "Indice a pesi uguali"} and "Value" in over["missing"]

        opts = get(c, "/strategies/builder")
        assert "my_checklist" in opts["excluded"] and all(m["key"] != "my_checklist" for m in opts["rankable"])
        definition = {**opts["default"], "name": "Qualità / à là", "ranking": [{"metric": "roic", "weight": 1}],
                      "filters": [{"metric": "roic", "op": ">=", "value": 0.1}]}
        prev = c.post(API_PREFIX + "/strategies/preview", json={"definition": definition}).json()
        assert prev["ok"] and prev["universe"] > 0 and prev["held"] <= prev["passing"]
        bad = {**definition, "filters": [{"metric": "my_checklist", "op": ">=", "value": 3}]}
        assert c.post(API_PREFIX + "/strategies/preview", json={"definition": bad}).json()["ok"] is False
        r = c.post(API_PREFIX + "/strategies/mine", json={"definition": bad})
        assert r.status_code == 422 and "checklist" in r.json()["detail"]
        r = c.post(API_PREFIX + "/strategies/mine", json={"definition": {**definition, "name": "Momentum"}})
        assert r.status_code == 422 and "standard" in r.json()["detail"]
        assert c.post(API_PREFIX + "/strategies/mine", json={"definition": definition}).json()["name"] == "Qualità / à là"
        dup = c.post(API_PREFIX + "/strategies/mine", json={"definition": definition})
        assert dup.status_code == 422 and "già" in dup.json()["detail"]
        mine = get(c, "/strategies/detail", params={"name": "Qualità / à là"})
        assert mine["mine"] is True and mine["definition"]["filters"][0]["metric"] == "roic"
        renamed = {**definition, "name": "Qualità alta"}
        assert c.post(API_PREFIX + "/strategies/mine", json={"definition": renamed, "editing": "Qualità / à là"}).status_code == 200
        names = [i["name"] for i in get(c, "/strategies")["items"]]
        assert "Qualità alta" in names and "Qualità / à là" not in names
        assert c.delete(API_PREFIX + "/strategies/mine", params={"name": "Qualità alta"}).status_code == 200
        assert c.delete(API_PREFIX + "/strategies/mine", params={"name": "Momentum"}).status_code == 404



def test_pipeline(client):
    """Pipeline: aggiungere, spostare (con cosa manca per la fase), storico, togliere; fase sconosciuta rifiutata."""
    assert get(client, "/pipeline")["count"] == 0
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    r = client.put(f"{API_PREFIX}/companies/{t.lower()}/pipeline", json={"stage": "idea", "note": "da un 13F"})
    assert r.status_code == 200 and r.json()["stage"] == "idea" and r.json()["note"] == "da un 13F"
    info = client.put(f"{API_PREFIX}/companies/{t}/pipeline", json={"stage": "pronta"}).json()
    assert info["note"] == "da un 13F"                               # nota vuota: resta quella di prima
    pronta = next(s for s in info["stages"] if s["key"] == "pronta")
    assert "fair value" in pronta["missing"]
    board = get(client, "/pipeline")
    json.dumps(board, allow_nan=False)
    card = next(c for s in board["stages"] for c in s["cards"])
    assert card["ticker"] == t and card["stage"] == "pronta" and card["missing"] and card["price"] > 0
    assert [h["stage"] for h in board["history"]] == ["pronta", "idea"]
    assert client.put(f"{API_PREFIX}/companies/{t}/pipeline", json={"stage": "boh"}).status_code == 422
    assert client.delete(f"{API_PREFIX}/companies/{t}/pipeline").json()["stage"] is None
    assert get(client, "/pipeline")["count"] == 0


def test_company_actions_and_exports(client):
    """Scheda: stato "seguita" nella testata, esportazioni Excel e PDF come file da scaricare."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    assert get(client, f"/companies/{t}")["header"]["followed"] is False
    client.put(f"{API_PREFIX}/watchlist/{t}")
    assert get(client, f"/companies/{t}")["header"]["followed"] is True
    x = client.get(f"{API_PREFIX}/companies/{t}/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK" and "attachment" in x.headers["content-disposition"]
    p = client.get(f"{API_PREFIX}/companies/{t}/export.pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF" and p.headers["content-type"] == "application/pdf"



def test_company_more_functions(client):
    """Funzioni "Altro" della scheda: metriche con mediane, dividendi, insider e fondi, la mia ricerca (con voti della
    checklist e appunti), strategia automatica; JSON senza NaN."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    m = get(client, f"/companies/{t}/metrics")
    assert m["groups"] and all({"value", "sector_median", "rank_sector", "fmt"} <= set(r) for g in m["groups"] for r in g["rows"])
    assert all(g["group"] != "Dimensione" for g in m["groups"])
    dv = get(client, f"/companies/{t}/dividends")
    assert dv["empty"] or dv["years"][0]["year"] > 2000
    own = get(client, f"/companies/{t}/ownership")
    assert {"summary", "transactions", "funds"} <= set(own)
    res = get(client, f"/companies/{t}/research")
    assert res["fields"]["one_liner"] == "" and res["checklist"]
    q = res["checklist"][0]["id"]
    r = client.put(f"{API_PREFIX}/companies/{t}/research", json={
        "fields": {"one_liner": "Leader con margini stabili", "review_date": "2027-02-01"},
        "scores": {str(q): 4}, "thesis": "appunti"})
    assert r.status_code == 200
    after = r.json()
    assert after["fields"]["one_liner"] == "Leader con margini stabili" and after["fields"]["review_date"] == "2027-02-01"
    assert after["checklist"][0]["score"] == 4 and after["score"] is not None and after["thesis"] == "appunti"
    assert client.put(f"{API_PREFIX}/companies/{t}/research", json={"fields": {"boh": "x"}}).status_code == 422
    assert client.put(f"{API_PREFIX}/companies/{t}/research", json={"fields": {}, "scores": {str(q): 9}}).status_code == 422
    auto = get(client, f"/companies/{t}/auto")
    assert {"quality", "why", "dcf", "decisions", "versions"} <= set(auto)
    for path in ("metrics", "dividends", "ownership", "research", "auto"):
        json.dumps(get(client, f"/companies/{t}/{path}"), allow_nan=False)



def test_backtest_and_lab(cfg, demo_db, tmp_path):
    """Backtest (vuoto: i comandi per calcolarlo) e Laboratorio: opzioni, anteprima delle modifiche, esperimento in coda
    (qui subito) con la configurazione attuale come confronto, elenco, confronto, eliminazione."""
    from modules.jobs import JobRunner

    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"universe:\n  source: sp500\nbacktest:\n  start: '2025-06-01'\n", encoding="utf-8")
    with TestClient(create_app(str(config), jobs=JobRunner(inline=True))) as c:
        auto = get(c, "/auto-strategy")
        json.dumps(auto, allow_nan=False)
        assert auto["empty"] is True and "--strategy" in auto["command"]
        bt = get(c, "/backtest")
        assert bt["strategy"] is None and "--only-backtest" in bt["commands"]["backtest"]
        opts = get(c, "/lab/options")
        values = {p["key"]: p["value"] for p in opts["params"]}
        assert "backtest.use_exits" in values and opts["presets"]
        prev = c.post(API_PREFIX + "/lab/preview", json={"values": {**values, "backtest.use_exits": False}}).json()
        assert prev["ok"] and prev["overrides"] == {"backtest.use_exits": False}
        bad = c.post(API_PREFIX + "/lab/preview", json={"values": {"boh": 1}}).json()
        assert bad["ok"] is False
        job = c.post(API_PREFIX + "/lab/run", json={"values": {**values, "backtest.use_exits": False}, "name": "senza uscite"}).json()
        assert job["state"] == "done", job
        exps = get(c, "/lab/experiments")
        assert len(exps["rows"]) == 2 and exps["rows"][0]["name"] == "senza uscite" and exps["rows"][1]["baseline"]
        ids = [r["id"] for r in exps["rows"]]
        comp = get(c, "/lab/compare", params={"id": ids})
        json.dumps(comp, allow_nan=False)
        assert len(comp["rows"]) == 2 and comp["rows"][0]["yaml"] and comp["curves"]["series"][-1]["key"] == "benchmark"
        assert c.delete(f"{API_PREFIX}/lab/experiments/{ids[0]}").status_code == 200
        assert len(get(c, "/lab/experiments")["rows"]) == 1



def test_funds_quality_and_manual_data(client):
    """Fondi (vuoti nella demo), qualità dei dati, dati a mano (tabella, salvataggio con i derivati, elenco,
    eliminazione), aziende fuori indice."""
    assert get(client, "/funds")["empty"] is True
    q = get(client, "/quality")
    json.dumps(q, allow_nan=False)
    assert {"filings", "summary", "issues"} <= set(q)
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    page = get(client, f"/manual/{t}")
    json.dumps(page, allow_nan=False)
    assert page["periods"] and any(i["key"] == "revenue" for i in page["items"]) and page["manual"] == []
    period = page["periods"][-1]
    r = client.post(f"{API_PREFIX}/manual/{t}", json={"changes": {period: {"revenue": 123456.0}}})
    assert r.status_code == 200 and r.json()["saved"] >= 1
    after = get(client, f"/manual/{t}")
    rev = next(i for i in after["items"] if i["key"] == "revenue")
    assert rev["values"][after["periods"].index(period)] == 123456.0
    row = next(m for m in after["manual"] if m["metric"] == "revenue")
    assert client.post(f"{API_PREFIX}/manual/{t}", json={"changes": {period: {"boh": 1}}}).status_code == 422
    assert client.post(f"{API_PREFIX}/manual/{t}", json={"changes": {"2099-12-31": {"revenue": 1}}}).status_code == 422
    d = client.post(f"{API_PREFIX}/manual/{t}/delete", json={"rows": [
        {"period_end": row["period_end"][:10], "metric": "revenue", "filed": row["filed"][:10]}]})
    assert d.status_code == 200
    assert all(m["metric"] != "revenue" for m in get(client, f"/manual/{t}")["manual"])
    assert client.post(f"{API_PREFIX}/custom-companies", json={"symbol": "eni.mi", "name": "Eni", "currency": "EUR"}).json()["ticker"] == "ENI.MI"
    cc = get(client, "/custom-companies")["companies"]
    assert cc[0]["ticker"] == "ENI.MI" and cc[0]["has_prices"] is False
    assert client.post(f"{API_PREFIX}/custom-companies", json={"symbol": "A B"}).status_code == 422
    client.delete(f"{API_PREFIX}/custom-companies/ENI.MI")
    assert get(client, "/custom-companies")["companies"] == []



def test_company_investors_panel(client):
    """Tavolo dei grandi investitori: un esito per investitore, sintesi, nota fissa; la metrica derivata in Esplora
    filtra ma non entra nelle strategie."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    p = get(client, f"/companies/{t}/investors")
    json.dumps(p, allow_nan=False)
    assert len(p["members"]) >= 18 and p["note"].startswith("Come le regole") and p["summary"]["lines"]
    assert {m["outcome"] for m in p["members"]} <= set(p["outcomes"]) and p["data_as_of"]
    rule = {"metric": "investors_liking", "op": ">=", "value": 1}
    r = client.post(f"{API_PREFIX}/explore", json={"rules": [rule]})
    assert r.status_code == 200 and r.json()["count"] >= 0
    bad = client.post(f"{API_PREFIX}/strategies/preview", json={"definition": {
        **get(client, "/strategies/builder")["default"], "filters": [rule]}}).json()
    assert bad["ok"] is False



def test_command_bar_market_strip_and_sparklines(client):
    """Comandi interpretati dal motore (modules/commands.py), striscia di mercato dalle chiusure salvate, minigrafici
    a un anno nelle tabelle (Esplora), data dell'ultimo aggiornamento in /health."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    c = get(client, "/command", params={"q": f"{t.lower()} fa"})
    assert c["page"] == "azienda" and c["tickers"] == [t] and c["section"] == "FA" and c["error"] is None
    assert get(client, "/command", params={"q": "esplora"})["page"] == "esplora"
    assert get(client, "/command", params={"q": "zzzzz"})["error"]
    strip = get(client, "/market-strip")
    json.dumps(strip, allow_nan=False)
    keys = [i["key"] for i in strip["items"]]
    assert "EW" in keys and "BREADTH" in keys and strip["as_of"]
    ew = next(i for i in strip["items"] if i["key"] == "EW")
    assert len(ew["spark"]) > 10 and ew["change"] is not None
    rows = client.post(f"{API_PREFIX}/explore", json={}).json()["rows"]
    assert rows and all(r["spark"] is None or len(r["spark"]) >= 2 for r in rows) and any(r["spark"] for r in rows)
    assert "last_update" in get(client, "/health")


def test_today(client):
    """Oggi: le mie aziende (seguite) con prezzo dal database, avvisi (non visti in cima, movimenti a parte),
    movimenti dell'indice ordinati, borse; nessun dato dal vivo."""
    empty = get(client, "/today")
    json.dumps(empty, allow_nan=False)
    assert empty["mine"] == [] and empty["source"].startswith("chiusure del")
    up, down = empty["movers"]["up"], empty["movers"]["down"]
    assert all(r["change_pct"] > 0 for r in up) and all(r["change_pct"] < 0 for r in down)
    assert [r["change_pct"] for r in up] == sorted([r["change_pct"] for r in up], reverse=True)
    assert empty["exchanges"] and {"name", "state", "until"} <= set(empty["exchanges"][0])
    t = up[0]["ticker"] if up else get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    client.put(API_PREFIX + f"/watchlist/{t}")
    d = client.app.state.contexts.get()
    d.db.add_alerts([{"ticker": t, "code": "MOV", "key": "m", "message": "+12.5% in a month", "data": {"ret_1m": 0.125}},
                     {"ticker": t, "code": "PX-", "key": "p", "message": "below 1,000.5"}], created_at="2026-11-20T10:00:00")
    page = get(client, "/today")
    json.dumps(page, allow_nan=False)
    row = page["mine"][0]
    assert row["ticker"] == t and row["unseen"] == 2 and row["last"] == pytest.approx(float(d.prices(t)["close"].iloc[-1]))
    assert row["category"] and page["unseen"] == 2
    assert [a["code"] for a in page["alerts"]] == ["PX-"] and page["alerts"][0]["message"] == "below 1.000,5"
    assert [a["code"] for a in page["moves"]] == ["MOV"] and page["moves"][0]["tone"] == "pos"


def test_price_chart(client):
    """Grafico del prezzo: barre allineate (tutte le serie lunghe quanto le date), candele fino a 1A e linea sui
    periodi lunghi in Auto, mai più di ~800 punti, riferimenti dell'utente, confronto in base 100, errori chiari."""
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    client.put(API_PREFIX + f"/companies/{t}/price-target", json={"buy_below": 1e7})  # ben fuori scala
    one = get(client, f"/companies/{t}/price", params={"period": "1A", "bench": True})
    json.dumps(one, allow_nan=False)
    n = len(one["dates"])
    assert one["candles"] is True and one["freq"] == "D" and 200 <= n <= 260
    for k in ("open", "high", "low", "close", "volume", "change", "vol_ma", "sma50", "sma200", "drawdown"):
        assert len(one[k]) == n, k
    assert len(one["bench"]["stock"]) == n and one["bench"]["stock"][0] == pytest.approx(100.0)
    assert one["summary"]["high"] >= one["summary"]["low"] and one["notes"]
    ref = next(r for r in one["references"] if r["label"] == "Compra sotto")
    assert ref["value"] == 1e7 and ref["in_range"] is False and any("fuori scala" in x.lower() for x in one["notes"])
    long = get(client, f"/companies/{t}/price", params={"period": "MAX", "style": "Linea"})
    assert long["candles"] is False and len(long["dates"]) <= 820
    assert client.get(API_PREFIX + f"/companies/{t}/price", params={"period": "2Z"}).status_code == 422
    assert client.get(API_PREFIX + f"/companies/{t}/price", params={"style": "Barre"}).status_code == 422


def test_journal(client):
    """Diario: vuoto, nuova decisione (prezzo = ultima chiusura, revisione fra 6 mesi), errori chiari, revisione
    con un giudizio ammesso, eliminazione; esito contro l'indice solo dopo 3 mesi."""
    empty = get(client, "/journal")
    assert empty["entries"] == [] and empty["summary"]["decisions"] == 0 and len(empty["actions"]) == 4
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    bad = client.post(API_PREFIX + "/journal", json={"ticker": t, "action": "BUY", "reason": "  "})
    assert bad.status_code == 422 and "perché" in bad.json()["detail"]
    assert client.post(API_PREFIX + "/journal", json={"ticker": t, "action": "HODL", "reason": "x"}).status_code == 422
    r = client.post(API_PREFIX + "/journal", json={"ticker": t.lower(), "action": "BUY", "reason": "Margini in crescita",
                                                   "change_mind": "margine sotto il 10%"})
    assert r.status_code == 200
    dec = r.json()["id"]
    page = get(client, "/journal")
    json.dumps(page, allow_nan=False)
    e = page["entries"][0]
    d = client.app.state.contexts.get()
    assert e["id"] == dec and e["ticker"] == t and e["action_label"] == "Compro" and e["right"] is None
    assert e["price"] == pytest.approx(float(d.data.close[t].dropna().iloc[-1]))
    assert e["review_due"] is False and e["review_date"] > e["date"]
    assert client.post(API_PREFIX + f"/journal/{dec}/review", json={"verdict": "Forse"}).status_code == 422
    ok = client.post(API_PREFIX + f"/journal/{dec}/review", json={"verdict": "Troppo presto", "note": "presto per dirlo"})
    assert ok.status_code == 200 and get(client, "/journal")["entries"][0]["verdict"] == "Troppo presto"
    assert client.post(API_PREFIX + "/journal/999999/review", json={"verdict": "Avevo torto"}).status_code == 422
    client.delete(API_PREFIX + f"/journal/{dec}")
    assert get(client, "/journal")["entries"] == []


def test_method(client):
    """Il mio metodo: profilo (scelte ammesse), anteprima di una formula (anche sbagliata), metrica personale creata
    ed eliminata, domande della checklist aggiunte, modificate e tolte."""
    page = get(client, "/method")
    json.dumps(page, allow_nan=False)
    assert page["styles"] and page["horizons"] and page["checklist"] and any(n["key"] == "roic" for n in page["formula_names"])
    ok = client.put(API_PREFIX + "/method/profile", json={"styles": [page["styles"][0]], "horizon": page["horizons"][0],
                                                          "positions": 15, "principles": "ROIC alto"})
    assert ok.status_code == 200 and ok.json()["positions"] == 15
    assert client.put(API_PREFIX + "/method/profile", json={"styles": ["Astrologia"]}).status_code == 422
    prev = client.post(API_PREFIX + "/method/metrics/preview", json={"formula": "roic - 0.08", "better": "high"})
    assert prev.status_code == 200
    p = prev.json()
    assert 0 < p["computable"] <= p["total"] and p["p10"] <= p["median"] <= p["p90"] and len(p["best"]) <= 10
    assert p["best"][0]["value"] >= p["best"][-1]["value"]
    bad = client.post(API_PREFIX + "/method/metrics/preview", json={"formula": "__import__('os')"})
    assert bad.status_code == 422
    made = client.post(API_PREFIX + "/method/metrics", json={"label": "ROIC oltre l'8%", "formula": "roic - 0.08",
                                                            "fmt": "pct", "better": "high"})
    assert made.status_code == 200
    key = made.json()["key"]
    assert any(m["key"] == key for m in get(client, "/method")["custom_metrics"])
    assert client.delete(API_PREFIX + f"/method/metrics/{key}").json()["used_by"] == []
    assert client.post(API_PREFIX + "/method/checklist", json={"question": "  "}).status_code == 422
    assert client.post(API_PREFIX + "/method/checklist", json={"question": "Capisco il business?", "weight": 2}).status_code == 200
    items = get(client, "/method")["checklist"]
    mine = next(i for i in items if i["question"] == "Capisco il business?")
    assert mine["weight"] == 2.0
    assert client.put(API_PREFIX + f"/method/checklist/{mine['id']}", json={"question": "Capisco davvero il business?",
                                                                           "weight": 9}).status_code == 422
    client.delete(API_PREFIX + f"/method/checklist/{mine['id']}")
    assert all(i["id"] != mine["id"] for i in get(client, "/method")["checklist"])


def test_portfolio(client):
    """Portafoglio: vuoto, acquisto (il perché va nel diario), vendita oltre le azioni rifiutata con il motivo, posizioni
    con valore e giudizio, composizione che somma a 1, operazioni, eliminazione."""
    assert get(client, "/portfolio")["empty"] is True
    t = get(client, "/companies", params={"limit": 1})["results"][0]["ticker"]
    d = client.app.state.contexts.get()
    day = str(d.data.close[t].dropna().index[-60].date())
    bad = client.post(API_PREFIX + "/portfolio/transactions", json={"ticker": t, "kind": "BUY", "date": day,
                                                                    "quantity": 0, "price": 10})
    assert bad.status_code == 422 and "quantità" in bad.json()["detail"]
    buy = client.post(API_PREFIX + "/portfolio/transactions", json={"ticker": t, "kind": "BUY", "date": day, "quantity": 10,
                                                                    "price": 100, "currency": "EUR",
                                                                    "reason": "Margini in crescita"})
    assert buy.status_code == 200, buy.text
    sell = client.post(API_PREFIX + "/portfolio/transactions", json={"ticker": t, "kind": "SELL", "date": day,
                                                                     "quantity": 50, "price": 110, "currency": "EUR"})
    assert sell.status_code == 422 and "possiedi" in sell.json()["detail"]
    page = get(client, "/portfolio")
    json.dumps(page, allow_nan=False)
    assert page["empty"] is False and page["positions"][0]["ticker"] == t and page["positions"][0]["known"] is True
    assert page["positions"][0]["quantity"] == 10 and page["positions"][0]["category"]
    for kind in ("Settore", "Valuta"):
        assert sum(c["share"] for c in page["composition"] if c["kind"] == kind) == pytest.approx(1.0)
    assert page["transactions"][0]["kind_label"] == "Acquisto" and page["summary"]["invested"] > 0
    assert any(e["reason"] == "Margini in crescita" for e in get(client, "/journal")["entries"])
    client.delete(API_PREFIX + f"/portfolio/transactions/{buy.json()['id']}")
    assert get(client, "/portfolio")["empty"] is True
    # in dollari senza il cambio di oggi nel database (la demo non ha EURUSD=X): valore vuoto e un avviso che lo dice
    usd = client.post(API_PREFIX + "/portfolio/transactions", json={"ticker": t, "kind": "BUY", "date": day, "quantity": 1,
                                                                    "price": 100, "currency": "USD", "fx": 1.08})
    page = get(client, "/portfolio")
    assert page["summary"]["value"] is None and any("Cambio mancante" in n["title"] for n in page["notes"])
    client.delete(API_PREFIX + f"/portfolio/transactions/{usd.json()['id']}")
