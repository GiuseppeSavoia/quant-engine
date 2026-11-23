"""Diario delle decisioni (docs/revisione_grafica.md §3.10): riepilogo in cima, revisioni scadute, tutte le decisioni,
nuova decisione in una finestra con un solo pulsante principale."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from ui import components as C
from ui.common import data

VERDICTS = ["Avevo ragione", "Avevo torto", "Troppo presto"]


@st.dialog("Nuova decisione", width="large")
def _new_decision():
    from modules import journal

    d = data()
    c = st.columns([2, 1.4])
    ticker = c[0].selectbox("Azienda", d.tickers, index=None, placeholder="Scrivi il ticker o il nome",
                            format_func=lambda t: f"{t} · {d.info(t).get('name') or ''}", key="dec_ticker")
    action = c[1].selectbox("Decisione", list(journal.ACTIONS), format_func=journal.ACTIONS.get, key="dec_action")
    reason = st.text_area("Perché", key="dec_reason", height=80,
                          placeholder="La tesi in due righe: cosa ti aspetti e perché")
    change = st.text_input("Cosa mi farebbe cambiare idea", key="dec_change",
                           placeholder="es. margine operativo sotto il 10% per due trimestri")
    with st.expander("Altri dettagli (prezzo, data di revisione)"):
        e = st.columns(2)
        ccy = d.currency(ticker) if ticker else "USD"
        price = e[0].number_input(f"Prezzo ({C.CURRENCY_SYMBOL.get(ccy, ccy)})", min_value=0.0, value=None, step=0.01,
                                  format="%.2f", key="dec_price", placeholder="vuoto = ultima chiusura")
        review = e[1].date_input("Rivedi il", value=(pd.Timestamp.today() + pd.DateOffset(months=6)).date(),
                                 format="DD/MM/YYYY", key="dec_review",
                                 help="Quel giorno la decisione compare in \"Da rivedere\" e in Le mie aziende.")
    if st.button("Salva decisione", type="primary"):
        if not ticker or not (reason or "").strip():
            st.error("Servono l'azienda e il perché.")
            return
        px = d.data.close[ticker].dropna() if ticker in d.data.close else pd.Series(dtype=float)
        d.db.add_decision({"ticker": ticker, "date": pd.Timestamp.today().normalize(), "action": action,
                           "price": price or (float(px.iloc[-1]) if not px.empty else None),
                           "reason": reason.strip(), "change_mind": (change or "").strip() or None,
                           "review_date": pd.Timestamp(review)})
        st.session_state["dec_toast"] = f"Decisione su {ticker} salvata: {journal.ACTIONS[action].lower()}"
        st.rerun()


def page_journal():
    from modules import journal

    d = data()
    if msg := st.session_state.pop("dec_toast", None):
        st.toast(msg, icon=":material/check:")
    head, btn = st.columns([5, 1], vertical_alignment="bottom")
    head.title("Diario delle decisioni")
    if btn.button("+ Decisione", type="primary", width="stretch", key="dec_add"):
        _new_decision()
    out = d.journal_view()
    if out.empty:
        C.empty_state("Il diario è vuoto",
                      "Annota ogni decisione (compro, vendo, non compro, aspetto) con il perché e cosa ti farebbe "
                      "cambiare idea: dopo almeno 3 mesi l'app ti dice se avevi ragione, confrontando il titolo con "
                      "l'indice. Anche il \"perché\" scritto quando aggiungi un acquisto nel Portafoglio arriva qui.")
        return
    h = journal.hit_rate(out)
    due = out[out["review_due"]]
    st.html(C.result_row_html([
        ("Decisioni", C.fmt_html(len(out), "int")),
        ("Giudicabili (3 mesi e più)", C.fmt_html(h["judged"], "int")),
        ("Avevi ragione", C.fmt_html(h["rate"], "pct", digits=0)),
        ("Revisioni scadute", C.fmt_html(len(due), "int"))],
        note=f"Ragione = il titolo ha fatto meglio dell'indice ({_bench(d)}) se avevi comprato o deciso di "
             "aspettare, peggio se avevi venduto o deciso di non comprare. Prima di 3 mesi il confronto dice poco."))
    if not due.empty:
        with C.panel("Da rivedere", right=("1 decisione" if len(due) == 1 else f"{len(due)} decisioni") + " con la data di revisione passata"):
            for r in due.itertuples():
                _review_form(d, r)
    with C.panel("Tutte le decisioni", right="dalla più recente"):
        shown = out.assign(
            azione=out["action"].map(journal.ACTIONS),
            esito=[C.badge("ragione", "pos", "✓") if x is True else C.badge("torto", "neg", "✗") if x is False
                   else C.badge("presto", "off") for x in out["right"]],
            price_txt=[C.fmt(p, "price", currency=d.currency(t)) for p, t in zip(out["price"], out["ticker"])],
            revisione=[review_text(v, n) for v, n in zip(out.get("verdict", [None] * len(out)),
                                                          out.get("review_note", [None] * len(out)))])
        C.dense_table(shown, {"date": ("Data", "date_short"), "ticker": ("Titolo", "ticker"),
                              "azione": ("Decisione", "text"), "price_txt": ("Prezzo", "text"),
                              "ret": ("Da allora", "pct", {"arrow": True}),
                              "excess": ("Contro l'indice", "pp"), "esito": ("Esito", "html"),
                              "reason": ("Perché", "text"), "revisione": ("La tua revisione", "text")},
                      max_rows=200, titles={"reason": "reason", "revisione": "revisione"})
        st.caption("Esito \"presto\": meno di 3 mesi, o prezzi mancanti per il confronto.")


def review_text(verdict, note) -> str:
    """"Troppo presto: il prezzo non è sceso" (vuoto se la decisione non è ancora stata rivista)."""
    parts = [str(x).strip() for x in (verdict, note) if isinstance(x, str) and x.strip()]
    return ": ".join(parts) if parts else C.NA


def _bench(d) -> str:
    return d.cfg.get("my_portfolio", {}).get("benchmark") or d.data.benchmark or "indice"


def _review_form(d, r) -> None:
    from modules import journal

    with st.form(f"rev_{r.id}", border=False):
        st.html(f'<div class="qe-result-title">{C.ticker_link(r.ticker)} · {html.escape(journal.ACTIONS[r.action])} '
                f'il {C.fmt(r.date, "date")} a {C.fmt(r.price, "price", currency=d.currency(r.ticker))}</div>'
                + C.kv_html([("Da allora", C.fmt_html(r.ret, "pct", arrow=True)),
                             ("Indice", C.fmt_html(r.bench_ret, "pct", arrow=True)),
                             ("Differenza", C.fmt_html(r.excess, "pp"))])
                + f'<div class="qe-result-note">Perché: {html.escape(r.reason or C.NA)}<br>Cosa mi farebbe '
                  f'cambiare idea: {html.escape(r.change_mind or C.NA)}</div>')
        c = st.columns([3, 2])
        note = c[0].text_input("Com'è andata e cosa hai imparato", key=f"revn_{r.id}")
        verdict = c[1].radio("Giudizio", VERDICTS, horizontal=True, key=f"revv_{r.id}", index=None)
        if st.form_submit_button("Salva la revisione", type="primary"):
            if verdict is None:  # nessun giudizio scelto in anticipo: "Avevo ragione" preselezionato era un invito
                st.error("Scegli un giudizio: avevo ragione, avevo torto o troppo presto.")
                return
            d.db.review_decision(r.id, note, verdict)
            st.session_state["dec_toast"] = f"Revisione di {r.ticker} salvata"
            st.rerun()
