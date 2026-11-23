"""
Le metriche nuove (test LBO, distanza dal default di Merton) avrebbero distinto le azioni migliori in passato?

    .venv/bin/python tools/metric_research.py [--start 2013-01-01] [--end 2026-09-30] [--demo]

Stesso impianto della prova storica del giudizio (tools/verdict_backtest.py): a ogni fine trimestre i membri
dell'indice di allora, `compute_metrics` su `MarketData.as_of(data)` (bilanci già depositati, prezzi fino a quel
giorno), rendimenti successivi dal giorno di borsa dopo. Database in SOLA LETTURA; scrive solo in reports/
(metriche_storiche_AAAAMMGG.md e .csv con i valori di ogni data).

Pre-registrazione (scritta PRIMA di vedere qualunque risultato, 2026-11-23; non si cambia dopo):
- metriche messe alla prova: lbo_irr (alto = meglio), distance_to_default (alto = meglio), con i parametri di
  modules/lbo.py DEFAULTS così come sono;
- orizzonte principale 12 mesi; 3 e 6 mesi solo come contorno;
- misura: IC di rango medio con t corretto per i periodi sovrapposti (research.analyze), su tutto il periodo e
  sulle due metà; correzione di Holm per le 2 prove (soglia 5% a una coda);
- confronti, non messi alla prova: rendimento del FCF sull'EV, EV/EBITDA (basso = meglio), Altman Z, volatilità
  (bassa = meglio). Se una metrica nuova ha correlazione di rango > 0,8 con un confronto, non è un segnale nuovo;
- nessuna taratura: se non passa, il report lo dice.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.db_manager import DBManager  # noqa: E402
from modules import lbo as lbo_mod  # noqa: E402
from modules import research  # noqa: E402
from modules import verdict_research as vr  # noqa: E402
from modules.config import PROJECT_ROOT, load_config  # noqa: E402
from modules.metrics import compute_metrics  # noqa: E402
from tools.verdict_backtest import load_data, md_table, num, pct  # noqa: E402

logger = logging.getLogger("metric_research")

TESTED = {"lbo_irr": "Rendimento LBO", "distance_to_default": "Distanza dal default"}
BASELINES = {"fcf_ev_yield": "FCF / EV", "neg_ev_ebitda": "EV/EBITDA (basso)", "altman_z": "Altman Z",
             "neg_volatility": "Volatilità (bassa)"}
COLUMNS = {**TESTED, **BASELINES}
HORIZONS = {3: 63, 6: 126, 12: 252}
PRIMARY = 252
ALPHA = 0.05


def collect(cfg, data, dates, members_fn, companies, progress=None) -> pd.DataFrame:
    frames = []
    sector = companies["sector"] if "sector" in companies else pd.Series(dtype=object)
    for i, day in enumerate(pd.DatetimeIndex(dates), 1):
        t0 = perf_counter()
        members = sorted(members_fn(day))
        view = data.as_of(day)
        m = compute_metrics(view, members, cfg=cfg)
        px = view.close
        ok = [t for t in members if t in px.columns and px[t].iloc[-6:].notna().any()]
        f = m.reindex(ok)
        frames.append(pd.DataFrame({
            "date": day, "ticker": ok, "sector": [sector.get(t) for t in ok],
            "lbo_irr": f["lbo_irr"].to_numpy(), "distance_to_default": f["distance_to_default"].to_numpy(),
            "fcf_ev_yield": f["fcf_ev_yield"].to_numpy(), "neg_ev_ebitda": -f["ev_ebitda"].to_numpy(),
            "altman_z": f["altman_z"].to_numpy(), "neg_volatility": -f["volatility"].to_numpy(),
            "market_cap": f["market_cap"].to_numpy()}))
        if progress:
            progress(i, len(dates), day, perf_counter() - t0)
    return pd.concat(frames, ignore_index=True)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """p-value corretti di Holm (passo dopo passo, monotoni)."""
    items = sorted(((p, k) for k, p in pvals.items() if np.isfinite(p)))
    m, out, running = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def one_sided_p(t: float) -> float:
    return 1 - 0.5 * (1 + math.erf(t / math.sqrt(2))) if np.isfinite(t) else np.nan


def correlations(sig: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for a in TESTED:
        for b in BASELINES:
            vals = []
            for _, g in sig.groupby("date"):
                x = g[[a, b]].dropna()
                if len(x) >= 30:
                    vals.append(x[a].rank().corr(x[b].rank()))
            rows.append({"Metrica": TESTED[a], "Confronto": BASELINES[b],
                         "Correlazione di rango media": num(np.nanmean(vals) if vals else np.nan)})
    return pd.DataFrame(rows)


def run_analysis(sig: pd.DataFrame, close: pd.DataFrame, rcfg: dict) -> pd.DataFrame:
    summary, quant, _ = research.analyze(sig, close, list(HORIZONS.values()), vr.STEP_DAYS,
                                         n_quantiles=5, min_names=int(rcfg.get("min_names", 20)),
                                         columns=COLUMNS)
    if not quant.empty:
        avg = quant.groupby(["signal", "horizon"])["mean_return"].transform("mean")
        top = quant.assign(excess=quant["mean_return"] - avg).query("quantile == 5")
        summary = summary.merge(top[["signal", "horizon", "excess"]].rename(columns={"excess": "top_excess"}),
                                on=["signal", "horizon"], how="left")
    return summary


def report(sig, full, halves, corr, info, args, cfg) -> str:
    lines = [f"# Metriche nuove alla prova del passato ({datetime.now():%d/%m/%Y})", "",
             "Pre-registrazione: vedi l'intestazione di tools/metric_research.py (scritta prima dei risultati).",
             f"Date: {info['dates']} fine trimestre dal {info['start']:%d/%m/%Y} al {info['end']:%d/%m/%Y}; "
             f"membri dell'indice di allora; {info['rows']} osservazioni; {info['seconds'] / 60:.1f} minuti.",
             f"Parametri del test LBO: {lbo_mod.settings(cfg)}", ""]
    prim = full[full["horizon"] == PRIMARY].set_index("signal")
    pv = {s: one_sided_p(prim.loc[s, "ic_t"]) for s in TESTED.values() if s in prim.index}
    adj = holm(pv)
    lines += ["## Risultato principale (12 mesi, tutto il periodo, correzione di Holm)", ""]
    rows = []
    for s in TESTED.values():
        if s not in prim.index:
            rows.append({"Metrica": s, "IC medio": "–", "t": "–", "p (Holm)": "–", "Esito": "dati insufficienti"})
            continue
        r = prim.loc[s]
        passed = np.isfinite(adj.get(s, np.nan)) and adj[s] < ALPHA
        rows.append({"Metrica": s, "IC medio": num(r["ic_mean"], 3), "t": num(r["ic_t"]),
                     "p (Holm)": num(adj.get(s, np.nan), 3),
                     "Quintile migliore vs media": pct(r.get("top_excess"), 1, sign=True),
                     "Esito": "PASSA" if passed else "non provato"})
    lines += [md_table(pd.DataFrame(rows)), ""]

    def table(df: pd.DataFrame) -> str:
        df = df.copy()
        out = pd.DataFrame({
            "Segnale": df["signal"], "Mesi": df["horizon"].map({v: k for k, v in HORIZONS.items()}),
            "Date": df.get("n_dates"), "Titoli": df.get("avg_names", pd.Series(np.nan)).map(lambda x: num(x, 0)),
            "IC medio": df.get("ic_mean").map(lambda x: num(x, 3)), "t": df.get("ic_t").map(num),
            "IC nel settore": df.get("ic_sector_neutral").map(lambda x: num(x, 3)),
            "Q5 - Q1 l'anno": df.get("spread_annual").map(lambda x: pct(x, 1, sign=True)),
            "Q5 vs media": df.get("top_excess", pd.Series(np.nan, index=df.index)).map(
                lambda x: pct(x, 1, sign=True))})
        return md_table(out.sort_values(["Mesi", "Segnale"]))

    lines += ["## Tutto il periodo (metriche nuove e confronti)", "", table(full), ""]
    for name, h in halves.items():
        lines += [f"## {name}", "", table(h), ""]
    lines += ["## Le metriche nuove sono davvero nuove?", "",
              "Correlazione di rango media tra le aziende alla stessa data (sopra 0,8 = quasi la stessa classifica).",
              "", md_table(corr), "",
              "## Come leggere", "",
              "- IC = correlazione di rango tra la metrica e il rendimento successivo (0,02-0,05 è già un segnale "
              "utile se costante); t sopra 2 con la correzione per i periodi sovrapposti.",
              "- Quintile migliore vs media: rendimento medio del 20% di aziende con la metrica migliore meno la "
              "media dei quintili, sull'orizzonte indicato (non annualizzato).",
              "- Cautele: universo = membri dell'S&P 500 di allora con prezzi disponibili (i delistati senza prezzo "
              "mancano: survivorship bias residuo), costi di transazione esclusi, 2013-2026 favorevole alle grandi "
              "aziende di qualità. Un risultato \"non provato\" non dice che la metrica sia inutile per leggere "
              "un'azienda: dice che da sola non ha battuto l'indice in modo misurabile."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--start", default="2013-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--demo", action="store_true", help="database demo (data/demo.db)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config()
    if args.demo:
        cfg["data"]["db_path"] = str(PROJECT_ROOT / "data" / "demo.db")
    db = DBManager(cfg["data"]["db_path"])
    data, universe, companies = load_data(cfg, db)
    dates = vr.quarter_dates(data.calendar, args.start, args.end)
    members_fn = (lambda day: universe.tickers(as_of=day))
    t0 = perf_counter()

    def progress(i, n, day, sec):
        logger.info("%d/%d %s (%.1f s)", i, n, f"{day:%Y-%m-%d}", sec)

    sig = collect(cfg, data, dates, members_fn, companies, progress)
    close = vr.prepare_close(data)
    rcfg = cfg.get("research", {})
    full = run_analysis(sig, close, rcfg)
    mid = dates[len(dates) // 2]
    halves = {f"Prima metà (fino al {dates[len(dates) // 2 - 1]:%d/%m/%Y})": run_analysis(sig[sig["date"] < mid], close, rcfg),
              f"Seconda metà (dal {mid:%d/%m/%Y})": run_analysis(sig[sig["date"] >= mid], close, rcfg)}
    info = {"dates": len(dates), "start": dates[0], "end": dates[-1], "rows": len(sig), "seconds": perf_counter() - t0}
    out_dir = PROJECT_ROOT / "reports"
    stem = f"metriche_storiche_{datetime.now():%Y%m%d}" + ("_demo" if args.demo else "")
    sig.to_csv(out_dir / f"{stem}.csv", index=False)
    (out_dir / f"{stem}.md").write_text(report(sig, full, halves, correlations(sig), info, args, cfg),
                                        encoding="utf-8")
    logger.info("Report: %s", out_dir / f"{stem}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
