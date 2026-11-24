"""Pagina "Oggi" (F1, ex Monitor; docs/revisione_grafica.md §4.1): "è successo qualcosa alle MIE aziende?".

Dall'alto in basso:
  1. Le mie aziende (seguite + in portafoglio + con una soglia di prezzo) con prezzo, variazione, 1 anno, margine sul
     tuo fair value, soglia "compra sotto" e il Verdetto (`verdict.assess_many`, in cache per versione dei dati);
     accanto gli Avvisi attivi (i non visti in cima) con "Segna tutti come visti".
  2. Ultime notizie (feed RSS, le mie aziende prima) e documenti ufficiali arrivati (10-Q/10-K, 8-K).
  3. Mercati (una riga), S&P 500 in salita / in discesa, acquisti degli insider.
  4. Richiusi: mappa dell'S&P 500 e borse mondiali (lo stato ●/○ resta nella barra di stato).

I due frammenti che si ridisegnano ogni 60 s a mercato aperto (le mie aziende; mercati e movimenti) leggono solo la
tabella già pronta del QuoteFeed e la striscia in cache: niente giudizi, metriche o grafici.
"""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard_data import DashboardData
from ui.common import DOWN, FONTS, TERMINAL, TOKENS, UP, data
from ui.components import (
    NA, alert_row_html, badge, dense_table, empty_state, fmt, fmt_html, panel, ticker_link, tone_of, verdict_badge,
)
from ui.pages.notizie import _news_table  # noqa: F401 - nome storico, lo usano altri moduli tramite questa pagina

# stile della striscia dei mercati e del pannello avvisi: solo token (ui/common.py), niente colori in linea.
# Attenzione: nessun carattere "minore di" qui dentro (st.html lo prenderebbe per un tag).
PAGE_CSS = """<style>
.qe-strip { display: flex; flex-wrap: nowrap; overflow-x: auto; border: 1px solid var(--border-ctl);
  background: var(--surface-1); scrollbar-width: thin; }
.qe-strip .tick { display: flex; flex-direction: column; flex: 0 0 auto; gap: 1px; padding: 4px 12px;
  border-right: 1px solid var(--border); min-width: 120px; font-variant-numeric: tabular-nums; }
.qe-strip .tick:last-child { border-right: 0; }
.qe-strip .nm { font: 500 var(--fs-micro)/1.3 var(--mono); color: var(--text-2); text-transform: uppercase;
  letter-spacing: .04em; white-space: nowrap; }
.qe-strip .px { font: var(--fs-data)/1.3 var(--mono); color: var(--text-num); white-space: nowrap; }
.qe-strip .ch { font: var(--fs-micro)/1.3 var(--mono); white-space: nowrap; }
.qe-alerts { max-height: 420px; overflow-y: auto; }
.qe-alerts .qe-alert .tk a { color: var(--info); }
.qe-mine-note { color: var(--text-3); font-size: var(--fs-micro); margin-top: 2px; }
.qe-links { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 8px; font-size: var(--fs-data); }
.qe-links a { color: var(--info); text-decoration: none; }
.qe-links a:hover, .qe-alerts .qe-alert .tk a:hover { text-decoration: underline; }
.qe-ex-note { color: var(--text-3); font-size: var(--fs-micro); margin-top: 4px; }
</style>"""


# ---------------------------------------------------------------- quotazioni
@st.cache_data(ttl=60, show_spinner=False)
def _market_quotes(symbols: tuple[str, ...]) -> pd.DataFrame:
    from modules import quotes

    try:
        return quotes.quote_table(quotes.download_closes(list(symbols)))
    except Exception:  # noqa: BLE001 - senza rete la striscia non c'è, il resto della pagina sì
        return pd.DataFrame()


@st.cache_resource
def _quote_feed(symbols: tuple[str, ...], every_open: int, every_closed: int):
    from modules.quotes import QuoteFeed

    return QuoteFeed(list(symbols), every_open, every_closed).start()


def _stock_quotes(d: DashboardData) -> tuple[pd.DataFrame, str]:
    """Quotazioni di oggi dei titoli dell'indice (in un thread, ~15 minuti di ritardo); finché non arrivano, le
    ultime due chiusure del database. Restituisce anche la fonte, breve, per la barra del titolo."""
    from modules.quotes import quote_table

    if getattr(d, "is_demo", False):  # dati simulati: le quotazioni vere di Yahoo non c'entrano con questi prezzi
        close = d.data.recent_close([t for t in d.index_tickers if t in d.data.close], 6)
        return quote_table(close), f"chiusure simulate del {fmt(d.data.last_date, 'date_short')}"
    mon = d.cfg["monitor"]
    feed = _quote_feed(tuple(d.index_tickers), int(mon["refresh_open_seconds"]), int(mon["refresh_closed_seconds"]))
    table, updated = feed.snapshot()
    if not table.empty:
        return table, f"prezzi delle {updated:%H:%M}, ritardo ~15 min"
    close = d.data.recent_close([t for t in d.index_tickers if t in d.data.close], 6)
    return quote_table(close), f"chiusure del {fmt(d.data.last_date, 'date_short')}"


def _db_quotes(d: DashboardData, tickers: list[str]) -> pd.DataFrame:
    """Ultime due chiusure del database (titoli fuori dall'indice, es. europei o aggiunti a mano)."""
    from modules.quotes import quote_table

    cols = [t for t in tickers if t in d.data.close]
    return quote_table(d.data.recent_close(cols, 6)) if cols else pd.DataFrame()


# ---------------------------------------------------------------- le mie aziende
def _held(d: DashboardData) -> set[str]:
    """Titoli con una posizione aperta nel portafoglio a mano."""
    try:
        tx = d.db.load_transactions()
    except Exception:  # noqa: BLE001 - senza tabella del portafoglio: nessuna posizione
        return set()
    if tx.empty:
        return set()
    q = tx[tx["kind"].isin(["BUY", "SELL"])]
    signed = np.where(q["kind"] == "BUY", 1.0, -1.0) * q["quantity"].fillna(0.0)
    held = pd.Series(signed, index=q.index).groupby(q["ticker"]).sum()
    return {t for t, n in held.items() if n > 1e-9}


def _my_tickers(d: DashboardData) -> list[str]:
    """Aziende seguite + in portafoglio + con una soglia di prezzo (le stesse che valuta il motore degli avvisi)."""
    from modules import alerts

    try:
        return alerts.followed_tickers(d.db)
    except Exception:  # noqa: BLE001 - tabelle del portafoglio o delle soglie mancanti: restano le seguite
        return list(d.watchlist())


def _fair_values(d: DashboardData) -> dict[str, float]:
    notes = d.db.load_notes()
    if notes.empty or "fair_value" not in notes:
        return {}
    return pd.to_numeric(notes.set_index("ticker")["fair_value"], errors="coerce").dropna().to_dict()


def _buy_below(d: DashboardData) -> dict[str, float]:
    try:
        t = d.db.load_price_targets()
    except Exception:  # noqa: BLE001
        return {}
    if t.empty:
        return {}
    return pd.to_numeric(t.set_index("ticker")["buy_below"], errors="coerce").dropna().to_dict()


@st.cache_data(show_spinner=False, max_entries=16)
def _verdicts(key: str, tickers: tuple[str, ...], _d: DashboardData) -> pd.DataFrame:  # noqa: ARG001
    """Giudizi delle mie aziende (verdetto, categoria, regola). `key` = versione dei dati + fair value dell'utente:
    si ricalcolano solo quando cambiano i dati, non a ogni aggiornamento delle quotazioni."""
    from modules import verdict

    if not tickers:
        return pd.DataFrame(columns=["verdict", "category", "rule"])
    try:
        table = verdict.assess_many(_d, list(tickers))
    except Exception:  # noqa: BLE001 - senza giudizi la tabella resta utile (colonna "Dati insuff.")
        return pd.DataFrame(columns=["verdict", "category", "rule"])
    return table[["verdict", "category", "rule"]]


def _verdict_key(d: DashboardData, fair: dict) -> str:
    return "|".join([str(id(d)), str(d.data.last_date), str(d.run_id),
                     ",".join(f"{t}={v:.6g}" for t, v in sorted(fair.items()))])


def _one_year(d: DashboardData, tickers: list[str]) -> dict[str, float]:
    """Rendimento a un anno (ultima chiusura contro ~252 sedute prima) dal database."""
    out = {}
    for t in tickers:
        if t not in d.data.close:
            continue
        s = d.data.close[t].dropna()
        if len(s) > 200:
            past = s[s.index <= s.index[-1] - pd.Timedelta(days=365)]
            if len(past) and past.iloc[-1] > 0:
                out[t] = float(s.iloc[-1] / past.iloc[-1] - 1)
    return out


def my_companies_table(d: DashboardData, q: pd.DataFrame, tickers: list[str], verdicts: pd.DataFrame,
                       fair: dict, buy: dict, held: set, unseen: dict, one_year: dict) -> pd.DataFrame:
    """Una riga per azienda: prezzo, variazione, 1 anno, margine sul fair value, distanza dalla soglia d'acquisto,
    verdetto. Ordine: prima chi ha avvisi non visti, poi per verdetto (verdict.ORDER), poi ticker."""
    from modules.verdict import ORDER

    missing = [t for t in tickers if t not in q.index]
    extra = _db_quotes(d, missing) if missing else pd.DataFrame()
    # titoli solo nel portafoglio, senza prezzi nel database (es. ENI.MI, un ETF appena comprato): quotazione di
    # Yahoo come nella pagina Portafoglio (collaudo 2026-11-16: righe tutte vuote)
    no_px = tuple(sorted(t for t in missing if extra.empty or t not in extra.index))
    live = _live_last(no_px) if no_px else {}
    known = set(d.tickers)
    rows = []
    for t in tickers:
        src = q if t in q.index else extra if (not extra.empty and t in extra.index) else None
        last = float(src.at[t, "last"]) if src is not None else float(live.get(t, np.nan))
        chg = float(src.at[t, "change_pct"]) if src is not None else np.nan
        fv, bb = fair.get(t), buy.get(t)
        # un ETF o un titolo senza bilanci nell'app non ha un giudizio: "–", non "dati insufficienti"
        cat = (NOT_JUDGED if t not in known
               else verdicts.at[t, "category"] if t in verdicts.index else "dati_insufficienti")
        rows.append({
            "ticker": t, "name": d.info(t).get("name") or "", "last": last, "change_pct": chg,
            "ret_1y": one_year.get(t, np.nan),
            "margin": fv / last - 1 if fv and np.isfinite(last) and last > 0 else np.nan,
            "buy_below": bb if bb else np.nan,
            "buy_gap": last / bb - 1 if bb and np.isfinite(last) else np.nan,
            "category": cat,
            "verdict": verdicts.at[t, "verdict"] if t in verdicts.index else "",
            "rule": verdicts.at[t, "rule"] if t in verdicts.index else "",
            "held": t in held, "unseen": int(unseen.get(t, 0)),
            "_order": ORDER.index(cat) if cat in ORDER else len(ORDER),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["unseen", "_order", "ticker"], ascending=[False, True, True]).reset_index(drop=True)


def _ticker_cell(v, row) -> str:
    extra = []
    if row.get("unseen"):
        extra.append(f'<span class="qe-badge accent" title="avvisi non visti">● {int(row["unseen"])}</span>')
    if row.get("held"):
        extra.append(badge("ptf", "neutral", title="in portafoglio"))
    return ticker_link(v) + ("&nbsp;" + "&nbsp;".join(extra) if extra else "")


def _buy_cell(v, row) -> str:
    """Soglia "compra sotto" e distanza del prezzo: "55,00 · +4,3%" (positivo = ancora sopra la soglia)."""
    if not np.isfinite(row.get("buy_below", np.nan)):
        return f'<span class="qe-na">{NA}</span>'
    gap = row.get("buy_gap", np.nan)
    tip = "prezzo sotto la soglia" if np.isfinite(gap) and gap <= 0 else "prezzo sopra la soglia"
    return (f'<span title="{tip}">{fmt(row["buy_below"], "price")} · '
            f'{fmt_html(gap, "pct", signed=True, color=False)}</span>')


NOT_JUDGED = "non_valutato"  # titoli fuori dall'anagrafica (ETF, aziende solo nel portafoglio)


def _live_last(tickers: tuple[str, ...]) -> dict[str, float]:
    from ui.pages.portafoglio import _live_prices

    return _live_prices(tickers)


def _verdict_cell(v, row) -> str:
    if row["category"] == NOT_JUDGED:
        return f'<span class="qe-na" title="ETF o titolo senza bilanci nell\'app: nessun giudizio">{NA}</span>'
    return verdict_badge(row["category"], row.get("verdict") or None)


MY_COLUMNS = {  # il verdetto subito dopo il nome: è la colonna che decide, non deve finire fuori schermo
    "ticker": ("Ticker", _ticker_cell),
    "name": ("Azienda", "text"),
    "category": ("Verdetto", _verdict_cell),
    "last": ("Prezzo", "price"),
    "change_pct": ("Oggi", "chg"),
    "ret_1y": ("1 anno", "chg"),
    "margin": ("Margine FV", "pct", {"signed": True}),
    "buy_below": ("Compra sotto", _buy_cell, {"call_missing": True}),
}
OPTIONAL_COLUMNS = ("margin", "buy_below")  # nascoste se nessuna azienda ha un fair value / una soglia


def visible_columns(table: pd.DataFrame) -> dict:
    """MY_COLUMNS senza le colonne facoltative tutte vuote."""
    return {k: v for k, v in MY_COLUMNS.items()
            if k not in OPTIONAL_COLUMNS or (k in table and pd.to_numeric(table[k], errors="coerce").notna().any())}


# avvisi: prima quelli che toccano la tesi (soglie, fair value, bilanci, giudizio, insider, 8-K), poi i movimenti di
# prezzo, che restano in un riquadro richiuso (sono tanti e dicono poco da soli)
from modules.alerts import ALERT_PRIORITY, PRICE_MOVES  # noqa: E402 - una sola definizione


def _empty_mine():
    from ui.common import data
    from ui.rules import get_started

    get_started(data(), key="mon_start")


def _alerts_panel(d: DashboardData) -> dict[str, int]:
    """Avvisi attivi: i non visti in cima (pallino), poi i visti. Restituisce i non visti per ticker."""
    try:
        al = d.db.load_alerts(active=True, limit=200)
    except Exception:  # noqa: BLE001 - database senza la tabella degli avvisi
        al = pd.DataFrame()
    unseen = al[al["seen_at"].isna()] if not al.empty else al
    n_new = len(unseen)
    right = (f"{n_new} non visti · " if n_new else "") + '<a href="./mie-aziende" target="_self">storico ›</a>'
    with panel("Avvisi", right=right):
        if al.empty:
            st.html('<div class="qe-empty-line">Nessun avviso attivo. Gli avvisi (prezzo sotto la soglia o il fair '
                    'value, bilanci nuovi, acquisti degli insider, movimenti forti...) arrivano con ogni '
                    'aggiornamento dei dati.</div>')
            return {}
        code = al["code"].astype(str)
        prio = code.map({c: i for i, c in enumerate(ALERT_PRIORITY)}).fillna(len(ALERT_PRIORITY))
        al = al.assign(_new=al["seen_at"].isna(), _prio=prio).sort_values(["_new", "_prio", "id"],
                                                                         ascending=[False, True, False])
        moves = code.reindex(al.index).isin(PRICE_MOVES)
        main, mv = al[~moves], al[moves]
        if main.empty:
            st.html('<div class="qe-empty-line">Nessun avviso su soglie, fair value, bilanci, giudizi o insider.</div>')
        else:
            rows = "".join(alert_row_html(r) for r in main.head(30).to_dict("records"))
            more = f'<div class="qe-more">altri {len(main) - 30} nello storico</div>' if len(main) > 30 else ""
            st.html(f'<div class="qe-alerts">{rows}</div>{more}')
        if not mv.empty:
            new_mv = int(mv["_new"].sum())
            with st.expander(f"Movimenti di prezzo ({len(mv)}" + (f", {new_mv} non visti)" if new_mv else ")")):
                st.html('<div class="qe-alerts">' + "".join(alert_row_html(r) for r in mv.head(30).to_dict("records"))
                        + "</div>")
        if n_new and st.button("Segna tutti come visti", key="mon_alerts_seen"):
            d.db.mark_alerts_seen()
            st.rerun()
    return unseen["ticker"].value_counts().to_dict() if n_new else {}


# ---------------------------------------------------------------- mercati
def _rate_change_html(v) -> str:
    """Variazione di un rendimento in punti base (la quotazione è in punti percentuali)."""
    x = None if v is None or not np.isfinite(v) else round(v * 100)
    if x is None:
        return f'<span class="qe-na">{NA}</span>'
    arrow = "▲" if x > 0 else "▼" if x < 0 else "■"
    return f'<span class="{tone_of(x)}">{arrow} {fmt(x, "int", signed=True)} pb</span>'


def market_strip_html(symbols: dict, q: pd.DataFrame, rates: set) -> str:
    cells = []
    for sym, name in symbols.items():
        if q.empty or sym not in q.index:
            continue
        r = q.loc[sym]
        if sym in rates:
            last, change = fmt(r["last"], "num") + "%", _rate_change_html(r["change"])
        else:
            last, change = fmt(r["last"], "num"), fmt_html(r["change_pct"], "pct", digits=2, arrow=True)
        cells.append(f'<div class="tick"><span class="nm">{html.escape(str(name))}</span>'
                     f'<span class="px">{last}</span><span class="ch">{change}</span></div>')
    return f'<div class="qe-strip">{"".join(cells)}</div>' if cells else ""


def _market_strip(d: DashboardData):
    mon = d.cfg["monitor"]
    symbols = dict(mon.get("symbols") or {})
    if not symbols:
        return
    strip = market_strip_html(symbols, _market_quotes(tuple(symbols)), set(mon.get("rates") or []))
    if strip:
        st.html(strip)
    else:
        st.html('<div class="qe-empty-line">Quotazioni dei mercati non disponibili (connessione?).</div>')


def _movers(d: DashboardData, q: pd.DataFrame):
    idx = q[q.index.isin(d.index_tickers)].dropna(subset=["change_pct"]).sort_values("change_pct")
    idx = idx.assign(ticker=idx.index, name=[d.info(t).get("name") or "" for t in idx.index])
    cols = {"ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"), "last": ("Prezzo", "price"),
            "change_pct": ("Oggi", "chg")}
    up, down = st.columns(2)
    with up, panel("S&P 500 · in salita"):
        dense_table(idx[idx["change_pct"] > 0].iloc[::-1].head(8), cols, empty="Nessun titolo in salita.")
    with down, panel("S&P 500 · in discesa"):
        dense_table(idx[idx["change_pct"] < 0].head(8), cols, empty="Nessun titolo in discesa.")


# ---------------------------------------------------------------- documenti, insider
def _documents_panel(d: DashboardData, mine: list[str], days: int):
    """10-Q/10-K arrivati (aziende seguite) e 8-K (le mie aziende prima; ⚠ = voci gravi)."""
    with panel("Documenti ufficiali", right=f"10-Q/10-K e 8-K · {days} giorni"):
        only_mine = st.toggle("Solo le mie aziende", key="mon_news_mine", disabled=not mine)
        frames = []
        f = d.recent_filings(max(days, 7))
        if not f.empty:
            frames.append(pd.DataFrame({"when": pd.to_datetime(f["filed"]), "ticker": f["ticker"], "form": f["form"],
                                        "what": f.get("status", pd.Series("", index=f.index)).fillna(""),
                                        "important": False, "url": None}))
        ev = d.news(days, mine if (only_mine and mine) else None)
        if not ev.empty:
            frames.append(pd.DataFrame({"when": pd.to_datetime(ev["when"]), "ticker": ev["ticker"], "form": ev["form"],
                                        "what": ev["what"], "important": ev["important"], "url": ev["url"]}))
        frames = [x for x in frames if not x.empty]
        if not frames:
            st.html(f'<div class="qe-empty-line">Nessun documento negli ultimi {days} giorni (si scaricano con '
                    '"Aggiorna dati" e con l\'aggiornamento automatico).</div>')
            return
        docs = pd.concat(frames, ignore_index=True)
        if only_mine and mine:
            docs = docs[docs["ticker"].isin(mine)]
        docs = docs.assign(_mine=docs["ticker"].isin(mine)).sort_values(["_mine", "when"], ascending=[False, False])
        docs["name"] = [d.info(t).get("name") or "" for t in docs["ticker"]]

        def what(v, row):
            text = ("⚠ " if row["important"] else "") + str(v or "")
            url = row.get("url")
            body = html.escape(text)
            if isinstance(url, str) and url:
                return (f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                        f'class="{"neg" if row["important"] else ""}">{body}</a>')
            return body

        dense_table(docs, {"when": ("Data", "date_short"), "ticker": ("Ticker", "ticker"),
                           "form": ("Doc", "text"), "name": ("Azienda", "text"), "what": ("Cosa", what)},
                    max_rows=12, empty="Nessun documento delle tue aziende in questi giorni.")


def _insider_panel(d: DashboardData, mine: list[str]):
    with panel("Acquisti degli insider", right="7 giorni"):
        tx = d.data.insiders
        if tx is None or tx.empty:
            st.html('<div class="qe-empty-line">Insider non scaricati (Aggiorna dati con l\'opzione insider).</div>')
            return
        buys = tx[(tx["code"] == "P") & (tx["filed"] >= pd.Timestamp.now() - pd.Timedelta(days=7))]
        if buys.empty:
            st.html('<div class="qe-empty-line">Nessun acquisto sul mercato depositato negli ultimi 7 giorni.</div>')
            return
        top = (buys.groupby("ticker").agg(value=("value", "sum"), buyers=("owner", "nunique"))
               .sort_values("value", ascending=False).head(10).reset_index())
        top["name"] = [d.info(t).get("name") or "" for t in top["ticker"]]
        dense_table(top, {"ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"),
                          "value": ("Valore", "money", {"currency": "USD"}), "buyers": ("Insider", "int")},
                    highlight=mine)


# ---------------------------------------------------------------- borse mondiali e mappa (richiuse)
def _exchange_codes(d: DashboardData) -> dict:
    from modules import exchanges

    conf = d.cfg["monitor"].get("exchanges") or {}
    return conf or {c: [None, ""] for c in exchanges.DEFAULT}


def exchanges_table_html(board: list[dict], conf: dict, q: pd.DataFrame) -> str:
    """Borse: ora locale, stato (● aperta / ◐ pausa / ○ chiusa, con il nome della festività), tra quanto cambia,
    indice e variazione. Gli orari vanno nel tooltip della riga; * = calendario delle festività parziale."""
    from modules import exchanges

    sym_label = {"APERTA": ("●", "pos", "aperta"), "PAUSA": ("◐", "warn", "pausa"), "CHIUSA": ("○", "", "chiusa")}
    rows = []
    for b in board:
        sym, index = (conf.get(b["code"]) or [None, ""])[:2]
        mark, tone, word = sym_label.get(b["state"], ("○", "", str(b["state"]).lower()))
        holiday = b.get("holiday")
        state = f"{mark} {word}" + (f" per {holiday}" if holiday and not b["open"] else "")
        verb = "chiude" if b["open"] else "apre"
        has_q = bool(sym) and not q.empty and sym in q.index
        last = fmt(q.at[sym, "last"], "num") if has_q else NA
        change = fmt_html(q.at[sym, "change_pct"], "pct", digits=2, arrow=True) if has_q else f'<span class="qe-na">{NA}</span>'
        star = "" if b.get("holidays_complete", True) else "*"
        tip = html.escape(f"orari {b['hours']} (ora locale)")
        rows.append(f'<tr title="{tip}"><td class="t">{html.escape(b["name"])}{star}</td>'
                    f'<td class="n">{b["local"]:%H:%M}</td><td class="t {tone}">{html.escape(state)}</td>'
                    f'<td class="t">{verb} tra {exchanges.until_text(b["until"])}</td>'
                    f'<td class="t fill">{html.escape(index or "")}</td><td class="n">{last}</td>'
                    f'<td class="n">{change}</td></tr>')
    head = "".join(f'<th class="{c}">{h}</th>' for h, c in [("Borsa", ""), ("Ora", "n"), ("Stato", ""), ("", ""),
                                                             ("Indice", ""), ("Ultimo", "n"), ("Oggi", "n")])
    return (f'<div class="qe-tablewrap"><table class="qe-dense"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _exchanges_panel(d: DashboardData):
    from modules import exchanges

    conf = _exchange_codes(d)
    board = exchanges.board(list(conf))
    syms = tuple(v[0] for v in conf.values() if v and v[0])
    q = _market_quotes(syms) if syms else pd.DataFrame()
    st.html(exchanges_table_html(board, conf, q))
    partial = [b["name"].title() for b in board if not b.get("holidays_complete", True)]
    note = "Orari della contrattazione continua, ora locale di ogni borsa (passa sulla riga per vederli). "
    if partial:
        note += (f"* Calendario delle festività parziale ({', '.join(partial)}): ci sono solo le feste a data certa; "
                 "quelle lunari o decise anno per anno mancano, quindi in quei giorni la borsa può risultare aperta.")
    else:
        note += "Festività incluse; non le chiusure straordinarie o anticipate."
    st.html(f'<div class="qe-ex-note">{html.escape(note)}</div>')


def _heatmap(d: DashboardData, q: pd.DataFrame):
    m = d.metrics()
    sectors = d.sectors()
    rows = [(t, sectors.get(t), m.at[t, "market_cap"], q.at[t, "change_pct"]) for t in q.index
            if t in m.index and isinstance(sectors.get(t), str) and pd.notna(m.at[t, "market_cap"])
            and pd.notna(q.at[t, "change_pct"])]
    if not rows:
        st.html('<div class="qe-empty-line">Mappa non disponibile.</div>')
        return
    df = pd.DataFrame(rows, columns=["ticker", "sector", "mcap", "chg"])
    sec = df.groupby("sector").apply(lambda g: pd.Series({"mcap": g["mcap"].sum(),
                                                           "chg": np.average(g["chg"], weights=g["mcap"])}),
                                     include_groups=False)
    labels = list(sec.index) + list(df["ticker"])
    parents = [""] * len(sec) + list(df["sector"])
    values = list(sec["mcap"]) + list(df["mcap"])
    chg = list(sec["chg"]) + list(df["chg"])
    text = [fmt(c, "pct", signed=True) for c in chg]
    mid = "#2a303b" if TERMINAL else "#e4e7ec"
    fig = go.Figure(go.Treemap(
        labels=labels, parents=parents, values=values, branchvalues="total", customdata=text,
        texttemplate="%{label}<br>%{customdata}", hovertemplate="%{label}: %{customdata}<extra></extra>",
        marker=dict(colors=chg, colorscale=[[0, DOWN], [0.5, mid], [1, UP]], cmid=0, cmin=-0.03, cmax=0.03,
                    line=dict(width=2, color=TOKENS["surface-1"])),
        textfont=dict(family=FONTS["sans"], size=13), tiling=dict(pad=1)))
    # niente testo sotto i 12 px: nei riquadri troppo piccoli si nasconde (resta nel tooltip)
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=0, b=0), uniformtext=dict(minsize=12, mode="hide"),
                      hoverlabel=dict(font_size=13))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.html('<div class="qe-ex-note">Riquadri grandi quanto la capitalizzazione, colore = variazione di oggi (pieno '
            'a ±3%); nei settori la media pesata.</div>')


# ---------------------------------------------------------------- pagina
def page_monitor():
    d = data()
    from modules.quotes import market_open

    live = market_open()
    every = 60 if live else None
    st.html(PAGE_CSS)

    mine = _my_tickers(d)
    held = _held(d)
    fair = _fair_values(d)
    buy = _buy_below(d)
    verdicts = pd.DataFrame(columns=["verdict", "category", "rule"])
    if mine:
        with st.spinner("Calcolo i giudizi delle tue aziende…"):
            verdicts = _verdicts(_verdict_key(d, fair), tuple(mine), d)
    one_year = _one_year(d, mine)

    left, right = st.columns([2.1, 1])
    with right:
        unseen = _alerts_panel(d)

    with left:
        @st.fragment(run_every=every)
        def my_companies():
            q, source = _stock_quotes(d)
            with panel("Le mie aziende", right=html.escape(source)):
                if not mine:
                    _empty_mine()
                    return
                table = my_companies_table(d, q, mine, verdicts, fair, buy, held, unseen, one_year)
                dense_table(table, visible_columns(table), max_rows=40, titles={"category": "rule"})
                st.html('<div class="qe-mine-note">Seguite, in portafoglio (ptf) e con una soglia di prezzo. Margine '
                        'FV = il tuo fair value contro il prezzo; Compra sotto = la tua soglia e quanto manca.</div>')

        my_companies()

    days = int(d.cfg["monitor"].get("news_days", 3))
    news_col, docs_col = st.columns([1.4, 1])
    with news_col:
        from ui.pages.notizie import latest_news, render_latest_news

        try:
            has_mine = bool(mine) and not latest_news(d, mine, 1).empty
        except Exception:  # noqa: BLE001 - feed non disponibili: si mostrano tutte
            has_mine = False
        with panel("Ultime notizie", right=("le tue aziende" if has_mine else "tutte")
                   + ' · <a href="./notizie" target="_self">tutte le notizie ›</a>'):
            render_latest_news(d, tickers=mine if has_mine else None, limit=10)
    with docs_col:
        _documents_panel(d, mine, days)

    @st.fragment(run_every=every)
    def markets():
        with panel("Mercati"):
            _market_strip(d)
        q, _ = _stock_quotes(d)
        a, b = st.columns([2, 1])
        with a:
            _movers(d, q)
        with b:
            _insider_panel(d, mine)

    markets()

    with st.expander("Mappa dell'S&P 500"):
        q, source = _stock_quotes(d)
        st.caption(f"Fonte: {source}.")
        _heatmap(d, q)
    with st.expander("Borse mondiali"):
        _exchanges_panel(d)
