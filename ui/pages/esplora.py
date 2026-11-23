"""Esplora (F4, docs/revisione_grafica.md §4.4 e §7.1): filtri sulle metriche del catalogo, sul giudizio e sui
punteggi dei pilastri; tabella ordinabile con la colonna "Giudizio"; selezione → scheda / segui / confronta."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import screener
from modules.metrics import METRICS, METRIC_BY_KEY
from ui import nav
from ui.common import data
from ui.components import CURRENCY_SYMBOL, empty_state, fmt, it_number, table_height
from ui.pages.confronta import MAX_COMPARE
from ui.rules import (PILLAR_HELP, PILLAR_SHORT, _rules_add, _rules_set, _rules_widgets, metric_kind, metric_label,
                      num_col, pillar_column, round_for_display, styled, verdict_note, verdict_options,
                      verdict_table, verdict_text)

from modules.services.explore import EXPLORE_DEFAULT_COLS, FILTER_PILLARS  # noqa: E402
SCORE_STEPS = [round(i / 10, 1) for i in range(11)]
GROUP_SYMBOL = {"S&P 500": "$", "FTSE MIB": "€", "Euro Stoxx 50": "€"}
from modules.services.explore import SORTS  # noqa: E402


def _ex_set(rules: list[dict], sectors: list[str] | None = None):
    _rules_set("ex", rules)
    st.session_state["ex_sectors"] = list(sectors or [])


def _ex_load(saved: dict):
    choice = st.session_state.get("ex_choice")
    if choice in screener.PRESETS:
        _ex_set(screener.PRESETS[choice]["rules"])
    elif choice in saved:
        _ex_set(saved[choice]["rules"], saved[choice]["sectors"])
        _ex_set_filters(saved[choice].get("filters") or {})


def _ex_filters() -> dict:
    """Filtro per giudizio e punteggi minimi correnti, come si salvano con la ricerca."""
    mins = {p: float(st.session_state.get(f"ex_min_{p}", 0.0) or 0.0) for p in FILTER_PILLARS}
    return {"verdicts": list(st.session_state.get("ex_verdict") or []),
            "min_scores": {p: v for p, v in mins.items() if v > 0}}


def _ex_set_filters(filters: dict):
    """Rimette i filtri di una ricerca salvata (quelle salvate prima: nessun filtro). Valori non più validi ignorati."""
    valid = set(verdict_options())
    st.session_state["ex_verdict"] = [c for c in filters.get("verdicts", []) if c in valid]
    mins = filters.get("min_scores") or {}
    for p in FILTER_PILLARS:
        v = float(mins.get(p, 0.0) or 0.0)
        st.session_state[f"ex_min_{p}"] = v if v in SCORE_STEPS else 0.0


def _ex_reset_scores():
    for p in FILTER_PILLARS:
        st.session_state[f"ex_min_{p}"] = 0.0


def page_explore():
    d = data()
    st.title("Esplora")
    st.caption("Cerca tra le aziende con le metriche del catalogo (ultimi 12 mesi, prezzi dell'ultima chiusura). "
               "Ogni condizione va rispettata; un'azienda senza il dato non passa.")
    if "ex_rules" not in st.session_state:
        _ex_set([])
    saved = d.db.load_screens()
    options = list(screener.PRESETS) + list(saved)
    table = d.explore_table()
    groups = ["S&P 500"] + (["FTSE MIB", "Euro Stoxx 50"] if not d.foreign.empty else []) + \
        (["Le mie aggiunte"] if not d.custom_companies.empty else []) + ["Tutte"]
    c0, c1, c2, c3 = st.columns([3, 4, 1, 1], vertical_alignment="bottom")
    with c0:
        group = st.segmented_control("Universo", groups, default="S&P 500", key="ex_universe") or "S&P 500"
    choice = c1.selectbox("Ricerca pronta o salvata", options, key="ex_choice", index=None,
                          placeholder="Scegli una ricerca pronta o una delle tue",
                          format_func=lambda o: f"{o} (salvata)" if o in saved else o)
    c2.button("Carica", on_click=_ex_load, args=(saved,), disabled=choice is None, width="stretch")
    if choice in saved and c3.button("Elimina", width="stretch", help="Elimina questa ricerca salvata"):
        d.db.delete_screen(choice)
        st.toast(f"Ricerca «{choice}» eliminata")
        st.rerun()
    if choice in screener.PRESETS:
        st.caption(screener.PRESETS[choice]["description"])
    table = table[table.index.isin(d.universe_tickers(group))]
    if group != "S&P 500":
        st.caption("Aziende europee: bilanci annuali ESEF dal 2021 (niente trimestri), valori nella valuta di "
                   "quotazione. I filtri \"nel migliore X% del settore/indice\" confrontano con le aziende mostrate.")

    # giudizi (in cache per versione dei dati) affiancati alle metriche
    verdicts = verdict_table(d, list(table.index))
    vcols = verdicts.reindex(table.index)
    table = table.assign(category=vcols["category"],
                         **{f"v_{p}": pd.to_numeric(vcols[p], errors="coerce").astype(float)
                            for p in FILTER_PILLARS + ["insiders"]},
                         v_overall=pd.to_numeric(vcols["overall"], errors="coerce").astype(float))

    sectors_all = sorted(table["sector"].dropna().unique())
    st.session_state["ex_sectors"] = [x for x in st.session_state.get("ex_sectors", []) if x in sectors_all]
    f1, f2 = st.columns([2, 3])
    f1.multiselect("Settori", sectors_all, key="ex_sectors", placeholder="Tutti i settori")
    verdict_slot = f2.container()

    n_rules = len(st.session_state["ex_rules"])
    mins_set = sum(1 for p in FILTER_PILLARS if st.session_state.get(f"ex_min_{p}", 0.0) > 0)
    label = "Condizioni" + (f" ({n_rules})" if n_rules else "") + " e punteggi minimi del giudizio" + \
        (f" ({mins_set})" if mins_set else "")
    with st.expander(label, expanded=bool(n_rules or mins_set)):
        rules = _rules_widgets("ex")
        st.button("＋ Aggiungi una condizione", on_click=_rules_add, args=("ex",))
        st.html('<div class="qe-panel-title" style="margin:8px 0 4px"><span class="l">Punteggi minimi dei '
                'pilastri</span><span class="r">da 0 a 1; 0 = nessun filtro</span></div>')
        pcols = st.columns(len(FILTER_PILLARS) + 1, vertical_alignment="bottom")
        mins = {}
        for col, p in zip(pcols, FILTER_PILLARS):
            mins[p] = col.select_slider(PILLAR_SHORT[p], SCORE_STEPS, key=f"ex_min_{p}", help=PILLAR_HELP[p],
                                        format_func=lambda x: fmt(x, "score", digits=1))
        pcols[-1].button("Azzera i punteggi", on_click=_ex_reset_scores, disabled=not any(mins.values()),
                         width="stretch")

    mask = screener.apply_rules(table, rules, d.sectors(), st.session_state["ex_sectors"])
    view = table[mask]
    for p, lo in mins.items():
        if lo > 0:
            view = view[view[f"v_{p}"] >= lo]

    # filtro per giudizio: conteggi sulle aziende che passano gli altri filtri
    counts = view["category"].value_counts()
    with verdict_slot:
        picked_v = st.pills("Giudizio", verdict_options(), selection_mode="multi", key="ex_verdict",
                            format_func=lambda c: f"{verdict_text(c)} · {int(counts.get(c, 0))}")
        verdict_note()
    if picked_v:
        view = view[view["category"].isin(picked_v)]

    # colonne e ordinamento
    rule_cols = list(dict.fromkeys(r["metric"] for r in rules))
    h1, hs, h2 = st.columns([4, 2, 1], vertical_alignment="bottom")
    text = hs.text_input("Cerca nei risultati", key="ex_text", placeholder="Cerca: ticker o nome",
                         label_visibility="collapsed")
    if text:
        t = text.lower()
        view = view[view.index.str.lower().str.contains(t, regex=False)
                    | view["name"].fillna("").str.lower().str.contains(t, regex=False)]
    with h2.popover("Colonne", width="stretch"):
        sort = st.radio("Ordina per", list(SORTS), format_func=SORTS.get, key="ex_sort")
        show_pillars = st.toggle("Punteggi dei pilastri", key="ex_show_pillars", value=False)
        extra = st.multiselect("Altre colonne", [m.key for m in METRICS if m.key not in rule_cols],
                               default=[k for k in EXPLORE_DEFAULT_COLS if k not in rule_cols], key="ex_cols",
                               format_func=lambda k: METRIC_BY_KEY[k].label, placeholder="Scegli le colonne")
    order = {c: i for i, c in enumerate(verdict_options())}
    if sort == "verdict":
        view = view.assign(_o=view["category"].map(order).fillna(99)) \
            .sort_values(["_o", "v_overall"], ascending=[True, False], na_position="last").drop(columns="_o")
        sorted_by = "giudizio"
    elif sort == "auto" and rules:
        first = METRIC_BY_KEY[rules[0]["metric"]]
        view = view.sort_values(first.key, ascending=first.better == "low", na_position="last")
        sorted_by = first.label
    else:
        view = view.sort_values("market_cap", ascending=False)
        sorted_by = "capitalizzazione"
    with h1:
        st.subheader(f"{len(view)} aziende su {len(table)} · ordinate per {sorted_by}")
        if text:
            st.caption(f"che contengono «{text}»")
        if rules:
            st.caption(it_number(" · ".join(screener.describe_rule(r) for r in rules)))

    cols = ["market_cap"] + [c for c in rule_cols + extra if c != "market_cap"]
    # valuta: dollari per l'S&P 500, euro per gli indici europei; per i gruppi misti la colonna Valuta
    currencies = pd.Series({t: ("USD" if u == "S&P 500" else d.currency(t)) for t, u in view["universe"].items()},
                           dtype=object)
    single = currencies.nunique() <= 1
    symbol = GROUP_SYMBOL.get(group) or (CURRENCY_SYMBOL.get(currencies.iloc[0], currencies.iloc[0])
                                         if single and len(currencies) else "")
    shown = round_for_display(view, cols)
    for c in cols:
        if METRIC_BY_KEY[c].fmt == "money":
            shown[c] = (shown[c] / 1e9).round(1) + 0.0
    shown = shown.assign(verdict=view["category"].map(verdict_text), currency=currencies.reindex(view.index),
                         star=view["followed"].map({True: "★", False: ""}),
                         price=view["price"].astype(float).round(2) + 0.0,
                         my_margin=view["my_margin"].astype(float).round(4) + 0.0)
    pillar_cols = [f"v_{p}" for p in FILTER_PILLARS + ["insiders"]] if show_pillars else []
    for c in pillar_cols:
        shown[c] = shown[c].round(2)
    shown = shown.reset_index()[["ticker", "name", "verdict", "sector"] + ([] if single else ["currency"]) +
                                ["price"] + cols + pillar_cols + ["my_margin", "star"]]
    unit = f" {symbol}" if symbol else ""
    if shown.empty:  # st.dataframe vuota scrive "empty" in inglese
        empty_state("Nessuna azienda passa i filtri", "Allenta una condizione, abbassa i punteggi minimi o "
                    "scegli un altro giudizio.")
    else:
        slot = st.container()
        kinds = {"price": "price", "my_margin": ("pct", {"signed": True}), **{c: metric_kind(c) for c in cols}}
        selection = st.dataframe(
            styled(shown, kinds), hide_index=True, width="stretch", height=table_height(len(shown), 15), on_select="rerun",
            selection_mode="multi-row", key="ex_table",
            column_config={"ticker": st.column_config.TextColumn("Ticker", pinned=True), "name": "Azienda",
                           "verdict": st.column_config.TextColumn("Giudizio", help="Sintesi dei criteri, non una "
                                                                  "raccomandazione (vedi la riga sotto il filtro)"),
                           "sector": "Settore", "currency": "Valuta",
                           "price": num_col(f"Prezzo{unit}"),
                           "my_margin": num_col("Margine sul tuo FV",
                                                help="Tuo fair value / prezzo − 1 (solo se l'hai salvato)"),
                           "star": st.column_config.TextColumn("★", help="★ = la segui", width="small"),
                           **{f"v_{p}": pillar_column(p) for p in FILTER_PILLARS + ["insiders"]},
                           **{c: num_col(metric_label(c, symbol), help=METRIC_BY_KEY[c].help or None)
                              for c in cols}})
        picked = [shown.iloc[i]["ticker"] for i in (selection.selection.rows if selection else [])]
        with slot:
            if not picked:
                st.caption("Seleziona una o più righe per aprire la scheda, seguirle o confrontarle.")
            else:
                b1, b2, b3 = st.columns(3)
                if b1.button(f"Apri la scheda di {picked[0]} →", type="primary", width="stretch"):
                    st.session_state["ticker"] = picked[0]
                    st.switch_page(nav.page("azienda"))
                new = [t for t in picked if not table.at[t, "followed"]]
                if b2.button(f"☆ Segui {len(new)} aziende selezionate" if len(new) != 1 else f"☆ Segui {new[0]}",
                             disabled=not new, width="stretch"):
                    d.follow(new)
                    st.toast(f"Ora segui: {', '.join(new)}")
                    st.rerun()
                label = (f"Confronta le {len(picked)} selezionate" if len(picked) > 1
                         else "Confronta (scegline almeno 2)")
                if b3.button(label, width="stretch",
                             disabled=not 2 <= len(picked) <= MAX_COMPARE,
                             help=f"Da 2 a {MAX_COMPARE} aziende"):
                    st.session_state["cmp"] = picked
                    st.switch_page(nav.page("confronta"))

    with st.expander("Salva questa ricerca"):
        st.caption("Si salvano le condizioni, i settori, il filtro per giudizio e i punteggi minimi.")
        name = st.text_input("Nome", key="ex_save_name", placeholder="es. Le mie qualità a sconto")
        filters = _ex_filters()
        has_filter = bool(rules or filters["verdicts"] or filters["min_scores"])
        if st.button("Salva", disabled=not (name and has_filter),
                     help="Servono un nome e almeno una condizione, un giudizio o un punteggio minimo"):
            d.db.save_screen(name.strip(), rules, st.session_state["ex_sectors"], filters)
            st.toast(f"Ricerca «{name.strip()}» salvata: la ritrovi nell'elenco in alto.")
