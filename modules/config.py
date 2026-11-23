"""Caricamento della configurazione (config.yaml) con valori di default."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "universe": {
        "source": "list",
        "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"],
        "refresh_days": 7,
        "point_in_time_membership": True,
        "membership": "history",
        "benchmark": "SPY",
        "risk_free_ticker": "^TNX",
    },
    "data": {
        "db_path": "data/finance_lake.db",
        "history_years": 5,
        "fundamentals_source": "sec",
        "fundamentals_refresh_days": 7,
        "fundamentals_max_age_days": 90,
        "fundamentals_due_days": 130,
        "price_chunk_size": 100,
        "price_chunk_pause": 1.0,
        "price_retries": 3,
        "max_stale_days": 5,
        "backup_dir": "data/backups",
        "backup_keep": 7,
        "facts_db_path": "data/sec_facts.db",
    },
    "sec": {
        "user_agent": "",
        "max_requests_per_second": 8,
        "extra_ciks": {},
        "filings_lookback_days": 30,
        "filings_index_wait_days": 4,
        "filings_current_pages": 10,
        "filings_give_up_days": 10,
        "share_classes": {},
    },
    "my_portfolio": {"benchmark": "SPY", "benchmark_currency": "USD"},  # portafoglio reale (pagina Portafoglio)
    "etf": {"cache_dir": "data/cache/etf", "max_age_days": 30, "not_fund_max_age_days": 180,
            "fetch_pause_seconds": 0.5, "top_n": 10, "aliases": {}},
    "europe": {"enabled": False, "indices": ["FTSE MIB", "Euro Stoxx 50"], "refresh_days": 7,
               "benchmark": "^STOXX50E"},  # indice per il beta delle europee (costo del capitale)
    # stime degli analisti (modules/estimates.py): solo informazione, mai nel giudizio; Yahoo solo per le aziende
    # seguite e nella pipeline, al massimo `max_tickers` per aggiornamento, riscaricate dopo `max_age_days`
    "estimates": {"enabled": False, "max_age_days": 3, "max_tickers": 60},
    # bilanci di riserva da Yahoo dove SEC/ESEF mancano (modules/yahoo_fundamentals.py); mai in demo
    "yahoo_fundamentals": {"enabled": False, "cache_dir": "data/cache/yahoo_fin", "max_age_days": 7,
                           "stale_days": 450, "no_shares": ["V", "ARES"]},
    # grafico dei prezzi della scheda (funzione GP, ui/pages/azienda/prezzo.py)
    "price_chart": {
        "monthly_after_years": 6,         # MAX oltre questi anni: candele mensili invece che settimanali
        "max_missing_open": 0.02,         # oltre questa quota di sedute senza apertura/massimo/minimo: solo linea
        "fair_value_range": [0.5, 2.0],   # fair value disegnato solo tra 0,5x il minimo e 2x il massimo del periodo
    },
    "monitor": {
        "symbols": {},  # in config.yaml (un dizionario: unito a questo, i predefiniti cambierebbero l'ordine)
        "rates": ["^TNX"],
        "exchanges": {},  # in config.yaml: codice borsa -> [simbolo Yahoo dell'indice, nome]
        "refresh_open_seconds": 300,
        "refresh_closed_seconds": 3600,
        "news_days": 3,
    },
    "news": {  # notizie da feed RSS pubblici (modules/news.py, pagina Notizie)
        "enabled": True,
        "feeds": [],  # in config.yaml: [{name, url, lang, group, every}]
        "every_seconds": 120,       # feed generali
        "pause_seconds": 1.0,       # tra una richiesta e l'altra
        "timeout_seconds": 15,
        "max_backoff_seconds": 3600,  # un feed che fallisce si riprova con attesa crescente, al massimo questa
        "keep_days": 3,             # memoria in cache_dir: le notizie più vecchie si buttano
        "cache_dir": "data/cache/news",
        "similarity": 0.92,         # titoli quasi uguali da fonti diverse = stessa notizia
        "ui_refresh_seconds": 30,   # la pagina rilegge la memoria (non la rete)
        "aliases": {},              # ticker -> altri nomi nei titoli (es. GOOGL: [Google])
        "x_accounts": [],           # account X iniziali (riquadro ufficiale); poi si gestiscono dalla pagina
        "x_height": 600,            # altezza in pixel di ogni riquadro X
        "company": {"every_seconds": 900, "yahoo_url": "", "google_url": "", "google_url_us": "",
                    "google_for_us": False, "google_lang_foreign": "it"},
    },
    "schedule": {
        "after_close_minutes": 30,
        "check_every_minutes": 60,
        "args": [],
    },
    "valuation": {
        "equity_risk_premium": 0.05,
        "risk_free_fallback": 0.042,
        "terminal_growth": 0.025,
        "projection_years": 10,
        "min_growth": 0.0,
        "max_growth": 0.15,
        "default_growth": 0.05,
        "wacc_floor": 0.06,
        "beta_window_days": 504,
        "default_tax_rate": 0.21,
        "max_fcf_lag_days": 400,
        "skip_sectors": ["Financials"],
        "marginal_tax_rate": 0.25,
        "erp_growth": 0.05,
        "beta_years": 5,
        "min_industry_peers": 10,
        "rnd_min_intensity": 0.03,
        "rnd_life_years": {"default": 5, "Information Technology": 3, "Communication Services": 3, "Health Care": 10},
        "synthetic_ratings": None,  # None = DEFAULT_RATINGS di modules/cost_of_capital.py
    },
    "regime": {
        "n_states": 3,
        "lookback_days": 756,
        "vol_window": 20,
        "min_observations": 250,
    },
    "multi_factor": {
        "weights": {"momentum": 0.35, "quality": 0.45, "volatility": 0.20},
    },
    "confidence": {
        "weights": {"regime": 0.30, "value": 0.40, "multi_factor": 0.30},
        "full_value_margin": 0.30,
        "thresholds": {"Bull": 0.50, "Neutral": 0.65, "Bear": 0.80},
        "default_threshold": 0.65,
    },
    "safety": {
        "max_daily_vol": 0.05,
        "outlier_sigma": 3.0,
        "min_history_days": 20,
    },
    "portfolio": {
        "method": "risk_parity",
        "max_weight": 0.20,
        "max_positions": 25,
        "lookback_days": 252,
    },
    "insiders": {"history_quarters": 12, "window_days": 90, "alert_min_value": 100000, "alert_days": 30},
    "superinvestors": {"history_quarters": 54, "funds": []},
    # sito pubblico statico (modules/public_site.py, che ha gli altri default: titolo, testi, fondi, avvisi)
    "public_site": {"enabled": False, "out_dir": "build/sito", "base_url": ""},
    "watchlist": {
        "new_filing_days": 14,
        "big_move_1m": 0.15,
        "drawdown_alert": -0.25,
        "margin_drop": 0.03,
        "near_fair_value": 0.05,
    },
    "alerts": {
        "enabled": True,
        "codes": ["PX-", "PX+", "FV", "CHG", "INS", "MOV", "MOV1", "REV", "8K", "VRD"],
        "fair_value_margin": 0.0,
        "new_filing_days": 14,
        "big_move_1m": 0.15,
        "big_move_1d": 0.07,
        "event_days": 7,
        "insider_days": 30,
        "insider_min_value": 100000,
        "summary_lines": 3,
        "thesis_gap": 0.03,
        "change_thresholds": {},
        "verdict_days": 30,
        "verdict_nodata_days": 3,
        "verdict_min_pillar_change": 0.01,
    },
    # giudizio (modules/verdict.py): soglie e pesi hanno i default nel modulo; qui solo lo storico
    "verdict": {
        "history": {"enabled": True, "keep_rule_days": 30},
        "tracking": {"min_age_days": 21, "min_independent_periods": 20},
    },
    # tavolo dei grandi investitori (modules/investor_panel.py): solo informazione, mai nel giudizio
    "investor_panel": {
        "near_miss": 0.10,          # "Quasi": una sola regola mancata di al massimo il 10% della soglia
        "near_miss_rank": 0.10,     # ...o, per "tra i migliori", di al massimo 10 punti percentili
        "near_miss_abs": 0.01,      # ...o, se la soglia è zero, di al massimo 0,01 (1 punto per le percentuali)
        "price_range": [0.05, 20.0],  # prezzi cercati tra 0,05 e 20 volte quello di oggi
    },
    "risk": {
        "max_sector_weight": 0.30,
        "target_volatility": 0.0,
        "vol_lookback_days": 63,
        "drawdown_limit": 0.0,
        "drawdown_exposure": 0.5,
        "report_lookback_days": 252,
        "var_confidence": 0.95,
        "market_shock": -0.20,
        "sector_shock": -0.30,
        "stress_scenarios": {
            "Crisi finanziaria (nov 2008 - mar 2009)": ["2008-11-04", "2009-03-09"],
            "Debito USA 2011": ["2011-07-22", "2011-10-03"],
            "Vendite di fine 2018": ["2018-09-20", "2018-12-24"],
            "Covid 2020": ["2020-02-19", "2020-03-23"],
            "Inflazione e tassi 2022": ["2022-01-03", "2022-10-12"],
        },
    },
    "execution": {
        "capital": 100000,
        "atr_period": 14,
        "stop_loss_atr": 2.0,
        "take_profit_atr": 3.0,
        "limit_order_vol": 0.30,
        "min_dollar_volume": 1_000_000,
        "max_slippage": 0.005,
    },
    "quality": {
        "max_missing_days": 10,
        "max_daily_jump": 0.40,
        "max_filing_age_days": 450,
        "max_shares_change": 3.0,
        "min_period_items_share": 0.6,  # ultimo periodo con meno del 60% delle voci ufficiali del precedente: avviso
    },
    "research": {
        "years": 3,
        "start": None,
        "step_days": 21,
        "horizons": [5, 21, 63, 126],
        "n_quantiles": 5,
        "min_names": 20,
        "t_strong": 3.0,
        "t_weak": 2.0,
    },
    "strategy": {
        # run_on_update (facoltativo, assente = falso): strategia automatica originale a ogni aggiornamento
        # (main.py la esegue solo con --strategy). Niente valore qui: entrerebbe nell'impronta della configurazione
        # (run_info.config_hash) e farebbe sembrare vecchi tutti i risultati salvati delle strategie.
        "mode": "confidence",
        "factor_weights": {},
    },
    "factors": {
        "weight_horizon": 21,
        "min_t": 0.0,
        "exclude": ["reversal_1m"],
        "max_statement_age_days": 550,
    },
    "backtest": {
        "years": 2,
        "start": None,
        "rebalance_days": 21,
        "commission": 0.001,
        "slippage": 0.0005,
        "min_trade_pct": 0.005,
        "use_exits": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Legge config.yaml e lo fonde con i default. I percorsi diventano assoluti."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    user_cfg: dict = {}
    if path.exists():
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ImportError("Manca PyYAML: esegui `pip install -r requirements.txt`") from exc
        with open(path, encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
    cfg = _deep_merge(DEFAULTS, user_cfg)
    for key in ("db_path", "backup_dir", "facts_db_path"):
        path_value = Path(cfg["data"][key])
        if not path_value.is_absolute():
            path_value = PROJECT_ROOT / path_value
        cfg["data"][key] = str(path_value)
    return cfg


def default_config() -> dict[str, Any]:
    """Copia dei default (utile per i test)."""
    return copy.deepcopy(DEFAULTS)


# ---------------------------------------------------------------- database della modalità demo
DEMO_DB_PATH = PROJECT_ROOT / "data" / "demo.db"  # per gli strumenti; main.py e la webapp usano demo_db_path()


def _holds_real_data(db_path: Path) -> bool:
    """True se il file è un database con un'esecuzione di main.py SENZA --demo come ultima (dati veri).
    Un file che non esiste, vuoto o senza esecuzioni non contiene dati veri."""
    import sqlite3

    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT argv FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:  # tabella runs assente: nessuna esecuzione registrata
        return False
    return bool(row) and "--demo" not in str(row[0] or "")


def demo_db_path(cfg: dict[str, Any], config_path: str | Path | None = None) -> Path:
    """Database usato con `--demo` (main.py e webapp, stessa regola).

    - Senza `--config`, o se la configurazione lascia il database dei dati veri (quello di config.yaml o
      `data/finance_lake.db`: una configurazione che cambia solo i parametri): `data/demo.db`.
    - Altrimenti il `db_path` della configurazione (es. una copia della demo per collaudi e test), ma MAI un
      database con dati veri: se l'ultima esecuzione registrata non era `--demo` si ferma con un errore.
    Così un comando "demo" non scrive mai nei dati veri, nemmeno con una configurazione sbagliata."""
    demo = PROJECT_ROOT / "data" / "demo.db"  # letto ora, non all'import: i test spostano PROJECT_ROOT
    if config_path is None:
        return demo
    configured = Path(cfg["data"]["db_path"]).resolve()
    real = {Path(load_config(None)["data"]["db_path"]).resolve(),
            (PROJECT_ROOT / DEFAULTS["data"]["db_path"]).resolve()}
    if configured in real:
        return demo
    if _holds_real_data(configured):
        raise ValueError(f"--demo: il database {configured} contiene dati veri (ultima esecuzione senza --demo). "
                         "Per la demo usa un database separato (data.db_path nella configurazione).")
    return configured
