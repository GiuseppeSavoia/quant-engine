"""Pagina "Strategia automatica": le decisioni BUY/SKIP/BLOCKED della strategia automatica ORIGINALE (sperimentale,
ordini solo simulati) e il rischio del suo portafoglio simulato.

Non è il Giudizio della scheda azienda (`modules/verdict.py`): lo dice la banda in cima, perché i due giudizi
usano criteri diversi e possono non coincidere.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard_data import DashboardData
from ui.common import ACTION_LABEL, MUTED, SERIES, _layout, data
from ui.components import (
    badge, dense_table, fmt, fmt_html, notice, num_column, panel, result_row_html, table_height,
)
from ui.pages.backtest import open_selected, run_label, start_job

ORDER_TYPE = {"MARKET": "al mercato", "LIMIT": "limite", "MOC": "in chiusura", "LOC": "limite in chiusura"}


def _risk_panel(d: DashboardData):
    risk = d.portfolio_risk()
    m = risk.get("measures", {})
    if "volatility" not in m:
        return
    with panel("Rischio del portafoglio simulato", right="pesi di oggi sui rendimenti dell'ultimo anno"):
        st.html(result_row_html([
            ("Volatilità annua", fmt_html(m.get("volatility"), "pct")),
            ("Beta", fmt_html(m.get("beta"), "num")),
            ("VaR 1 giorno", fmt_html(-m["var_1d"] if m.get("var_1d") is not None else None, "pct")),
            ("CVaR 1 giorno", fmt_html(-m["cvar_1d"] if m.get("cvar_1d") is not None else None, "pct")),
            ("Posizioni equivalenti", fmt_html(m.get("effective_positions"), "num", digits=1)),
        ], note=f"{fmt(m.get('positions'), 'int')} titoli. VaR: perdita superata nel 5% peggiore dei giorni; CVaR: "
                "perdita media in quei giorni. Beta 1 = si muove come il mercato. La liquidità conta zero."))
        stress = risk.get("stress", pd.DataFrame())
        if stress.empty:
            return
        fig = go.Figure()
        for i, (col, name) in enumerate([("portfolio", "Portafoglio simulato"), ("benchmark", "Benchmark")]):
            fig.add_trace(go.Bar(y=stress["scenario"], x=stress[col], name=name, orientation="h",
                                 marker=dict(color=SERIES[i], cornerradius=4), hovertemplate="%{x:+.1%}"))
        fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
        fig.update_xaxes(tickformat=".0%", zeroline=True, zerolinecolor=MUTED)
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(_layout(fig, 60 + 46 * len(stress)).update_layout(hovermode="y unified"), width="stretch")
        st.caption("Prove di stress: il portafoglio di oggi nelle crisi passate (rendimento vero di ogni titolo; chi "
                   "allora non era quotato conta per beta x mercato) e con shock ipotetici. Il beta sottostima le "
                   "crisi: un portafoglio investito a metà perde circa la metà.")


def page_strategy():
    d = data()
    dec = d.decisions()
    st.title("Strategia automatica")
    notice("BUY, SKIP e BLOCKED sono le decisioni della strategia automatica ORIGINALE (regime di mercato, DCF, "
           "multi-factor), sperimentale e con ordini solo simulati: nessun broker collegato. Non sono il Giudizio "
           "della scheda azienda, che usa criteri diversi, e non sono raccomandazioni di investimento.", "warn",
           title="Sperimentale.")
    if dec.empty:
        start_job("Nessuna analisi nel database",
                  "L'analisi applica la strategia automatica ai dati già scaricati (di solito 1-2 minuti).",
                  "Esegui l'analisi", ["--strategy"], key="auto_run")
        return
    counts = dec["action"].value_counts()
    st.html(result_row_html([
        ("Regime di mercato", fmt_html(dec["regime"].iloc[0], "text")),
        ("BUY", fmt_html(int(counts.get("BUY", 0)), "int")),
        ("SKIP", fmt_html(int(counts.get("SKIP", 0)), "int")),
        ("BLOCKED", fmt_html(int(counts.get("BLOCKED", 0)), "int")),
        ("Dati al", fmt_html(dec["as_of"].iloc[0], "date")),
    ], note=f"Ultima analisi del {run_label(getattr(d, 'decisions_run_id', d.run_id))}."))
    try:  # la strategia non gira più a ogni aggiornamento (strategy.run_on_update): si rifà da qui quando serve
        stale = d.data.last_date is not None and pd.Timestamp(dec["as_of"].iloc[0]) < pd.Timestamp(d.data.last_date)
    except (TypeError, ValueError):
        stale = False
    if stale:
        start_job(f"Analisi sui dati del {fmt(dec['as_of'].iloc[0], 'date')}",
                  "La strategia automatica non si rifà più a ogni aggiornamento dei dati: è sperimentale e non serve "
                  "al giudizio sulle aziende. Puoi rifarla sui dati di oggi (di solito 1-2 minuti).",
                  "Rifai l'analisi", ["--strategy"], key="auto_rerun")

    orders = d.orders()
    with st.container():
        with panel("Portafoglio simulato", right=badge("solo simulato", "neutral")):
            if orders.empty:
                st.html('<div class="qe-empty-line">Nessun ordine: nessun titolo ha superato i filtri.</div>')
            else:
                view = orders.merge(dec[["ticker", "name", "sector", "confidence", "margin_of_safety"]], on="ticker",
                                    how="left")
                view = view.sort_values("weight", ascending=False).assign(
                    order_type=view["order_type"].map(lambda t: ORDER_TYPE.get(str(t), str(t).lower())))
                dense_table(view, {
                    "ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"), "sector": ("Settore", "text"),
                    "weight": ("Peso", "pct"), "shares": ("Azioni", "int"), "reference_price": ("Prezzo $", "price"),
                    "stop_loss": ("Stop-loss $", "price"), "take_profit": ("Take-profit $", "price"),
                    "confidence": ("Confidenza", "score"),
                    "margin_of_safety": ("Margine DCF", "pct", {"signed": True}),
                    "order_type": ("Tipo", "text"),
                }, max_rows=max(40, len(view)))
    left, right = st.columns([2, 3])
    with left:
        invested = float(orders["weight"].sum()) if not orders.empty else 0.0
        with panel("Peso per settore",
                   right=f"investito {fmt(invested, 'pct', digits=0)} · liquidità {fmt(1 - invested, 'pct', digits=0)}"
                   if not orders.empty else ""):
            if orders.empty:
                st.html('<div class="qe-empty-line">Nessuna posizione.</div>')
            else:
                by_sector = (orders.merge(dec[["ticker", "sector"]], on="ticker").groupby("sector")["weight"].sum()
                             .sort_values())
                fig = go.Figure(go.Bar(x=by_sector.values, y=by_sector.index, orientation="h",
                                       marker=dict(color=SERIES[0], cornerradius=4),
                                       hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
                fig.update_xaxes(tickformat=".0%")
                st.plotly_chart(_layout(fig, max(200, 60 + 30 * len(by_sector)), legend=False)
                                .update_layout(hovermode="closest"), width="stretch")

    with right:
        _risk_panel(d)
    _decisions_section(dec)


def _decisions_section(dec: pd.DataFrame):
    st.subheader("Tutte le decisioni")
    f1, f2, f3 = st.columns([2, 3, 3])
    actions = f1.multiselect("Decisione", ["BUY", "SKIP", "BLOCKED"], default=["BUY", "BLOCKED"], key="auto_actions",
                             placeholder="Tutte le decisioni")
    sectors = f2.multiselect("Settore", sorted(dec["sector"].dropna().unique()), key="auto_sectors",
                             placeholder="Tutti i settori")
    text = f3.text_input("Cerca", key="auto_text", placeholder="Ticker o nome")
    view = dec[dec["action"].isin(actions)] if actions else dec
    if sectors:
        view = view[view["sector"].isin(sectors)]
    if text:
        t = text.lower()
        view = view[view["ticker"].str.lower().str.contains(t, regex=False)
                    | view["name"].fillna("").str.lower().str.contains(t, regex=False)]
    view = view.assign(action=view["action"].map(lambda a: ACTION_LABEL.get(a, a))).sort_values(
        "confidence", ascending=False)
    st.caption(f"{fmt(len(view), 'int')} titoli su {fmt(len(dec), 'int')}, ordinati per confidenza.")
    slot = st.container()  # il pulsante "apri la scheda" sta sopra la tabella
    selection = st.dataframe(
        view[["ticker", "name", "sector", "action", "confidence", "threshold", "price", "fair_value",
              "margin_of_safety", "mf_score", "momentum", "quality", "volatility_score", "reasoning"]],
        hide_index=True, width="stretch", height=table_height(len(view)), on_select="rerun",
        selection_mode="single-row", key="auto_decisions",
        column_config={
            "ticker": "Ticker", "name": "Azienda", "sector": "Settore", "action": "Decisione",
            "confidence": num_column("Confidenza", "score", help="0-1: BUY se supera la soglia"),
            "threshold": num_column("Soglia", "score"),
            "price": num_column("Prezzo $", "price"),
            "fair_value": num_column("Fair value DCF $", "price"),
            "margin_of_safety": num_column("Margine DCF", "pct"),
            "mf_score": num_column("Multi-factor", "score"),
            "momentum": num_column("Momentum", "score"),
            "quality": num_column("Qualità", "score"),
            "volatility_score": num_column("Bassa volatilità", "score"),
            "reasoning": st.column_config.TextColumn("Motivazione", width="large"),
        })
    open_selected(view, selection, slot)
