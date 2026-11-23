"""Componenti dell'interfaccia (docs/revisione_grafica.md §3 e §7): formati dei numeri in italiano e pezzi di HTML.

Un solo punto di formattazione: `fmt` (stringa), `fmt_html` (con colore e "–" grigio), `fmt_table` (tabella di
stringhe per `st.dataframe`), `num_column` (colonna numerica ordinabile). Mai `None`, `nan` o `-0,0` a schermo:
i valori mancanti diventano "–".

Le funzioni `*_html` restituiscono HTML che usa le classi e le variabili CSS di `ui/common.py` (APP_CSS, iniettato
da app.py in ogni pagina): si disegnano con `st.html(...)`. I colori stanno nei token (`--pos`, `--neg`, `--accent`...),
quindi lo stesso HTML va bene nell'aspetto terminale e in quello chiaro. Nessuna informazione solo col colore:
variazioni con ▲▼■, esiti con ✓ ● ✗, stati con simbolo + parola.

Questo modulo non importa `ui.common` (è `ui.common` che importa da qui): si può usare da qualunque pagina.
"""
from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import streamlit as st

# formati puri (senza Streamlit): stanno in modules/formats.py, usati anche dal sito pubblico e dai rapporti
from modules.formats import (  # noqa: F401 - riesportati per le pagine
    CURRENCY_SYMBOL, DIGITS, KINDS, MINUS, NA, _date, _missing, _no_signed_zero, _num, _symbol, fmt, it_number, tone_of,
)


def na_html(na: str = NA) -> str:
    return f'<span class="qe-na">{html.escape(na)}</span>'


def fmt_html(v, kind: str = "num", *, color: bool | None = None, **kw) -> str:
    """Come `fmt`, in HTML: mancante = "–" grigio; con `signed`/`arrow` (o `color=True`) verde/rosso secondo il
    segno, sempre insieme al segno e alla freccia."""
    text = fmt(v, kind, **kw)
    if _missing(v) or (kind not in ("text", "date", "date_short") and _num(v) is None):
        return na_html(kw.get("na", NA))
    if color if color is not None else (kw.get("signed") or kw.get("arrow")):
        return f'<span class="{tone_of(v)}">{html.escape(text)}</span>'
    return html.escape(text)


def signed_pct_html(v, digits: int = 1) -> str:
    """Variazione percentuale con freccia e colore: ▲ +1,6% / ▼ −10,6% / – ."""
    return fmt_html(v, "pct", digits=digits, arrow=True)


METRIC_KINDS = {"pct": "pct", "x": "mult", "money": "money", "int": "int", "num": "num", "date": "date"}


def fmt_metric(key: str, v, *, html_out: bool = False, currency: str | None = "USD") -> str:
    """Valore di una metrica del catalogo (`modules.metrics`) nel formato giusto per il suo tipo, in italiano.
    Sostituisce `metrics.format_value` nell'interfaccia (che resta in inglese per log e rapporti)."""
    from modules.metrics import METRIC_BY_KEY

    m = METRIC_BY_KEY.get(key)
    kind = METRIC_KINDS.get(m.fmt if m else "num", "num")
    kw = {"currency": currency} if kind == "money" else {}
    return fmt_html(v, kind, **kw) if html_out else fmt(v, kind, **kw)


# ================================================================== tabelle per st.dataframe
ColumnSpec = "str | tuple[str, str] | tuple[str, str, dict] | Callable"


def _spec(spec) -> tuple[str | None, object, dict]:
    """(intestazione, tipo, opzioni) da "pct" | ("Margine", "pct") | ("Margine", "pct", {"signed": True})."""
    if isinstance(spec, tuple):
        return spec[0], spec[1], (spec[2] if len(spec) > 2 else {})
    return None, spec, {}


def fmt_table(df: pd.DataFrame, columns: dict, *, na: str = NA) -> pd.DataFrame:
    """Tabella di sole stringhe già formattate (per `st.dataframe` in sola lettura o con valori misti).

    `columns`: colonna -> tipo di `fmt` | (intestazione, tipo) | (intestazione, tipo, opzioni di fmt) |
    funzione(valore) -> str. Le colonne escono nell'ordine dato, rinominate con l'intestazione se c'è; una colonna
    assente diventa tutta "–". Ordinare PRIMA di formattare (le stringhe si ordinano come testo).
    """
    out = {}
    for col, spec in columns.items():
        head, kind, opts = _spec(spec)
        values = df[col] if col in df else pd.Series([None] * len(df), index=df.index)
        if callable(kind):
            series = values.map(lambda v, f=kind: na if _missing(v) else str(f(v)))
        else:
            series = values.map(lambda v, k=kind, o=opts: fmt(v, k, na=na, **o))
        out[head or col] = series
    return pd.DataFrame(out, index=df.index)


def num_column(label: str, kind: str = "num", *, digits: int | None = None, help: str | None = None,  # noqa: A002
               width: str | int | None = None):
    """Colonna numerica ORDINABILE per `st.dataframe` (column_config): i valori restano numeri, il browser li scrive
    nel formato locale (la cornice imposta l'italiano e il punto delle migliaia anche sotto 10.000, che l'italiano
    del browser ometterebbe: "1.950,73"; vedi shell._keyboard). `pct`: valori in frazione (0,123 -> 12,3%). Per i
    multipli l'unità va nell'intestazione ("P/E (x)"). Celle vuote per i mancanti."""
    d = DIGITS.get(kind, 2) if digits is None else digits
    if kind == "pct":
        return st.column_config.NumberColumn(label, format="percent", step=10 ** -(d + 2), help=help, width=width)
    return st.column_config.NumberColumn(label, format="localized", step=10 ** -d if d else 1, help=help,
                                         width=width)


def table_height(n_rows: int, max_rows: int = 15) -> int:
    """Altezza di `st.dataframe` per `n_rows` righe (mai righe vuote in fondo, al massimo `max_rows`)."""
    return int(min(max(n_rows, 1), max_rows) * 35 + 38)


# ================================================================== pannello
@contextmanager
def panel(title: str, *, right: str = "", key: str | None = None):
    """Riquadro con barra del titolo (maiuscolo, colore d'accento) e testo facoltativo a destra (data, fonte,
    link: HTML già pronto). `with panel("Le mie aziende", right="prezzi al 02/11/26"): ...`"""
    with st.container(border=True, key=key):
        st.html(panel_title_html(title, right))
        yield


def panel_title_html(title: str, right: str = "") -> str:
    r = f'<span class="r">{right}</span>' if right else ""
    return f'<div class="qe-panel-title"><span class="l">{html.escape(title)}</span>{r}</div>'


# ================================================================== tabella fitta (HTML)
TEXT_KINDS = {"text", "ticker", "html", "date", "date_short"}
LEGACY = {"signed": ("pct", {"digits": 2, "arrow": True}), "chg": ("pct", {"arrow": True})}


def ticker_link(ticker: str, fn: str | None = None, label: str | None = None) -> str:
    """Link alla scheda dell'azienda (la cornice legge `?t=` e apre la scheda; `fn` = funzione, es. "DCF")."""
    t = str(ticker)
    q = f"t={quote(t)}" + (f"&fn={quote(fn)}" if fn else "")
    return (f'<a class="qe-tk" href="./azienda?{q}" target="_self" title="Apri la scheda di {html.escape(t)}">'
            f"{html.escape(label or t)}</a>")


def _cell(v, row, kind, opts) -> tuple[str, str]:
    """(classe, html) di una cella."""
    if callable(kind):
        return "", ("" if _missing(v) and not opts.get("call_missing") else str(kind(v, row)))
    if kind == "ticker":
        return "t", (na_html() if _missing(v) else ticker_link(v, opts.get("fn")))
    if kind == "html":
        return "t", ("" if _missing(v) else str(v))
    if kind == "text":
        return "t", html.escape("" if _missing(v) else str(v))
    if kind in ("date", "date_short"):
        return "t", fmt(v, kind) if not _missing(v) else na_html()
    if kind in LEGACY:
        k, o = LEGACY[kind]
        return ("n", fmt_html(v, k, **o)) if _num(v) is not None else ("na", NA)
    if isinstance(kind, str) and "{" in kind:  # formato Python (vecchie chiamate): convertito in italiano
        x = _num(v)
        if x is None:
            return "na", NA
        return "n", html.escape(_no_signed_zero(it_number(kind.format(x))))
    if _num(v) is None:
        return "na", NA
    return "n", fmt_html(v, kind, **opts)


def dense_table_html(df: pd.DataFrame, columns: dict, *, max_rows: int = 40, highlight: Iterable[str] = (),
                     key_col: str = "ticker", titles: dict[str, str] | None = None) -> str:
    """Tabella fitta in sola lettura (§3.5) come HTML.

    `columns`: colonna -> (intestazione, tipo) oppure (intestazione, tipo, opzioni di `fmt`). Tipi: quelli di
    `fmt` (pct, mult, price, money...), "text", "ticker" (link alla scheda), "html" (già pronto, fidato),
    "signed" (variazione con freccia, 2 decimali), "chg" (variazione con freccia, 1 decimale), una stringa di
    formato Python ("{:,.2f}", convertita in italiano) o una funzione (valore, riga) -> html.
    Le intestazioni dei numeri sono allineate a destra; la colonna di testo più lunga si accorcia con "…".
    `highlight`: valori di `key_col` da evidenziare (es. l'azienda aperta). `titles`: colonna -> colonna il cui
    valore va nel tooltip della cella.
    """
    specs = {c: _spec(s) for c, s in columns.items()}
    numeric = {c for c, (_, k, _) in specs.items() if not callable(k) and k not in TEXT_KINDS}
    texts = [c for c, (_, k, _) in specs.items() if k == "text" and c in df]
    fill = max(texts, key=lambda c: df[c].astype(str).str.len().max() if len(df) else 0) if texts else None
    head = "".join(f'<th class="{"n" if c in numeric else ""}" title="{html.escape(h or c)}">{html.escape(h or c)}</th>'
                   for c, (h, _, _) in specs.items())
    marked = {str(x) for x in highlight}
    rows = []
    for _, r in df.head(max_rows).iterrows():
        cells = []
        for col, (_, kind, opts) in specs.items():
            v = r.get(col)
            cls, body = _cell(v, r, kind, opts)
            if col == fill:
                cls = (cls + " fill").strip()
            tip = ""
            if titles and col in titles and not _missing(r.get(titles[col])):
                tip = f' title="{html.escape(str(r.get(titles[col])))}"'
            elif col == fill and not _missing(v):
                tip = f' title="{html.escape(str(v))}"'
            cells.append(f'<td class="{cls}"{tip}>{body}</td>')
        me = key_col in r and str(r.get(key_col)) in marked
        rows.append(f'<tr{" class=me" if me else ""}>' + "".join(cells) + "</tr>")
    more = (f'<div class="qe-more">altre {len(df) - max_rows} righe non mostrate</div>'
            if len(df) > max_rows else "")
    return (f'<div class="qe-tablewrap"><table class="qe-dense"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{more}')


def dense_table(df: pd.DataFrame, columns: dict, max_rows: int = 40, *, highlight: Iterable[str] = (),
                key_col: str = "ticker", titles: dict[str, str] | None = None, empty: str | None = None) -> None:
    """Disegna `dense_table_html` (stessi argomenti). `empty`: frase al posto della tabella se non ci sono righe."""
    if df is None or df.empty:
        if empty:
            st.html(f'<div class="qe-empty-line">{html.escape(empty)}</div>')
            return
        df = pd.DataFrame(columns=list(columns))
    st.html(dense_table_html(df, columns, max_rows=max_rows, highlight=highlight, key_col=key_col, titles=titles))


# ================================================================== distintivi, stati vuoti, avvisi
TONES = ("pos", "neg", "warn", "neutral", "info", "trap", "off", "accent")


def badge(text: str, tone: str = "neutral", symbol: str = "", title: str | None = None) -> str:
    """Distintivo di stato: tone pos | neg | warn | neutral | info | trap | off | accent. Sempre testo (+ simbolo),
    mai solo colore. Es. `badge("MRG margine in calo", "warn", "!")`."""
    tip = f' title="{html.escape(title)}"' if title else ""
    return (f'<span class="qe-badge {tone}"{tip}>{html.escape(symbol) + " " if symbol else ""}'
            f"{html.escape(text)}</span>")


def empty_state(what: str, why: str = "", action_label: str | None = None, key: str | None = None, *,
                details: str | None = None) -> bool:
    """Stato vuoto (al posto di st.info/st.warning): cosa manca, perché, al massimo UN pulsante che lo risolve.
    `details`: dettagli tecnici (comandi, file) in un riquadro richiudibile. Restituisce True se il pulsante è
    stato premuto."""
    st.html(empty_state_html(what, why, details))
    return bool(action_label) and st.button(action_label, key=key)


def empty_state_html(what: str, why: str = "", details: str | None = None) -> str:
    extra = (f'<details><summary>Dettagli</summary><div>{html.escape(details)}</div></details>' if details else "")
    return (f'<div class="qe-empty"><b>{html.escape(what)}</b>'
            f'{f"<span>{html.escape(why)}</span>" if why else ""}{extra}</div>')


NOTICE_SYMBOL = {"neg": "✗", "warn": "!", "info": "i", "pos": "✓", "neutral": "●"}


def notice_html(text: str, tone: str = "warn", *, title: str = "", symbol: str | None = None) -> str:
    """Avviso in linea (banda con bordo sinistro colorato): `title` in grassetto davanti (es. "Allarme:"). Il testo
    è escapato."""
    sym = NOTICE_SYMBOL.get(tone, "!") if symbol is None else symbol
    t = f"<b>{html.escape(title)}</b> " if title else ""
    return f'<div class="qe-notice {tone}"><i>{html.escape(sym)}</i><span>{t}{html.escape(text)}</span></div>'


def notice(text: str, tone: str = "warn", *, title: str = "", symbol: str | None = None) -> None:
    st.html(notice_html(text, tone, title=title, symbol=symbol))


# ================================================================== testata, righe chiave/valore, briciole
def range_html(lo, hi, last, digits: int = 2) -> str:
    """Intervallo (es. 52 settimane) con la posizione del prezzo: "52,91 ──●── 69,07"."""
    lo_x, hi_x, last_x = _num(lo), _num(hi), _num(last)
    if lo_x is None or hi_x is None:
        return na_html()
    pos = 50.0 if last_x is None or hi_x <= lo_x else max(0.0, min(100.0, (last_x - lo_x) / (hi_x - lo_x) * 100))
    return (f'{fmt(lo_x, "num", digits=digits)} <i class="rng" title="posizione del prezzo: {pos:.0f}%">'
            f'<b style="left:{pos:.0f}%"></b></i> {fmt(hi_x, "num", digits=digits)}')


def kv_html(items: Sequence[tuple[str, str]], *, big: bool = False) -> str:
    """Righe etichetta/valore su una riga che va a capo per coppie intere: [("P/E", fmt_html(12.3, "mult")), ...].
    I valori sono HTML (già formattato con fmt_html); le etichette sono testo. `big`: valori a 18 px."""
    cells = "".join(f'<div><dt>{html.escape(k)}</dt><dd>{v}</dd></div>' for k, v in items)
    return f'<dl class="qe-kv{" big" if big else ""}">{cells}</dl>'


def head_html(ticker: str, name: str = "", *, meta: str = "", price=None, currency: str | None = "USD",
              change=None, source: str = "", kv: Sequence[tuple[str, str]] | None = None, right: str = "") -> str:
    """Testata a due livelli (§3.6): ticker e nome con settore sotto, a destra prezzo, variazione e fonte
    ("chiusura 02/11/26"); sotto, le coppie `kv` (HTML). `right`: HTML extra sotto il prezzo (es. sparkline)."""
    px = (f'<span class="px">{fmt(price, "price", currency=currency)}</span>' if _num(price) is not None
          else f'<span class="px">{na_html()}</span>')
    chg = f'<span class="chg">{signed_pct_html(change, 2)}</span>' if _num(change) is not None else ""
    return (f'<div class="qe-head"><div class="qe-head-id"><span class="tk">{html.escape(ticker)}</span>'
            f'<span class="nm">{html.escape(name or "")}</span>'
            f'{f"<span class=meta>{html.escape(meta)}</span>" if meta else ""}</div>'
            f'<div class="qe-head-px">{px}{chg}{f"<span class=src>{html.escape(source)}</span>" if source else ""}'
            f'{right}</div>{kv_html(kv) if kv else ""}</div>')


def result_row_html(items: Sequence[tuple[str, str]], *, title: str = "", note: str = "") -> str:
    """Riga di risultato (DCF, riepilogo del portafoglio): valori grandi in fila con un titolo e una nota sotto.
    `items` come `kv_html` (valori HTML)."""
    t = f'<div class="qe-result-title">{html.escape(title)}</div>' if title else ""
    n = f'<div class="qe-result-note">{html.escape(note)}</div>' if note else ""
    return f'<div class="qe-result">{t}{kv_html(items, big=True)}{n}</div>'


def breadcrumb_html(parts: Sequence[str]) -> str:
    """"Dove sono": AZIENDE › SCHEDA › AAPL › DCF (l'ultima parte in evidenza)."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    body = " <i>›</i> ".join(f"<span>{html.escape(p)}</span>" for p in parts[:-1])
    last = f"<b>{html.escape(parts[-1])}</b>"
    return f'<nav class="qe-crumbs">{body + " <i>›</i> " if body else ""}{last}</nav>'


# ================================================================== verdetto (modules/verdict.py)
# categoria -> (simbolo, tono, etichetta corta); stesse chiavi di verdict.LABELS
VERDICT_STYLE = {
    "candidata": ("✓", "pos", "Candidata"),
    "aspettare": ("◐", "warn", "Aspettare"),
    "neutra": ("●", "neutral", "Neutra"),
    "qualita_dubbia": ("!", "trap", "Dubbia"),
    "evitare": ("✗", "neg", "Evitare"),
    "dati_insufficienti": ("?", "off", "Dati insuff."),
}
OUTCOME_SYMBOL = {"favorevole": ("✓", "pos"), "neutro": ("●", "neutral"), "contrario": ("✗", "neg"),
                  "mancante": (NA, "off")}


def verdict_badge(category: str, label: str | None = None, *, short: bool = True) -> str:
    """Distintivo del verdetto per le tabelle: "✓ Candidata", "✗ Evitare"... (label = testo lungo se short=False)."""
    sym, tone, short_label = VERDICT_STYLE.get(category, ("●", "neutral", category))
    return badge(short_label if short or not label else label, tone, sym, title=label)


def pillar_bar_html(score, grade: str = "", *, label: str = "", available: int | None = None,
                    total: int | None = None, context: bool = False) -> str:
    """Riga di un pilastro: etichetta · barra a 10 celle · punteggio 0-1 · voto in parole · "7 prove su 9".
    `score=None` -> "dati insufficienti" senza barra. `context`: distintivo "solo contesto" (Chi è dentro)."""
    x = _num(score)
    lab = f'<span class="lab">{html.escape(label)}</span>' if label else ""
    ctx = badge("solo contesto", "neutral") if context else ""
    cov = (f'<span class="cov">{available} {"prova" if available == 1 else "prove"} su {total}</span>'
           if available is not None and total and available < total else "")
    if x is None:
        return f'<div class="qe-pillar none">{lab}<span class="off">dati insufficienti</span>{ctx}{cov}</div>'
    x = max(0.0, min(1.0, x))
    tone = "pos" if x >= 0.75 else "neg" if x < 0.25 else "mid"
    cells = round(x * 10)
    bar = "".join(f'<i class="{"on" if i < cells else ""}"></i>' for i in range(10))
    return (f'<div class="qe-pillar">{lab}<span class="bar {tone}" role="img" aria-label="{x:.2f} su 1">{bar}</span>'
            f'<span class="sc">{fmt(x, "score")}</span><span class="gr">{html.escape(grade)}</span>{ctx}{cov}</div>')


def _pillar_html(p) -> str:
    ev = list(getattr(p, "evidence", []) or [])
    miss = list(getattr(p, "missing", []) or [])
    total = len(ev)
    return pillar_bar_html(getattr(p, "score", None), getattr(p, "grade", ""), label=getattr(p, "label", ""),
                           available=total - len(miss), total=total, context=getattr(p, "key", "") == "insiders")


def verdict_card_html(v, *, value_html: str = "", max_changes: int = 4) -> str:
    """Riquadro del Verdetto (§4.2, §7.1) da un `modules.verdict.Verdict`: barra del titolo con tipo di azienda,
    data e cautela; etichetta grande con simbolo; regola; allarmi (✗) e cose da verificare (!) sopra i pilastri;
    a destra "cosa farebbe cambiare il giudizio" e `value_html` (riga VALORE, HTML del chiamante); in fondo i
    problemi dei dati. Le prove ("Perché?") le disegna il chiamante con `evidence_table_html`."""
    cat = getattr(v, "category", "neutra")
    sym, tone, _ = VERDICT_STYLE.get(cat, ("●", "neutral", ""))
    as_of = getattr(v, "as_of", None)
    profile = getattr(v, "profile_label", "") or ""
    right = " · ".join(x for x in [profile, f"al {fmt(as_of, 'date_short')}" if not _missing(as_of) else "",
                                   "non è una raccomandazione"] if x)
    disclaimer = getattr(v, "disclaimer", "")
    title = (f'<div class="qe-panel-title"><span class="l">Verdetto</span>'
             f'<span class="r" title="{html.escape(disclaimer)}">{html.escape(right)}</span></div>')
    alarms = "".join(notice_html(a, "neg", title="Allarme:") for a in getattr(v, "alarms", []) or [])
    checks = "".join(notice_html(c, "warn", title="Da verificare:") for c in getattr(v, "checks", []) or [])
    checks += "".join(notice_html(c, "warn", title="Dati di riserva:") for c in getattr(v, "data_notes", []) or [])
    pillars = "".join(_pillar_html(p) for p in (getattr(v, "pillars", {}) or {}).values())
    changes = list(getattr(v, "changes", []) or [])
    shown = "".join(f"<li>{html.escape(c)}</li>" for c in changes[:max_changes])
    extra = f'<li class="more">altri {len(changes) - max_changes}</li>' if len(changes) > max_changes else ""
    right_col = ((f'<div class="sub">Cosa farebbe cambiare il giudizio</div><ul>{shown}{extra}</ul>' if changes else "")
                 + (f'<div class="sub">Valore</div><div class="val">{value_html}</div>' if value_html else ""))
    issues = getattr(v, "data_issues", []) or []
    foot = f'<div class="foot">Dati: {html.escape("; ".join(issues))}</div>' if issues else ""
    rule = getattr(v, "rule", "") or ""
    return (f'<div class="qe-verdict {tone}">{title}<div class="body"><div class="left">'
            f'<div class="label {tone}"><span class="sym">{html.escape(sym)}</span>{html.escape(getattr(v, "label", ""))}</div>'
            f'<div class="rule" title="{html.escape(rule)}">{html.escape(rule)}</div>{alarms}{checks}'
            f'<div class="pillars">{pillars}</div></div><div class="right">{right_col}</div></div>{foot}</div>')


def evidence_table_html(pillar) -> str:
    """Prove di un pilastro come tabella fitta: Prova · Valore · Riferimento · Esito (✓ ● ✗ –) · Frase. Le mancanti in
    fondo, in grigio."""
    ev = list(getattr(pillar, "evidence", []) or [])
    ev.sort(key=lambda e: getattr(e, "outcome", "") == "mancante")
    rows = []
    for e in ev:
        out = getattr(e, "outcome", "mancante")
        sym, tone = OUTCOME_SYMBOL.get(out, ("●", "neutral"))
        cls = ' class="miss"' if out == "mancante" else ""
        tip = html.escape(e.text or "")  # la frase completa al passaggio del mouse (prima era una colonna troncata)
        rows.append(f'<tr{cls} title="{tip}"><td class=t>{html.escape(e.label)}</td>'
                    f"<td class=n>{html.escape(e.shown or NA)}</td>"
                    f'<td class="t fill" title="{html.escape(e.reference or "")}">{html.escape(e.reference or "")}</td>'
                    f'<td class="t"><span class="{tone}">{sym} {html.escape(out)}</span></td></tr>')
    return ('<div class="qe-tablewrap"><table class="qe-dense"><thead><tr><th>Prova</th><th class=n>Valore</th>'
            '<th>Riferimento</th><th>Esito</th></tr></thead><tbody>' + "".join(rows)
            + "</tbody></table></div>")


# ================================================================== cosa è cambiato (modules/changes.py)
def change_rows_html(summary: Sequence[dict], *, limit: int = 3, price_line: str = "", thesis_line: str = "") -> str:
    """Corpo del pannello "Cosa è cambiato": due colonne Favorevoli ▲ / Contrarie ▼ (frasi di `changes.summarize`,
    al massimo `limit` per parte, già ordinate per importanza; le neutre non si mostrano), poi le righe facoltative
    del prezzo dal deposito e della tesi (testo)."""
    good = [s for s in summary if s.get("favorable") is True][:limit]
    bad = [s for s in summary if s.get("favorable") is False][:limit]

    def col(items, title, cls, sym):
        lis = "".join(f'<li><span class="{cls}">{sym}</span> {html.escape(s.get("text", ""))}</li>' for s in items)
        return (f'<div><div class="sub">{title}</div>'
                f'{f"<ul>{lis}</ul>" if lis else "<div class=none>nessuna</div>"}</div>')

    lines = "".join(f'<div class="line">{html.escape(t)}</div>' for t in (price_line, thesis_line) if t)
    return (f'<div class="qe-changes"><div class="cols">{col(good, "Favorevoli ▲", "pos", "▲")}'
            f'{col(bad, "Contrarie ▼", "neg", "▼")}</div>{lines}</div>')


# ================================================================== avvisi (modules/alerts.py)
from modules.alerts import ALERT_TONES, alert_tone  # noqa: E402 - una sola definizione


def alert_badge(code: str, data: dict | None = None) -> str:
    """Codice dell'avviso come distintivo col tono giusto (MOV/MOV1 secondo il verso del movimento)."""
    return badge(code, alert_tone(code, data))


def _ago(created, now=None) -> str:
    if _missing(created):
        return ""
    try:
        c = pd.Timestamp(created)
    except (TypeError, ValueError):
        return ""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now())
    days = (now.normalize() - c.normalize()).days
    return "oggi" if days <= 0 else "ieri" if days == 1 else f"da {days} g"


def alert_row_html(alert, *, now=None, link: bool = True) -> str:
    """Una riga di avviso: pallino se non visto · codice (distintivo) · ticker (link alla scheda) · messaggio · età.
    `alert`: dict o riga di `db.load_alerts()` (ticker, code, message, data, created_at, seen_at)."""
    get = alert.get if hasattr(alert, "get") else (lambda k, d=None: getattr(alert, k, d))
    unseen = _missing(get("seen_at"))
    t = str(get("ticker") or "")
    tk = ticker_link(t) if link and t else html.escape(t)
    data = get("data") if isinstance(get("data"), dict) else None
    dot = '<span class="dot" title="non visto">●</span>' if unseen else '<span class="dot"></span>'
    return (f'<div class="qe-alert{" new" if unseen else ""}">{dot}{alert_badge(str(get("code") or ""), data)}'
            f'<span class="tk">{tk}</span><span class="msg" title="{html.escape(str(get("message") or ""))}">'
            f'{html.escape(str(get("message") or ""))}</span>'
            f'<span class="age">{_ago(get("created_at"), now)}</span></div>')


__all__ = [
    "NA", "MINUS", "CURRENCY_SYMBOL", "fmt", "fmt_html", "fmt_metric", "fmt_table", "num_column", "table_height",
    "it_number", "tone_of", "na_html", "signed_pct_html", "panel", "panel_title_html", "dense_table",
    "dense_table_html", "ticker_link", "badge", "empty_state", "empty_state_html", "notice", "notice_html",
    "range_html", "kv_html", "head_html", "result_row_html", "breadcrumb_html", "VERDICT_STYLE", "verdict_badge",
    "pillar_bar_html", "verdict_card_html", "evidence_table_html", "change_rows_html", "ALERT_TONES",
    "alert_badge", "alert_row_html",
]


def plural(n: int, one: str, many: str) -> str:
    """"1 azienda", "3 aziende" (numero in italiano)."""
    return f"{fmt(n, 'int')} {one if n == 1 else many}"
