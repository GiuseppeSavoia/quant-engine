"""Confronta (F5, docs/revisione_grafica.md §4.5): da 2 a 5 aziende. In cima il giudizio e i pilastri di ognuna,
poi le metriche del catalogo in tabelle fitte (una per gruppo, colonne = aziende, migliore della riga in grassetto
con ▲), poi i grafici nel tempo."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.metrics import GROUPS, METRIC_BY_KEY
from ui.common import SERIES, _layout, data
from ui.components import (CURRENCY_SYMBOL, empty_state, fmt, fmt_html, fmt_metric, na_html, panel,
                           pillar_bar_html, ticker_link, verdict_badge)
from ui.pages.azienda.valutazione import _multiples
from ui.rules import PILLAR_HELP, pillar_grade, verdict_note, verdict_table

from modules.services.compare import MAX_COMPARE, PILLAR_ROWS  # noqa: E402


# ---------------------------------------------------------------- tabelle HTML (colonne = aziende)
def _head(tickers: list[str], d, first: str = "", numeric: bool = False) -> str:
    cells = "".join(
        f'<th{" class=n" if numeric else ""} title="{html.escape(str(d.info(t).get("name") or ""))}">'
        f'<span style="color:{SERIES[i % len(SERIES)]}">●</span> {ticker_link(t)}</th>'
        for i, t in enumerate(tickers))
    return f'<thead><tr><th>{html.escape(first)}</th>{cells}</tr></thead>'


def _table(head: str, rows: list[str], n: int) -> str:
    """Tabella fitta con colonne di larghezza fissa (prima 28%, le aziende in parti uguali): i gruppi uno sotto
    l'altro restano allineati."""
    each = 72 / max(n, 1)
    cols = '<col style="width:28%">' + "".join(f'<col style="width:{each:.2f}%">' for _ in range(n))
    return (f'<div class="qe-tablewrap"><table class="qe-dense" style="table-layout:fixed"><colgroup>{cols}'
            f'</colgroup>{head}<tbody>{"".join(rows)}</tbody></table></div>')


def _row(label: str, cells: list[str], *, title: str = "", align: str = "n") -> str:
    tip = f' title="{html.escape(title)}"' if title else ""
    return (f'<tr><td class="t"{tip}>{html.escape(label)}</td>'
            + "".join(f'<td class="{align}">{c}</td>' for c in cells) + "</tr>")


def _verdict_html(d, tickers: list[str]) -> str:
    """Giudizio, pilastri, copertura, prezzo e valori dell'utente: una colonna per azienda."""
    v = verdict_table(d, tickers).reindex(tickers)
    for c in ["category", "verdict", "coverage"] + [k for k, _ in PILLAR_ROWS]:
        if c not in v:
            v[c] = np.nan
    targets = d.db.load_price_targets()
    targets = targets.set_index("ticker") if not targets.empty else pd.DataFrame(columns=["buy_below"])
    rows = []
    rows.append(_row("Azienda", [html.escape(str(d.info(t).get("name") or "")) for t in tickers], align="t"))
    rows.append(_row("Giudizio", [verdict_badge(v.at[t, "category"], v.at[t, "verdict"])
                                  if isinstance(v.at[t, "category"], str) else na_html() for t in tickers],
                     align="t", title="Sintesi dei criteri impostati, non una raccomandazione"))
    for key, label in PILLAR_ROWS:
        cells = []
        for t in tickers:
            s = v.at[t, key] if key in v.columns else None
            s = None if s is None or pd.isna(s) else float(s)
            grade = pillar_grade(key, s)
            # con 4-5 aziende la colonna è stretta: il voto in parole va nel suggerimento
            bar = pillar_bar_html(s, grade if len(tickers) <= 3 else "")
            cells.append(f'<span title="{html.escape(grade)}">{bar}</span>' if grade else bar)
        rows.append(_row(label, cells, align="t", title=PILLAR_HELP[key]))
    rows.append(_row("Copertura delle prove", [fmt_html(v.at[t, "coverage"], "pct", digits=0) for t in tickers],
                     title="Quota delle prove del giudizio con il dato disponibile"))
    prices, fvs, buys = [], [], []
    for t in tickers:
        cur = d.currency(t)
        px = d.data.prices(t)
        last = float(px.iloc[-1]) if not px.empty else np.nan
        prices.append(fmt_html(last, "price", currency=cur))
        note = d.note(t)
        fv = note.get("fair_value")
        fvs.append(fmt_html(fv, "price", currency=cur) + (
            f' <span class="{"pos" if fv / last > 1 else "neg"}">({fmt(fv / last - 1, "pct", signed=True)})</span>'
            if fv and np.isfinite(last) and last > 0 else ""))
        buy = targets["buy_below"].get(t) if t in targets.index else None
        buys.append(fmt_html(buy, "price", currency=cur) if buy is not None and pd.notna(buy) else na_html())
    rows.append(_row(f"Prezzo (chiusura {fmt(d.data.last_date, 'date_short')})", prices))
    rows.append(_row("Tuo fair value (margine)", fvs))
    rows.append(_row("Tua soglia «compra sotto»", buys))
    return _table(_head(tickers, d), rows, len(tickers))


def _group_html(d, table: pd.DataFrame, tickers: list[str], group: str) -> str:
    rows = []
    sub = table[table["group"] == group]
    currencies = {t: d.currency(t) for t in tickers}
    for key, r in sub.iterrows():
        m = METRIC_BY_KEY.get(key)
        best = r["best"] if isinstance(r["best"], str) else None
        cells = []
        for t in tickers:
            body = fmt_metric(key, r.get(t), html_out=True, currency=currencies[t])
            if best == t:
                body = f'<b title="la migliore della riga">▲ {body}</b>'
            cells.append(body)
        rows.append(_row(r["label"], cells, title=(m.help if m and m.help else "")))
    return _table(_head(tickers, d, "Metrica", numeric=True), rows, len(tickers))


def _compare_table(d, tickers: list[str]) -> pd.DataFrame:
    """Metriche affiancate senza mai andare in eccezione (servizio compare.compare_metrics)."""
    return d.compare_metrics(tickers)


# ---------------------------------------------------------------- pagina
def page_compare():
    d = data()
    st.title("Confronta")
    labels = {t: f"{t} — {d.info(t).get('name') or ''}" for t in d.tickers}
    if "cmp" not in st.session_state:
        start = [t for t in d.watchlist() if t in labels][:3]
        st.session_state["cmp"] = start if len(start) >= 2 else [t for t in ("AAPL", "MSFT") if t in labels]
    st.session_state["cmp"] = [t for t in st.session_state["cmp"] if t in labels][:MAX_COMPARE]
    tickers = st.multiselect(f"Aziende (da 2 a {MAX_COMPARE})", d.tickers, key="cmp", max_selections=MAX_COMPARE,
                             format_func=labels.get, placeholder="Cerca per ticker o nome")
    if len(tickers) < 2:
        empty_state("Scegli almeno due aziende",
                    "Scrivile qui sopra, oppure selezionale in Esplora o in Le mie aziende e premi «Confronta». "
                    "Dalla barra dei comandi: CMP AAPL MSFT.")
        return

    with panel("Giudizio", right="sintesi dei criteri · non è una raccomandazione"):
        st.html(_verdict_html(d, tickers))
        verdict_note()

    table = _compare_table(d, tickers)
    st.subheader("Numeri a confronto")
    st.caption("▲ in grassetto = la migliore della riga (per P/E e simili, la più bassa). Ultimi 12 mesi, prezzi "
               "dell'ultima chiusura; importi nella valuta di ogni azienda.")
    if table.empty:
        empty_state("Metriche non disponibili", "Per queste aziende mancano le metriche del catalogo "
                    "(prezzi o bilanci assenti alla data dei dati).")
    for group in GROUPS:
        if not table.empty and (table["group"] == group).any():
            with panel(group):
                st.html(_group_html(d, table, tickers, group))

    st.subheader("Nel tempo")
    years = st.radio("Periodo del prezzo", [1, 3, 5, 10], index=2, horizontal=True, key="cmp_years",
                     format_func=lambda y: f"{y} {'anno' if y == 1 else 'anni'}")
    left, right = st.columns(2)
    with left:
        st.markdown("**Prezzo** (1 = inizio del periodo)")
        _compare_chart(d.compare_prices(tickers, years), tickers, log=True)
    with right:
        st.markdown("**Ricavi degli ultimi 12 mesi** (100 = prima data comune)")
        _compare_chart(d.compare_series(tickers, "revenue", index_to_first=True), tickers)
    left, right = st.columns(2)
    with left:
        st.markdown("**Margine operativo** (ultimi 12 mesi)")
        _compare_chart(d.compare_series(tickers, "operating_margin"), tickers, pct_axis=True)
    with right:
        st.markdown("**P/E nel tempo** (con i bilanci noti a ogni data)")
        _compare_chart(_pe_history(d, tickers), tickers)


def _pe_history(d, tickers: list[str]) -> pd.DataFrame:
    """P/E nel tempo di ogni azienda; vuoto (niente eccezione) se per un'azienda mancano i multipli."""
    cols = {}
    for t in tickers:
        try:
            hist = _multiples(d, t, d.run_id or "")
        except Exception:  # noqa: BLE001 - un'azienda senza storia non deve fermare la pagina
            continue
        if isinstance(hist, pd.DataFrame) and "pe" in hist:
            cols[t] = hist["pe"]
    return pd.DataFrame(cols)


def _compare_chart(df: pd.DataFrame, tickers: list[str], log: bool = False, pct_axis: bool = False):
    if df.empty or df.dropna(how="all").empty:
        st.html('<div class="qe-empty-line">Dati insufficienti per questo grafico.</div>')
        return
    fig = go.Figure()
    for i, t in enumerate(tickers):
        if t in df and df[t].notna().any():
            s = df[t].dropna()
            fig.add_trace(go.Scatter(x=s.index, y=s, name=t, mode="lines", line=dict(color=SERIES[i], width=2),
                                     hovertemplate="%{y:.1%}" if pct_axis else "%{y:,.2f}"))
    if log:
        fig.update_yaxes(type="log")
    if pct_axis:
        fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(_layout(fig, 320), width="stretch", config={"displayModeBar": False})


__all__ = ["MAX_COMPARE", "page_compare", "CURRENCY_SYMBOL"]
