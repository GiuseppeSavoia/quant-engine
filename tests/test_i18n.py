"""Inglese dell'interfaccia (modules/i18n): formati, traduzione all'uscita dell'API, catalogo coerente e completo sulle
frasi che il motore produce con i dati simulati. L'italiano resta la lingua del motore (nessun test esistente cambia).
"""
import json
import re
from collections import defaultdict

from modules import i18n
from modules.formats import en_value, fmt


def test_english_formats_and_conversion_from_italian():
    cases = [(6.63e10, "money", {"currency": "USD"}, "$66.3B"), (1234.5, "num", {}, "1,234.50"),
             (-0.123, "pct", {}, "−12.3%"), (0.012, "pp", {}, "+1.2 pp"), (12.3, "mult", {}, "12.3x"),
             (1234.5, "money", {"currency": "CHF"}, "CHF 1,234"), ("2026-11-13", "date", {}, "13 Nov 2026")]
    for v, kind, opt, en in cases:
        assert fmt(v, kind, lang="en", **opt) == en
        assert en_value(fmt(v, kind, **opt)) == en          # dal testo italiano allo stesso risultato
    assert en_value("set 26") == "Sep 26" and en_value("0.93") is None and en_value("AAPL") is None


def test_translate_whole_strings_only(monkeypatch, tmp_path):
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "x.json").write_text(json.dumps({
        "Oggi": "Today", "{0} regole su {1}": "{0} of {1} rules", "Prezzo {0} sopra {1}": "Price {0} above {1}"}),
        encoding="utf-8")
    monkeypatch.setattr(i18n, "CATALOG_DIR", tmp_path)
    i18n.reload()
    try:
        assert i18n.translate("Oggi") == "Today"
        assert i18n.translate("Prezzo 66,3 mld $ sopra 12,3%") == "Price $66.3B above 12.3%"
        assert i18n.translate("2 regole su 3 · Oggi") == "2 of 3 rules · Today"
        assert i18n.translate("Oggi è un altro giorno") == "Oggi è un altro giorno"     # mai pezzi di frase
        assert i18n.translate_obj({"ticker": "Oggi", "label": "Oggi"}) == {"ticker": "Oggi", "label": "Today"}
        assert i18n.translate("Oggi", "it") == "Oggi"
    finally:
        monkeypatch.undo()
        i18n.reload()


def test_catalog_files_agree_and_keep_placeholders():
    seen: dict[str, tuple[str, str]] = {}
    conflicts, wrong = [], []
    for path in sorted((i18n.CATALOG_DIR / "en").glob("*.json")):
        for it, en in json.loads(path.read_text(encoding="utf-8")).items():
            if it in seen and seen[it][1] != en:
                conflicts.append(f"{it!r}: {seen[it][0]} / {path.name}")
            seen[it] = (path.name, en)
            ph = lambda s: sorted(re.findall(r"\{\d+\}", s))  # noqa: E731
            if ph(it) != ph(en):
                wrong.append(it)
    assert conflicts == [] and wrong == []


def test_api_translates_engine_text_and_leaves_data(cfg, demo_db, tmp_path):
    from tests.test_api import API_PREFIX, TestClient, create_app

    config = tmp_path / "config.yaml"
    config.write_text(f"data:\n  db_path: {cfg['data']['db_path']}\n  facts_db_path: {tmp_path / 'facts.db'}\n"
                      f"  backup_dir: {tmp_path / 'b'}\nuniverse:\n  source: sp500\n", encoding="utf-8")
    with TestClient(create_app(str(config))) as c:
        t = c.get(f"{API_PREFIX}/companies?limit=1").json()["results"][0]["ticker"]
        it = c.get(f"{API_PREFIX}/companies/{t}").json()
        en = c.get(f"{API_PREFIX}/companies/{t}", headers={"X-Lang": "en"}).json()
        assert en["header"]["ticker"] == it["header"]["ticker"] and en["header"]["name"] == it["header"]["name"]
        assert set(en) == set(it)                                   # stessa struttura, stessi codici
        assert en["verdict"]["label"] != it["verdict"]["label"] or not it["verdict"]["label"]


def test_engine_text_seen_by_the_web_is_all_translated():
    """Ogni frase del motore che arriva all'interfaccia web (dati simulati) ha la sua traduzione."""
    from tools.i18n_coverage import uncovered

    found = uncovered(4)
    by_path = defaultdict(list)
    for text, where in found.items():
        by_path[where[0]].append(text)
    assert not found, {k: v[:5] for k, v in by_path.items()}
