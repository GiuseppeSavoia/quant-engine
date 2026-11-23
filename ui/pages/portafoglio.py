"""Portafoglio inserito a mano (docs/revisione_grafica.md §3.10, §7.4): riga di risultato in cima, posizioni con il
giudizio, esposizione vera dentro gli ETF, sovrapposizioni, operazioni e dividendi/tasse stimate.

Le composizioni degli ETF si scaricano da Yahoo SOLO col pulsante "Aggiorna composizioni" o quando la copia in cache
è scaduta (DashboardData.portfolio_look_through): mai a ogni apertura della pagina."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui import components as C
from ui.common import MUTED, SERIES, _layout, _line, data

TAX_NOTE = "stima, non è consulenza fiscale"
PORT_TABS = ["Posizioni", "Esposizione vera", "Sovrapposizioni ETF", "Operazioni", "Dividendi e tasse"]
from modules.services.portfolio import CURRENCIES, SUFFIX_CCY, guess_currency, held_quantity  # noqa: E402,F401


# ---------------------------------------------------------------- dati
@st.cache_data(ttl=300, show_spinner=False)
def _live_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Ultimo prezzo dei titoli non ancora nel database (es. un ETF appena inserito): entra nell'aggiornamento serale."""
    from modules import quotes

    try:
        return {k: float(v) for k, v in quotes.quote_table(quotes.download_closes(list(tickers)))["last"].items()}
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _verdicts(_d, tickers: tuple[str, ...], key: int) -> dict[str, tuple[str, str]]:  # noqa: ARG001 - key = dati
    """Giudizio (categoria, etichetta) delle azioni del portafoglio, in cache per i dati caricati."""
    from modules import verdict

    if not tickers:
        return {}
    try:
        table = verdict.assess_many(_d, list(tickers))
    except Exception:  # noqa: BLE001 - senza giudizio la pagina funziona lo stesso
        return {}
    return {t: (r["category"], r["verdict"]) for t, r in table.iterrows()}


def _trim(v, min_digits: int, max_digits: int = 4) -> str:
    """Numero in italiano con i decimali che servono (10 -> "10", 14,5 -> "14,50" con min_digits=2)."""
    text = C.fmt(v, "num", digits=max_digits)
    if "," not in text:
        return text
    whole, dec = text.split(",")
    dec = dec.rstrip("0").ljust(min_digits, "0")
    return f"{whole},{dec}" if dec else whole


def _held_quantity(tx: pd.DataFrame, ticker: str, day) -> float:
    return held_quantity(tx, ticker, day)


def _guess_currency(symbol: str) -> str:
    return guess_currency(symbol)


# ---------------------------------------------------------------- nuova operazione
@st.dialog("Nuova operazione", width="large")
def _new_transaction():
    from modules import portfolio as pf

    d = data()
    c = st.columns([1.6, 1.2, 1.2])
    ticker = c[0].text_input("Simbolo Yahoo", key="tx_ticker", placeholder="AAPL, ENI.MI, VWCE.DE",
                             help="Il simbolo come su Yahoo Finance: il suffisso è la borsa (.MI Milano, .DE Xetra, "
                                  ".AS Amsterdam). Nessun suffisso = borsa americana.").strip().upper()
    kind = c[1].selectbox("Tipo", list(pf.KINDS), format_func=pf.KINDS.get, key="tx_kind")
    day = c[2].date_input("Data", value=pd.Timestamp.today().date(), format="DD/MM/YYYY", key="tx_day",
                          max_value=pd.Timestamp.today().date())
    c = st.columns([1.2, 1.2, 1, 1.4])
    qty = c[0].number_input("Quantità (azioni)" if kind == "DIV" else "Quantità", min_value=0.0, value=None,
                            step=1.0, format="%.4f", key="tx_qty", placeholder="es. 10")
    guess = _guess_currency(ticker)
    currency = c[2].selectbox("Valuta", CURRENCIES, index=CURRENCIES.index(guess), key=f"tx_ccy_{guess}",
                              help="Valuta in cui è quotato il titolo su quella borsa (proposta dal suffisso).")
    price = c[1].number_input(f"Dividendo per azione ({currency})" if kind == "DIV" else f"Prezzo ({currency})",
                              min_value=0.0, value=None, step=0.01, format="%.4f", key="tx_price",
                              placeholder="es. 182,50")
    fx = None
    if currency != "EUR":
        fx = c[3].number_input(f"Cambio: {currency} per 1 €", min_value=0.0, value=None, step=0.0001, format="%.4f",
                               key="tx_fx", placeholder="vuoto = chiusura Yahoo",
                               help=f"Quanti {currency} valeva un euro quel giorno, come sull'estratto del broker "
                                    f"(es. 1,0850). Vuoto: il cambio di chiusura di Yahoo di quel giorno.")
    else:
        c[3].caption("In euro: nessun cambio.")
    with st.expander("Altri dettagli (commissioni, nota, perché)"):
        e = st.columns([1, 3])
        fees = e[0].number_input(f"Commissioni ({currency})", min_value=0.0, value=None, step=0.5, format="%.2f",
                                 key="tx_fees", placeholder="0,00")
        note = e[1].text_input("Nota", key="tx_note", placeholder="facoltativa")
        reason = st.text_area("Perché (va nel diario delle decisioni)", key="tx_reason", height=68,
                              placeholder="La tesi in due righe: cosa ti aspetti e perché",
                              disabled=kind == "DIV")
    verb = {"BUY": "Aggiungi acquisto", "SELL": "Aggiungi vendita", "DIV": "Aggiungi dividendo"}[kind]
    if st.button(verb, type="primary"):
        try:  # stessi controlli dell'interfaccia web (servizio portfolio.add_transaction)
            tx_id = d.add_transaction(ticker, kind, day, qty or 0.0, price or 0.0, currency, fx or None, fees or 0.0,
                                      note or None, reason if kind != "DIV" else None)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state["port_toast"] = (f"{pf.KINDS[kind]} di {_trim(qty, 0)} "
                                          f"{ticker} aggiunt" + ("a" if kind == "SELL" else "o"))
        st.session_state["port_new_id"] = tx_id
        st.rerun()


# ---------------------------------------------------------------- modifica di un'operazione
EDIT_FIELDS = {"ticker": "Simbolo", "kind": "Tipo", "date": "Data", "quantity": "Quantità", "price": "Prezzo",
               "currency": "Valuta", "fx": "Cambio per 1 €", "fees": "Commissioni", "note": "Nota"}


def _edit_value(field: str, v) -> str:
    from modules import portfolio as pf

    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "–"
    if field == "kind":
        return pf.KINDS.get(v, v)
    if field == "date":
        return C.fmt(v, "date")
    if field in ("quantity", "price", "fx", "fees"):
        return _trim(float(v), 0 if field == "quantity" else 2)
    return str(v)


def edit_changes(old: dict, new: dict) -> list[str]:
    """Campi cambiati, in chiaro ("Quantità: 10 → 12"), per la conferma."""
    out = []
    for f, label in EDIT_FIELDS.items():
        a, b = _edit_value(f, old.get(f)), _edit_value(f, new.get(f))
        if a != b:
            out.append(f"{label}: {a} → {b}")
    return out


def edit_error(tx: pd.DataFrame, tx_id: int, new: dict) -> str | None:
    """Errore della modifica (campi mancanti, vendite non più coperte), None se va bene."""
    from modules import portfolio as pf

    missing = [n for n, ok in (("simbolo", bool(new["ticker"])), ("quantità", bool(new["quantity"])),
                               ("prezzo", bool(new["price"]))) if not ok]
    if missing:
        return "Mancano: " + ", ".join(missing) + "."
    if " " in new["ticker"]:
        return "Il simbolo non può contenere spazi."
    after = tx.copy()
    idx = after.index[after["id"] == tx_id]
    for k, v in new.items():
        after.loc[idx, k] = v
    after["date"] = pd.to_datetime(after["date"])
    bad = [b for b in pf.oversold(after) if b not in pf.oversold(tx)]  # solo i problemi causati da questa modifica
    if bad:
        b = bad[0]
        return (f"Con questa modifica la vendita del {C.fmt(b['date'], 'date')} di {_trim(b['sold'], 0)} {b['ticker']} "
                f"supera le azioni possedute ({_trim(b['held'], 0)}).")
    return None


@st.dialog("Modifica operazione", width="large")
def _edit_transaction(tx_id: int):
    from modules import portfolio as pf

    d = data()
    tx = d.db.load_transactions()
    row = tx[tx["id"] == tx_id]
    if row.empty:
        st.error("L'operazione non c'è più.")
        return
    r = row.iloc[0]
    k = f"txe_{tx_id}_"
    c = st.columns([1.6, 1.2, 1.2])
    ticker = c[0].text_input("Simbolo Yahoo", value=r["ticker"], key=k + "ticker").strip().upper()
    kind = c[1].selectbox("Tipo", list(pf.KINDS), index=list(pf.KINDS).index(r["kind"]), format_func=pf.KINDS.get,
                          key=k + "kind")
    day = c[2].date_input("Data", value=pd.Timestamp(r["date"]).date(), format="DD/MM/YYYY", key=k + "day",
                          max_value=pd.Timestamp.today().date())
    c = st.columns([1.2, 1.2, 1, 1.4])
    qty = c[0].number_input("Quantità", min_value=0.0, value=float(r["quantity"]), step=1.0, format="%.4f",
                            key=k + "qty")
    currency = c[2].selectbox("Valuta", CURRENCIES, index=CURRENCIES.index(r["currency"])
                              if r["currency"] in CURRENCIES else 0, key=k + "ccy")
    price = c[1].number_input(f"Prezzo ({currency})", min_value=0.0, value=float(r["price"]), step=0.01,
                              format="%.4f", key=k + "price")
    fx = None
    if currency != "EUR":
        fx = c[3].number_input(f"Cambio: {currency} per 1 €", min_value=0.0,
                               value=float(r["fx"]) if pd.notna(r["fx"]) else None, step=0.0001, format="%.4f",
                               key=k + "fx", placeholder="vuoto = chiusura Yahoo")
    e = st.columns([1, 3])
    fees = e[0].number_input(f"Commissioni ({currency})", min_value=0.0,
                             value=float(r["fees"]) if pd.notna(r["fees"]) else 0.0, step=0.5, format="%.2f",
                             key=k + "fees")
    note = e[1].text_input("Nota", value=r["note"] if isinstance(r["note"], str) else "", key=k + "note")
    new = {"ticker": ticker, "kind": kind, "date": pd.Timestamp(day), "quantity": qty, "price": price,
           "currency": currency, "fx": fx or None, "fees": fees or 0.0, "note": note or None}
    old = {f: r[f] for f in EDIT_FIELDS}
    confirm = st.session_state.get("port_edit_confirm")
    if confirm != tx_id:
        if st.button("Salva le modifiche", type="primary", key=k + "save"):
            if err := edit_error(tx, tx_id, new):
                st.error(err)
                return
            if not edit_changes(old, new):
                C.notice("Nessuna modifica da salvare.", "info")
                return
            st.session_state["port_edit_confirm"] = tx_id
            st.rerun(scope="fragment")
        return
    changes = edit_changes(old, new)
    err = edit_error(tx, tx_id, new)
    if err or not changes:
        st.session_state.pop("port_edit_confirm", None)
        if err:
            st.error(err)
        else:
            C.notice("Nessuna modifica da salvare.", "info")
        return
    C.notice("; ".join(changes) + ". Prezzo medio, guadagni e tasse si ricalcolano con i valori nuovi.", "warn",
             title="Confermi la modifica?")
    a, b, _ = st.columns([1, 1, 3])
    if a.button("Sì, salva", type="primary", key=k + "yes"):
        d.db.update_transaction(tx_id, new)
        st.session_state.pop("port_edit_confirm", None)
        st.session_state["port_toast"] = f"Operazione su {ticker} modificata"
        st.rerun()
    if b.button("Annulla", key=k + "no"):
        st.session_state.pop("port_edit_confirm", None)
        st.rerun(scope="fragment")


# ---------------------------------------------------------------- pagina
def page_portfolio():
    d = data()
    if msg := st.session_state.pop("port_toast", None):
        st.toast(msg, icon=":material/check:")
    head, btn = st.columns([5, 1], vertical_alignment="bottom")
    head.title("Portafoglio")
    if btn.button("+ Operazione", type="primary", width="stretch", key="port_add"):
        _new_transaction()
    tx = d.db.load_transactions()
    if tx.empty:
        C.empty_state("Il portafoglio è vuoto",
                      "Inserisci acquisti, vendite e dividendi con \"+ Operazione\": l'app calcola valore, guadagno "
                      "in euro (cambio compreso), confronto con l'indice, tasse stimate e cosa c'è dentro i tuoi ETF. "
                      "Tutto resta solo sul tuo Mac.")
        return
    from modules import portfolio as pf

    fx_needed = {pf.fx_symbol(c) for c in tx["currency"].dropna().unique()} - {None}
    missing = tuple(sorted({t for t in tx["ticker"].unique() if t not in d.data.close}
                           | {s for s in fx_needed if s not in d.data.close or d.data.close[s].isna().all()}))
    view = d.portfolio_view(_live_prices(missing) if missing else None)
    pos = view.get("positions", pd.DataFrame())
    _summary(d, view, pos)
    look = None
    if not pos.empty:  # dalla cache: si scarica solo col pulsante o se la copia è scaduta
        if st.session_state.pop("etf_refresh", False):
            with st.spinner("Scarico le composizioni degli ETF da Yahoo…", show_time=True):
                look = d.portfolio_look_through(pos, refresh=True)
            st.toast("Composizioni degli ETF aggiornate" if not look["failed"] else
                     f"Yahoo non ha risposto per {', '.join(look['failed'])}", icon=":material/check:")
        else:
            look = d.portfolio_look_through(pos)
        names = {t: c.name for t, c in look["compositions"].items() if c.name}
        pos = pos.assign(name=[n or names.get(t, "") for t, n in zip(pos["ticker"], pos["name"])])
    # scheda aperta in session_state (etichette fisse): dopo un'azione (elimina, modifica, aggiorna composizioni)
    # la pagina resta sulla scheda in cui si era, invece di tornare a Posizioni
    tabs = st.tabs(PORT_TABS, key="port_tab", on_change="rerun")
    with tabs[0]:
        _positions(d, view, pos, set(look["fetched"]) if look else set())
    with tabs[1]:
        _exposure(d, pos, look)
    with tabs[2]:
        _overlaps(look)
    with tabs[3]:
        _transactions(d, tx)
    with tabs[4]:
        _dividends_taxes(view)


def _summary(d, view: dict, pos: pd.DataFrame) -> None:
    if pos.empty:
        C.empty_state("Nessuna posizione aperta", "Le operazioni inserite sono tutte vendite o dividendi: vedi le "
                      "schede Operazioni e Dividendi e tasse.")
        return
    value, cost = pos["value_eur"].sum(min_count=1), pos["cost_eur"].sum(min_count=1)
    complete = pos["value_eur"].notna().all()
    gain = value - cost if complete else np.nan
    realized = view["realized"]["gain_eur"].sum() if not view["realized"].empty else 0.0
    div = view["dividends"]
    h = view.get("history", pd.DataFrame())
    vs = np.nan
    if complete and not h.empty and "benchmark" in h:
        last = h.dropna(subset=["invested", "benchmark"]).tail(1)
        if not last.empty and last["invested"].iloc[0] > 0:  # valore di oggi (anche prezzi appena scaricati)
            vs = (value - last["benchmark"].iloc[0]) / last["invested"].iloc[0]
    cost_complete = pos["cost_eur"].notna().all()
    if not cost_complete:  # un costo in euro mancante (cambio del giorno ignoto): niente totali parziali
        cost, gain, vs = np.nan, np.nan, np.nan
    items = [("Valore", C.fmt_html(value if complete else np.nan, "money", currency="EUR")),
             ("Investito", C.fmt_html(cost, "money", currency="EUR")),
             ("Guadagno", C.fmt_html(gain, "money", currency="EUR", arrow=True)),
             ("Guadagno %", C.fmt_html(gain / cost if cost else np.nan, "pct", arrow=True)),
             (f"Contro {view['benchmark']}", C.fmt_html(vs, "pp")),
             ("Realizzato", C.fmt_html(realized, "money", currency="EUR", signed=True)),
             ("Dividendi netti", C.fmt_html(div["net_eur"].sum() if not div.empty else 0.0, "money", currency="EUR"))]
    days = [d.data.close[t].last_valid_index() for t in pos["ticker"] if t in d.data.close]
    days = [x for x in days if x is not None]
    live = sorted(t for t in pos["ticker"] if t not in d.data.close)
    when = f"chiusure al {C.fmt(min(days), 'date_short')}" if days else "ultime chiusure"
    if live:
        when += f"; per {', '.join(live)} la quotazione di oggi, non ancora nel database"
    note = (f"In euro: costo al cambio del giorno di ogni operazione, valore al cambio di oggi ({when}). Contro {view['benchmark']}: gli stessi euro investiti "
            "nell'indice negli stessi giorni, differenza in punti sul capitale investito.")
    st.html(C.result_row_html(items, note=note))
    if view.get("missing_prices"):
        C.notice(f"Senza prezzo: {', '.join(view['missing_prices'])}. Il simbolo Yahoo è giusto? Il prezzo arriva "
                 "con il prossimo aggiornamento dei dati; fino ad allora valore e guadagno totali restano vuoti.",
                 "warn", title="Prezzi mancanti:")
    mfx = view.get("missing_fx")
    if mfx is not None and not mfx.empty:
        which = ", ".join(f"{r.ticker} del {C.fmt(r.date, 'date')}" for r in mfx.itertuples(index=False))
        C.notice(f"{which}: manca il cambio di quel giorno ({', '.join(sorted(set(mfx['currency'])))} per 1 €), quindi "
                 "il costo in euro e i totali restano vuoti. Attendi il prossimo aggiornamento dei dati (scarica lo "
                 "storico dei cambi) oppure, nella scheda Operazioni, elimina l'operazione e reinseriscila con il "
                 "cambio dell'estratto del broker.",
                 "warn", title="Cambio dell'operazione mancante:")
    no_fx = sorted(pos.loc[pos["value_eur"].isna() & pos["price"].notna(), "ticker"])
    if no_fx:
        ccys = sorted(set(pos.loc[pos["ticker"].isin(no_fx), "currency"]))
        C.notice(f"{', '.join(no_fx)}: manca il cambio di oggi ({', '.join(ccys)} per 1 €) nel database, quindi "
                 "il valore in euro resta vuoto. Arriva con il prossimo aggiornamento dei dati.", "warn",
                 title="Cambio mancante:")


def _positions(d, view: dict, pos: pd.DataFrame, etfs: set[str]) -> None:
    if pos.empty:
        C.empty_state("Nessuna posizione aperta")
        return
    stocks = tuple(sorted(t for t in pos["ticker"] if t in set(d.tickers)))
    with st.spinner("Calcolo i giudizi…"):
        verdicts = _verdicts(d, stocks, id(d))
    known = set(d.tickers)
    shown = pos.sort_values("value_eur", ascending=False, na_position="last").assign(
        verdetto=lambda x: [C.verdict_badge(*verdicts[t]) if t in verdicts else
                            (C.badge("ETF", "info") if t in etfs else C.NA) for t in x["ticker"]])

    def ticker_cell(v, row):
        return C.ticker_link(v) if v in known else html.escape(str(v))

    def money_ccy(col):
        return lambda v, row: C.fmt_html(v, "price", currency=row.get("currency"))

    with C.panel("Posizioni", right=f"{C.plural(len(pos), 'titolo', 'titoli')} · valori in euro"):
        C.dense_table(shown, {
            "ticker": ("Titolo", ticker_cell), "name": ("Nome", "text"), "quantity": ("Quantità", "num", {"digits": 2}),
            "avg_cost": ("Costo medio", money_ccy("avg_cost")), "price": ("Prezzo", money_ccy("price")),
            "value_eur": ("Valore €", "num", {"digits": 0}), "weight": ("Peso", "pct"),
            "gain_eur": ("Guadagno €", "num", {"digits": 0, "signed": True}),
            "gain_pct": ("Guadagno %", "pct", {"arrow": True}), "verdetto": ("Giudizio", "html")})
        st.caption("Giudizio: quello della scheda dell'azienda (solo per le azioni con dati; non è una "
                   "raccomandazione). Il costo medio comprende le commissioni.")
    value = pos["value_eur"].sum()
    left, right = st.columns([1, 1.6])
    with left, C.panel("Composizione per settore e valuta"):
        if pos["value_eur"].notna().any() and value > 0:
            by = pd.concat([pos.groupby("sector")["value_eur"].sum().rename("valore").to_frame().assign(tipo="Settore"),
                            pos.groupby("currency")["value_eur"].sum().rename("valore").to_frame().assign(tipo="Valuta")])
            fig = go.Figure()
            for i, (kind, g) in enumerate(by.groupby("tipo")):
                g = g.sort_values("valore")
                fig.add_trace(go.Bar(x=g["valore"] / value, y=[f"{kind}: {x}" for x in g.index], orientation="h",
                                     name=kind, marker=dict(color=SERIES[i]), hovertemplate="%{x:.1%}<extra></extra>"))
            fig.update_xaxes(tickformat=".0%")
            st.plotly_chart(_layout(fig, 300, legend=False), width="stretch", config={"displayModeBar": False})
            st.caption("Gli ETF qui contano come \"Altro\": per i settori dentro gli ETF vedi Esposizione vera.")
        else:
            C.empty_state("Composizione non disponibile", "Serve il valore in euro di almeno una posizione.")
    h = view["history"]
    with right, C.panel("Andamento in euro", right=f"contro {view['benchmark']}: stessi euro, stessi giorni"):
        no_hist = sorted(t for t in pos["ticker"] if t not in d.data.close or not d.data.close[t].notna().any())
        if no_hist:
            C.empty_state("Andamento non ancora disponibile",
                          f"Manca lo storico dei prezzi di {', '.join(no_hist)} nel database: arriva con il prossimo "
                          "aggiornamento dei dati. Senza, il grafico mostrerebbe un valore più basso di quello vero.")
        elif h.empty or not h["value"].notna().any():
            C.empty_state("Andamento non disponibile", "Mancano prezzi o cambi per i giorni delle operazioni.")
        else:
            fig = go.Figure()
            _line(fig, h.index, h["value"], "Il portafoglio", SERIES[0])
            _line(fig, h.index, h["invested"], "Soldi investiti", MUTED, dash="dot", width=1.5)
            if "benchmark" in h:
                _line(fig, h.index, h["benchmark"], f"Se avessi comprato {view['benchmark']}", SERIES[1])
            fig.update_traces(hovertemplate="%{y:,.0f} €")
            st.plotly_chart(_layout(fig, 300), width="stretch", config={"displayModeBar": False})


# ---------------------------------------------------------------- esposizione vera (dentro gli ETF)
def _exposure(d, pos: pd.DataFrame, look: dict | None) -> None:
    if pos.empty or look is None:
        C.empty_state("Nessuna posizione aperta")
        return
    status = look["status"]
    if status == "no_etf":
        C.empty_state("Nessun ETF nel portafoglio",
                      "Le tue posizioni sono tutte azioni: l'esposizione vera coincide con la tabella Posizioni.")
        return
    if status == "empty":
        if C.empty_state("Composizioni degli ETF non ancora scaricate",
                         f"\"Aggiorna composizioni\" scarica da Yahoo, per {', '.join(look['to_download'])}, le prime "
                         "10 posizioni e i settori dell'intero fondo (qualche secondo per ETF). Poi l'app le rilegge "
                         "dal Mac e le riscarica da sola solo quando sono più vecchie di 30 giorni.",
                         "Aggiorna composizioni", key="etf_refresh_empty"):
            st.session_state["etf_refresh"] = True
            st.rerun()
        return
    lt, s = look["look"], look["look"]["summary"]
    dates = sorted({v for v in look["fetched"].values() if v})
    when = f"composizioni Yahoo del {C.fmt(dates[0], 'date_short')}" if dates else "composizioni Yahoo"
    if len(dates) > 1:
        when = f"composizioni Yahoo dal {C.fmt(dates[0], 'date_short')} al {C.fmt(dates[-1], 'date_short')}"
    top, btn = st.columns([5, 1.3], vertical_alignment="center")
    top.html(C.kv_html([
        ("Totale", C.fmt_html(s["total_eur"], "money", currency="EUR")),
        ("Azioni dirette", C.fmt_html(s["direct_eur"], "money", currency="EUR")),
        ("In ETF", C.fmt_html(s["etf_eur"], "money", currency="EUR")),
        ("Dettagliato", C.fmt_html(1 - s["undetailed_pct"] if s["total_eur"] else np.nan, "pct")),
        ("Non dettagliato", C.fmt_html(s["undetailed_pct"], "pct") + " · "
         + C.fmt_html(s["undetailed_eur"], "money", currency="EUR"))]))
    if btn.button("Aggiorna composizioni", width="stretch", key="etf_refresh_btn",
                  help="Riscarica da Yahoo le prime 10 posizioni e i settori di ogni ETF del portafoglio."):
        st.session_state["etf_refresh"] = True
        st.rerun()
    if look["failed"]:
        C.notice(f"Yahoo non ha risposto per {', '.join(look['failed'])}: uso la copia precedente, se c'è.", "warn",
                 title="Scarico non riuscito:")
    if look["to_download"]:
        C.notice(f"{', '.join(look['to_download'])}: composizione non ancora scaricata, conta tutto come non "
                 "dettagliato. Premi \"Aggiorna composizioni\".", "info")
    if s["missing_value"]:
        C.notice(f"{', '.join(s['missing_value'])}: senza valore in euro (prezzo o cambio mancante), esclusi da "
                 "questi conti.", "warn")
    for dup in lt["possible_duplicates"]:
        C.notice(f"{dup['symbol']} ({dup['name'] or 'nome assente'}) e {dup['candidate']} "
                 f"({dup['candidate_name'] or 'nome assente'}): non unite per prudenza. Se sono la stessa azienda, "
                 "aggiungi l'alias nella sezione etf di config.yaml.", "info", title="Forse la stessa azienda:")
    aliases = _applied_aliases(d, pos, look)
    if aliases:
        st.caption("Stessa azienda con un altro simbolo, già unite: " + " · ".join(f"{a} = {b}" for a, b in aliases))

    left, right = st.columns([1.5, 1])
    with left, C.panel("Per azienda · almeno", right=f"{when} · limite inferiore"):
        _company_table(d, lt)
    with right, C.panel("Per settore · completo", right="settori dell'intero fondo"):
        _sector_chart(lt)
    _concentration(look)
    with C.panel("ETF nel portafoglio", right=when):
        _funds_table(look)



def _applied_aliases(d, pos: pd.DataFrame, look: dict) -> list[tuple[str, str]]:
    from modules import etf

    aliases = {k.upper(): v.upper() for k, v in etf.settings(d.cfg)["aliases"].items()}
    seen = set(pos["ticker"])
    for c in look["compositions"].values():
        seen |= set(c.holdings["symbol"])
    return sorted((s, aliases[s.upper()]) for s in seen if s.upper() in aliases)


def _company_table(d, lt: dict) -> None:
    by, s = lt["by_company"], lt["summary"]
    known = set(d.tickers)
    syms = lt["companies"].groupby("company")["symbol"].apply(list).to_dict()
    comp = lt["companies"]
    src = {c: (g["sources"].iloc[0] if len(g) == 1 else
               " · ".join(f"{sym}: {so}" for sym, so in zip(g["symbol"], g["sources"])))
           for c, g in comp.groupby("company")}
    rows = by.head(25).assign(
        sym=lambda x: [next((t for t in syms.get(c, []) if t in known), (syms.get(c) or [""])[0]) for c in x["company"]],
        name=lambda x: [n or c for n, c in zip(x["name"], x["company"])],
        sources=lambda x: [src.get(c, "") for c in x["company"]])
    under = pd.DataFrame([{"sym": "", "name": f"░ {_undetailed_label()}", "total_eur": s["undetailed_eur"],
                           "weight": s["undetailed_pct"], "sources": "ETF oltre le prime 10 posizioni"}])
    table = pd.concat([rows, under], ignore_index=True)

    def sym_cell(v, row):
        return C.ticker_link(v) if v in known else html.escape(str(v or ""))

    C.dense_table(table, {"sym": ("Titolo", sym_cell), "name": ("Azienda", "text"),
                          "total_eur": ("Almeno €", "num", {"digits": 0}), "weight": ("Almeno %", "pct"),
                          "sources": ("Da dove", "text")}, max_rows=30, titles={"sources": "sources"})
    more = len(by) - 25
    st.caption(("Le cifre sono un minimo: degli ETF si conoscono solo le prime 10 posizioni, quindi una stessa "
                "azienda può pesare di più nella parte non dettagliata. Classi diverse della stessa azienda "
                "(GOOGL e GOOG) sono sommate.") + (f" Altre {more} aziende non mostrate." if more > 0 else ""))


def _undetailed_label() -> str:
    from modules import etf

    return etf.UNDETAILED


def _sector_chart(lt: dict) -> None:
    from modules import etf

    sec = lt["sectors"].sort_values("total_eur")
    if sec.empty:
        C.empty_state("Settori non disponibili")
        return
    special = {etf.UNCLASSIFIED, etf.BONDS, etf.CASH, etf.OTHER}
    fig = go.Figure()
    for label, part, color in (("Azioni dirette", "direct_eur", SERIES[0]), ("Dentro gli ETF", "etf_eur", SERIES[1])):
        fig.add_trace(go.Bar(
            x=sec[part] / lt["summary"]["total_eur"], y=sec["sector"], orientation="h", name=label,
            marker=dict(color=color, pattern=dict(shape=["/" if s in special else "" for s in sec["sector"]])),
            hovertemplate="%{y}: %{x:.1%}<extra>" + label + "</extra>"))
    fig.update_layout(barmode="stack")
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(_layout(fig, max(260, 26 * len(sec) + 60)), width="stretch", config={"displayModeBar": False})
    st.caption("Completo: Yahoo dà i settori dell'intero fondo. Tratteggiato: obbligazioni, liquidità e la parte che "
               "Yahoo non classifica (\"Non classificato\"). Settori di Yahoo tradotti in quelli del resto dell'app: "
               "piccole differenze possibili.")


def _concentration(look: dict) -> None:
    c = look["concentration"]
    if not c or c["top"].empty:
        return
    partial = c["partial"]
    n_eq = C.fmt(c["effective_n"], "num", digits=0)
    items = [("Primo nome", C.fmt_html(c["top_weight"], "pct") + f' <span class="qe-na">{html.escape(str(c["top"].iloc[0].get("name") or c["top"].iloc[0].get("company") or ""))}</span>'),
             ("Primi 10", ("almeno " if partial else "") + C.fmt_html(c["top_n_weight"], "pct")),
             ("Aziende equivalenti", ("al più " if partial else "") + html.escape(n_eq))]
    with C.panel("Concentrazione", right="sulle aziende note" if partial else ""):
        st.html(C.kv_html(items))
        if partial:
            st.caption("Aziende equivalenti = quante aziende di peso uguale darebbero la stessa concentrazione. È un "
                       "limite superiore: le aziende della parte non dettagliata (e il peso in più dei nomi già visti) "
                       "possono solo aumentare la concentrazione, quindi il numero vero è al più questo. Per lo "
                       "stesso motivo \"primi 10\" è un minimo.")


def _funds_table(look: dict) -> None:
    lt = look["look"]
    f = lt["funds"].copy()
    if f.empty:
        C.empty_state("Nessun ETF con valore in euro")
        return
    comps = look["compositions"]
    f["name"] = [comps[e].name if e in comps else "" for e in f["etf"]]
    f["fetched"] = [comps[e].fetched if e in comps else None for e in f["etf"]]
    f["state"] = ["da scaricare" if e in look["to_download"] else
                  ("solo settori" if comps[e].holdings.empty and comps[e].sectors else
                   "nessun dettaglio" if comps[e].holdings.empty else "prime 10 + settori") for e in f["etf"]]
    C.dense_table(f, {"etf": ("ETF", "text"), "name": ("Nome", "text"), "value_eur": ("Valore €", "num", {"digits": 0}),
                      "detailed_pct": ("Dettagliato", "pct"), "n_holdings": ("Posizioni note", "int"),
                      "state": ("Cosa si sa", "text"), "fetched": ("Scaricato il", "date_short")})


def _overlaps(look: dict | None) -> None:
    if not look or look["status"] in ("no_positions",):
        C.empty_state("Nessuna posizione aperta")
        return
    if look["status"] == "no_etf":
        C.empty_state("Nessun ETF nel portafoglio", "La sovrapposizione si misura tra due o più ETF.")
        return
    ov = look["overlaps"]
    if not ov:
        C.empty_state("Servono almeno due ETF con la composizione scaricata",
                      "La sovrapposizione confronta le posizioni di due ETF: scarica le composizioni in Esposizione "
                      "vera (\"Aggiorna composizioni\").")
        return
    rows = [{"a": o["a"], "b": o["b"],
             "range": (f"{C.fmt(o['overlap'], 'pct', digits=0)}" if not o["lower_bound"] else
                       f"{C.fmt(o['overlap'], 'pct', digits=0).rstrip('%')}–{C.fmt(o['upper_bound'], 'pct', digits=0)}"),
             "known": o["overlap"], "sectors": o["sector_overlap"],
             "common": ", ".join(o["common"]["symbol"].head(6)) or C.NA} for o in ov]
    with C.panel("Sovrapposizione tra ETF", right="intervallo: si conoscono solo le prime 10 posizioni"):
        C.dense_table(pd.DataFrame(rows), {
            "a": ("ETF A", "text"), "b": ("ETF B", "text"), "range": ("Sovrapposizione", "text"),
            "known": ("Certa (nelle prime 10)", "pct"), "sectors": ("Settori in comune", "pct"),
            "common": ("Aziende in comune note", "text")})
        st.caption("Sovrapposizione = quota dei due fondi investita nelle stesse aziende. La parte certa viene dalle "
                   "posizioni note; il massimo vale se tutto il resto coincidesse. \"Settori in comune\" è "
                   "un'indicazione su tutto il fondo: settori uguali non vuol dire aziende uguali.")
    for o in ov:
        if o["common"].empty:
            continue
        with st.expander(f"{o['a']} e {o['b']}: aziende in comune tra le prime 10"):
            C.dense_table(o["common"], {"symbol": ("Titolo", "text"), "name": ("Azienda", "text"),
                                        "weight_a": (f"Peso in {o['a']}", "pct"),
                                        "weight_b": (f"Peso in {o['b']}", "pct"),
                                        "overlap": ("In comune", "pct")})


# ---------------------------------------------------------------- operazioni
def _transactions(d, tx: pd.DataFrame) -> None:
    from modules import portfolio as pf

    new_id = st.session_state.get("port_new_id")
    view = tx.sort_values(["date", "id"], ascending=False).reset_index(drop=True)
    shown = C.fmt_table(view.assign(
        new=["● nuova" if i == new_id else "" for i in view["id"]],
        tipo=view["kind"].map(pf.KINDS),
        fx=[r if c != "EUR" else np.nan for r, c in zip(view["fx"], view["currency"])]), {
        "new": ("●", "text"), "date": ("Data", "date"), "tipo": ("Tipo", "text"), "ticker": ("Titolo", "text"),
        "quantity": ("Quantità", lambda v: _trim(v, 0)), "price": ("Prezzo", lambda v: _trim(v, 2)),
        "currency": ("Valuta", "text"), "fx": ("Cambio per 1 €", lambda v: _trim(v, 2)),
        "fees": ("Commissioni", "num"), "note": ("Nota", "text")})
    st.caption(f"{C.plural(len(tx), 'operazione', 'operazioni')} inserit{'a' if len(tx) == 1 else 'e'}.")
    sel = st.dataframe(shown, hide_index=True, width="stretch", height=C.table_height(len(shown)),
                       on_select="rerun", selection_mode="single-row", key="port_tx_table")
    st.caption("Cambio vuoto = chiusura di Yahoo del giorno. Seleziona una riga per modificarla o eliminarla.")
    rows = sel.selection.rows if sel else []
    pending = st.session_state.get("port_del")
    if rows and pending is None:
        r = view.iloc[rows[0]]
        e0, e1, e2 = st.columns([4, 1, 1], vertical_alignment="center")
        e0.caption(f"Selezionata: {pf.KINDS[r['kind']].lower()} di {_trim(r['quantity'], 0)} {r['ticker']} del "
                   f"{C.fmt(r['date'], 'date')}")
        if e1.button("Modifica", key="port_edit_ask", width="stretch", icon=":material/edit:"):
            st.session_state.pop("port_edit_confirm", None)
            _edit_transaction(int(r["id"]))
        if e2.button("Elimina", key="port_del_ask", width="stretch", icon=":material/delete:"):
            st.session_state["port_del"] = int(r["id"])
            st.rerun()
    if pending is not None:
        r = tx.loc[tx["id"] == pending]
        if r.empty:
            st.session_state.pop("port_del", None)
            return
        r = r.iloc[0]
        C.notice(f"{pf.KINDS[r['kind']]} di {_trim(r['quantity'], 0)} {r['ticker']} del "
                 f"{C.fmt(r['date'], 'date')}: l'operazione verrà cancellata e non si può recuperare.", "neg",
                 title="Confermi?")
        a, b, _ = st.columns([1, 1, 4])
        if a.button("Sì, elimina", type="primary", key="port_del_yes"):
            d.db.delete_transaction(int(pending))
            st.session_state.pop("port_del", None)
            st.session_state["port_toast"] = f"Operazione su {r['ticker']} eliminata"
            st.rerun()
        if b.button("Annulla", key="port_del_no"):
            st.session_state.pop("port_del", None)
            st.rerun()


# ---------------------------------------------------------------- dividendi e tasse
def _dividends_taxes(view: dict) -> None:
    div, taxes = view.get("dividends", pd.DataFrame()), view.get("taxes")
    a, b = st.columns(2)
    with a, C.panel("Dividendi incassati", right=TAX_NOTE):
        if div is None or div.empty:
            C.empty_state("Nessun dividendo inserito",
                          "Aggiungi un dividendo con \"+ Operazione\", tipo Dividendo: azioni possedute e dividendo "
                          "per azione.")
        else:
            C.dense_table(div.sort_values("date", ascending=False), {
                "date": ("Data", "date_short"), "ticker": ("Titolo", "text"),
                "gross_eur": ("Lordo €", "num"), "us_tax": ("Ritenuta USA €", "num"),
                "it_tax": ("Imposta IT €", "num"), "net_eur": ("Netto €", "num")})
            st.caption("Stima: 15% trattenuto negli USA (con il modulo W-8BEN) e 26% in Italia sul resto.")
    with b, C.panel("Tasse stimate (26%)", right=TAX_NOTE):
        if not taxes:
            C.empty_state("Nessuna posizione")
            return
        rows = [{"anno": str(y), "netto": v["net_gain"], "usate": v.get("loss_used", 0.0), "imposta": v["tax"]}
                for y, v in sorted(taxes["by_year"].items())]
        tax = taxes["tax_if_sold"]
        carry = taxes.get("carry") or []
        items = [("Plus/minus se vendessi oggi", C.fmt_html(taxes["unrealized_gain"], "money", currency="EUR",
                                                             signed=True)),
                 ("Imposta in più" if tax >= 0 else "Imposta in meno (credito)",
                  C.fmt_html(abs(tax), "money", currency="EUR"))]
        if carry:
            items.append(("Minusvalenze da recuperare", C.fmt_html(sum(c["amount"] for c in carry), "money",
                                                                   currency="EUR")))
        st.html(C.kv_html(items))
        if carry:
            st.caption("Minusvalenze riportabili: " + " · ".join(
                f"{C.fmt(c['amount'], 'money', currency='EUR')} del {c['year']} (fino al 31/12/{c['expires']})"
                for c in carry) + ". Compensano solo plusvalenze di azioni, non i guadagni degli ETF.")
        if taxes.get("incomplete"):
            st.caption("Stima sulle sole posizioni con guadagno noto (manca un prezzo o un cambio).")
        if rows:
            C.dense_table(pd.DataFrame(rows), {"anno": ("Anno", "text"),
                                               "netto": ("Plus/minus €", "num", {"signed": True}),
                                               "usate": ("Minus usate €", "num"),
                                               "imposta": ("Imposta €", "num")})
        else:
            st.caption("Nessuna vendita inserita: niente plusvalenze realizzate.")
        st.caption("Stima, non è consulenza fiscale. Vendendo oggi guadagni e perdite delle azioni si compensano tra "
                   "loro, con il realizzato dell'anno e con le minusvalenze dei 4 anni prima (solo quelle inserite "
                   "qui); i guadagni degli ETF no (redditi di capitale). Regime amministrato o dichiarativo e "
                   "minusvalenze di altri conti cambiano i conti.")
