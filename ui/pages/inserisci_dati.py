"""Inserisci dati (docs/revisione_grafica.md §3.10): voci di bilancio a mano e aziende fuori indice."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import streamlit as st

from modules import manual_data
from modules.dashboard_data import DashboardData
from ui import components as C
from ui.common import data


# ---------------------------------------------------------------- dati inseriti a mano
CURRENCIES = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "SEK", "DKK", "NOK", "HKD", "AUD"]


def page_data_entry():
    d = data()
    st.title("Inserisci dati")
    st.caption("Inserisci le voci di bilancio che vuoi, per qualsiasi periodo: tutto il resto (free cash flow, "
               "margini, multipli, ROIC, punteggi, DCF) lo calcola l'app. Un valore inserito a mano prevale su quello "
               "della SEC per lo stesso periodo e voce.")
    if msg := st.session_state.pop("flash_data", None):
        st.toast(msg, icon=":material/check:")
    tabs = st.tabs(["Dati di bilancio", "Aggiungi un'azienda"])
    with tabs[0]:
        _manual_editor(d)
    with tabs[1]:
        _custom_company_form(d)


def _manual_editor(d: DashboardData):
    labels = {t: f"{t} · {d.info(t).get('name') or ''}" for t in d.tickers}
    current = st.session_state.get("ticker", d.tickers[0] if d.tickers else None)
    ticker = st.selectbox("Azienda", d.tickers, index=d.tickers.index(current) if current in d.tickers else 0,
                          format_func=labels.get, key="de_ticker")
    c1, c2, c3 = st.columns(3)
    kind = c1.radio("Periodo", ["annual", "ttm"], horizontal=True, key="de_kind",
                    format_func={"annual": "Esercizio completo", "ttm": "Ultimi 12 mesi a un trimestre"}.get)
    unit_name = c2.selectbox("Importi in", list(manual_data.UNITS), index=2, key="de_unit")
    share_name = c3.selectbox("Azioni in", list(manual_data.UNITS), index=2, key="de_share_unit")
    unit, share_unit = manual_data.UNITS[unit_name], manual_data.UNITS[share_name]
    currency = d.currency(ticker)
    if currency != "USD":
        C.notice(f"{ticker} è quotata in {currency}: inserisci i bilanci in {currency}, la stessa valuta del prezzo, "
                 "altrimenti capitalizzazione e multipli non tornano.", "info")

    base = d.manual_table(ticker, kind, unit, share_unit)
    extra_key = f"de_extra_{ticker}_{kind}"
    extra = st.session_state.setdefault(extra_key, [])
    for period in extra:
        if period not in base.columns:
            base[period] = np.nan
    base = base[sorted(base.columns)]
    a1, a2 = st.columns([2, 1], vertical_alignment="bottom")
    new_period = a1.date_input("Aggiungi un periodo (data di chiusura)", value=None, key=f"de_new_{ticker}_{kind}",
                               max_value=pd.Timestamp.now().date(), format="DD/MM/YYYY")
    if a2.button("Aggiungi la colonna", disabled=new_period is None, width="stretch"):
        extra.append(pd.Timestamp(new_period).strftime("%Y-%m-%d"))
        st.rerun()
    shown = base.copy()
    shown.index = [manual_data.ITEM_LABELS[k] for k in base.index]
    st.caption(f"Valori in {unit_name} di {currency} (azioni in {share_name}). Già compilati con i dati attuali: "
               "modifica solo le celle che vuoi cambiare o aggiungere. Il capex si può scrivere con o senza segno; "
               "il free cash flow si calcola da solo.")
    edited = st.data_editor(shown, width="stretch", height=35 * (len(shown) + 1) + 3, key=f"de_editor_{ticker}_{kind}",
                            column_config={c: st.column_config.NumberColumn(fmt_period(c), format="localized")
                                           for c in shown.columns})
    edited.index = base.index
    changes = manual_data.changed_cells(base, edited)

    v1, v2 = st.columns([2, 1])
    validity = v1.radio("Da quando valgono", ["oggi", "pubblicazione"], horizontal=True, key="de_validity",
                        format_func={"oggi": "Da oggi (consigliato: nel backtest non li sapevi)",
                                     "pubblicazione": "Dalla pubblicazione originale (correggere la storia)"}.get)
    days = v2.number_input("Giorni dopo la chiusura", 0, 365, 60, key="de_days",
                           disabled=validity == "oggi",
                           help="Quando il dato era pubblico: in genere 40-60 giorni per un trimestrale, 60-90 per "
                                "un annuale.")
    n = sum(len(v) for v in changes.values())
    label = "Nessuna modifica da salvare" if not n else ("Salva 1 valore" if n == 1 else f"Salva {n} valori")
    if st.button(label, type="primary", disabled=not n):
        try:
            saved = d.save_manual(ticker, changes, kind, unit, share_unit, None if validity == "oggi" else int(days))
            st.session_state.pop(extra_key, None)
            st.session_state["flash_data"] = (f"Salvati {saved} valori per {ticker} (compresi i derivati). L'app ha "
                                              "ricaricato i dati: li trovi nella scheda dell'azienda.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    manual = d.db.load_manual_financials(ticker)
    if manual.empty:
        C.empty_state(f"Nessun valore inserito a mano per {ticker}",
                      "Quelli che salvi qui sopra compaiono in questo elenco, da dove puoi anche toglierli.")
        return
    st.html(C.panel_title_html("Valori che hai inserito", C.plural(len(manual), "valore", "valori")))
    view = manual.assign(voce=manual["metric"].map(lambda m: manual_data.ITEM_LABELS.get(m, "Free cash flow (calcolato)")),
                         tipo=manual["source"].map({"manual": "esercizio", "manual_ttm": "12 mesi"}))
    selection = st.dataframe(
        view[["period_end", "tipo", "voce", "value", "filed"]], hide_index=True, width="stretch",
        on_select="rerun", selection_mode="multi-row", key=f"de_manual_{ticker}",
        column_config={"period_end": st.column_config.DateColumn("Chiusura", format="DD/MM/YYYY"),
                       "tipo": "Periodo", "voce": "Voce",
                       "value": st.column_config.NumberColumn("Valore", format="localized"),
                       "filed": st.column_config.DateColumn("Vale dal", format="DD/MM/YYYY")})
    picked = selection.selection.rows if selection else []
    if st.button(("Togli il valore selezionato" if len(picked) == 1 else f"Togli i {len(picked)} valori selezionati")
                 if picked else "Seleziona i valori da togliere",
                 disabled=not picked):
        rows = [tuple(manual.iloc[i][["ticker", "period_end", "metric", "filed"]]) for i in picked]
        d.db.delete_manual_financials(rows)
        st.session_state["flash_data"] = f"Tolti {len(rows)} valori: dove c'era, torna a valere il dato SEC."
        st.rerun()


def _custom_company_form(d: DashboardData):
    st.caption("Per un'azienda fuori dall'S&P 500 serve il simbolo di Yahoo Finance (es. ENI.MI per Eni a Milano, "
               "NESN.SW per Nestlé, ASML.AS per ASML ad Amsterdam, ROKU per un'americana). I prezzi si scaricano con "
               "\"Aggiorna dati\" (menu Dati in alto a destra); per le americane anche i bilanci SEC, per le altre li inserisci tu. Le aziende "
               "aggiunte non entrano nei backtest delle strategie, che seguono la composizione dell'indice.")
    with st.form("custom_company"):
        c1, c2 = st.columns(2)
        symbol = c1.text_input("Simbolo Yahoo", placeholder="es. ENI.MI")
        name = c2.text_input("Nome", placeholder="es. Eni")
        c3, c4 = st.columns(2)
        sectors = sorted({s for s in d.sectors().values() if isinstance(s, str) and s})
        sector = c3.selectbox("Settore", sectors, index=None, placeholder="Scegli il settore")
        currency = c4.selectbox("Valuta del prezzo e dei bilanci", CURRENCIES)
        if st.form_submit_button("Aggiungi l'azienda", type="primary"):
            sym = symbol.strip().upper()
            if not sym or " " in sym:
                st.error("Scrivi il simbolo Yahoo, senza spazi.")
            else:
                d.db.add_custom_company(sym, name.strip() or sym, sector, currency)
                st.session_state["flash_data"] = (f"{sym} aggiunta. Per scaricarne i prezzi: menu \"Dati\" in alto "
                                                  "a destra, \"Aggiorna dati\".")
                st.rerun()
    cc = d.custom_companies
    if cc.empty:
        return
    st.html(C.panel_title_html("Aziende che hai aggiunto", C.plural(len(cc), "azienda", "aziende")))
    pending = st.session_state.get("cc_del")
    for r in cc.itertuples():
        c1, c2, c3 = st.columns([4, 2, 1], vertical_alignment="center")
        has_prices = not d.data.prices(r.ticker).empty
        c1.html(f"{C.ticker_link(r.ticker)} {html.escape(r.name or '')} · {html.escape(r.sector or 'settore –')} · "
                f"{html.escape(r.currency or '')}")
        c2.html(C.badge("prezzi presenti", "pos", "✓") if has_prices else
                C.badge("prezzi da scaricare", "warn", "!", title="Menu Dati in alto a destra, Aggiorna dati"))
        if pending == r.ticker:
            a, b = c3.columns(2)
            if a.button("Sì", key=f"cc_yes_{r.ticker}", type="primary", help=f"Togli {r.ticker} dalle tue aziende"):
                d.db.remove_custom_company(r.ticker)
                st.session_state.pop("cc_del", None)
                st.session_state["flash_data"] = f"{r.ticker} tolta dalle tue aziende"
                st.rerun()
            if b.button("No", key=f"cc_no_{r.ticker}"):
                st.session_state.pop("cc_del", None)
                st.rerun()
        elif c3.button("Togli", key=f"cc_del_{r.ticker}"):
            st.session_state["cc_del"] = r.ticker
            st.rerun()
    if pending in set(cc["ticker"]):
        C.notice(f"Togliere {pending} dalle tue aziende? Non si aggiorneranno più i suoi prezzi; i valori di bilancio che "
                 "hai inserito restano salvati e tornano se la riaggiungi.", "warn",
                 title="Conferma:")


def fmt_period(col: str) -> str:
    """Intestazione di un periodo: "2025-12-31" -> "31/12/2025"."""
    return C.fmt(col, "date") if C.fmt(col, "date") != C.NA else str(col)
