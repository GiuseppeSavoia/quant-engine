"""Pipeline: le aziende che sto studiando, per fase (Idee -> In studio -> Pronta per decidere -> In portafoglio /
Scartata), con cosa manca per passare alla fase dopo (modules/workflow.py). Le aziende si aggiungono qui o dalla
scheda (menu "Pipeline")."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import streamlit as st

from modules import workflow as wf
from ui import components as C
from ui.common import data

CSS = """<style>
.qe-pipe-card { border: 1px solid var(--border); border-radius: var(--r-md); background: var(--surface-1);
  box-shadow: var(--shadow); padding: 8px 10px; margin: 0 0 8px 0; font-size: 0.86rem; }
.qe-pipe-card .top { display: flex; justify-content: space-between; gap: 6px; align-items: baseline; }
.qe-pipe-card .nm { color: var(--text-3); font-size: 0.8rem; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.qe-pipe-card .row { display: flex; justify-content: space-between; gap: 6px; margin-top: 2px; }
.qe-pipe-card .miss { color: var(--warn); font-size: 0.78rem; margin-top: 3px; }
.qe-pipe-card .ok { color: var(--pos); font-size: 0.78rem; margin-top: 3px; }
.qe-pipe-card .note { color: var(--text-3); font-size: 0.78rem; margin-top: 3px; }
.qe-pipe-head { font-weight: 600; margin-bottom: 4px; }
</style>"""


def card_html(row: dict, currency: str = "") -> str:
    """Una scheda della pipeline: ticker, giudizio, prezzo e margine sul tuo fair value, giorni nella fase, cosa
    manca (o "pronta"), nota."""
    margin = row.get("margin")
    fv = C.fmt_html(margin, "pct", digits=0, signed=True) if margin is not None and np.isfinite(margin) else \
        f'<span class="qe-na">FV {C.NA}</span>'
    days = row.get("days")
    miss = row.get("missing") or []
    status = (f'<div class="miss">manca: {html.escape(", ".join(miss))}</div>' if miss else
              '<div class="ok">✓ pronta per la fase</div>' if row.get("stage") in ("pronta", "portafoglio") else "")
    note = f'<div class="note">{html.escape(str(row["note"]))}</div>' if row.get("note") else ""
    verdict = C.verdict_badge(row["category"], row.get("verdict")) if row.get("category") else ""
    return (f'<div class="qe-pipe-card"><div class="top">{C.ticker_link(row["ticker"])}{verdict}</div>'
            f'<div class="nm" title="{html.escape(row.get("name") or "")}">{html.escape(row.get("name") or "")}</div>'
            f'<div class="row"><span>{C.fmt(row.get("price"), "price")} {html.escape(currency)}</span>'
            f'<span title="margine del tuo fair value sul prezzo">{fv}</span>'
            f'<span title="giorni in questa fase">{"" if days is None else f"{days} g"}</span></div>'
            f"{status}{note}</div>")


def _move_form(d, board: dict):
    with st.expander("Aggiungi o sposta un'azienda", expanded=board["count"] == 0):
        c = st.columns([2, 1.6, 3, 1])
        t = c[0].selectbox("Azienda", d.tickers, index=None, placeholder="Ticker o nome", key="pipe_t",
                           format_func=lambda x: f"{x} · {d.info(x).get('name') or ''}")
        cur = next((c["stage"] for st_ in board["stages"] for c in st_["cards"] if c["ticker"] == t), None)
        keys = [k for k, _ in wf.STAGES]
        stage = c[1].selectbox("Fase", keys, index=keys.index(cur) if cur in keys else 0, key="pipe_s",
                               format_func=wf.STAGE_LABELS.get)
        note = c[2].text_input("Nota (es. perché la scarto, da dove viene l'idea)", key="pipe_n")
        c[3].write("")
        if c[3].button("Salva", key="pipe_save", type="primary", disabled=not t):
            d.set_pipeline_stage(t, stage, note)
            st.session_state["qe_flash"] = f"{t}: {wf.STAGE_LABELS[stage]}."
            st.rerun()
        if t and cur and st.button(f"Togli {t} dalla pipeline", key="pipe_del"):
            d.remove_from_pipeline(t)
            st.rerun()


def page_pipeline():
    d = data()
    st.html(CSS)
    board = d.pipeline_board()
    st.html(C.panel_title_html("Pipeline", "le aziende che sto studiando, dall'idea alla decisione"))
    _move_form(d, board)
    if board["count"] == 0:
        C.empty_state("La pipeline è vuota",
                      "Aggiungi un'idea qui sopra o dalla scheda di un'azienda (menu Pipeline). Per passare a "
                      "«Pronta per decidere» servono tesi in una frase, rischi, cosa ti farebbe cambiare idea, un "
                      "fair value e una valutazione salvata: è il contenuto minimo di un memo d'investimento.")
        return
    cols = st.columns(len(board["stages"]))
    for col, stage in zip(cols, board["stages"]):
        with col:
            st.html(f'<div class="qe-pipe-head">{html.escape(stage["label"])} · {len(stage["cards"])}</div>'
                    + "".join(card_html(r, r.get("currency") or "") for r in stage["cards"]))
    st.caption("Fatto quando: «Pronta per decidere» = tesi in una frase, rischi, cosa farebbe cambiare idea (scheda → "
               "La mia ricerca), fair value e una valutazione salvata nel diario (scheda → Valutazione); «In "
               "portafoglio» anche la data di revisione. Lo storico degli spostamenti resta salvato.")
    if board["history"]:
        with st.expander("Storico degli spostamenti"):
            show = pd.DataFrame(board["history"])[["ticker", "label", "note", "at"]]
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={"ticker": "Ticker", "label": "Fase", "note": "Nota",
                                        "at": st.column_config.DatetimeColumn("Quando", format="DD/MM/YYYY HH:mm")})
