"""Sito pubblico statico (modules/public_site.py): dati in sola lettura, pagine, feed RSS, niente dati privati.
Nessuna rete: database demo dei test + dati d'esempio di tools/build_site.seed_demo."""
import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import pytest

from modules import formats, public_site
from tools.build_site import seed_demo

NOW = datetime(2026, 11, 17, 21, 30, tzinfo=public_site.TZ)
SECRET = "Mario Rossi mario.rossi@example.com"


@pytest.fixture
def site_cfg(cfg):
    cfg["sec"]["user_agent"] = SECRET
    cfg["public_site"] = {"base_url": "https://esempio.github.io/registro", "title": "Registro di prova"}
    return cfg


def _build(cfg, db, out, seed=True):
    if seed:
        seed_demo(db)
    ro = public_site.ReadOnlyDB(db.db_path)
    return public_site.build(cfg, ro, out, now=NOW)


# ------------------------------------------------------------------ formati
VALUES = [None, np.nan, float("inf"), -float("inf"), 0, -0.0, 0.0004, -0.0004, 1, -1, 12.345, -12.345, 999.5,
          1234.5, -1234.5, 99_999.99, 1.5e6, -2.25e9, 3.3e12, "abc", "12", True, pd.NaT,
          pd.Timestamp("2026-11-13"), "2026-11-13"]
KINDS = ["num", "int", "price", "pct", "pp", "mult", "money", "shares", "score", "text", "date", "date_short"]


def test_fmt_identico_a_quello_della_webapp():
    from ui import components

    assert components.fmt is formats.fmt and components.it_number is formats.it_number  # una sola definizione
    for v in VALUES:
        for kind in KINDS:
            for kw in ({}, {"signed": True}, {"arrow": True}, {"currency": "EUR"}, {"digits": 0}):
                assert isinstance(formats.fmt(v, kind, **kw), str), (v, kind, kw)


# ------------------------------------------------------------------ sito completo
def test_sito_dalla_demo(site_cfg, demo_db, tmp_path):
    out = tmp_path / "sito"
    res = _build(site_cfg, demo_db, out)
    assert res["companies"] >= 4 and res["alerts"] > 0
    for page in ["index.html", "portafoglio.html", "decisioni.html", "aziende.html", "fondi.html", "avvisi.html",
                 "metodo.html", "avvisi.xml", "static/style.css", "static/site.js", "aziende/AAPL.html",
                 "aziende/AAPL.xml", public_site.MARKER]:
        assert (out / page).exists(), page
    assert list((out / "fondi").glob("fondo-dimostrativo-a-*.html"))
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Registro di prova" in home and "Dati simulati" not in home  # banner solo sui dati della demo
    assert "Valore del portafoglio" in home and "€" in home
    dec = (out / "decisioni.html").read_text(encoding="utf-8")
    assert "Sbagliata" in dec or "Giusta" in dec  # esiti onesti, non solo le vittorie
    company = (out / "aziende/AAPL.html").read_text(encoding="utf-8")
    assert "Possiedo" in company and "Qualità del business" in company
    assert "non hanno battuto l'indice" in (out / "metodo.html").read_text(encoding="utf-8")


def test_contesto_serializzabile_e_modelli_rigorosi(site_cfg, demo_db, tmp_path):
    seed_demo(demo_db)
    ctx = public_site.collect(site_cfg, public_site.ReadOnlyDB(demo_db.db_path), now=NOW)
    json.dumps(ctx, allow_nan=False)
    trades = [t for t in ctx["portfolio"]["trades"] if t["kind"] in ("BUY", "SELL")]
    assert trades and all(t["amount_eur"] is not None for t in trades)  # cambi letti dai dati pubblici
    assert ctx["portfolio"]["kpi"]["twr"] is not None
    out = tmp_path / "rigoroso"
    out.mkdir()
    public_site.render(ctx, out, strict=True)  # un campo mancante nei modelli sarebbe un errore
    # grafico settimanale: mai la serie giornaliera dei prezzi
    dates = pd.to_datetime(ctx["portfolio"]["chart"]["points"]["dates"], dayfirst=True)
    assert (pd.Series(dates).diff().dropna() >= pd.Timedelta(days=5)).all()
    assert "price" not in json.dumps(ctx["companies"][0]["timeline"])


def test_feed_rss_valido(site_cfg, demo_db, tmp_path):
    out = tmp_path / "sito"
    _build(site_cfg, demo_db, out)
    for f in [out / "avvisi.xml", *(out / "aziende").glob("*.xml")]:
        root = ET.parse(f).getroot()
        assert root.tag == "rss" and root.get("version") == "2.0"
        for item in root.findall("./channel/item"):
            assert parsedate_to_datetime(item.findtext("pubDate")).tzinfo is not None
            assert item.find("guid").get("isPermaLink") == "false"
            assert item.findtext("link").startswith("https://esempio.github.io/registro/")
    guids = [i.findtext("guid") for i in ET.parse(out / "avvisi.xml").getroot().findall("./channel/item")]
    assert len(guids) == len(set(guids))
    # stessi identificativi a ogni nuova generazione (i lettori di feed non vedono doppioni)
    public_site.build(site_cfg, public_site.ReadOnlyDB(demo_db.db_path), out, now=NOW)
    again = [i.findtext("guid") for i in ET.parse(out / "avvisi.xml").getroot().findall("./channel/item")]
    assert again == guids


def test_nessun_dato_privato(site_cfg, demo_db, tmp_path):
    out = tmp_path / "sito"
    _build(site_cfg, demo_db, out)
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in {".html", ".xml", ".js", ".css"}:
            text = p.read_text(encoding="utf-8")
            assert SECRET not in text and "example.com" not in text and "/Users/" not in text, p
            assert str(tmp_path) not in text, p
    assert public_site.leaks(out, site_cfg) == []
    (out / "spia.html").write_text("scrivimi a qualcuno@esempio.it", encoding="utf-8")
    assert public_site.leaks(out, site_cfg)


def test_stati_vuoti_senza_dati_utente(site_cfg, demo_db, tmp_path):
    out = tmp_path / "vuoto"
    res = _build(site_cfg, demo_db, out, seed=False)
    assert res["companies"] == 0
    assert "Il portafoglio è ancora vuoto" in (out / "index.html").read_text(encoding="utf-8")
    assert "Nessuna decisione registrata" in (out / "decisioni.html").read_text(encoding="utf-8")
    assert "Nessun 13F disponibile" in (out / "fondi.html").read_text(encoding="utf-8")
    ET.parse(out / "avvisi.xml")
    ctx = public_site.collect(site_cfg, public_site.ReadOnlyDB(demo_db.db_path), now=NOW)
    (tmp_path / "rigoroso").mkdir()
    public_site.render(ctx, tmp_path / "rigoroso", strict=True)


def test_cartella_sostituita_solo_se_generata(site_cfg, demo_db, tmp_path):
    other = tmp_path / "altro"
    other.mkdir()
    (other / "importante.txt").write_text("non toccare", encoding="utf-8")
    with pytest.raises(ValueError):
        _build(site_cfg, demo_db, other)
    assert (other / "importante.txt").exists()
    out = tmp_path / "sito"
    _build(site_cfg, demo_db, out, seed=False)
    (out / "aziende").mkdir(exist_ok=True)
    (out / "aziende" / "VECCHIA.html").write_text("pagina di un'azienda non più seguita", encoding="utf-8")
    public_site.build(site_cfg, public_site.ReadOnlyDB(demo_db.db_path), out, now=NOW)
    assert not (out / "aziende" / "VECCHIA.html").exists()


def test_database_aperto_in_sola_lettura(demo_db):
    ro = public_site.ReadOnlyDB(demo_db.db_path)
    with pytest.raises(sqlite3.OperationalError):
        ro.add_to_watchlist(["AAPL"])


def test_13f_unita_sbagliate_corrette_solo_se_a_1000_volte():
    days = pd.to_datetime(["2026-09-29", "2026-09-30"])
    close = pd.DataFrame({"A": [10.0, 10.0], "B": [20.0, 20.0], "C": [40.0, 40.0]}, index=days)
    rows = []
    for acc, scale in (("migliaia", 0.001), ("dollari", 1.0), ("troppo", 1000.0), ("obbligazioni", 0.01)):
        for t, px in (("A", 10.0), ("B", 20.0), ("C", 40.0)):
            rows.append({"accession": acc, "report_date": days[1], "ticker": t, "shares": 1000.0,
                         "value": 1000.0 * px * scale})
    h = pd.DataFrame(rows)
    fixed, bad = public_site.fix_thousands(h, close)
    assert bad == {"migliaia", "troppo"}
    tot = fixed.groupby("accession")["value"].sum()
    assert tot["migliaia"] == pytest.approx(70_000) and tot["troppo"] == pytest.approx(70_000)
    assert tot["dollari"] == pytest.approx(70_000) and tot["obbligazioni"] == pytest.approx(700)


def test_rendimento_ponderato_nel_tempo():
    # compro 100, +10%, vendo metà (55 incassati), +10%: 21% (guadagno / investito netto darebbe 34%)
    value = pd.Series([100.0, 110.0, 55.0, 60.5])
    invested = pd.Series([100.0, 100.0, 45.0, 45.0])
    assert public_site.twr(value, invested) == pytest.approx(0.21)
    assert (60.5 - 45) / 45 == pytest.approx(0.344, abs=1e-3)


def test_git_e_cname_restano(site_cfg, demo_db, tmp_path):
    out = tmp_path / "pagine"
    (out / ".git").mkdir(parents=True)
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (out / "CNAME").write_text("registro.example.org", encoding="utf-8")
    _build(site_cfg, demo_db, out, seed=False)
    public_site.build(site_cfg, public_site.ReadOnlyDB(demo_db.db_path), out, now=NOW)
    assert (out / ".git" / "HEAD").exists() and (out / "CNAME").exists() and (out / "index.html").exists()
