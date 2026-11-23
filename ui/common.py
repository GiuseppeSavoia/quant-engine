"""Tema, token CSS e funzioni condivise da tutte le pagine (formattazione, pannelli, tabelle, grafici, dati).

Sistema visivo: docs/revisione_grafica.md §3. Ogni colore e dimensione è una variabile CSS su `:root` (TOKENS):
l'aspetto chiaro cambia solo i valori. I componenti (tabelle fitte, distintivi, testata, verdetto...) stanno in
`ui/components.py`; i nomi storici qui sotto (panel, dense_table, money, pct, num, signed_html...) restano e
usano gli stessi componenti, con i numeri in formato italiano.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data.db_manager import DBManager
from modules import dashboard_data, updater
from modules.config import PROJECT_ROOT, demo_db_path, load_config
from modules.dashboard_data import DashboardData
from ui import components as C
from ui import nav

# aspetto: "scuro" (terminale) = il tema di .streamlit/config.toml; "chiaro" = il tema chiaro con l'accento blu di §3.3;
# "Segui il sistema" (predefinito) = l'uno o l'altro come il Mac. Si sceglie nel menu "Dati"; la scelta resta in
# preferenze_interfaccia.json ({"aspetto": "sistema" | "scuro" | "chiaro"}).
# I file delle preferenze stanno accanto al database (data/ per i dati reali e la demo): con `--config` che punta a
# una copia (collaudi, test) l'app non tocca le preferenze dell'utente.
def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)
    return p.parse_known_args(sys.argv[1:])[0]


def prefs_path(name: str) -> Path:
    """Percorso di un file di preferenze dell'interfaccia (cartella del database in uso)."""
    try:
        a = _args()
        if not a.config:
            return PROJECT_ROOT / "data" / name
        cfg = load_config(a.config)
        db = demo_db_path(cfg, a.config) if a.demo else Path(cfg["data"]["db_path"])
        return (db if db.is_absolute() else PROJECT_ROOT / db).parent / name
    except Exception:  # noqa: BLE001 - config illeggibile: posto predefinito
        return PROJECT_ROOT / "data" / name


def main_config_args() -> list[str]:
    """Argomenti da passare a main.py lanciato dalla webapp: stessa demo e stessa configurazione della webapp
    (senza `--config` un'app aperta su una copia aggiornerebbe il database predefinito)."""
    a = _args()
    return (["--demo"] if a.demo else []) + (["--config", str(Path(a.config).resolve())] if a.config else [])




# aspetto chiaro: i valori di §3.3 (accento blu 4,94:1 sul bianco); tutto il resto: valori predefiniti di Streamlit
CLASSIC_THEME = {"base": "light", "primaryColor": "#1f6fd1", "linkColor": "#1f6fd1", "font": "sans-serif",
                 "codeFont": "monospace", "greenColor": "#0a7f0a", "redColor": "#b42f2f", "borderColor": "#8a8983",
                 "showWidgetBorder": True}


@st.cache_resource
def _terminal_theme() -> dict[str, dict]:
    """Opzioni del tema terminale lette da config.toml all'avvio (prima di qualunque cambio)."""
    from streamlit import config as st_config

    return {section: {k: v for k, v in st_config.get_options_for_section(section).items() if v is not None}
            for section in ("theme", "theme.sidebar")}


# Aspetto scelto nel menu "Dati": segue il Mac (predefinito), sempre scuro (terminale) o sempre chiaro.
# Streamlit 1.66 segue da solo il sistema solo se config.toml definisce [theme.light] e [theme.dark]; allora però la
# scelta resta nel browser (localStorage) e il codice Python non sa quale tema è attivo, mentre colori dei grafici,
# palette e CSS dipendono da TERMINAL. Quindi il tema resta UNO, imposto dal server (`_apply_theme`), e per "Segui il
# sistema" il browser scrive `prefers-color-scheme` in un cookie (`scheme_script`, iniettato da ui/shell.py) e ricarica
# la pagina se il tema mostrato è l'altro; il server legge il cookie all'apertura della sessione (st.context.cookies).
APPEARANCES = {"sistema": "Segui il sistema", "scuro": "Scuro", "chiaro": "Chiaro"}
SCHEME_COOKIE = "qe_scheme"
PREFS_FILE = "preferenze_interfaccia.json"


def appearance() -> str:
    """Preferenza dell'aspetto: "sistema" (predefinita), "scuro" o "chiaro". Il vecchio formato
    {"terminal": vero/falso} dell'interruttore vale come scelta esplicita: scuro/chiaro."""
    try:
        prefs = json.loads(prefs_path(PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "sistema"
    if not isinstance(prefs, dict):
        return "sistema"
    if prefs.get("aspetto") in APPEARANCES:
        return prefs["aspetto"]
    if "terminal" in prefs:
        return "scuro" if prefs["terminal"] else "chiaro"
    return "sistema"


def system_scheme() -> str | None:
    """"dark"/"light" del Mac come lo vede il browser (cookie scritto da `scheme_script`); None se non ancora noto
    (prima apertura, test, cookie bloccati)."""
    try:
        value = st.context.cookies.get(SCHEME_COOKIE)
    except Exception:  # noqa: BLE001 - fuori da una sessione Streamlit
        return None
    return value if value in ("dark", "light") else None


def _terminal_wanted() -> bool:
    """Tema scuro (terminale)? Con "Segui il sistema" e schema ancora ignoto: scuro, come prima."""
    mode = appearance()
    if mode == "sistema":
        return system_scheme() != "light"
    return mode == "scuro"


def scheme_script(mode: str, rendered_dark: bool) -> str:
    """Script per "Segui il sistema": scrive lo schema del Mac nel cookie e ricarica la pagina se il server ha
    disegnato l'altro tema (anche quando l'aspetto del Mac cambia con la pagina aperta). Un solo ricaricamento per
    lo stesso schema ogni 15 secondi: con i cookie bloccati non si entra in un ciclo."""
    return """<script>
(() => {
  window.__qeScheme = {mode: %s, rendered: %s};
  const query = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const sync = () => {
    const s = window.__qeScheme;
    if (!s || s.mode !== 'sistema' || !query) return;
    const want = query.matches ? 'dark' : 'light';
    document.cookie = '%s=' + want + '; path=/; max-age=31536000; SameSite=Lax';
    if (want === s.rendered) return;
    let last = ['', 0];
    try { last = (sessionStorage.getItem('qe_scheme_reload') || '|0').split('|'); } catch (err) {}
    if (last[0] === want && Date.now() - (Number(last[1]) || 0) < 15000) return;  // già ricaricato per questo
    try { sessionStorage.setItem('qe_scheme_reload', want + '|' + Date.now()); } catch (err) {}
    window.location.reload();
  };
  if (query && !window.__qeSchemeListen) {
    window.__qeSchemeListen = true;
    try { query.addEventListener('change', sync); } catch (err) {}
  }
  sync();
})();
</script>""" % (json.dumps(mode), json.dumps("dark" if rendered_dark else "light"), SCHEME_COOKIE)


def _apply_theme(terminal: bool) -> None:
    """Imposta il tema del server (app per un solo utente): Streamlit lo manda al browser a ogni esecuzione."""
    from streamlit import config as st_config

    changed = False
    for section, opts in _terminal_theme().items():
        classic = CLASSIC_THEME if section == "theme" else {}
        for key in set(opts) | set(classic):
            value = opts.get(key) if terminal else classic.get(key)
            if st_config.get_option(f"{section}.{key}") != value:
                st_config.set_option(f"{section}.{key}", value)
                changed = True
    if changed:
        # il tema parte verso il browser all'inizio dell'esecuzione, prima di questa funzione, e il browser lo applica
        # solo al caricamento: si ricarica la pagina una volta (es. "Segui il sistema" appena saputo lo schema del Mac,
        # o il primo caricamento dopo l'avvio del server con l'aspetto chiaro)
        try:
            st.session_state["ui_reload"] = True
        except Exception:  # noqa: BLE001 - fuori da una sessione Streamlit
            pass


def set_appearance(mode: str) -> None:
    """Salva l'aspetto scelto (accanto al database in uso) e chiede di ricaricare la pagina."""
    if mode not in APPEARANCES:
        return
    path = prefs_path(PREFS_FILE)
    try:
        prefs = json.loads(path.read_text(encoding="utf-8"))
        prefs = prefs if isinstance(prefs, dict) else {}
    except (OSError, ValueError):
        prefs = {}
    prefs.pop("terminal", None)  # formato vecchio dell'interruttore
    prefs["aspetto"] = mode
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs), encoding="utf-8")
    st.session_state["ui_reload"] = True  # il browser applica il tema nuovo solo ricaricando la pagina


def _on_appearance():
    set_appearance(st.session_state.get("ui_appearance") or appearance())


TERMINAL = _terminal_wanted()


# ---------------------------------------------------------------- token (§3.1, §3.3)
# contrasti WCAG calcolati nella revisione: testo >= 4,5:1, controlli >= 3:1; "--text-off" solo per "–" e disattivati
TOKENS_TERMINAL = {
    "bg": "#000000", "surface-1": "#0d0d0d", "surface-2": "#1a1a1a", "surface-hi": "#3a2a00",
    "border": "#2a2a2a", "border-ctl": "#5c5c5c",
    "text": "#e8e6e1", "text-num": "#ffffff", "text-2": "#b4b2a9", "text-3": "#9a978f", "text-off": "#6e6d68",
    "accent": "#ffa028", "on-accent": "#000000", "header-line": "#ffa028", "label": "#a8a59c",
    "pos": "#2fbf4a", "neg": "#ff5a52", "warn": "#ffd166", "info": "#5aa0f0", "trap": "#e8743b",
}
TOKENS_LIGHT = {
    "bg": "#ffffff", "surface-1": "#f7f6f3", "surface-2": "#efeee9", "surface-hi": "#fff1cc",
    "border": "#e3e1da", "border-ctl": "#8a8983",
    "text": "#1f1e1c", "text-num": "#111111", "text-2": "#52514e", "text-3": "#6b6a66", "text-off": "#8a8983",
    "accent": "#1f6fd1", "on-accent": "#ffffff", "header-line": "#d3d1c7", "label": "#6b6a66",
    "pos": "#0a7f0a", "neg": "#b42f2f", "warn": "#8a5a00", "info": "#1f6fd1", "trap": "#b0451a",
}
TYPE_SCALE = {"fs-micro": "12px", "fs-data": "13px", "fs-body": "14px", "fs-title": "13px", "fs-h2": "16px",
              "fs-h1": "20px", "fs-key": "26px", "fs-big": "18px"}
FONTS = {"mono": 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace',
         "sans": '-apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif'}
TOKENS = TOKENS_TERMINAL if TERMINAL else TOKENS_LIGHT


# palette categoriale (ordine fisso, mai ciclato), validata per ciascun fondo; il 5° colore solo per Confronta
SERIES = (["#c98500", "#3987e5", "#d95926", "#9085e9", "#199e70"] if TERMINAL
          else ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"])


MUTED = "#8a8983"                                   # linee di riferimento (prezzo, mediane, soglie)


INK2 = "#b4b2a9" if TERMINAL else "#52514e"         # riferimenti più marcati


GRID = "#262626" if TERMINAL else None


STATUS = {"BUY": TOKENS["pos"], "BLOCKED": TOKENS["neg"], "SKIP": MUTED}


ACTION_LABEL = {"BUY": "▲ BUY", "SKIP": "● SKIP", "BLOCKED": "■ BLOCKED"}


DIVERGING = [[0.0, "#d03b3b"], [0.5, "#383835" if TERMINAL else "#f0efec"], [1.0, SERIES[1] if TERMINAL else SERIES[0]]]


AMBER = "#ffa028"
ACCENT = TOKENS["accent"]


UP, DOWN = TOKENS["pos"], TOKENS["neg"]  # variazioni (sempre con ▲▼, non solo colore)


def _root_css() -> str:
    lines = [f"  --{k}: {v};" for k, v in {**TOKENS, **TYPE_SCALE, **FONTS}.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


# ---------------------------------------------------------------- CSS dell'app (iniettato da app.py in ogni pagina)
BASE_CSS = """
/* fondo e testo: sans-serif per le frasi, monospazio solo per numeri, tabelle, codici, ticker, barra comandi */
.stApp { background: var(--bg); color: var(--text); }
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button { font-family: var(--sans); }
.stMarkdown p, .stMarkdown li { font-size: var(--fs-body); line-height: 1.45; }
h1, h2, h3, h4 { font-family: var(--sans) !important; text-transform: none !important; letter-spacing: 0 !important; }
h1 { font-size: var(--fs-h1) !important; font-weight: 600 !important; color: var(--text-num) !important;
  padding: .1rem 0 .2rem !important; line-height: 1.3 !important; }
h2 { font-size: 18px !important; font-weight: 600 !important; color: var(--text) !important; }
h3 { font-size: var(--fs-h2) !important; font-weight: 600 !important; color: var(--text) !important;
  border-bottom: 1px solid var(--border); padding-bottom: .25rem !important; }
h4 { font-size: var(--fs-body) !important; font-weight: 600 !important; color: var(--text) !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
  font-size: var(--fs-data) !important; color: var(--text-2) !important; }
[data-testid="stWidgetLabel"] p { font-size: var(--fs-data) !important; color: var(--text-2); }
code { font-family: var(--mono) !important; }
.stMarkdown code { color: var(--text); background: var(--surface-2); font-size: .92em; }
/* numeri in evidenza: st.metric */
[data-testid="stMetricLabel"] p { font: 500 var(--fs-micro) var(--mono) !important; color: var(--accent) !important;
  text-transform: uppercase; letter-spacing: .04em; }
[data-testid="stMetricValue"] { font: 500 var(--fs-big) var(--mono) !important; color: var(--text-num);
  font-variant-numeric: tabular-nums; }
[data-testid="stMetricDelta"] { font-size: var(--fs-micro) !important; }
/* menu, schede, expander: niente maiuscolo, 13-14 px */
button[role="tab"] p { font-size: var(--fs-body) !important; text-transform: none !important; }
[data-testid="stExpander"] summary p { font-size: var(--fs-body) !important; color: var(--text) !important;
  text-transform: none !important; }
[data-testid="stExpander"] details { border-color: var(--border-ctl) !important; border-radius: 0 !important; }
[data-testid="stPopoverButton"] p { font-size: var(--fs-data) !important; text-transform: none; }
/* testata di Streamlit: menu in alto, riga sottile sotto; niente barra laterale */
[data-testid="stHeader"] { background: var(--bg) !important; border-bottom: 1px solid var(--header-line); }
[data-testid="stTopNav"] *, [data-testid="stTopNavSection"] * { font-size: var(--fs-data) !important;
  text-transform: none !important; letter-spacing: 0 !important; }
[data-testid="stSidebarCollapsedControl"], [data-testid="stSidebar"] { display: none !important; }
/* chip dei multiselect: grigie con bordo (l'accento pieno solo per l'azione principale e il tasto attivo).
   Streamlit 1.66 disegna le chip con react-aria (span[data-tag] dentro stMultiSelectTagsContainer), non più con
   baseweb: il vecchio selettore resta per le versioni precedenti */
.stMultiSelect [data-baseweb="tag"], [data-testid="stMultiSelectTagsContainer"] [data-tag] {
  background: var(--surface-2) !important; border: 1px solid var(--border-ctl) !important;
  border-radius: 0 !important; }
.stMultiSelect [data-baseweb="tag"] span, .stMultiSelect [data-baseweb="tag"] svg,
[data-testid="stMultiSelectTagsContainer"] [data-tag] *, [data-testid="stMultiSelectTagsContainer"] [data-tag] {
  color: var(--text) !important; fill: var(--text) !important; font-size: var(--fs-micro); }
[data-testid="stMultiSelectTagsContainer"] [data-tag]:focus-visible { outline: 2px solid var(--accent); }
/* pulsanti secondari con bordo dei controlli (>= 3:1) */
.stButton button[kind="secondary"], .stDownloadButton button[kind="secondary"], [data-testid="stPopoverButton"] {
  border-color: var(--border-ctl) !important; }
/* moduli (§3.10): niente +/- sui numeri, focus d'accento */
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] { display: none !important; }
.stApp input:focus-visible, .stApp textarea:focus-visible { outline: 2px solid var(--accent) !important;
  outline-offset: -1px; }
/* pagina: margini laterali 24 px, spazi a multipli di 4 */
.block-container { padding: 3.9rem 24px 3.2rem !important; max-width: 100% !important; }
[data-testid="stVerticalBlock"] { gap: 12px; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 0 !important; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > div > .stHtml .qe-panel-title),
div[data-testid="stVerticalBlock"]:has(> .stElementContainer .qe-panel-title) { background: var(--surface-1); }
.modebar-container { display: none !important; }
/* nessun HTML più largo del suo riquadro (A2): le tabelle larghe scorrono dentro, non coprono i vicini */
.stHtml { max-width: 100%; overflow-x: auto; }
/* toast sopra la barra di stato */
[data-testid="stToastContainer"] { bottom: 2.4rem !important; z-index: 1000200 !important; }
[data-testid="stToast"] { border: 1px solid var(--border-ctl); border-radius: 0 !important; font-size: var(--fs-body); }
/* attesa (§3.9): barra sottile animata sotto la testata mentre lo script gira */
body:has([data-testid="stStatusWidget"]) [data-testid="stHeader"]::after { content: ""; position: absolute; left: 0;
  right: 0; bottom: -1px; height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
  background-size: 40% 100%; background-repeat: no-repeat; animation: qe-run 1s linear infinite; }
@keyframes qe-run { from { background-position: -40% 0 } to { background-position: 140% 0 } }
[data-testid="stStatusWidget"] * { color: var(--accent) !important; font-size: var(--fs-micro) !important; }
"""

COMPONENTS_CSS = """
.pos { color: var(--pos); } .neg { color: var(--neg); } .flat { color: var(--text-3); }
.off, .qe-na { color: var(--text-off); }
/* pannello (§3.7) */
.qe-panel-title { display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
  margin: -.4rem -.6rem .5rem; padding: 5px 10px; background: var(--surface-2); border-left: 3px solid var(--accent);
  border-bottom: 1px solid var(--border); font: 600 var(--fs-title) var(--sans); color: var(--text);
  text-transform: uppercase; letter-spacing: .05em; }
.qe-panel-title .l { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.qe-panel-title .r { color: var(--text-3); text-transform: none; font: 400 var(--fs-micro) var(--sans);
  letter-spacing: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.qe-panel-title .r a { color: var(--info); text-decoration: none; }
/* tabelle fitte (§3.5) */
.qe-tablewrap { overflow-x: auto; max-width: 100%; }
table.qe-dense { width: 100%; border-collapse: collapse; table-layout: auto; font: var(--fs-data)/1.35 var(--sans);
  font-variant-numeric: tabular-nums lining-nums; margin: 0; }
table.qe-dense th { position: sticky; top: 0; background: var(--surface-2); color: var(--label);
  font-size: var(--fs-micro); font-weight: 500; text-transform: uppercase; letter-spacing: .04em; text-align: left;
  padding: 4px 8px; border-bottom: 1px solid var(--border-ctl); white-space: nowrap; }
table.qe-dense td { padding: 4px 8px; border-bottom: 1px solid var(--border); color: var(--text-num);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 320px; }
table.qe-dense td.t { color: var(--text); }
table.qe-dense td.n, table.qe-dense th.n { text-align: right; }
table.qe-dense td.na { color: var(--text-off); text-align: right; }
table.qe-dense td.src-fb { font-style: italic; color: var(--warn, inherit); }
table.qe-dense td .qe-src { font-size: 0.75em; margin-left: 2px; vertical-align: super; }
table.qe-dense td.fill { width: 100%; max-width: 0; }
table.qe-dense tr:has(a.qe-tk):hover td { background: var(--surface-2); }
table.qe-dense tr.me td { background: var(--surface-hi); }
table.qe-dense tr.miss td { color: var(--text-off); }
a.qe-tk { color: var(--info); text-decoration: none; font-family: var(--mono); font-weight: 600; }
a.qe-tk:hover { text-decoration: underline; }
.qe-more, .qe-empty-line { color: var(--text-3); font-size: var(--fs-micro); padding: 4px 8px; }
.qe-empty-line { font-size: var(--fs-data); color: var(--text-2); }
/* distintivi e stati vuoti (§3.8) */
.qe-badge { display: inline-block; padding: 1px 6px; font: 500 var(--fs-micro)/1.4 var(--mono);
  text-transform: uppercase; letter-spacing: .04em; border: 1px solid currentColor; white-space: nowrap;
  vertical-align: baseline; }
.qe-badge.pos { color: var(--pos); } .qe-badge.neg { color: var(--neg); } .qe-badge.warn { color: var(--warn); }
.qe-badge.neutral { color: var(--text-2); } .qe-badge.info { color: var(--info); } .qe-badge.trap { color: var(--trap); }
.qe-badge.off { color: var(--text-off); border-style: dashed; } .qe-badge.accent { color: var(--accent); }
.qe-empty { border: 1px dashed var(--border-ctl); padding: 12px 14px; color: var(--text-2); font-size: var(--fs-body); }
.qe-empty b { display: block; color: var(--text); font-weight: 600; margin-bottom: 2px; }
.qe-empty details { margin-top: 6px; font-size: var(--fs-micro); color: var(--text-3); }
.qe-empty summary { cursor: pointer; }
/* avvisi in linea */
.qe-notice { display: flex; gap: 8px; align-items: baseline; padding: 6px 10px; margin: 4px 0;
  border-left: 3px solid var(--text-3); background: var(--surface-2); color: var(--text); font-size: var(--fs-body); }
.qe-notice i { font: 600 var(--fs-data) var(--mono); font-style: normal; }
.qe-notice.neg { border-color: var(--neg); } .qe-notice.neg i { color: var(--neg); }
.qe-notice.warn { border-color: var(--warn); } .qe-notice.warn i { color: var(--warn); }
.qe-notice.info { border-color: var(--info); } .qe-notice.info i { color: var(--info); }
.qe-notice.pos { border-color: var(--pos); } .qe-notice.pos i { color: var(--pos); }
/* testata a due livelli (§3.6) e righe chiave/valore */
.qe-head { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; padding: 8px 12px;
  border: 1px solid var(--border-ctl); background: var(--surface-1); }
/* testata vecchia (una riga di span con etichetta in grassetto), finché la scheda non passa a head_html; NB: mai il carattere "minore di" nel CSS, DOMPurify scarterebbe tutto lo stile */
.qe-head:not(:has(> .qe-head-id)) { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 0;
  font: var(--fs-data) var(--sans); font-variant-numeric: tabular-nums; padding: 6px 10px; }
.qe-head:not(:has(> .qe-head-id)) > span { margin-right: 18px; white-space: nowrap; }
.qe-head:not(:has(> .qe-head-id)) b { color: var(--label); font-weight: 500; margin-right: 6px; }
.qe-head:not(:has(> .qe-head-id)) .big { font-size: var(--fs-big); color: var(--text-num); }
.qe-head .tk { font: 600 var(--fs-key)/1.15 var(--mono); color: var(--text-num); margin-right: 12px; }
.qe-head .nm { font-size: var(--fs-h2); color: var(--text); }
.qe-head .meta, .qe-head .src { display: block; color: var(--text-3); font-size: var(--fs-micro); }
.qe-head-px { text-align: right; white-space: nowrap; }
.qe-head .px { font: 600 var(--fs-key)/1.15 var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums; }
.qe-head .chg { font: var(--fs-body) var(--sans); font-variant-numeric: tabular-nums; margin-left: 10px; }
.qe-kv { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 4px 24px; margin: 6px 0 0; }
.qe-kv > div { white-space: nowrap; }
.qe-kv dt { color: var(--label); font: var(--fs-micro) var(--sans); text-transform: uppercase; letter-spacing: .05em; }
.qe-kv dd { margin: 0; font: 500 var(--fs-data) var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums; }
.qe-kv.big dd { font-size: var(--fs-big); }
.qe-kv a { color: var(--info); }
.qe-kv .rng { display: inline-block; position: relative; width: 64px; height: 4px; background: var(--border-ctl);
  vertical-align: middle; margin: 0 4px; }
.qe-kv .rng b { position: absolute; top: -3px; width: 2px; height: 10px; background: var(--accent); }
.qe-result { padding: 8px 12px; border: 1px solid var(--border-ctl); background: var(--surface-1); }
.qe-result .qe-kv { margin: 0; }
.qe-result-title { font: 600 var(--fs-title) var(--sans); color: var(--text); text-transform: uppercase;
  letter-spacing: .04em; margin-bottom: 4px; }
.qe-result-note { color: var(--text-3); font-size: var(--fs-micro); margin-top: 4px; }
/* verdetto (§4.2, §7.1) */
.qe-verdict { border: 1px solid var(--border-ctl); border-left: 4px solid var(--text-2); background: var(--surface-1);
  padding: .4rem .6rem .6rem; }
.qe-verdict.pos { border-left-color: var(--pos); } .qe-verdict.warn { border-left-color: var(--warn); }
.qe-verdict.neg { border-left-color: var(--neg); } .qe-verdict.trap { border-left-color: var(--trap); }
.qe-verdict.off { border-left-color: var(--text-off); border-style: dashed; }
.qe-verdict .body { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 8px 24px; }
.qe-verdict .label { font: 600 var(--fs-key)/1.2 var(--sans); color: var(--text-2); }
.qe-verdict .label .sym { font-family: var(--mono); margin-right: 10px; }
.qe-verdict .label.pos { color: var(--pos); } .qe-verdict .label.warn { color: var(--warn); }
.qe-verdict .label.neg { color: var(--neg); } .qe-verdict .label.trap { color: var(--trap); }
.qe-verdict .label.off { color: var(--text-off); }
.qe-verdict .rule { color: var(--text-2); font-size: var(--fs-body); margin: 2px 0 8px; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.qe-verdict .sub { font: 600 var(--fs-micro) var(--sans); color: var(--label); text-transform: uppercase;
  letter-spacing: .04em; margin: 4px 0 2px; }
.qe-verdict ul { margin: 0 0 6px; padding-left: 18px; font-size: var(--fs-data); color: var(--text); }
.qe-verdict li.more { color: var(--text-3); list-style: none; margin-left: -18px; }
.qe-verdict .val { font: var(--fs-data) var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums; }
.qe-verdict .foot { color: var(--text-3); font-size: var(--fs-micro); margin-top: 6px; }
.qe-pillar { display: flex; align-items: center; gap: 10px; font-size: var(--fs-data); padding: 2px 0;
  white-space: nowrap; }
.qe-pillar .lab { width: 13.5em; flex-shrink: 0; color: var(--text); overflow: hidden; text-overflow: ellipsis; }
.qe-pillar .bar { display: inline-flex; gap: 2px; }
.qe-pillar .bar i { width: 9px; height: 10px; background: var(--border); }
.qe-pillar .bar i.on { background: var(--text-2); }
.qe-pillar .bar.pos i.on { background: var(--pos); } .qe-pillar .bar.neg i.on { background: var(--neg); }
.qe-pillar .sc { font-family: var(--mono); color: var(--text-num); width: 2.8em; text-align: right; }
.qe-pillar .gr { color: var(--text-2); }
.qe-pillar .cov { color: var(--text-3); font-size: var(--fs-micro); }
/* cosa è cambiato (§7.2) */
.qe-changes .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 24px; }
.qe-changes .sub { font: 600 var(--fs-micro) var(--sans); color: var(--label); text-transform: uppercase;
  letter-spacing: .04em; margin-bottom: 2px; }
.qe-changes ul { margin: 0; padding: 0; list-style: none; font-size: var(--fs-data); }
.qe-changes li { padding: 1px 0; }
.qe-changes .none { color: var(--text-3); font-size: var(--fs-data); }
.qe-changes .line { margin-top: 6px; font-size: var(--fs-data); color: var(--text-2); }
/* avvisi (§7.3) */
.qe-alert { display: flex; align-items: baseline; gap: 8px; padding: 3px 0; font-size: var(--fs-data);
  border-bottom: 1px solid var(--border); color: var(--text-2); }
.qe-alert.new { color: var(--text-num); }
.qe-alert .dot { width: 10px; color: var(--accent); flex-shrink: 0; }
.qe-alert .tk { font-family: var(--mono); width: 5.5em; flex-shrink: 0; }
.qe-alert .msg { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qe-alert .age { color: var(--text-3); font-size: var(--fs-micro); white-space: nowrap; }
/* briciole ("dove sono") */
.qe-crumbs { font: var(--fs-micro) var(--mono); color: var(--text-3); text-transform: uppercase; letter-spacing: .04em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qe-crumbs i { font-style: normal; margin: 0 4px; color: var(--text-off); }
.qe-crumbs b { color: var(--accent); font-weight: 600; }
"""

# cornice (§3.12): barra dei comandi + tasti fissi sotto il menu, barra di stato in fondo
SHELL_CSS = """
/* il contenitore con la chiave sta dentro un stLayoutWrapper alto quanto lui: fisso è il wrapper */
[data-testid="stLayoutWrapper"]:has(> .st-key-qe_shell), .st-key-qe_shell { position: sticky; top: 3.75rem;
  z-index: 990; background: var(--bg); }
.st-key-qe_shell { padding: 6px 0 4px; border-bottom: 1px solid var(--border); gap: 4px !important; }
.st-key-qe_shell [data-testid="stHorizontalBlock"] { gap: 12px !important; }
input[aria-label="Comando"] { font: var(--fs-body) var(--mono) !important; color: var(--text-num) !important;
  caret-color: var(--accent); border-color: var(--accent) !important; }
input[aria-label="Comando"]::placeholder { color: var(--text-3) !important; opacity: 1; }
.st-key-qe_fkeys { gap: 4px !important; flex-wrap: nowrap !important; overflow: hidden; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"] {
  background: transparent; border: 1px solid var(--accent); border-radius: 0; padding: 2px 5px !important;
  min-height: 0 !important; margin: 0 !important; gap: 0 !important; white-space: nowrap; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"] span:has(> [data-testid="stIconMaterial"]) { display: none; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"] * { color: var(--accent) !important;
  font: 600 var(--fs-micro)/1.5 var(--mono) !important; text-transform: none; white-space: nowrap; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"]:hover { background: var(--surface-2); }
.st-key-qe_fkey_on [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"]:hover {
  background: var(--accent) !important; }
.st-key-qe_fkey_on [data-testid="stPageLink-NavLink"] * { color: var(--on-accent) !important; }
.st-key-qe_fkeys .stElementContainer, .st-key-qe_fkeys [data-testid="stVerticalBlock"] { width: auto !important; }
.st-key-qe_fkey_on { width: auto !important; flex: 0 0 auto !important; }
.st-key-qe_crumbs { min-height: 0; }
.qe-status { position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000100; display: flex; gap: 0; align-items: center;
  padding: 4px 14px; font: var(--fs-micro)/1.4 var(--mono); white-space: nowrap; overflow: hidden;
  background: var(--surface-2); color: var(--text-2); border-top: 1px solid var(--border-ctl); }
.qe-status > span { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; padding-right: 8px; }
.qe-status > span + span::before { content: "·"; color: var(--text-off); margin-right: 8px; }
.qe-status > span.keep { flex-shrink: 0; }
.qe-status > span.low { flex-shrink: 1; }
.qe-status > span.lowest { flex-shrink: 1000; }
.qe-status b { color: var(--accent); font-weight: 600; }
.qe-status .demo { color: var(--warn); font-weight: 600; }
.qe-status .o { color: var(--pos); } .qe-status .p { color: var(--warn); } .qe-status .c { color: var(--text-3); }
/* esito dell'ultimo aggiornamento (barra di stato e menu "Dati") */
.qe-status .upd-ok { color: var(--pos); } .qe-status .upd-warn { color: var(--warn); font-weight: 600; }
.qe-status .upd-fail { color: var(--neg); font-weight: 600; }
table.qe-steps td:first-child { width: 1.2em; text-align: center; vertical-align: top; }
table.qe-steps td.lab { white-space: normal; max-width: none; width: 100%; }
table.qe-steps td.n { vertical-align: top; }
table.qe-steps .note { color: var(--text-3); font: var(--fs-micro)/1.35 var(--sans); margin-top: 1px; }
table.qe-steps details, details.qe-hist { font: var(--fs-micro) var(--sans); color: var(--text-3); margin-top: 2px; }
table.qe-steps summary, details.qe-hist summary { cursor: pointer; }
table.qe-steps pre { white-space: pre-wrap; margin: 2px 0 0; font: var(--fs-micro)/1.35 var(--mono); color: var(--text-2); }
"""

APP_CSS = "<style>\n" + _root_css() + BASE_CSS + COMPONENTS_CSS + SHELL_CSS + "</style>"
# nomi storici: app.py e shell.py iniettavano questi due; ora è tutto in APP_CSS (stessi token per i due aspetti)
TERMINAL_CSS = APP_CSS
STATUS_CSS = ""


# ---------------------------------------------------------------- caricamento dati
@st.cache_resource(show_spinner="Carico prezzi e bilanci dal database…")
def _load(config_path: str | None, demo: bool, data_key: str, code_mtime: float) -> DashboardData:  # noqa: ARG001
    """`data_key` (ultima esecuzione di main.py) e data di modifica del codice sono solo chiavi di cache."""
    cfg = load_config(config_path)
    reports = PROJECT_ROOT / "reports"
    if demo:
        cfg["data"]["db_path"] = str(demo_db_path(cfg, config_path))
        reports = reports / "demo"
    return DashboardData(cfg, DBManager(cfg["data"]["db_path"]), reports)


def data() -> DashboardData:
    a = _args()
    cfg = load_config(a.config)
    try:  # con --demo e --config vale il database della configurazione (stessa regola di main.py)
        db_path = demo_db_path(cfg, a.config) if a.demo else Path(cfg["data"]["db_path"])
    except ValueError as exc:
        C.empty_state("Demo non aperta", str(exc))
        st.stop()
    if not db_path.exists():
        C.empty_state("Nessun dato ancora", "Il database non c'è: serve un primo aggiornamento dei dati.",
                      details=f"File atteso: {db_path}. Da Terminale, nella cartella del progetto: "
                              ".venv/bin/python main.py")
        st.stop()
    # chiave: ultima esecuzione di main.py (gli esperimenti del laboratorio scrivono nel database ma non
    # cambiano i dati di mercato, quindi non devono invalidare la cache). Durante un aggiornamento si resta
    # sull'esecuzione precedente: quella in corso ha scritto solo una parte dei dati.
    status = updater.read_status(PROJECT_ROOT)
    before = updater.run_id_before(status) if status.get("running") else None
    db = DBManager(db_path)
    # anche i dati inseriti a mano e le aziende aggiunte cambiano i dati da caricare
    key = f"{db.last_run_id(before) or db_path.stat().st_mtime}|{db.user_data_version()}"
    return _load(a.config, a.demo, key, _code_mtime())


def _code_mtime() -> float:
    """Ultima modifica della facciata `dashboard_data.py` e dei servizi (`modules/services/*.py`)."""
    files = [Path(dashboard_data.__file__), *(Path(dashboard_data.__file__).parent / "services").glob("*.py")]
    return max(f.stat().st_mtime for f in files)


# ---------------------------------------------------------------- formattazione (in italiano: ui/components.fmt)
def md(text: str) -> str:
    """Testo per st.caption/markdown: due "$" verrebbero presi per una formula matematica."""
    return text.replace("$", "\\$")


CURRENCY_SYMBOL = C.CURRENCY_SYMBOL
fmt, fmt_html, fmt_table, fmt_metric = C.fmt, C.fmt_html, C.fmt_table, C.fmt_metric


def money(v: float, currency: bool | str = True) -> str:
    """Importo leggibile in italiano: 49,8 mld $ (senza valuta dove l'etichetta la indica già; "EUR" -> €)."""
    return C.fmt(v, "money", currency=currency if currency else None)


def pct(v: float, signed: bool = True) -> str:
    """12,3% (con segno: +12,3%); mancante "–"."""
    return C.fmt(v, "pct", signed=signed)


def num(v: float, digits: int = 2) -> str:
    return C.fmt(v, "num", digits=digits)


panel = C.panel  # panel(title, *, right="", key=None)


def signed_html(v: float, fmt: str = "{:+.2%}") -> str:  # noqa: A002 - nome storico del parametro
    """Variazione con segno, freccia e colore (non solo colore), in italiano. `fmt`: formato Python del numero."""
    if v is None or not np.isfinite(v):
        return C.na_html()
    tone = C.tone_of(v)
    arrow = "▲" if tone == "pos" else "▼" if tone == "neg" else "■"
    text = C._no_signed_zero(C.it_number(fmt.format(v)))
    return f'<span class="{tone}">{arrow} {text}</span>'


def dense_table(df, columns: dict, max_rows: int = 30, **kw):
    """Tabella fitta in sola lettura (ui/components.dense_table): `columns` colonna -> (intestazione, tipo); tipi
    "text", "ticker" (link alla scheda), "signed", "pct", quelli di `fmt` o un formato Python ("{:,.2f}")."""
    C.dense_table(df, columns, max_rows, **kw)


def _layout(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=28, b=8), hovermode="x unified",
                      showlegend=legend, legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      separators=",.")  # numeri dei grafici in italiano: virgola decimale, punto per le migliaia
    fig.update_xaxes(showgrid=False, showspikes=True, spikemode="across", spikethickness=1, spikedash="dot",
                     spikecolor=MUTED)
    fig.update_yaxes(gridwidth=1, showspikes=True, spikethickness=1, spikedash="dot", spikecolor=MUTED)
    if TERMINAL:  # griglia appena visibile sul nero, numeri in monospazio
        fig.update_layout(font=dict(family="ui-monospace, Menlo, monospace", size=12),
                          hoverlabel=dict(bgcolor="#1a1a1a", bordercolor="#ffa028",
                                          font=dict(family="ui-monospace, Menlo, monospace")))
        fig.update_xaxes(linecolor="#333", tickcolor="#333")
        fig.update_yaxes(gridcolor=GRID, zerolinecolor="#444")
    else:
        fig.update_layout(font=dict(size=12))
    italian_dates(fig)
    return fig


# Plotly scrive i mesi in inglese ("Jan 2026", "Nov 2, 2026") e Streamlit non carica la lingua italiana: sugli assi
# delle date si usano formati numerici (collaudo 2026-11-16).
DATE_TICKS = [dict(dtickrange=[None, 43200000], value="%d/%m %H:%M"),          # tacche sotto il giorno
              dict(dtickrange=[86400000, 2419200000], value="%d/%m/%y"),     # giorni e settimane (fino a 28 g)
              dict(dtickrange=["M1", "M11"], value="%m/%Y"),                 # mesi
              dict(dtickrange=["M12", None], value="%Y")]                    # anni


def _is_date_values(x) -> bool:
    if x is None:
        return False
    try:
        arr = np.asarray(x)
    except Exception:  # noqa: BLE001
        return False
    if arr.size == 0:
        return False
    if np.issubdtype(arr.dtype, np.datetime64):
        return True
    first = arr.flat[0]
    if isinstance(first, (pd.Timestamp, np.datetime64)) or hasattr(first, "isoformat"):
        return True
    return isinstance(first, str) and len(first) >= 10 and first[4] == "-" and first[7] == "-" and first[:4].isdigit()


def italian_dates(fig: go.Figure) -> go.Figure:
    """Assi x con date: etichette e passaggio del mouse in formato italiano (13/11/2026, 11/2026, 2026)."""
    axes = set()
    for tr in fig.data:
        if getattr(tr, "orientation", None) == "h":
            continue
        if _is_date_values(getattr(tr, "x", None)):
            name = getattr(tr, "xaxis", None) or "x"
            axes.add("xaxis" + name[1:])
    for name in axes:
        ax = fig.layout[name] if name in fig.layout else None
        if ax is not None and ax.tickformat:  # formato scelto dalla pagina: resta
            continue
        fig.update_layout({name: dict(tickformatstops=DATE_TICKS,
                                      hoverformat=(ax.hoverformat if ax is not None and ax.hoverformat
                                                   else "%d/%m/%Y"))})
    return fig


def _line(fig, x, y, name, color, row=None, dash=None, width=2):
    kw = dict(row=row, col=1) if row else {}
    fig.add_trace(go.Scatter(x=x, y=y, name=name, mode="lines", line=dict(color=color, width=width, dash=dash)), **kw)


def _open_selected(view, selection, slot):
    rows = selection.selection.rows if selection else []
    with slot:
        if rows:
            ticker = view.iloc[rows[0]]["ticker"]
            if st.button(f"Apri la scheda di {ticker} →", type="primary"):
                st.session_state["ticker"] = ticker
                st.switch_page(nav.page("azienda"))
        else:
            st.caption("Seleziona una riga per aprire la scheda dell'azienda.")
