"""
QUANT_ENGINE — orchestratore.

Uso:
  python main.py                 aggiorna universo, prezzi e bilanci; qualità dei dati, storico dei giudizi, avvisi
  python main.py --backtest      come sopra + backtest della stessa strategia
  python main.py --only-backtest solo backtest (sui dati già nel database)
  python main.py --research      ricerca dei segnali: ogni punteggio prevede i rendimenti futuri?
  python main.py --factors       ricerca dei fattori classici e della loro combinazione (walk-forward)
  python main.py --no-update     salta il download (usa i dati già salvati)
  python main.py --demo          tutto su dati simulati (nessuna connessione necessaria)
  python main.py --list          usa solo i ticker di config.yaml invece dell'S&P 500
  python main.py --strategy      anche la strategia automatica originale (regime, confidenza, ordini simulati),
                                 facoltativa: di norma non gira (strategy.run_on_update: false)

Ogni aggiornamento è fatto di passi (modules/pipeline.py) con esito, durata e conteggi: il riepilogo va in
data/logs/aggiornamento_riepilogo.json (menu "Dati" della webapp, notifica dell'aggiornamento automatico).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from data.db_manager import DBManager, FactsStore
from modules import run_info
from modules.backtester import Backtester
from modules.config import PROJECT_ROOT, demo_db_path, load_config
from modules.data_ingestor import DataIngestor
from modules.data_quality import DataQualityChecker, stale_prices_summary, summary as quality_summary
from modules.execution_engine import ExecutionEngine
from modules.market_data import MarketData
from modules.pipeline import Pipeline, compact, summary_path
from modules.report import write_analysis_report, write_backtest_report, write_factor_report, write_research_report
from modules.research import FactorResearch, SignalResearch
from modules.strategy import Strategy
from modules.universe import UniverseManager
from modules.technical import annualized_volatility, average_dollar_volume, average_true_range

logger = logging.getLogger("quant_engine")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="QUANT_ENGINE: analisi quantitativa e backtest")
    p.add_argument("--config", default=None, help="percorso del file di configurazione")
    p.add_argument("--no-update", action="store_true", help="non scaricare dati, usa il database")
    p.add_argument("--force-fundamentals", action="store_true", help="riscarica i bilanci anche se recenti")
    p.add_argument("--backtest", action="store_true", help="esegui anche il backtest")
    p.add_argument("--only-backtest", action="store_true", help="esegui solo il backtest")
    p.add_argument("--research", action="store_true", help="ricerca dei segnali (IC, quantili, decadimento)")
    p.add_argument("--factors", action="store_true", help="ricerca dei fattori classici e combinazione")
    p.add_argument("--demo", action="store_true", help="usa dati simulati (data/demo.db; con --config il suo db_path, mai un database con dati veri)")
    p.add_argument("--list", action="store_true", help="usa solo universe.tickers di config.yaml")
    p.add_argument("--insiders", action="store_true",
                   help="scarica insider (Form 4) e portafogli dei grandi investitori (13F); poi si aggiornano da soli")
    p.add_argument("--strategy", action="store_true",
                   help="esegui anche la strategia automatica originale (ordini simulati); facoltativa")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def run_factor_research(cfg: dict, data: MarketData, universe: UniverseManager, run_id: str, reports_dir: Path,
                        version: str | None = None):
    res = FactorResearch(cfg, universe_fn=_universe_fn(cfg, universe), sectors=universe.sectors()).run(data)
    h = res.params["weight_horizon"]
    best = res.summary[res.summary["horizon"] == h].sort_values("ic_t", ascending=False).head(5)
    for r in best.itertuples(index=False):
        logger.info("Fattore %-28s IC %+.3f  t %+.2f  (%s, %d giorni)", r.signal, r.ic_mean, r.ic_t, r.verdict, h)
    logger.info("Pesi della combinazione (stimati fino al %s): %s", res.params["split"],
                {k: round(v, 2) for k, v in res.weights.items()})
    for r in res.composite[res.composite["horizon"] == h].itertuples(index=False):
        logger.info("Combinazione, %s: IC %+.3f  t %+.2f", r.period, r.ic_mean, r.ic_t)
    if res.weights:
        import yaml

        snippet = yaml.safe_dump({"strategy": {"mode": "factors", "factor_weights": {
            k: round(v, 2) for k, v in sorted(res.weights.items(), key=lambda kv: -kv[1])}}}, sort_keys=False)
        logger.info("Per provarli come strategia (meglio prima nel Laboratorio, sulla seconda metà):\n%s", snippet)
    path = write_factor_report(res, run_id, reports_dir, version=version)
    logger.info("Report fattori: %s", path)
    return res


def run_quality(cfg: dict, db: DBManager, data: MarketData, tickers: list[str]):
    issues = DataQualityChecker(cfg["quality"], cfg["data"]["max_stale_days"]) \
        .run(data, tickers, db.load_splits(tickers))
    if issues.empty:
        logger.info("Qualità dei dati: nessun problema")
    for r in quality_summary(issues).itertuples(index=False):
        log = logger.warning if r.severity == "errore" else logger.info
        log("Qualità dei dati [%s] %s: %d titoli", r.severity, r.check, r.tickers)
    stale = stale_prices_summary(issues)
    if stale:
        logger.warning("Qualità dei dati: %s", stale)
    return issues


def record_in_status(**fields) -> None:
    """Aggiunge campi allo stato dell'aggiornamento (data/logs/aggiornamento_stato.json) solo se main.py è stato
    lanciato da modules.updater (pulsante o aggiornamento automatico): l'updater, a fine corsa, li conserva."""
    import os

    from modules import updater

    try:
        status = updater.read_status()
        if status.get("finished") is None and status.get("pid") == os.getppid():
            status.update(fields)
            updater._write_status(updater.ROOT, status)
    except Exception as exc:  # noqa: BLE001 - lo stato è un di più: non deve far fallire l'aggiornamento
        logger.debug("Stato dell'aggiornamento non scritto: %s", exc)


def run_analysis(cfg: dict, db: DBManager, data: MarketData, strategy: Strategy, run_id: str, reports_dir: Path,
                 quality=None, version: str | None = None):
    snapshot = data.as_of(data.last_date)
    result = strategy.run(snapshot)
    r = result.regime
    logger.info("Regime di mercato: %s (%s)", r.label, r.method)

    verbose = len(result.decisions) <= 30
    for d in result.decisions:
        if verbose or d["action"] != "SKIP":
            logger.info("%-6s %-7s %s", d["ticker"], d["action"], d.get("reasoning", ""))
    counts = {a: sum(d["action"] == a for d in result.decisions) for a in ("BUY", "SKIP", "BLOCKED")}
    logger.info("Decisioni: %d BUY, %d SKIP, %d BLOCKED", counts["BUY"], counts["SKIP"], counts["BLOCKED"])
    db.log_decisions(run_id, result.decisions)

    engine = ExecutionEngine(cfg["execution"])
    orders = []
    for ticker, weight in result.weights.items():
        ohlc = snapshot.ohlc(ticker)
        order = engine.build_order(
            ticker, float(weight), float(ohlc["close"].iloc[-1]),
            average_true_range(ohlc, cfg["execution"]["atr_period"]),
            annualized_volatility(ohlc["close"]), average_dollar_volume(ohlc),
        )
        if order:
            orders.append(order.to_dict())
            logger.info("ORDINE SIMULATO %s %s %d azioni (peso %.1f%%) SL %s TP %s", order.order_type,
                        ticker, order.shares, weight * 100, order.stop_loss, order.take_profit)
    if orders:
        db.log_orders(run_id, orders)
    else:
        logger.info("Nessun titolo ha superato i filtri: nessun ordine.")

    risk = {"measures": strategy.risk.measure(result.weights, snapshot),
            "stress": strategy.risk.stress_test(result.weights, snapshot)}
    m = risk["measures"]
    if result.risk.notes:
        logger.info("Limiti di rischio: %s", "; ".join(result.risk.notes))
    if "volatility" in m:
        logger.info("Rischio: investito %.0f%%, volatilità stimata %.1f%%, beta %.2f, VaR 1 giorno %.1f%%",
                    m["exposure"] * 100, m["volatility"] * 100, m["beta"], m["var_1d"] * 100)
    path = write_analysis_report(result, orders, run_id, reports_dir, quality=quality, version=version, risk=risk)
    logger.info("Report analisi: %s", path)
    return result, orders


def _universe_fn(cfg: dict, universe: UniverseManager):
    """Universo point-in-time (solo i titoli già entrati nell'indice a ogni data), se configurato."""
    pit = universe.source == "sp500" and cfg["universe"].get("point_in_time_membership", True)
    return (lambda day: universe.tickers(as_of=day)) if pit else None


def run_backtest(cfg: dict, db: DBManager, data: MarketData, strategy: Strategy, universe: UniverseManager,
                 run_id: str, reports_dir: Path, version: str | None = None):
    b = cfg["backtest"]
    logger.info("Backtest in corso (%s, ribilanciamento ogni %d giorni)...",
                f"dal {b['start']}" if b.get("start") else f"{b['years']} anni", b["rebalance_days"])
    bt = Backtester(cfg, strategy=strategy, universe_fn=_universe_fn(cfg, universe)).run(data)
    m, b = bt.metrics, bt.benchmark_metrics
    logger.info("Strategia: rendimento %.1f%%, Sharpe %.2f, max drawdown %.1f%%, %d operazioni",
                m["total_return"] * 100, m["sharpe_ratio"], m["max_drawdown"] * 100, m["n_trades"])
    if b:
        logger.info("Benchmark: rendimento %.1f%%, Sharpe %.2f, max drawdown %.1f%%",
                    b["total_return"] * 100, b["sharpe_ratio"], b["max_drawdown"] * 100)
    db.log_backtest(run_id, bt.params, {"strategy": m, "benchmark": b})
    path = write_backtest_report(bt, run_id, reports_dir, version=version)
    logger.info("Report backtest: %s", path)
    return bt


def run_research(cfg: dict, data: MarketData, strategy: Strategy, universe: UniverseManager,
                 run_id: str, reports_dir: Path, version: str | None = None):
    res = SignalResearch(cfg, strategy, universe_fn=_universe_fn(cfg, universe)).run(data)
    main_h = 21 if 21 in res.params["horizons"] else res.params["horizons"][0]
    for r in res.summary[res.summary["horizon"] == main_h].itertuples(index=False):
        if getattr(r, "n_dates", 0):
            logger.info("Segnale %-17s IC %+.3f  t %+.2f  (%s, %d giorni)", r.signal, r.ic_mean, r.ic_t,
                        r.verdict, main_h)
    path = write_research_report(res, run_id, reports_dir, version=version)
    logger.info("Report ricerca: %s", path)
    return res


def run_alerts(cfg: dict, db: DBManager, data: MarketData, today=None) -> list[dict]:
    """Avvisi delle aziende seguite e in portafoglio (modules/alerts.py): registra solo quelli nuovi. Un errore va nel
    log e non blocca il resto."""
    from modules.alerts import evaluate

    try:
        new = evaluate(db, data, cfg, today if today is not None else pd.Timestamp.now().floor("s"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Avvisi non valutati: %s", exc)
        return []
    for a in new:
        logger.info("AVVISO %-6s %-4s %s", a["ticker"], a["code"], a["message"])
    logger.info("Avvisi: %d nuovi", len(new))
    return new


def run_verdict_history(cfg: dict, db: DBManager, data: MarketData, run_id: str, reports_dir: Path) -> dict | None:
    """Storico dei giudizi di tutto l'universo alla data dei dati (modules/verdict_history.py), prima degli avvisi
    (l'avviso VRD lo legge). Un errore va nel log e non blocca il resto."""
    from modules import verdict_history

    if not verdict_history.history_settings(cfg).get("enabled", True):
        return None
    try:
        res = verdict_history.record(db, cfg, data=data, reports_dir=reports_dir, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Storico dei giudizi non scritto: %s", exc)
        return None
    logger.info("Storico dei giudizi: %d aziende al %s in %.1f s (%s)", res["rows"], res["as_of"], res["seconds"],
                ", ".join(f"{k} {v}" for k, v in sorted(res["by_category"].items())))
    return res


def history_start(cfg: dict) -> pd.Timestamp:
    return pd.Timestamp.now().normalize() - pd.DateOffset(years=int(cfg["data"]["history_years"]))


def build_sources(cfg: dict, db: DBManager):
    """Sorgenti reali: prezzi da Yahoo, bilanci da SEC (o Yahoo), universo da Wikipedia/SEC."""
    from modules.data_ingestor import YahooSource

    fsrc = cfg["data"].get("fundamentals_source", "sec")
    ua = (cfg.get("sec", {}).get("user_agent") or "").strip()
    needs_sec = fsrc == "sec" or cfg["universe"].get("source") == "sp500"
    if needs_sec and ("@" not in ua or len(ua.split()) < 2):
        raise ValueError("Imposta sec.user_agent in config.yaml con nome ed email "
                         "(es. \"Mario Rossi mario@esempio.it\"): la SEC lo richiede per ogni richiesta.")
    price_source = YahooSource()
    provider = fundamentals_source = None
    if needs_sec:
        from modules.http_client import RateLimitedClient
        from modules.universe import WebUniverseProvider

        http = RateLimitedClient(ua, max_per_second=cfg["sec"].get("max_requests_per_second", 8))
        provider = WebUniverseProvider(http)
        if fsrc == "sec":
            from modules.sec_edgar import SecSource
            fundamentals_source = SecSource(http, db.company_ciks(), cfg["sec"].get("extra_ciks"))
    if fsrc == "yahoo":
        fundamentals_source = price_source
    return price_source, fundamentals_source, provider


def check_filings(cfg: dict, db: DBManager, source, tickers: list[str]) -> list[str]:
    """Aziende con un 10-Q/10-K nuovo nell'indice SEC (da riscaricare subito). Un errore non blocca: restano
    il controllo per età dei bilanci e, al giro dopo, l'indice dei giorni mancati."""
    from modules.sec_filings import FilingWatcher

    extra = cfg["sec"].get("extra_ciks") or {}
    ciks = {t: [source.cik_map.get(t), *extra.get(t, [])] for t in tickers}
    try:
        watcher = FilingWatcher(db, source.http, cfg)
        pending = watcher.check(ciks)
        watcher.fill_items()  # voci degli 8-K: la pagina Notizie e la scheda azienda
    except Exception as exc:  # noqa: BLE001
        logger.error("Indice dei depositi SEC non letto: %s", exc)
        return []
    return sorted(set(pending["ticker"]) & set(tickers))


def europe_ingestor(cfg: dict, db: DBManager):
    """Aziende europee. Connessione con un nome generico: l'indirizzo email di sec.user_agent è solo per la SEC."""
    from modules.europe import EuropeIngestor
    from modules.http_client import RateLimitedClient

    name = "QUANT_ENGINE (uso personale)"
    return EuropeIngestor(db, RateLimitedClient(name, max_per_second=4), cfg,
                          archive_http=RateLimitedClient(name, max_per_second=1, backoff=4.0))


def update_share_classes(cfg: dict, db: DBManager, source) -> None:
    """Azioni delle aziende con più classi dalle copertine dei 10-Q/10-K (solo quelle non ancora lette)."""
    from modules.share_classes import ShareClassIngestor

    try:
        n = ShareClassIngestor(db, source.http, cfg).update(source.cik_map)
        logger.info("Classi di azioni: %d copertine nuove lette", n)
    except Exception as exc:  # noqa: BLE001 - non deve bloccare l'analisi
        logger.error("Azioni per classe non aggiornate: %s", exc)


def _ownership(cfg: dict, db: DBManager, data_tickers: list[str], full: bool) -> dict | None:
    """Insider e 13F (senza catturare gli errori: li registra il passo). None se non ancora caricati."""
    from modules.http_client import RateLimitedClient
    from modules.insiders import InsiderIngestor
    from modules.superinvestors import ThirteenFIngestor

    initialized = bool(db.fetched_tickers("insider_bulk"))
    if not (full or initialized):
        return None
    http = RateLimitedClient(cfg["sec"]["user_agent"], max_per_second=cfg["sec"].get("max_requests_per_second", 8))
    ciks = db.company_ciks()
    wanted = set(data_tickers)
    issuers = {}
    for t, c in ciks.items():
        if t in wanted and c is not None and not pd.isna(c):
            issuers.setdefault(int(c), t)
    ins = InsiderIngestor(db, http, cfg, PROJECT_ROOT / "data" / "sec_bulk")
    n = ins.update_bulk(issuers)
    loaded = sorted(db.fetched_tickers("insider_bulk"))
    since = pd.Period(loaded[-1].upper(), freq="Q").end_time.normalize() if loaded else pd.Timestamp.now() - pd.Timedelta(days=90)
    watch = [t for t in db.load_watchlist() if t in ciks]
    n += ins.update_recent(watch, ciks, since)
    logger.info("Insider: %d operazioni nuove (trimestri fino al %s; Form 4 recenti di %d aziende seguite)",
                n, loaded[-1] if loaded else "-", len(watch))
    comp = db.load_companies(only_index=False)[["ticker", "name"]]
    m = ThirteenFIngestor(db, http, cfg).update(comp)
    logger.info("Grandi investitori: %d posizioni nuove dai 13F", m)
    return {"insider": n, "13f": m, "trimestre": loaded[-1] if loaded else None}


def update_ownership(cfg: dict, db: DBManager, data_tickers: list[str], full: bool) -> None:
    """Insider e 13F. La prima volta (o con --insiders) carica i trimestri passati; poi a ogni esecuzione solo i
    trimestri nuovi, i Form 4 recenti delle aziende seguite e i 13F nuovi dei fondi della lista."""
    try:
        _ownership(cfg, db, data_tickers, full)
    except Exception as exc:  # noqa: BLE001 - insider e 13F non devono bloccare l'analisi
        logger.error("Insider/13F non aggiornati: %s", exc)


# ---------------------------------------------------------------- passi dell'aggiornamento (modules/pipeline.py)
def _short_list(items, n: int = 5) -> str:
    items = list(items)
    return ", ".join(items[:n]) + ("…" if len(items) > n else "")


def step_prices(r, ingestor: DataIngestor, cfg: dict, data_tickers: list[str], demo: bool = False) -> dict:
    """Prezzi e split (come DataIngestor.update_all, senza i bilanci): avviso se dei titoli dell'indice restano
    senza l'ultima seduta (il guasto dei 164 titoli fermi per due settimane). Con le europee anche il loro indice
    (`europe.benchmark`, per il beta nel costo del capitale), mai in demo."""
    u, eu = cfg["universe"], cfg.get("europe") or {}
    extra = [t for t in (u.get("benchmark"), u.get("risk_free_ticker"),
                         eu.get("benchmark") if eu.get("enabled") and not demo else None) if t]
    all_tickers = list(dict.fromkeys(list(data_tickers) + extra))
    prices = ingestor.update_prices(all_tickers)
    ingestor.update_splits(all_tickers)
    behind = prices.get("behind", [])
    r.count("righe nuove", int(prices.get("rows", 0)))
    if prices.get("reloaded"):
        r.count("storici riscaricati", len(prices["reloaded"]))
    r.count("indietro", len(behind))
    if behind:
        r.warn(f"prezzi indietro per {len(behind)} titoli dell'indice ({_short_list(behind)})")
    return prices


def step_fundamentals(r, ingestor: DataIngestor, tickers: list[str], data_tickers: list[str], force: bool,
                      refresh: list[str]) -> dict:
    results = ingestor.update_fundamentals(list(data_tickers), force=force, refresh=refresh) or {}
    failed = sorted(t for t, ok in results.items() if not ok)
    r.count("scaricati", sum(bool(v) for v in results.values()))
    if refresh:
        r.count("depositi nuovi", len(refresh))
    if failed:
        r.count("non riusciti", len(failed))
    in_index = [t for t in failed if t in set(tickers)]  # gli ex membri senza dati SEC sono la norma
    if in_index:
        r.warn(f"bilanci non scaricati per {len(in_index)} aziende dell'indice ({_short_list(in_index)})")
    return results


def step_quality(r, cfg: dict, db: DBManager, data: MarketData, tickers: list[str], run_id: str,
                 reports_dir: Path) -> pd.DataFrame:
    """Controlli di qualità; il CSV lo legge la pagina Qualità dei dati (prima lo scriveva solo l'analisi della
    strategia automatica, che ora è facoltativa)."""
    issues = run_quality(cfg, db, data, tickers)
    reports_dir.mkdir(parents=True, exist_ok=True)
    issues.to_csv(reports_dir / f"qualita_dati_{run_id}.csv", index=False)
    if issues.empty:
        r.count("problemi", 0)
        return issues
    by = issues.groupby("severity")["ticker"].nunique()
    r.count("titoli con errori", int(by.get("errore", 0)))
    r.count("titoli con segnalazioni", int(by.get("avviso", 0)))
    stale = sorted(issues.loc[issues["check"] == "prezzo vecchio", "ticker"].unique())
    if stale:
        r.warn(f"prezzo vecchio per {len(stale)} titoli ({_short_list(stale)})")
    return issues


def step_strategy(r, cfg, db, data, strategy, run_id, reports_dir, version, quality=None) -> None:
    result, orders = run_analysis(cfg, db, data, strategy, run_id, reports_dir, quality=quality, version=version)
    for a in ("BUY", "SKIP", "BLOCKED"):
        r.count(a, sum(d["action"] == a for d in result.decisions))
    r.count("ordini simulati", len(orders))


def step_verdicts(r, cfg: dict, db: DBManager, data: MarketData, run_id: str, reports_dir: Path) -> dict:
    from modules import verdict_history

    if not verdict_history.history_settings(cfg).get("enabled", True):
        r.skip("disattivato in config.yaml (verdict.history.enabled)")
    res = verdict_history.record(db, cfg, data=data, reports_dir=reports_dir, run_id=run_id)
    logger.info("Storico dei giudizi: %d aziende al %s in %.1f s (%s)", res["rows"], res["as_of"], res["seconds"],
                ", ".join(f"{k} {v}" for k, v in sorted(res["by_category"].items())))
    r.count("aziende", res["rows"])
    r.count("data dei dati", res["as_of"])
    if not res["rows"]:
        r.warn("nessun giudizio salvato")
    return res


def step_alerts(r, cfg: dict, db: DBManager, data: MarketData) -> list[dict]:
    from modules.alerts import evaluate

    new = evaluate(db, data, cfg, pd.Timestamp.now().floor("s"))
    for a in new:
        logger.info("AVVISO %-6s %-4s %s", a["ticker"], a["code"], a["message"])
    logger.info("Avvisi: %d nuovi", len(new))
    r.count("nuovi", len(new))
    return new


def step_public_site(r, cfg: dict, db: DBManager, demo: bool):
    """Sito pubblico (modules/public_site.py, facoltativo): solo se il modulo c'è e `public_site.enabled` è vero."""
    import importlib
    import importlib.util

    conf = cfg.get("public_site") or {}
    if importlib.util.find_spec("modules.public_site") is None:
        r.skip("modulo modules/public_site.py non presente")
    if not conf.get("enabled"):
        r.skip("public_site.enabled è falso in config.yaml")
    if demo:
        r.skip("dati simulati: il sito pubblico si costruisce solo con i dati veri")
    public_site = importlib.import_module("modules.public_site")
    out = Path(conf.get("out_dir") or conf.get("output_dir") or "site")
    out_dir = out if out.is_absolute() else PROJECT_ROOT / out
    res = public_site.build(cfg, db, out_dir)
    r.count("cartella", str(out_dir))
    if isinstance(res, dict):
        for k, v in list(res.items())[:6]:
            if isinstance(v, (int, float, str)):
                r.count(str(k), v)
    return res


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    reports_dir = PROJECT_ROOT / "reports"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    argv_list = [str(a) for a in (sys.argv[1:] if argv is None else argv)]

    if args.list:
        cfg["universe"]["source"] = "list"
    price_source = fundamentals_source = provider = None
    if args.demo:
        from modules.demo_data import DemoSource
        try:  # data/demo.db, o il database di --config se non contiene dati veri (modules/config.demo_db_path)
            cfg["data"]["db_path"] = str(demo_db_path(cfg, args.config))
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        demo = DemoSource()
        price_source = fundamentals_source = provider = demo
        reports_dir = reports_dir / "demo"
        logger.warning("MODALITÀ DEMO: dati simulati, non usare per decisioni reali")

    db = DBManager(cfg["data"]["db_path"])
    while db.run_exists(run_id):  # due esecuzioni nello stesso secondo: si aspetta il successivo (formato invariato)
        time.sleep(1.01 - datetime.now().microsecond / 1e6)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    info = run_info.collect(cfg, argv_list)
    version = run_info.label(info)
    db.log_run(run_id, info)
    logger.info("=== QUANT_ENGINE · run %s · %s ===", run_id, version)
    if info["git_dirty"]:
        logger.warning("Ci sono modifiche non salvate in git: questo risultato non è riproducibile dal solo commit")

    pipe = Pipeline(run_id, argv_list, demo=args.demo)
    try:
        return _run_steps(args, cfg, db, pipe, run_id, reports_dir, version,
                          price_source, fundamentals_source, provider)
    finally:
        summary = pipe.write(summary_path(cfg["data"]["db_path"], demo=args.demo))
        pipe.log_summary()
        record_in_status(summary=compact(summary))


def _run_steps(args, cfg: dict, db: DBManager, pipe: Pipeline, run_id: str, reports_dir: Path, version: str,
               price_source, fundamentals_source, provider) -> int:
    """I passi dell'aggiornamento, in ordine. Codice di uscita: 1 = impossibile procedere (fonti non configurate,
    nessun prezzo nel database), 2 = prezzi non aggiornati, falliti o saltati (l'automatico riprova all'ora dopo),
    0 = altrimenti, anche con passi falliti o avvisi (sono nel riepilogo e nella notifica)."""
    downloads = ()
    if not args.no_update and not args.demo:
        pipe.step("copia", "Copia di sicurezza del database",
                  lambda r: db.backup(cfg["data"]["backup_dir"], int(cfg["data"].get("backup_keep", 7))))
        downloads = ("copia",)  # senza copia di sicurezza non si scrive nulla di nuovo nel database
        try:
            price_source, fundamentals_source, provider = build_sources(cfg, db)
        except (ValueError, ImportError) as exc:
            logger.error("%s", exc)
            pipe.fatal = str(exc)
            return 1

    universe = UniverseManager(db, cfg, provider=None if args.no_update else provider)

    def _universe(r):
        universe.refresh()
        r.count("titoli", len(universe.tickers()))

    pipe.step("universo", "Universo (S&P 500 e aziende seguite)", _universe,
              needs=downloads if not args.no_update else ())
    europe = None
    if not args.no_update and not args.demo and cfg.get("europe", {}).get("enabled"):
        europe = europe_ingestor(cfg, db)
        # prima dei prezzi: così si scaricano nello stesso giro
        pipe.step("europa_elenco", "Elenco delle aziende europee", lambda r: europe.refresh_universe(), needs=downloads)
    tickers = universe.tickers()
    # dati anche per gli ex membri dell'indice usciti nel periodo dello storico (servono al backtest)
    data_tickers = universe.data_tickers(since=history_start(cfg))
    logger.info("Universo: %d titoli (%s), %d con gli ex membri per il backtest", len(tickers), universe.source,
                len(data_tickers))

    if not args.no_update:
        if fundamentals_source is not None and hasattr(fundamentals_source, "cik_map"):
            fundamentals_source.cik_map = db.company_ciks()  # CIK appena aggiornati
        facts_store, refresh = None, []
        if hasattr(fundamentals_source, "fetch_bundle"):  # SEC: trimestri singoli e archivio di tutti i valori
            facts_store = FactsStore(cfg["data"]["facts_db_path"])
            src = fundamentals_source

            def _filings(r):
                from modules.sec_filings import FilingWatcher

                extra = cfg["sec"].get("extra_ciks") or {}
                ciks = {t: [src.cik_map.get(t), *extra.get(t, [])] for t in data_tickers}
                watcher = FilingWatcher(db, src.http, cfg)
                pending = watcher.check(ciks)
                watcher.fill_items()  # voci degli 8-K: la pagina Notizie e la scheda azienda
                todo = sorted(set(pending["ticker"]) & set(data_tickers))
                r.count("aziende con depositi nuovi", len(todo))
                return todo

            # se l'indice SEC non si legge restano il controllo per età dei bilanci e, al giro dopo, i giorni mancati
            refresh = pipe.step("depositi", "Nuovi depositi SEC (10-Q, 10-K, 8-K)", _filings, needs=downloads,
                                default=[])
        ingestor = DataIngestor(db, cfg, price_source=price_source, fundamentals_source=fundamentals_source,
                                facts_store=facts_store)
        prices = pipe.step("prezzi", "Prezzi e split", lambda r: step_prices(r, ingestor, cfg, data_tickers, args.demo),
                           needs=downloads, default={})
        if not args.demo:  # titoli dell'indice ancora senza l'ultima seduta, letto da chi mostra lo stato
            record_in_status(prices_behind=len(prices.get("behind", [])))
        if fundamentals_source is not None:
            pipe.step("bilanci", "Bilanci (10-K, 10-Q)",
                      lambda r: step_fundamentals(r, ingestor, tickers, data_tickers, args.force_fundamentals,
                                                  refresh),
                      needs=downloads)
        if facts_store is not None and cfg["sec"].get("share_classes"):
            def _classes(r):
                from modules.share_classes import ShareClassIngestor

                r.count("copertine nuove", ShareClassIngestor(db, fundamentals_source.http, cfg)
                        .update(fundamentals_source.cik_map))

            pipe.step("classi_azioni", "Azioni per classe (copertine SEC)", _classes, needs=downloads)
        if not args.demo and cfg["data"].get("fundamentals_source", "sec") == "sec":
            def _owners(r):
                res = _ownership(cfg, db, data_tickers, full=args.insiders)
                if res is None:
                    r.skip("insider e 13F non ancora caricati: la prima volta python main.py --insiders")
                r.count("operazioni insider nuove", res["insider"])
                r.count("posizioni 13F nuove", res["13f"])

            pipe.step("insider", "Insider e grandi investitori (Form 4, 13F)", _owners, needs=downloads)
        if europe is not None:
            pipe.step("europa", "Bilanci europei", lambda r: europe.update(), needs=downloads)
        if not args.demo and cfg.get("yahoo_fundamentals", {}).get("enabled"):
            def _yahoo_fill(r):
                from modules.yahoo_fundamentals import YahooFiller

                foreign = db.load_foreign()
                extra = [] if foreign.empty else list(foreign["ticker"])
                res = YahooFiller(db, cfg).update(list(dict.fromkeys(list(tickers) + extra)))
                r.count("aziende completate", res["completate"])
                r.count("valori di riserva", res["valori"])
                if res["errori"]:
                    r.warn(f"{len(res['errori'])} aziende senza risposta da Yahoo")

            pipe.step("bilanci_riserva", "Bilanci di riserva (Yahoo, solo dove mancano)", _yahoo_fill,
                      needs=downloads)
        if not args.demo and cfg.get("estimates", {}).get("enabled"):
            def _estimates(r):
                from modules import estimates

                pipe_t = db.load_pipeline()
                mine = list(dict.fromkeys(db.load_watchlist() + ([] if pipe_t.empty else list(pipe_t["ticker"]))))
                e = cfg["estimates"]
                res = estimates.refresh(db, mine, int(e.get("max_age_days", 3)), int(e.get("max_tickers", 60)))
                r.count("aziende con stime aggiornate", res["aggiornate"])
                if res["errori"]:
                    r.warn(f"{len(res['errori'])} aziende senza stime da Yahoo")

            # solo informazione (scheda, funzione Stime): non entrano nel giudizio
            pipe.step("stime", "Stime degli analisti (aziende seguite e pipeline)", _estimates, needs=downloads)

    def _load(r):
        data = MarketData.from_db(db, cfg, data_tickers)
        if data.close.empty:
            raise RuntimeError("il database non contiene prezzi")
        missing = [t for t in tickers if t not in data.close]
        if missing:
            logger.warning("%d ticker senza prezzi, saranno saltati: %s", len(missing), missing[:20])
        r.count("titoli con prezzi", int(data.close.shape[1]))
        r.count("ultimo prezzo", f"{pd.Timestamp(data.last_date):%Y-%m-%d}" if data.last_date is not None else None)
        return data

    data = pipe.step("dati", "Caricamento dei dati", _load)
    if data is None:
        logger.error("Il database non contiene prezzi. Esegui senza --no-update (serve internet).")
        pipe.fatal = "il database non contiene prezzi (serve un aggiornamento con internet)"
        return 1

    strategy = Strategy(cfg, tickers=tickers, sectors=universe.sectors())
    need = ("dati",)
    if not args.only_backtest:
        quality = pipe.step("qualita", "Controllo della qualità dei dati",
                  lambda r: step_quality(r, cfg, db, data, tickers, run_id, reports_dir), needs=need)
        label = "Strategia automatica originale (ordini simulati)"
        if args.strategy or cfg.get("strategy", {}).get("run_on_update", False):
            pipe.step("strategia", label,
                      lambda r: step_strategy(r, cfg, db, data, strategy, run_id, reports_dir, version, quality),
                      needs=need)
        else:
            pipe.skipped("strategia", label, "facoltativa (strategy.run_on_update: false): python main.py --strategy "
                         "o pagina Strategia automatica", needs=need)
        pipe.step("giudizi", "Storico dei giudizi", lambda r: step_verdicts(r, cfg, db, data, run_id, reports_dir),
                  needs=need)
        # dopo lo storico dei giudizi (l'avviso VRD lo legge), ma non dipende dal suo esito
        pipe.step("avvisi", "Avvisi delle aziende seguite", lambda r: step_alerts(r, cfg, db, data), needs=need)
    if args.backtest or args.only_backtest:
        pipe.step("backtest", "Backtest della strategia automatica",
                  lambda r: run_backtest(cfg, db, data, strategy, universe, run_id, reports_dir, version=version),
                  needs=need)
    if args.research:
        pipe.step("ricerca", "Ricerca dei segnali",
                  lambda r: run_research(cfg, data, strategy, universe, run_id, reports_dir, version=version),
                  needs=need)
    if args.factors:
        pipe.step("fattori", "Ricerca dei fattori",
                  lambda r: run_factor_research(cfg, data, universe, run_id, reports_dir, version=version),
                  needs=need)
    if not args.only_backtest:
        pipe.step("sito_pubblico", "Sito pubblico", lambda r: step_public_site(r, cfg, db, args.demo), needs=need)
    # prezzi non aggiornati (falliti, o saltati perché è fallita la copia di sicurezza): l'automatico riprova
    return 2 if pipe.result("prezzi") is not None and not pipe.ok("prezzi") else 0


if __name__ == "__main__":
    sys.exit(main())
