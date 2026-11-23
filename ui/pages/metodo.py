"""Il mio metodo (docs/revisione_grafica.md §3.10): profilo, metriche personali, checklist."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import method, metrics as metrics_mod
from modules.dashboard_data import DashboardData
from ui import components as C
from ui.common import data


def page_method():
    d = data()
    if msg := st.session_state.pop("method_toast", None):
        st.toast(msg, icon=":material/check:")
    st.title("Il mio metodo")
    st.caption("Qui metti del tuo: come investi, le tue metriche, le domande che ti fai su ogni azienda. Tutto "
               "resta nel database e guida il resto dell'app.")
    tabs = st.tabs(["Il mio profilo", "Le mie metriche", "La mia checklist"])
    with tabs[0]:
        _profile_section(d)
    with tabs[1]:
        _custom_metrics_section(d)
    with tabs[2]:
        _checklist_section(d)


def _profile_section(d: DashboardData):
    prof = d.profile()
    with st.form("profile"):
        styles = st.multiselect("Cosa cerco soprattutto", method.STYLES, default=prof["styles"],
                                placeholder="Scegli uno o più stili")
        c1, c2 = st.columns(2)
        horizon = c1.selectbox("Per quanto tengo un titolo", method.HORIZONS,
                               index=method.HORIZONS.index(prof["horizon"]) if prof["horizon"] in method.HORIZONS else 2)
        positions = c2.number_input("Quante aziende in portafoglio", 1, 200, int(prof["positions"]))
        principles = st.text_area("I miei principi: cosa deve avere un'azienda per interessarmi",
                                  prof["principles"], height=140,
                                  placeholder="es. ROIC sopra il 15% da anni, debito basso, management che "
                                              "riacquista azioni solo quando costano poco...")
        red_flags = st.text_area("Cosa mi fa scartare subito un'azienda", prof["red_flags"], height=110,
                                 placeholder="es. diluizione continua, utili non coperti da cassa, business che "
                                             "non capisco...")
        sell_rules = st.text_area("Quando vendo", prof["sell_rules"], height=110,
                                  placeholder="es. la tesi è sbagliata, il prezzo supera di molto il mio fair value, "
                                              "trovo di meglio...")
        if st.form_submit_button("Salva il profilo", type="primary"):
            d.save_profile({"styles": styles, "horizon": horizon, "positions": int(positions),
                            "principles": principles, "red_flags": red_flags, "sell_rules": sell_rules})
            st.toast("Profilo salvato", icon=":material/check:")


def _custom_metrics_section(d: DashboardData):
    st.caption("Una metrica tua è una formula sulle metriche del catalogo: per esempio "
               "`fcf_yield - 0.02 * net_debt_ebitda` (rendimento del FCF penalizzato dal debito) oppure "
               "`roic - 0.08` (ROIC oltre un costo del capitale dell'8%). Da quando la salvi vale ovunque: "
               "Riepilogo, Esplora, Confronta e strategie, anche nei backtest (con i dati di ogni data).")
    mine = d.db.load_custom_metrics()
    if not mine:
        C.empty_state("Nessuna metrica personale", "Creala qui sotto: nome, formula e come si legge.")
    pending = st.session_state.get("cm_del")
    for m in mine:
        c1, c2, c3 = st.columns([3, 5, 1.2], vertical_alignment="center")
        c1.markdown(f"**{m['label']}**")
        c2.code(m["formula"], language=None)
        if pending == m["key"]:
            a, b = c3.columns(2)
            if a.button("Sì", key=f"cm_yes_{m['key']}", type="primary", help="Elimina la metrica"):
                used = d.delete_custom_metric(m["key"])
                st.session_state.pop("cm_del", None)
                if used:
                    st.session_state["flash_method"] = ("La usavano queste tue strategie, da sistemare: "
                                                        + ", ".join(used))
                st.session_state["method_toast"] = f"Metrica \"{m['label']}\" eliminata"
                st.rerun()
            if b.button("No", key=f"cm_no_{m['key']}"):
                st.session_state.pop("cm_del", None)
                st.rerun()
            C.notice(f"Eliminare \"{m['label']}\"? Sparisce da Esplora, Confronta e dalle strategie che la usano.",
                     "warn", title="Conferma:")
        elif c3.button("Elimina", key=f"cm_del_{m['key']}"):
            st.session_state["cm_del"] = m["key"]
            st.rerun()
    if msg := st.session_state.pop("flash_method", None):
        C.notice(msg, "warn")

    st.html(C.panel_title_html("Nuova metrica"))
    c1, c2, c3 = st.columns([3, 2, 2])
    label = c1.text_input("Nome", key="cm_label", placeholder="es. FCF corretto per il debito")
    fmt = c2.selectbox("Si legge come", list(metrics_mod.CUSTOM_FORMATS), format_func=metrics_mod.CUSTOM_FORMATS.get,
                       key="cm_fmt")
    better = c3.selectbox("Meglio se", ["high", "low", None], key="cm_better",
                          format_func=lambda b: {"high": "più alta", "low": "più bassa", None: "nessuno dei due"}[b])
    formula = st.text_input("Formula", key="cm_formula", placeholder="es. fcf_yield - 0.02 * net_debt_ebitda")
    description = st.text_input("Cosa misura (facoltativo)", key="cm_desc")
    with st.expander("Nomi delle metriche da usare nella formula"):
        names = pd.DataFrame([{"Nome nella formula": m.key, "Metrica": m.label, "Gruppo": m.group}
                              for m in metrics_mod.BASE_METRICS] + [{"Nome nella formula": "price", "Metrica": "Prezzo",
                                                                     "Gruppo": "Prezzo"}])
        st.dataframe(names, hide_index=True, width="stretch", height=300)
        st.caption("Operazioni: + - * / ** e parentesi. Funzioni: min(a, b), max(a, b), abs(a), log(a), sqrt(a). "
                   "Le percentuali sono frazioni: 15% si scrive 0.15.")
    if formula:
        try:
            values = d.preview_formula(formula)
            table = d.explore_table()
            ok = values.dropna()
            kind = {"pct": "pct", "x": "mult"}.get(fmt, "num")
            if ok.empty:
                C.notice(f"Oggi non è calcolabile per nessuna delle {len(values)} aziende: a tutte manca almeno "
                         "una delle metriche usate.", "warn")
            else:
                st.caption(f"Oggi calcolabile per {len(ok)} aziende su {len(values)}. Mediana "
                           f"{C.fmt(ok.median(), kind)}, dal 10° al 90° percentile {C.fmt(ok.quantile(0.1), kind)} "
                           f"– {C.fmt(ok.quantile(0.9), kind)}.")
            if better and not ok.empty:
                top = ok.sort_values(ascending=better == "low").head(10)
                st.caption("Le 10 migliori oggi: " + ", ".join(
                    f"{t} ({table.at[t, 'name'] if t in table.index else ''})" for t in top.index))
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Salva la metrica", type="primary", disabled=not (label and formula)):
        definition = {"key": metrics_mod.custom_key(label), "label": label.strip(), "formula": formula.strip(),
                      "fmt": fmt, "better": better, "description": description.strip()}
        try:
            d.save_custom_metric(definition)
            for k in ("cm_label", "cm_formula", "cm_desc"):
                st.session_state.pop(k, None)
            st.session_state["method_toast"] = f"Metrica \"{label.strip()}\" salvata"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _checklist_section(d: DashboardData):
    st.caption("Le domande che ti fai su ogni azienda. Dai un voto da 1 a 5 nella scheda dell'azienda (Il mio "
               "giudizio); il voto complessivo è la media pesata. Togliere una domanda non cancella i voti già dati.")
    items = d.checklist_items()
    if items.empty:
        C.empty_state("Nessuna domanda nella checklist", "Aggiungi la prima qui sotto.")
    for _, item in items.iterrows():
        i = int(item["id"])
        with st.expander(f"{item['question']} (peso {C.fmt(item['weight'], 'num', digits=1)})"):
            q = st.text_input("Domanda", item["question"], key=f"ci_q_{i}")
            h = st.text_area("Come rispondere", item["help"] or "", key=f"ci_h_{i}", height=80)
            w = st.slider("Peso", 0.0, 3.0, float(item["weight"]), 0.5, key=f"ci_w_{i}")
            b1, b2 = st.columns(2)
            if b1.button("Salva la domanda", key=f"ci_save_{i}", type="primary"):
                d.db.save_checklist_item(q, h, w, item_id=i)
                st.session_state["method_toast"] = "Domanda salvata"
                st.rerun()
            if b2.button("Togli questa domanda", key=f"ci_del_{i}"):
                d.db.deactivate_checklist_item(i)
                st.rerun()
    st.html(C.panel_title_html("Nuova domanda"))
    q = st.text_input("Domanda", key="ci_new_q", placeholder="es. Il debito è gestibile anche in una recessione?")
    h = st.text_input("Come rispondere (facoltativo)", key="ci_new_h")
    if st.button("Aggiungi la domanda", disabled=not q, type="primary"):
        d.db.save_checklist_item(q.strip(), h.strip())
        st.session_state["method_toast"] = "Domanda aggiunta"
        for k in ("ci_new_q", "ci_new_h"):
            st.session_state.pop(k, None)
        st.rerun()
