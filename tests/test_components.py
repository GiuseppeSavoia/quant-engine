"""Componenti dell'interfaccia (ui/components.py) e cornice: numeri in italiano, mai None/nan a schermo, HTML dei
componenti, CSS valido per st.html, SEGUI senza cambio di pagina."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui import components as C  # noqa: E402


@pytest.mark.parametrize("value, kind, kw, expected", [
    (60.28, "price", {}, "60,28"),
    (60.28, "price", {"currency": "USD"}, "60,28 $"),
    (1234.5, "num", {"digits": 1}, "1.234,5"),
    (0.123, "pct", {}, "12,3%"),
    (0.016, "pct", {"signed": True}, "+1,6%"),
    (-0.106, "pct", {"arrow": True}, "▼ −10,6%"),
    (0.0, "pct", {"arrow": True}, "■ 0,0%"),
    (-0.0004, "mult", {}, "0,0x"),             # niente "-0,0x"
    (12.34, "mult", {}, "12,3x"),
    (66.3e9, "money", {"currency": "USD"}, "66,3 mld $"),
    (1589.3e9, "money", {}, "1,6 T"),
    (12.5e6, "money", {"currency": "EUR"}, "12,5 mln €"),
    (1234, "money", {"currency": True}, "1.234 $"),
    (1234567, "int", {}, "1.234.567"),
    (0.012, "pp", {}, "+1,2 punti"),
    (0.78, "score", {}, "0,78"),
    (pd.Timestamp("2026-11-13"), "date", {}, "13/11/2026"),
    (pd.Timestamp("2026-11-13"), "date_short", {}, "13/11/26"),
])
def test_fmt_italian(value, kind, kw, expected):
    assert C.fmt(value, kind, **kw) == expected


@pytest.mark.parametrize("missing", [None, np.nan, float("inf"), pd.NA, pd.NaT, "n/d"])
def test_missing_is_dash(missing):
    for kind in ("num", "pct", "mult", "money", "price", "date"):
        assert C.fmt(missing, kind) == "–"
    assert 'class="qe-na"' in C.fmt_html(missing, "pct")


def test_fmt_html_colors_only_with_sign():
    assert C.fmt_html(0.05, "pct", arrow=True) == '<span class="pos">▲ +5,0%</span>'
    assert C.fmt_html(-0.05, "pct", signed=True) == '<span class="neg">−5,0%</span>'
    assert C.fmt_html(0.05, "pct") == "5,0%"


def test_fmt_metric_uses_catalog_kind():
    assert C.fmt_metric("pe", 12.34) == "12,3x"
    assert C.fmt_metric("market_cap", 66.3e9) == "66,3 mld $"
    assert C.fmt_metric("pe", None) == "–"


def test_fmt_table_strings_and_headers():
    df = pd.DataFrame({"ticker": ["AAPL", "MSFT"], "pe": [12.34, None], "chg": [0.0155, -0.2]})
    out = C.fmt_table(df, {"ticker": ("Ticker", "text"), "pe": ("P/E", "mult"),
                           "chg": ("Oggi", "pct", {"arrow": True}), "missing": "num"})
    assert list(out.columns) == ["Ticker", "P/E", "Oggi", "missing"]
    assert out["P/E"].tolist() == ["12,3x", "–"]
    assert out["Oggi"].tolist() == ["▲ +1,6%", "▼ −20,0%"]
    assert out["missing"].tolist() == ["–", "–"]
    text = out.to_string()
    assert "None" not in text and "nan" not in text


def test_dense_table_html_links_missing_and_legacy_formats():
    df = pd.DataFrame({"ticker": ["AAPL", "MSFT"], "name": ["Apple Inc.", "Microsoft"], "last": [60.28, np.nan],
                       "chg": [0.0155, -0.0035], "ratio": [-0.0004, 2.5]})
    html = C.dense_table_html(df, {"ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"),
                                   "last": ("Ultimo", "{:,.2f}"), "chg": ("Oggi", "signed"),
                                   "ratio": ("Debito/EBITDA", "{:.1f}x")}, highlight=["MSFT"])
    assert 'href="./azienda?t=AAPL"' in html and 'target="_self"' in html
    assert '<td class="na">–</td>' in html                       # prezzo mancante
    assert "60,28" in html and "▲ +1,55%" in html and "▼ −0,35%" in html
    assert ">0,0x<" in html and "-0" not in html and "−0,0x" not in html
    assert "<tr class=me>" in html                                 # riga evidenziata
    assert 'class="t fill"' in html                                # il nome si accorcia
    assert "None" not in html and "nan" not in html


def test_badges_notices_empty_state_and_head():
    assert C.badge("MRG margine in calo", "warn", "!") == '<span class="qe-badge warn">! MRG margine in calo</span>'
    assert "&lt;b&gt;" in C.badge("<b>", "neg")                    # testo sempre escapato
    assert 'class="qe-notice neg"' in C.notice_html("Altman sotto 1,8", "neg", title="Allarme:")
    e = C.empty_state_html("Nessun insider", "Non ancora scaricati", details="main.py --insiders")
    assert "<details>" in e and "Nessun insider" in e
    h = C.head_html("AAPL", "Apple Inc.", meta="Information Technology", price=60.28, change=0.0155,
                    source="chiusura 02/11/26", kv=[("P/E", C.fmt_html(12.3, "mult")),
                                                     ("52 sett.", C.range_html(52.91, 69.07, 60.28))])
    assert "60,28 $" in h and "▲ +1,55%" in h and "<dt>P/E</dt><dd>12,3x</dd>" in h and "left:46%" in h
    assert C.breadcrumb_html(["Aziende", "Scheda", "AAPL"]).endswith("<b>AAPL</b></nav>")
    assert "qe-kv big" in C.result_row_html([("Valore atteso", "504,00 $")], title="Valutazione")


def test_verdict_components_match_verdict_module():
    from modules import verdict

    assert set(C.VERDICT_STYLE) == set(verdict.ORDER)
    ev = [SimpleNamespace(label="ROIC", shown="25%", reference="≥ 15%", outcome="favorevole", text="alto"),
          SimpleNamespace(label="Insider", shown="–", reference="", outcome="mancante", text="dati assenti")]
    pillar = SimpleNamespace(key="quality", label="Qualità del business", score=0.78, grade="Eccellente",
                             evidence=ev, missing=ev[1:])
    v = SimpleNamespace(category="candidata", label="Candidata all'acquisto", rule="qualità ≥ 0,6", profile_label="Banca",
                        as_of=pd.Timestamp("2026-11-02"), disclaimer="non è una raccomandazione", alarms=["Beneish"],
                        checks=[], pillars={"quality": pillar}, changes=["a", "b", "c", "d", "e"], data_issues=[])
    card = C.verdict_card_html(v, value_html="Prezzo 60,28")
    assert "qe-verdict pos" in card and "✓" in card and "Allarme:" in card and "altri 1" in card
    assert "1 prova su 2" in card and "0,78" in card and "Banca · al 02/11/26" in card
    assert "dati insufficienti" in C.pillar_bar_html(None, label="Chi è dentro", context=True)
    table = C.evidence_table_html(pillar)
    assert table.index("ROIC") < table.index("Insider") and 'class="miss"' in table
    assert C.verdict_badge("evitare").startswith('<span class="qe-badge neg"')


def test_changes_and_alerts():
    html = C.change_rows_html([{"text": "Ricavi +4,1%", "favorable": True},
                               {"text": "Margine −1,2 punti", "favorable": False},
                               {"text": "neutra", "favorable": None}], price_line="Prezzo −8% dal deposito")
    assert "Ricavi" in html and "Margine" in html and "neutra" not in html and "Prezzo" in html
    row = C.alert_row_html({"ticker": "AAPL", "code": "MOV", "message": "−12% in un mese",
                            "data": {"ret_1m": -0.12}, "created_at": "2026-11-10", "seen_at": None},
                           now="2026-11-13")
    assert "qe-alert new" in row and 'qe-badge neg">MOV' in row and "da 3 g" in row and "./azienda?t=AAPL" in row


def test_app_css_is_safe_for_st_html():
    """DOMPurify scarta un intero <style> che contiene "<" (es. in un commento): niente CSS su tutta l'app."""
    from ui import common

    body = common.APP_CSS.removeprefix("<style>").removesuffix("</style>")
    assert "<" not in body and "--accent" in body and ".qe-dense" in body and ".st-key-qe_shell" in body
    # nomi storici usati dalle pagine non ancora migrate
    for name in ("panel", "dense_table", "money", "pct", "num", "signed_html", "_layout", "_line", "SERIES", "UP",
                 "DOWN", "MUTED", "AMBER", "STATUS", "ACTION_LABEL", "TERMINAL_CSS", "STATUS_CSS", "md", "data"):
        assert hasattr(common, name), name
    assert common.money(66.3e9) == "66,3 mld $" and common.pct(None) == "–" and common.num(1234.5, 1) == "1.234,5"
    assert common.signed_html(-0.0155) == '<span class="neg">▼ −1,55%</span>'


def test_multiselect_chips_are_grey_in_streamlit_166():
    """Streamlit 1.66 disegna le chip dei multiselect con react-aria (span[data-tag]): il vecchio selettore baseweb
    non le prendeva più e restavano piene nell'accento."""
    from ui import common

    css = common.APP_CSS
    assert '[data-testid="stMultiSelectTagsContainer"] [data-tag]' in css
    rule = css[css.index('[data-testid="stMultiSelectTagsContainer"] [data-tag] {'):]
    rule = rule[:rule.index("}")]
    assert "var(--surface-2)" in rule and "var(--border-ctl)" in rule


def test_no_raw_messages_or_terminal_commands_in_pages():
    """Niente st.info/st.warning (stati vuoti e avvisi con `empty_state`/`notice`), niente `format_value` (inglese)
    e niente comandi da Terminale fuori dai "Dettagli" (`details=`) nelle pagine."""
    import re

    offenders = []
    for path in sorted((ROOT / "ui").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"st\.(info|warning)\(|format_value\(|Choose options", code):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
            if re.search(r"(python|streamlit) [\w./-]+\.py", code) and "details" not in code \
                    and not code.lstrip().startswith(("\"\"\"", "'")) and "help=" not in code:
                ctx = "\n".join(text.splitlines()[max(0, n - 3):n])
                if "details" not in ctx:
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_sortable_tables_group_thousands_in_italian():
    """`num_column` lascia i numeri al browser (formato "localized" = Intl.NumberFormat con l'italiano forzato), ma
    l'italiano non separa le migliaia sotto 10.000 ("1950,73"): la cornice fa usare sempre il punto. Se c'è Node,
    il pezzo di JavaScript si prova davvero."""
    import inspect
    import shutil as sh
    import subprocess

    from ui import shell

    src = inspect.getsource(shell._keyboard)
    assert "useGrouping = 'always'" in src and C.num_column("Prezzo")["type_config"]["format"] == "localized"
    node = sh.which("node")
    if not node:
        return
    start = src.index("  try {\n    const NF = Intl.NumberFormat;")
    snippet = src[start:src.index("} catch (err) {}", start) + len("} catch (err) {}")]
    js = snippet + """
const f = (v, o) => new Intl.NumberFormat(['it-IT', 'it'], o).format(v);
console.log([f(1950.731, {minimumFractionDigits: 2, maximumFractionDigits: 2}), f(0.1234, {style: 'percent',
  maximumFractionDigits: 1}), f(-1234567.5), new Intl.NumberFormat('en-US').format(1950.5)].join('|'));"""
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30).stdout.strip()
    assert out == "1.950,73|12,3%|-1.234.567,5|1,950.5"


def test_fkeys_and_help():
    from modules import commands
    from ui import nav

    assert [k for k, _, _ in nav.FKEYS] == [f"F{i}" for i in range(1, 9)]
    assert [p for _, p, _ in nav.FKEYS] == ["", "mie-aziende", "azienda", "esplora", "confronta", "portafoglio",
                                           "diario", "notizie"]
    assert nav.SPECS[""][2] == "Oggi" and nav.section_of("backtest") == "Strategie e ricerca"
    assert "fn" in commands.HELP_TEXT and "Alt" in commands.HELP_TEXT and "F8" in commands.HELP_TEXT
    assert commands.parse("oggi", ["AAPL"], {"AAPL": "Apple"}).page == ""


# ---------------------------------------------------------------- cornice con AppTest (copia del db demo)
DEMO_DB = ROOT / "data" / "demo.db"


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    if not DEMO_DB.exists():
        pytest.skip("manca data/demo.db")
    pytest.importorskip("streamlit.testing.v1")
    import shutil
    import socket

    from modules import quotes

    tmp = tmp_path_factory.mktemp("shell")
    db = tmp / "demo.db"
    shutil.copy(DEMO_DB, db)
    config = tmp / "config.yaml"
    config.write_text(f"data:\n  db_path: {db}\n  facts_db_path: {tmp / 'facts.db'}\n  backup_dir: {tmp / 'b'}\n",
                      encoding="utf-8")

    def no_network(*a, **k):
        raise OSError("rete disattivata nei test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["app.py", "--config", str(config)])
        mp.setattr(socket, "create_connection", no_network)
        mp.setattr(socket.socket, "connect", no_network)
        mp.setattr(quotes, "download_closes", no_network)
        mp.setattr(quotes.QuoteFeed, "start", lambda self: self)
        yield config


def _run(url_path=None, query=None):
    from streamlit.testing.v1 import AppTest
    from streamlit.util import calc_hash

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    if url_path is not None:
        at._page_hash = calc_hash(url_path)
    for k, v in (query or {}).items():
        at.query_params[k] = v
    return at.run()


def test_follow_stays_on_page_with_toast(app_env):
    at = _run("esplora")
    at.text_input(key="qe_cmd").input("SEGUI MSFT").run()
    assert not at.exception
    assert any("esplora" in t.value.lower() for t in at.title)          # niente cambio di pagina
    assert any("MSFT" in t.value for t in at.toast)
    at.text_input(key="qe_cmd").input("TOGLI MSFT").run()
    assert any("tolta" in t.value for t in at.toast)


def test_ticker_link_opens_company(app_env):
    at = _run("azienda", {"t": "msft", "fn": "DCF"})
    assert not at.exception
    assert at.session_state["ticker"] == "MSFT" and at.session_state["co_fn"] == "DCF"
