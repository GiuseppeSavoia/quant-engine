"""Pagina "Laboratorio": esperimenti sulla strategia automatica originale (config + modifiche dei parametri in
`lab.PARAMS`), stesso Backtester; non scrive mai config.yaml. Numeri e tabelle con `ui/components`."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import lab
from modules.dashboard_data import DashboardData
from ui.common import MUTED, SERIES, _layout, _line, data
from ui.components import NA, dense_table, empty_state, fmt, notice, panel


LAB_PREFIX = "lab:"


def _lab_value(p: lab.Param, value):
    """Tipo esatto che il widget si aspetta (in YAML `3` è un intero anche per un parametro decimale)."""
    if p.kind == "float":
        return float(value)
    if p.kind == "int":
        return int(value)
    if p.kind == "sectors":
        return list(value)
    return value


def _lab_reset(base: dict, preset: dict | None = None):
    for key, p in lab.PARAM_BY_KEY.items():
        st.session_state[LAB_PREFIX + key] = _lab_value(p, lab.get_value(base, p.path))
    for key, value in (preset or {}).items():
        st.session_state[LAB_PREFIX + key] = _lab_value(lab.PARAM_BY_KEY[key], value)


def _lab_widget(p: lab.Param, sectors: list[str]):
    key = LAB_PREFIX + ".".join(p.path)
    if p.kind == "float":
        st.number_input(p.label, min_value=float(p.min), max_value=float(p.max), step=float(p.step), key=key,
                        help=p.help or None, format="%.2f")
    elif p.kind == "int":
        st.number_input(p.label, min_value=int(p.min), max_value=int(p.max), step=int(p.step), key=key,
                        help=p.help or None)
    elif p.kind == "bool":
        st.toggle(p.label, key=key, help=p.help or None)
    elif p.kind == "choice":
        st.selectbox(p.label, list(p.options), key=key, help=p.help or None)
    elif p.kind == "sectors":
        st.multiselect(p.label, sectors, key=key, help=p.help or None, placeholder="Nessun settore")


def page_lab():
    d = data()
    base = d.cfg
    st.title("Laboratorio della strategia")
    st.caption("Cambia i parametri della strategia automatica originale e rifai il backtest sugli stessi dati. "
               "La configurazione salvata non cambia; ogni prova resta registrata con la versione del codice e i "
               "parametri.")
    if LAB_PREFIX + "confidence.weights.regime" not in st.session_state:
        _lab_reset(base)

    st.subheader("Punti di partenza")
    presets = dict(lab.PRESETS)
    if d.factor_weights():  # pesi trovati dall'ultima `python main.py --factors`
        presets["Fattori: pesi dalla ricerca"] = lab.factor_preset(d.factor_weights())
    per_row = 4  # a 1280 px sette pulsanti su una riga tagliano i nomi
    cols = [c for _ in range(-(-(len(presets) + 1) // per_row)) for c in st.columns(per_row)]
    if cols[0].button("Configurazione attuale", width="stretch"):
        _lab_reset(base)
    for col, (name, preset) in zip(cols[1:], presets.items()):
        if col.button(name, width="stretch"):
            _lab_reset(base, preset)
            st.session_state["lab_name"] = name

    sectors = sorted(d.companies["sector"].dropna().unique()) if "sector" in d.companies else []
    with st.form("lab_form"):
        groups: dict[str, list[lab.Param]] = {}
        for p in lab.PARAMS:
            groups.setdefault(p.group, []).append(p)
        for group, params in groups.items():
            box = st.expander(group) if group.startswith("Pesi dei fattori") else st.container()
            with box:
                st.markdown(f"**{group}**")
                cols = st.columns(4)
                for i, p in enumerate(params):
                    with cols[i % 4]:
                        _lab_widget(p, sectors)
        c1, c2, c3 = st.columns([3, 2, 2])
        name = c1.text_input("Nome dell'esperimento (facoltativo)", key="lab_name")
        period = c2.selectbox("Periodo", list(lab.PERIODS), format_func=lab.PERIODS.get, key="lab_period",
                              help="Scegli le varianti sulla prima metà, poi verifica la migliore sulla seconda: "
                                   "è la prova che non hai adattato i parametri ai dati.")
        with_research = c3.checkbox("Anche la ricerca dei segnali (raddoppia il tempo)")
        start, end = lab.period_bounds(base, d.data.calendar, period)
        st.caption(f"Backtest dal {fmt(start, 'date')} al {fmt(end, 'date')}: con lo storico lungo servono 1-2 minuti.")
        submitted = st.form_submit_button("Esegui il backtest", type="primary")

    experiments = d.db.load_experiments()
    as_of = str(pd.Timestamp(d.data.last_date).date())
    if submitted:
        values = {key: st.session_state[LAB_PREFIX + key] for key in lab.PARAM_BY_KEY}
        overrides = lab.changes(base, values)
        try:
            lab.apply_overrides(base, overrides)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
        same = experiments[(experiments["data_as_of"] == as_of) & (experiments["period"] == period)] \
            if not experiments.empty else experiments
        has_baseline = not same.empty and any(not o for o in same["overrides"])
        ids = []
        with st.status("Backtest in corso…", expanded=True) as status:
            if overrides and not has_baseline:
                st.write("Prima la configurazione attuale, come termine di confronto…")
                ids.append(d.db.log_experiment(lab.run_experiment(
                    base, {}, d.data, d.index_tickers, d.sectors(), d.universe_fn(), with_research=with_research,
                    period=period)))
            st.write(lab.describe(base, overrides))
            exp = lab.run_experiment(base, overrides, d.data, d.index_tickers, d.sectors(), d.universe_fn(),
                                     name=name.strip(), with_research=with_research, period=period)
            ids.append(d.db.log_experiment(exp))
            status.update(label=f"Fatto in {fmt(exp.seconds, 'int')} secondi", state="complete")
        st.session_state["lab_selected"] = ids
        experiments = d.db.load_experiments()

    if experiments.empty:
        empty_state("Nessun esperimento ancora",
                    "Scegli un punto di partenza o cambia i parametri, poi premi \"Esegui il backtest\".")
        return
    _lab_results(d, base, experiments, as_of)


def _lab_label(row) -> str:
    period = "" if row.period == "all" else f" [{'1ª' if row.period == 'first' else '2ª'} metà]"
    return f"#{row.id} · {row.name}{period}"




def _lab_overrides(overrides) -> bool:
    """True se l'esperimento è del Laboratorio (solo parametri di `lab.PARAMS`); le strategie a regole della pagina
    Strategie finiscono nella stessa tabella con modifiche di altro tipo, che `lab.describe` non sa leggere."""
    return all(k in lab.PARAM_BY_KEY for k in (overrides or {}))


def _lab_results(d: DashboardData, base: dict, experiments: pd.DataFrame, as_of: str):
    st.subheader("Confronto")
    same = experiments[experiments["data_as_of"] == as_of]
    labels = {int(r.id): _lab_label(r) for r in experiments.itertuples()}
    baseline_ids = [int(r.id) for r in same.itertuples() if not r.overrides]
    default = list(dict.fromkeys(baseline_ids[-1:] + st.session_state.get("lab_selected", [])
                                 + [int(i) for i in same["id"].tail(3)]))[:4]
    chosen = st.multiselect("Esperimenti da confrontare (al massimo 4)", list(labels), default=default,
                            format_func=labels.get, max_selections=4, placeholder="Scegli fino a 4 esperimenti")
    if not chosen:
        empty_state("Nessun esperimento scelto", "Scegline da uno a quattro qui sopra per confrontarli.")
        return
    rows = experiments.set_index("id").loc[chosen]

    table = pd.DataFrame([{
        "name": labels[i], "total_return": r.metrics.get("total_return"),
        "excess": r.metrics.get("excess_return_vs_benchmark"), "cagr": r.metrics.get("cagr"),
        "sharpe": r.metrics.get("sharpe_ratio"), "dd": r.metrics.get("max_drawdown"),
        "trades": r.metrics.get("n_trades"), "costs": r.metrics.get("total_costs"),
        "exposure": r.metrics.get("avg_exposure"), "turnover": r.metrics.get("turnover_annual"),
        "coverage": r.metrics.get("avg_coverage"),
        "period": lab.PERIODS.get(r.period, r.period).split(" (")[0], "as_of": r.data_as_of,
        "code": (r.git_commit or NA) + (" +mod." if r.git_dirty else ""),
    } for i, r in rows.iterrows()])
    n_same = len(same)
    with panel("Esperimenti a confronto", right=f"{n_same} esperimenti sugli stessi dati (al {fmt(as_of, 'date')})"):
        dense_table(table, {
            "name": ("Esperimento", "text"), "total_return": ("Rendimento", "pct", {"signed": True}),
            "excess": ("vs benchmark", "pp"), "cagr": ("All'anno", "pct", {"signed": True}),
            "sharpe": ("Sharpe", "num"), "dd": ("Perdita max", "pct"), "trades": ("Operazioni", "int"),
            "costs": ("Costi", "money", {"currency": "USD"}), "exposure": ("Esposizione", "pct"),
            "turnover": ("Rotazione", "mult"), "coverage": ("Copertura", "pct"), "period": ("Periodo", "text"),
        })
        notice(f"Più varianti si provano sugli stessi dati ({n_same} finora), più la migliore è anche la più "
               "fortunata. Una differenza piccola (qualche punto di rendimento, 0,1-0,2 di Sharpe) non dimostra "
               "nulla: va confermata sulla seconda metà o su dati che il laboratorio non ha visto. Copertura = "
               "membri dell'indice con un prezzo (il resto sono ex membri delistati: survivorship bias).", "info",
               title="Cautela:")
        if len({r.data_as_of for _, r in rows.iterrows()}) > 1:
            notice("Esperimenti fatti su dati di date diverse: i numeri non sono confrontabili.", "warn")
        if len({r.period for _, r in rows.iterrows()}) > 1:
            notice("Periodi diversi: i rendimenti non sono confrontabili tra loro, solo con il benchmark dello "
                   "stesso periodo.", "warn")

        fig = go.Figure()
        for color, (i, r) in zip(SERIES, rows.iterrows()):
            _line(fig, r.equity.index, r.equity.values, labels[i], color)
        bench = rows.iloc[0].benchmark
        if not bench.empty:
            _line(fig, bench.index, bench.values, "Benchmark (compra e tieni)", MUTED, dash="dash", width=1.5)
        fig.update_yaxes(title="Valore del portafoglio ($)")
        st.plotly_chart(_layout(fig, 420), width="stretch")

    research = [(labels[i], r.research) for i, r in rows.iterrows() if not r.research.empty]
    if research:
        ic = pd.DataFrame({name: rs[rs["horizon"] == 21].set_index("signal")["ic_mean"] for name, rs in research})
        cols = {"signal": ("Segnale", "text"),
                **{c: (c, "num", {"digits": 3, "signed": True}) for c in ic.columns}}
        with panel("IC dei segnali a 21 giorni"):
            dense_table(ic.rename_axis("signal").reset_index(), cols)

    st.subheader("Dettagli")
    for i, r in rows.iterrows():
        with st.expander(labels[i]):
            if not _lab_overrides(r.overrides):
                st.write("Strategia a regole provata nella pagina Strategie: si modifica lì, non nel Laboratorio.")
                st.caption(f"Creato il {fmt(r.created_at, 'date')} in {fmt(r.seconds, 'int')} secondi")
                continue
            st.write(lab.describe(base, r.overrides))
            if r.overrides:
                st.caption("Modifiche rispetto alla configurazione attuale (per adottarle vanno copiate in "
                           "config.yaml):")
                st.code(lab.overrides_yaml(r.overrides), language="yaml")
            st.caption(f"Creato il {fmt(r.created_at, 'date')} in {fmt(r.seconds, 'int')} secondi con i dati al "
                       f"{fmt(r.data_as_of, 'date')} · versione del codice {r.git_commit or NA}"
                       f"{' (con modifiche non salvate)' if r.git_dirty else ''} · configurazione {r.config_hash}")
            if st.button("Elimina questo esperimento", key=f"del_{i}"):
                d.db.delete_experiment(i)
                st.session_state["lab_selected"] = [x for x in st.session_state.get("lab_selected", []) if x != i]
                st.rerun()
