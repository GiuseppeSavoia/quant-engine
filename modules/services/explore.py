"""Esplora: cercare tra le aziende con le metriche del catalogo, il giudizio e i punteggi dei pilastri.

Una sola definizione per la webapp Streamlit (ui/pages/esplora.py, ui/rules.py importano unità, testi e regole da qui) e
per l'interfaccia web (api/: /explore). Le regole sono quelle di modules/screener.py: ogni condizione va rispettata, un
dato mancante non passa mai."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules import screener
from modules.formats import it_text
from modules.metrics import METRIC_BY_KEY, METRICS
from modules.services.base import Service

EXPLORE_DEFAULT_COLS = ["pe", "ev_ebit", "fcf_yield", "roic", "operating_margin", "revenue_growth",
                        "net_debt_ebitda", "dividend_yield", "ret_1y"]
FILTER_PILLARS = ["quality", "solidity", "price", "flags"]
PILLARS = ["quality", "solidity", "price", "flags", "insiders"]
PILLAR_SHORT = {"quality": "Qualità", "solidity": "Solidità", "price": "Conven.", "flags": "Allarmi",
                "insiders": "Insider"}
PILLAR_HELP = {
    "quality": "Qualità del business, da 0 a 1 (1 = eccellente: redditività alta e costante).",
    "solidity": "Solidità finanziaria, da 0 a 1 (1 = molto solida: poco debito, interessi ben coperti).",
    "price": "Pilastro Prezzo, da 0 a 1 (1 = prezzo basso rispetto a valore stimato, settore e storia).",
    "flags": "Segnali d'allarme, da 0 a 1 (1 = nessun allarme contabile o di bilancio).",
    "insiders": "Chi è dentro (insider e fondi), da 0 a 1: solo contesto, non entra nel giudizio.",
}
VERDICT_NOTE = ("Il giudizio è una sintesi dei criteri impostati, non una raccomandazione: nella prova sul passato "
                "(2013-2026, dati di allora) le «Candidate» non hanno battuto l'indice. Dettagli e prove nella scheda "
                "dell'azienda.")
SORTS = {"auto": "Prima condizione (o capitalizzazione)", "verdict": "Giudizio", "market_cap": "Capitalizzazione"}
MAX_ROWS = 600


def rule_unit(metric: str, op: str) -> tuple[str, float]:
    """Unità mostrata e fattore di conversione di una condizione (valore mostrato = valore x fattore)."""
    if op in screener.RANK_OPS:
        return "%", 100.0
    return {"pct": ("%", 100.0), "money": ("mld $", 1e-9), "x": ("x", 1.0)}.get(METRIC_BY_KEY[metric].fmt, ("", 1.0))


class ExploreService(Service):
    def explore_universes(self) -> list[str]:
        ctx = self.ctx
        return ["S&P 500"] + (["FTSE MIB", "Euro Stoxx 50"] if not ctx.foreign.empty else []) + \
            (["Le mie aggiunte"] if not ctx.custom_companies.empty else []) + ["Tutte"]

    def explore_catalog(self) -> dict:
        """Tutto quello che serve a costruire i filtri: metriche (con gruppo, verso migliore e unità delle
        condizioni), operatori, ricerche pronte e salvate, universi, settori, pilastri, giudizi, colonne predefinite."""
        from modules.verdict import LABELS, ORDER

        metrics = []
        for m in METRICS:
            unit, factor = rule_unit(m.key, ">=")
            metrics.append({"key": m.key, "label": m.label, "group": m.group, "fmt": m.fmt, "better": m.better,
                            "help": m.help, "unit": unit, "factor": factor})
        saved = self.db.load_screens()
        return {
            "metrics": metrics,
            "ops": [{"value": k, "label": v, "rank": k in screener.RANK_OPS} for k, v in screener.OPS.items()],
            "presets": [{"name": n, "description": p["description"], "rules": p["rules"]}
                        for n, p in screener.PRESETS.items()],
            "saved": [{"name": n, **s} for n, s in saved.items()],
            "universes": self.explore_universes(),
            "sectors": sorted({s for s in self.ctx.sectors().values() if isinstance(s, str) and s}),
            "pillars": [{"key": p, "label": PILLAR_SHORT[p], "help": PILLAR_HELP[p]} for p in FILTER_PILLARS],
            "verdicts": [{"value": c, "label": LABELS.get(c, c)} for c in ORDER],
            "sorts": [{"value": k, "label": v} for k, v in SORTS.items()],
            "default_columns": EXPLORE_DEFAULT_COLS,
            "note": VERDICT_NOTE,
        }

    def explore(self, universe: str = "S&P 500", rules: list[dict] | None = None, sectors: list[str] | None = None,
                verdicts: list[str] | None = None, min_scores: dict | None = None, text: str = "",
                sort: str = "auto", columns: list[str] | None = None) -> dict:
        """Aziende che passano i filtri, già ordinate, con le colonne scelte (valori grezzi nelle unità della
        metrica). ValueError (messaggio in italiano) per condizioni non valide o universo sconosciuto."""
        ctx = self.ctx
        rules = list(rules or [])
        for r in rules:
            screener.validate_rule(r)
        if universe not in self.explore_universes():
            raise ValueError(f"Universo sconosciuto: {universe}")
        if sort not in SORTS:
            raise ValueError(f"Ordinamento sconosciuto: {sort}")
        cols_req = [c for c in (columns if columns is not None else EXPLORE_DEFAULT_COLS) if c in METRIC_BY_KEY]
        table = ctx.explore_table()
        table = table[table.index.isin(ctx.universe_tickers(universe))]
        total = len(table)
        verdict_tab = ctx.verdict_overview(list(table.index)).reindex(table.index)
        table = table.assign(category=verdict_tab["category"], verdict=verdict_tab["verdict"],
                             overall=pd.to_numeric(verdict_tab["overall"], errors="coerce"),
                             **{f"pillar_{p}": pd.to_numeric(verdict_tab[p], errors="coerce") for p in PILLARS})
        view = table[screener.apply_rules(table, rules, ctx.sectors(), list(sectors or []))]
        for p, lo in (min_scores or {}).items():
            if p in FILTER_PILLARS and lo and float(lo) > 0:
                view = view[view[f"pillar_{p}"] >= float(lo)]
        counts = view["category"].value_counts()
        if verdicts:
            view = view[view["category"].isin(verdicts)]
        if text:
            t = text.strip().lower()
            view = view[view.index.str.lower().str.contains(t, regex=False)
                        | view["name"].fillna("").str.lower().str.contains(t, regex=False)]
        from modules.verdict import ORDER

        if sort == "verdict":
            order = {c: i for i, c in enumerate(ORDER)}
            view = view.assign(_o=view["category"].map(order).fillna(99)) \
                .sort_values(["_o", "overall"], ascending=[True, False], na_position="last").drop(columns="_o")
            sorted_by = "giudizio"
        elif sort == "auto" and rules:
            first = METRIC_BY_KEY[rules[0]["metric"]]
            view = view.sort_values(first.key, ascending=first.better == "low", na_position="last")
            sorted_by = first.label
        else:
            view = view.sort_values("market_cap", ascending=False, na_position="last")
            sorted_by = "capitalizzazione"
        rule_cols = list(dict.fromkeys(r["metric"] for r in rules))
        cols = ["market_cap"] + [c for c in rule_cols + cols_req if c != "market_cap"]
        cols = list(dict.fromkeys(cols))
        shown = view.head(MAX_ROWS)
        currency = {t: ("USD" if u == "S&P 500" else ctx.currency(t)) for t, u in shown["universe"].items()} \
            if "universe" in shown else {t: ctx.currency(t) for t in shown.index}
        rows = []
        for t, r in shown.iterrows():
            rows.append({"ticker": t, "name": r.get("name"), "sector": r.get("sector"), "currency": currency.get(t),
                         "price": r.get("price"), "category": r.get("category"), "verdict": r.get("verdict"),
                         "followed": bool(r.get("followed")), "my_margin": r.get("my_margin"),
                         **{f"pillar_{p}": r.get(f"pillar_{p}") for p in PILLARS},
                         "values": {c: (float(r[c]) if c in r and pd.notna(r[c]) and np.isfinite(float(r[c])) else None)
                                    for c in cols}})
        return {"universe": universe, "total": total, "count": int(len(view)), "shown": len(rows),
                "sorted_by": sorted_by, "rules": [it_text(screener.describe_rule(r)) for r in rules],
                "verdict_counts": {str(k): int(v) for k, v in counts.items()},
                "columns": [{"key": c, "label": METRIC_BY_KEY[c].label, "fmt": METRIC_BY_KEY[c].fmt,
                             "help": METRIC_BY_KEY[c].help} for c in cols],
                "currencies": sorted({c for c in currency.values() if c}), "rows": rows,
                "european": universe != "S&P 500"}

    def save_screen(self, name: str, rules: list[dict], sectors: list[str] | None = None,
                    filters: dict | None = None) -> None:
        """Salva (o sostituisce) una ricerca: condizioni, settori, giudizi e punteggi minimi."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Serve un nome per la ricerca.")
        for r in rules:
            screener.validate_rule(r)
        filters = filters or {}
        if not (rules or filters.get("verdicts") or filters.get("min_scores")):
            raise ValueError("Serve almeno una condizione, un giudizio o un punteggio minimo.")
        self.db.save_screen(name, rules, sectors or [], filters)

    def delete_screen(self, name: str) -> None:
        self.db.delete_screen(name)
