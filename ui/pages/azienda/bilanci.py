"""Scheda azienda, funzioni Bilanci (FA) e Trimestri (ERN), più l'archivio di tutte le voci SEC
(docs/revisione_grafica.md §1 B5, B8, B9).

Periodi leggibili ("2024", "set 2024", "12 mesi a giu 25", "giu 25"), numeri in italiano (`ui.components.fmt`),
assi onesti (le azioni in circolazione partono da zero e la variazione è scritta), stati vuoti con `empty_state`.
`_bars(fin, x, series, height)` è usata anche dalla funzione Dividendi di scheda.py: firma invariata.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard_data import DashboardData, FINANCIAL_ROWS, RATIO_ROWS
from ui.common import SERIES, _layout
from ui.components import NA, dense_table, empty_state, fmt
from modules.services.statements import (MONTHS, PCT_ROWS, SOURCE_MARKS, month_label,  # noqa: F401
                                         shares_summary)
from modules.services.statements import period_labels as _period_labels

TABLE_PERIODS = 12
VIEWS = ["Esercizi", "Trimestri singoli", "Ultimi 12 mesi a ogni trimestre"]


def _bars(fin: pd.DataFrame, x: list[str], series: list[tuple[str, str]], height: int = 330):
    fig = go.Figure()
    for i, (col, name) in enumerate(series):
        if col in fin and fin[col].notna().any():
            fig.add_trace(go.Bar(x=x, y=fin[col] / 1e9, name=name, marker=dict(color=SERIES[i], cornerradius=4),
                                 hovertemplate="%{y:,.2f} mld"))
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
    fig.update_yaxes(title="Miliardi", rangemode="tozero")
    fig.update_xaxes(type="category")
    return _layout(fig, height)


def _shares_chart(fin: pd.DataFrame, x: list[str]):
    sh = fin["shares"]
    fig = go.Figure(go.Bar(x=x, y=sh / 1e6, name="Azioni", marker=dict(color=SERIES[0], cornerradius=4),
                           hovertemplate="%{y:,.1f} mln<extra></extra>"))
    fig.update_xaxes(type="category")
    fig.update_yaxes(rangemode="tozero", title="Milioni")
    summary = shares_summary(sh, x)
    st.markdown("**Azioni in circolazione** (asse da zero)" + (f": {summary}" if summary else ""))
    st.plotly_chart(_layout(fig, 240, legend=False), width="stretch")
    st.caption("Scendono con i riacquisti, salgono con aumenti di capitale e piani azionari. L'asse parte da zero: "
               "un calo dell'1% resta un calo dell'1%.")


def _unit_label(label: str, unit: str) -> str:
    """"Azioni (diluite)" -> "Azioni (diluite, mln)"; "Azioni" -> "Azioni (mln)"."""
    return label[:-1] + f", {unit})" if label.endswith(")") else f"{label} ({unit})"


def financial_table(fin: pd.DataFrame, x: list[str]) -> pd.DataFrame:
    """Voci per periodo già formattate in italiano: importi in milioni con separatori, percentuali, debito/patrimonio
    come multiplo, azioni in milioni; "–" per i mancanti."""
    table = fin.drop(columns="ttm", errors="ignore").T
    table.columns = x
    rows = [r for r in list(FINANCIAL_ROWS) + list(RATIO_ROWS) if r in table.index]
    labels = {**FINANCIAL_ROWS, **RATIO_ROWS}

    def cell(metric, v):
        v = pd.to_numeric(v, errors="coerce")
        if pd.isna(v) or not np.isfinite(v):
            return NA
        if metric in PCT_ROWS:
            return fmt(v, "pct")
        if metric == "debt_to_equity":
            return fmt(v, "mult", digits=2)
        if metric == "shares":
            return fmt(v / 1e6, "num", digits=1)
        return fmt(v / 1e6, "int")

    return pd.DataFrame({c: [cell(r, table.loc[r, c]) for r in rows] for c in table.columns},
                        index=[_unit_label(labels[r], "mln") if r == "shares" else labels[r] for r in rows])


def source_marks(fin: pd.DataFrame, sources: pd.DataFrame, x: list[str]) -> pd.DataFrame:
    """Per ogni cella di `financial_table` la chiave di SOURCE_MARKS ("" = fonte ufficiale o valore derivato)."""
    rows = [r for r in list(FINANCIAL_ROWS) + list(RATIO_ROWS) if r in fin.columns]
    labels = {**FINANCIAL_ROWS, **RATIO_ROWS}
    src = sources.reindex(index=fin.index) if not sources.empty else pd.DataFrame(index=fin.index)

    def mark(r, day):
        if r not in src.columns:
            return ""
        v = str(src.at[day, r]) if pd.notna(src.at[day, r]) else ""
        return next((k for k in SOURCE_MARKS if v.startswith(k)), "")

    return pd.DataFrame({c: [mark(r, day) for r in rows] for c, day in zip(x, fin.index)},
                        index=[_unit_label(labels[r], "mln") if r == "shares" else labels[r] for r in rows])


def financial_table_html(shown: pd.DataFrame, first: str = "Voce", marks: pd.DataFrame | None = None,
                         links: dict | None = None) -> str:
    """La tabella delle voci come tabella fitta HTML: voce a sinistra, numeri allineati a destra, "–" grigio; si
    scorre in orizzontale se i periodi non ci stanno. `marks` (stessa forma, chiavi di SOURCE_MARKS): le celle che non
    vengono da un deposito ufficiale hanno un simbolo e la spiegazione al passaggio del mouse."""
    import html as _h

    def td(v, m):
        if v == NA:
            return f'<td class="na">{NA}</td>'
        if m in SOURCE_MARKS:
            sym, cls, why = SOURCE_MARKS[m]
            return (f'<td class="n {cls}" title="{_h.escape(why)}">{_h.escape(str(v))}'
                    f'<span class="qe-src">{_h.escape(sym)}</span></td>')
        return f'<td class="n">{_h.escape(str(v))}</td>'

    def th(c):
        url, tip = (links or {}).get(c, (None, str(c)))
        text = _h.escape(str(c))
        if url:  # la colonna porta al deposito da cui vengono i numeri
            text = f'<a href="{_h.escape(url)}" target="_blank" rel="noopener">{text} ↗</a>'
        return f'<th class="n" title="{_h.escape(tip)}">{text}</th>'

    head = f'<th title="{_h.escape(first)}">{_h.escape(first)}</th>' + "".join(th(c) for c in shown.columns)
    body = []
    for label, row in shown.iterrows():
        mrow = marks.loc[label] if marks is not None and label in marks.index else None
        cells = "".join(td(v, "" if mrow is None else mrow.get(c, "")) for c, v in row.items())
        body.append(f'<tr><td class="t" title="{_h.escape(str(label))}">{_h.escape(str(label))}</td>{cells}</tr>')
    width = 200 + 80 * len(shown.columns)
    return (f'<div class="qe-tablewrap"><table class="qe-dense" style="min-width:{width}px">'
            f'<colgroup><col style="width:200px">{"<col>" * len(shown.columns)}</colgroup>'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def _financials_tab(d: DashboardData, ticker: str, view: str = "Esercizi"):
    view = st.segmented_control("Periodo", VIEWS, default=view, key="fin_view" if view == "Esercizi" else "ern_view",
                                label_visibility="collapsed") or view
    quarterly = view != "Esercizi"
    single = view == "Trimestri singoli"
    with st.spinner("Preparo i bilanci…"):
        fin = d.quarterly(ticker) if single else d.financials(ticker, quarterly=quarterly)
    if fin.empty:
        if single:
            empty_state("Nessun trimestre singolo per questa azienda",
                        "I trimestri si ricavano dai 10-Q della SEC al prossimo aggiornamento dei dati (menu Dati in "
                        "alto a destra). Le aziende europee pubblicano solo gli esercizi: guarda \"Esercizi\".")
        else:
            empty_state("Nessun bilancio per questa azienda",
                        "La SEC non ha bilanci per questo ticker (aziende europee senza deposito, o aggiunte da te): "
                        "si possono inserire a mano nella pagina Inserisci dati.")
        _sec_archive(d, ticker)
        return
    fin = fin.tail(16 if quarterly else 11)
    x = _period_labels(fin, quarterly)

    left, right = st.columns(2)
    left.markdown("**Ricavi, utili e cassa**")
    left.plotly_chart(_bars(fin, x, [("revenue", "Ricavi"), ("net_income", "Utile netto"),
                                     ("free_cash_flow", "Free cash flow")]), width="stretch")
    fig = go.Figure()
    for i, (col, name) in enumerate([("gross_margin", "Lordo"), ("operating_margin", "Operativo"),
                                     ("net_margin", "Netto"), ("fcf_margin", "FCF")]):
        if col in fin and fin[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=fin[col], name=name, mode="lines+markers",
                                     line=dict(color=SERIES[i], width=2), marker=dict(size=7),
                                     hovertemplate="%{y:.1%}"))
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(type="category")
    right.markdown("**Margini (% dei ricavi)**")
    right.plotly_chart(_layout(fig, 330), width="stretch")

    left, right = st.columns(2)
    left.markdown("**Debito, cassa e patrimonio**")
    left.plotly_chart(_bars(fin, x, [("total_debt", "Debito"), ("cash", "Cassa"), ("equity", "Patrimonio netto")]),
                      width="stretch")
    right.markdown("**Soldi restituiti agli azionisti**")
    right.plotly_chart(_bars(fin, x, [("dividends_paid", "Dividendi"), ("buybacks", "Riacquisti di azioni")]),
                       width="stretch")
    if "shares" in fin and fin["shares"].notna().any():
        _shares_chart(fin, x)

    cur = d.currency(ticker) if hasattr(d, "currency") else "USD"
    shown = financial_table(fin, x)
    marks = None if single else source_marks(fin, d.financial_sources(ticker, quarterly=quarterly), x)
    if shown.shape[1] > TABLE_PERIODS:  # nella tabella gli ultimi 12 periodi: a 1280 px ci stanno senza scorrere
        shown = shown.iloc[:, -TABLE_PERIODS:]
    st.markdown(f"**Le voci** (milioni di {cur}, salvo dove indicato; ultima versione depositata)")
    by_day = {} if single else d.financial_links(ticker, quarterly=quarterly)
    links = {lbl: by_day.get(day) for lbl, day in zip(x, fin.index)}
    st.html(financial_table_html(shown, marks=marks, links={k: v for k, v in links.items() if v}))
    used = set() if marks is None else set(marks[shown.columns].to_numpy().ravel()) & set(SOURCE_MARKS)
    if used:
        st.caption(" · ".join(f"{SOURCE_MARKS[k][0]} {SOURCE_MARKS[k][2].lower()}" for k in SOURCE_MARKS if k in used)
                   + ". I margini e i rapporti calcolati da questi valori ne ereditano i limiti.")
    st.caption("Ogni colonna è un solo trimestre (dal 10-Q; il quarto = esercizio meno i primi nove mesi); stato "
               "patrimoniale alla chiusura; crescita sullo stesso trimestre dell'anno prima; azioni = media diluita "
               "del trimestre (manca nel quarto). Nella tabella gli ultimi 12 trimestri." if single else
               "Ogni colonna: ultimi 12 mesi alla chiusura del trimestre (dai 10-Q; gli esercizi dai 10-K); crescita "
               "sullo stesso trimestre dell'anno prima. Nella tabella gli ultimi 12 trimestri." if quarterly else
               "Esercizi dai 10-K (il mese è indicato se l'esercizio non chiude a dicembre); \"12 mesi a\": ultimi "
               "quattro trimestri dai 10-Q.")
    _sec_archive(d, ticker)


@st.cache_data(show_spinner=False, max_entries=8)
def _sec_export(_d: DashboardData, ticker: str, key) -> bytes:
    return _d.sec_quarters_wide(ticker).to_csv(index=False).encode("utf-8")


def _sec_archive(d: DashboardData, ticker: str):
    """Ogni voce depositata alla SEC dall'azienda (archivio completo), per trimestre."""
    concepts = d.sec_concepts(ticker)
    title = (f"Tutte le voci depositate alla SEC ({fmt(len(concepts), 'int')})" if not concepts.empty
             else "Tutte le voci depositate alla SEC")
    with st.expander(title):
        if concepts.empty:
            empty_state("Archivio delle voci SEC vuoto per questa azienda",
                        "Si riempie al prossimo aggiornamento dei dati (menu Dati in alto a destra; la prima volta "
                        "qualche minuto). Le aziende europee non depositano alla SEC.")
            return
        st.caption("Ogni valore dei 10-K e 10-Q con le etichette XBRL dell'azienda (anche quelle che l'app non usa). "
                   "Per singolo trimestre: il valore dichiarato o la differenza tra due valori \"da inizio "
                   "esercizio\" (solo importi).")
        text = st.text_input("Cerca una voce", key=f"sec_q_{ticker}", placeholder="es. Revenue, Inventory, Lease")
        view = concepts
        if text:
            t = text.lower()
            view = concepts[concepts["label"].str.lower().str.contains(t, regex=False)
                            | concepts["concept"].str.lower().str.contains(t, regex=False)]
        if view.empty:
            st.html('<div class="qe-empty-line">Nessuna voce trovata: prova con un\'altra parola (in inglese, come '
                    'le etichette XBRL).</div>')
            return
        by_id = concepts.set_index("concept_id")
        cid = st.selectbox("Voce", view["concept_id"], key=f"sec_c_{ticker}", placeholder="Scegli una voce…",
                           format_func=lambda i: (f"{by_id.at[i, 'label']} · {by_id.at[i, 'unit']} "
                                                  f"(fino a {month_label(by_id.at[i, 'last_end'], False)})"))
        row = by_id.loc[cid]
        q = d.sec_quarters(ticker, [cid])
        from modules.sec_quarterly import concept_quarters

        series = concept_quarters(q, row["taxonomy"], row["concept"], row["unit"]).tail(20)
        if series.empty:
            st.html('<div class="qe-empty-line">Nessun valore trimestrale per questa voce.</div>')
        else:
            money = "/" not in row["unit"] and row["unit"] not in ("shares", "pure")
            scale, unit = (1e6, "mln") if money and series["value"].abs().max() >= 1e6 else (1, row["unit"])
            fig = go.Figure(go.Bar(x=[month_label(p) for p in series["period_end"]], y=series["value"] / scale,
                                   marker=dict(color=SERIES[0], cornerradius=4),
                                   hovertemplate="%{y:,.2f} " + unit + "<extra></extra>"))
            fig.update_xaxes(type="category")
            fig.update_yaxes(rangemode="tozero", title=unit)
            st.plotly_chart(_layout(fig, 240, legend=False), width="stretch")
            st.caption(f"{row['concept']} ({row['taxonomy']}), unità {row['unit']}.")
            tab = series.iloc[::-1].assign(value=series["value"].iloc[::-1] / scale)
            dense_table(tab, {"period_end": ("Trimestre al", "date"), "value": (f"Valore ({unit})", "num"),
                              "filed": ("Depositato", "date"), "how": ("Come", "text"),
                              "versions": ("Versioni", "int")}, key_col="period_end")
        st.download_button("Scarica tutte le voci per trimestre (CSV)",
                           _sec_export(d, ticker, (d.run_id, len(concepts))),
                           file_name=f"{ticker}_trimestri_sec.csv", mime="text/csv")
