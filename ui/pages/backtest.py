"""Pagina "Backtest e ricerca": il backtest della strategia automatica originale, la ricerca dei segnali e dei fattori.

Strumento di ricerca, non del percorso principale (docs/revisione_grafica.md C10): stesse regole di numeri e tabelle
del resto dell'app (`ui/components`), cautele statistiche visibili ma compatte, stati vuoti con un pulsante che
avvia il calcolo (main.py in un processo staccato, come il menu "Dati").

Qui stanno anche gli aiuti comuni alle pagine di ricerca (`start_job`, `open_selected`, `filings_section`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import updater
from modules.backtester import yearly_returns
from modules.dashboard_data import DashboardData
from ui import nav
from ui.common import DIVERGING, PROJECT_ROOT, SERIES, STATUS, _args, _layout, _line, data, main_config_args
from ui.components import (
    NA, dense_table, empty_state, fmt, fmt_html, notice, panel, result_row_html,
)


# ---------------------------------------------------------------- aiuti comuni alle pagine di ricerca
def run_label(run_id) -> str:
    """"20261101_120000" -> "01/11/2026 12:00" (l'identificativo interno non si mostra)."""
    try:
        return f"{pd.to_datetime(str(run_id), format='%Y%m%d_%H%M%S'):%d/%m/%Y %H:%M}"
    except (TypeError, ValueError):
        return NA


def start_job(what: str, why: str, label: str, args: list[str], key: str) -> None:
    """Stato vuoto con UN pulsante che avvia main.py (senza scaricare dati) in un processo staccato: l'avanzamento
    si segue nel menu "Dati" in alto a destra, come per "Aggiorna dati"."""
    running = updater.is_running(PROJECT_ROOT)
    why = why + (" Un aggiornamento è già in corso: attendi che finisca (menu Dati in alto a destra)." if running
                 else "")
    main_args = main_config_args() + ["--no-update", *args]
    if empty_state(what, why, None if running else label, key=key,
                   details="Da Terminale: python main.py " + " ".join(main_args)):
        try:
            updater.start(main_args, PROJECT_ROOT)
            st.toast("Calcolo avviato: l'avanzamento è nel menu Dati in alto a destra.", icon=":material/check:")
        except RuntimeError as exc:
            st.toast(str(exc), icon=":material/error:")
        st.rerun()


def open_selected(view: pd.DataFrame, selection, slot) -> None:
    """Pulsante (secondario) per aprire la scheda della riga selezionata in una tabella `st.dataframe`."""
    rows = selection.selection.rows if selection else []
    with slot:
        if rows:
            ticker = view.iloc[rows[0]]["ticker"]
            if st.button(f"Apri la scheda di {ticker} ›"):
                st.session_state["ticker"] = ticker
                st.switch_page(nav.page("azienda"))
        else:
            st.caption("Seleziona una riga per aprire la scheda dell'azienda.")


# ---------------------------------------------------------------- pagina
def page_backtest():
    d = data()
    st.title("Backtest e ricerca")
    runs = d.backtest_runs()
    eq = d.backtest_equity()
    if runs.empty or eq.empty:
        start_job("Nessun backtest nel database",
                  "Il backtest rifà la strategia automatica originale dal primo giorno configurato a oggi, con i "
                  "soli dati noti a ogni data.", "Esegui il backtest (circa 3 minuti)", ["--only-backtest"],
                  key="bt_run")
    else:
        _backtest_section(d, runs, eq)
    _factor_section(d)
    _signals_section(d)


def _backtest_section(d: DashboardData, runs: pd.DataFrame, eq: pd.DataFrame):
    last = runs.iloc[-1]
    m, b = last["metrics"]["strategy"], last["metrics"].get("benchmark") or {}
    p = last["params"]
    right = (f"dal {fmt(p.get('start'), 'date_short')} al {fmt(p.get('end'), 'date_short')} · "
             f"calcolato il {run_label(last['run_id'])}")
    with panel("Strategia automatica originale", right=right):
        excess = (m.get("total_return", np.nan) - b.get("total_return", np.nan)) if b else np.nan
        st.html(result_row_html([
            ("Rendimento", fmt_html(m.get("total_return"), "pct", signed=True)),
            ("Benchmark", fmt_html(b.get("total_return"), "pct", signed=True)),
            ("Differenza", fmt_html(excess, "pp")),
            ("Sharpe", f'{fmt_html(m.get("sharpe_ratio"), "num")} / {fmt_html(b.get("sharpe_ratio"), "num")}'),
            ("Perdita massima", fmt_html(m.get("max_drawdown"), "pct")),
            ("Operazioni", fmt_html(m.get("n_trades"), "int")),
            ("Costi", fmt_html(m.get("total_costs"), "money", currency="USD")),
        ], note=f"Ribilanciamento ogni {fmt(p.get('rebalance_days'), 'int')} giorni, "
                f"{fmt(p.get('n_tickers'), 'int')} titoli. Sharpe: strategia / benchmark. "
                "Ordini solo simulati: è la strategia sperimentale, non il Giudizio della scheda azienda."))
        fig = go.Figure()
        _line(fig, eq.index, eq["strategia"], "Strategia", SERIES[0])
        if "benchmark" in eq and eq["benchmark"].notna().any():
            _line(fig, eq.index, eq["benchmark"], "Benchmark (compra e tieni)", SERIES[1])
        fig.update_yaxes(title="Valore del portafoglio ($)")
        st.plotly_chart(_layout(fig, 380), width="stretch")
        fig = go.Figure(go.Scatter(x=eq.index, y=eq["esposizione"], fill="tozeroy", mode="lines", name="Esposizione",
                                   line=dict(color=SERIES[0], width=1.5), hovertemplate="%{y:.0%}"))
        fig.update_yaxes(tickformat=".0%", range=[0, 1.05], title="Quota investita")
        st.plotly_chart(_layout(fig, 180, legend=False), width="stretch")
    _yearly_section(eq)
    _coverage_section(d, m)


def _signals_section(d: DashboardData):
    st.subheader("Ricerca dei segnali della strategia")
    rs = d.research_summary()
    if rs.empty:
        start_job("Nessuna ricerca dei segnali nel database",
                  "Misura quanto ogni segnale della strategia (IC, quintili, settori) ha anticipato i rendimenti.",
                  "Esegui la ricerca dei segnali", ["--research"], key="bt_research")
        return
    ic = rs.pivot_table(index="signal", columns="horizon", values="ic_mean")
    tstat = rs.pivot_table(index="signal", columns="horizon", values="ic_t").reindex_like(ic)
    finite = np.abs(ic.to_numpy())[np.isfinite(ic.to_numpy())]
    lim = max(0.1, float(finite.max())) if finite.size else 0.1
    text = [[f"{fmt(v, 'num', digits=3, signed=True)}<br>t {fmt(t, 'num', digits=1, signed=True)}"
             for v, t in zip(rv, rt)] for rv, rt in zip(ic.to_numpy(), tstat.to_numpy())]
    fig = go.Figure(go.Heatmap(z=ic.to_numpy(), x=[f"{h} giorni" for h in ic.columns], y=ic.index,
                               colorscale=DIVERGING, zmin=-lim, zmax=lim, zmid=0, text=text,
                               texttemplate="%{text}", xgap=2, ygap=2, colorbar=dict(title="IC"),
                               hovertemplate="%{y}, %{x}: IC %{z:+.3f}<extra></extra>"))
    st.plotly_chart(_layout(fig, 340, legend=False).update_layout(hovermode="closest"), width="stretch")
    notice("IC = correlazione di rango tra punteggio e rendimento futuro (blu: il segnale ha ragione, rosso: "
           "funziona al contrario). Serve |t| ≥ 3 per parlare di segnale solido.", "info", title="Come leggerlo:")
    view = rs.sort_values(["signal", "horizon"])
    dense_table(view, {
        "signal": ("Segnale", "text"), "horizon": ("Orizzonte (gg)", "int"),
        "ic_mean": ("IC medio", "num", {"digits": 3, "signed": True}),
        "ic_t": ("t", "num", {"digits": 2, "signed": True}),
        "ic_hit_rate": ("Date con IC > 0", "pct"),
        "ic_sector_neutral": ("IC nei settori", "num", {"digits": 3, "signed": True}),
        "spread_annual": ("Spread annuo Q5-Q1", "pct", {"signed": True}),
        "monotonicity": ("Monotonia", "num"),
        "verdict": ("Giudizio", "text"),
    }, max_rows=max(40, len(view)))


def _factor_section(d: DashboardData):
    fs = d.factor_summary()
    if fs.empty:
        return
    from modules.factors import FACTOR_LABELS

    h = int(d.cfg.get("factors", {}).get("weight_horizon", 21))
    st.subheader("Ricerca dei fattori classici")
    view = fs[fs["horizon"] == h].sort_values("ic_t", ascending=False)
    colors = [SERIES[0] if v >= 0 else STATUS["BLOCKED"] for v in view["ic_mean"]]
    fig = go.Figure(go.Bar(x=view["ic_mean"], y=[FACTOR_LABELS.get(s, s) for s in view["signal"]], orientation="h",
                           marker=dict(color=colors, cornerradius=4),
                           text=[f"t {fmt(t, 'num', digits=1, signed=True)}" for t in view["ic_t"]],
                           textposition="outside", hovertemplate="%{y}: IC %{x:+.3f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(title=f"IC medio a {h} giorni (blu: il fattore ha ragione, rosso: al contrario)")
    st.plotly_chart(_layout(fig, max(240, 60 + 26 * len(view)), legend=False).update_layout(hovermode="closest"),
                    width="stretch")
    comp = d.factor_composite()
    weights = d.factor_weights()
    left, right = st.columns([2, 3])
    with left:
        with panel("Pesi della combinazione", right="scelti solo sulla prima metà"):
            if weights:
                w = pd.DataFrame({"factor": [FACTOR_LABELS.get(k, k) for k in weights],
                                  "weight": list(weights.values())}).sort_values("weight", ascending=False)
                dense_table(w, {"factor": ("Fattore", "text"), "weight": ("Peso", "pct")})
            else:
                st.html('<div class="qe-empty-line">Nessun fattore con IC positivo nella prima metà.</div>')
    with right:
        with panel("La combinazione nelle due metà", right=f"orizzonte {h} giorni"):
            c = comp[comp["horizon"] == h] if not comp.empty else comp
            dense_table(c, {
                "period": ("Periodo", "text"), "ic_mean": ("IC medio", "num", {"digits": 3, "signed": True}),
                "ic_t": ("t", "num", {"digits": 2, "signed": True}), "ic_hit_rate": ("Date con IC > 0", "pct"),
                "spread_annual": ("Spread annuo Q5-Q1", "pct", {"signed": True}),
            }, empty="Combinazione non ancora misurata.")
            notice("Conta solo la seconda metà: i pesi sono stati scelti senza guardarla. Per provarla come "
                   "strategia: Laboratorio, punto di partenza \"Fattori: pesi dalla ricerca\" (da giudicare sulla "
                   "seconda metà).", "info", title="Cautela:")


def _yearly_section(eq: pd.DataFrame):
    yearly = yearly_returns(eq["strategia"], eq["benchmark"] if "benchmark" in eq else None)
    if yearly.empty:
        return
    right = ""
    if "differenza" in yearly:
        right = (f"batte il benchmark in {int((yearly['differenza'] > 0).sum())} anni su {len(yearly)} "
                 "(primo e ultimo anno possono essere parziali)")
    with panel("Anno per anno", right=right):
        fig = go.Figure()
        fig.add_trace(go.Bar(x=yearly.index.astype(str), y=yearly["strategia"], name="Strategia",
                             marker=dict(color=SERIES[0], cornerradius=4), hovertemplate="%{y:+.1%}"))
        if "benchmark" in yearly:
            fig.add_trace(go.Bar(x=yearly.index.astype(str), y=yearly["benchmark"], name="Benchmark",
                                 marker=dict(color=SERIES[1], cornerradius=4), hovertemplate="%{y:+.1%}"))
        fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
        fig.update_yaxes(tickformat=".0%", title="Rendimento nell'anno")
        st.plotly_chart(_layout(fig, 300), width="stretch")


def _coverage_section(d: DashboardData, m: dict):
    cov = d.backtest_coverage()
    if cov.empty or "avg_coverage" not in m:
        return
    with panel("Copertura dell'universo",
               right=f"media {fmt(m.get('avg_coverage'), 'pct')} · minimo {fmt(m.get('min_coverage'), 'pct')}"):
        share = cov["with_price"] / cov["members"]
        fig = go.Figure(go.Scatter(x=cov["date"], y=share, mode="lines", name="Copertura",
                                   line=dict(color=SERIES[0], width=2), hovertemplate="%{y:.1%}"))
        fig.update_yaxes(tickformat=".0%", title="Membri dell'indice con un prezzo")
        st.plotly_chart(_layout(fig, 220, legend=False), width="stretch")
        notice("I titoli senza prezzo sono ex membri delistati che Yahoo non ha più (in genere aziende acquisite o "
               "fallite): è il survivorship bias che resta. Per eliminarlo servono i prezzi dei delistati "
               "(fornitore a pagamento).", "warn", title="Survivorship bias:")


# ---------------------------------------------------------------- depositi SEC (usata dalla pagina Qualità)
FILING_STATUS = {"caricato": "✓ caricato", "da caricare": "● da caricare", "non nei dati SEC": "✗ non nei dati SEC"}


def filings_section(d: DashboardData):
    filings = d.recent_filings(14)
    if filings.empty:
        with panel("Bilanci depositati alla SEC", right="ultimi 14 giorni"):
            empty_state("Nessun 10-Q o 10-K registrato negli ultimi 14 giorni",
                        "L'indice della SEC si controlla a ogni aggiornamento dei dati (menu Dati in alto a destra) "
                        "e con l'aggiornamento automatico serale.")
        return
    counts = filings["status"].value_counts()
    right = (f"ultimi 14 giorni · {counts.get('caricato', 0)} caricati · {counts.get('da caricare', 0)} da caricare"
             f" · {counts.get('non nei dati SEC', 0)} non nei dati SEC")
    with panel("Bilanci depositati alla SEC", right=right):
        view = filings.assign(name=[d.info(t).get("name") or "" for t in filings["ticker"]],
                              status=filings["status"].map(lambda s: FILING_STATUS.get(s, s)))
        dense_table(view, {
            "filed": ("Depositato", "date_short"), "ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"),
            "form": ("Documento", "text"), "status": ("Stato", "text"), "loaded_at": ("Caricato il", "date_short"),
            "accession": ("Numero SEC", "text"),
        }, max_rows=60)
        st.caption("I dati XBRL arrivano poco dopo il deposito: \"da caricare\" si riprova all'aggiornamento "
                   "successivo. \"Non nei dati SEC\" capita soprattutto ai documenti depositati insieme da "
                   "capogruppo e controllate (utility, REIT).")


_filings_section = filings_section  # nome storico
