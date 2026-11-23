"""
Il giudizio avrebbe funzionato in passato? Verifica storica point-in-time di `modules/verdict.py`.

A ogni data di una griglia (fine di ogni trimestre, di default dal 2013) si giudicano TUTTI i membri dell'S&P 500
di quella data con la STESSA funzione dell'app (`verdict.assess_many`), ma su una vista dei dati ferma a quel giorno
(`PointInTimeView`): prezzi fino a quel giorno, bilanci già depositati (`MarketData.as_of`), membri dell'indice di
allora (tabella `index_membership`), metriche e confronti di settore sull'universo di allora, costo del capitale
ricalcolato con i dati di allora (beta, tasso a 10 anni, premio implicito dell'S&P 500). Poi si guarda cosa è
successo dopo.

Cosa NON si può ricostruire nel passato e quindi resta fuori (dichiarato nel report):
- il tuo fair value (non esisteva): la prova "Margine rispetto al tuo fair value" è sempre mancante;
- il controllo di qualità dei dati (CSV di oggi, non point-in-time): prova "Avvisi sulla qualità dei dati" mancante;
- la checklist personale (solo UI) e le valutazioni salvate nel diario;
- insider e 13F: point-in-time per data di deposito, ma esistono solo da poco (insider: archivi dal 2023, 13F dal
  2014); il pilastro "Chi è dentro" non cambia il giudizio e non viene analizzato;
- settori e sotto-settori: quelli di OGGI (GICS cambiati nel 2016 e nel 2018); gli ex membri dell'indice non hanno
  settore nel database, quindi niente confronti con il settore e sempre profilo "generale" (o REIT se c'è l'FFO);
- la tabella dei rating sintetici (spread) è quella di oggi.

Il premio per il rischio azionario: di base quello implicito calcolato a ogni data dai dividendi e riacquisti
dell'indice di allora (`erp_mode="implied"`); per la sensibilità un valore fisso dichiarato (`erp_mode="fixed"`,
`valuation.equity_risk_premium` di config.yaml, 5%).

Rendimenti successivi: dalla chiusura del giorno di borsa DOPO il giudizio (come il backtest) a `h` giorni di borsa
più avanti. Un titolo che smette di quotare (delistato, acquisito) conta con il suo ultimo prezzo, come se fosse
stato venduto quel giorno e tenuto in contanti: meglio che scartarlo (scartarlo toglierebbe proprio i casi estremi).
I titoli senza nessun prezzo alla data del giudizio (ex membri che Yahoo non ha più) non possono entrare: è il
survivorship bias residuo, contato a ogni data.

Misure (in parole semplici):
- rendimento medio e mediano per categoria di giudizio e orizzonte; eccesso rispetto all'indice a pesi uguali (media
  dei membri con prezzo alla stessa data) e a SPY; quota dei titoli che battono l'indice a pesi uguali;
- IC (correlazione di rango tra un punteggio e il rendimento successivo, tra i titoli della stessa data: 0 = nessun
  legame, 0,05 = legame utile per un segnale azionario); t-stat = quanto la media è lontana da zero rispetto al rumore
  (sopra 2 difficilmente è caso, sopra 3 è solido), con il numero di osservazioni ridotto quando i periodi si
  sovrappongono (`research.analyze`);
- un portafoglio "tutte le Candidate a pesi uguali, ribilanciato a ogni data" contro l'indice a pesi uguali e SPY.

UNA sola configurazione: le soglie di `verdict.DEFAULTS` così come sono. Questo modulo non tara nulla.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from modules import verdict as vd
from modules.backtester import performance_metrics
from modules.dashboard_data import DashboardData
from modules.metrics import compute_metrics
from modules.research import analyze, forward_returns

logger = logging.getLogger(__name__)

HORIZONS_MONTHS = {3: 63, 6: 126, 12: 252, 24: 504, 36: 756}  # mesi -> giorni di borsa
STEP_DAYS = 63              # una data al trimestre ~ 63 giorni di borsa (per la correzione dei periodi sovrapposti)
PILLARS = {vd.QUALITY: "Qualità", vd.SOLIDITY: "Solidità", vd.PRICE: "Prezzo", vd.FLAGS: "Allarmi (alto = pochi)",
           "overall": "Punteggio complessivo"}
CATEGORY_ORDER = [vd.CANDIDATE, vd.WAIT, vd.NEUTRAL, vd.VALUE_TRAP, vd.AVOID, vd.NO_DATA]
SHORT_LABELS = {vd.CANDIDATE: "Candidata", vd.WAIT: "Aspettare (buona, cara)", vd.NEUTRAL: "Nessun segnale netto",
                vd.VALUE_TRAP: "Prezzo basso, qualità dubbia", vd.AVOID: "Da evitare", vd.NO_DATA: "Dati insufficienti"}


# ------------------------------------------------------------------------------------------------ date
def quarter_dates(calendar: pd.DatetimeIndex, start="2013-01-01", end=None) -> pd.DatetimeIndex:
    """Ultimo giorno di borsa di ogni trimestre tra `start` e `end` (default: ultimo giorno dei dati)."""
    calendar = pd.DatetimeIndex(calendar).sort_values()
    end = pd.Timestamp(end) if end is not None else calendar[-1]
    ends = pd.date_range(pd.Timestamp(start), end, freq="QE")
    pos = calendar.searchsorted(ends, side="right") - 1
    days = [calendar[p] for p in pos if p >= 0 and calendar[p] >= pd.Timestamp(start)]
    return pd.DatetimeIndex(sorted(set(days)))


# ------------------------------------------------------------------------------------------------ vista storica
class _NoUserData:
    """Al posto del database nella vista storica: nessun dato dell'utente esisteva allora."""

    @staticmethod
    def load_notes() -> pd.DataFrame:
        return pd.DataFrame(columns=["ticker", "fair_value"])

    @staticmethod
    def load_valuations(ticker=None) -> pd.DataFrame:
        return pd.DataFrame()


class PointInTimeView(DashboardData):
    """I dati dell'app come erano a `date`: stesse funzioni di DashboardData (costo del capitale, DCF a leve, metriche),
    ma su `MarketData.as_of(date)` e sui membri dell'indice di quel giorno. Non legge né scrive il database."""

    def __init__(self, cfg: dict, data, date, members: list[str], companies: pd.DataFrame,
                 erp_mode: str = "implied"):  # noqa: D107 - niente super().__init__: non si apre il database
        self.cfg = cfg
        self.db = _NoUserData()
        self.reports_dir = Path(".")
        self.companies = companies
        self.index_tickers = sorted(members)
        self.tickers = list(self.index_tickers)
        self.custom_companies = pd.DataFrame(columns=["ticker", "currency"])
        self.foreign = pd.DataFrame(columns=["ticker", "currency", "index_name"])
        self.data = data.as_of(date)
        self.run_id = None
        self._metrics = None
        self._industry = None
        self._erp = None
        self.erp_mode = erp_mode
        self._sectors = {t: s for t, s in companies["sector"].items() if isinstance(s, str)} \
            if "sector" in companies else {}

    def sectors(self) -> dict[str, str]:
        return self._sectors

    def metrics(self) -> pd.DataFrame:  # senza il voto della checklist: solo oggi
        if self._metrics is None:
            self._metrics = compute_metrics(self.data, self.tickers,
                                            insider_days=int(self.cfg.get("insiders", {}).get("window_days", 90)))
        return self._metrics

    def quality_issues(self) -> pd.DataFrame:  # il controllo di qualità è di oggi: non disponibile nel passato
        return pd.DataFrame()

    def implied_erp(self) -> dict:
        if self.erp_mode == "implied":
            return super().implied_erp()
        if self._erp is None:
            v = self.cfg["valuation"]
            self._erp = {"erp": float(v["equity_risk_premium"]), "computed": False, "cash_yield": np.nan,
                         "coverage": np.nan, "risk_free": self.data.risk_free(v["risk_free_fallback"]),
                         "growth": float(v.get("erp_growth", 0.05)), "fallback": v["equity_risk_premium"]}
        return self._erp


class _WarningCounter(logging.Handler):
    """Conta gli avvisi del giudizio (costo del capitale / DCF non calcolati) senza riempire lo schermo."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0
        self.examples: list[str] = []

    def emit(self, record):
        self.count += 1
        if len(self.examples) < 5:
            self.examples.append(record.getMessage()[:160])


# ------------------------------------------------------------------------------------------------ giudizi
def collect_verdicts(cfg: dict, data, dates, members_fn, companies: pd.DataFrame, erp_mode: str = "implied",
                     progress=None) -> tuple[pd.DataFrame, dict]:
    """Giudizio point-in-time di tutti i membri dell'indice a ogni data. Restituisce (tabella lunga, informazioni:
    tempi, avvisi, ERP e tasso usati a ogni data)."""
    vlog = logging.getLogger(vd.__name__)
    counter = _WarningCounter()
    vlog.addHandler(counter)
    old_propagate = vlog.propagate
    vlog.propagate = False
    frames, per_date = [], []
    t0 = perf_counter()
    try:
        for i, day in enumerate(pd.DatetimeIndex(dates), 1):
            t1 = perf_counter()
            members = sorted(members_fn(day))
            view = PointInTimeView(cfg, data, day, members, companies, erp_mode)
            table = vd.assess_many(view, members)
            m = view.metrics()
            px = view.data.close
            has_price = pd.Series({t: bool(t in px.columns and px[t].iloc[-6:].notna().any()) for t in members})
            erp = view.implied_erp()
            table = table.reset_index().assign(
                date=day, has_price=lambda x: x["ticker"].map(has_price).fillna(False).astype(bool),
                known_sector=lambda x: x["sector"].notna(),
                market_cap=lambda x: x["ticker"].map(m["market_cap"]))
            frames.append(table)
            per_date.append({"date": day, "members": len(members), "with_price": int(has_price.sum()),
                             "erp": erp.get("erp", np.nan), "risk_free": erp.get("risk_free", np.nan),
                             "seconds": perf_counter() - t1})
            if progress:
                progress(i, len(dates), day, perf_counter() - t1)
    finally:
        vlog.removeHandler(counter)
        vlog.propagate = old_propagate
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    info = {"per_date": pd.DataFrame(per_date), "seconds": perf_counter() - t0, "warnings": counter.count,
            "warning_examples": counter.examples}
    return out, info


# ------------------------------------------------------------------------------------------------ rendimenti
def prepare_close(data) -> pd.DataFrame:
    """Chiusure sul calendario di borsa; dopo l'ultimo prezzo di un titolo si tiene quel prezzo (delistato o
    acquisito = venduto all'ultimo prezzo e tenuto in contanti). Prima del primo prezzo resta vuoto."""
    special = {data.benchmark, data.risk_free_ticker}
    close = data.close[[c for c in data.close.columns if c not in special]].reindex(data.calendar)
    return close.ffill()


def benchmark_close(data) -> pd.Series:
    b = data.benchmark
    return data.close[b].reindex(data.calendar).ffill() if b and b in data.close else pd.Series(dtype=float)


def attach_forward_returns(verdicts: pd.DataFrame, close: pd.DataFrame, spy: pd.Series,
                           horizons: dict[int, int] = HORIZONS_MONTHS) -> pd.DataFrame:
    """Aggiunge a ogni giudizio il rendimento successivo per ogni orizzonte (`r_<mesi>`), quello medio dell'indice a
    pesi uguali alla stessa data (`ew_<mesi>`: tutti i membri con un rendimento) e quello di SPY (`spy_<mesi>`)."""
    v = verdicts.copy()
    dates = pd.DatetimeIndex(sorted(v["date"].unique()))
    for months, h in horizons.items():
        fwd = forward_returns(close, dates, h)
        long = fwd.stack().rename(f"r_{months}")
        long.index.names = ["date", "ticker"]
        v = v.join(long, on=["date", "ticker"])
        v.loc[~v["has_price"], f"r_{months}"] = np.nan
        ew = v.groupby("date")[f"r_{months}"].mean()
        v[f"ew_{months}"] = v["date"].map(ew)
        if not spy.empty:
            sf = forward_returns(spy.to_frame("SPY"), dates, h)["SPY"]
            v[f"spy_{months}"] = v["date"].map(sf)
        else:
            v[f"spy_{months}"] = np.nan
    return v


def _t_overlap(series: np.ndarray, h: int, step: int = STEP_DAYS) -> float:
    """t-stat della media di una serie per data, con osservazioni ridotte per i periodi sovrapposti."""
    x = np.asarray(series, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return np.nan
    sd = x.std(ddof=1)
    if sd <= 0:
        return np.nan
    return float(x.mean() / sd * np.sqrt(n * min(1.0, step / h)))


def category_table(v: pd.DataFrame, horizons: dict[int, int] = HORIZONS_MONTHS, by: str = "category",
                   min_obs: int = 1) -> pd.DataFrame:
    """Una riga per (gruppo, orizzonte). Medie "per data": prima la media del gruppo a ogni data, poi la media nel
    tempo (ogni trimestre pesa uguale). Mediana e quota che batte l'indice sono sulle singole osservazioni."""
    rows = []
    for months, h in horizons.items():
        r, ew, spy = f"r_{months}", f"ew_{months}", f"spy_{months}"
        ok = v[v[r].notna()]
        for key, g in ok.groupby(by, sort=False):
            if len(g) < min_obs:
                continue
            per_date = g.groupby("date").agg(ret=(r, "mean"), ew=(ew, "first"), spy=(spy, "first"))
            ex_ew = per_date["ret"] - per_date["ew"]
            ex_spy = per_date["ret"] - per_date["spy"]
            rows.append({
                by: key, "months": months, "obs": len(g), "dates": len(per_date),
                "mean": float(per_date["ret"].mean()), "median": float(g[r].median()),
                "excess_ew": float(ex_ew.mean()), "excess_spy": float(ex_spy.mean()),
                "median_excess_ew": float((g[r] - g[ew]).median()),
                "beat_ew": float((g[r] > g[ew]).mean()), "beat_spy": float((g[r] > g[spy]).mean()),
                "dispersion": float(g[r].std(ddof=1)) if len(g) > 1 else np.nan,
                "t_excess_ew": _t_overlap(ex_ew.to_numpy(), h),
                "dates_beating_ew": float((ex_ew > 0).mean()),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    if by == "category":
        out["_o"] = out["category"].map({c: i for i, c in enumerate(CATEGORY_ORDER)})
        out = out.sort_values(["months", "_o"]).drop(columns="_o")
    return out.reset_index(drop=True)


def pillar_ic(v: pd.DataFrame, close: pd.DataFrame, horizons: dict[int, int] = HORIZONS_MONTHS,
              min_names: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    """IC dei punteggi dei pilastri con `research.analyze` (stesso calcolo della ricerca sui segnali)."""
    sig = v[v["has_price"]][["date", "ticker", "sector", *PILLARS]].copy()
    sig["sector"] = sig["sector"].fillna("ex membro (settore ignoto)")
    for c in PILLARS:
        sig[c] = pd.to_numeric(sig[c], errors="coerce")
    summary, quant, _ = analyze(sig, close, list(horizons.values()), STEP_DAYS, n_quantiles=5, min_names=min_names,
                                columns=PILLARS)
    inv = {h: m for m, h in horizons.items()}
    for df in (summary, quant):
        if not df.empty:
            df["months"] = df["horizon"].map(inv)
    return summary, quant


# ------------------------------------------------------------------------------------------------ portafogli
@dataclass
class PortfolioResult:
    equity: pd.Series
    stats: dict
    turnover: float              # rotazione annua (quota del portafoglio comprata all'anno)
    holdings: pd.Series          # titoli in portafoglio a ogni data
    empty_dates: int             # date senza titoli (in contanti)
    equity_net: pd.Series = field(default_factory=pd.Series)
    stats_net: dict = field(default_factory=dict)


def group_portfolio(v: pd.DataFrame, close: pd.DataFrame, selector, cost_per_side: float = 0.0015) -> PortfolioResult:
    """Portafoglio a pesi uguali dei titoli scelti da `selector(tabella della data) -> ticker`, comprati alla chiusura
    del giorno di borsa dopo la data e tenuti fino al ribilanciamento successivo (pesi che si muovono con i prezzi).
    Senza titoli: contanti a rendimento zero. `cost_per_side` = commissione + slippage del backtest."""
    dates = pd.DatetimeIndex(sorted(v["date"].unique()))
    idx = close.index
    values = close.to_numpy(dtype=float)
    cols = {c: i for i, c in enumerate(close.columns)}
    starts = [int(idx.searchsorted(d, side="left")) + 1 for d in dates]
    by_date = {d: g for d, g in v.groupby("date")}
    pieces, pieces_net, held = [], [], {}
    level, level_net = 1.0, 1.0
    prev_w = pd.Series(dtype=float)
    turnover_total, empty = 0.0, 0
    for k, d in enumerate(dates):
        s = starts[k]
        if s >= len(idx):
            break
        e = starts[k + 1] if k + 1 < len(dates) else len(idx) - 1
        e = min(e, len(idx) - 1)
        chosen = [t for t in selector(by_date[d]) if t in cols and np.isfinite(values[s, cols[t]])
                  and values[s, cols[t]] > 0]
        chosen = sorted(set(chosen))
        held[d] = len(chosen)
        if chosen:
            j = [cols[t] for t in chosen]
            rel = values[s:e + 1, j] / values[s, j]
            path = np.nanmean(rel, axis=1)
            new_w = pd.Series(1.0 / len(chosen), index=chosen)
            end_w = pd.Series(rel[-1] / rel[-1].sum(), index=chosen)
        else:
            empty += 1
            path = np.ones(e - s + 1)
            new_w = end_w = pd.Series(dtype=float)
        union = new_w.index.union(prev_w.index)
        traded = float((new_w.reindex(union, fill_value=0) - prev_w.reindex(union, fill_value=0)).abs().sum())
        turnover_total += traded / 2
        cost = traded * cost_per_side
        seg = pd.Series(level * path, index=idx[s:e + 1])
        seg_net = pd.Series(level_net * (1 - cost) * path, index=idx[s:e + 1])
        if pieces:  # il primo giorno del tratto è l'ultimo del precedente: non ripeterlo
            seg, seg_net = seg.iloc[1:], seg_net.iloc[1:]
        pieces.append(seg)
        pieces_net.append(seg_net)
        level, level_net = level * path[-1], level_net * (1 - cost) * path[-1]
        prev_w = end_w
    equity = pd.concat(pieces) if pieces else pd.Series(dtype=float)
    equity_net = pd.concat(pieces_net) if pieces_net else pd.Series(dtype=float)
    equity = equity[~equity.index.duplicated(keep="last")]
    equity_net = equity_net[~equity_net.index.duplicated(keep="last")]
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9) if len(equity) > 1 else np.nan
    return PortfolioResult(equity=equity, stats=performance_metrics(equity), turnover=turnover_total / years,
                           holdings=pd.Series(held), empty_dates=empty, equity_net=equity_net,
                           stats_net=performance_metrics(equity_net))


def buy_and_hold(series: pd.Series, start, end=None) -> pd.Series:
    s = series.loc[pd.Timestamp(start):] if end is None else series.loc[pd.Timestamp(start):pd.Timestamp(end)]
    s = s.dropna()
    return s / s.iloc[0] if not s.empty else s


def category_selector(category: str):
    def pick(g: pd.DataFrame) -> list[str]:
        return g.loc[(g["category"] == category) & g["has_price"], "ticker"].tolist()
    return pick


def all_selector(g: pd.DataFrame) -> list[str]:
    return g.loc[g["has_price"], "ticker"].tolist()


# ------------------------------------------------------------------------------------------------ studio completo
@dataclass
class StudyResult:
    verdicts: pd.DataFrame
    info: dict
    categories: pd.DataFrame          # tutte le date
    categories_halves: pd.DataFrame   # colonna "half"
    categories_sector_known: pd.DataFrame
    by_profile: pd.DataFrame
    by_sector: pd.DataFrame
    ic: pd.DataFrame
    ic_halves: pd.DataFrame
    quantiles: pd.DataFrame
    portfolios: dict                  # nome -> PortfolioResult
    portfolio_halves: pd.DataFrame
    survivorship: pd.DataFrame
    params: dict


def _halves(dates: pd.DatetimeIndex) -> tuple[pd.Timestamp, list[tuple[str, pd.DatetimeIndex]]]:
    mid = dates[len(dates) // 2]
    return mid, [("Prima metà", dates[dates < mid]), ("Seconda metà", dates[dates >= mid])]


def analyze_study(verdicts: pd.DataFrame, data, info: dict | None = None,
                  horizons: dict[int, int] = HORIZONS_MONTHS, cost_per_side: float = 0.0015) -> StudyResult:
    close = prepare_close(data)
    spy = benchmark_close(data)
    v = attach_forward_returns(verdicts, close, spy, horizons)
    dates = pd.DatetimeIndex(sorted(v["date"].unique()))
    mid, halves = _halves(dates)
    priced = v[v["has_price"]]

    cats = category_table(priced, horizons)
    halves_rows, ic_halves = [], []
    ic, quant = pillar_ic(v, close, horizons)
    for name, ds in halves:
        part = priced[priced["date"].isin(ds)]
        halves_rows.append(category_table(part, horizons).assign(half=name))
        s, _ = pillar_ic(v[v["date"].isin(ds)], close, horizons)
        ic_halves.append(s.assign(half=name))
    known = category_table(priced[priced["known_sector"]], horizons)
    h12 = {12: horizons[12]} if 12 in horizons else dict(list(horizons.items())[:1])
    prof = priced.assign(group=priced["profile"] + " | " + priced["category"])
    by_profile = category_table(prof, h12, by="group")
    sec = priced.assign(group=priced["sector"].fillna("ex membro (settore ignoto)") + " | " + priced["category"])
    by_sector = category_table(sec, h12, by="group")

    portfolios = {SHORT_LABELS[c]: group_portfolio(v, close, category_selector(c), cost_per_side)
                  for c in CATEGORY_ORDER if c != vd.NO_DATA}
    portfolios["Indice a pesi uguali (tutti i membri con prezzo)"] = group_portfolio(v, close, all_selector,
                                                                                     cost_per_side)
    ph = []
    first_start = portfolios[SHORT_LABELS[vd.CANDIDATE]].equity.index[0] \
        if not portfolios[SHORT_LABELS[vd.CANDIDATE]].equity.empty else None
    for name, res in list(portfolios.items()) + [("SPY", None)]:
        eq = res.equity if res is not None else (buy_and_hold(spy, first_start) if first_start is not None
                                                 else pd.Series(dtype=float))
        if eq.empty:
            continue
        cut = close.index[min(int(close.index.searchsorted(mid)) + 1, len(close.index) - 1)]
        for half, part in (("Prima metà", eq.loc[:cut]), ("Seconda metà", eq.loc[cut:]), ("Tutto", eq)):
            st = performance_metrics(part / part.iloc[0]) if len(part) > 1 else {}
            ph.append({"portfolio": name, "half": half, **{k: st.get(k, np.nan) for k in
                                                            ("cagr", "annual_volatility", "max_drawdown",
                                                             "sharpe_ratio")}})
    surv = (info or {}).get("per_date", pd.DataFrame())
    if not surv.empty:
        surv = surv.assign(coverage=surv["with_price"] / surv["members"])
    params = {"dates": len(dates), "start": dates[0].date(), "end": dates[-1].date(), "split": mid.date(),
              "horizons": horizons, "step_days": STEP_DAYS, "cost_per_side": cost_per_side,
              "data_end": close.index[-1].date()}
    return StudyResult(v, info or {}, cats, pd.concat(halves_rows, ignore_index=True), known, by_profile, by_sector,
                       ic, pd.concat(ic_halves, ignore_index=True), quant, portfolios, pd.DataFrame(ph), surv, params)


def category_agreement(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Matrice dei giudizi di due esecuzioni sugli stessi (data, titolo): righe = a, colonne = b."""
    j = a[["date", "ticker", "category"]].merge(b[["date", "ticker", "category"]], on=["date", "ticker"],
                                                 suffixes=("_a", "_b"))
    m = pd.crosstab(j["category_a"], j["category_b"])
    order = [c for c in CATEGORY_ORDER if c in m.index or c in m.columns]
    return m.reindex(index=order, columns=order, fill_value=0)
