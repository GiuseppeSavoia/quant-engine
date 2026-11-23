"""
Strategie definite a regole, senza codice: il catalogo standard e quelle create dall'utente.

Una definizione è un dizionario (salvabile in JSON):
  universe   {"sectors": [...] (vuoto = tutti), "watchlist_only": bool}
  filters    regole di modules/screener.py (tutte da rispettare)
  ranking    [{"metric": chiave, "weight": peso}]: punteggio = media pesata delle posizioni percentili (0-1,
             1 = migliore) tra le aziende che passano i filtri; senza classifica si tengono le più grandi
  portfolio  {"positions": N, "weighting": equal | risk_parity | min_variance, "max_weight": 0-1,
              "max_sector": 0-1, "rebalance_days": giorni di borsa tra due ribilanciamenti}

`to_config` la traduce nella configurazione del motore (`strategy.mode: rules`), così il backtest è lo stesso
point-in-time di tutto il resto: a ogni ribilanciamento le metriche si ricalcolano con i soli dati noti allora.
Nessun parametro viene stimato dai dati: le due metà del periodo si leggono dallo stesso backtest.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from modules import metrics as metrics_mod
from modules.metrics import JUDGMENT_KEYS, METRIC_BY_KEY, percentile_ranks
from modules.screener import describe_rule, validate_rule

WEIGHTINGS = {"equal": "pesi uguali", "risk_parity": "meno peso ai titoli più volatili",
              "min_variance": "minima varianza (tiene conto delle correlazioni)"}
REBALANCE = {21: "ogni mese", 63: "ogni trimestre", 126: "ogni sei mesi", 252: "ogni anno"}
# metriche calcolate dai soli prezzi: se una strategia usa solo queste, i bilanci non servono (backtest veloce)
PRICE_METRICS = {"ret_1m", "ret_1y", "mom_12_1", "volatility", "beta", "drawdown_52w", "vs_sma200"}


def default_definition() -> dict:
    return {
        "name": "", "description": "",
        "universe": {"sectors": [], "watchlist_only": False},
        "filters": [],
        "ranking": [{"metric": "fcf_yield", "weight": 1.0}],
        "portfolio": {"positions": 30, "weighting": "equal", "max_weight": 0.10, "max_sector": 0.30,
                      "rebalance_days": 63},
    }


def validate_definition(d: dict) -> None:
    judged = needed_metrics(d) & JUDGMENT_KEYS
    if judged:
        raise ValueError("Il tuo voto della checklist esiste solo da oggi: nel backtest sarebbe un dato dal futuro. "
                         "Usalo in Esplora e Confronta, non nelle strategie.")
    for rule in d.get("filters", []):
        validate_rule(rule)
    for r in d.get("ranking", []):
        m = METRIC_BY_KEY.get(r.get("metric"))
        if m is None:
            raise ValueError(f"metrica sconosciuta nella classifica: {r.get('metric')}")
        if not m.better:
            raise ValueError(f"'{m.label}' non ha un verso migliore: non può ordinare le aziende")
        if float(r.get("weight", 0)) < 0:
            raise ValueError("i pesi della classifica non possono essere negativi")
    p = d["portfolio"]
    if int(p["positions"]) < 1:
        raise ValueError("serve almeno un titolo in portafoglio")
    if p["weighting"] not in WEIGHTINGS:
        raise ValueError(f"metodo dei pesi sconosciuto: {p['weighting']}")
    if not 0 < float(p["max_weight"]) <= 1 or not 0 < float(p["max_sector"]) <= 1:
        raise ValueError("peso massimo per titolo e per settore tra 0 e 100%")
    if int(p["rebalance_days"]) < 1:
        raise ValueError("ribilanciamento: almeno ogni giorno")


def needed_metrics(d: dict) -> set[str]:
    return {r["metric"] for r in d.get("filters", [])} | {r["metric"] for r in d.get("ranking", [])}


def rank_score(metrics: pd.DataFrame, ranking: list[dict]) -> pd.Series:
    """Media pesata delle posizioni percentili (1 = migliore) delle metriche della classifica. Una metrica
    mancante per un'azienda non conta: i pesi si rinormalizzano su quelle disponibili."""
    w = {r["metric"]: float(r["weight"]) for r in ranking if float(r["weight"]) > 0}
    if not w or metrics.empty:
        return pd.Series(np.nan, index=metrics.index)
    ranks = percentile_ranks(metrics[[k for k in w if k in metrics]])
    weight = pd.Series(w).reindex(ranks.columns)
    num = ranks.mul(weight).sum(axis=1, min_count=1)
    den = ranks.notna().mul(weight).sum(axis=1)
    return num / den.where(den > 0)


def to_config(base_cfg: dict, d: dict, watchlist: list[str] | None = None) -> dict:
    """Configurazione del motore per la definizione `d` (modalità rules, nessuna uscita stop-loss/take-profit,
    solo il tetto per settore come limite di rischio)."""
    validate_definition(d)
    cfg = copy.deepcopy(base_cfg)
    u, p = d.get("universe", {}), d["portfolio"]
    cfg.setdefault("strategy", {})
    custom = {k: dict(metrics_mod.CUSTOM[k], key=k) for k in sorted(needed_metrics(d)) if k in metrics_mod.CUSTOM}
    cfg["strategy"].update(mode="rules", rules={
        "filters": d.get("filters", []), "ranking": d.get("ranking", []),
        "sectors": list(u.get("sectors") or []),
        "tickers": sorted(watchlist) if u.get("watchlist_only") else None,
        # le formule delle metriche personali usate: la configurazione resta completa e riproducibile
        # (e se cambi una formula cambia anche l'impronta del backtest)
        "custom_metrics": list(custom.values()),
    })
    cfg["portfolio"].update(method=p["weighting"], max_positions=int(p["positions"]),
                            max_weight=float(p["max_weight"]))
    cfg["risk"].update(max_sector_weight=float(p["max_sector"]), target_volatility=0.0, drawdown_limit=0.0)
    cfg["backtest"].update(rebalance_days=int(p["rebalance_days"]), use_exits=False)
    if d.get("start"):  # dati disponibili solo da una certa data (es. i 13F)
        cfg["backtest"]["start"] = str(d["start"])
    return cfg


def describe(d: dict) -> list[str]:
    """La definizione in frasi, per la pagina Strategie."""
    u, p = d.get("universe", {}), d["portfolio"]
    where = "le tue aziende (lista di oggi)" if u.get("watchlist_only") else "tutto l'S&P 500 (membri a ogni data)"
    if u.get("sectors"):
        where += ", solo " + ", ".join(u["sectors"])
    lines = [f"**Universo**: {where}"]
    lines.append("**Filtri**: " + ("; ".join(describe_rule(r) for r in d.get("filters", [])) or "nessuno"))
    rk = [r for r in d.get("ranking", []) if float(r["weight"]) > 0]
    if rk:
        total = sum(float(r["weight"]) for r in rk)
        lines.append("**Classifica**: " + ", ".join(
            f"{METRIC_BY_KEY[r['metric']].label} {float(r['weight']) / total:.0%}" for r in rk))
    else:
        lines.append("**Classifica**: nessuna (si tengono le aziende più grandi)")
    lines.append(f"**Portafoglio**: {int(p['positions'])} titoli, {WEIGHTINGS[p['weighting']]}, massimo "
                 f"{float(p['max_weight']):.0%} per titolo e {float(p['max_sector']):.0%} per settore, ribilanciato "
                 f"{REBALANCE.get(int(p['rebalance_days']), 'ogni ' + str(p['rebalance_days']) + ' giorni di borsa')}")
    if d.get("start"):
        lines.append(f"**Backtest**: dal {pd.Timestamp(d['start']):%d/%m/%Y} (prima mancano i dati che usa)")
    return lines


def _rules(name: str, description: str, ranking: list[tuple[str, float]], filters: list[dict] | None = None,
           positions: int = 50, rebalance_days: int = 63, max_weight: float = 0.05, sectors=None,
           max_sector: float = 0.30) -> dict:
    d = default_definition()
    d.update(name=name, description=description, filters=filters or [],
             ranking=[{"metric": m, "weight": w} for m, w in ranking])
    d["universe"]["sectors"] = list(sectors or [])
    d["portfolio"].update(positions=positions, rebalance_days=rebalance_days, max_weight=max_weight,
                          max_sector=max_sector)
    return d


NON_FINANCIAL = ["Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Health Care",
                 "Industrials", "Information Technology", "Materials", "Real Estate"]

# il catalogo standard: strategie note, con parametri scelti PRIMA di guardare i risultati
CATALOG: dict[str, dict] = {
    "Indice a pesi uguali": {
        "kind": "rules", "baseline": True,
        "definition": _rules("Indice a pesi uguali", "Tutti i membri dell'indice, stesso peso, ribilanciato ogni "
                             "mese. È il termine di confronto onesto: chi sceglie i titoli deve fare meglio di così.",
                             [], positions=600, rebalance_days=21, max_weight=1.0, max_sector=1.0),
    },
    "Momentum": {
        "kind": "rules",
        "definition": _rules("Momentum", "I 50 titoli saliti di più da 12 a 1 mese fa (si salta l'ultimo mese, che "
                             "tende a invertirsi). Ribilanciato ogni mese.", [("mom_12_1", 1.0)], rebalance_days=21),
    },
    "Value": {
        "kind": "rules",
        "definition": _rules("Value", "I 50 titoli più economici rispetto a utili, free cash flow e utile "
                             "operativo.", [("earnings_yield", 1.0), ("fcf_yield", 1.0), ("ev_ebit", 1.0)]),
    },
    "Quality": {
        "kind": "rules",
        "definition": _rules("Quality", "I 50 titoli con il miglior rendimento sul capitale, margini più alti e "
                             "meno debito.", [("roic", 1.0), ("gross_profitability", 1.0), ("net_debt_ebitda", 1.0)]),
    },
    "Bassa volatilità": {
        "kind": "rules",
        "definition": _rules("Bassa volatilità", "I 50 titoli meno volatili nell'ultimo anno.", [("volatility", 1.0)],
                             rebalance_days=21),
    },
    "Magic Formula": {
        "kind": "rules",
        "definition": _rules("Magic Formula", "Greenblatt: buone aziende (ROIC alto) a buon prezzo (EV/EBIT basso), "
                             "30 titoli. Esclusi Financials e Utilities, come nell'originale.",
                             [("roic", 1.0), ("ev_ebit", 1.0)], positions=30, max_weight=0.06,
                             sectors=NON_FINANCIAL),
    },
    "Qualità a prezzo giusto": {
        "kind": "rules",
        "definition": _rules("Qualità a prezzo giusto", "La ricerca pronta di Esplora (ROIC >= 15%, margine operativo "
                             ">= 15%, debito netto/EBITDA <= 2,5, rendimento FCF >= 4%), i 30 con il FCF più alto.",
                             [("fcf_yield", 1.0)], positions=30, max_weight=0.06,
                             filters=[{"metric": "roic", "op": ">=", "value": 0.15},
                                      {"metric": "operating_margin", "op": ">=", "value": 0.15},
                                      {"metric": "net_debt_ebitda", "op": "<=", "value": 2.5},
                                      {"metric": "fcf_yield", "op": ">=", "value": 0.04}]),
    },
    "Piotroski": {
        "kind": "rules",
        "definition": _rules("Piotroski", "Piotroski (2000): tra le aziende più economiche rispetto al patrimonio "
                             "(il 20% con il prezzo/patrimonio più basso), quelle con F-score almeno 8, cioè con il "
                             "bilancio in miglioramento. Fino a 30 titoli; se ne passano meno, il resto resta liquido.",
                             [("piotroski", 1.0), ("pb", 0.5)], positions=30, max_weight=0.10,
                             filters=[{"metric": "pb", "op": "top_index", "value": 0.2},
                                      {"metric": "piotroski", "op": ">=", "value": 8}]),
    },
    "Strategia originale": {
        "kind": "config", "overrides": {},
        "description": "La strategia di config.yaml (regime di mercato + DCF + multi-factor, soglie, stop-loss e "
                       "take-profit). È quella dell'analisi giornaliera e della pagina Strategia automatica.",
    },
}
BASELINE = "Indice a pesi uguali"

# strategie ispirate ai grandi investitori (modules/investors.py, che usa _rules e i settori definiti qui sopra)
from modules.investors import EXISTING_SHEETS, INVESTOR_STRATEGIES  # noqa: E402

for _name, _sheet in EXISTING_SHEETS.items():
    CATALOG[_name] = {**CATALOG[_name], "group": "Grandi investitori", "investor": _sheet}
CATALOG.update(INVESTOR_STRATEGIES)
