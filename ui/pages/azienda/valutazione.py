"""Scheda azienda, funzione Valutazione (DCF): valutazione a leve (Damodaran), costo del capitale, DCF rapido
dell'utente, multipli nel tempo (docs/revisione_grafica.md §1 A3, §3.6).

Il RISULTATO sta in cima e resta visibile (riga fissa sotto la cornice) mentre si cambiano le leve: si prepara un
segnaposto, si disegnano le leve, si calcola e si riempie il segnaposto. Numeri in italiano con `ui.components`
(mai None/nan), avvisi dei controlli con `notice`, salvataggi confermati con `st.toast`. I calcoli sono quelli di
`modules.valuation_model` e `DashboardData` (qui solo presentazione).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import valuation_model as vm
from modules.dashboard_data import DashboardData, HISTORY_MULTIPLES
from modules.metrics import METRIC_BY_KEY
from ui.common import MUTED, SERIES, _layout, _line
from ui.components import (NA, dense_table, empty_state, fmt, fmt_html, fmt_metric, notice, num_column,
                           result_row_html, table_height)
from ui.pages.azienda.prezzo import _fair_value_history
from ui.pages.azienda.verdetto import it_text

# riga del risultato fissa sotto la cornice mentre si scorre tra le leve (niente parentesi angolari nel CSS)
VALUATION_CSS = """<style>
[data-testid="stLayoutWrapper"]:has(> .st-key-dcf_result), .st-key-dcf_result { position: sticky;
  top: calc(3.75rem + 66px); z-index: 980; background: var(--bg); }
.st-key-dcf_result .qe-result { border-left: 3px solid var(--accent); }
.st-key-dcf_result .qe-result { padding: 6px 12px; }
.st-key-dcf_result .qe-result .qe-kv { gap: 2px 20px; }
.qe-val-start { font-size: var(--fs-data); color: var(--text-2); line-height: 1.5; }
.qe-val-start b { color: var(--text-num); font-family: var(--mono); font-weight: 500; }
.qe-val-start ul { margin: 4px 0 0 18px; padding: 0; }
</style>"""

SCENARIOS = ["Pessimista", "Base", "Ottimista"]

EDIT_LEVERS = ["growth", "target_margin", "margin_years", "sales_to_capital", "wacc", "terminal_wacc",
               "terminal_growth", "terminal_roic", "tax_rate", "failure_prob"]


def _valuation_tab(d: DashboardData, ticker: str):
    st.html(VALUATION_CSS)
    with st.container():  # la riga fissa del risultato si ferma alla fine di questo blocco
        _scenario_valuation(d, ticker)
    st.divider()
    _multiples_history(d, ticker)
    with st.expander("Test LBO: quanto pagherebbe un fondo di private equity"):
        _lbo_panel(d, ticker)
    with st.expander("DCF rapido sul free cash flow (il modello semplice)"):
        _user_dcf(d, ticker)
    with st.expander("Fair value del sistema nel tempo (DCF automatico, ricalcolato ogni mese)"):
        if d.valuation(ticker)["dcf_skipped_sector"]:
            empty_state("Nessuna storia del fair value", "Banche e assicurazioni sono escluse dal DCF automatico.")
        else:
            _fair_value_history(d, ticker)


def lbo_items(r, currency: str = "USD", target: float = 0.20) -> list[tuple[str, str]]:
    """Riga di risultato del test LBO: rendimento del fondo al prezzo di oggi, prezzo massimo, margine."""
    return [("Rendimento del fondo", fmt_html(r.irr, "pct")),
            (f"Prezzo massimo al {fmt(target, 'pct', digits=0)}", fmt_html(r.max_price, "price", currency=currency)),
            ("Rispetto al prezzo", fmt_html(r.upside, "pct", arrow=True)),
            ("Prezzo", fmt_html(r.price, "price", currency=currency)),
            ("Debito / EBITDA", fmt_html(r.new_debt / r.ebitda if r.ebitda else None, "mult")),
            ("Uscita a", fmt_html(r.exit_multiple, "mult"))]


def lbo_years_frame(r) -> pd.DataFrame:
    return pd.DataFrame([{"anno": y["anno"], "inizio": y["debito_inizio"], "interessi": y["interessi"],
                          "flusso": y["flusso_levered"], "fine": y["debito_fine"]} for y in r.years])


def _lbo_panel(d: DashboardData, ticker: str):
    from modules import lbo as lbo_mod

    r = d.lbo(ticker)
    if r is None or not r.ok:
        empty_state("Test LBO non disponibile", r.reason if r is not None else "Nessun prezzo per questo titolo.")
        return
    p = lbo_mod.settings(d.cfg)
    cur = d.currency(ticker)
    st.html(result_row_html(lbo_items(r, cur, p["target_irr"]), title="Test LBO",
                            note=f"Il fondo paga il prezzo + {fmt(p['premium'], 'pct', digits=0)}, si indebita al "
                                 f"{fmt(r.rate, 'pct')} e usa tutta la cassa per ripagare il debito per "
                                 f"{p['years']} anni; EBITDA fermo, uscita al multiplo di oggi."))
    if d.info(ticker).get("sector") in lbo_mod.CAUTION_SECTORS:
        notice("Settore finanziario o immobiliare: per assicurazioni, banche e REIT il debito e la cassa non si leggono "
               "come in un'industria e il test non ha senso; per broker, borse, gestori e pagamenti sì.", "warn")
    st.caption("Lettura: un rendimento alto vuol dire che la cassa prodotta e il debito che l'azienda regge "
               "basterebbero a un compratore finanziario anche pagando un premio: è il \"pavimento\" del prezzo usato "
               "dalle banche d'affari. Un rendimento basso NON vuol dire che l'azienda sia cara: chi cresce vale più "
               "di quanto un LBO senza crescita riesca a pagare. Non entra nel giudizio.")
    sources = pd.DataFrame([
        {"voce": "Equity comprata (prezzo + premio)", "importo": r.purchase_equity},
        {"voce": "Debito esistente - cassa (rifinanziato)", "importo": r.refinanced_debt},
        {"voce": "Totale impieghi", "importo": r.uses},
        {"voce": "Nuovo debito", "importo": r.new_debt},
        {"voce": "Capitale del fondo", "importo": r.sponsor_equity},
        {"voce": "Equity del fondo all'uscita", "importo": r.exit_equity}])
    c1, c2 = st.columns(2)
    with c1:
        dense_table(sources, {"voce": ("Fonti e impieghi", "text"), "importo": ("Importo", "money")},
                    key_col="voce")
    with c2:
        dense_table(lbo_years_frame(r), {"anno": ("Anno", "int"), "inizio": ("Debito inizio", "money"),
                                         "interessi": ("Interessi", "money"), "fine": ("Debito fine", "money")},
                    key_col="anno")
    st.caption("Il foglio \"LBO\" del modello Excel (Esporta) ha le stesse formule, con le ipotesi da cambiare.")


# ================================================================== leve
def _lever_label(k: str) -> str:
    return vm.LEVER_LABELS[k] + (" (%)" if k in vm.PCT_LEVERS else "")


def _levers_frame(scenarios: dict, probabilities: dict) -> pd.DataFrame:
    rows = {}
    for k in EDIT_LEVERS:
        rows[_lever_label(k)] = [float(getattr(scenarios[n], k)) * (100 if k in vm.PCT_LEVERS else 1)
                                 for n in SCENARIOS]
    rows["Probabilità (%)"] = [float(probabilities.get(n, 0)) * 100 for n in SCENARIOS]
    return pd.DataFrame(rows, index=SCENARIOS).T


def _parse_levers(frame: pd.DataFrame) -> tuple[dict, dict]:
    scenarios, probs = {}, {}
    for n in SCENARIOS:
        values = {}
        for k in EDIT_LEVERS:
            v = float(frame.at[_lever_label(k), n])
            if not np.isfinite(v):
                raise ValueError(f"valore mancante: {_lever_label(k)}, {n}")
            values[k] = v / 100 if k in vm.PCT_LEVERS else v
        values["margin_years"] = int(max(1, round(values["margin_years"])))
        scenarios[n] = vm.Levers(**values)
        probs[n] = max(float(frame.at["Probabilità (%)", n]), 0.0) / 100
    return scenarios, probs


@st.cache_data(show_spinner=False)
def _simulation(base_json: str, levers_json: str) -> dict:
    base = vm.Base(**json.loads(base_json))
    lv = {n: vm.Levers(**x) for n, x in json.loads(levers_json).items()}
    return vm.simulate(base, lv["Pessimista"], lv["Base"], lv["Ottimista"])


# ================================================================== presentazione (funzioni pure, testate)
def _finite(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def valuation_result_items(summary: dict, price: float, probs: dict, sim: dict | None, implied, currency: str = "USD"
                           ) -> list[tuple[str, str]]:
    """Coppie (etichetta, HTML) della riga di risultato: valore atteso, margine sul prezzo, intervallo 10°-90° della
    simulazione, i tre scenari con la loro probabilità, prezzo, crescita implicita nel prezzo."""
    ev = _finite(summary.get("expected"))
    px = _finite(price)
    total = sum(probs.values()) or 1.0
    items = [("Valore per azione", fmt_html(ev, "price", currency=currency)),
             ("Margine sul prezzo", fmt_html(ev / px - 1 if ev is not None and px else None, "pct", arrow=True))]
    if sim:
        lo, hi = _finite(sim.get("p10")), _finite(sim.get("p90"))
        rng = (f'{fmt(lo, "price", digits=0)} – {fmt(hi, "price", digits=0, currency=currency)}'
               if lo is not None and hi is not None else None)
        items.append(("Intervallo 10°–90°", rng or fmt_html(None)))
        items.append(("Sopra il prezzo", fmt_html(sim.get("prob_above_price"), "pct", digits=0)))
    for n in SCENARIOS:
        v = summary.get("per_scenario", {}).get(n)
        items.append((f"{n} {fmt(probs.get(n, 0) / total, 'pct', digits=0)}", fmt_html(v, "price", currency=currency)))
    items.append(("Prezzo", fmt_html(px, "price", currency=currency)))
    items.append(("Crescita implicita", fmt_html(implied, "pct") if implied is not None else fmt_html(None)))
    return items


def outside_range_note(summary: dict, sim: dict | None) -> str:
    """Frase in più quando il valore pesato cade fuori dall'intervallo 10°-90° della simulazione (collaudo 2026-11-16:
    368 $ con intervallo 241-352 $ sembrava un errore). Vuota se dentro o se manca qualcosa."""
    ev = _finite(summary.get("expected"))
    lo, hi = (_finite((sim or {}).get("p10")), _finite((sim or {}).get("p90")))
    if ev is None or lo is None or hi is None or lo <= ev <= hi:
        return ""
    return ("Il valore è " + ("sopra" if ev > hi else "sotto") + " l'intervallo: la simulazione muove solo le quattro "
            "leve principali, insieme e con il caso base più probabile, mentre la media pesata dà tutto il peso "
            "scelto a uno scenario estremo. ")


def explain_check(msg: str) -> str:
    """Avvisi del modello resi comprensibili: oltre il 100% del valore dopo il decimo anno vuol dire che i flussi dei
    primi dieci anni, scontati, sono negativi (si investe più di quanto si incassa)."""
    m = re.search(r"Il (\d+)% del valore viene da dopo il decimo anno", msg)
    if m and int(m.group(1)) > 100:
        return msg + " Oltre il 100%: nei primi dieci anni l'azienda investe più di quanto incassa (flussi negativi)."
    return msg


PLAUS_RATIO_HINT = "Ricavi per unità di capitale investito"


def plausibility_view(plaus: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """La tabella "È plausibile?" con valori già formattati (percentuali come percentuali, il rapporto ricavi/capitale
    come numero, "–" per i mancanti) e se c'è almeno un confronto col settore."""
    if plaus is None or plaus.empty:
        return pd.DataFrame(columns=["Leva", "La tua ipotesi", "Settore 10°", "Mediana", "Settore 90°", "Posizione",
                                     "Giudizio"]), False
    rows, compared = [], False
    for _, r in plaus.iterrows():
        label = str(r.get("lever", ""))
        kind = ("num", {"digits": 2}) if label.startswith(PLAUS_RATIO_HINT) else ("pct", {})
        has = _finite(r.get("p50")) is not None
        if not has and "troppo piccolo" in str(r.get("verdict", "")):
            continue
        compared = compared or (has and not label.startswith("Margine obiettivo vs"))
        rows.append({"Leva": label, "La tua ipotesi": fmt(r.get("value"), kind[0], **kind[1]),
                     "Settore 10°": fmt(r.get("p10"), kind[0], **kind[1]),
                     "Mediana": fmt(r.get("p50"), kind[0], **kind[1]),
                     "Settore 90°": fmt(r.get("p90"), kind[0], **kind[1]),
                     "Posizione": fmt(r.get("percentile"), "pct", digits=0),
                     "Giudizio": it_text(str(r.get("verdict") or NA))})
    return pd.DataFrame(rows), compared


def cost_of_capital_rows(c: dict) -> pd.DataFrame:
    """Passi del costo del capitale come (Passo, Valore) già in italiano."""
    def p2(x):
        return fmt(x, "pct", digits=2)

    cov = c.get("coverage")
    coverage = ("nessun debito" if (cov is None or not np.isfinite(cov)) and (c.get("debt") or 0) <= 0 else
                fmt(cov, "mult") if cov is not None and np.isfinite(cov) else "ignota")
    rows = [
        ("Tasso privo di rischio (Treasury 10 anni)", p2(c.get("risk_free"))),
        ("Premio per il rischio azionario implicito" if c.get("erp_computed") else
         "Premio per il rischio (valore delle impostazioni: implicito non calcolabile)", p2(c.get("erp"))),
        (f"Beta senza debito del gruppo «{c.get('group') or 'tutte'}» ({c.get('peers')} aziende, corretto per la "
         "cassa)", fmt(c.get("beta_unlevered"), "num")),
        (f"Beta di regressione del titolo contro {c.get('beta_benchmark') or 'il mercato'} (solo confronto: "
         "rumoroso)", fmt(c.get("beta_regression"), "num")),
        ("Beta riadattato al debito dell'azienda", fmt(c.get("beta"), "num")),
        ("Costo dell'equity = privo di rischio + beta × premio", p2(c.get("cost_of_equity"))),
        ("Copertura degli interessi (EBIT / interessi)", coverage),
        ("Rating sintetico e spread", f"{c.get('rating') or NA}, {fmt(c.get('spread'), 'pct', digits=2, signed=True)}"),
        ("Costo del debito prima / dopo le tasse",
         f"{p2(c.get('cost_of_debt'))} / {p2(c.get('cost_of_debt_after_tax'))} (aliquota marginale "
         f"{fmt(c.get('marginal_tax'), 'pct', digits=0)})"),
        ("Pesi di mercato: equity / debito",
         f"{fmt(c.get('weight_equity'), 'pct', digits=0)} / {fmt(c.get('weight_debt'), 'pct', digits=0)}"),
        ("Costo del capitale (WACC)", p2(c.get("wacc"))),
        ("Costo del capitale da azienda matura (beta 1), proposto come finale", p2(c.get("mature_wacc"))),
    ]
    return pd.DataFrame(rows, columns=["Passo", "Valore"])


def _cost_of_capital_panel(c: dict):
    with st.expander(f"Costo del capitale passo per passo: {fmt(c.get('wacc'), 'pct', digits=2)} (anni 1-5)"):
        dense_table(cost_of_capital_rows(c), {"Passo": ("Passo", "text"), "Valore": ("Valore", "text")})
        st.caption("Beta di settore, rating sintetico e premio implicito come li calcola Damodaran. Il premio implicito "
                   "è il rendimento che uguaglia il valore dell'S&P 500 a dividendi e riacquisti attesi.")


def _start_html(base, setup: dict, currency: str) -> str:
    ic = base.revenue / base.invested_capital if base.invested_capital else None
    parts = [f"ricavi <b>{fmt(base.revenue, 'money', currency=currency)}</b>",
             f"margine operativo <b>{fmt(base.margin, 'pct')}</b>",
             f"ricavi per unità di capitale investito <b>{fmt(ic, 'num')}</b>",
             f"debito netto <b>{fmt(base.debt - base.cash, 'money', currency=currency)}</b>",
             f"prezzo <b>{fmt(base.price, 'price', currency=currency)}</b>",
             f"tasso privo di rischio <b>{fmt(base.risk_free, 'pct', digits=2)}</b>"]
    import html as _h

    notes = "".join(f"<li>{_h.escape(it_text(n))}</li>" for n in setup["notes"] + setup["adjustments"])
    return (f'<div class="qe-val-start">Punto di partenza (ultimi 12 mesi): {", ".join(parts)}.'
            f'{f"<ul>{notes}</ul>" if notes else ""}</div>')


# ================================================================== valutazione a leve
def _scenario_valuation(d: DashboardData, ticker: str):
    cur = d.currency(ticker) if hasattr(d, "currency") else "USD"
    st.subheader("Valutazione a leve: dalla storia ai numeri")
    if msg := st.session_state.pop(f"val_saved_{ticker}", None):
        st.toast(msg, icon=":material/check:")
    fresh_key = f"levers_fresh_{ticker}"
    o1, o2, o3 = st.columns([2, 2, 1], vertical_alignment="center")
    use_rnd = o1.checkbox("R&S come investimento", True, key=f"opt_rnd_{ticker}",
                          help="Damodaran: la R&S crea un'attività che dura anni. Si toglie dai costi, si ammortizza "
                               "(3 anni per tecnologia, 10 per farmaceutica, 5 negli altri casi) e si aggiunge al "
                               "capitale investito. Si applica se la R&S supera il 3% dei ricavi.")
    use_leases = o2.checkbox("Leasing operativi come debito", True, key=f"opt_lease_{ticker}",
                             help="Gli affitti di lungo periodo sono un debito: si aggiungono al debito e al capitale "
                                  "investito, e la quota interessi della rata torna nell'utile operativo.")
    editor_key = f"levers_{ticker}"
    if o3.button("Valori proposti", width="stretch", key=f"lev_reset_{ticker}",
                 help="Torna alle leve proposte dal sistema (la valutazione salvata resta nel diario)"):
        st.session_state.pop(editor_key, None)
        st.session_state[fresh_key] = True
        st.rerun()
    with st.spinner("Preparo il punto di partenza…"):
        setup = d.valuation_setup(ticker, use_saved=not st.session_state.get(fresh_key, False), rnd=use_rnd,
                                  leases=use_leases)
    if setup is None:
        empty_state("Valutazione non disponibile", "Mancano ricavi, azioni o prezzo dell'azienda. Si possono "
                    "inserire a mano nella pagina Inserisci dati.")
        return
    base = setup["base"]
    if d.info(ticker).get("sector") in set(d.cfg["valuation"].get("skip_sectors", [])):
        notice("margine operativo e reinvestimento descrivono male banche e assicurazioni: usa questa valutazione "
               "con cautela e guarda soprattutto P/patrimonio tangibile e ROTCE.", "warn", title="Settore finanziario:")

    # segnaposti: il risultato (fisso in cima) e gli avvisi dei controlli, riempiti dopo il calcolo
    result_slot = st.container(key="dcf_result")
    checks_slot = st.container()

    with st.expander("Punto di partenza e costo del capitale"):
        st.html(_start_html(base, setup, cur))
    _cost_of_capital_panel(setup["cost_of_capital"])
    research = d.research(ticker)
    story = st.text_area("La storia in breve: che azienda sarà tra 10 anni?", research.get("story") or "",
                         key=f"story_{ticker}", height=80,
                         placeholder="es. Resta leader nel suo mercato, cresce come l'economia più qualcosa grazie "
                                     "ai prezzi, i margini restano alti perché i clienti non cambiano fornitore…")
    st.caption("Le leve di ogni scenario: il caso base proposto è l'azienda di oggi; " +
               ("ripartono dalla tua ultima valutazione salvata." if setup["saved"] else
                "pessimista e ottimista sono spostati in modo simmetrico. Cambiale secondo la tua storia: il "
                "risultato in alto si aggiorna."))
    frame = _levers_frame(setup["scenarios"], setup["probabilities"])
    edited = st.data_editor(frame, key=editor_key, width="stretch", height=table_height(len(frame), 12),
                            column_config={"_index": st.column_config.TextColumn("Leva", width="large"),
                                           **{n: num_column(n, "num", digits=2) for n in SCENARIOS}})
    try:
        scenarios, probs = _parse_levers(edited)
    except (ValueError, TypeError, KeyError):
        with result_slot:
            empty_state("Leve non valide", "Ogni cella della tabella delle leve deve contenere un numero.")
        return
    total_p = sum(probs.values())

    with st.spinner("Calcolo la valutazione…"):
        results = {n: vm.value(base, lv, with_table=True) for n, lv in scenarios.items()}
        summary = vm.scenario_values(base, scenarios, probs)
        implied = vm.implied_growth(base, scenarios["Base"])
        sim = _simulation(json.dumps(asdict(base)), json.dumps({n: lv.to_dict() for n, lv in scenarios.items()}))
    issues = [(n, lvl, explain_check(it_text(msg))) for n, r in results.items() for lvl, msg in r.checks]

    note = ("Valore = media dei tre scenari pesata con le probabilità; intervallo dalla simulazione. "
            + outside_range_note(summary, sim)
            + ("Crescita implicita: crescita dei ricavi (anni 1-5) che giustifica il prezzo di oggi." if implied is not None
               else "Crescita implicita: nessuna tra −10% e +60% giustifica il prezzo di oggi."))
    with result_slot:
        st.html(result_row_html(valuation_result_items(summary, base.price, probs, sim, implied, cur),
                                title="Risultato della tua valutazione", note=note))
    with checks_slot:
        if abs(total_p - 1) > 0.001:
            notice(f"le probabilità sommano {fmt(total_p, 'pct', digits=0)}: le riporto al 100% in proporzione.",
                   "info", title="Probabilità:")
        for n, lvl, msg in issues:
            notice(msg, "neg" if lvl == "errore" else "warn", title=f"{n}:")

    st.markdown("**È plausibile?** Il caso base confrontato con le aziende dello stesso settore")
    view, compared = plausibility_view(d.plausibility(ticker, scenarios["Base"]))
    if not compared:
        st.html('<div class="qe-empty-line">Confronto col settore non disponibile: meno di 5 aziende dello stesso '
                'settore con dati sufficienti.</div>')
    if not view.empty:
        dense_table(view, {"Leva": ("Leva", "text"), "La tua ipotesi": ("La tua ipotesi", "html"),
                           "Settore 10°": ("Settore 10°", "html"), "Mediana": ("Mediana", "html"),
                           "Settore 90°": ("Settore 90°", "html"), "Posizione": ("Posizione", "html"),
                           "Giudizio": ("Giudizio", "text")}, key_col="Leva")

    st.markdown("**Simulazione**: le quattro leve principali variano tra pessimista e ottimista (più probabile il "
                "caso base), 5.000 valutazioni")
    left, right = st.columns(2)
    with left:
        fig = go.Figure(go.Histogram(x=sim["values"], nbinsx=50, marker=dict(color=SERIES[0]),
                                     hovertemplate="%{x:,.0f}: %{y} casi<extra></extra>"))
        fig.add_vline(x=base.price, line=dict(color=MUTED, dash="dash"), annotation_text="prezzo")
        fig.update_xaxes(title=f"Valore per azione ({cur})")
        fig.update_yaxes(title="Casi")
        st.plotly_chart(_layout(fig, 300, legend=False).update_layout(hovermode="closest"), width="stretch")
        st.caption(f"Nel {fmt(sim['prob_above_price'], 'pct', digits=0)} dei casi il valore supera il prezzo di oggi; "
                   f"mediana {fmt(sim['p50'], 'price', digits=0, currency=cur)}. Le leve variano in modo indipendente: "
                   "nella realtà crescita e margini spesso si muovono insieme.")
    with right:
        names = [vm.LEVER_LABELS[k] for k in sim["swing"]]
        lows = [v[0] for v in sim["swing"].values()]
        highs = [v[1] for v in sim["swing"].values()]
        mid = summary["per_scenario"]["Base"]
        fig = go.Figure()
        fig.add_trace(go.Bar(y=names, x=[lo - mid for lo in lows], base=mid, orientation="h", name="verso il pessimista",
                             marker=dict(color=SERIES[1], cornerradius=4), hovertemplate="%{x:,.0f}<extra></extra>"))
        fig.add_trace(go.Bar(y=names, x=[hi - mid for hi in highs], base=mid, orientation="h", name="verso l'ottimista",
                             marker=dict(color=SERIES[0], cornerradius=4), hovertemplate="%{x:,.0f}<extra></extra>"))
        fig.update_layout(barmode="overlay")
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(title=f"Valore per azione ({cur})")
        st.plotly_chart(_layout(fig, 300).update_layout(hovermode="closest"), width="stretch")
        st.caption("Quanto cambia il valore muovendo una leva alla volta dal caso base: in alto quella che conta di "
                   "più, dove conviene concentrare la ricerca.")
    with st.expander("Anno per anno (caso base)"):
        t = results["Base"].table
        money_cols = ("ricavi", "ebit", "nopat", "reinvestimento", "fcff", "valore_attuale", "capitale_investito")
        labels = {"anno": "Anno", "crescita": "Crescita", "ricavi": "Ricavi", "margine": "Margine", "ebit": "EBIT",
                  "nopat": "NOPAT", "reinvestimento": "Reinvestimento", "fcff": "FCFF", "costo_capitale": "Costo cap.",
                  "valore_attuale": "Valore attuale", "capitale_investito": "Capitale investito", "roic": "ROIC"}
        spec = {}
        for c in t.columns:
            kind = ("pct" if c in ("crescita", "margine", "costo_capitale", "roic") else
                    "money" if c in money_cols else "text" if t[c].dtype == object else
                    "int" if pd.api.types.is_integer_dtype(t[c]) else "num")
            spec[c] = (labels.get(c, c.replace("_", " ").capitalize()), kind)
        dense_table(t, spec, key_col="anno")
        st.caption(f"Il {fmt(results['Base'].tv_share, 'pct', digits=0)} del valore viene dal valore finale (dopo il "
                   "decimo anno).")

    has_error = any(lvl == "errore" for _, lvl, _ in issues)
    with st.form(f"val_save_{ticker}", border=False):
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        vnote = c1.text_input("Nota per il diario (facoltativa)", key=f"vnote_{ticker}",
                              placeholder="es. dopo il 10-Q di ottobre, margini più bassi del previsto")
        save = c2.form_submit_button("Salva nel diario", type="primary", width="stretch", disabled=has_error,
                                     help="Il valore atteso diventa anche il tuo fair value (testata, home, avvisi)."
                                     if not has_error else "Correggi prima gli errori segnalati sotto il risultato.")
    if save:
        entry = d.save_valuation(ticker, setup, scenarios, probs, story, vnote, sim)
        st.session_state.pop(fresh_key, None)
        if story and story != (research.get("story") or ""):
            d.save_research(ticker, {**{k: research.get(k) for k in d.db.RESEARCH_FIELDS}, "story": story})
        st.session_state[f"val_saved_{ticker}"] = (
            f"Valutazione salvata nel diario: {fmt(entry['expected_value'], 'price', currency=cur)} per azione, "
            "ora è il tuo fair value.")
        st.rerun()


# ================================================================== DCF rapido dell'utente
DCF_FIELDS = {  # chiave: (etichetta, minimo, massimo, passo, in percentuale)
    "growth": ("Crescita annua all'inizio", -10.0, 40.0, 0.5, True),
    "wacc": ("Tasso di sconto (WACC)", 4.0, 15.0, 0.1, True),
    "terminal_growth": ("Crescita per sempre (dopo il periodo)", 0.0, 4.0, 0.1, True),
}


def _dcf_reset(ticker: str, values: dict):
    st.session_state[f"dcf_{ticker}_fcf"] = float(values["fcf"] or 0) / 1e9
    st.session_state[f"dcf_{ticker}_years"] = int(values["years"])
    for k, (_, lo, hi, step, is_pct) in DCF_FIELDS.items():
        v = float(values[k] or 0) * (100 if is_pct else 1)
        st.session_state[f"dcf_{ticker}_{k}"] = _on_grid(min(max(v, lo), hi), lo, step)


def _on_grid(v: float, lo: float, step: float) -> float:
    """Il valore sulla griglia del cursore (le opzioni di select_slider devono coincidere)."""
    return round(lo + round((v - lo) / step) * step, 2)


def _grid(lo: float, hi: float, step: float) -> list[float]:
    return [round(lo + i * step, 2) for i in range(int(round((hi - lo) / step)) + 1)]


def sensitivity_view(grid: pd.DataFrame, price: float) -> pd.DataFrame:
    """Griglia di sensibilità in italiano: righe "WACC 7,5%", colonne "crescita 6,0%", celle "123 ▲" (sopra il
    prezzo) / "98 ▼" (sotto): il verso è scritto, non solo colorato."""
    def cell(v):
        x = _finite(v)
        if x is None:
            return NA
        return f"{fmt(x, 'num', digits=0)} {'▲' if x > price else '▼'}"

    out = pd.DataFrame([[cell(v) for v in row] for row in grid.to_numpy()],
                       index=[f"WACC {fmt(w, 'pct')}" for w in grid.index],
                       columns=[f"crescita {fmt(g, 'pct')}" for g in grid.columns])
    return out


def sensitivity_html(view: pd.DataFrame) -> str:
    """La griglia di sensibilità come tabella fitta: ▲ verde sopra il prezzo, ▼ rosso sotto (simbolo + colore)."""
    import html as _h

    def td(v: str) -> str:
        if v == NA:
            return f'<td class="na">{NA}</td>'
        tone = "pos" if v.endswith("▲") else "neg"
        return f'<td class="n"><span class="{tone}">{_h.escape(v)}</span></td>'

    head = "<th></th>" + "".join(f'<th class="n">{_h.escape(c)}</th>' for c in view.columns)
    body = "".join(f'<tr><td class="t">{_h.escape(i)}</td>{"".join(td(v) for v in row)}</tr>'
                   for i, row in zip(view.index, view.to_numpy()))
    return (f'<div class="qe-tablewrap"><table class="qe-dense"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _user_dcf(d: DashboardData, ticker: str):
    cur = d.currency(ticker) if hasattr(d, "currency") else "USD"
    if msg := st.session_state.pop(f"dcf_saved_{ticker}", None):
        st.toast(msg, icon=":material/check:")
    sector = d.info(ticker).get("sector")
    if sector in set(d.cfg["valuation"].get("skip_sectors", [])):
        notice("per banche e assicurazioni il free cash flow non misura la capacità di generare utili: qui il DCF "
               "ha poco senso.", "warn", title="Settore finanziario:")
    inp = d.dcf_inputs(ticker)
    if inp["shares"] is None or not inp["price"] or not np.isfinite(inp["price"]):
        empty_state("DCF non calcolabile", "Mancano il numero di azioni o il prezzo.")
        return
    if f"dcf_{ticker}_wacc" not in st.session_state:
        _dcf_reset(ticker, {k: (v if v is not None else d.cfg["valuation"]["default_growth"] if k == "growth"
                                else 0.08 if k == "wacc" else v) for k, v in inp["current"].items()})
    top = st.container()
    left, right = st.columns([2, 3])
    with left:
        fcf = st.number_input(f"Free cash flow di partenza (miliardi, {cur})", step=0.1, format="%.2f",
                              key=f"dcf_{ticker}_fcf",
                              help="Proposta del sistema: media degli ultimi 3 anni = "
                                   f"{fmt(inp['system']['fcf'], 'money', currency=cur)}")
        values = {}
        for k, (label, lo, hi, step, _) in DCF_FIELDS.items():
            key = f"dcf_{ticker}_{k}"
            if st.session_state.get(key) not in _grid(lo, hi, step):  # valore vecchio fuori griglia
                st.session_state[key] = _on_grid(min(max(float(st.session_state.get(key) or lo), lo), hi), lo, step)
            values[k] = st.select_slider(label, _grid(lo, hi, step), key=key,
                                         format_func=lambda v: fmt(v / 100, "pct")) / 100
        years = st.slider("Anni di previsione", 5, 20, key=f"dcf_{ticker}_years",
                          help="La crescita scende in linea retta da quella iniziale a quella \"per sempre\".")
        b1, b2 = st.columns(2)
        save = b1.button("Salva come mio fair value", type="primary", width="stretch", key=f"dcf_save_{ticker}")
        b2.button("Valori del sistema", width="stretch", on_click=_dcf_reset, args=(ticker, inp["system"]),
                  key=f"dcf_sys_{ticker}",
                  help="Rimette le ipotesi del DCF automatico (non cancella la valutazione salvata).")
    res = d.user_dcf(ticker, fcf * 1e9, values["growth"], values["wacc"], values["terminal_growth"], years,
                     inp["net_debt"] or 0.0, inp["shares"], inp["price"])
    if res["error"]:
        with top:
            empty_state("DCF non calcolabile con queste ipotesi", it_text(str(res["error"])))
        return
    ig = res["implied_growth"]
    with top:
        st.html(result_row_html([
            ("Fair value per azione", fmt_html(res["fair_value"], "price", currency=cur)),
            ("Prezzo", fmt_html(inp["price"], "price", currency=cur)),
            ("Margine di sicurezza", fmt_html(res["margin"], "pct", arrow=True)),
            ("Crescita implicita", fmt_html(ig, "pct") if ig is not None else fmt_html(None)),
        ], title="Il tuo DCF", note="Margine = fair value / prezzo − 1 (positivo: il prezzo è sotto la tua stima). "
                                    "Crescita implicita: quella iniziale che il prezzo sconta con questo tasso di "
                                    "sconto."))
    with right:
        st.caption(f"Debito netto {fmt(inp['net_debt'] or 0, 'money', currency=cur)} · "
                   f"{fmt(inp['shares'], 'shares')} di azioni. Fair value = valore attuale dei flussi futuri + valore "
                   "finale, meno il debito netto, diviso per le azioni.")
        st.markdown("**Sensibilità**: fair value cambiando tasso di sconto e crescita iniziale")
        price = inp["price"]
        grid = res["grid"]
        view = sensitivity_view(grid, price)
        st.html(sensitivity_html(view))
        st.caption(f"▲ sopra il prezzo di oggi ({fmt(price, 'price', currency=cur)}), ▼ sotto. Al centro le tue "
                   "ipotesi.")
    if save:
        d.save_note(ticker, fair_value=float(res["fair_value"]),
                    assumptions={"fcf": fcf * 1e9, "years": years, **values})
        st.session_state[f"dcf_saved_{ticker}"] = (f"Salvato: il tuo fair value è "
                                                   f"{fmt(res['fair_value'], 'price', currency=cur)}.")
        st.rerun()
    elif inp["saved"]:
        st.caption("Ipotesi caricate dalla tua ultima valutazione salvata.")


# ================================================================== multipli nel tempo
@st.cache_data(show_spinner="Ricostruisco i multipli degli ultimi 10 anni…")
def _multiples(_d: DashboardData, ticker: str, key: str) -> pd.DataFrame:  # noqa: ARG001
    return _d.historical_multiples(ticker)


def _multiples_history(d: DashboardData, ticker: str):
    st.subheader("Multipli nel tempo")
    hist = _multiples(d, ticker, d.run_id or "")
    available = [k for k in HISTORY_MULTIPLES if k in hist and hist[k].notna().sum() >= 12]
    if not available:
        empty_state("Multipli nel tempo non disponibili", "Servono almeno 12 mesi di bilanci e prezzi per questa "
                    "azienda.")
        return
    key = st.selectbox("Multiplo", available, format_func=lambda k: METRIC_BY_KEY[k].label, key="multiple_pick")
    s = hist[key].dropna()
    median, now = float(s.median()), float(s.iloc[-1])
    position = float((s < now).mean())
    is_pct = METRIC_BY_KEY[key].fmt == "pct"
    fig = go.Figure()
    _line(fig, s.index, s, METRIC_BY_KEY[key].label, SERIES[0])
    fig.add_hline(y=median, line=dict(color=MUTED, width=1, dash="dash"),
                  annotation_text=f"mediana {fmt_metric(key, median)}", annotation_position="top left")
    fig.update_yaxes(tickformat=".1%" if is_pct else None)
    st.plotly_chart(_layout(fig, 320, legend=False), width="stretch")
    better = METRIC_BY_KEY[key].better
    cheap = (position < 0.5) if better == "low" else (position > 0.5)
    st.caption(f"Oggi {fmt_metric(key, now)}, mediana degli ultimi 10 anni {fmt_metric(key, median)}: più alto del "
               f"{fmt(position, 'pct', digits=0)} dei mesi passati"
               + (f" ({'più economico' if cheap else 'più caro'} della sua media storica)." if better else ".")
               + " Ogni punto usa i bilanci già depositati a quella data.")
