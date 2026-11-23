"""Pagina "Strategie": strategie a regole (catalogo standard, grandi investitori, create dall'utente), backtest sulle
due metà, cosa terrebbero oggi, costruttore senza codice. Numeri e tabelle con `ui/components`; le cautele
statistiche restano visibili come bande compatte (`notice`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import run_info, screener, significance, strategies
from modules.backtester import yearly_returns
from modules.dashboard_data import DashboardData
from modules.metrics import JUDGMENT_KEYS, METRICS, METRIC_BY_KEY
from ui.common import MUTED, SERIES, _layout, _line, data, md
from ui.components import (
    dense_table, empty_state, fmt, fmt_html, notice, num_column, panel, plural, result_row_html, table_height,
)
from ui.rules import _rules_add, _rules_set, _rules_widgets


# ---------------------------------------------------------------- strategie
SB_PREFIX = ("sb_", "sbr_", "sk_", "skr_")


def _sb_load(definition: dict, editing: str | None = None, switch: bool = True):
    """Carica una definizione nel costruttore e (con `switch`) passa alla vista del costruttore."""
    d = {**strategies.default_definition(), **definition}
    for k in [k for k in st.session_state if str(k).startswith(SB_PREFIX)]:
        del st.session_state[k]
    p = d["portfolio"]
    st.session_state.update({
        "sb_name": d.get("name", ""), "sb_desc": d.get("description", ""),
        "sb_sectors": list(d["universe"].get("sectors") or []), "sb_watch": bool(d["universe"].get("watchlist_only")),
        "sb_positions": int(p["positions"]), "sb_weighting": p["weighting"],
        "sb_maxw": float(p["max_weight"]) * 100, "sb_maxsec": float(p["max_sector"]) * 100,
        "sb_rebal": int(p["rebalance_days"]), "sb_editing": editing,
    })
    if switch:
        st.session_state["st_view"] = "Crea o modifica"
    _rules_set("sb", d.get("filters", []))
    st.session_state["sk_rank"] = [{"id": i, **r} for i, r in enumerate(d.get("ranking", []))]
    st.session_state["sk_next"] = len(st.session_state["sk_rank"])


def _sb_definition() -> dict:
    ss = st.session_state
    return {
        "name": ss.get("sb_name", "").strip(), "description": ss.get("sb_desc", "").strip(),
        "universe": {"sectors": list(ss.get("sb_sectors", [])), "watchlist_only": bool(ss.get("sb_watch"))},
        "filters": ss.get("sb_current_filters", []),
        "ranking": ss.get("sb_current_ranking", []),
        "portfolio": {"positions": int(ss.get("sb_positions", 30)), "weighting": ss.get("sb_weighting", "equal"),
                      "max_weight": float(ss.get("sb_maxw", 10)) / 100, "max_sector": float(ss.get("sb_maxsec", 30)) / 100,
                      "rebalance_days": int(ss.get("sb_rebal", 63))},
    }


def _sb_save(d: DashboardData):
    definition = _sb_definition()
    try:
        name = d.save_user_strategy(definition, st.session_state.get("sb_editing"))
    except ValueError as exc:
        st.session_state["sb_error"] = str(exc)
        return
    st.session_state.update({"st_sel": name, "st_view": "Risultati", "sb_editing": name,
                             "flash": f"Strategia \"{name}\" salvata."})


def _rank_add():
    i = st.session_state.get("sk_next", 0)
    st.session_state["sk_rank"].append({"id": i, "metric": "roic", "weight": 1.0})
    st.session_state["sk_next"] = i + 1


def _rank_remove(rank_id: int):
    st.session_state["sk_rank"] = [r for r in st.session_state["sk_rank"] if r["id"] != rank_id]


def _sb_load_filters(saved: dict):
    choice = st.session_state.get("sb_filter_src")
    if choice in screener.PRESETS:
        _rules_set("sb", screener.PRESETS[choice]["rules"])
    elif choice in saved:
        _rules_set("sb", saved[choice]["rules"])
        st.session_state["sb_sectors"] = list(saved[choice]["sectors"])


def page_strategies():
    d = data()
    st.title("Strategie")
    if "flash" in st.session_state:
        st.toast(st.session_state.pop("flash"), icon=":material/check:")
    if not st.session_state.get("st_view"):
        st.session_state["st_view"] = "Risultati"
    view = st.segmented_control("Vista", ["Risultati", "Crea o modifica"], key="st_view",
                                label_visibility="collapsed") or "Risultati"
    if view == "Crea o modifica":
        _strategy_builder(d)
    else:
        _strategy_results(d)


def _run_both(d: DashboardData, name: str):
    with st.spinner("Backtest in corso…", show_time=True):
        d.strategy_result(name, run=True)
        d.strategy_result(strategies.BASELINE, run=True)
    st.rerun()


def _strategy_results(d: DashboardData):
    catalog, mine = d.strategy_catalog(), d.user_strategies()
    names = list(catalog) + list(mine)
    if st.session_state.get("st_sel") not in names:
        st.session_state["st_sel"] = names[0]
    c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
    name = c1.selectbox("Strategia", names, key="st_sel", format_func=lambda n: _strategy_label(n, catalog, mine),
                        placeholder="Scegli una strategia")
    c2.button("＋ Nuova strategia", width="stretch", on_click=_sb_load, args=(strategies.default_definition(),))
    entry = mine.get(name) or catalog[name]
    definition = mine.get(name) or (entry.get("definition") if entry.get("kind") == "rules" else None)
    if definition:
        if definition.get("description"):
            st.caption(md(definition["description"]))
        if sheet := (entry.get("investor") if name not in mine else None):
            with st.expander("L'investitore e la traduzione in regole"):
                st.markdown(md(f"**Chi**: {sheet['who']}  \n**Fonte**: {sheet['source']}  \n"
                               f"**Il metodo originale**: {sheet['original']}  \n"
                               f"**Come è tradotto qui, e cosa manca**: {sheet['translation']}"))
            notice("Le regole catturano solo la parte misurabile del metodo: i risultati veri di questi investitori "
                   "venivano anche da giudizio, concentrazione, tempismo e mercati diversi dall'S&P 500 di oggi. "
                   "Parametri fissati prima di guardare i risultati.", "info", title="Cautela:")
        with st.container(key="st_rules"):
            st.markdown(md("  \n".join(strategies.describe(definition))))
        if definition.get("universe", {}).get("watchlist_only"):
            notice("Usa le aziende che segui OGGI: le hai scelte sapendo come sono andate, quindi il backtest è "
                   "ottimista (le perdenti del passato non sono nella lista).", "warn", title="Selezione a posteriori:")
    else:
        st.caption(md(entry.get("description", "")))
    b = st.columns(4)
    if name in mine:
        b[0].button("Modifica", width="stretch", on_click=_sb_load, args=(definition, name))
        b[1].button("Duplica", width="stretch", on_click=_sb_load, args=({**definition, "name": f"{name} (copia)"},))
        with b[2].popover("Elimina", width="stretch"):
            st.write(f"Eliminare \"{name}\"? I backtest già fatti restano nel database.")
            if st.button("Sì, elimina"):
                d.delete_user_strategy(name)
                st.session_state.pop("st_sel", None)
                st.rerun()
    elif definition:
        b[0].button("Duplica e modifica", width="stretch",
                    on_click=_sb_load, args=({**definition, "name": f"{name} (mia)"},))
    else:
        b[0].caption("Si modifica nel Laboratorio.")

    result = d.strategy_result(name)
    baseline = d.strategy_result(strategies.BASELINE)
    if result is None or baseline is None:
        if empty_state("Backtest non ancora fatto con i dati di oggi",
                       "Dal primo giorno configurato a oggi, con i soli dati noti a ogni data: di solito 1-3 minuti "
                       "(la prima volta qualcosa in più, per la strategia originale e l'indice a pesi uguali).",
                       "Esegui il backtest", key="st_run"):
            _run_both(d, name)
    else:
        if result.get("stale"):
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.caption(f"Backtest con i dati al {fmt(result['data_as_of'], 'date')}: da allora sono arrivati prezzi "
                       "e bilanci nuovi (di solito cambia poco).")
            if c2.button("Ricalcola", width="stretch"):
                _run_both(d, name)
        _strategy_report(d, name, result, None if name == strategies.BASELINE else baseline)
    _strategy_holdings(d, name)
    _strategy_overview(d, names, mine)


def _strategy_label(name: str, catalog: dict, mine: dict) -> str:
    if name in mine:
        return f"{name} · mia"
    return f"{name} · grande investitore" if catalog.get(name, {}).get("group") == "Grandi investitori" else name


def _strategy_report(d: DashboardData, name: str, result: dict, baseline: dict | None):
    table = d.period_table(result, baseline)
    bench = str(d.cfg["universe"].get("benchmark") or "Benchmark")
    st.subheader("Risultati")
    second = table[table["period"].str.startswith("2a")].set_index("what") if not table.empty else table
    if "Strategia" in second.index and "Indice a pesi uguali" in second.index:
        s, e = second.loc["Strategia"], second.loc["Indice a pesi uguali"]
        better = s["cagr"] > e["cagr"] and s["sharpe_ratio"] >= e["sharpe_ratio"]
        verdict = ("batte l'indice a pesi uguali sia nel rendimento sia nel rapporto rendimento/rischio"
                   if better else "non batte in modo netto l'indice a pesi uguali")
        tail = f" {bench}: {fmt(second.loc[bench, 'cagr'], 'pct', signed=True)} l'anno." if bench in second.index else ""
        notice(f"nella seconda metà la strategia {verdict} ({fmt(s['cagr'], 'pct', signed=True)} l'anno contro "
               f"{fmt(e['cagr'], 'pct', signed=True)}; Sharpe {fmt(s['sharpe_ratio'], 'num')} contro "
               f"{fmt(e['sharpe_ratio'], 'num')}).{tail}", "pos" if better else "neutral", title="In sintesi:")
    with panel("Rendimento e rischio per periodo", right="stessa curva divisa in due metà"):
        dense_table(table, {
            "period": ("Periodo", "text"), "what": ("Cosa", "text"),
            "total_return": ("Rendimento", "pct", {"signed": True}), "cagr": ("All'anno", "pct", {"signed": True}),
            "annual_volatility": ("Volatilità", "pct"), "sharpe_ratio": ("Sharpe", "num"),
            "max_drawdown": ("Perdita massima", "pct"),
        }, key_col="what", highlight=["Strategia"], empty="Curva del backtest vuota.")

    eq, bench_eq = result["equity"], result["benchmark"]
    base_eq = baseline["equity"] if baseline else None
    with panel("Valore nel tempo", right="1 = inizio · scala logaritmica"):
        fig = go.Figure()
        for i, (s, label) in enumerate([(eq, name), (base_eq, "Indice a pesi uguali"), (bench_eq, bench)]):
            if s is not None and not s.empty:
                s = s.loc[eq.index[0]:]
                _line(fig, s.index, s / s.iloc[0], label, SERIES[i])
        mid = eq.index[0] + (eq.index[-1] - eq.index[0]) / 2
        fig.add_vline(x=mid, line=dict(color=MUTED, width=1, dash="dot"))
        fig.update_yaxes(type="log", title="Valore (1 = inizio)")
        st.plotly_chart(_layout(fig, 360), width="stretch")
        st.caption("La linea tratteggiata divide le due metà. Se hai scelto i parametri guardando la prima, la "
                   "seconda è la prova più onesta.")

    yearly = yearly_returns(eq, base_eq.loc[eq.index[0]:] if base_eq is not None else None)
    yb = yearly_returns(bench_eq)
    with panel("Anno per anno"):
        fig = go.Figure()
        for i, (col, label, src) in enumerate([("strategia", name, yearly),
                                               ("benchmark", "Indice a pesi uguali", yearly),
                                               ("strategia", bench, yb)]):
            if col in src:
                fig.add_trace(go.Bar(x=src.index.astype(str), y=src[col], name=label,
                                     marker=dict(color=SERIES[i], cornerradius=4), hovertemplate="%{y:+.1%}"))
        fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(_layout(fig, 280), width="stretch")

    m = result["metrics"]
    st.html(result_row_html([
        ("Rotazione annua", fmt_html(m.get("turnover_annual"), "mult")),
        ("Costi totali", fmt_html(m.get("total_costs"), "money", currency="USD")),
        ("Esposizione media", fmt_html(m.get("avg_exposure"), "pct")),
        ("Copertura dei dati", fmt_html(m.get("avg_coverage"), "pct")),
    ], note="Rotazione: quante volte all'anno si ricompra l'intero portafoglio (più è alta, più costi). Costi: "
            "commissioni e slippage su 100.000 iniziali. Copertura: quota dei membri dell'indice con prezzi (gli ex "
            "membri delistati mancano: survivorship bias)."))
    notice(f"Configurazioni diverse provate finora sugli stessi dati (Laboratorio e Strategie): "
           f"{fmt(d.db.count_variants(), 'int')}. Più se ne provano, più la migliore è anche la più fortunata.",
           "info", title="Cautela:")
    try:
        a = significance.assess(eq, d.db.load_experiments())
        notice(significance.verdict_text(a), "pos" if (a.get("dsr") or 0) >= 0.95 else "neutral",
               title="Sharpe corretto per i tentativi:")
    except Exception:  # la nota è informativa: mai bloccare la pagina
        pass


@st.cache_data(show_spinner="Calcolo cosa terrebbe oggi…")
def _holdings(_d: DashboardData, name: str, key: str) -> pd.DataFrame:  # noqa: ARG001
    return _d.strategy_holdings(name)


def _strategy_holdings(d: DashboardData, name: str):
    cfg, _ = d.strategy_config(name)
    hold = _holdings(d, name, f"{d.run_id}-{run_info.config_hash(cfg)}-{len(d.watchlist())}")
    right = "" if hold.empty else f"{plural(len(hold), 'titolo', 'titoli')} · investito {fmt(hold['weight'].sum(), 'pct', digits=0)}"
    with panel("Cosa terrebbe oggi", right=right):
        if hold.empty:
            st.html('<div class="qe-empty-line">Nessun titolo passa i filtri oggi.</div>')
            return
        dense_table(hold, {"ticker": ("Ticker", "ticker"), "name": ("Azienda", "text"), "sector": ("Settore", "text"),
                           "weight": ("Peso", "pct"), "score": ("Punteggio", "score")},
                    max_rows=max(40, len(hold)))
        st.caption("Non è un consiglio: è ciò che le regole selezionano con i dati di oggi. Punteggio 0-1 della "
                   "classifica (1 = la migliore); clic sul ticker per aprire la scheda.")


def _strategy_overview(d: DashboardData, names: list[str], mine: dict):
    st.subheader("Tutte le strategie a confronto")
    over = d.strategies_overview()
    rows = [{"Strategia": r["name"], "Tipo": "Mia" if r["mine"] else r["group"], "Dal": str(r["since"]),
             "all": r["all"], "first": r["first"], "second": r["second"], "sharpe": r["sharpe"], "dd": r["drawdown"],
             "turnover": r["turnover"] if r["turnover"] is not None else np.nan} for r in over["rows"]]
    missing = over["missing"]
    if rows:
        notice("Ordinate per rendimento nella seconda metà. Con tante strategie provate sugli stessi dati, la prima "
               "è anche la più fortunata: conta di più chi regge in entrambe le metà e il confronto con l'indice a "
               "pesi uguali.", "info", title="Cautela:")
        table = pd.DataFrame(rows)
        st.dataframe(table, hide_index=True, width="stretch", height=table_height(len(table), 25),
                     column_config={
                         "all": num_column("All'anno (tutto)", "pct"), "first": num_column("All'anno (1a metà)", "pct"),
                         "second": num_column("All'anno (2a metà)", "pct"), "sharpe": num_column("Sharpe", "num"),
                         "dd": num_column("Perdita massima", "pct"),
                         "turnover": num_column("Rotazione (x)", "num", digits=1)})
    if missing:
        if empty_state(f"{len(missing)} strategie senza backtest con i dati di oggi",
                       "Ognuna richiede 1-3 minuti: in tutto può volerci parecchio.",
                       f"Calcola le {len(missing)} mancanti", key="st_missing", details=", ".join(missing)):
            bar = st.progress(0.0)
            for i, n in enumerate(missing):
                bar.progress(i / len(missing), text=f"Backtest di {n}…")
                d.strategy_result(n, run=True)
            st.rerun()


def _strategy_builder(d: DashboardData):
    if "sb_name" not in st.session_state:
        _sb_load(strategies.default_definition(), switch=False)
    editing = st.session_state.get("sb_editing")
    st.caption(f"Stai modificando \"{editing}\"." if editing else "Nuova strategia: universo, filtri, classifica, "
               "portafoglio. Il backtest userà a ogni data solo i dati noti allora.")
    c1, c2 = st.columns([2, 3])
    c1.text_input("Nome", key="sb_name")
    c2.text_input("Descrizione (facoltativa)", key="sb_desc")

    st.markdown("#### 1. Universo")
    sectors_all = sorted({s for s in d.sectors().values() if isinstance(s, str) and s})
    st.multiselect("Settori", sectors_all, key="sb_sectors", placeholder="Tutti i settori")
    st.checkbox("Solo le aziende che seguo", key="sb_watch",
                help="La lista è quella di oggi: nel backtest le hai scelte col senno di poi, quindi è ottimista.")

    st.markdown("#### 2. Filtri")
    saved = d.db.load_screens()
    f1, f2 = st.columns([4, 1], vertical_alignment="bottom")
    src = f1.selectbox("Parti da una ricerca di Esplora", ["—"] + list(screener.PRESETS) + list(saved),
                       key="sb_filter_src", format_func=lambda o: f"{o} (salvata)" if o in saved else o)
    f2.button("Usa questi filtri", on_click=_sb_load_filters, args=(saved,), disabled=src == "—", width="stretch")
    st.session_state["sb_current_filters"] = _rules_widgets("sb", exclude=JUDGMENT_KEYS)
    st.button("＋ Aggiungi un filtro", on_click=_rules_add, args=("sb",))

    st.markdown("#### 3. Classifica")
    st.caption("Tra le aziende che passano i filtri si tengono le prime N per punteggio: la media pesata delle "
               "posizioni nella classifica di ogni metrica (per P/E e simili, più basso = meglio).")
    rankable = [m.key for m in METRICS if m.better and m.key not in JUDGMENT_KEYS]
    ranking = []
    for r in st.session_state["sk_rank"]:
        k = f"skr_{r['id']}"
        if f"{k}_metric" not in st.session_state:
            st.session_state[f"{k}_metric"] = r["metric"]
            st.session_state[f"{k}_weight"] = float(r["weight"])
        c1, c2, c3 = st.columns([5, 4, 0.9], vertical_alignment="center")
        metric = c1.selectbox("Metrica", rankable, key=f"{k}_metric", label_visibility="collapsed",
                              format_func=lambda x: f"{METRIC_BY_KEY[x].group} · {METRIC_BY_KEY[x].label}")
        weight = c2.slider("Peso", 0.0, 1.0, step=0.05, key=f"{k}_weight", label_visibility="collapsed")
        c3.button("✕", key=f"{k}_del", on_click=_rank_remove, args=(r["id"],))
        ranking.append({"metric": metric, "weight": weight})
    st.session_state["sb_current_ranking"] = ranking
    st.button("＋ Aggiungi una metrica alla classifica", on_click=_rank_add)

    st.markdown("#### 4. Portafoglio")
    p1, p2, p3 = st.columns(3)
    p1.number_input("Numero di titoli", 1, 600, key="sb_positions")
    p2.selectbox("Pesi", list(strategies.WEIGHTINGS), key="sb_weighting", format_func=strategies.WEIGHTINGS.get)
    p3.selectbox("Ribilanciamento", list(strategies.REBALANCE), key="sb_rebal", format_func=strategies.REBALANCE.get,
                 help="Più spesso = segue meglio le regole ma costa di più.")
    q1, q2 = st.columns(2)
    q1.slider("Peso massimo per titolo", 1.0, 100.0, step=1.0, key="sb_maxw", format="%d%%")
    q2.slider("Peso massimo per settore", 5.0, 100.0, step=5.0, key="sb_maxsec", format="%d%%")

    prev = d.strategy_preview(_sb_definition())
    if prev["ok"]:
        notice(f"oggi passano i filtri {fmt(prev['passing'], 'int')} aziende su {fmt(prev['universe'], 'int')}; la "
               f"strategia ne terrebbe {fmt(prev['held'], 'int')}.", "info", title="Anteprima:")
    else:
        notice(prev["error"], "warn", title="Da completare:")
    if "sb_error" in st.session_state:
        st.error(st.session_state.pop("sb_error"))
    st.button("Salva la strategia", type="primary", on_click=_sb_save, args=(d,))
