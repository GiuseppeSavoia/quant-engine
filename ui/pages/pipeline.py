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
.qe-pipe-card { border: 1px solid var(--line, #333); padding: 6px 8px; margin: 0 0 6px 0; font-size: 0.86rem; }
.qe-pipe-card .top { display: flex; justify-content: space-between; gap: 6px; align-items: baseline; }
.qe-pipe-card .nm { color: var(--text-muted, #999); font-size: 0.8rem; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.qe-pipe-card .row { display: flex; justify-content: space-between; gap: 6px; margin-top: 2px; }
.qe-pipe-card .miss { color: var(--warn, #c90); font-size: 0.78rem; margin-top: 3px; }
.qe-pipe-card .ok { color: var(--pos, #3a3); font-size: 0.78rem; margin-top: 3px; }
.qe-pipe-card .note { color: var(--text-muted, #999); font-size: 0.78rem; margin-top: 3px; }
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


def board(d, pipe: pd.DataFrame, verdicts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una riga per azienda della pipeline con prezzo, margine sul fair value, giudizio, giorni e cosa manca."""
    rows = []
    notes = d.db.load_notes()
    fair = pd.to_numeric(notes.set_index("ticker")["fair_value"], errors="coerce").to_dict() if not notes.empty else {}
    for r in pipe.itertuples(index=False):
        t = r.ticker
        px = d.prices(t) if t in d.tickers else pd.DataFrame()
        price = float(px["close"].iloc[-1]) if not px.empty else np.nan
        fv = fair.get(t)
        fv = None if fv is None or pd.isna(fv) else float(fv)
        try:
            has_val = not d.valuation_diary(t).empty
        except Exception:  # noqa: BLE001 - azienda senza diario
            has_val = False
        done = wf.completeness(d.research(t), fv, has_val)
        rows.append({"ticker": t, "name": d.info(t).get("name") or "", "stage": r.stage, "note": r.note,
                     "price": price, "currency": d.currency(t),
                     "margin": fv / price - 1 if fv and np.isfinite(price) and price > 0 else np.nan,
                     "days": wf.days_in_stage(r.updated_at), "missing": wf.missing_for(r.stage, done),
                     "category": verdicts.at[t, "category"] if verdicts is not None and t in verdicts.index else None,
                     "verdict": verdicts.at[t, "verdict"] if verdicts is not None and t in verdicts.index else None})
    return pd.DataFrame(rows)


def _move_form(d, pipe: pd.DataFrame):
    with st.expander("Aggiungi o sposta un'azienda", expanded=pipe.empty):
        c = st.columns([2, 1.6, 3, 1])
        t = c[0].selectbox("Azienda", d.tickers, index=None, placeholder="Ticker o nome", key="pipe_t",
                           format_func=lambda x: f"{x} · {d.info(x).get('name') or ''}")
        cur = pipe.set_index("ticker")["stage"].get(t) if t and not pipe.empty else None
        keys = [k for k, _ in wf.STAGES]
        stage = c[1].selectbox("Fase", keys, index=keys.index(cur) if cur in keys else 0, key="pipe_s",
                               format_func=wf.STAGE_LABELS.get)
        note = c[2].text_input("Nota (es. perché la scarto, da dove viene l'idea)", key="pipe_n")
        c[3].write("")
        if c[3].button("Salva", key="pipe_save", type="primary", disabled=not t):
            d.db.set_pipeline_stage(t, stage, note.strip() or None)
            st.session_state["qe_flash"] = f"{t}: {wf.STAGE_LABELS[stage]}."
            st.rerun()
        if t and cur and st.button(f"Togli {t} dalla pipeline", key="pipe_del"):
            d.db.remove_from_pipeline(t)
            st.rerun()


def page_pipeline():
    d = data()
    st.html(CSS)
    pipe = d.db.load_pipeline()
    st.html(C.panel_title_html("Pipeline", "le aziende che sto studiando, dall'idea alla decisione"))
    _move_form(d, pipe)
    if pipe.empty:
        C.empty_state("La pipeline è vuota",
                      "Aggiungi un'idea qui sopra o dalla scheda di un'azienda (menu Pipeline). Per passare a "
                      "«Pronta per decidere» servono tesi in una frase, rischi, cosa ti farebbe cambiare idea, un "
                      "fair value e una valutazione salvata: è il contenuto minimo di un memo d'investimento.")
        return
    try:
        from ui.rules import verdict_table
        verdicts = verdict_table(d, [t for t in pipe["ticker"] if t in d.tickers])
    except Exception:  # noqa: BLE001 - senza giudizi la pipeline resta utile
        verdicts = None
    table = board(d, pipe, verdicts)
    cols = st.columns(len(wf.STAGES))
    for col, (key, label) in zip(cols, wf.STAGES):
        part = table[table["stage"] == key]
        with col:
            st.html(f'<div class="qe-pipe-head">{html.escape(label)} · {len(part)}</div>'
                    + "".join(card_html(r, r.get("currency") or "") for r in part.to_dict("records")))
    st.caption("Fatto quando: «Pronta per decidere» = tesi in una frase, rischi, cosa farebbe cambiare idea (scheda → "
               "La mia ricerca), fair value e una valutazione salvata nel diario (scheda → Valutazione); «In "
               "portafoglio» anche la data di revisione. Lo storico degli spostamenti resta salvato.")
    log = d.db.load_pipeline_log()
    if not log.empty:
        with st.expander("Storico degli spostamenti"):
            show = log.sort_values("at", ascending=False).head(100).assign(
                stage=lambda x: x["stage"].map(lambda s: wf.STAGE_LABELS.get(s, s)))
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={"ticker": "Ticker", "stage": "Fase", "note": "Nota",
                                        "at": st.column_config.DatetimeColumn("Quando", format="DD/MM/YYYY HH:mm")})
