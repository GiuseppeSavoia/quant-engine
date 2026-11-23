"""Scheda azienda, parte alta (docs/revisione_grafica.md §4.2, §7.1-7.3): il Verdetto, "cosa è cambiato", gli avvisi
attivi dell'azienda, le soglie di prezzo dell'utente e la funzione "Prove" (le prove di ogni pilastro con il
confronto col settore).

Il giudizio e "cosa è cambiato" vengono dal servizio `judgment` (`d.company_verdict`, `d.what_changed`: regole di
modules/verdict.py e modules/changes.py invariate), soglie e avvisi dal servizio `watch`: qui solo la presentazione. I due calcoli stanno in `st.cache_data` con chiave
(ticker, versione dei dati, dati dell'utente che li cambiano): cambiare funzione della scheda non li rifà.
"""
from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

from modules.changes import ITEMS as CHANGE_ITEMS  # solo etichette delle voci
from ui.components import (NA, OUTCOME_SYMBOL, alert_row_html, change_rows_html, empty_state, evidence_table_html,
                           fmt, fmt_html, fmt_metric, panel, verdict_card_html)

# CSS dei pezzi costruiti qui (niente parentesi angolari dentro il CSS: regola dei componenti)
VERDETTO_CSS = """<style>
.qe-honest { font-size: var(--fs-data); color: var(--text-2); margin: 2px 0 0; padding: 4px 10px;
  border-left: 3px solid var(--border-ctl); }
.qe-honest summary { cursor: pointer; color: var(--text-2); }
.qe-honest summary b { color: var(--text); font-weight: 600; }
.qe-honest summary span { color: var(--info); margin-left: 6px; }
.qe-honest div { margin-top: 6px; color: var(--text-2); line-height: 1.45; max-width: 980px; }
.qe-honest p { margin: 0 0 6px; }
.qe-targets { font: var(--fs-data) var(--mono); color: var(--text-num); }
.qe-targets .lbl { color: var(--text-3); font-family: var(--sans); }
.qe-alerts-box { margin-top: 2px; }
.qe-thr { color: var(--accent); font-weight: 600; }
.qe-thr-n { color: var(--text-3); font-family: var(--sans); font-size: var(--fs-micro); }
.qe-ev-sec { color: var(--text-3); }
</style>"""


# ================================================================== calcoli in cache
def data_key(d) -> str:
    """Versione dei dati: oggetto DashboardData (cambia a ogni ricaricamento), ultima esecuzione, dati a mano."""
    return f"{id(d)}|{getattr(d, 'run_id', '')}|{d.user_data_version()}"


@st.cache_data(show_spinner=False, max_entries=128)
def _verdict_cached(ticker: str, key: str, fair_value, _d):  # noqa: ARG001 - key e fair_value: solo chiavi
    return _d.company_verdict(ticker)


@st.cache_data(show_spinner=False, max_entries=128)
def _changes_cached(ticker: str, key: str, thesis_key: str, sector, currency: str, _d, _note, _valuation):  # noqa: ARG001
    return _d.what_changed(ticker, sector, currency, note=_note or {}, valuation=_valuation)


def assess(d, ticker: str, fair_value=None):
    """Il Verdetto dell'azienda (o None se il calcolo non riesce: la scheda resta usabile)."""
    fv = None if fair_value is None or pd.isna(fair_value) else float(fair_value)
    try:
        return _verdict_cached(ticker, data_key(d), fv, d)  # frasi già in italiano (modules/verdict.py)
    except Exception as exc:  # noqa: BLE001
        st.session_state.setdefault("_verdict_errors", {})[ticker] = str(exc)
        return None


from modules.formats import it_text  # noqa: E402,F401 - riesportata per valutazione.py e ricerca.py


def what_changed(d, ticker: str, sector, currency: str) -> dict:
    note, valuation = d.thesis_inputs(ticker)
    vkey = "" if not valuation else str(valuation.get("id") or valuation.get("created_at") or "")
    thesis_key = f"{note.get('updated_at')}|{note.get('fair_value')}|{vkey}"
    try:
        return _changes_cached(ticker, data_key(d), thesis_key, sector, currency, d, note, valuation) or {}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ================================================================== soglie di prezzo
def price_target(d, ticker: str) -> dict:
    """Soglie dell'utente per il titolo: {buy_below, sell_above, note} (vuoto se non ci sono)."""
    return d.price_target(ticker)


def targets_html(target: dict, currency: str) -> str:
    """"compra sotto 55,00 · vendi sopra 80,00" (HTML) o "–"."""
    parts = []
    if target.get("buy_below") is not None:
        parts.append(f'<span class="lbl">compra sotto</span> {fmt(target["buy_below"], "price")}')
    if target.get("sell_above") is not None:
        parts.append(f'<span class="lbl">vendi sopra</span> {fmt(target["sell_above"], "price")}')
    if not parts:
        return f'<span class="qe-na">{NA}</span>'
    return '<span class="qe-targets">' + " · ".join(parts) + "</span>"


def targets_form(d, ticker: str, target: dict, currency: str) -> None:
    """Contenuto del popover "Soglie": compra sotto / vendi sopra / nota, Salva e Togli."""
    st.caption(f"Ricevi un avviso (PX- / PX+) quando il prezzo passa la soglia. Valori in {currency}.")
    with st.form(f"targets_{ticker}", border=False):
        buy = st.number_input("Compra sotto", min_value=0.0, value=target.get("buy_below"), step=1.0,
                              format="%.2f", placeholder="nessuna soglia", key=f"tg_buy_{ticker}")
        sell = st.number_input("Vendi sopra", min_value=0.0, value=target.get("sell_above"), step=1.0,
                               format="%.2f", placeholder="nessuna soglia", key=f"tg_sell_{ticker}")
        note = st.text_input("Nota", value=target.get("note") or "", placeholder="perché queste soglie",
                             key=f"tg_note_{ticker}")
        saved = st.form_submit_button("Salva le soglie", type="primary")
    if saved:
        try:
            d.set_price_target(ticker, buy_below=buy, sell_above=sell, note=note or None)
        except ValueError as exc:  # soglie mancanti o invertite: il messaggio è già per l'utente
            st.error(str(exc))
        else:
            st.session_state["qe_flash"] = f"Soglie di {ticker} salvate."
            st.rerun()
    if target and st.button("Togli le soglie", key=f"tg_del_{ticker}"):
        d.delete_price_target(ticker)
        st.session_state["qe_flash"] = f"Soglie di {ticker} tolte."
        st.rerun()


# ================================================================== verdetto
HONEST_SHORT = ("Il giudizio riassume criteri fissi applicati ai dati: non è una raccomandazione. "
                "Nella prova sul passato le aziende «Candidata all'acquisto» non hanno battuto l'indice.")
HONEST_LONG = [
    "Il giudizio è una sintesi automatica: applica ai dati disponibili soglie scelte in anticipo (qualità, solidità, "
    "prezzo, segnali d'allarme). Non conosce il tuo quadro personale e non prevede il rendimento.",
    "Lo abbiamo messo alla prova sul passato: ogni trimestre dal 2013 al 2026, sulle aziende dell'S&P 500 di allora e "
    "con i soli dati già pubblicati a quella data. Nei 12 mesi successivi le aziende «Candidata all'acquisto» hanno "
    "reso in media lo 0,8% in meno dell'indice a pesi uguali (e il 2,1% in meno dell'S&P 500). La differenza è piccola "
    "e statisticamente non significativa: può essere caso, ma di certo non è un vantaggio dimostrato.",
    "Il pilastro Prezzo (sembrare a buon mercato) ha avuto un legame leggermente negativo con i rendimenti "
    "successivi. Per questo dal 23/11/2026 il prezzo fa solo da freno: per «Candidata» basta che non sia alto, non "
    "serve che sembri conveniente. Con nessuna delle due regole le Candidate hanno battuto l'indice (differenze non "
    "misurabili). La soglia non è stata scelta guardando i risultati; ora si misura in avanti, con i giudizi salvati "
    "ogni sera.",
    "Usalo per capire l'azienda e vedere quali prove pesano a favore e contro, non come segnale per comprare. "
    "Dettagli: reports/verdetto_storico_*.md (modules/verdict_research.py).",
]


def honesty_html() -> str:
    body = "".join(f"<p>{html.escape(p)}</p>" for p in HONEST_LONG)
    return (f'<details class="qe-honest"><summary><b>i</b> {html.escape(HONEST_SHORT)}<span>quanto è affidabile?'
            f"</span></summary><div>{body}</div></details>")


def value_html(v, price, currency: str, fair_value, target: dict) -> str:
    """Riga VALORE del riquadro: prezzo, tuo fair value con margine, soglie."""
    parts = [f"Prezzo {fmt(price, 'price', currency=currency)}"]
    if fair_value is not None and not pd.isna(fair_value) and price:
        parts.append(f"tuo FV {fmt(fair_value, 'price')} ({fmt_html(float(fair_value) / float(price) - 1, 'pct', digits=0, signed=True)})")
    else:
        parts.append(f'tuo FV <span class="qe-na">{NA}</span>')
    if target:
        parts.append(targets_html(target, currency))
    thr = threshold_html(v)
    return (thr + "<br>" if thr else "") + " · ".join(parts)


_THRESHOLD = re.compile(r"(Sotto|Sopra) circa ([\d.,]+) (\S+) \(([-+−]?\d+%) da oggi\)")


def threshold_html(v) -> str:
    """Il prezzo a cui il giudizio cambierebbe (da "cosa farebbe cambiare"), in evidenza nella riga VALORE."""
    for c in (v.changes if v is not None else []):
        m = _THRESHOLD.search(c)
        if m:
            side, px, cur, chg = m.groups()
            what = "diventa Candidata" if side == "Sotto" else "smette di essere Candidata"
            return (f'<span class="qe-thr">Prezzo soglia: {html.escape(side.lower())} {html.escape(px)} '
                    f'{html.escape(cur)}</span> <span class="qe-thr-n">({html.escape(chg.replace("-", "−"))} da oggi: '
                    f"{what})</span>")
    return ""


def render_verdict(v, value: str = "") -> None:
    """Riquadro del Verdetto + nota di onestà + "Perché?" (prove di ogni pilastro, mancanti in fondo)."""
    st.html(VERDETTO_CSS + verdict_card_html(v, value_html=value) + honesty_html())
    pillars = [p for p in (v.pillars or {}).values()]
    with st.expander("Perché? Le prove di ogni pilastro"):
        tabs = st.tabs([_tab_label(p) for p in pillars])
        for tab, p in zip(tabs, pillars):
            with tab:
                if not p.evidence:
                    st.html('<div class="qe-empty-line">Nessuna prova per questo tipo di azienda.</div>')
                    continue
                st.html(evidence_table_html(p))
                if p.missing:
                    n = len(p.missing)
                    st.html(f'<div class="qe-more">{"Manca" if n == 1 else "Mancano"} {n} '
                            f'{"prova" if n == 1 else "prove"} su {len(p.evidence)}: '
                            f'{html.escape(", ".join(e.label for e in p.missing))}. Un dato mancante non conta mai '
                            "come favorevole.</div>")
        if v.changes[4:]:
            st.html('<div class="qe-more">Cosa farebbe cambiare il giudizio (tutto): '
                    + html.escape(" ".join(v.changes)) + "</div>")


def _tab_label(p) -> str:
    score = NA if p.score is None else fmt(p.score, "score")
    return f"{p.label.split(' (')[0]} {score}"


# ================================================================== cosa è cambiato
FMT_KIND = {"money": "money", "shares": "shares", "pct": "pct", "x": "mult", "num": "num"}
COMPARISON = {"previous": "periodo prima", "year_ago": "un anno prima", "valuation": "dal deposito a oggi",
              "between": ""}


def _price_line(wc: dict, currency: str) -> str:
    latest = wc.get("latest") or {}
    p0, p1 = latest.get("price"), wc.get("price_now")
    if p0 is None or p1 is None or pd.isna(p0) or pd.isna(p1) or not p0:
        return ""
    chg = float(p1) / float(p0) - 1
    line = (f"Prezzo dal deposito del {fmt(latest.get('filed'), 'date_short')}: {fmt(p0, 'price')} → "
            f"{fmt(p1, 'price', currency=currency)} ({fmt(chg, 'pct', arrow=True)})")
    rel = [c.text for c in wc.get("valuation") or [] if c.relevant]
    return line + (" · " + "; ".join(rel) if rel else "")


def _thesis_line(wc: dict) -> str:
    th = wc.get("thesis") or []
    if not th:
        return ""
    ok = sum(1 for t in th if t["on_track"] is True)
    ko = [t for t in th if t["on_track"] is False]
    wait = sum(1 for t in th if t["on_track"] is None)  # ancora niente da confrontare (es. nessun bilancio nuovo)
    text = f"La tua tesi: ✓ {ok} ipotesi rispettat{'a' if ok == 1 else 'e'}"
    if wait:
        text += f" · … {wait} in corso"
    if ko:
        text += f" · ✗ {len(ko)} no ({ko[0]['text']})"
    return text


def render_changes(wc: dict, currency: str) -> None:
    """Pannello "Cosa è cambiato": favorevoli/contrarie dell'ultimo bilancio, prezzo dal deposito, tesi; tutti i
    cambi prima/dopo in un riquadro richiudibile."""
    if not wc or wc.get("error"):
        with panel("Cosa è cambiato"):
            why = ("il confronto non è riuscito: " + wc["error"]) if wc.get("error") else \
                "nessun bilancio in archivio per questa azienda"
            st.html(f'<div class="qe-empty-line">Niente da confrontare: {html.escape(why)}.</div>')
        return
    latest = wc.get("latest") or {}
    comps = wc.get("comparisons") or {}
    form = latest.get("form") or "bilancio"
    title = f"Cosa è cambiato · {form} del {fmt(latest.get('filed'), 'date_short')}"
    right = " e ".join(f"contro {COMPARISON[k]}" for k in ("previous", "year_ago") if k in comps)
    filing = [s for s in wc.get("summary") or [] if s.get("comparison") in ("previous", "year_ago")]
    price_line, thesis_line = _price_line(wc, currency), _thesis_line(wc)
    with panel(title, right=html.escape(right)):
        filed = latest.get("filed")
        stale = filed is not None and pd.notna(filed) and wc.get("as_of") is not None and \
            (pd.Timestamp(wc["as_of"]) - pd.Timestamp(filed)).days > 90
        if not filing and stale:
            st.html(f'<div class="qe-empty-line">Ultimo bilancio del {fmt(filed, "date")}, nessun cambio rilevante.'
                    f'{" " + html.escape(price_line) if price_line else ""}</div>')
        else:
            st.html(change_rows_html(filing, limit=3, price_line=price_line, thesis_line=thesis_line))
        items = [c for k in ("previous", "year_ago") for c in (comps.get(k) or {}).get("items", [])] + \
            list(wc.get("valuation") or [])
        if items:
            with st.expander(f"Tutti i cambi, prima e dopo ({len(items)})"):
                st.html(changes_table_html(items, currency))


def changes_table_html(items, currency: str) -> str:
    rows = []
    for c in sorted(items, key=lambda c: (list(COMPARISON).index(c.comparison) if c.comparison in COMPARISON else 9,
                                           -c.score)):
        it = CHANGE_ITEMS.get(c.key)
        kind = FMT_KIND.get(it.fmt if it else "num", "num")
        cur = {"currency": currency} if kind == "money" else {}
        delta = fmt(c.delta, "pp") if c.kind == "pt" and kind == "pct" else \
            fmt(c.delta, "num", signed=True) if c.kind == "pt" else fmt(c.delta, "pct", signed=True)
        verso = {True: '<span class="pos">▲ buono</span>', False: '<span class="neg">▼ cattivo</span>'}.get(
            c.favorable, '<span class="flat">■</span>')
        rows.append(f'<tr{"" if c.relevant else " class=miss"}><td class="t">{html.escape(c.label)}</td>'
                    f'<td class="t">{html.escape(COMPARISON.get(c.comparison, c.comparison))}</td>'
                    f'<td class="n">{fmt(c.before, kind, **cur)}</td><td class="n">{fmt(c.after, kind, **cur)}</td>'
                    f'<td class="n">{delta}</td><td class="t">{"● sì" if c.relevant else "no"}</td>'
                    f'<td class="t">{verso}</td></tr>')
    return ('<div class="qe-tablewrap"><table class="qe-dense"><thead><tr><th>Voce</th><th>Confronto</th>'
            '<th class=n>Prima</th><th class=n>Dopo</th><th class=n>Variazione</th><th>Rilevante</th><th>Verso</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
            '<div class="qe-more">Righe grigie: variazioni sotto la soglia di rilevanza (alerts.change_thresholds in '
            "config.yaml).</div>")


# ================================================================== avvisi attivi
def render_alerts(d, ticker: str) -> None:
    """Avvisi ancora attivi dell'azienda (una riga ciascuno); aprire la scheda li segna come visti."""
    a = d.active_alerts(ticker)
    if a.empty:
        return
    with panel(f"Avvisi attivi · {len(a)}", right="storico in Le mie aziende"):
        st.html('<div class="qe-alerts-box">' + "".join(alert_row_html(r, link=False) for r in a.to_dict("records"))
                + "</div>")
    unseen = a.loc[a["seen_at"].isna(), "id"].tolist() if "seen_at" in a else []
    if unseen:
        try:
            d.mark_alerts_seen(unseen)
        except Exception:  # noqa: BLE001 - segnare come visti è un di più: la scheda non si rompe
            pass


# ================================================================== funzione "Prove"
def render_evidence(v, cm: pd.DataFrame, sector: str | None) -> None:
    """Prove raggruppate per pilastro: valore, riferimento, mediana del settore e posizione nel settore, esito."""
    if v is None:
        empty_state("Prove non disponibili", "Il giudizio non è stato calcolato per questa azienda.")
        return
    cmi = cm.set_index("key") if "key" in cm else pd.DataFrame()
    st.html(f'<div class="qe-more">Ogni prova con la soglia fissata a priori e, dove è una metrica del catalogo, la '
            f'mediana del settore ({html.escape(sector or "n/d")}) e la posizione nel settore (100% = la migliore). '
            "Le prove mancanti sono in fondo, in grigio: non contano mai come favorevoli.</div>")
    for p in v.pillars.values():
        score = NA if p.score is None else f"{fmt(p.score, 'score')} {p.grade}"
        n_ok = len(p.evidence) - len(p.missing)
        cov = f"{n_ok} {'prova' if n_ok == 1 else 'prove'} su {len(p.evidence)}"
        with panel(p.label, right=html.escape(f"{score} · {cov}")):
            if not p.evidence:
                st.html('<div class="qe-empty-line">Nessuna prova per questo tipo di azienda.</div>')
                continue
            st.html(_evidence_rows_html(p, cmi))


def _evidence_rows_html(p, cmi: pd.DataFrame) -> str:
    ev = sorted(p.evidence, key=lambda e: e.outcome == "mancante")
    rows = []
    for e in ev:
        sym, tone = OUTCOME_SYMBOL.get(e.outcome, ("●", "neutral"))
        med, rank = NA, NA
        if e.key in cmi.index:
            med = fmt_metric(e.key, cmi.at[e.key, "sector_median"])
            r = cmi.at[e.key, "rank_sector"]
            rank = fmt(r, "pct", digits=0) if r is not None and not pd.isna(r) else NA
        miss = " class=miss" if e.outcome == "mancante" else ""
        rows.append(f'<tr{miss} title="{html.escape(e.text or "")}"><td class="t">{html.escape(e.label)}</td>'
                    f'<td class="n">{html.escape(e.shown or NA)}</td>'
                    f'<td class="t fill" title="{html.escape(e.reference or "")}">{html.escape(e.reference or "")}</td>'
                    f'<td class="n qe-ev-sec">{med}</td><td class="n qe-ev-sec">{rank}</td>'
                    f'<td class="t"><span class="{tone}">{sym} {html.escape(e.outcome)}</span></td></tr>')
    return ('<div class="qe-tablewrap"><table class="qe-dense"><thead><tr><th>Prova</th><th class=n>Valore</th>'
            '<th>Riferimento</th><th class=n>Settore</th><th class=n>Nel settore</th><th>Esito</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def verdict_error_box(ticker: str) -> None:
    err = (st.session_state.get("_verdict_errors") or {}).get(ticker)
    empty_state("Giudizio non calcolato", "Il calcolo del verdetto non è riuscito per questa azienda: le funzioni "
                "sotto restano disponibili.", details=err)


__all__ = ["assess", "what_changed", "price_target", "targets_html", "targets_form", "render_verdict",
           "render_changes", "render_alerts", "render_evidence", "verdict_error_box", "value_html", "honesty_html",
           "data_key"]
