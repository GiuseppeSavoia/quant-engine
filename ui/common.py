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




# Aspetto chiaro (2026-11-24, "terminale da prodotto", stessa struttura dello scuro che è la base): fondo #f3f5f8,
# superfici bianche con bordi sottili, accento verde acqua scuro #0f766e (>= 4,5:1 sul bianco), angoli piccoli. Tutte le
# opzioni esplicite: quelle che mancano qui _apply_theme le mette a None (valori predefiniti di Streamlit). Stessi
# valori di TOKENS_LIGHT; lo scuro sta in .streamlit/config.toml (valori di TOKENS_DARK).
FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, Roboto, Helvetica Neue, sans-serif"
LIGHT_THEME = {"base": "light", "primaryColor": "#0f766e", "backgroundColor": "#f3f5f8",
               "secondaryBackgroundColor": "#f6f8fa", "textColor": "#0f1720", "linkColor": "#0f766e",
               "borderColor": "#8c959f", "showWidgetBorder": True, "baseRadius": "6px", "buttonRadius": "6px",
               "font": FONT_STACK, "headingFont": FONT_STACK, "codeFont": "monospace", "baseFontSize": 14,
               "dataframeBorderColor": "#d8dee4", "dataframeHeaderBackgroundColor": "#f6f8fa",
               "dataframeHeaderTextColor": "#5d6773", "greenColor": "#1a7f37", "redColor": "#cf222e",
               "orangeColor": "#9a6700", "blueColor": "#0969da",
               "chartCategoricalColors": ["#0969da", "#d1631b", "#8250df", "#9a7b00", "#bf3989"]}
LIGHT_SIDEBAR = {"backgroundColor": "#ffffff", "secondaryBackgroundColor": "#f6f8fa", "textColor": "#0f1720"}
CLASSIC_THEME = LIGHT_THEME  # nome storico


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
    """Preferenza dell'aspetto: "scuro" (predefinito dal 2026-11-24: è l'aspetto del prodotto), "chiaro" o
    "sistema". Il vecchio formato {"terminal": vero/falso} dell'interruttore vale come scelta esplicita: scuro/chiaro."""
    try:
        prefs = json.loads(prefs_path(PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "scuro"
    if not isinstance(prefs, dict):
        return "scuro"
    if prefs.get("aspetto") in APPEARANCES:
        return prefs["aspetto"]
    if "terminal" in prefs:
        return "scuro" if prefs["terminal"] else "chiaro"
    return "scuro"


def system_scheme() -> str | None:
    """"dark"/"light" del Mac come lo vede il browser (cookie scritto da `scheme_script`); None se non ancora noto
    (prima apertura, test, cookie bloccati)."""
    try:
        value = st.context.cookies.get(SCHEME_COOKIE)
    except Exception:  # noqa: BLE001 - fuori da una sessione Streamlit
        return None
    return value if value in ("dark", "light") else None


def _terminal_wanted() -> bool:
    """Tema scuro? Con "Segui il sistema" e schema ancora ignoto: scuro, l'aspetto del prodotto."""
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
        classic = LIGHT_THEME if section == "theme" else LIGHT_SIDEBAR
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


# ---------------------------------------------------------------- token (sistema visivo 2026-11-24, terminale da prodotto)
# Scelta dell'utente il 2026-11-24 tra tre direzioni mostrate: "terminale moderno" scuro (tipo i terminali finanziari
# web), rifinito come prodotto da vendere. SOSTITUISCE lo stile Apple del 2026-11-23. Scuro = base: fondo #0b0e13,
# superfici #11151c/#171c24, bordi sottili e niente ombre sulle schede (solo sui menu), angoli piccoli (4/6/8 px),
# accento verde acqua SOLO per interazione e selezione (link, fuoco, scheda attiva, pulsante primario): esiti e
# variazioni usano i colori di stato (pos/neg/warn) con simbolo. Chiaro = stessa struttura, accento #0f766e (>= 4,5:1).
# Numeri in monospazio con cifre tabellari, sempre nel formato italiano (fmt). Testo >= 4,5:1, bordo controlli >= 3:1.
TOKENS_DARK = {
    "bg": "#0b0e13", "surface-1": "#11151c", "surface-2": "#171c24", "surface-hi": "#10302c", "chrome": "#0e1218",
    "border": "#222934", "border-ctl": "#5b6576", "track": "#222934",
    "shadow": "none", "shadow-lg": "0 16px 40px rgba(0,0,0,.55)",
    "text": "#e6edf3", "text-num": "#f0f6fc", "text-2": "#b1bac4", "text-3": "#8b949e", "text-off": "#545d68",
    "accent": "#2dd4bf", "accent-soft": "#10302c", "on-accent": "#04201c", "header-line": "#222934",
    "label": "#8b949e", "fill": "rgba(139,148,158,.14)", "fill-hover": "rgba(139,148,158,.22)",
    "pos": "#3fb950", "neg": "#f85149", "warn": "#d29922", "info": "#58a6ff", "trap": "#f0883e",
}
TOKENS_LIGHT = {
    "bg": "#f3f5f8", "surface-1": "#ffffff", "surface-2": "#f6f8fa", "surface-hi": "#e3f4f1", "chrome": "#ffffff",
    "border": "#d8dee4", "border-ctl": "#8c959f", "track": "#e5e9ee",
    "shadow": "none", "shadow-lg": "0 16px 40px rgba(15,23,32,.16)",
    "text": "#0f1720", "text-num": "#0f1720", "text-2": "#3b4450", "text-3": "#5d6773", "text-off": "#a3acb6",
    "accent": "#0f766e", "accent-soft": "#e3f4f1", "on-accent": "#ffffff", "header-line": "#d8dee4",
    "label": "#5d6773", "fill": "rgba(15,23,32,.06)", "fill-hover": "rgba(15,23,32,.1)",
    "pos": "#1a7f37", "neg": "#cf222e", "warn": "#9a6700", "info": "#0969da", "trap": "#bc4c00",
}
TOKENS_TERMINAL = TOKENS_DARK  # nome storico
TYPE_SCALE = {"fs-micro": "11px", "fs-data": "13px", "fs-body": "14px", "fs-title": "15px", "fs-h2": "17px",
              "fs-h1": "24px", "fs-key": "26px", "fs-big": "20px"}
FONTS = {"mono": '"SF Mono", ui-monospace, SFMono-Regular, Menlo, "JetBrains Mono", Consolas, monospace',
         "sans": 'Inter, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, '
                 'sans-serif'}
RADII = {"r-sm": "4px", "r-md": "6px", "r-lg": "8px"}
TOKENS = TOKENS_DARK if TERMINAL else TOKENS_LIGHT


# palette categoriale (ordine fisso, mai ciclato): blu, arancio, viola, oro, rosa. Niente verde e niente verde acqua:
# il verde è "in rialzo/positivo" e il verde acqua è l'accento dell'interfaccia. Validata con lo script della skill
# dataviz (scuro su #11151c e #0b0e13, chiaro su #ffffff e #f3f5f8). Il 5° colore solo per Confronta.
SERIES_DARK = ["#3b86e8", "#d0732f", "#a371f7", "#a8831a", "#db61a2"]
SERIES_LIGHT = ["#0969da", "#d1631b", "#8250df", "#9a7b00", "#bf3989"]
SERIES = SERIES_DARK if TERMINAL else SERIES_LIGHT


MUTED = "#8b949e"                                   # linee di riferimento (prezzo, mediane, soglie)


INK2 = "#b1bac4" if TERMINAL else "#3b4450"         # riferimenti più marcati


GRID = "#1c222c" if TERMINAL else "#e5e9ee"


STATUS = {"BUY": TOKENS["pos"], "BLOCKED": TOKENS["neg"], "SKIP": MUTED}


ACTION_LABEL = {"BUY": "▲ BUY", "SKIP": "● SKIP", "BLOCKED": "■ BLOCKED"}


DIVERGING = [[0.0, TOKENS["neg"]], [0.5, "#222934" if TERMINAL else "#e5e9ee"], [1.0, SERIES[0]]]


ACCENT = TOKENS["accent"]
AMBER = ACCENT  # nome storico: era l'ambra del terminale


UP, DOWN = TOKENS["pos"], TOKENS["neg"]  # variazioni (sempre con ▲▼, non solo colore)


def _root_css() -> str:
    lines = [f"  --{k}: {v};" for k, v in {**TOKENS, **TYPE_SCALE, **FONTS, **RADII}.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


# ---------------------------------------------------------------- CSS dell'app (iniettato da app.py in ogni pagina)
# NB: mai il carattere "minore di" in questo CSS, nemmeno nei commenti: DOMPurify di st.html scarterebbe tutto lo stile.
BASE_CSS = """
/* un solo carattere: quello di sistema (SF Pro sul Mac), cifre tabellari per i numeri; monospazio solo per i codici */
.stApp { background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased; }
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button { font-family: var(--sans); }
.stMarkdown p, .stMarkdown li { font-size: var(--fs-body); line-height: 1.5; letter-spacing: -.009em; }
h1, h2, h3, h4 { font-family: var(--sans) !important; text-transform: none !important; }
h1 { font-size: var(--fs-h1) !important; font-weight: 700 !important; color: var(--text-num) !important;
  letter-spacing: -.022em !important; padding: .6rem 0 .15rem !important; line-height: 1.15 !important; }
h2 { font-size: 24px !important; font-weight: 700 !important; color: var(--text) !important; letter-spacing: -.018em !important; }
h3 { font-size: var(--fs-h2) !important; font-weight: 650 !important; color: var(--text) !important;
  letter-spacing: -.012em !important; padding-bottom: .1rem !important; }
h4 { font-size: var(--fs-body) !important; font-weight: 650 !important; color: var(--text) !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
  font-size: var(--fs-data) !important; color: var(--text-3) !important; line-height: 1.5; }
[data-testid="stWidgetLabel"] p { font-size: var(--fs-data) !important; color: var(--text-3); font-weight: 500; }
code { font-family: var(--mono) !important; }
.stMarkdown code { color: var(--text); background: var(--fill); font-size: .88em; border-radius: 6px; padding: 1px 5px; }
hr { border-color: var(--border) !important; }
a { text-decoration: none; }
/* numeri in evidenza: st.metric come una scheda */
[data-testid="stMetric"] { background: var(--surface-1); border-radius: var(--r-lg); padding: 16px 18px;
  box-shadow: var(--shadow); }
[data-testid="stMetricLabel"] p { font: 500 var(--fs-data) var(--sans) !important; color: var(--text-3) !important; }
[data-testid="stMetricValue"] { font: 650 26px var(--sans) !important; color: var(--text-num);
  font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
[data-testid="stMetricDelta"] { font-size: var(--fs-micro) !important; }
/* schede (st.tabs), expander, popover */
button[role="tab"] p { font-size: var(--fs-body) !important; font-weight: 500; }
[data-testid="stExpander"] details { border: 0 !important; border-radius: var(--r-md) !important;
  background: var(--surface-1); box-shadow: var(--shadow); overflow: hidden; }
[data-testid="stExpander"] summary { padding-top: 12px; padding-bottom: 12px; }
[data-testid="stExpander"] summary p { font-size: var(--fs-body) !important; font-weight: 550; color: var(--text) !important; }
[data-testid="stExpander"] summary:hover { background: var(--surface-2); }
[data-testid="stPopoverButton"] p { font-size: var(--fs-data) !important; font-weight: 500; }
[data-testid="stPopoverBody"] { background: var(--surface-1) !important; border: 0 !important;
  border-radius: var(--r-md) !important; box-shadow: var(--shadow-lg) !important; }
/* barra in alto (menu, marchio): come la barra di una finestra, unita alla barra di ricerca sotto */
[data-testid="stHeader"] { background: var(--chrome) !important; border-bottom: 0; }
[data-testid="stTopNav"] *, [data-testid="stTopNavSection"] * { font-size: var(--fs-data) !important;
  font-weight: 500; text-transform: none !important; letter-spacing: -.005em !important; }
[data-testid="stSidebar"], [data-testid="stExpandSidebarButton"] { display: none !important; }
[data-testid="stHeaderLogo"], [data-testid="stLogo"] { height: 28px !important; max-height: 28px !important;
  width: auto !important; max-width: none !important; }
/* chip dei multiselect: pillole grigie piene (react-aria in Streamlit 1.66) */
[data-testid="stMultiSelectTagsContainer"] [data-tag] {
  background: var(--surface-2) !important; border: 1px solid var(--border) !important; border-radius: 999px !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] *, [data-testid="stMultiSelectTagsContainer"] [data-tag] {
  color: var(--text) !important; fill: var(--text) !important; font-size: var(--fs-micro); }
[data-testid="stMultiSelectTagsContainer"] [data-tag]:focus-visible { outline: 2px solid var(--accent); }
/* pulsanti a pillola: primario blu pieno, secondario grigio pieno senza bordo */
.stButton button, .stDownloadButton button, .stFormSubmitButton button, [data-testid="stPopoverButton"] {
  font-weight: 500; border-radius: 999px !important; transition: background .15s, transform .1s; }
.stButton button:active, .stDownloadButton button:active, [data-testid="stPopoverButton"]:active { transform: scale(.98); }
.stButton button[kind="secondary"], .stDownloadButton button[kind="secondary"], [data-testid="stPopoverButton"],
.stFormSubmitButton button[kind="secondaryFormSubmit"] {
  border-color: transparent !important; background: var(--fill) !important; color: var(--text) !important; }
.stButton button[kind="secondary"]:hover, .stDownloadButton button[kind="secondary"]:hover,
[data-testid="stPopoverButton"]:hover { background: var(--fill-hover) !important; }
.stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover { filter: brightness(1.08); }
/* campi: angoli ampi, anello di focus blu morbido */
[data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
[data-testid="stTextAreaRootElement"] { border-radius: 10px !important; }
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] { display: none !important; }
.stApp input:focus-visible, .stApp textarea:focus-visible { outline: none !important; }
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within { box-shadow: 0 0 0 4px var(--accent-soft);
  border-color: var(--accent) !important; }
/* pagina: margini laterali 40 px, larghezza massima per schermi grandi, aria tra i blocchi */
.block-container { padding: 3.75rem 40px 3.8rem !important; max-width: 1480px !important; }
[data-testid="stVerticalBlock"] { gap: 16px; }
/* schede (pannelli, components.panel): bianche su fondo grigio, senza bordo, angoli ampi, ombra appena percettibile */
div[data-testid="stVerticalBlock"]:has(> .stElementContainer > .stHtml > .qe-panel-title) {
  background: var(--surface-1); border: 0 !important; border-radius: var(--r-lg) !important;
  box-shadow: var(--shadow); padding: 18px 20px 20px !important; gap: 12px; }
.modebar-container { display: none !important; }
/* nessun HTML più largo del suo riquadro: le tabelle larghe scorrono dentro, non coprono i vicini */
.stHtml { max-width: 100%; overflow-x: auto; }
/* tabelle st.dataframe: angoli come le schede */
[data-testid="stDataFrame"] > div { border-radius: var(--r-md); }
/* toast sopra la barra di stato */
[data-testid="stToastContainer"] { bottom: 2.8rem !important; z-index: 1000200 !important; }
[data-testid="stToast"] { border: 0; border-radius: var(--r-md) !important; font-size: var(--fs-body);
  box-shadow: var(--shadow-lg); }
/* attesa: barra sottile animata sotto la testata mentre lo script gira */
body:has([data-testid="stStatusWidget"]) [data-testid="stHeader"]::after { content: ""; position: absolute; left: 0;
  right: 0; bottom: -1px; height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
  background-size: 40% 100%; background-repeat: no-repeat; animation: qe-run 1s linear infinite; }
@keyframes qe-run { from { background-position: -40% 0 } to { background-position: 140% 0 } }
[data-testid="stStatusWidget"] * { color: var(--accent) !important; font-size: var(--fs-micro) !important; }
"""

COMPONENTS_CSS = """
.pos { color: var(--pos); } .neg { color: var(--neg); } .flat { color: var(--text-3); }
.off, .qe-na { color: var(--text-off); }
/* titolo della scheda (pannello) */
.qe-panel-title { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin: 0;
  font: 650 var(--fs-title)/1.3 var(--sans); color: var(--text-num); letter-spacing: -.02em; }
.qe-panel-title .l { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.qe-panel-title .r { color: var(--text-3); font: 400 var(--fs-micro) var(--sans); letter-spacing: 0; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.qe-panel-title .r a { color: var(--info); font-weight: 400; }
.qe-panel-title .r a:hover { text-decoration: underline; }
/* tabelle: righe ariose, separatori sottili, intestazioni grigie */
.qe-tablewrap { overflow-x: auto; max-width: 100%; }
table.qe-dense { width: 100%; border-collapse: collapse; table-layout: auto; font: var(--fs-data)/1.4 var(--sans);
  font-variant-numeric: tabular-nums lining-nums; margin: 0; letter-spacing: -.005em; }
table.qe-dense th { position: sticky; top: 0; background: var(--surface-1); color: var(--label);
  font-size: var(--fs-micro); font-weight: 500; text-align: left; padding: 6px 12px 8px;
  border-bottom: 1px solid var(--border); white-space: nowrap; }
table.qe-dense td { padding: 9px 12px; border-bottom: 1px solid var(--border); color: var(--text-num);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 320px; }
table.qe-dense tr:last-child td { border-bottom: 0; }
table.qe-dense td.t { color: var(--text); }
table.qe-dense td.n, table.qe-dense th.n { text-align: right; }
table.qe-dense td.na { color: var(--text-off); text-align: right; }
table.qe-dense td.src-fb { font-style: italic; color: var(--warn, inherit); }
table.qe-dense td .qe-src { font-size: 0.75em; margin-left: 2px; vertical-align: super; }
table.qe-dense td.fill { width: 100%; max-width: 0; }
table.qe-dense tr:has(a.qe-tk):hover td { background: var(--surface-2); }
table.qe-dense tr.me td { background: var(--surface-hi); }
table.qe-dense tr.miss td { color: var(--text-off); }
a.qe-tk { color: var(--info); font-family: var(--sans); font-weight: 600; letter-spacing: .01em; }
a.qe-tk:hover { text-decoration: underline; }
.qe-more, .qe-empty-line { color: var(--text-3); font-size: var(--fs-micro); padding: 8px 12px; }
.qe-empty-line { font-size: var(--fs-data); color: var(--text-2); }
/* distintivi: pillole piene con colore tenue */
.qe-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 9px; border-radius: 999px;
  font: 600 var(--fs-micro)/1.45 var(--sans); white-space: nowrap; vertical-align: baseline;
  background: color-mix(in srgb, currentColor 13%, transparent); }
.qe-badge.pos { color: var(--pos); } .qe-badge.neg { color: var(--neg); } .qe-badge.warn { color: var(--warn); }
.qe-badge.neutral { color: var(--text-2); } .qe-badge.info { color: var(--info); } .qe-badge.trap { color: var(--trap); }
.qe-badge.off { color: var(--text-3); background: transparent; box-shadow: inset 0 0 0 1px var(--border); }
.qe-badge.accent { color: var(--accent); }
/* stato vuoto */
.qe-empty { border-radius: var(--r-md); padding: 18px 20px; color: var(--text-2); font-size: var(--fs-body);
  background: var(--surface-1); box-shadow: var(--shadow); line-height: 1.5; }
.qe-empty b { display: block; color: var(--text-num); font-weight: 650; margin-bottom: 4px; font-size: var(--fs-title);
  letter-spacing: -.015em; }
.qe-empty details { margin-top: 8px; font-size: var(--fs-micro); color: var(--text-3); }
.qe-empty summary { cursor: pointer; }
[data-testid="stVerticalBlock"]:has(> .stElementContainer > .stHtml > .qe-panel-title) .qe-empty {
  background: var(--surface-2); box-shadow: none; }
/* avvisi in linea: riquadri tinti, senza barre */
.qe-notice { display: flex; gap: 10px; align-items: baseline; padding: 11px 14px; margin: 4px 0; border-radius: var(--r-md);
  background: var(--surface-2); color: var(--text); font-size: var(--fs-body); line-height: 1.5; }
.qe-notice i { font: 700 var(--fs-data) var(--sans); font-style: normal; }
.qe-notice.neg { background: color-mix(in srgb, var(--neg) 9%, var(--surface-1)); } .qe-notice.neg i { color: var(--neg); }
.qe-notice.warn { background: color-mix(in srgb, var(--warn) 10%, var(--surface-1)); } .qe-notice.warn i { color: var(--warn); }
.qe-notice.info { background: color-mix(in srgb, var(--info) 8%, var(--surface-1)); } .qe-notice.info i { color: var(--info); }
.qe-notice.pos { background: color-mix(in srgb, var(--pos) 9%, var(--surface-1)); } .qe-notice.pos i { color: var(--pos); }
/* testata dell'azienda: nome grande, ticker accanto, prezzo grande a destra; sotto, i numeri chiave */
.qe-head { display: grid; grid-template-columns: 1fr auto; gap: 6px 28px; padding: 24px 26px 22px;
  border-radius: var(--r-lg); background: var(--surface-1); box-shadow: var(--shadow); }
/* testata vecchia (una riga di span con etichetta in grassetto) */
.qe-head:not(:has(> .qe-head-id)) { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 0;
  font: var(--fs-data) var(--sans); font-variant-numeric: tabular-nums; padding: 14px 18px; }
.qe-head:not(:has(> .qe-head-id)) > span { margin-right: 22px; white-space: nowrap; }
.qe-head:not(:has(> .qe-head-id)) b { color: var(--label); font-weight: 500; margin-right: 6px; }
.qe-head:not(:has(> .qe-head-id)) .big { font-size: var(--fs-big); color: var(--text-num); }
.qe-head-id { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; min-width: 0; }
.qe-head .nm { order: 1; font: 700 var(--fs-key)/1.15 var(--sans); color: var(--text-num); letter-spacing: -.025em; }
.qe-head .tk { order: 2; font: 600 var(--fs-body)/1.4 var(--sans); color: var(--text-3); letter-spacing: .01em; }
.qe-head-id:has(.nm:empty) .tk { font: 700 var(--fs-key)/1.15 var(--sans); color: var(--text-num); letter-spacing: -.02em; }
.qe-head .meta { order: 3; flex-basis: 100%; color: var(--text-3); font-size: var(--fs-data); }
.qe-head .src { display: block; color: var(--text-3); font-size: var(--fs-micro); margin-top: 4px; }
.qe-head-px { text-align: right; white-space: nowrap; }
.qe-head .px { font: 700 var(--fs-key)/1.15 var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums;
  letter-spacing: -.025em; }
.qe-head .chg { font: 600 var(--fs-body) var(--sans); font-variant-numeric: tabular-nums; margin-left: 10px; }
.qe-kv { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 12px 32px; margin: 14px 0 0; padding-top: 16px;
  border-top: 1px solid var(--border); }
.qe-kv > div { white-space: nowrap; }
.qe-kv dt { color: var(--label); font: 400 var(--fs-micro) var(--sans); margin-bottom: 2px; }
.qe-kv dd { margin: 0; font: 600 var(--fs-body) var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums;
  letter-spacing: -.01em; }
.qe-kv.big dd { font-size: var(--fs-big); letter-spacing: -.02em; }
.qe-kv a { color: var(--info); }
.qe-kv .rng { display: inline-block; position: relative; width: 70px; height: 4px; background: var(--track);
  border-radius: 2px; vertical-align: middle; margin: 0 6px; }
.qe-kv .rng b { position: absolute; top: -3px; width: 10px; height: 10px; margin-left: -4px; border-radius: 50%;
  background: var(--surface-1); box-shadow: 0 0 0 2px var(--accent); }
.qe-result { padding: 18px 20px; border-radius: var(--r-lg); background: var(--surface-1); box-shadow: var(--shadow); }
.qe-result .qe-kv { margin: 0; padding-top: 0; border-top: 0; }
.qe-result-title { font: 650 var(--fs-title) var(--sans); color: var(--text-num); margin-bottom: 10px; letter-spacing: -.02em; }
.qe-result-note { color: var(--text-3); font-size: var(--fs-micro); margin-top: 10px; }
/* verdetto: scheda pulita, il giudizio grande e colorato è il protagonista */
.qe-verdict { border-radius: var(--r-lg); background: var(--surface-1); box-shadow: var(--shadow); padding: 20px 24px 22px; }
.qe-verdict.off { box-shadow: inset 0 0 0 1px var(--border); }
.qe-verdict .body { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 12px 36px; }
.qe-verdict .label { font: 700 var(--fs-key)/1.15 var(--sans); color: var(--text-2); letter-spacing: -.025em; }
.qe-verdict .label .sym { margin-right: 10px; }
.qe-verdict .label.pos { color: var(--pos); } .qe-verdict .label.warn { color: var(--warn); }
.qe-verdict .label.neg { color: var(--neg); } .qe-verdict .label.trap { color: var(--trap); }
.qe-verdict .label.off { color: var(--text-off); }
.qe-verdict .rule { color: var(--text-3); font-size: var(--fs-body); margin: 6px 0 16px; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.qe-verdict .sub { font: 600 var(--fs-micro) var(--sans); color: var(--label); margin: 6px 0 4px; }
.qe-verdict ul { margin: 0 0 6px; padding-left: 18px; font-size: var(--fs-data); color: var(--text); }
.qe-verdict li.more { color: var(--text-3); list-style: none; margin-left: -18px; }
.qe-verdict .val { font: var(--fs-data) var(--sans); color: var(--text-num); font-variant-numeric: tabular-nums; }
.qe-verdict .foot { color: var(--text-3); font-size: var(--fs-micro); margin-top: 10px; }
/* pilastri: barra di avanzamento continua e arrotondata */
.qe-pillar { display: flex; align-items: center; gap: 14px; font-size: var(--fs-data); padding: 5px 0; white-space: nowrap; }
.qe-pillar .lab { width: 13.5em; flex-shrink: 0; color: var(--text); overflow: hidden; text-overflow: ellipsis; }
.qe-pillar .bar { display: inline-flex; gap: 0; border-radius: 3px; overflow: hidden; }
.qe-pillar .bar i { width: 11px; height: 6px; background: var(--track); }
.qe-pillar .bar i.on { background: var(--text-3); }
.qe-pillar .bar.pos i.on { background: var(--pos); } .qe-pillar .bar.neg i.on { background: var(--neg); }
.qe-pillar .sc { font-weight: 650; color: var(--text-num); width: 2.8em; text-align: right;
  font-variant-numeric: tabular-nums; }
.qe-pillar .gr { color: var(--text-2); }
.qe-pillar .cov { color: var(--text-3); font-size: var(--fs-micro); }
/* cosa è cambiato */
.qe-changes .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 32px; }
.qe-changes .sub { font: 600 var(--fs-micro) var(--sans); color: var(--label); margin-bottom: 6px; }
.qe-changes ul { margin: 0; padding: 0; list-style: none; font-size: var(--fs-data); }
.qe-changes li { padding: 3px 0; }
.qe-changes .none { color: var(--text-3); font-size: var(--fs-data); }
.qe-changes .line { margin-top: 10px; font-size: var(--fs-data); color: var(--text-2); }
/* avvisi */
.qe-alert { display: flex; align-items: baseline; gap: 10px; padding: 9px 0; font-size: var(--fs-data);
  border-bottom: 1px solid var(--border); color: var(--text-2); }
.qe-alert:last-child { border-bottom: 0; }
.qe-alert.new { color: var(--text-num); }
.qe-alert .dot { width: 10px; color: var(--accent); flex-shrink: 0; }
.qe-alert .tk { font-weight: 600; width: 5.5em; flex-shrink: 0; }
.qe-alert .msg { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qe-alert .age { color: var(--text-3); font-size: var(--fs-micro); white-space: nowrap; }
/* briciole ("dove sono") */
.qe-crumbs { font: var(--fs-micro) var(--sans); color: var(--text-3); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.qe-crumbs i { font-style: normal; margin: 0 6px; color: var(--text-off); }
.qe-crumbs b { color: var(--text-2); font-weight: 500; }
"""

# cornice: barra di ricerca e controllo segmentato delle pagine sotto il menu (unita alla barra in alto), barra di stato
_SEARCH_ICON = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' "
                "viewBox='0 0 24 24' fill='none' stroke='%238e8e93' stroke-width='2.2' stroke-linecap='round'%3E"
                "%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.5-3.5'/%3E%3C/svg%3E\")")
SHELL_CSS = """
/* il contenitore con la chiave sta dentro un stLayoutWrapper alto quanto lui: fisso è il wrapper */
[data-testid="stLayoutWrapper"]:has(> .st-key-qe_shell), .st-key-qe_shell { position: sticky; top: 3.75rem;
  z-index: 990; }
/* fascia a tutta larghezza: ombra piena oltre i bordi e ritaglio, riga sottile in fondo */
.st-key-qe_shell { background: var(--chrome); padding: 4px 0 10px !important; margin-bottom: 8px !important;
  gap: 4px !important; box-shadow: 0 0 0 100vmax var(--chrome), 0 1px 0 100vmax var(--border);
  clip-path: inset(0 -100vmax -1px -100vmax); }
.st-key-qe_shell [data-testid="stHorizontalBlock"] { gap: 16px !important; }
/* ricerca: campo grigio pieno a pillola, come la ricerca di sistema */
input[aria-label="Comando"] { font: var(--fs-body) var(--sans) !important; color: var(--text-num) !important;
  caret-color: var(--accent); padding-left: 36px !important; background: transparent SEARCH_ICON no-repeat 13px center !important; }
.st-key-qe_shell [data-testid="stTextInputRootElement"] { border-color: transparent !important; border-radius: 10px !important;
  background: var(--fill) !important; }
.st-key-qe_shell [data-testid="stTextInputRootElement"]:focus-within { background: var(--surface-1) !important;
  border-color: var(--accent) !important; box-shadow: 0 0 0 4px var(--accent-soft); }
input[aria-label="Comando"]::placeholder { color: var(--text-3) !important; opacity: 1; }
/* pagine principali: controllo segmentato (F1-F8 / Alt+1-8 restano sulla tastiera, nel suggerimento) */
.st-key-qe_fkeys { gap: 0 !important; flex-wrap: nowrap !important; overflow: hidden; width: fit-content !important;
  max-width: 100%; flex: 0 1 auto !important; background: var(--fill); border-radius: 9px; padding: 2px !important; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"] {
  background: transparent; border: 0; border-radius: 7px; padding: 4px 13px !important;
  min-height: 0 !important; margin: 0 !important; gap: 0 !important; white-space: nowrap; transition: background .15s; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"] span:has(> [data-testid="stIconMaterial"]) { display: none; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"] * { color: var(--text) !important;
  font: 500 var(--fs-data)/1.4 var(--sans) !important; white-space: nowrap; letter-spacing: -.005em; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"]:hover { background: var(--fill); }
.st-key-qe_fkey_on [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"]:hover {
  background: var(--surface-1) !important; box-shadow: 0 1px 3px rgba(0,0,0,.12), 0 0 0 .5px rgba(0,0,0,.04); }
.st-key-qe_fkey_on [data-testid="stPageLink-NavLink"] * { color: var(--text-num) !important; font-weight: 600 !important; }
.st-key-qe_fkeys .stElementContainer, .st-key-qe_fkeys [data-testid="stVerticalBlock"] { width: auto !important; }
.st-key-qe_fkey_on { width: auto !important; flex: 0 0 auto !important; }
.st-key-qe_crumbs { min-height: 0; }
.qe-status { position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000100; display: flex; gap: 0; align-items: center;
  padding: 7px 40px; font: 11px/1.4 var(--sans); white-space: nowrap; overflow: hidden;
  background: var(--chrome); color: var(--text-3); border-top: 1px solid var(--border);
  font-variant-numeric: tabular-nums; }
.qe-status > span { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; padding-right: 10px; }
.qe-status > span + span::before { content: "·"; color: var(--text-off); margin-right: 10px; }
.qe-status > span.keep { flex-shrink: 0; }
.qe-status > span.low { flex-shrink: 1; }
.qe-status > span.lowest { flex-shrink: 1000; }
.qe-status b { color: var(--text); font-weight: 600; }
.qe-status .demo { color: var(--warn); font-weight: 700; background: color-mix(in srgb, var(--warn) 13%, transparent);
  border-radius: 999px; padding: 0 7px; }
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
""".replace("SEARCH_ICON", _SEARCH_ICON)

# stile "terminale da prodotto" (2026-11-24, come l'interfaccia web: web/src/styles/app.css): superfici piatte con bordi
# sottili e niente ombre, angoli piccoli, intestazioni di schede e tabelle in maiuscoletto, numeri in monospazio.
# Viene dopo gli altri blocchi e li corregge (stessi selettori, già verificati da ui/streamlit_contract.py).
PRODUCT_CSS = """
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button { letter-spacing: 0; }
.stMarkdown p, .stMarkdown li { font-size: var(--fs-body); letter-spacing: 0; }
h1 { font-size: var(--fs-h1) !important; font-weight: 600 !important; letter-spacing: -.01em !important; }
h2 { font-size: 19px !important; font-weight: 600 !important; letter-spacing: -.01em !important; }
h3 { font-size: var(--fs-h2) !important; font-weight: 600 !important; letter-spacing: 0 !important; }
h4 { font-weight: 600 !important; }
.stMarkdown code { border-radius: var(--r-sm); }
[data-testid="stMetric"] { box-shadow: none; border: 1px solid var(--border); padding: 12px 14px; }
[data-testid="stMetricLabel"] p { font: 600 var(--fs-micro) var(--sans) !important; letter-spacing: .06em; text-transform: uppercase;
  color: var(--label) !important; }
[data-testid="stMetricValue"] { font: 600 22px var(--mono) !important; letter-spacing: 0; }
[data-testid="stExpander"] details { box-shadow: none; border: 1px solid var(--border) !important; border-radius: var(--r-lg) !important; }
[data-testid="stExpander"] summary p { font-weight: 500; }
[data-testid="stPopoverBody"] { border: 1px solid var(--border) !important; }
[data-testid="stMultiSelectTagsContainer"] [data-tag] { border-radius: var(--r-sm) !important; }
[data-testid="stButtonGroup"] button { border-radius: var(--r-md) !important; }
.stButton button, .stDownloadButton button, .stFormSubmitButton button, [data-testid="stPopoverButton"] {
  border-radius: var(--r-md) !important; font-size: var(--fs-data); }
.stButton button:active, .stDownloadButton button:active, [data-testid="stPopoverButton"]:active { transform: translateY(1px); }
.stButton button[kind="secondary"], .stDownloadButton button[kind="secondary"], [data-testid="stPopoverButton"],
.stFormSubmitButton button[kind="secondaryFormSubmit"] {
  border: 1px solid var(--border) !important; background: var(--surface-2) !important; }
.stButton button[kind="secondary"]:hover, .stDownloadButton button[kind="secondary"]:hover,
[data-testid="stPopoverButton"]:hover { border-color: var(--border-ctl) !important; background: var(--surface-2) !important; }
.stButton button[kind="primary"] p, .stFormSubmitButton button[kind="primaryFormSubmit"] p { font-weight: 600; color: var(--on-accent); }
[data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
[data-testid="stTextAreaRootElement"] { border-radius: var(--r-md) !important; }
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within { box-shadow: 0 0 0 3px var(--accent-soft); }
.block-container { padding: 3.75rem 24px 3.8rem !important; max-width: 1600px !important; }
[data-testid="stVerticalBlock"] { gap: 14px; }
div[data-testid="stVerticalBlock"]:has(> .stElementContainer > .stHtml > .qe-panel-title) {
  box-shadow: none; border: 1px solid var(--border) !important; padding: 14px 16px 16px !important; }
[data-testid="stToast"] { border: 1px solid var(--border); }
/* titoli delle schede e tabelle: maiuscoletto grigio, numeri in monospazio */
.qe-panel-title { font: 600 var(--fs-micro)/1.3 var(--sans); letter-spacing: .07em; text-transform: uppercase; color: var(--label); }
.qe-panel-title .r { text-transform: none; letter-spacing: 0; }
.qe-panel-title .r a { color: var(--accent); }
table.qe-dense { letter-spacing: 0; }
table.qe-dense th { font-weight: 600; letter-spacing: .06em; text-transform: uppercase; padding: 6px 10px 7px; }
table.qe-dense td { padding: 7px 10px; color: var(--text); }
table.qe-dense td.n { font-family: var(--mono); font-size: 12.5px; color: var(--text-num); }
table.qe-dense tr:has(a.qe-tk):hover td { background: var(--fill); }
a.qe-tk { color: var(--accent); font-family: var(--mono); }
.qe-badge { border-radius: var(--r-sm); padding: 1px 7px; background: color-mix(in srgb, currentColor 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, currentColor 32%, transparent); }
.qe-empty { box-shadow: none; border: 1px dashed var(--border-ctl); border-radius: var(--r-lg); font-size: var(--fs-data); }
.qe-empty b { font-size: var(--fs-body); font-weight: 600; letter-spacing: 0; }
[data-testid="stVerticalBlock"]:has(> .stElementContainer > .stHtml > .qe-panel-title) .qe-empty { background: transparent; }
.qe-notice { border: 1px solid var(--border); background: var(--surface-1); color: var(--text-2); font-size: var(--fs-data); }
.qe-notice.neg { border-color: color-mix(in srgb, var(--neg) 40%, var(--border)); background: color-mix(in srgb, var(--neg) 7%, var(--surface-1)); }
.qe-notice.warn { border-color: color-mix(in srgb, var(--warn) 40%, var(--border)); background: color-mix(in srgb, var(--warn) 7%, var(--surface-1)); }
.qe-notice.info { background: var(--surface-1); }
.qe-notice.pos { border-color: color-mix(in srgb, var(--pos) 40%, var(--border)); background: color-mix(in srgb, var(--pos) 7%, var(--surface-1)); }
.qe-head, .qe-result, .qe-verdict { box-shadow: none; border: 1px solid var(--border); padding: 18px 20px; }
.qe-head .nm { font: 600 var(--fs-key)/1.15 var(--sans); letter-spacing: -.01em; }
.qe-head .tk { font: 600 var(--fs-data)/1.4 var(--mono); color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  border-radius: var(--r-sm); padding: 0 6px; }
.qe-head .px { font: 600 var(--fs-key)/1.15 var(--mono); letter-spacing: 0; }
.qe-head .chg { font-family: var(--mono); }
.qe-kv dt { font: 500 var(--fs-micro) var(--sans); letter-spacing: .05em; text-transform: uppercase; }
.qe-kv dd { font: 600 var(--fs-body) var(--mono); letter-spacing: 0; }
.qe-kv .rng { height: 3px; }
.qe-kv .rng b { top: -4px; width: 3px; height: 11px; margin-left: -1px; border-radius: 1px; background: var(--accent); box-shadow: none; }
.qe-result-title, .qe-verdict .sub, .qe-changes .sub { font: 600 var(--fs-micro) var(--sans); letter-spacing: .07em; text-transform: uppercase;
  color: var(--label); }
.qe-verdict .label { font-weight: 600; letter-spacing: -.01em; }
.qe-verdict .rule { font-size: var(--fs-data); }
.qe-pillar .bar i { width: 11px; height: 4px; }
.qe-pillar .sc { font-family: var(--mono); font-weight: 600; }
.qe-alert .tk { font-family: var(--mono); }
/* cornice: ricerca come campo con bordo, pagine come schede con il bordo */
.st-key-qe_shell [data-testid="stTextInputRootElement"] { border: 1px solid var(--border) !important; border-radius: var(--r-md) !important;
  background: var(--surface-2) !important; }
.st-key-qe_shell [data-testid="stTextInputRootElement"]:focus-within { box-shadow: 0 0 0 3px var(--accent-soft); }
input[aria-label="Comando"] { font: var(--fs-data) var(--sans) !important; }
.st-key-qe_fkeys { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-md); }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"] { border-radius: var(--r-sm); padding: 3px 10px !important; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"] * { color: var(--text-3) !important; }
.st-key-qe_fkeys [data-testid="stPageLink-NavLink"]:hover * { color: var(--text) !important; }
.st-key-qe_fkey_on [data-testid="stPageLink-NavLink"], .st-key-qe_fkey_on [data-testid="stPageLink-NavLink"]:hover {
  box-shadow: inset 0 0 0 1px var(--border) !important; }
.qe-status { font: 11px/1.4 var(--mono); padding: 7px 24px; }
.qe-status a, .qe-honest a, p.qe-limits a, .qe-onboard a { color: var(--accent) !important; }
.qe-onboard b { color: var(--text-num); font-weight: 600; font-size: var(--fs-body); }
.qe-onboard p { margin: 2px 0 10px; color: var(--text-3); font-size: var(--fs-data); }
.qe-onboard code { color: var(--accent); }
.qe-pick { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: var(--fs-data); }
p.qe-limits { margin: 0 0 10px; font-size: var(--fs-data); color: var(--text-2); line-height: 1.6; }
.qe-status .demo { border-radius: var(--r-sm); background: transparent; border: 1px solid color-mix(in srgb, var(--warn) 45%, transparent);
  padding: 0 6px; letter-spacing: .06em; }
"""

APP_CSS = "<style>\n" + _root_css() + BASE_CSS + COMPONENTS_CSS + SHELL_CSS + PRODUCT_CSS + "</style>"
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
    # stesso carattere dell'app, griglia appena visibile, assi e riquadro del passaggio del mouse coi token del tema
    fig.update_layout(font=dict(family=FONTS["sans"], size=12, color=TOKENS["text-2"]),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      hoverlabel=dict(bgcolor=TOKENS["surface-1"], bordercolor=TOKENS["border"],
                                      font=dict(family=FONTS["sans"], color=TOKENS["text"], size=12)))
    fig.update_xaxes(linecolor=TOKENS["border"], tickcolor=TOKENS["border"], zerolinecolor=TOKENS["border"])
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=TOKENS["border"], linecolor=TOKENS["border"])
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
