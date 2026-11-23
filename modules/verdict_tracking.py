"""
Misura in avanti del giudizio: i giudizi SALVATI giorno per giorno (tabella `verdict_history`, modules/verdict_history.py)
confrontati con ciò che è successo DOPO. È l'unica prova fuori campione vera: quando il giudizio è stato scritto, i
rendimenti successivi non esistevano ancora (la prova storica di modules/verdict_research.py invece ricostruisce il
passato con i dati di oggi e le regole scritte oggi).

Stesse misure della prova storica, riusandone le funzioni (`attach_forward_returns`, `category_table`, `research.analyze`):
- per categoria e orizzonte: rendimento medio, eccesso rispetto all'indice a pesi uguali (media dei titoli dell'S&P 500
  giudicati quel giorno con un prezzo) e a SPY, quota che batte l'indice, t-stat;
- per pilastro: IC (correlazione di rango punteggio-rendimento tra i titoli della stessa data) e quintili.

Le date sono giornaliere, quindi i periodi si sovrappongono moltissimo: il t-stat usa le osservazioni ridotte
(`n x passo / orizzonte`) e il rapporto conta i PERIODI INDIPENDENTI (giorni di storia / orizzonte). Con pochi mesi di
storia i numeri non dicono nulla: l'avvertenza è nel risultato (`warnings`) e in testa al rapporto.

Solo l'S&P 500 di default (prezzi in dollari, stesso indice di confronto); le europee hanno un'altra valuta e un
altro mercato. Funzione pura sui dati passati: niente database, niente orologio (la data la passa chi chiama).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from modules import verdict as vd
from modules import verdict_research as vr
from modules.research import analyze

HORIZONS = {1: 21, 3: 63, 6: 126, 12: 252}  # mesi -> giorni di borsa
PILLARS = {vd.QUALITY: "Qualità", vd.SOLIDITY: "Solidità", vd.PRICE: "Prezzo", vd.FLAGS: "Allarmi (alto = pochi)",
           "overall": "Punteggio complessivo"}
DEFAULTS = {"min_age_days": 21, "min_independent_periods": 20}
CAVEAT = ("Misura su giudizi salvati prima di conoscere i rendimenti (fuori campione vero), ma con pochi mesi di "
          "storia NON dice nulla: i periodi si sovrappongono e un solo mercato (un solo anno) decide tutto. "
          "Servono anni prima di trarre conclusioni.")


def tracking_settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("verdict", {}) or {}).get("tracking", {})}


@dataclass
class TrackingResult:
    observations: pd.DataFrame        # un giudizio per riga con i rendimenti successivi (r_, ew_, spy_<mesi>)
    categories: pd.DataFrame          # vr.category_table con il t-stat corretto per le date giornaliere
    ic: pd.DataFrame                  # IC dei pilastri (research.analyze)
    quantiles: pd.DataFrame
    periods: pd.DataFrame             # per orizzonte: osservazioni, date, periodi indipendenti, se basta
    params: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def enough(self) -> bool:
        return bool(not self.periods.empty and self.periods["enough"].any())


def to_observations(history: pd.DataFrame, sectors: dict | None = None) -> pd.DataFrame:
    """Righe di `verdict_history` -> forma di verdict_research (date, ticker, category, pilastri, has_price, sector)."""
    h = history
    out = pd.DataFrame({
        "date": pd.to_datetime(h["as_of"]).to_numpy(), "ticker": h["ticker"].to_numpy(),
        "category": h["category"].to_numpy(), "profile": h["profile"].to_numpy(),
        vd.QUALITY: pd.to_numeric(h["quality"], errors="coerce").to_numpy(dtype=float),
        vd.SOLIDITY: pd.to_numeric(h["solidity"], errors="coerce").to_numpy(dtype=float),
        vd.PRICE: pd.to_numeric(h["price_pillar"], errors="coerce").to_numpy(dtype=float),
        vd.FLAGS: pd.to_numeric(h["flags"], errors="coerce").to_numpy(dtype=float),
        "overall": pd.to_numeric(h["overall"], errors="coerce").to_numpy(dtype=float),
        "has_price": pd.to_numeric(h["price"], errors="coerce").notna().to_numpy(),
        "config_hash": h["config_hash"].to_numpy(), "universe": h["universe"].to_numpy(),
    })
    out["sector"] = out["ticker"].map(sectors or {})
    return out


def _step(dates: pd.DatetimeIndex, calendar: pd.DatetimeIndex) -> int:
    """Passo tipico tra due date giudicate, in giorni di borsa (1 se giornaliere)."""
    if len(dates) < 2:
        return 1
    pos = calendar.searchsorted(dates)
    return max(1, int(np.median(np.diff(pos))))


def _fix_t(cats: pd.DataFrame, v: pd.DataFrame, horizons: dict[int, int], step: int) -> pd.DataFrame:
    """category_table calcola il t-stat per date trimestrali: qui lo si ricalcola con il passo vero."""
    if cats.empty:
        return cats
    cats = cats.copy()
    for i, row in cats.iterrows():
        m = int(row["months"])
        r, ew = f"r_{m}", f"ew_{m}"
        g = v[(v["category"] == row["category"]) & v[r].notna()]
        per_date = g.groupby("date").agg(ret=(r, "mean"), ew=(ew, "first"))
        cats.at[i, "t_excess_ew"] = vr._t_overlap((per_date["ret"] - per_date["ew"]).to_numpy(), horizons[m], step)
    return cats


def forward_study(history: pd.DataFrame, data, today=None, min_age_days: int = 21,
                  horizons: dict[int, int] | None = None, universe: str | None = "sp500",
                  config_hash: str | None = None, sectors: dict | None = None, min_names: int = 20,
                  min_independent_periods: int = 20) -> TrackingResult:
    """Rendimenti successivi dei giudizi salvati da almeno `min_age_days` giorni (rispetto a `today`, default l'ultimo
    giorno dei dati). `universe`: "sp500" (default), "europe", ... o None = tutti; `config_hash`: solo i giudizi fatti
    con quella configurazione (None = tutti, il rapporto dice quante impronte ci sono)."""
    horizons = dict(HORIZONS if horizons is None else horizons)
    close = vr.prepare_close(data)
    spy = vr.benchmark_close(data)
    today = pd.Timestamp(today) if today is not None else (close.index[-1] if len(close.index) else pd.Timestamp.now())
    h = history.copy() if history is not None else pd.DataFrame()
    params = {"today": today.date(), "min_age_days": int(min_age_days), "universe": universe or "tutti",
              "horizons": horizons, "rows_in_history": int(len(h))}
    warnings = [CAVEAT]
    empty = TrackingResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), params,
                           warnings)
    if h.empty:
        warnings.append("Lo storico dei giudizi è vuoto: si riempie a ogni aggiornamento serale.")
        return empty
    h["as_of"] = pd.to_datetime(h["as_of"])
    if universe:
        h = h[h["universe"] == universe]
    if config_hash:
        h = h[h["config_hash"] == config_hash]
    params["fingerprints"] = int(h["config_hash"].nunique()) if not h.empty else 0
    h = h[h["as_of"] <= today - pd.Timedelta(days=int(min_age_days))]
    if h.empty:
        warnings.append(f"Nessun giudizio salvato da almeno {min_age_days} giorni.")
        return empty
    if params["fingerprints"] > 1:
        warnings.append(f"I giudizi misurati vengono da {params['fingerprints']} configurazioni o versioni del codice "
                        "diverse: la misura le mescola.")
    v = to_observations(h, sectors)
    v = vr.attach_forward_returns(v, close, spy, horizons)
    dates = pd.DatetimeIndex(sorted(v["date"].unique()))
    step = _step(dates, close.index)
    priced = v[v["has_price"]]
    cats = _fix_t(vr.category_table(priced, horizons), priced, horizons, step)

    sig = priced[["date", "ticker", "sector", *PILLARS]].copy()
    sig["sector"] = sig["sector"].astype(object).where(sig["sector"].notna(), "settore ignoto")
    ic, quant, _ = analyze(sig, close, list(horizons.values()), step, n_quantiles=5, min_names=min_names,
                           columns=PILLARS)
    inv = {d: m for m, d in horizons.items()}
    for df in (ic, quant):
        if not df.empty:
            df["months"] = df["horizon"].map(inv)

    rows = []
    for m, days in horizons.items():
        ok = priced[priced[f"r_{m}"].notna()]
        ds = pd.DatetimeIndex(sorted(ok["date"].unique()))
        span = int(close.index.searchsorted(ds[-1]) - close.index.searchsorted(ds[0])) if len(ds) else 0
        indep = (span / days + 1) if len(ds) else 0.0
        rows.append({"months": m, "obs": int(len(ok)), "dates": int(len(ds)),
                     "first": ds[0].date() if len(ds) else None, "last": ds[-1].date() if len(ds) else None,
                     "independent_periods": round(indep, 1), "enough": bool(indep >= min_independent_periods)})
    periods = pd.DataFrame(rows)
    if not periods["enough"].any():
        best = periods.loc[periods["independent_periods"].idxmax()] if not periods.empty else None
        n = 0.0 if best is None else best["independent_periods"]
        warnings.append(f"Al massimo {str(n).replace('.', ',')} periodi indipendenti (ne servono almeno "
                        f"{min_independent_periods}): nessun numero qui sotto è una prova.")
    params.update(step_days=step, dates=len(dates), first=dates[0].date(), last=dates[-1].date(),
                  observations=int(len(v)), with_price=int(v["has_price"].sum()))
    return TrackingResult(v, cats, ic, quant, periods, params, warnings)
