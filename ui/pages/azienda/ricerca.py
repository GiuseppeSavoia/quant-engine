"""Scheda azienda, funzioni Insider e fondi (HDS), La mia ricerca (NOTE) e Strategia automatica (AUTO)
(docs/revisione_grafica.md §1 A1, A7, B1, B5, B10).

- HDS: stati vuoti con `empty_state` (cosa manca, perché, i dettagli tecnici richiusi), numeri in italiano.
- NOTE: un solo modulo con un solo pulsante principale (tesi, storia, rischi, revisione, voti 1-5 compatti, appunti),
  conferma con `st.toast`; sotto, il diario delle valutazioni (sola lettura).
- AUTO: la strategia automatica ORIGINALE (sperimentale), dichiarata come tale: non è il giudizio della scheda (quello
  è il Verdetto in cima). Colonne con nomi leggibili, numeri formattati.
"""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import method
from modules.dashboard_data import DashboardData
from ui.common import SERIES, STATUS, _layout, _line
from ui.components import (NA, badge, empty_state, fmt, fmt_html, fmt_table, notice, num_column, result_row_html,
                           table_height)
from ui.pages.azienda.prezzo import _why_section
from ui.pages.azienda.verdetto import it_text

RICERCA_CSS = """<style>
.qe-auto-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 2px 0 4px; }
.qe-auto-head b { font-size: var(--fs-h2); color: var(--text); font-weight: 600; }
.qe-auto-head span.txt { color: var(--text-2); font-size: var(--fs-data); }
.st-key-qe_checklist [data-testid="stWidgetLabel"] p { font-size: var(--fs-data); }
</style>"""


def _cur(d: DashboardData, ticker: str) -> str:
    try:
        return d.currency(ticker)
    except Exception:  # noqa: BLE001
        return "USD"


# ================================================================== insider e grandi investitori (HDS)
INSIDER_HELP = ("Si scaricano dal menu Dati in alto a destra, con l'opzione \"Anche insider e grandi investitori\" "
                "(la prima volta 5-10 minuti); poi si aggiornano da soli a ogni aggiornamento dei dati.")


def _ownership_tab(d: DashboardData, ticker: str):
    st.html(RICERCA_CSS)
    tx = d.insider_transactions(ticker)
    if tx.empty and d.data.holdings.empty:
        empty_state("Nessun dato su insider e grandi investitori", INSIDER_HELP,
                    details="Da terminale: .venv/bin/python main.py --insiders")
        return
    cur = _cur(d, ticker)
    st.subheader("Insider")
    st.caption("Amministratori, dirigenti e soci oltre il 10% dichiarano ogni operazione entro due giorni (Form 4). "
               "Conta soprattutto chi COMPRA con i propri soldi, meglio se in più persone; le vendite dicono poco, "
               "quelle programmate (piani 10b5-1) ancora meno.")
    sm = d.insider_summary(ticker)

    def buyers(n):
        return f'{fmt_html(n, "int")} <span class="qe-na">insider</span>'

    st.html(result_row_html([
        ("Acquisti 90 giorni", fmt_html(sm["90"]["buy_value"], "money", currency=cur)),
        ("Chi ha comprato", buyers(sm["90"]["buyers"])),
        ("Vendite non progr. 90 g", fmt_html(sm["90"]["sell_value"], "money", currency=cur)),
        ("Acquisti 12 mesi", fmt_html(sm["365"]["buy_value"], "money", currency=cur)),
        ("Chi ha comprato", buyers(sm["365"]["buyers"])),
        ("Vendite non progr. 12 mesi", fmt_html(sm["365"]["sell_value"], "money", currency=cur)),
    ], note=f"In più {fmt(sm['365'].get('planned_sell_value'), 'money', currency=cur)} di vendite programmate "
            "(10b5-1) negli ultimi 12 mesi."))
    if tx.empty:
        empty_state("Nessuna operazione di insider in archivio",
                    "Negli archivi trimestrali SEC scaricati non ci sono Form 4 per questa azienda.")
    else:
        px = d.data.prices(ticker)
        px = px[px.index >= pd.Timestamp.now() - pd.DateOffset(years=3)]
        fig = go.Figure()
        _line(fig, px.index, px, "Prezzo", SERIES[0])
        for label, color, symbol, cond in (
                ("Acquisto di un insider", STATUS["BUY"], "triangle-up", lambda f: f["code"].eq("P")),
                ("Vendita non programmata", STATUS["BLOCKED"], "triangle-down",
                 lambda f: f["code"].eq("S") & ~f["planned"].astype(bool))):
            ev = tx[cond(tx)].groupby("trans_date", as_index=False)["value"].sum()
            if ev.empty:
                continue
            y = px.reindex(ev["trans_date"], method="nearest") if not px.empty else ev["value"] * 0
            size = 8 + 18 * np.sqrt(ev["value"] / max(ev["value"].max(), 1))
            fig.add_trace(go.Scatter(x=ev["trans_date"], y=y.to_numpy(), mode="markers", name=label,
                                     marker=dict(color=color, symbol=symbol, size=size, line=dict(color="white", width=1)),
                                     customdata=ev["value"],
                                     hovertemplate="%{x|%d/%m/%Y}: %{customdata:,.0f}<extra></extra>"))
        st.plotly_chart(_layout(fig, 300), width="stretch")
        st.caption("La grandezza dei triangoli è proporzionale al valore dell'operazione.")
        only = st.segmented_control("Mostra", ["Acquisti e vendite sul mercato", "Tutte le operazioni"],
                                    default="Acquisti e vendite sul mercato", key=f"ins_filter_{ticker}",
                                    label_visibility="collapsed") or "Acquisti e vendite sul mercato"
        view = tx[tx["code"].isin(["P", "S"])] if only.startswith("Acquisti") else tx
        view = view.assign(title=view["title"].fillna(""))
        cols = ["filed", "trans_date", "owner", "title", "relationship", "kind", "shares", "price", "value", "planned",
                "owned_after"]
        shown = view[[c for c in cols if c in view]].head(300)
        if shown.empty:
            st.html('<div class="qe-empty-line">Nessun acquisto o vendita sul mercato: solo assegnazioni, esercizi di '
                    'opzioni e simili (scegli "Tutte le operazioni").</div>')
        else:
            st.dataframe(shown, hide_index=True, width="stretch", height=table_height(len(shown), 12),
                         column_config={
                             "filed": st.column_config.DateColumn("Depositato", format="DD/MM/YYYY"),
                             "trans_date": st.column_config.DateColumn("Operazione", format="DD/MM/YYYY"),
                             "owner": "Chi", "title": "Ruolo", "relationship": "Relazione", "kind": "Tipo",
                             "shares": num_column("Azioni", "int"),
                             "price": num_column(f"Prezzo ({cur})", "price"),
                             "value": num_column(f"Valore ({cur})", "int"),
                             "planned": st.column_config.CheckboxColumn("Programmata (10b5-1)"),
                             "owned_after": num_column("Azioni dopo", "int")})
    st.subheader("Grandi investitori")
    holders = d.fund_holders(ticker)
    if holders.empty:
        if d.data.holdings.empty:
            empty_state("Nessun dato sui fondi", INSIDER_HELP)
        else:
            empty_state("Nessuno dei fondi seguiti possiede questa azienda",
                        "Nell'ultimo 13F di ciascuno dei fondi seguiti non compare.",
                        details="Elenco dei fondi: config.yaml, sezione superinvestors")
        return
    st.caption("Dall'ultimo 13F di ogni fondo seguito: posizioni a fine trimestre, depositate fino a 45 giorni dopo. "
               "Cosa hanno fatto dopo non si sa.")
    view = holders[["fund_name", "report_date", "change", "shares", "shares_change", "value", "weight"]]
    st.dataframe(view, hide_index=True, width="stretch", height=table_height(len(view)), column_config={
        "fund_name": "Fondo", "report_date": st.column_config.DateColumn("Al", format="DD/MM/YYYY"),
        "change": "Rispetto al trimestre prima",
        "shares": num_column("Azioni", "int"),
        "shares_change": num_column("Variazione azioni", "pct"),
        "value": num_column("Valore ($)", "int"),
        "weight": num_column("Peso nel fondo", "pct", digits=2)})


# ================================================================== la mia ricerca (NOTE)
def checklist_scores(raw: dict) -> dict[int, float | None]:
    """Voti dai controlli (1-5 o None = non votato) in float."""
    return {int(k): (float(v) if v is not None else None) for k, v in raw.items()}


def _research_tab(d: DashboardData, ticker: str, note: dict):
    st.html(RICERCA_CSS)
    if msg := st.session_state.pop(f"res_saved_{ticker}", None):
        st.toast(msg, icon=":material/check:")
    r = d.research(ticker)
    items = d.checklist_items()
    current = d.checklist(ticker)
    head, pdf = st.columns([4, 1], vertical_alignment="bottom")
    head.subheader("La mia ricerca")
    try:
        from modules.research_pdf import company_pdf

        pdf.download_button("Esporta in PDF", data=company_pdf(d, ticker), width="stretch",
                            file_name=f"{ticker}_scheda_{pd.Timestamp.now():%Y-%m-%d}.pdf", mime="application/pdf")
    except ImportError:
        pdf.caption("PDF non disponibile (manca il pacchetto fpdf2)")
    st.caption("La tua tesi, i voti che i numeri non danno e gli appunti. Si salva tutto insieme.")

    with st.form(f"research_{ticker}", border=False):
        one_liner = st.text_input("La tesi in una frase", r.get("one_liner") or "",
                                  placeholder="es. Leader con margini stabili, prezzata come se dovesse smettere di "
                                              "crescere")
        story = st.text_area("La storia: che azienda sarà tra 10 anni", r.get("story") or "", height=90,
                             placeholder="Mercato, vantaggio, come guadagna, cosa cambia nei prossimi anni…")
        c1, c2 = st.columns(2)
        catalysts = c1.text_area("Cosa deve succedere perché io abbia ragione", r.get("catalysts") or "", height=90)
        risks = c2.text_area("Rischi", r.get("risks") or "", height=90)
        c3, c4 = st.columns([3, 1])
        change_mind = c3.text_area("Cosa mi farebbe cambiare idea", r.get("change_mind") or "", height=68)
        default_review = pd.Timestamp(r["review_date"]).date() if r.get("review_date") else \
            (pd.Timestamp.now() + pd.Timedelta(days=90)).date()
        review = c4.date_input("Prossima revisione", default_review, format="DD/MM/YYYY",
                               help="Da quel giorno la home ti ricorda di rivedere la tesi (avviso REV).")

        new_scores: dict[int, float | None] = {}
        st.markdown("**Il mio giudizio** (voti 1-5; clicca di nuovo un voto per toglierlo)")
        if items.empty:
            st.html('<div class="qe-empty-line">La checklist è vuota: le domande si aggiungono nella pagina Il mio '
                    'metodo.</div>')
        else:
            with st.container(key="qe_checklist"):
                cols = st.columns(2, gap="medium")
                for n, (_, item) in enumerate(items.iterrows()):
                    i = int(item["id"])
                    value = current.get(i)
                    choice = cols[n % 2].segmented_control(
                        item["question"], [1, 2, 3, 4, 5], default=int(value) if value else None,
                        key=f"chk_{ticker}_{i}", help=item["help"] or None)
                    new_scores[i] = None if choice is None else float(choice)
            st.caption("Domande e pesi si cambiano in Il mio metodo. Il voto compare accanto alle metriche, ma non nei "
                       "backtest: nel passato non esisteva.")
        thesis = st.text_area("Appunti liberi", value=note.get("thesis", "") or "", height=120,
                              key=f"thesis_{ticker}", placeholder="Quello che non sta nei campi sopra…")
        saved = st.form_submit_button("Salva la mia ricerca", type="primary")
    if saved:
        d.save_research(ticker, {"one_liner": one_liner, "story": story, "catalysts": catalysts, "risks": risks,
                                 "change_mind": change_mind, "review_date": str(review)})
        if not items.empty and new_scores != checklist_scores({k: current.get(k) for k in new_scores}):
            d.save_checklist(ticker, new_scores)
        if thesis != (note.get("thesis") or ""):
            d.save_note(ticker, thesis=thesis)
        st.session_state[f"res_saved_{ticker}"] = f"Ricerca su {ticker} salvata."
        st.rerun()

    score = (method.checklist_score({k: v for k, v in current.items() if v is not None}, items)
             if not items.empty else None)
    fv = note.get("fair_value")
    a = note.get("assumptions") or {}
    cur = _cur(d, ticker)
    fv_txt = fmt_html(fv, "price", currency=cur)
    if fv is not None and a:
        fv_txt += (f' <span class="qe-na">(FCF {fmt(a.get("fcf"), "money")}, crescita {fmt(a.get("growth"), "pct")}, '
                   f'WACC {fmt(a.get("wacc"), "pct")}, {fmt(a.get("years"), "int")} anni)</span>')
    when = [x for x in (r.get("updated_at"), note.get("updated_at")) if x]
    st.html(result_row_html([
        ("Il tuo voto", f"{fmt_html(score, 'num', digits=1)} / 5" if score is not None else fmt_html(None)),
        ("Il tuo fair value", fv_txt),
        ("Ultima modifica", fmt_html(max(pd.Timestamp(x) for x in when), "date") if when else fmt_html(None)),
    ]))
    st.divider()
    _diary_section(d, ticker)


def _diary_section(d: DashboardData, ticker: str):
    st.subheader("Diario delle valutazioni")
    diary = d.valuation_diary(ticker)
    if diary.empty:
        empty_state("Ancora nessuna valutazione salvata",
                    "Si salvano dalla funzione Valutazione (DCF). Col tempo il diario ti dice se le tue stime erano "
                    "troppo ottimiste o troppo pessimiste.")
        return
    cur = _cur(d, ticker)
    px = d.data.prices(ticker)
    px = px[px.index >= diary["created_at"].min() - pd.Timedelta(days=90)]
    fig = go.Figure()
    _line(fig, px.index, px, "Prezzo", SERIES[0])
    fig.add_trace(go.Scatter(x=diary["created_at"], y=diary["expected_value"], mode="markers", name="Il tuo valore",
                             marker=dict(color=SERIES[1], size=11, line=dict(color="white", width=2)),
                             hovertemplate="%{x|%d/%m/%Y}: %{y:,.2f}<extra></extra>"))
    st.plotly_chart(_layout(fig, 280), width="stretch")
    view = diary.sort_values("created_at", ascending=False)
    view = view.assign(toward=view["toward_value"].map(lambda b: "✓ sì" if b else "✗ no"))
    st.dataframe(view[["created_at", "price", "expected_value", "margin_then", "return_since", "toward", "note"]],
                 hide_index=True, width="stretch", height=table_height(len(view)), column_config={
                     "created_at": st.column_config.DatetimeColumn("Data", format="DD/MM/YYYY"),
                     "price": num_column(f"Prezzo allora ({cur})", "price"),
                     "expected_value": num_column(f"Il tuo valore ({cur})", "price"),
                     "margin_then": num_column("Margine allora", "pct"),
                     "return_since": num_column("Prezzo da allora", "pct"),
                     "toward": st.column_config.TextColumn("Verso il tuo valore?",
                                                           help="Il prezzo si è mosso nella direzione che indicava "
                                                                "la tua valutazione"),
                     "note": "Nota"})
    if len(diary) > 1:
        st.caption(f"Il prezzo si è mosso verso il tuo valore in {int(diary['toward_value'].sum())} valutazioni su "
                   f"{len(diary)}. Con poche valutazioni e pochi mesi è ancora presto per giudicare.")


# compatibilità: la vecchia funzione NOTE separata (ora dentro il modulo unico di _research_tab)
def _notes_tab(d: DashboardData, ticker: str, note: dict):
    _research_tab(d, ticker, note)


# ================================================================== strategia automatica (AUTO)
DECISION_COLUMNS = {
    "as_of": ("Data", "date"),
    "action": ("Decisione", lambda a: {"BUY": "▲ BUY", "SKIP": "● SKIP", "BLOCKED": "■ BLOCKED"}.get(a, a)),
    "confidence": ("Confidenza", "score"),
    "threshold": ("Soglia", "score"),
    "regime": ("Regime", "text"),
    "price": ("Prezzo", "price"),
    "fair_value": ("Fair value DCF", "price"),
    "margin_of_safety": ("Margine DCF", "pct", {"signed": True}),
    "reasoning": ("Motivo", lambda t: it_text(str(t))),
}


def decision_history_view(hist: pd.DataFrame) -> pd.DataFrame:
    """Storico delle decisioni della strategia automatica con nomi di colonna leggibili e numeri in italiano."""
    if hist is None or hist.empty:
        return pd.DataFrame(columns=[v[0] for v in DECISION_COLUMNS.values()])
    return fmt_table(hist, DECISION_COLUMNS)


VERSION_COLUMNS = {"period_end": ("Periodo al", "date"), "metric": ("Voce", "text"), "value": ("Valore", "num"),
                   "filed": ("Depositato", "date"), "form": ("Documento", "text"), "source": ("Fonte", "text"),
                   "accn": ("Numero del deposito", "text")}


def _quality_notices(d: DashboardData, ticker: str):
    q = d.quality_for(ticker)
    for r in q.itertuples():
        notice(f"{r.check}: {it_text(str(r.detail))}", "neg" if r.severity == "errore" else "warn",
               title="Dati da controllare:")


def _strategy_tab(d: DashboardData, ticker: str):
    st.html(RICERCA_CSS)
    st.html('<div class="qe-auto-head"><b>Strategia automatica originale</b>'
            + badge("sperimentale", "warn", "!")
            + '<span class="txt">non è il giudizio della scheda: quello è il Verdetto in cima</span></div>')
    st.caption("Cosa direbbe la strategia automatica del progetto (regole e soglie in config.yaml) all'ultima analisi. "
               "È uno strumento di ricerca, non un consiglio: nei backtest ha reso meno dell'indice (pagina "
               "Backtest). BUY/SKIP qui sotto riguardano solo questa strategia.")
    _quality_notices(d, ticker)
    _why_section(d, ticker)
    res = d.valuation(ticker)
    val, fs = res["valuation"], res["factors"]
    cur = _cur(d, ticker)
    st.html(result_row_html([
        ("Fair value del sistema", fmt_html(val.fair_value, "price", currency=cur)),
        ("Margine DCF", fmt_html(val.margin, "pct", arrow=True)),
        ("Punteggio multi-factor", fmt_html(fs.total, "score")),
        ("Crescita usata", fmt_html(val.growth, "pct")),
    ], title="Il DCF e i fattori della strategia", note=" · ".join(it_text(n) for n in val.notes)))
    st.markdown("**Storico delle decisioni della strategia**")
    hist = d.decision_history(ticker)
    if hist.empty:
        empty_state("Nessuna decisione registrata", "Il titolo non è mai entrato in un'analisi della strategia.")
    else:
        shown = decision_history_view(hist)
        st.dataframe(shown, hide_index=True, width="stretch", height=table_height(len(shown), 12),
                     column_config={"Motivo": st.column_config.TextColumn("Motivo", width="large")})
    with st.expander("Versioni depositate dei bilanci"):
        st.caption("Ogni valore con la sua data di deposito alla SEC. Un 10-K ripete gli anni precedenti, a volte "
                   "corretti: il backtest a ogni data usa solo le versioni già depositate.")
        v = d.financial_versions(ticker)
        if v.empty:
            empty_state("Nessuna versione in archivio", "Per questa azienda non ci sono bilanci depositati.")
        else:
            cols = {c: s for c, s in VERSION_COLUMNS.items() if c in v}
            st.dataframe(fmt_table(v, cols), hide_index=True, width="stretch", height=420)
            st.caption(f"{fmt(len(v), 'int')} valori. Gli importi sono nella valuta del bilancio, non scalati.")
