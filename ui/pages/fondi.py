"""Pagina "Grandi investitori": portafogli dei fondi seguiti dai 13F trimestrali della SEC (`superinvestors.py`)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.common import data
from ui.components import dense_table, empty_state, fmt, notice, num_column, panel, table_height

CHANGE_LABEL = {"nuova": "▲ nuova", "aumentata": "▲ aumentata", "ridotta": "▼ ridotta", "uscita": "✗ uscita",
                "invariata": "■ invariata"}


def page_funds():
    d = data()
    st.title("Grandi investitori")
    pos = d.fund_positions()
    if pos.empty:
        empty_state("Nessun portafoglio 13F scaricato",
                    "Apri il menu Dati in alto a destra, spunta \"Anche insider e grandi investitori\" e premi "
                    "\"Aggiorna dati\" (la prima volta 5-10 minuti). Con i dati simulati della demo non ci sono.",
                    details="Da Terminale: python main.py --insiders. I fondi seguiti sono nella sezione "
                            "superinvestors di config.yaml.")
        return
    notice("Posizioni a fine trimestre in azioni USA, depositate fino a 45 giorni dopo: utili per idee e conferme, "
           "non per copiare (quando le vedi sono già vecchie).", "info", title="13F della SEC:")
    live = pos[pos["change"] != "uscita"]
    _top_section(d, live)
    _fund_section(pos, live)


def _top_section(d, live: pd.DataFrame):
    top = (live.dropna(subset=["ticker"]).groupby("ticker")
           .agg(funds=("fund_cik", "nunique"),
                bought=("change", lambda c: int(c.isin(["nuova", "aumentata"]).sum())),
                value=("value", "sum")).sort_values(["funds", "value"], ascending=False).head(25).reset_index())
    top.insert(1, "name", [d.info(t).get("name") or "" for t in top["ticker"]])
    with panel("Le aziende più presenti nei portafogli", right=f"prime {len(top)} per numero di fondi"):
        dense_table(top, {"ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"),
                          "funds": ("Fondi", "int"), "bought": ("Comprata o aumentata da", "int"),
                          "value": ("Valore complessivo", "money", {"currency": "USD"})},
                    empty="Nessuna posizione con un ticker riconosciuto.")


def _fund_section(pos: pd.DataFrame, live: pd.DataFrame):
    funds = (live.groupby(["fund_cik", "fund_name"]).agg(report=("report_date", "max"), value=("value", "sum"))
             .reset_index().sort_values("value", ascending=False))
    if funds.empty:
        return
    info = funds.set_index("fund_cik")
    st.subheader("Il portafoglio di un fondo")
    choice = st.selectbox("Fondo", funds["fund_cik"], key="funds_choice", placeholder="Scegli un fondo",
                          format_func=lambda c: (f"{info.at[c, 'fund_name']} — al {fmt(info.at[c, 'report'], 'date')}"
                                                 f", {fmt(info.at[c, 'value'], 'money', currency='USD')}"))
    port = pos[pos["fund_cik"] == choice].sort_values("weight", ascending=False)
    view = port.assign(change=port["change"].map(lambda c: CHANGE_LABEL.get(c, c)),
                       value_mln=port["value"] / 1e6, shares_mln=port["shares"] / 1e6)
    st.dataframe(view[["issuer", "ticker", "change", "weight", "value_mln", "shares_mln", "shares_change"]],
                 hide_index=True, width="stretch", height=table_height(len(view)), column_config={
                     "issuer": "Emittente (come nel 13F)", "ticker": "Ticker",
                     "change": "Rispetto al trimestre prima",
                     "weight": num_column("Peso", "pct"),
                     "value_mln": num_column("Valore (mln $)", "num", digits=1),
                     "shares_mln": num_column("Azioni (mln)", "num", digits=2),
                     "shares_change": num_column("Variazione azioni", "pct")})
    st.caption("Il ticker è ricavato dal nome dell'emittente: per le aziende fuori dall'S&P 500 può mancare.")
