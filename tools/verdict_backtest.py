"""
Il giudizio avrebbe funzionato in passato? (vedi modules/verdict_research.py)

    .venv/bin/python tools/verdict_backtest.py [--start 2013-01-01] [--end 2026-09-30] [--no-sensitivity] [--demo]

Legge il database in SOLA LETTURA (copia Parquet della webapp se valida, altrimenti SQLite; non salva nulla) e scrive
soltanto in reports/: verdetto_storico_AAAAMMGG.md (report), .csv (ogni giudizio con i rendimenti successivi) e
verdetto_storico_AAAAMMGG_categorie.csv (tabelle per categoria). Una sola configurazione del giudizio: le soglie di
modules/verdict.py così come sono. Nessuna taratura.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.db_manager import DBManager  # noqa: E402
from modules import verdict as vd  # noqa: E402
from modules import verdict_research as vr  # noqa: E402
from modules.config import PROJECT_ROOT, load_config  # noqa: E402
from modules.market_data import MarketData  # noqa: E402
from modules.universe import UniverseManager  # noqa: E402

logger = logging.getLogger("verdict_backtest")


# ------------------------------------------------------------------------------------------------ formati
def pct(x, digits=1, sign=False) -> str:
    if x is None or not np.isfinite(x):
        return "–"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def num(x, digits=2) -> str:
    return "–" if x is None or not np.isfinite(x) else f"{x:.{digits}f}"


def md_table(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    return "\n".join([head, sep] + ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)])


def label(c: str) -> str:
    return vr.SHORT_LABELS.get(c, c)


# ------------------------------------------------------------------------------------------------ dati
def load_data(cfg: dict, db: DBManager) -> tuple[MarketData, UniverseManager, pd.DataFrame]:
    """Stessi dati della webapp, senza scrivere nulla (niente copia Parquet salvata se manca)."""
    u = UniverseManager(db, cfg)
    since = pd.Timestamp.now().normalize() - pd.DateOffset(years=int(cfg["data"]["history_years"]))
    tickers = u.data_tickers(since)
    key = "|".join([str(db.latest_run_id()), db.data_version(), db.user_data_version(),
                    hashlib.md5(",".join(tickers).encode()).hexdigest()])
    data = MarketData.load_snapshot(Path(db.db_path).parent / "cache" / Path(db.db_path).stem, key)
    if data is None:
        logger.info("Copia Parquet non valida: lettura dal database (qualche secondo in più)")
        data = MarketData.from_db(db, cfg, tickers)
    comp = db.load_companies(only_index=False).set_index("ticker")
    return data, u, comp


# ------------------------------------------------------------------------------------------------ report
def pivot_excess(cats: pd.DataFrame, value: str = "excess_ew", fmt=pct) -> pd.DataFrame:
    if cats.empty:
        return pd.DataFrame()
    p = cats.pivot(index="category", columns="months", values=value)
    p = p.reindex([c for c in vr.CATEGORY_ORDER if c in p.index])
    out = pd.DataFrame({"Giudizio": [label(c) for c in p.index]})
    for m in p.columns:
        out[f"{m} mesi"] = [fmt(x, sign=True) if fmt is pct else fmt(x) for x in p[m]]
    return out


def detail_table(cats: pd.DataFrame, months: int) -> pd.DataFrame:
    c = cats[cats["months"] == months]
    return pd.DataFrame({
        "Giudizio": [label(x) for x in c["category"]], "Osservazioni": c["obs"].astype(int),
        "Date": c["dates"].astype(int), "Rend. medio": [pct(x) for x in c["mean"]],
        "Mediano": [pct(x) for x in c["median"]], "vs pesi uguali": [pct(x, sign=True) for x in c["excess_ew"]],
        "t": [num(x, 1) for x in c["t_excess_ew"]], "vs SPY": [pct(x, sign=True) for x in c["excess_spy"]],
        "% batte pesi uguali": [pct(x, 0) for x in c["beat_ew"]], "% batte SPY": [pct(x, 0) for x in c["beat_spy"]],
        "Dispersione": [pct(x, 0) for x in c["dispersion"]],
        "% trimestri sopra l'indice": [pct(x, 0) for x in c["dates_beating_ew"]],
    })


def ic_table(ic: pd.DataFrame) -> pd.DataFrame:
    if ic.empty:
        return pd.DataFrame()
    rows = []
    for name in vr.PILLARS.values():
        r = {"Pilastro": name}
        for m in sorted(ic["months"].dropna().unique()):
            s = ic[(ic["signal"] == name) & (ic["months"] == m)]
            r[f"{int(m)} mesi IC (t)"] = "–" if s.empty or not np.isfinite(s["ic_mean"].iloc[0]) else \
                f"{s['ic_mean'].iloc[0]:+.3f} ({s['ic_t'].iloc[0]:+.1f})"
        rows.append(r)
    return pd.DataFrame(rows)


def ic_extra_table(ic: pd.DataFrame, months: int) -> pd.DataFrame:
    s = ic[ic["months"] == months]
    return pd.DataFrame({
        "Pilastro": s["signal"], "IC medio": [f"{x:+.3f}" for x in s["ic_mean"]], "t": [num(x, 1) for x in s["ic_t"]],
        "% date con IC > 0": [pct(x, 0) for x in s["ic_hit_rate"]],
        "IC dentro il settore": [f"{x:+.3f}" if np.isfinite(x) else "–" for x in s["ic_sector_neutral"]],
        "Migliori 20% - peggiori 20%": [pct(x, sign=True) for x in s["spread"]],
        "Ordine dei quintili (1 = perfetto)": [num(x, 2) for x in s["monotonicity"]],
        "Titoli per data": [f"{x:.0f}" for x in s["avg_names"]],
    })


def portfolio_table(res: vr.StudyResult) -> pd.DataFrame:
    rows = []
    for name, p in res.portfolios.items():
        st, sn = p.stats, p.stats_net
        rows.append({"Portafoglio": name, "CAGR": pct(st.get("cagr")), "CAGR dopo i costi": pct(sn.get("cagr")),
                     "Volatilità": pct(st.get("annual_volatility")), "Perdita massima": pct(st.get("max_drawdown")),
                     "Sharpe (senza tasso)": num(st.get("sharpe_ratio")),
                     "Rotazione annua": f"{p.turnover:.0%}", "Titoli medi": f"{p.holdings.mean():.0f}",
                     "Date in contanti": str(p.empty_dates)})
    cand = res.portfolios[vr.SHORT_LABELS[vd.CANDIDATE]].equity
    if not cand.empty:
        spy = res.portfolio_halves[(res.portfolio_halves["portfolio"] == "SPY") &
                                   (res.portfolio_halves["half"] == "Tutto")]
        if not spy.empty:
            s = spy.iloc[0]
            rows.append({"Portafoglio": "SPY (comprato e tenuto)", "CAGR": pct(s["cagr"]), "CAGR dopo i costi": "–",
                         "Volatilità": pct(s["annual_volatility"]), "Perdita massima": pct(s["max_drawdown"]),
                         "Sharpe (senza tasso)": num(s["sharpe_ratio"]), "Rotazione annua": "0%",
                         "Titoli medi": "1", "Date in contanti": "0"})
    return pd.DataFrame(rows)


def halves_portfolio_table(res: vr.StudyResult) -> pd.DataFrame:
    ph = res.portfolio_halves
    keep = [vr.SHORT_LABELS[vd.CANDIDATE], vr.SHORT_LABELS[vd.WAIT], vr.SHORT_LABELS[vd.AVOID],
            "Indice a pesi uguali (tutti i membri con prezzo)", "SPY"]
    rows = []
    for name in keep:
        r = {"Portafoglio": name}
        for half in ("Prima metà", "Seconda metà"):
            s = ph[(ph["portfolio"] == name) & (ph["half"] == half)]
            r[f"{half}: CAGR"] = pct(s["cagr"].iloc[0]) if not s.empty else "–"
            r[f"{half}: perdita max"] = pct(s["max_drawdown"].iloc[0]) if not s.empty else "–"
        rows.append(r)
    return pd.DataFrame(rows)


def group_table(df: pd.DataFrame, min_obs: int = 30) -> pd.DataFrame:
    if df.empty:
        return df
    d = df[df["obs"] >= min_obs].copy()
    parts = d["group"].str.split(" | ", n=1, expand=True, regex=False)
    d["Gruppo"], d["Giudizio"] = parts[0], parts[1].map(label)
    d["_o"] = parts[1].map({c: i for i, c in enumerate(vr.CATEGORY_ORDER)})
    d = d.sort_values(["Gruppo", "_o"])
    return pd.DataFrame({"Gruppo": d["Gruppo"], "Giudizio": d["Giudizio"], "Osservazioni": d["obs"].astype(int),
                         "Rend. medio 12 mesi": [pct(x) for x in d["mean"]],
                         "vs pesi uguali": [pct(x, sign=True) for x in d["excess_ew"]],
                         "t": [num(x, 1) for x in d["t_excess_ew"]],
                         "% batte pesi uguali": [pct(x, 0) for x in d["beat_ew"]]})


def answer_lines(res: vr.StudyResult) -> list[str]:
    """Frasi automatiche sui numeri principali (nessun giudizio di merito aggiunto a mano)."""
    c = res.categories
    out = []

    def get(cat, m, col):
        s = c[(c["category"] == cat) & (c["months"] == m)]
        return float(s[col].iloc[0]) if not s.empty else np.nan

    for m in (12, 36):
        ex, t = get(vd.CANDIDATE, m, "excess_ew"), get(vd.CANDIDATE, m, "t_excess_ew")
        ex_avoid = get(vd.AVOID, m, "excess_ew")
        ex_spy = get(vd.CANDIDATE, m, "excess_spy")
        if np.isfinite(ex):
            out.append(f"A {m} mesi le Candidate hanno reso in media {pct(ex, sign=True)} rispetto all'indice a pesi "
                       f"uguali (t = {num(t, 1)}) e {pct(ex_spy, sign=True)} rispetto a SPY; le \"Da evitare\" "
                       f"{pct(ex_avoid, sign=True)} rispetto all'indice a pesi uguali.")
    p = res.portfolios[vr.SHORT_LABELS[vd.CANDIDATE]]
    ew = res.portfolios["Indice a pesi uguali (tutti i membri con prezzo)"]
    spy = res.portfolio_halves[(res.portfolio_halves["portfolio"] == "SPY") & (res.portfolio_halves["half"] == "Tutto")]
    if p.stats:
        out.append(f"Portafoglio delle Candidate (pesi uguali, ribilanciato ogni trimestre): CAGR {pct(p.stats['cagr'])} "
                   f"({pct(p.stats_net.get('cagr'))} dopo i costi), contro {pct(ew.stats.get('cagr'))} dell'indice a "
                   f"pesi uguali e {pct(spy['cagr'].iloc[0]) if not spy.empty else '–'} di SPY.")
    return out


def write_report(res: vr.StudyResult, sens: vr.StudyResult | None, agreement: pd.DataFrame | None, path: Path,
                 timings: dict, cfg: dict) -> None:
    P = res.params
    L = []
    w = L.append
    w(f"# Il giudizio avrebbe funzionato? Verifica storica ({datetime.now():%Y-%m-%d})\n")
    w("> Una sola configurazione: le soglie di `modules/verdict.py` così come sono (fissate a priori, nessuna taratura "
      "sui risultati qui sotto). Non è una raccomandazione né una previsione.\n")
    w("## In breve\n")
    for line in answer_lines(res):
        w(f"- {line}")
    w("")
    w("## Come è stato fatto\n")
    w(f"- {P['dates']} date, l'ultimo giorno di borsa di ogni trimestre dal {P['start']} al {P['end']} (dati fino al "
      f"{P['data_end']}). A ogni data: membri dell'S&P 500 di quel giorno (composizione storica), dati com'erano allora "
      "(`MarketData.as_of`: prezzi fino a quel giorno, bilanci già depositati), metriche, confronti di settore "
      "sull'indice di allora, costo del capitale e DCF inverso ricalcolati con beta, tasso a 10 anni e premio implicito "
      "di quel giorno. Il giudizio è quello dell'app (`verdict.assess_many`), sulla vista `PointInTimeView`.")
    w("- Rendimento successivo: dalla chiusura del giorno di borsa dopo il giudizio a 3, 6, 12, 24, 36 mesi (63, 126, "
      "252, 504, 756 giorni di borsa). Titolo che smette di quotare: conta il suo ultimo prezzo (venduto e tenuto in "
      "contanti). Indice a pesi uguali = media dei membri con prezzo alla stessa data.")
    w("- \"vs pesi uguali\" = media nel tempo (ogni trimestre pesa uguale) di [rendimento medio del gruppo - rendimento "
      "medio dell'indice a pesi uguali], nel periodo (non annualizzato).")
    w("- **IC** = correlazione di rango tra un punteggio e il rendimento successivo, tra i titoli della stessa data "
      "(0 = nessun legame; 0,02-0,05 = utile per un segnale azionario; sopra 0,10 di solito c'è un errore). "
      "**t** = quanto la media è lontana da zero rispetto al rumore: sopra 2 difficilmente è caso, sopra 3 è solido; "
      "le osservazioni sono ridotte quando i periodi si sovrappongono (a 12 mesi con date trimestrali: un quarto).")
    w("- Escluso perché non ricostruibile nel passato: il tuo fair value, il controllo di qualità dei dati (CSV di oggi), "
      "la checklist e il diario delle valutazioni. Insider e 13F sono point-in-time ma esistono solo da poco: il "
      "pilastro \"Chi è dentro\" non cambia il giudizio e qui non è analizzato. Settori e sotto-settori sono quelli di "
      "oggi; gli ex membri dell'indice non hanno settore nel database. Spread dei rating sintetici: tabella di oggi.")
    w("")
    w("## 1. Rendimento in eccesso rispetto all'indice a pesi uguali, per giudizio e orizzonte\n")
    w(md_table(pivot_excess(res.categories)))
    w("\nStesso confronto con SPY:\n")
    w(md_table(pivot_excess(res.categories, "excess_spy")))
    w("\nt-stat dell'eccesso sui pesi uguali (corretto per i periodi sovrapposti):\n")
    w(md_table(pivot_excess(res.categories, "t_excess_ew", fmt=num)))
    for m in (12, 36):
        w(f"\n### Dettaglio a {m} mesi\n")
        w(md_table(detail_table(res.categories, m)))
    w("\n## 2. Le due metà del periodo\n")
    w(f"Divisione alla data {P['split']}: i giudizi prima e dopo. I rendimenti a lungo orizzonte della prima metà "
      "proseguono nella seconda (nessun parametro è stimato, quindi non c'è nulla da proteggere).\n")
    for half in ("Prima metà", "Seconda metà"):
        w(f"**{half}** - eccesso sui pesi uguali:\n")
        w(md_table(pivot_excess(res.categories_halves[res.categories_halves["half"] == half])))
        w("")
    w("## 3. Solo le aziende con settore noto (alla pari sulle prove)\n")
    w("Gli ex membri non hanno settore nel database: niente confronti di settore, quindi meno prove e meno Candidate. "
      "Questo arricchisce le Candidate di aziende ancora nell'indice oggi (sopravvissute: un'informazione dal futuro). "
      "Qui il confronto è solo tra aziende con settore noto, quasi tutte membri di oggi: tutti i gruppi hanno lo stesso "
      "vantaggio, il confronto TRA gruppi è più equo, ma i rendimenti assoluti sono gonfiati.\n")
    w(md_table(pivot_excess(res.categories_sector_known)))
    v = res.verdicts[res.verdicts["has_price"]]
    share = v.groupby("category")["known_sector"].apply(lambda s: 1 - s.mean())
    w("\nQuota di ex membri (settore ignoto) in ogni giudizio: " +
      ", ".join(f"{label(c)} {pct(share.get(c, np.nan), 0)}" for c in vr.CATEGORY_ORDER if c in share.index) +
      f" (tutti: {pct(1 - v['known_sector'].mean(), 0)}).\n")
    w("## 4. Quale pilastro conta? IC dei punteggi\n")
    w(md_table(ic_table(res.ic)))
    w("\nDettaglio a 12 mesi:\n")
    w(md_table(ic_extra_table(res.ic, 12)))
    for half in ("Prima metà", "Seconda metà"):
        w(f"\n**{half}**:\n")
        w(md_table(ic_table(res.ic_halves[res.ic_halves["half"] == half])))
    w("\n## 5. Portafogli: tutte le aziende di un giudizio a pesi uguali, ribilanciate ogni trimestre\n")
    w(f"Comprate alla chiusura del giorno dopo il giudizio, tenute fino al trimestre dopo. Costi: "
      f"{P['cost_per_side']:.2%} per lato (commissione + slippage del backtest). Perdita massima su dati giornalieri.\n")
    w(md_table(portfolio_table(res)))
    w("\n**Le due metà:**\n")
    w(md_table(halves_portfolio_table(res)))
    w("\n## 6. Per tipo di azienda e per settore (12 mesi, gruppi con almeno 30 osservazioni)\n")
    w(md_table(group_table(res.by_profile)))
    w("\n")
    w(md_table(group_table(res.by_sector)))
    w("\n## 7. Sopravvivenza: quanti membri dell'indice hanno un prezzo\n")
    s = res.survivorship
    if not s.empty:
        y = s.assign(anno=s["date"].dt.year).groupby("anno").agg(membri=("members", "mean"),
                                                                 con_prezzo=("with_price", "mean"))
        y["copertura"] = [pct(a / b, 0) for a, b in zip(y["con_prezzo"], y["membri"])]
        y = y.reset_index()
        y["membri"] = y["membri"].round(0).astype(int)
        y["con_prezzo"] = y["con_prezzo"].round(0).astype(int)
        w(md_table(y))
        w("\nI membri senza prezzo (ex membri che Yahoo non ha più: falliti, acquisiti, fusi) non entrano mai nelle "
          "misure: in genere sono andati peggio della media, quindi tutti i rendimenti qui sono un po' gonfiati.")
    if sens is not None:
        w("\n## 8. Sensibilità al premio per il rischio\n")
        erp = res.survivorship["erp"] if "erp" in res.survivorship else pd.Series(dtype=float)
        w(f"Base: premio implicito calcolato a ogni data (da {pct(erp.min())} a {pct(erp.max())}, mediana "
          f"{pct(erp.median())}). Alternativa: premio fisso {pct(cfg['valuation']['equity_risk_premium'])} "
          "(config.yaml). Cambia il costo del capitale, quindi le prove \"FCF vs costo del capitale\", \"crescita "
          "implicita\" e \"P/TBV giustificato\".\n")
        if agreement is not None and not agreement.empty:
            same = np.trace(agreement.to_numpy()) / agreement.to_numpy().sum()
            w(f"Stesso giudizio nel {pct(same, 0)} dei casi. Righe = premio implicito, colonne = premio fisso:\n")
            a = agreement.copy()
            a.index = [label(c) for c in a.index]
            a.columns = [label(c) for c in a.columns]
            w(md_table(a.reset_index().rename(columns={"index": "Implicito \\ Fisso"})))
        w("\nCon il premio fisso, eccesso sui pesi uguali:\n")
        w(md_table(pivot_excess(sens.categories)))
        w("\n")
        w(md_table(portfolio_table(sens)))
    w("\n## Onestà statistica: cosa questi numeri NON dicono\n")
    w("- **Soglie fissate a priori ma non alla cieca.** Le soglie del giudizio vengono da principi classici, ma sono "
      "state scritte dopo aver visto molti backtest sugli stessi dati 2012-2026 (catalogo di strategie, grandi "
      "investitori: \"vince la qualità\"). Il giudizio premia la qualità: un buon risultato qui era in parte atteso.")
    w("- **Una sola configurazione provata** (più la sensibilità sul premio per il rischio, dichiarata). Nessuna soglia "
      "è stata cambiata dopo aver visto i risultati.")
    w("- **Survivorship bias residuo**: copertura dei prezzi dal ~73% (2013) a oltre il 90%; i delistati senza prezzo "
      "mancano. In più gli ex membri non hanno settore: meno prove, meno Candidate tra loro (vedi sezione 3).")
    w("- **Periodo favorevole alla qualità**: 2013-2026 è stato un periodo eccezionale per le grandi aziende di "
      "qualità e per l'S&P 500 pesato per capitalizzazione (SPY ha battuto i pesi uguali). Un decennio diverso "
      "potrebbe dare l'opposto.")
    w("- **Osservazioni non indipendenti**: le stesse aziende restano Candidate per molti trimestri e i periodi a 12-36 "
      "mesi si sovrappongono; il t corregge in modo approssimato. Con 6 giudizi x 5 orizzonti x 5 pilastri, qualche "
      "t sopra 2 esce per caso.")
    w("- **Classificazione di oggi**: settori GICS di oggi applicati al passato (lieve, riguarda i confronti di settore).")
    w(f"\n## Tempi\n\nGiudizi: {timings.get('collect', 0):.0f} s ({timings.get('per_date', 0):.1f} s per data); "
      f"sensibilità: {timings.get('collect_sens', 0):.0f} s; analisi e report: {timings.get('analyze', 0):.0f} s. "
      f"Avvisi del giudizio (costo del capitale/DCF non calcolati): {res.info.get('warnings', 0)}.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------------ main
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--start", default="2013-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--no-sensitivity", action="store_true")
    p.add_argument("--demo", action="store_true", help="database demo (data/demo.db)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    if args.demo:
        cfg["data"]["db_path"] = str(PROJECT_ROOT / "data" / "demo.db")
    db = DBManager(cfg["data"]["db_path"])
    t0 = perf_counter()
    data, universe, companies = load_data(cfg, db)
    logger.info("Dati caricati in %.0f s", perf_counter() - t0)
    dates = vr.quarter_dates(data.calendar, args.start, args.end)
    logger.info("%d date dal %s al %s", len(dates), dates[0].date(), dates[-1].date())
    members_fn = (lambda day: universe.tickers(as_of=day))

    def progress(i, n, day, s):
        if i % 5 == 0 or i == n:
            logger.info("Giudizi: %d/%d (%s, %.1f s)", i, n, day.date(), s)

    timings = {}
    t = perf_counter()
    verdicts, info = vr.collect_verdicts(cfg, data, dates, members_fn, companies, "implied", progress)
    timings["collect"] = perf_counter() - t
    timings["per_date"] = timings["collect"] / len(dates)
    sens_v = None
    if not args.no_sensitivity:
        t = perf_counter()
        sens_v, sens_info = vr.collect_verdicts(cfg, data, dates, members_fn, companies, "fixed", progress)
        timings["collect_sens"] = perf_counter() - t
    t = perf_counter()
    cost = float(cfg["backtest"].get("commission", 0.001)) + float(cfg["backtest"].get("slippage", 0.0005))
    res = vr.analyze_study(verdicts, data, info, cost_per_side=cost)
    sens = vr.analyze_study(sens_v, data, sens_info, cost_per_side=cost) if sens_v is not None else None
    agreement = vr.category_agreement(verdicts, sens_v) if sens_v is not None else None
    timings["analyze"] = perf_counter() - t

    out = PROJECT_ROOT / "reports"
    out.mkdir(exist_ok=True)
    stem = f"verdetto_storico_{datetime.now():%Y%m%d}" + ("_demo" if args.demo else "")
    cols = ["date", "ticker", "name", "sector", "profile", "category", "verdict", *vr.PILLARS, vd.INSIDERS,
            "coverage", "alarms", "checks", "has_price", "known_sector", "market_cap"]
    cols += [c for c in res.verdicts.columns if c[:2] in ("r_",) or c[:3] in ("ew_",) or c[:4] in ("spy_",)]
    res.verdicts[[c for c in cols if c in res.verdicts.columns]].to_csv(out / f"{stem}.csv", index=False)
    tables = [res.categories.assign(table="tutto"), res.categories_halves.rename(columns={"half": "table"}),
              res.categories_sector_known.assign(table="settore noto")]
    if sens is not None:
        tables.append(sens.categories.assign(table="premio fisso"))
    pd.concat(tables, ignore_index=True).to_csv(out / f"{stem}_categorie.csv", index=False)
    write_report(res, sens, agreement, out / f"{stem}.md", timings, cfg)
    logger.info("Report: %s (totale %.0f s)", out / f"{stem}.md", perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
