"""
Filtri sulle metriche del catalogo (modules/metrics.py): una ricerca è un elenco di regole, tutte da rispettare.

Una regola è un dizionario {"metric": chiave, "op": operatore, "value": numero}:
  ">="          la metrica vale almeno `value`
  "<="          la metrica vale al massimo `value`
  "top_sector"  l'azienda è nel `value` (es. 0.2 = 20%) migliore del suo settore per quella metrica
  "top_index"   l'azienda è nel `value` migliore di tutto l'universo
I valori sono nelle unità della metrica: frazioni per le percentuali (0.15 = 15%), dollari per gli importi.
Un'azienda senza il dato non passa la regola (un filtro "debito basso" non deve far passare chi il debito
non lo dichiara).

Le stesse regole servono alle strategie create dall'utente: `apply_rules` lavora su qualunque tabella di
`compute_metrics`, anche calcolata su un'istantanea passata (backtest point-in-time).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.metrics import METRIC_BY_KEY, format_value, percentile_ranks

OPS = {
    ">=": "almeno",
    "<=": "al massimo",
    "top_sector": "tra i migliori del settore (%)",
    "top_index": "tra i migliori dell'S&P 500 (%)",
}
RANK_OPS = {"top_sector", "top_index"}


def validate_rule(rule: dict) -> None:
    if rule.get("metric") not in METRIC_BY_KEY:
        raise ValueError(f"metrica sconosciuta: {rule.get('metric')}")
    if rule.get("op") not in OPS:
        raise ValueError(f"operatore sconosciuto: {rule.get('op')}")
    if rule["op"] in RANK_OPS and not METRIC_BY_KEY[rule["metric"]].better:
        raise ValueError(f"'{METRIC_BY_KEY[rule['metric']].label}' non ha un verso migliore: niente classifica")
    if rule["op"] in RANK_OPS and not 0 < float(rule["value"]) <= 1:
        raise ValueError("la quota dei migliori va tra 0 e 1 (es. 0.2 = il 20% migliore)")


def apply_rules(metrics: pd.DataFrame, rules: list[dict], sectors: dict[str, str] | None = None,
                only_sectors: list[str] | None = None) -> pd.Series:
    """Maschera booleana (indice = ticker) delle aziende che rispettano TUTTE le regole e, se indicati,
    appartengono a uno dei settori `only_sectors`."""
    mask = pd.Series(True, index=metrics.index)
    if only_sectors:
        sec = pd.Series({t: (sectors or {}).get(t) for t in metrics.index})
        mask &= sec.isin(only_sectors)
    ranks: dict[str, pd.DataFrame] = {}
    for rule in rules:
        validate_rule(rule)
        key, op, value = rule["metric"], rule["op"], float(rule["value"])
        col = metrics[key].astype(float) if key in metrics else pd.Series(np.nan, index=metrics.index)
        if op == ">=":
            mask &= col >= value
        elif op == "<=":
            mask &= col <= value
        else:
            scope = "sector" if op == "top_sector" else "index"
            if scope not in ranks:
                ranks[scope] = percentile_ranks(metrics, sectors if scope == "sector" else None)
            mask &= ranks[scope][key] >= 1 - value  # NaN -> False
    return mask.fillna(False)


def describe_rule(rule: dict) -> str:
    m = METRIC_BY_KEY[rule["metric"]]
    if rule["op"] in RANK_OPS:
        where = "del settore" if rule["op"] == "top_sector" else "dell'S&P 500"
        return f"{m.label}: nel {float(rule['value']):.0%} migliore {where}"
    return f"{m.label} {rule['op']} {format_value(m.key, float(rule['value']))}"


# ricerche pronte: punti di partenza ragionevoli, non raccomandazioni
PRESETS: dict[str, dict] = {
    "Qualità a prezzo giusto": {
        "description": "Rendono molto sul capitale, con margini alti e poco debito, a un prezzo non estremo "
                       "rispetto al cash flow.",
        "rules": [{"metric": "roic", "op": ">=", "value": 0.15}, {"metric": "operating_margin", "op": ">=", "value": 0.15},
                  {"metric": "net_debt_ebitda", "op": "<=", "value": 2.5}, {"metric": "fcf_yield", "op": ">=", "value": 0.04}],
    },
    "Economiche sul cash flow": {
        "description": "Molto free cash flow rispetto al prezzo, debito sotto controllo e ricavi non in calo.",
        "rules": [{"metric": "fcf_yield", "op": ">=", "value": 0.07}, {"metric": "debt_to_equity", "op": "<=", "value": 1.5},
                  {"metric": "revenue_growth", "op": ">=", "value": 0.0}],
    },
    "Crescita costante": {
        "description": "Ricavi in crescita da tre anni e anche nell'ultimo, con margini positivi e senza diluire "
                       "gli azionisti.",
        "rules": [{"metric": "revenue_cagr_3y", "op": ">=", "value": 0.10}, {"metric": "revenue_growth", "op": ">=", "value": 0.08},
                  {"metric": "operating_margin", "op": ">=", "value": 0.10}, {"metric": "share_change", "op": "<=", "value": 0.02}],
    },
    "Dividendi solidi": {
        "description": "Dividendo alto, interessi ampiamente coperti dagli utili e debito ragionevole.",
        "rules": [{"metric": "dividend_yield", "op": ">=", "value": 0.03}, {"metric": "interest_coverage", "op": ">=", "value": 5.0},
                  {"metric": "net_debt_ebitda", "op": "<=", "value": 3.0}],
    },
    "Riacquistano azioni": {
        "description": "Il numero di azioni scende di almeno il 2% l'anno, pagato con il cash flow.",
        "rules": [{"metric": "share_change", "op": "<=", "value": -0.02}, {"metric": "fcf_yield", "op": ">=", "value": 0.04}],
    },
    "Le migliori del loro settore": {
        "description": "Nel 25% migliore del proprio settore per ROIC e per rendimento del FCF.",
        "rules": [{"metric": "roic", "op": "top_sector", "value": 0.25}, {"metric": "fcf_yield", "op": "top_sector", "value": 0.25}],
    },
}
