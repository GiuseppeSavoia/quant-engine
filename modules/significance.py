"""Significatività statistica di un backtest dopo molti tentativi (Bailey e López de Prado).

Sharpe deflazionato (DSR): probabilità che lo Sharpe vero sia positivo, una volta tolta la parte che si ottiene
solo perché si sono provate N configurazioni e si guarda la migliore. Il registro dei tentativi è la tabella
`experiments` (una riga per configurazione e periodo): `trial_sharpes` ne legge gli Sharpe giornalieri.
Nessun parametro stimato dai dati del backtest: serve solo a dire quanto fidarsi di un numero.
"""
from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

EULER_GAMMA = 0.5772156649015329


def daily_sharpe(equity: pd.Series) -> float:
    """Sharpe giornaliero (non annualizzato) dai rendimenti della curva; 0 se piatta o troppo corta."""
    rets = equity.dropna().pct_change().dropna()
    if len(rets) < 3 or rets.std() < 1e-12:
        return 0.0
    return float(rets.mean() / rets.std())


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Sharpe massimo atteso (giornaliero) tra n_trials prove senza alcun edge vero."""
    if n_trials <= 1 or sharpe_variance <= 0:
        return 0.0
    sd = sqrt(sharpe_variance)
    return sd * ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials)
                 + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * np.e)))


def deflated_sharpe(equity: pd.Series, n_trials: int, sharpe_variance: float) -> dict:
    """DSR di una curva: {sharpe_annual, sharpe_benchmark_annual, dsr, n_obs}. dsr = None se i dati non bastano."""
    rets = equity.dropna().pct_change().dropna()
    n = len(rets)
    if n < 30 or rets.std() < 1e-12:
        return {"sharpe_annual": 0.0, "sharpe_benchmark_annual": 0.0, "dsr": None, "n_obs": n}
    sr = float(rets.mean() / rets.std())
    g3, g4 = float(skew(rets)), float(kurtosis(rets, fisher=False))
    sr0 = expected_max_sharpe(n_trials, sharpe_variance)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2
    if denom <= 0:
        return {"sharpe_annual": sr * sqrt(252), "sharpe_benchmark_annual": sr0 * sqrt(252), "dsr": None, "n_obs": n}
    z = (sr - sr0) * sqrt(n - 1) / sqrt(denom)
    return {"sharpe_annual": sr * sqrt(252), "sharpe_benchmark_annual": sr0 * sqrt(252),
            "dsr": float(norm.cdf(z)), "n_obs": n}


def trial_sharpes(experiments: pd.DataFrame) -> list[float]:
    """Sharpe giornaliero di ogni configurazione diversa provata (periodo 'all'; l'ultima per configurazione)."""
    if experiments is None or experiments.empty:
        return []
    df = experiments
    if "period" in df:
        df = df[df["period"].fillna("all") == "all"]
    df = df.drop_duplicates("config_hash", keep="last")
    return [daily_sharpe(eq) for eq in df["equity"] if isinstance(eq, pd.Series) and not eq.empty]


def assess(equity: pd.Series, experiments: pd.DataFrame) -> dict:
    """DSR di `equity` contro tutti i tentativi registrati (almeno n = 1)."""
    sharpes = trial_sharpes(experiments)
    n = max(len(sharpes), 1)
    var = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    out = deflated_sharpe(equity, n, var)
    out["n_trials"] = n
    return out


def verdict_text(a: dict) -> str:
    """Frase in italiano per la webapp e i rapporti."""
    if a.get("dsr") is None:
        return "Dati insufficienti per stimare la probabilità che lo Sharpe sia reale."
    return (f"Dopo {a['n_trials']} configurazioni provate, la probabilità che lo Sharpe vero sia positivo è "
            f"{a['dsr']:.0%} (Sharpe osservato {a['sharpe_annual']:.2f}; quello che darebbe la sola fortuna del "
            f"migliore tra i tentativi: {a['sharpe_benchmark_annual']:.2f}). Sotto il 95% non è provato.")
