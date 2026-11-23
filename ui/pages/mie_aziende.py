"""Le mie aziende (F2, docs/revisione_grafica.md §4.3 e §7.3): per ogni azienda seguita il giudizio e i pilastri,
prezzo, margine sul tuo fair value e sulla tua soglia "compra sotto", gli avvisi; storico degli avvisi; soglie."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import streamlit as st

from ui import nav
from ui.common import data, md
from ui.components import (CURRENCY_SYMBOL, _ago, alert_badge, badge, empty_state, fmt, fmt_table, it_number, plural,
                           num_column, panel, table_height, ticker_link, verdict_badge)
from ui.pages.confronta import MAX_COMPARE
from ui.rules import PILLAR_HELP, PILLAR_SHORT, num_col, pillar_column, styled, verdict_note, verdict_table, verdict_text

MAIN_PILLARS = ["quality", "solidity", "price", "flags"]
# colonne facoltative (popover "Colonne"): chiave -> etichetta
EXTRA_COLUMNS = {
    "sell_above": "Vendi sopra", "ev_ebit": "EV/EBIT", "fcf_yield": "Rend. FCF", "roic": "ROIC",
    "revenue_growth": "Crescita ricavi", "net_debt_ebitda": "Debito netto/EBITDA", "dividend_yield": "Dividendo",
    "v_insiders": "Insider (pilastro)", "coverage": "Copertura del giudizio", "last_filing": "Ultimo bilancio",
    "sector": "Settore", "thesis": "Tesi",
}
DEFAULT_EXTRA = ["fcf_yield", "roic"]
ALERT_STATES = ["Attivi", "Non visti", "Chiusi", "Tutti"]


# ---------------------------------------------------------------- dati della pagina
def _targets(d) -> pd.DataFrame:
    t = d.db.load_price_targets()
    return t.set_index("ticker") if not t.empty else pd.DataFrame(columns=["buy_below", "sell_above", "note"])


def _table(d, watch: list[str]) -> pd.DataFrame:
    """watchlist_table + giudizio, pilastri e soglie di prezzo (una riga per azienda seguita)."""
    table = d.watchlist_table()
    verdicts = verdict_table(d, watch)
    v = verdicts.reindex(table["ticker"])
    table["category"] = v["category"].to_numpy()
    table["verdict_long"] = v["verdict"].to_numpy()
    for p in MAIN_PILLARS + ["insiders"]:
        table[f"v_{p}"] = pd.to_numeric(v[p], errors="coerce").to_numpy(dtype=float)
    table["coverage"] = pd.to_numeric(v["coverage"], errors="coerce").to_numpy(dtype=float)
    table["overall"] = pd.to_numeric(v["overall"], errors="coerce").to_numpy(dtype=float)
    targets = _targets(d)
    table["buy_below"] = pd.to_numeric(table["ticker"].map(targets["buy_below"]) if not targets.empty else np.nan,
                                       errors="coerce")
    table["sell_above"] = pd.to_numeric(table["ticker"].map(targets["sell_above"]) if not targets.empty
                                        else np.nan, errors="coerce")
    price = table["price"].where(table["price"] > 0)
    table["buy_margin"] = table["buy_below"] / price - 1
    table["currency"] = [d.currency(t) for t in table["ticker"]]
    table["thesis"] = table["has_thesis"].map({True: "✓", False: ""})
    return table


def _alert_rows(d, table: pd.DataFrame) -> list[dict]:
    """Avvisi da guardare: quelli attivi registrati (con lo stato visto/non visto) + quelli calcolati adesso dalle
    regole della home (`watchlist_table`) che non sono già tra i registrati. Ordinati per azienda (giudizio)."""
    watch = list(table["ticker"])
    active = d.db.load_alerts(active=True)
    active = active[active["ticker"].isin(watch)] if not active.empty else active
    rows = active.to_dict("records") if not active.empty else []
    have = {(r["ticker"], r["code"]) for r in rows}
    for t, alerts in zip(table["ticker"], table["alerts"]):
        for code, text in alerts:
            if (t, code) not in have:
                rows.append({"ticker": t, "code": code, "message": text, "data": {}, "created_at": None,
                             "seen_at": "calcolato ora"})
    order = {t: i for i, t in enumerate(watch)}
    rows.sort(key=lambda r: (order.get(r["ticker"], 999), str(r["code"])))
    return rows


# ---------------------------------------------------------------- pagina
def page_home():
    d = data()
    watch = d.watchlist()
    head, add_col = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        st.title("Le mie aziende")
    with add_col.popover("＋ Aggiungi o togli", width="stretch"):
        _follow_widgets(d, watch)
    if not watch:
        _empty(d)
        return

    table = _table(d, watch)
    table["_order"] = table["category"].map({c: i for i, c in enumerate(_order())}).fillna(99)
    table["alert_count"] = table["alerts"].map(len)
    table = table.sort_values(["_order", "overall"], ascending=[True, False], na_position="last")
    st.caption(f"{plural(len(table), 'azienda', 'aziende')} · prezzi al {fmt(d.data.last_date, 'date')}")

    rows = _alert_rows(d, table)
    _watch_panel(d, table, rows)
    _main_table(d, table)
    _targets_editor(d, table)
    _history(d, watch)
    _legend(d)


def _order() -> list[str]:
    from modules import verdict

    return verdict.ORDER


def _follow_widgets(d, watch: list[str]):
    labels = {t: f"{t} — {d.info(t).get('name') or ''}" for t in d.tickers}
    add = st.multiselect("Aggiungi", [t for t in d.tickers if t not in watch], format_func=labels.get,
                         key="watch_add", placeholder="Cerca per ticker o nome")
    if st.button("Aggiungi alla lista", type="primary", disabled=not add, key="watch_add_btn"):
        d.follow(add)
        st.session_state.pop("watch_add", None)
        st.toast(f"Aggiunte: {', '.join(add)}")
        st.rerun()
    if watch:
        remove = st.multiselect("Togli", watch, format_func=lambda t: labels.get(t, t), key="watch_remove",
                                placeholder="Scegli le aziende da togliere")
        if st.button("Togli dalla lista", disabled=not remove, key="watch_remove_btn"):
            d.follow(remove, on=False)
            st.session_state.pop("watch_remove", None)
            st.toast(f"Tolte: {', '.join(remove)}")
            st.rerun()


def _empty(d):
    empty_state("Non segui ancora nessuna azienda",
                "Qui trovi, per ogni azienda che segui, il giudizio sintetico, prezzo, margine sul tuo fair value e "
                "sulla tua soglia d'acquisto, e gli avvisi (nuovi bilanci, prezzo sotto la soglia, movimenti forti, "
                "margini in calo). Aggiungine con \"＋ Aggiungi o togli\" qui sopra, con \"☆ Segui\" nella scheda o "
                "dalla barra dei comandi (SEGUI AAPL).")
    v = verdict_table(d, list(d.index_tickers))
    top = v[v["category"] == "candidata"].head(6) if not v.empty else v
    if top.empty:
        return
    with panel("Per cominciare", right="le prime «Candidate» dell'S&P 500 per punteggio"):
        verdict_note()
        cols = st.columns(len(top))
        for col, (t, r) in zip(cols, top.iterrows()):
            with col:
                st.html(f'<div>{ticker_link(t)} <span class="flat">{html.escape(str(r.get("name") or ""))}'
                        f'</span></div>')
                if st.button(f"☆ Segui {t}", key=f"home_start_{t}", width="stretch"):
                    d.follow([t])
                    st.toast(f"Ora segui {t}")
                    st.rerun()


ENGLISH_NUMBER_CODES = {"PX-", "PX+", "FV", "FV~", "MOV", "MOV1", "52S", "INS", "MRG", "DOC", "REV"}


def _it_message(r) -> str:
    """Messaggio dell'avviso con i numeri in italiano: le regole di modules/alerts.py scrivono 1,234.5 e 26.8%
    (CHG viene da changes.py, già in italiano; 8K contiene numeri di voce come 4.02 da non toccare)."""
    msg = str(r.get("message") or "")
    return it_number(msg) if r.get("code") in ENGLISH_NUMBER_CODES else msg


def _unseen(r) -> bool:
    s = r.get("seen_at")
    return s is None or (isinstance(s, float) and np.isnan(s))


def _watch_panel(d, table: pd.DataFrame, rows: list[dict]):
    unseen = [r for r in rows if _unseen(r)]
    right = f"{len(rows)} avvisi" + (f" · {len(unseen)} non visti" if unseen else "") if rows else ""
    with panel("Da guardare", right=right):
        if not rows:
            st.html('<div class="qe-empty-line">Nessun avviso: niente bilanci nuovi, movimenti forti, prezzi sotto '
                    'le tue soglie o vicini al tuo fair value.</div>')
            return
        verdicts = table.set_index("ticker")[["category", "verdict_long"]]
        lines, last = [], None
        for r in rows:
            t = r["ticker"]
            first = t != last
            last = t
            vb = ""
            if first and t in verdicts.index and isinstance(verdicts.at[t, "category"], str):
                vb = verdict_badge(verdicts.at[t, "category"], verdicts.at[t, "verdict_long"])
            new = _unseen(r)
            data_ = r.get("data") if isinstance(r.get("data"), dict) else None
            sep = ' style="border-top:1px solid var(--border-ctl)"' if first and lines else ""
            lines.append(
                f'<div class="qe-alert{" new" if new else ""}"{sep}>'
                f'<span class="dot" title="{"non visto" if new else ""}">{"●" if new else ""}</span>'
                f'<span class="tk">{ticker_link(t) if first else ""}</span>'
                f'<span style="width:9.5em;flex-shrink:0">{vb}</span>{alert_badge(str(r["code"]), data_)}'
                f'<span class="msg" title="{html.escape(_it_message(r))}">{html.escape(_it_message(r))}</span>'
                f'<span class="age">{_ago(r.get("created_at")) if r.get("created_at") else "adesso"}</span></div>')
        # al massimo ~8 righe visibili: con molti avvisi la tabella delle aziende resta nella prima schermata
        st.html('<div style="max-height:216px;overflow-y:auto">' + "".join(lines) + "</div>")
        ids = [int(r["id"]) for r in unseen if "id" in r and pd.notna(r.get("id"))]
        if ids and st.button(f"Segna come visti ({len(ids)})", key="home_seen_all"):
            d.db.mark_alerts_seen(ids)
            st.toast("Avvisi segnati come visti")
            st.rerun()


def _main_table(d, table: pd.DataFrame):
    currencies = set(table["currency"])
    one = len(currencies) == 1
    sym = CURRENCY_SYMBOL.get(next(iter(currencies)), next(iter(currencies))) if one else ""
    unit = f" {sym}" if sym else ""
    with panel("Le mie aziende", right="ordinate per giudizio"):
        top_l, top_r = st.columns([5, 1], vertical_alignment="bottom")
        with top_r.popover("Colonne", width="stretch"):
            extra = st.multiselect("Altre colonne", list(EXTRA_COLUMNS), default=DEFAULT_EXTRA, key="home_cols",
                                   format_func=EXTRA_COLUMNS.get, placeholder="Scegli le colonne")
        with top_l:
            verdict_note()
        cols = ["ticker", "name", "verdict"] + [f"v_{p}" for p in MAIN_PILLARS] + \
            ([] if one else ["currency"]) + ["price", "ret_1m", "ret_1y", "pe", "my_fair_value", "my_margin",
                                             "buy_below", "buy_margin"] + extra + ["alert_count"]
        view = table.assign(verdict=table["category"].map(verdict_text))
        for c in ("ret_1m", "ret_1y", "my_margin", "buy_margin", "fcf_yield", "roic", "revenue_growth",
                  "dividend_yield", "coverage"):
            view[c] = view[c].astype(float).round(4) + 0.0
        for c in ("pe", "ev_ebit", "net_debt_ebitda"):
            view[c] = view[c].astype(float).round(1) + 0.0
        view["last_filing"] = pd.to_datetime(view["last_filing"], errors="coerce")
        view = view.reset_index(drop=True)
        progress = {f"v_{p}": pillar_column(p) for p in MAIN_PILLARS + ["insiders"]}
        for p in MAIN_PILLARS + ["insiders"]:
            view[f"v_{p}"] = view[f"v_{p}"].round(2)
        slot = st.container()
        kinds = {"price": "price", "ret_1m": ("pct", {"arrow": True}), "ret_1y": ("pct", {"arrow": True}),
                 "pe": ("num", {"digits": 1}), "ev_ebit": ("num", {"digits": 1}),
                 "net_debt_ebitda": ("num", {"digits": 1}), "my_fair_value": "price", "buy_below": "price",
                 "sell_above": "price", "my_margin": ("pct", {"signed": True}),
                 "buy_margin": ("pct", {"signed": True}), "fcf_yield": "pct", "roic": "pct",
                 "revenue_growth": ("pct", {"signed": True}), "dividend_yield": "pct",
                 "coverage": ("pct", {"digits": 0}), "alert_count": "int", "last_filing": "date"}
        selection = st.dataframe(
            styled(view[cols], kinds), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key="home_table", height=table_height(len(view), 20),
            column_config={
                "ticker": st.column_config.TextColumn("Ticker", pinned=True), "name": "Azienda",
                "verdict": st.column_config.TextColumn("Giudizio", help="Sintesi dei criteri (✓ Candidata, ◐ "
                                                       "Aspettare, ● Neutra, ! Dubbia, ✗ Evitare, ? Dati insuff.): "
                                                       "non è una raccomandazione."),
                **progress, "currency": "Valuta",
                "price": num_col(f"Prezzo{unit}"),
                "ret_1m": num_col("1 mese"), "ret_1y": num_col("1 anno"),
                "pe": num_col("P/E (x)"), "ev_ebit": num_col("EV/EBIT (x)"),
                "my_fair_value": num_col(f"Tuo FV{unit}", help="Il fair value che hai salvato (scheda "
                                            "→ Valutazione o La mia ricerca)"),
                "my_margin": num_col("Margine sul FV", help="Tuo fair value / prezzo − 1: positivo = il "
                                        "prezzo è sotto il tuo fair value"),
                "buy_below": num_col(f"Compra sotto{unit}", help="La tua soglia d'acquisto (si cambia "
                                        "in «Soglie di prezzo» qui sotto)"),
                "buy_margin": num_col("Margine sulla soglia", help="Soglia «compra sotto» / prezzo − 1: "
                                         "positivo = il prezzo è già sotto la soglia"),
                "sell_above": num_col(f"Vendi sopra{unit}"),
                "fcf_yield": num_col("Rend. FCF"), "roic": num_col("ROIC"),
                "revenue_growth": num_col("Crescita ricavi"),
                "net_debt_ebitda": num_col("Debito netto/EBITDA (x)"),
                "dividend_yield": num_col("Dividendo"),
                "coverage": num_col("Copertura", help="Quota delle prove del giudizio con "
                                       "il dato disponibile"),
                "last_filing": st.column_config.Column("Ultimo bilancio"),
                "sector": "Settore", "thesis": st.column_config.TextColumn("Tesi", help="✓ = hai scritto le note"),
                "alert_count": num_col("Avvisi", help="Avvisi calcolati adesso (vedi «Da guardare»)"),
            })
        picked = [view.iloc[i]["ticker"] for i in (selection.selection.rows if selection else [])]
        with slot:
            if not picked:
                st.caption("Seleziona una riga per aprire la scheda, più righe per confrontarle o toglierle.")
            else:
                b1, b2, b3 = st.columns(3)
                if b1.button(f"Apri la scheda di {picked[0]} →", type="primary", width="stretch"):
                    st.session_state["ticker"] = picked[0]
                    st.switch_page(nav.page("azienda"))
                cmp_label = (f"Confronta le {len(picked)} selezionate" if len(picked) > 1
                             else "Confronta (scegline almeno 2)")
                if b2.button(cmp_label, width="stretch",
                             disabled=not 2 <= len(picked) <= MAX_COMPARE, help=f"Da 2 a {MAX_COMPARE} aziende"):
                    st.session_state["cmp"] = picked
                    st.switch_page(nav.page("confronta"))
                if b3.button(f"Togli {picked[0] if len(picked) == 1 else f'le {len(picked)} selezionate'} "
                             "dalla lista", width="stretch"):
                    d.follow(picked, on=False)
                    st.toast(f"Tolte: {', '.join(picked)}")
                    st.rerun()


def _targets_editor(d, table: pd.DataFrame):
    targets = _targets(d)
    with st.expander("Soglie di prezzo: compra sotto / vendi sopra"):
        st.caption("Quando il prezzo scende sotto «compra sotto» (o sale sopra «vendi sopra») compare un avviso "
                   "(PX- / PX+) al prossimo aggiornamento dei dati. Lascia vuoto per non avere la soglia.")
        tickers = list(dict.fromkeys(list(table["ticker"]) + list(targets.index)))
        price = table.set_index("ticker")["price"]

        def old(t, col):
            v = targets[col].get(t) if t in targets.index else None
            return None if v is None or pd.isna(v) else v

        # testo, non NumberColumn: un campo vuoto si vedrebbe "None" e si scrive con la virgola (70,50)
        edit = pd.DataFrame({
            "ticker": tickers,
            "price": [fmt(price.get(t), "price") for t in tickers],
            "buy_below": [fmt(old(t, "buy_below"), "num", na="") for t in tickers],
            "sell_above": [fmt(old(t, "sell_above"), "num", na="") for t in tickers],
            "note": [str(old(t, "note") or "") for t in tickers],
        })
        edited = st.data_editor(
            edit, hide_index=True, width="stretch", key="home_targets", num_rows="fixed",
            disabled=["ticker", "price"], height=table_height(len(edit), 12),
            column_config={
                "ticker": "Ticker", "price": st.column_config.TextColumn("Prezzo", alignment="right"),
                "buy_below": st.column_config.TextColumn("Compra sotto", alignment="right",
                                                         help="Prezzo, es. 70,50; vuoto = nessuna soglia"),
                "sell_above": st.column_config.TextColumn("Vendi sopra", alignment="right",
                                                          help="Prezzo, es. 120; vuoto = nessuna soglia"),
                "note": st.column_config.TextColumn("Nota", help="Perché questa soglia (facoltativa)"),
            })
        parsed, wrong = [], []
        for r in edited.itertuples(index=False):
            buy, ok_b = _parse_price(r.buy_below)
            sell, ok_s = _parse_price(r.sell_above)
            if not (ok_b and ok_s):
                wrong.append(f"{r.ticker}: numero non valido")
            elif buy is not None and sell is not None and buy >= sell:
                wrong.append(f"{r.ticker}: «compra sotto» deve essere minore di «vendi sopra»")
            parsed.append((r.ticker, buy, sell, (r.note or "").strip() or None))
        for w in wrong:
            st.html('<div class="qe-empty-line">' + badge("!", "warn") + " " + html.escape(w) + "</div>")
        if st.button("Salva le soglie", type="primary", disabled=bool(wrong), key="home_targets_save"):
            changed = 0
            for t, buy, sell, note in parsed:
                if _eq(buy, old(t, "buy_below")) and _eq(sell, old(t, "sell_above")) and \
                        (note or "") == str(old(t, "note") or ""):
                    continue
                if buy is None and sell is None and not note:
                    d.db.delete_price_target(t)
                else:
                    d.db.set_price_target(t, buy, sell, note)
                changed += 1
            st.toast(f"Soglie salvate ({changed} aziende)" if changed else "Nessuna soglia cambiata")
            st.rerun()


def _parse_price(text) -> tuple[float | None, bool]:
    """"70,50" / "1.234,5" / "70.5" / "" -> (valore o None, valido)."""
    s = str(text or "").strip().replace(" ", "").replace("€", "").replace("$", "")
    if not s or s in ("–", "-"):
        return None, True
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None, False
    if v <= 0 or not np.isfinite(v):
        return None, v == 0
    return v, True


def _eq(a, b) -> bool:
    a_na = a is None or (isinstance(a, float) and np.isnan(a))
    b_na = b is None or (isinstance(b, float) and np.isnan(b))
    return (a_na and b_na) or (not a_na and not b_na and abs(float(a) - float(b)) < 1e-9)


def _history(d, watch: list[str]):
    hist = d.db.load_alerts(limit=2000)
    unseen = int(hist["seen_at"].isna().sum()) if not hist.empty else 0
    title = "Storico degli avvisi" + (f" · {unseen} non visti" if unseen else "")
    with st.expander(title):
        if hist.empty:
            empty_state("Nessun avviso registrato",
                        "Gli avvisi si registrano a ogni aggiornamento dei dati (menu Dati o aggiornamento serale) "
                        "per le aziende che segui, quelle in portafoglio e quelle con una soglia di prezzo. Quelli "
                        "calcolati adesso sono in «Da guardare».")
            return
        c1, c2, c3 = st.columns([2, 2, 2], vertical_alignment="bottom")
        state = c1.segmented_control("Mostra", ALERT_STATES, default="Tutti", key="home_hist_state")
        tickers = c2.multiselect("Aziende", sorted(hist["ticker"].unique()), key="home_hist_tk",
                                 placeholder="Tutte le aziende")
        codes = c3.multiselect("Codici", sorted(hist["code"].unique()), key="home_hist_code",
                               placeholder="Tutti i codici")
        view = hist
        if state == "Attivi":
            view = view[view["cleared_at"].isna()]
        elif state == "Non visti":
            view = view[view["seen_at"].isna()]
        elif state == "Chiusi":
            view = view[view["cleared_at"].notna()]
        if tickers:
            view = view[view["ticker"].isin(tickers)]
        if codes:
            view = view[view["code"].isin(codes)]
        if view.empty:
            st.html('<div class="qe-empty-line">Nessun avviso con questi filtri.</div>')
            return
        shown = view.assign(
            created=pd.to_datetime(view["created_at"], errors="coerce"),
            message=[_it_message(r) for r in view.to_dict("records")],
            state=[("chiuso il " + fmt(c, "date_short")) if pd.notna(c) else "attivo" for c in view["cleared_at"]],
            seen=[("sì, " + fmt(s, "date_short")) if pd.notna(s) else "● no" for s in view["seen_at"]])
        st.dataframe(fmt_table(shown, {"created": ("Data", "date_short"), "ticker": ("Azienda", "text"),
                                       "code": ("Codice", "text"), "message": ("Messaggio", "text"),
                                       "state": ("Stato", "text"), "seen": ("Visto", "text")}),
                     hide_index=True, width="stretch", height=table_height(len(shown), 12),
                     column_config={"Messaggio": st.column_config.TextColumn("Messaggio", width="large")})
        ids = [int(i) for i in view.loc[view["seen_at"].isna(), "id"]]
        if ids and st.button(f"Segna come visti ({len(ids)})", key="home_hist_seen",
                             help="Solo gli avvisi mostrati con questi filtri"):
            d.db.mark_alerts_seen(ids)
            st.toast(f"{len(ids)} avvisi segnati come visti")
            st.rerun()


def _legend(d):
    w = d.cfg.get("watchlist", {})
    a = d.cfg.get("alerts", {})
    with st.expander("Cosa vogliono dire i codici degli avvisi"):
        st.markdown(md(
            "- `PX-` / `PX+` il prezzo è sotto la tua soglia «compra sotto» / sopra «vendi sopra»\n"
            f"- `FV` il prezzo è sotto il fair value che hai salvato; `FV~` è sopra di meno del "
            f"{fmt(w.get('near_fair_value', 0.05), 'pct', digits=0)}\n"
            f"- `DOC` un 10-Q o 10-K depositato negli ultimi {w.get('new_filing_days', 14)} giorni; `CHG` il bilancio "
            "nuovo ha cambi rilevanti (scheda → cosa è cambiato)\n"
            f"- `MOV` il prezzo si è mosso di almeno il {fmt(w.get('big_move_1m', 0.15), 'pct', digits=0)} in un "
            f"mese; `MOV1` di almeno il {fmt(a.get('big_move_1d', 0.07), 'pct', digits=0)} in un giorno\n"
            f"- `52S` è almeno il {fmt(-w.get('drawdown_alert', -0.25), 'pct', digits=0)} sotto il massimo "
            "dell'ultimo anno\n"
            f"- `MRG` il margine operativo (ultimi 12 mesi) è sceso di almeno "
            f"{fmt(w.get('margin_drop', 0.03), 'pp', digits=0).lstrip('+')} rispetto a un anno prima\n"
            "- `INS` gli insider hanno comprato sul mercato; `8K` un 8-K con voci gravi\n"
            "- `REV` è arrivata la data di revisione della tua tesi o di una decisione del diario\n"
            "- `VRD` il giudizio dell'azienda è cambiato (es. da Aspettare a Candidata all'acquisto), con il pilastro "
            "che l'ha fatto cambiare\n\n"
            "Le soglie stanno in config.yaml (sezioni watchlist e alerts)."))
