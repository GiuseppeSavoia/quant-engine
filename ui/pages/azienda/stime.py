"""Scheda azienda, funzione Stime (EE): consenso degli analisti, multipli sui prossimi 12 mesi, confronto con le
ipotesi del DCF, revisioni nel tempo; scarico da Yahoo e import da CSV. I numeri vengono già calcolati dal servizio
`estimates` (`d.estimates_overview`, modules/services/estimates.py): qui solo la presentazione. Solo informazione: le
stime non entrano nel giudizio."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from ui.common import SERIES, _args, _layout
from ui.components import NA, empty_state, fmt, fmt_html, kv_html, notice

TEMPLATE = ("ticker,metric,period,value,low,high,analysts,source,date\n"
            "MSFT,eps,0y,20.01,19.55,20.63,32,FactSet,2026-11-23\n"
            "MSFT,eps,+1y,24.20,22.90,25.85,33,FactSet,2026-11-23\n"
            "MSFT,revenue,0y,395966000000,,,47,FactSet,2026-11-23\n"
            "MSFT,price_target,12m,636.12,440,870,,FactSet,2026-11-23\n")


def _upload(d, ticker: str):
    with st.expander("Importa stime da CSV (es. esportate da FactSet o Bloomberg)"):
        st.caption("Colonne: ticker, metric (eps, revenue, price_target), period (0y = esercizio in corso, +1y = "
                   "prossimo, 12m per il prezzo obiettivo), value; facoltative low, high, analysts, source, date. "
                   "Importi in unità (non milioni), nella valuta del titolo. A pari data vince il CSV su Yahoo.")
        st.download_button("Scarica il modello CSV", TEMPLATE, file_name="stime_modello.csv", mime="text/csv",
                           key="ee_tpl")
        up = st.file_uploader("CSV delle stime", type=["csv"], key="ee_csv", label_visibility="collapsed")
        if up is not None and st.button("Importa", key="ee_import", type="primary"):
            try:
                n, tickers = d.import_estimates_csv(up.getvalue().decode("utf-8-sig"))
            except Exception as exc:  # noqa: BLE001 - errore del file: si spiega, non si blocca
                notice(f"CSV non importato: {exc}", "warn")
                return
            st.session_state["qe_flash"] = f"{n} stime importate ({', '.join(tickers)})."
            st.rerun()


def _estimates_tab(d, ticker: str):
    demo = bool(getattr(_args(), "demo", False))
    view = d.estimates_overview(ticker, online=not demo)
    left, right = st.columns([3, 1], vertical_alignment="center")
    left.caption("Consenso degli analisti: solo informazione, non entra nel giudizio. Le medie degli analisti sono "
                 "storicamente ottimiste, soprattutto oltre l'anno.")
    if right.button("Scarica da Yahoo", key="ee_fetch", disabled=demo, width="stretch",
                    help="Non disponibile sui dati simulati" if demo else "Stime e prezzi obiettivo di Yahoo"):
        with st.spinner("Scarico le stime…"):
            res = d.refresh_estimates(ticker)
        st.session_state["qe_flash"] = (f"Stime di {ticker} aggiornate." if res["aggiornate"]
                                        else f"Nessuna stima da Yahoo per {ticker}.")
        st.rerun()
    if view["empty"]:
        empty_state("Nessuna stima per questa azienda",
                    "Scaricale da Yahoo (pulsante qui sopra) oppure importale da un CSV. Le aziende seguite e nella "
                    "pipeline si aggiornano da sole con i dati serali.")
        _upload(d, ticker)
        return
    cur, s = view["currency"], view["summary"]
    for n in view["fx_notes"]:
        notice(n[0].upper() + n[1:] + ".", "warn" if "esclusi" in n else "info")
    st.html(kv_html([
        ("P/E prossimi 12 mesi", fmt_html(s["pe_ntm"], "mult")),
        ("P/E ultimi 12 mesi", fmt_html(view["trailing_pe"], "mult")),
        ("EV/Ricavi 12 mesi", fmt_html(s["ev_sales_ntm"], "mult")),
        ("Utile per azione: quest'anno / prossimo", f"{fmt_html(s['eps_growth_fy0'], 'pct', signed=True)} / "
                                                    f"{fmt_html(s['eps_growth_fy1'], 'pct', signed=True)}"),
        ("Ricavi: quest'anno / prossimo", f"{fmt_html(s['revenue_growth_fy0'], 'pct', signed=True)} / "
                                          f"{fmt_html(s['revenue_growth_fy1'], 'pct', signed=True)}"),
        ("PEG (P/E 12m / crescita EPS)", fmt_html(s["peg"], "num", digits=2)),
        (f"Prezzo obiettivo ({cur})", f"{fmt(s['price_target'], 'price')} "
                                      f"{fmt_html(s['upside'], 'pct', digits=0, signed=True)}"),
    ]))
    if (dcf := view["dcf"]) is not None:
        gap = dcf["gap"]
        notice(f"Il tuo scenario Base del DCF usa una crescita dei ricavi del {fmt(dcf['base_growth'], 'pct')} l'anno "
               f"per 5 anni; il consenso si aspetta {fmt(dcf['consensus_growth'], 'pct')} in media nei prossimi due "
               f"esercizi ({'più ottimista' if gap > 0 else 'più prudente'} di {fmt(abs(gap), 'pp')}).",
               "warn" if abs(gap) > 0.05 else "info")
    show = view["table"]
    st.dataframe(show[["voce", "periodo", "avg", "low", "high", "analysts", "year_ago", "growth", "source", "as_of"]],
                 hide_index=True, width="stretch",
                 column_config={"voce": "Voce", "periodo": "Periodo",
                                "avg": st.column_config.NumberColumn("Media (ricavi in mln)", format="localized"),
                                "low": st.column_config.NumberColumn("Minimo", format="localized"),
                                "high": st.column_config.NumberColumn("Massimo", format="localized"),
                                "analysts": st.column_config.NumberColumn("Analisti", format="%d"),
                                "year_ago": st.column_config.NumberColumn("Anno prima", format="localized"),
                                "growth": st.column_config.NumberColumn("Crescita", format="percent"),
                                "source": "Fonte", "as_of": st.column_config.DateColumn("Del", format="DD/MM/YYYY")})
    rev = view["revisions"]
    if len(rev) > 1:
        fig = go.Figure(go.Scatter(x=rev.index, y=rev.values, mode="lines+markers", line=dict(color=SERIES[0])))
        st.markdown("**Revisioni: utile per azione atteso per l'esercizio prossimo**")
        st.plotly_chart(_layout(fig, 260), width="stretch")
    else:
        st.caption("Le revisioni delle stime si vedranno dopo qualche scarico (uno per giorno).")
    st.caption(f"Stime del {fmt(s['as_of'], 'date')} · fonti: {', '.join(s['sources']) or NA}. Prossimi 12 mesi = "
               "media dell'esercizio in corso e del prossimo pesata per il tempo che manca alla chiusura.")
    _upload(d, ticker)
