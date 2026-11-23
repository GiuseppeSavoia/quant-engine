"""Scheda azienda (docs/revisione_grafica.md §4.2): il giudizio prima di tutto.

Dall'alto: pulsanti (Segui, Soglie, Cambia azienda) · testata a due livelli · VERDETTO con la nota sulla prova
storica e "Perché?" · "Cosa è cambiato" dall'ultimo bilancio · avvisi attivi · menu delle funzioni (sei voci
principali + "Altro") · la funzione aperta. L'azienda arriva dalla cornice (barra di ricerca, link `?t=AAPL&fn=DCF`,
session_state "ticker" e "co_fn"); i codici delle funzioni sono in `modules.commands.FUNCTIONS`.
"""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import commands
from modules.dashboard_data import DashboardData
from modules.metrics import GROUPS
from ui.common import SERIES, TERMINAL, _layout, _open_selected, data
from ui.components import (NA, dense_table, empty_state, fmt, fmt_html, fmt_metric, head_html, panel, range_html,
                           ticker_link)
from ui.pages.azienda import verdetto as V
from ui.pages.azienda.bilanci import _bars, _financials_tab
from ui.pages.azienda.prezzo import _price_tab
from ui.pages.azienda.ricerca import _ownership_tab, _research_tab, _strategy_tab
from ui.pages.azienda.stime import _estimates_tab
from ui.pages.azienda.valutazione import _valuation_tab
from ui.rules import _metric_column

# menu: le prime sei voci sempre visibili, le altre nella tendina "Altro" (ordine = numeri dei comandi)
MAIN_FUNCTIONS = ["PRV", "FA", "GP", "DCF", "RV", "CF"]
DEFAULT_FUNCTION = "PRV"


# ================================================================== pagina
def page_company():
    d = data()
    if not d.tickers:
        empty_state("Nessuna azienda nel database", "Aggiorna i dati dal menu \"Dati\" in alto a destra.")
        return
    ticker = _current_ticker(d)
    info = d.info(ticker)
    cur = d.currency(ticker)
    sector = info.get("sector") if isinstance(info.get("sector"), str) else None
    note = d.note(ticker)
    fair_value = note.get("fair_value")
    target = V.price_target(d, ticker)
    px = d.prices(ticker)

    _actions(d, ticker, target, cur)
    with st.spinner("Calcolo il giudizio…"):
        v = V.assess(d, ticker, fair_value)
    cm = d.company_metrics(ticker)
    value = cm.set_index("key")["value"].get
    st.html(_head(d, ticker, info, px, value, fair_value, target, cur, v))

    last = float(px["close"].iloc[-1]) if not px.empty else None
    if v is not None:
        V.render_verdict(v, V.value_html(v, v.price or last, cur, fair_value, target))
    else:
        V.verdict_error_box(ticker)
    with st.spinner("Confronto con il bilancio precedente…"):
        wc = V.what_changed(d, ticker, sector, cur)
    V.render_changes(wc, cur)
    V.render_alerts(d, ticker)

    fn = _function_menu()
    sections = {
        "PRV": lambda: V.render_evidence(v, cm, sector),
        "DES": lambda: _summary_tab(cm, sector, cur),
        "FA": lambda: _financials_tab(d, ticker),
        "ERN": lambda: _financials_tab(d, ticker, view="Trimestri singoli"),
        "GP": lambda: _price_tab(d, ticker, px),
        "RV": lambda: _peers_section(d, ticker),
        "DCF": lambda: _valuation_tab(d, ticker),
        "DVD": lambda: _dividends_section(d, ticker),
        "HDS": lambda: _ownership_tab(d, ticker),
        "CF": lambda: _company_filings_section(d, ticker),
        "NOTE": lambda: _research_tab(d, ticker, note),
        "AUTO": lambda: _strategy_tab(d, ticker),
        "EE": lambda: _estimates_tab(d, ticker),
    }
    if px.empty and fn in ("GP", "DCF", "RV", "DVD", "AUTO"):
        empty_state("Nessun prezzo per questo titolo", "Senza prezzi questa funzione non si può calcolare: prova "
                    "Bilanci o Notizie e documenti, oppure aggiorna i dati.")
        return
    sections[fn]()


def _current_ticker(d: DashboardData) -> str:
    """Azienda aperta: quella scelta dalla cornice o da "Cambia"; altrimenti la prima delle tue aziende."""
    t = st.session_state.get("ticker")
    if t not in d.tickers:
        follow = [x for x in d.watchlist() if x in d.tickers]
        t = follow[0] if follow else d.tickers[0]
    st.session_state["ticker"] = t
    return t


def _pick_company():
    t = st.session_state.get("co_pick")
    if t:
        st.session_state["ticker"] = t
    st.session_state["co_pick"] = None


def _actions(d: DashboardData, ticker: str, target: dict, cur: str):
    """Riga dei pulsanti sopra la testata: Segui, Soglie di prezzo, Cambia azienda (la ricerca vera è nella cornice)."""
    following = ticker in d.watchlist()
    with st.container(horizontal=True, horizontal_alignment="right", gap="small", key="co_actions"):
        if st.button("★ Seguita" if following else "☆ Segui", key="co_follow",
                     help="Togli dalle tue aziende" if following else "Aggiungi alle tue aziende (Le mie aziende, F2)"):
            d.follow(ticker, on=not following)
            st.session_state["qe_flash"] = f"{ticker} " + ("tolta dalle" if following else "aggiunta alle") + \
                " tue aziende."
            st.rerun()
        with st.popover("Soglie di prezzo", help="Compra sotto / vendi sopra: avvisi PX- e PX+"):
            V.targets_form(d, ticker, target, cur)
        with st.popover(_pipeline_label(d, ticker), help="Fase dell'azienda nel tuo lavoro (pagina Pipeline)"):
            _pipeline_panel(d, ticker)
        with st.popover("Esporta", help="Modello di valutazione in Excel (con le formule) e sintesi in PDF"):
            _export_panel(d, ticker)
        with st.popover("Cambia azienda", help="Oppure scrivi il ticker o il nome nella barra in alto"):
            st.selectbox("Azienda", d.tickers, key="co_pick", on_change=_pick_company, index=None,
                         placeholder="Scrivi ticker o nome…",
                         format_func=lambda t: f"{t} — {d.info(t).get('name') or ''}")


def _pipeline_stage(d: DashboardData, ticker: str) -> str | None:
    pipe = d.db.load_pipeline()
    return pipe.set_index("ticker")["stage"].get(ticker) if not pipe.empty else None


def _pipeline_label(d: DashboardData, ticker: str) -> str:
    from modules.workflow import STAGE_LABELS

    stage = _pipeline_stage(d, ticker)
    return f"Pipeline: {STAGE_LABELS.get(stage, stage)}" if stage else "Pipeline"


def _pipeline_panel(d: DashboardData, ticker: str):
    from modules import workflow as wf

    cur = _pipeline_stage(d, ticker)
    keys = [k for k, _ in wf.STAGES]
    stage = st.radio("Fase", keys, index=keys.index(cur) if cur in keys else 0, key="co_pipe_stage",
                     format_func=wf.STAGE_LABELS.get)
    note = st.text_input("Nota", key="co_pipe_note", placeholder="es. idea da 13F, scartata per il debito")
    if st.button("Salva la fase", key="co_pipe_save", type="primary", width="stretch"):
        d.db.set_pipeline_stage(ticker, stage, note.strip() or None)
        st.session_state["qe_flash"] = f"{ticker}: {wf.STAGE_LABELS[stage]}."
        st.rerun()
    if cur:
        fv = d.note(ticker).get("fair_value")
        missing = wf.missing_for(stage, wf.completeness(d.research(ticker), fv, not d.valuation_diary(ticker).empty))
        if missing:
            st.caption("Per questa fase manca: " + ", ".join(missing) + ".")


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _export_panel(d: DashboardData, ticker: str):
    """Excel e PDF si preparano solo quando si chiede (qualche secondo), poi si scaricano."""
    day = pd.Timestamp.now().strftime("%Y%m%d")
    st.caption("Excel: DCF con le formule (ipotesi in blu da cambiare), scenari, sensibilità, costo del capitale, "
               "bilanci con la fonte di ogni esercizio. PDF: giudizio, tesi, numeri, concorrenti, valutazione.")
    key_x, key_p = f"qe_xlsx_{ticker}", f"qe_pdf_{ticker}"
    if st.button("Prepara il modello Excel", key="co_xlsx_make", width="stretch"):
        from modules.excel_model import company_model

        with st.spinner("Preparo il modello…"):
            st.session_state[key_x] = company_model(d, ticker) or b""
    if key_x in st.session_state:
        if st.session_state[key_x]:
            st.download_button("Scarica il modello Excel", data=st.session_state[key_x], mime=XLSX_MIME,
                               file_name=f"{ticker}_modello_{day}.xlsx", width="stretch", key="co_xlsx_dl")
        else:
            st.caption("Valutazione non impostabile: mancano ricavi, azioni o prezzi.")
    if st.button("Prepara la sintesi in PDF", key="co_pdf_make", width="stretch"):
        from modules.research_pdf import company_pdf

        with st.spinner("Preparo il PDF…"):
            st.session_state[key_p] = company_pdf(d, ticker)
    if st.session_state.get(key_p):
        st.download_button("Scarica il PDF", data=st.session_state[key_p], mime="application/pdf",
                           file_name=f"{ticker}_sintesi_{day}.pdf", width="stretch", key="co_pdf_dl")


def _head(d: DashboardData, ticker: str, info: dict, px: pd.DataFrame, value, fair_value, target: dict, cur: str,
          v) -> str:
    """Testata a due livelli: identità e prezzo; sotto i numeri chiave (scelti secondo il tipo di azienda)."""
    group = d.universe_of(ticker)
    sub = info.get("sub_industry")
    meta = [x for x in (info.get("sector"), sub) if isinstance(x, str) and x and x != "nan"]
    if group != "S&P 500":
        meta.append(group + (f" · valori in {cur}" if cur != "USD" else ""))
    if v is not None and v.profile != "general":
        meta.append(v.profile_label)
    stmt = d.metrics().at[ticker, "statement_date"] if ticker in d.metrics().index else None
    europe = group not in ("S&P 500", "Aggiunta da te")
    src = []
    last = change = None
    kv = [("Cap.", fmt_html(value("market_cap"), "money", currency=cur))]
    if not px.empty:
        close = px["close"]
        last = float(close.iloc[-1])
        change = last / float(close.iloc[-2]) - 1 if len(close) > 1 else None
        src.append(f"chiusura {fmt(close.index[-1], 'date_short')}")
    if stmt is not None and pd.notna(stmt):
        src.append(f"{'esercizio' if europe else 'bilanci'} al {fmt(stmt, 'date_short')}")
    profile = v.profile if v is not None else "general"
    keys = {"bank": ["pe", "ptbv", "rotce"], "insurer": ["pe", "ptbv", "rotce"], "reit": ["p_ffo", "ffo_yield", "pb"]}
    labels = {"pe": "P/E", "ev_ebit": "EV/EBIT", "fcf_yield": "Rend. FCF", "ptbv": "P/TBV", "rotce": "ROTCE",
              "p_ffo": "P/FFO", "ffo_yield": "Rend. FFO", "pb": "P/B"}
    for k in keys.get(profile, ["pe", "ev_ebit", "fcf_yield"]):
        kv.append((labels[k], fmt_metric(k, value(k), html_out=True)))
    kv.append(("1 anno", fmt_html(value("ret_1y"), "pct", arrow=True)))
    if not px.empty:
        year = px.tail(252)
        lo = year["low"].min() if "low" in year and year["low"].notna().any() else year["close"].min()
        hi = year["high"].max() if "high" in year and year["high"].notna().any() else year["close"].max()
        kv.append(("52 sett.", range_html(lo, hi, last)))
    if fair_value is not None and pd.notna(fair_value) and last:
        kv.append(("Tuo FV", f"{fmt(fair_value, 'price')} "
                             f"{fmt_html(float(fair_value) / last - 1, 'pct', digits=0, signed=True)}"))
    else:
        kv.append(("Tuo FV", f'<span class="qe-na">{NA}</span> {ticker_link(ticker, "NOTE", "imposta")}'))
    kv.append(("Soglie", V.targets_html(target, cur)))
    return head_html(ticker, info.get("name") or "", meta=" · ".join(meta), price=last, currency=cur, change=change,
                     source=" · ".join(src), kv=kv)


# ================================================================== menu delle funzioni
def _pick(key: str):
    v = st.session_state.get(key)
    if v:
        st.session_state["co_fn"] = v


def _function_menu() -> str:
    """Sei voci principali (pulsanti) + "Altro" (tendina). La funzione aperta sta in session_state["co_fn"], che
    anche la barra dei comandi imposta ("AAPL FA", "FA", "4")."""
    names = dict(commands.FUNCTIONS)
    codes = [c for c, _ in commands.FUNCTIONS]
    fn = st.session_state.get("co_fn")
    if fn not in names:
        fn = DEFAULT_FUNCTION
    st.session_state["co_fn"] = fn
    more = [c for c in codes if c not in MAIN_FUNCTIONS]
    # i due controlli mostrano la funzione aperta (uno solo dei due ha un valore)
    st.session_state["co_fn_main"] = fn if fn in MAIN_FUNCTIONS else None
    st.session_state["co_fn_more"] = fn if fn in more else None
    number = {c: i for i, c in enumerate(codes, 1)}
    left, right = st.columns([5, 1.3], vertical_alignment="center", gap="small")
    left.segmented_control("Funzione", MAIN_FUNCTIONS, key="co_fn_main", label_visibility="collapsed",
                           on_change=_pick, args=("co_fn_main",),
                           format_func=lambda c: f"{names[c]} · {c}")
    right.selectbox("Altre funzioni", more, key="co_fn_more", label_visibility="collapsed", on_change=_pick,
                    args=("co_fn_more",), placeholder="Altro…",
                    format_func=lambda c: f"{names[c]} · {c}",
                    help="Comandi: " + ", ".join(f"{number[c]} {c}" for c in codes))
    return fn


# ================================================================== funzioni della scheda
def _peers_section(d: DashboardData, ticker: str):
    peers, group, cols = d.peers(ticker)
    if peers.empty:
        empty_state("Nessun gruppo di confronto", "Per questa azienda manca il settore.")
        return
    st.caption(f"Le aziende più grandi di \"{group}\", con {ticker} evidenziata; sotto, {ticker} contro le mediane "
               "del gruppo.")
    median = peers[cols].median()

    def mark(row):
        on = row["ticker"] == ticker
        return [f"background-color: {'#3a2a00' if TERMINAL else '#e6f1fb'}" if on else "" for _ in row]

    shown = peers.assign(market_cap=peers["market_cap"] / 1e9)
    slot = st.container()
    sel = st.dataframe(shown.style.apply(mark, axis=1), hide_index=True, width="stretch", on_select="rerun",
                       selection_mode="single-row", key=f"rv_{ticker}",
                       column_config={"ticker": "Ticker", "name": "Azienda", **{c: _metric_column(c) for c in cols}})
    _open_selected(peers, sel, slot)
    from modules.metrics import METRIC_BY_KEY

    own = peers.set_index("ticker")
    rows = pd.DataFrame([{"metrica": METRIC_BY_KEY[c].label,
                          "own": fmt_metric(c, own.at[ticker, c], html_out=True) if ticker in own.index else NA,
                          "median": fmt_metric(c, median[c], html_out=True)} for c in cols])
    dense_table(rows, {"metrica": ("Metrica", "text"), "own": (ticker, "html"), "median": ("Mediana del gruppo", "html")})


def _dividends_section(d: DashboardData, ticker: str):
    h = d.dividend_history(ticker)
    if h.empty or (h["dividends"].fillna(0).sum() == 0 and h["buybacks"].fillna(0).sum() == 0):
        empty_state("Né dividendi né riacquisti", "Negli esercizi disponibili l'azienda non ha restituito soldi agli "
                    "azionisti (o i dati mancano).")
        return
    x = [str(p.year) for p in h["period_end"]]
    left, right = st.columns(2)
    left.markdown("**Soldi restituiti agli azionisti (miliardi)**")
    left.plotly_chart(_bars(h.set_index("period_end"), x, [("dividends", "Dividendi"), ("buybacks", "Riacquisti")]),
                      width="stretch")
    fig = go.Figure()
    for i, (col, name) in enumerate([("dividend_yield", "Rendimento da dividendi"),
                                     ("shareholder_yield", "Dividendi + riacquisti")]):
        if h[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=h[col], name=name, mode="lines+markers", hovertemplate="%{y:.1%}",
                                     line=dict(color=SERIES[i], width=2), marker=dict(size=7)))
    fig.update_yaxes(tickformat=".1%")  # con ".0%" rendimenti tra 0,5% e 1,5% davano tre tacche "1%"
    fig.update_xaxes(type="category")
    right.markdown("**Rendimento per chi possiede le azioni** (al prezzo di fine esercizio)")
    right.plotly_chart(_layout(fig, 330), width="stretch")
    table = h.assign(period_end=x).set_index("period_end").T
    kinds = {"dividends": ("Dividendi (mln)", "mln"), "buybacks": ("Riacquisti (mln)", "mln"),
             "net_income": ("Utile netto (mln)", "mln"), "free_cash_flow": ("Free cash flow (mln)", "mln"),
             "shares": ("Azioni (mln)", "azioni"), "dps": ("Dividendo per azione", "price"),
             "payout": ("Dividendi / utile", "pct"), "fcf_payout": ("(Dividendi + riacquisti) / FCF", "pct"),
             "dividend_yield": ("Rendimento da dividendi", "pct"),
             "shareholder_yield": ("Rendimento per gli azionisti", "pct")}

    def show(k, v):
        kind = kinds[k][1]
        if kind == "mln":
            return fmt(v / 1e6 if v is not None and np.isfinite(v) else None, "int")
        if kind == "azioni":
            return fmt(v / 1e6 if v is not None and np.isfinite(v) else None, "num", digits=1)
        return fmt(v, kind)

    st.dataframe(pd.DataFrame({c: [show(k, table.at[k, c]) if k in table.index else NA for k in kinds]
                               for c in table.columns}, index=[lab for lab, _ in kinds.values()]), width="stretch")
    st.caption("Rendimenti al prezzo di fine esercizio. Dividendo per azione = dividendi pagati / azioni diluite "
               "(approssimato). Riacquisti: soldi spesi per comprare azioni proprie.")


def _company_filings_section(d: DashboardData, ticker: str):
    with panel("Ultime notizie", right=f"titoli che citano {html.escape(ticker)}"):
        try:
            from ui.pages.notizie import render_latest_news

            render_latest_news(d, tickers=[ticker], limit=8)
        except Exception as exc:  # noqa: BLE001 - le notizie non devono rompere la scheda
            st.html(f'<div class="qe-empty-line">Notizie non disponibili ({html.escape(str(exc))}).</div>')
    f = d.company_filings(ticker)
    if f.empty:
        sec_filer = ticker not in set(d.foreign["ticker"]) | set(d.custom_companies["ticker"])
        empty_state("Nessun deposito in archivio",
                    "L'archivio dei documenti SEC si riempie con l'aggiornamento dei dati (menu Dati in alto a destra)."
                    if sec_filer else "Le aziende europee e quelle aggiunte da te non depositano alla SEC.")
        return
    f = f.assign(what=np.where(f["important"], "⚠ " + f["what"], f["what"]))
    with panel("Documenti depositati", right=f"{len(f)} · 10-K, 10-Q, 8-K dalla SEC"):
        st.caption("⚠ = da guardare subito (revisore, bilanci non affidabili, fallimento, svalutazioni...). Il link "
                   "apre il documento sul sito della SEC.")
        st.dataframe(f[["when", "form", "what", "url"]], hide_index=True, width="stretch", height=480,
                     column_config={"when": st.column_config.DatetimeColumn("Depositato", format="DD/MM/YYYY HH:mm"),
                                    "form": "Documento", "what": "Cosa",
                                    "url": st.column_config.LinkColumn("SEC", display_text="apri")})


def _summary_tab(cm: pd.DataFrame, sector: str | None, currency: str = "USD"):
    """Tutte le metriche del catalogo (ex DES), per gruppo, con la mediana del settore e dell'S&P 500."""
    st.caption(f"Ogni numero con la mediana del suo settore ({sector or 'n/d'}) e dell'S&P 500. \"Nel settore\": "
               "quante aziende del settore fanno peggio (100% = la migliore; per P/E e simili, più basso è meglio).")
    groups = [g for g in GROUPS if g != "Dimensione" and cm.loc[cm["group"] == g, "value"].notna().any()]
    for i in range(0, len(groups), 2):
        cols = st.columns(2)
        for col, group in zip(cols, groups[i:i + 2]):
            rows = cm[cm["group"] == group]
            with col, panel(group):
                st.html(_metric_table_html(rows, currency))
    with st.expander("Cosa significano"):
        for _, r in cm.iterrows():
            if r["help"]:
                st.markdown(f"- **{r['label']}**: {r['help']}")


def _metric_table_html(rows: pd.DataFrame, currency: str) -> str:
    body = []
    for _, r in rows.iterrows():
        k = r["key"]
        cur = currency if currency else "USD"
        rank = r["rank_sector"]
        body.append(f'<tr><td class="t fill" title="{html.escape(str(r["label"]))}">{html.escape(str(r["label"]))}</td>'
                    f'<td class="n">{fmt_metric(k, r["value"], html_out=True, currency=cur)}</td>'
                    f'<td class="n">{fmt_metric(k, r["sector_median"], html_out=True, currency=cur)}</td>'
                    f'<td class="n">{fmt_html(rank, "pct", digits=0)}</td>'
                    f'<td class="n">{fmt_metric(k, r["index_median"], html_out=True, currency="USD")}</td></tr>')
    return ('<div class="qe-tablewrap"><table class="qe-dense"><thead><tr><th>Metrica</th><th class=n>Valore</th>'
            '<th class=n>Settore</th><th class=n>Nel settore</th><th class=n>S&amp;P 500</th></tr></thead><tbody>'
            + "".join(body) + "</tbody></table></div>")
