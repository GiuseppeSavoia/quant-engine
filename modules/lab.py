"""
Laboratorio della strategia: provare parametri diversi senza toccare config.yaml.

Un esperimento = configurazione di base (config.yaml) + alcune modifiche. Si fa girare lo stesso
backtest point-in-time di main.py (e, se richiesto, la ricerca dei segnali) sugli stessi dati, e
il risultato viene salvato nel database (tabella `experiments`) con commit git, configurazione
completa e data dei dati, così ogni prova resta confrontabile e riproducibile.

Attenzione all'overfitting: provando molte varianti sugli stessi anni, la migliore sarà anche la
più fortunata. Il numero di esperimenti fatti sugli stessi dati è parte del risultato.
Per questo si può limitare il backtest a una metà del periodo (PERIODS): si sceglie la variante
sulla prima metà e la si verifica sulla seconda, che durante la scelta non è stata guardata.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field

import pandas as pd

from modules import run_info
from modules.backtester import Backtester
from modules.factors import FACTORS
from modules.research import SignalResearch
from modules.strategy import Strategy


@dataclass(frozen=True)
class Param:
    path: tuple[str, ...]
    label: str
    group: str
    kind: str                 # float | int | bool | choice | sectors
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple = ()
    help: str = ""


PARAMS: list[Param] = [
    Param(("strategy", "mode"), "Modalità", "Strategia", "choice", options=("confidence", "factors"),
          help="confidence = punteggio originale (regime, DCF, multi-factor); factors = combinazione dei fattori "
               "classici con i pesi qui sotto"),
    Param(("confidence", "weights", "regime"), "Peso del regime", "Punteggio di confidenza", "float", 0, 1, 0.05,
          help="I pesi vengono normalizzati: contano le proporzioni."),
    Param(("confidence", "weights", "value"), "Peso del margine DCF", "Punteggio di confidenza", "float", 0, 1, 0.05),
    Param(("confidence", "weights", "multi_factor"), "Peso del multi-factor", "Punteggio di confidenza", "float",
          0, 1, 0.05),
    Param(("confidence", "full_value_margin"), "Margine DCF per il punteggio pieno", "Punteggio di confidenza",
          "float", 0.05, 1, 0.05),
    Param(("confidence", "thresholds", "Bull"), "Soglia in Bull", "Soglie per regime", "float", 0, 1, 0.05),
    Param(("confidence", "thresholds", "Neutral"), "Soglia in Neutral", "Soglie per regime", "float", 0, 1, 0.05),
    Param(("confidence", "thresholds", "Bear"), "Soglia in Bear", "Soglie per regime", "float", 0, 1, 0.05),
    Param(("multi_factor", "weights", "momentum"), "Peso momentum", "Multi-factor", "float", 0, 1, 0.05),
    Param(("multi_factor", "weights", "quality"), "Peso quality", "Multi-factor", "float", 0, 1, 0.05),
    Param(("multi_factor", "weights", "volatility"), "Peso bassa volatilità", "Multi-factor", "float", 0, 1, 0.05),
    Param(("valuation", "skip_sectors"), "Settori senza DCF", "Valutazione", "sectors",
          help="Per questi settori il margine DCF vale zero."),
    Param(("valuation", "max_growth"), "Crescita massima nel DCF", "Valutazione", "float", 0, 0.3, 0.01),
    Param(("portfolio", "max_positions"), "Titoli in portafoglio", "Portafoglio", "int", 5, 600, 1),
    Param(("portfolio", "max_weight"), "Peso massimo per titolo", "Portafoglio", "float", 0.001, 0.5, 0.01),
    Param(("portfolio", "method"), "Metodo dei pesi", "Portafoglio", "choice",
          options=("risk_parity", "min_variance", "equal")),
    Param(("backtest", "use_exits"), "Stop-loss e take-profit", "Uscite e ribilanciamento", "bool"),
    Param(("execution", "stop_loss_atr"), "Stop-loss (multipli di ATR)", "Uscite e ribilanciamento", "float",
          0.5, 6, 0.5),
    Param(("execution", "take_profit_atr"), "Take-profit (multipli di ATR)", "Uscite e ribilanciamento", "float",
          0.5, 10, 0.5),
    Param(("backtest", "rebalance_days"), "Ribilanciamento ogni (giorni di borsa)", "Uscite e ribilanciamento",
          "int", 5, 63, 1),
    Param(("risk", "max_sector_weight"), "Peso massimo per settore", "Rischio di portafoglio", "float",
          0.05, 1, 0.05, help="1 = nessun tetto. L'eccesso va agli altri settori o resta liquido."),
    Param(("risk", "target_volatility"), "Volatilità obiettivo (annua)", "Rischio di portafoglio", "float",
          0, 0.5, 0.01, help="0 = nessuna. Se la volatilità stimata è più alta, si riduce l'esposizione."),
    Param(("risk", "vol_lookback_days"), "Giorni per stimare la volatilità", "Rischio di portafoglio", "int",
          21, 252, 1),
    Param(("risk", "drawdown_limit"), "Limite di perdita dal massimo", "Rischio di portafoglio", "float",
          -0.6, 0, 0.05, help="0 = nessuno. Es. -0.15: oltre il -15% dal massimo si riduce l'esposizione."),
    Param(("risk", "drawdown_exposure"), "Esposizione oltre il limite di perdita", "Rischio di portafoglio",
          "float", 0, 1, 0.05),
] + [Param(("strategy", "factor_weights", f.key), f.label, "Pesi dei fattori (solo modalità factors)", "float",
           0, 1, 0.05) for f in FACTORS]

PERIODS = {"all": "Tutto il periodo", "first": "Prima metà (per scegliere)", "second": "Seconda metà (per verificare)"}
PARAM_BY_KEY = {".".join(p.path): p for p in PARAMS}

# modifiche pronte per le domande emerse dall'analisi
PRESETS: dict[str, dict] = {
    "Senza stop-loss/take-profit": {"backtest.use_exits": False},
    "Regime fuori dal punteggio": {"confidence.weights.regime": 0.0, "confidence.thresholds.Bull": 0.5,
                                   "confidence.thresholds.Neutral": 0.5, "confidence.thresholds.Bear": 0.5},
    "REIT senza DCF": {"valuation.skip_sectors": ["Financials", "Real Estate"]},
    # termine di confronto per chi sceglie titoli: tutti i membri dell'indice (con un anno di prezzi), stesso peso
    "Tutto l'indice a pesi uguali": {"strategy.mode": "factors", "strategy.factor_weights.mom_12_1": 1.0,
                                     "portfolio.max_positions": 600, "portfolio.max_weight": 0.01,
                                     "portfolio.method": "equal", "backtest.use_exits": False},
    "Solo multi-factor": {"confidence.weights.regime": 0.0, "confidence.weights.value": 0.0,
                          "confidence.thresholds.Bull": 0.6, "confidence.thresholds.Neutral": 0.6,
                          "confidence.thresholds.Bear": 0.6},
}


def get_value(cfg: dict, path: tuple[str, ...]):
    node = cfg
    for k in path:
        if path[:2] == ("strategy", "factor_weights") and k not in node:
            return 0.0  # peso non indicato = fattore non usato
        node = node[k]
    return node


def apply_overrides(base: dict, overrides: dict) -> dict:
    """Copia della configurazione con le modifiche ({"a.b.c": valore}). La base non viene toccata."""
    cfg = copy.deepcopy(base)
    for key, value in overrides.items():
        if key not in PARAM_BY_KEY:
            raise KeyError(f"Parametro non modificabile dal laboratorio: {key}")
        *parents, last = PARAM_BY_KEY[key].path
        node = cfg
        for k in parents:
            node = node.setdefault(k, {})
        node[last] = copy.deepcopy(value)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    st = cfg.get("strategy", {})
    if st.get("mode") == "factors" and not any(v > 0 for v in (st.get("factor_weights") or {}).values()):
        raise ValueError("Modalità factors: almeno un peso dei fattori deve essere maggiore di zero")
    if sum(cfg["confidence"]["weights"].values()) <= 0:
        raise ValueError("Almeno un peso della confidenza deve essere maggiore di zero")
    if sum(cfg["multi_factor"]["weights"].values()) <= 0:
        raise ValueError("Almeno un peso del multi-factor deve essere maggiore di zero")


def factor_preset(weights: dict[str, float]) -> dict:
    """Strategia a fattori con i pesi trovati dalla ricerca: sempre investita, 50 titoli a pesi uguali."""
    preset = {"strategy.mode": "factors", "backtest.use_exits": False, "portfolio.method": "equal",
              "portfolio.max_positions": 50, "portfolio.max_weight": 0.05}
    for f in FACTORS:
        preset[f"strategy.factor_weights.{f.key}"] = round(float(weights.get(f.key, 0.0)), 2)
    return preset


def changes(base: dict, overrides: dict) -> dict:
    """Solo le modifiche che cambiano davvero un valore rispetto alla base."""
    out = {}
    for key, value in overrides.items():
        old = get_value(base, PARAM_BY_KEY[key].path)
        same = (sorted(old) == sorted(value)) if isinstance(old, list) else old == value
        if not same:
            out[key] = value
    return out


def describe(base: dict, overrides: dict) -> str:
    if not overrides:
        return "configurazione attuale"
    parts = []
    for key, value in overrides.items():
        p = PARAM_BY_KEY[key]
        old = get_value(base, p.path)
        fmt = (lambda v: ", ".join(v) or "nessuno") if p.kind == "sectors" else (
            lambda v: "sì" if v is True else "no" if v is False else f"{v:g}" if isinstance(v, float) else str(v))
        parts.append(f"{p.label}: {fmt(old)} → {fmt(value)}")
    return "; ".join(parts)


def overrides_yaml(overrides: dict) -> str:
    """Le modifiche nel formato di config.yaml, da copiare se si vuole adottarle."""
    tree: dict = {}
    for key, value in overrides.items():
        *parents, last = PARAM_BY_KEY[key].path
        node = tree
        for k in parents:
            node = node.setdefault(k, {})
        node[last] = value
    import yaml

    return yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)


@dataclass
class Experiment:
    name: str
    overrides: dict
    config: dict
    metrics: dict
    benchmark_metrics: dict
    equity: pd.Series
    benchmark: pd.Series
    research: pd.DataFrame = field(default_factory=pd.DataFrame)
    period: str = "all"
    coverage: float | None = None
    data_as_of: str = ""
    seconds: float = 0.0
    info: dict = field(default_factory=dict)


def period_bounds(cfg: dict, calendar: pd.DatetimeIndex, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inizio e fine del backtest per il periodo scelto (le metà sono divise a metà del tempo)."""
    end = calendar[-1]
    b = cfg["backtest"]
    start = pd.Timestamp(b["start"]) if b.get("start") else end - pd.DateOffset(years=b["years"])
    start = max(start, calendar[0])
    mid = start + (end - start) / 2
    return {"all": (start, end), "first": (start, mid), "second": (mid, end)}[period]


def run_experiment(base_cfg: dict, overrides: dict, data, tickers: list[str], sectors: dict[str, str],
                   universe_fn=None, name: str = "", with_research: bool = False, period: str = "all") -> Experiment:
    """Backtest (e ricerca) con la configurazione modificata, sugli stessi dati di main.py."""
    cfg = apply_overrides(base_cfg, overrides)
    return run_config(cfg, name or describe(base_cfg, overrides), overrides, data, tickers, sectors, universe_fn,
                      with_research, period)


def run_config(cfg: dict, name: str, overrides: dict, data, tickers: list[str], sectors: dict[str, str],
               universe_fn=None, with_research: bool = False, period: str = "all") -> Experiment:
    """Backtest di una configurazione completa (es. una strategia a regole); `overrides` è solo la descrizione
    registrata con l'esperimento."""
    t0 = time.time()
    start, end = period_bounds(cfg, data.calendar, period)
    strategy = Strategy(cfg, tickers=tickers, sectors=sectors)
    bt = Backtester(cfg, strategy=strategy, universe_fn=universe_fn).run(data, start=start, end=end)
    research = pd.DataFrame()
    if with_research:
        rcfg = copy.deepcopy(cfg)
        rcfg["research"]["start"] = str(start.date())
        research = SignalResearch(rcfg, strategy, universe_fn=universe_fn).run(data, end=end).summary
    return Experiment(
        name=name, overrides=overrides, config=cfg, metrics=bt.metrics,
        benchmark_metrics=bt.benchmark_metrics, equity=bt.equity, benchmark=bt.benchmark, research=research,
        period=period, coverage=bt.metrics.get("avg_coverage"),
        data_as_of=str(pd.Timestamp(data.last_date).date()), seconds=time.time() - t0,
        info=run_info.collect(cfg, ["laboratorio"]),
    )
