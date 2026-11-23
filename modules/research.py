"""
Ricerca dei segnali: ogni punteggio della strategia prevede davvero i rendimenti futuri?

Prima di combinare i segnali (e di sceglierne i pesi) bisogna misurarli uno per uno.
Per ogni data di ricerca (ogni `step_days` giorni di borsa) si fa girare la STESSA strategia
dell'analisi live sull'istantanea point-in-time, si leggono i punteggi di ogni titolo e si
confrontano con il rendimento successivo, misurato dalla chiusura del giorno di borsa dopo il
segnale (come l'esecuzione del backtest) fino a `orizzonte` giorni più avanti.

Misure, per segnale e orizzonte:
- IC (information coefficient): correlazione di rango tra punteggio e rendimento futuro tra i
  titoli di una stessa data, poi media nel tempo. 0,02-0,05 è già interessante per un segnale
  azionario; sopra 0,10 di solito c'è un errore (look-ahead).
- t-stat dell'IC: la media è distinguibile da zero? Con orizzonte più lungo del passo i periodi
  si sovrappongono e non sono indipendenti: il numero di osservazioni si riduce in proporzione.
- % date con IC > 0: quanto spesso il segnale ha avuto ragione.
- IC neutrale per settore: la stessa misura confrontando i titoli solo dentro il proprio
  settore. Se crolla, il segnale stava solo scommettendo su un settore.
- Quantili: rendimento medio dei titoli divisi in `n_quantiles` gruppi per punteggio;
  spread = migliori - peggiori; monotonia = i gruppi sono in ordine (1 = perfetto)?
- Decadimento: l'IC sui diversi orizzonti dice quanto a lungo il segnale resta informativo.

Attenzione ai test multipli: con 6 segnali x 4 orizzonti un t-stat sopra 2 esce per caso
quasi una volta su 24. Per questo il verdetto "solido" richiede |t| >= `t_strong` (3 di default).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# colonna nelle decisioni della strategia -> nome leggibile
SIGNALS = {
    "momentum": "Momentum 12-1",
    "quality": "Quality",
    "volatility_score": "Bassa volatilità",
    "margin_of_safety": "Margine DCF",
    "mf_score": "Multi-factor",
    "confidence": "Confidenza",
}


@dataclass
class ResearchResult:
    signals: pd.DataFrame      # date, ticker, sector, un colonna per segnale
    summary: pd.DataFrame      # una riga per (segnale, orizzonte)
    quantiles: pd.DataFrame    # rendimento medio per quantile, per (segnale, orizzonte)
    ic: pd.DataFrame           # IC per data: colonne MultiIndex (segnale, orizzonte)
    params: dict


def research_dates(calendar: pd.DatetimeIndex, years: float, step: int, end=None, start=None) -> pd.DatetimeIndex:
    end = pd.Timestamp(end) if end is not None else calendar[-1]
    start = pd.Timestamp(start) if start else end - pd.DateOffset(years=years)
    days = calendar[(calendar >= start) & (calendar <= end)]
    return days[::step]


def collect_signals(strategy, data, dates, universe_fn=None) -> pd.DataFrame:
    """Punteggi point-in-time di tutti i titoli a ogni data, dalla stessa pipeline della strategia."""
    rows = []
    for i, day in enumerate(dates, 1):
        universe = universe_fn(day) if universe_fn else None
        res = strategy.run(data.as_of(day), tickers=universe)
        for d in res.decisions:
            rows.append({"date": day, "ticker": d["ticker"], "sector": d.get("sector"),
                         **{c: d.get(c) for c in SIGNALS}})
        if i % 10 == 0:
            logger.info("Ricerca: %d/%d date", i, len(dates))
    df = pd.DataFrame(rows, columns=["date", "ticker", "sector", *SIGNALS])
    for c in SIGNALS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def forward_returns(close: pd.DataFrame, dates, horizon: int, lag: int = 1) -> pd.DataFrame:
    """Rendimento da `lag` giorni di borsa dopo la data a `lag + horizon` giorni dopo.
    Righe = date, colonne = ticker; NaN se il periodo esce dai dati."""
    pos = close.index.get_indexer(pd.DatetimeIndex(dates))
    out = pd.DataFrame(np.nan, index=pd.DatetimeIndex(dates), columns=close.columns)
    values = close.to_numpy(dtype=float)
    for row, p in enumerate(pos):
        if p < 0 or p + lag + horizon >= len(close):
            continue
        start, end = values[p + lag], values[p + lag + horizon]
        with np.errstate(divide="ignore", invalid="ignore"):
            out.iloc[row] = np.where(start > 0, end / start - 1, np.nan)
    return out


def _rank_corr(x: pd.Series, y: pd.Series) -> float:
    return float(x.rank().corr(y.rank()))


def _sector_neutral_corr(x: pd.Series, y: pd.Series, sector: pd.Series) -> float:
    """Correlazione dei ranghi percentuali calcolati dentro ogni settore."""
    groups = sector.fillna("n/d")
    rx = x.groupby(groups).rank(pct=True)
    ry = y.groupby(groups).rank(pct=True)
    big = groups.map(groups.value_counts()) >= 3  # settori con almeno 3 titoli
    return float(rx[big].corr(ry[big])) if big.sum() >= 10 else float("nan")


def analyze(signals: pd.DataFrame, close: pd.DataFrame, horizons: list[int], step: int,
            n_quantiles: int = 5, min_names: int = 20, t_strong: float = 3.0, t_weak: float = 2.0,
            columns: dict[str, str] | None = None):
    """Restituisce (summary, quantiles, ic_per_data). `columns`: colonna -> nome (default: i segnali
    della strategia)."""
    columns = SIGNALS if columns is None else columns
    dates = pd.DatetimeIndex(sorted(signals["date"].unique()))
    summary, quant_rows, ic_cols = [], [], {}
    by_date = {d: g.set_index("ticker") for d, g in signals.groupby("date")}
    for h in horizons:
        fwd = forward_returns(close, dates, h)
        for col, name in columns.items():
            ics, neutral, spreads, qmeans, names = [], [], [], [], []
            ic_series = {}
            for d in dates:
                g = by_date[d]
                y = fwd.loc[d].reindex(g.index)
                ok = g[col].notna() & y.notna()
                if ok.sum() < max(min_names, 2 * n_quantiles):
                    continue
                x, yy, sec = g.loc[ok, col], y[ok], g.loc[ok, "sector"]
                ic = _rank_corr(x, yy)
                if not np.isfinite(ic):  # punteggio costante a questa data
                    continue
                ics.append(ic)
                ic_series[d] = ic
                neutral.append(_sector_neutral_corr(x, yy, sec))
                q = pd.qcut(x.rank(method="first"), n_quantiles, labels=range(1, n_quantiles + 1))
                qm = yy.groupby(q, observed=True).mean()
                qmeans.append(qm)
                spreads.append(qm.iloc[-1] - qm.iloc[0])
                names.append(int(ok.sum()))
            ic_cols[(name, h)] = pd.Series(ic_series, dtype=float)
            n = len(ics)
            if n == 0:
                summary.append({"signal": name, "horizon": h, "n_dates": 0})
                continue
            ic_arr = np.array(ics)
            sd = ic_arr.std(ddof=1) if n > 1 else np.nan
            n_eff = n * min(1.0, step / h)  # periodi sovrapposti: meno osservazioni indipendenti
            if n < 2:
                t = np.nan
            elif sd > 0:
                t = ic_arr.mean() / sd * np.sqrt(n_eff)
            else:  # IC identico a ogni data: evidenza "infinita" (o nulla se è zero)
                t = np.sign(ic_arr.mean()) * np.inf if ic_arr.mean() != 0 else 0.0
            qavg = pd.concat(qmeans, axis=1).mean(axis=1)
            mono = float(pd.Series(qavg.index.astype(int), index=qavg.index).corr(qavg, method="spearman"))
            summary.append({
                "signal": name, "horizon": h, "n_dates": n, "avg_names": float(np.mean(names)),
                "ic_mean": float(ic_arr.mean()), "ic_t": float(t), "ic_hit_rate": float((ic_arr > 0).mean()),
                "ic_sector_neutral": float(np.nanmean(neutral)) if np.isfinite(neutral).any() else np.nan,
                "spread": float(np.mean(spreads)), "spread_annual": float(np.mean(spreads) * 252 / h),
                "monotonicity": mono, "verdict": verdict(t, t_strong, t_weak),
            })
            for qi, r in qavg.items():
                quant_rows.append({"signal": name, "horizon": h, "quantile": int(qi), "mean_return": float(r)})
    return pd.DataFrame(summary), pd.DataFrame(quant_rows), pd.DataFrame(ic_cols)


def verdict(t: float, t_strong: float, t_weak: float) -> str:
    if np.isnan(t):
        return "dati insufficienti"
    if t >= t_strong:
        return "solido"
    if t >= t_weak:
        return "promettente (da confermare)"
    if t <= -t_weak:
        return "funziona al contrario"
    return "nessuna evidenza"


def collect_factors(data, dates, universe_fn=None, sectors: dict | None = None,
                    max_age_days: int = 550) -> pd.DataFrame:
    """Fattori classici point-in-time di tutti i membri dell'indice a ogni data."""
    from modules.factors import compute_factors

    sectors = sectors or {}
    frames = []
    for i, day in enumerate(dates, 1):
        snap = data.as_of(day)
        universe = universe_fn(day) if universe_fn else [c for c in snap.close.columns
                                                          if c not in (data.benchmark, data.risk_free_ticker)]
        f = compute_factors(snap, list(universe), max_age_days)
        frames.append(f.assign(date=day, sector=[sectors.get(t) for t in f.index]).reset_index())
        if i % 20 == 0:
            logger.info("Fattori: %d/%d date", i, len(dates))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def ic_weights(summary: pd.DataFrame, horizon: int, min_t: float = 0.0,
               exclude: tuple[str, ...] = ()) -> dict[str, float]:
    """Pesi della combinazione: proporzionali all'IC medio (solo i fattori con IC positivo e t >= min_t).
    I fattori in `exclude` vengono misurati ma non entrano nella combinazione."""
    from modules.factors import FACTORS

    by_label = {f.label: f.key for f in FACTORS if f.key not in exclude}
    s = summary[(summary["horizon"] == horizon) & (summary["n_dates"] > 0)].set_index("signal")
    s = s[s.index.isin(list(by_label))]
    good = s[(s["ic_mean"] > 0) & (s["ic_t"] >= min_t)]["ic_mean"]
    total = good.sum()
    return {by_label[k]: float(v / total) for k, v in good.items() if k in by_label} if total > 0 else {}


@dataclass
class FactorResearchResult:
    factors: pd.DataFrame          # date, ticker, sector, un colonna per fattore (+ composite)
    summary: pd.DataFrame          # tutti i fattori, tutto il periodo
    summary_first: pd.DataFrame    # prima metà: serve a scegliere i pesi
    composite: pd.DataFrame        # combinazione: prima metà (in-sample) e seconda (out-of-sample)
    weights: dict
    quantiles: pd.DataFrame
    params: dict


class FactorResearch:
    """Misura i fattori classici e costruisce una combinazione con pesi scelti SOLO sulla prima metà
    delle date, poi giudicata sulla seconda metà (walk-forward a un passo)."""

    def __init__(self, cfg: dict, universe_fn=None, sectors: dict | None = None):
        self.r = cfg["research"]
        self.f = cfg.get("factors", {})
        self.universe_fn = universe_fn
        self.sectors = sectors or {}

    def run(self, data, end=None) -> FactorResearchResult:
        from modules.factors import FACTOR_LABELS, composite_score

        step = int(self.r["step_days"])
        horizons = [int(h) for h in self.r["horizons"]]
        h_main = int(self.f.get("weight_horizon", 21))
        dates = research_dates(data.calendar, self.r["years"], step, end, self.r.get("start"))
        if len(dates) < 6:
            raise ValueError("Periodo di ricerca troppo corto")
        logger.info("Ricerca fattori: %d date dal %s al %s", len(dates), dates[0].date(), dates[-1].date())
        fac = collect_factors(data, dates, self.universe_fn, self.sectors, int(self.f.get("max_statement_age_days", 550)))
        special = {data.benchmark, data.risk_free_ticker}
        close = data.close[[c for c in data.close.columns if c not in special]].reindex(data.calendar).ffill(limit=5)
        kw = dict(n_quantiles=int(self.r["n_quantiles"]), min_names=int(self.r["min_names"]),
                  t_strong=self.r["t_strong"], t_weak=self.r["t_weak"])

        summary, quantiles, _ = analyze(fac, close, horizons, step, columns=FACTOR_LABELS, **kw)
        mid = dates[len(dates) // 2]
        first, second = fac[fac["date"] < mid], fac[fac["date"] >= mid]
        # i rendimenti futuri della prima metà non devono "sconfinare" nella seconda
        cut = data.calendar[data.calendar < mid]
        summary_first, _, _ = analyze(first, close.loc[:cut[-1]] if len(cut) else close, [h_main], step,
                                      columns=FACTOR_LABELS, **kw)
        weights = ic_weights(summary_first, h_main, float(self.f.get("min_t", 0.0)),
                             tuple(self.f.get("exclude", ())))
        fac["composite"] = np.nan
        for day, idx in fac.groupby("date").groups.items():
            fac.loc[idx, "composite"] = composite_score(fac.loc[idx].set_index("ticker")[list(FACTOR_LABELS)],
                                                        weights).to_numpy()
        comp_rows = []
        for name, part in (("Prima metà (pesi stimati qui)", fac[fac["date"] < mid]),
                           ("Seconda metà (fuori campione)", fac[fac["date"] >= mid])):
            sm, _, _ = analyze(part, close, horizons, step, columns={"composite": "Combinazione"}, **kw)
            comp_rows.append(sm.assign(period=name))
        params = {"start": dates[0].date(), "end": dates[-1].date(), "n_dates": len(dates), "step_days": step,
                  "horizons": horizons, "split": mid.date(), "weight_horizon": h_main,
                  "n_quantiles": kw["n_quantiles"]}
        return FactorResearchResult(fac, summary, summary_first, pd.concat(comp_rows, ignore_index=True),
                                    weights, quantiles, params)


class SignalResearch:
    def __init__(self, cfg: dict, strategy, universe_fn=None):
        self.r = cfg["research"]
        self.strategy = strategy
        self.universe_fn = universe_fn

    def run(self, data, end=None) -> ResearchResult:
        step = int(self.r["step_days"])
        horizons = [int(h) for h in self.r["horizons"]]
        dates = research_dates(data.calendar, self.r["years"], step, end, self.r.get("start"))
        if len(dates) < 3:
            raise ValueError("Periodo di ricerca troppo corto")
        logger.info("Ricerca segnali: %d date dal %s al %s, orizzonti %s giorni",
                    len(dates), dates[0].date(), dates[-1].date(), horizons)
        signals = collect_signals(self.strategy, data, dates, self.universe_fn)
        special = {data.benchmark, data.risk_free_ticker}
        close = data.close[[c for c in data.close.columns if c not in special]] \
            .reindex(data.calendar).ffill(limit=5)
        summary, quantiles, ic = analyze(signals, close, horizons, step, int(self.r["n_quantiles"]),
                                         int(self.r["min_names"]), self.r["t_strong"], self.r["t_weak"])
        params = {"start": dates[0].date(), "end": dates[-1].date(), "n_dates": len(dates), "step_days": step,
                  "horizons": horizons, "n_quantiles": int(self.r["n_quantiles"]),
                  "point_in_time_universe": self.universe_fn is not None}
        return ResearchResult(signals, summary, quantiles, ic, params)
