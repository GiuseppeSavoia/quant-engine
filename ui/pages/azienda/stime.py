"""Scheda azienda, funzione Stime (EE): consenso degli analisti, multipli sui prossimi 12 mesi, confronto con le
ipotesi del DCF, revisioni nel tempo; scarico da Yahoo e import da CSV (modules/estimates.py). Solo informazione:
le stime non entrano nel giudizio."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import estimates as es
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
                rows = es.parse_csv(up.getvalue().decode("utf-8-sig"))
            except Exception as exc:  # noqa: BLE001 - errore del file: si spiega, non si blocca
                notice(f"CSV non importato: {exc}", "warn")
                return
            n = d.db.save_estimates(rows)
            st.session_state["qe_flash"] = f"{n} stime importate ({', '.join(sorted(set(rows['ticker'])))})."
            st.rerun()


def _estimates_tab(d, ticker: str):
    demo = bool(getattr(_args(), "demo", False))
    est = d.db.load_estimates([ticker])
    left, right = st.columns([3, 1], vertical_alignment="center")
    left.caption("Consenso degli analisti: solo informazione, non entra nel giudizio. Le medie degli analisti sono "
                 "storicamente ottimiste, soprattutto oltre l'anno.")
    if right.button("Scarica da Yahoo", key="ee_fetch", disabled=demo, width="stretch",
                    help="Non disponibile sui dati simulati" if demo else "Stime e prezzi obiettivo di Yahoo"):
        with st.spinner("Scarico le stime…"):
            res = es.refresh(d.db, [ticker], max_age_days=0, max_tickers=1, pause=0)
        st.session_state["qe_flash"] = (f"Stime di {ticker} aggiornate." if res["aggiornate"]
                                        else f"Nessuna stima da Yahoo per {ticker}.")
        st.rerun()
    if est.empty:
        empty_state("Nessuna stima per questa azienda",
                    "Scaricale da Yahoo (pulsante qui sopra) oppure importale da un CSV. Le aziende seguite e nella "
                    "pipeline si aggiornano da sole con i dati serali.")
        _upload(d, ticker)
        return
    m = d.metrics()
    g = lambda k: float(m.at[ticker, k]) if ticker in m.index and k in m.columns else np.nan  # noqa: E731
    px = d.prices(ticker)
    price = float(px["close"].iloc[-1]) if not px.empty else np.nan
    annual = d.data.statements(ticker)
    cur = d.currency(ticker)
    from modules.europe import _yahoo_fx

    est, fx_notes = es.to_currency(est, cur, None if demo else _yahoo_fx)
    for n in fx_notes:
        notice(n[0].upper() + n[1:] + ".", "warn" if "esclusi" in n else "info")
    s = es.summary(est, price, g("market_cap"), g("enterprise_value"), annual.index[-1] if not annual.empty else None)
    st.html(kv_html([
        ("P/E prossimi 12 mesi", fmt_html(s["pe_ntm"], "mult")),
        ("P/E ultimi 12 mesi", fmt_html(g("pe"), "mult")),
        ("EV/Ricavi 12 mesi", fmt_html(s["ev_sales_ntm"], "mult")),
        ("Utile per azione: quest'anno / prossimo", f"{fmt_html(s['eps_growth_fy0'], 'pct', signed=True)} / "
                                                    f"{fmt_html(s['eps_growth_fy1'], 'pct', signed=True)}"),
        ("Ricavi: quest'anno / prossimo", f"{fmt_html(s['revenue_growth_fy0'], 'pct', signed=True)} / "
                                          f"{fmt_html(s['revenue_growth_fy1'], 'pct', signed=True)}"),
        ("PEG (P/E 12m / crescita EPS)", fmt_html(s["peg"], "num", digits=2)),
        (f"Prezzo obiettivo ({cur})", f"{fmt(s['price_target'], 'price')} "
                                      f"{fmt_html(s['upside'], 'pct', digits=0, signed=True)}"),
    ]))
    try:
        setup = d.valuation_setup(ticker)
    except Exception:  # noqa: BLE001
        setup = None
    if setup is not None and np.isfinite(s["revenue_growth_fy0"]):
        base = setup["scenarios"].get("Base") or next(iter(setup["scenarios"].values()))
        cons = np.nanmean([s["revenue_growth_fy0"], s["revenue_growth_fy1"]])
        gap = base.growth - cons
        tone = "warn" if abs(gap) > 0.05 else "info"
        notice(f"Il tuo scenario Base del DCF usa una crescita dei ricavi del {fmt(base.growth, 'pct')} l'anno per 5 "
               f"anni; il consenso si aspetta {fmt(cons, 'pct')} in media nei prossimi due esercizi "
               f"({'più ottimista' if gap > 0 else 'più prudente'} di {fmt(abs(gap), 'pp')}).", tone)
    lat = es.latest(est)
    show = lat[lat["metric"] != "price_target"].assign(
        voce=lambda x: x["metric"].map(es.METRICS), periodo=lambda x: x["period"].map(lambda p: es.PERIODS.get(p, p)))
    big = show["metric"] == "revenue"
    for c in ("avg", "low", "high", "year_ago"):
        show[c] = np.where(big, show[c] / 1e6, show[c])
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
    rev = es.revisions(est)
    if len(rev) > 1:
        fig = go.Figure(go.Scatter(x=rev.index, y=rev.values, mode="lines+markers", line=dict(color=SERIES[0])))
        st.markdown("**Revisioni: utile per azione atteso per l'esercizio prossimo**")
        st.plotly_chart(_layout(fig, 260), width="stretch")
    else:
        st.caption("Le revisioni delle stime si vedranno dopo qualche scarico (uno per giorno).")
    st.caption(f"Stime del {fmt(s['as_of'], 'date')} · fonti: {', '.join(s['sources']) or NA}. Prossimi 12 mesi = "
               "media dell'esercizio in corso e del prossimo pesata per il tempo che manca alla chiusura.")
    _upload(d, ticker)
