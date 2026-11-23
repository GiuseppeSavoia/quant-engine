"""Pagina "Qualità dei dati": depositi SEC recenti e controlli dell'ultima analisi (`data_quality.py`)."""
from __future__ import annotations

import streamlit as st

from ui.common import data
from ui.components import badge, dense_table, empty_state, notice, panel, table_height
from ui.pages.backtest import filings_section

SEVERITY = {"errore": ("errore", "neg", "✗"), "avviso": ("avviso", "warn", "!"), "info": ("info", "neutral", "●")}


def _severity_html(v, _row=None) -> str:
    text, tone, sym = SEVERITY.get(str(v), (str(v), "neutral", "●"))
    return badge(text, tone, sym)


def page_quality():
    d = data()
    st.title("Qualità dei dati")
    filings_section(d)
    q = d.quality_issues()
    with panel("Controlli dell'ultima analisi", right=f"{len(q)} segnalazioni" if not q.empty else ""):
        if q.empty:
            if d.run_id:
                notice("Nessun problema registrato nell'ultima analisi.", "pos")
            else:
                empty_state("Nessuna analisi nel database",
                            "I controlli si fanno a ogni aggiornamento dei dati (menu Dati in alto a destra).")
            return
        summary = (q.groupby(["severity", "check"])["ticker"].nunique().rename("n").reset_index()
                   .sort_values(["severity", "n"], ascending=[False, False]))
        dense_table(summary, {"severity": ("Gravità", _severity_html), "check": ("Controllo", "text"),
                              "n": ("Titoli", "int")})
        checks = st.multiselect("Controllo", sorted(q["check"].unique()), placeholder="Tutti i controlli",
                                key="dq_checks")
        view = (q[q["check"].isin(checks)] if checks else q).sort_values(["severity", "check", "ticker"])
        view = view.assign(severity=view["severity"].map(lambda s: f"{SEVERITY.get(s, (s, '', '●'))[2]} {s}"))
        st.dataframe(view[["ticker", "check", "severity", "detail"]], hide_index=True, width="stretch",
                     height=table_height(len(view)),
                     column_config={"ticker": "Ticker", "check": "Controllo", "severity": "Gravità",
                                    "detail": st.column_config.TextColumn("Dettaglio", width="large")})
