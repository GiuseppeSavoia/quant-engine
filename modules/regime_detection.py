"""
Regime Detection con Hidden Markov Model (HMM) gaussiano.

L'HMM è implementato qui in numpy (Baum-Welch + Viterbi, covarianza diagonale):
niente dipendenza da hmmlearn, che su Mac richiede spesso un compilatore.

Il regime è una proprietà del MERCATO: il modello si addestra una sola volta per
esecuzione sul benchmark (es. SPY) usando due caratteristiche giornaliere,
il rendimento logaritmico e la volatilità mobile. Gli stati vengono poi ordinati
per rendimento corretto per il rischio: il più basso è "Bear", il più alto "Bull".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from modules.technical import log_returns, rule_based_regime

logger = logging.getLogger(__name__)

LABELS = {2: ["Bear", "Bull"], 3: ["Bear", "Neutral", "Bull"]}


class GaussianHMM:
    """HMM gaussiano a covarianza diagonale, addestrato con l'algoritmo di Baum-Welch."""

    def __init__(self, n_states: int = 3, n_iter: int = 200, tol: float = 1e-4, min_var: float = 1e-4):
        self.n_states = n_states
        self.n_iter = n_iter
        self.tol = tol
        self.min_var = min_var
        self.converged = False

    # --------------------------------------------------------------- internals
    def _log_emission(self, X: np.ndarray) -> np.ndarray:
        diff = X[:, None, :] - self.means_[None, :, :]
        return -0.5 * (np.log(2 * np.pi * self.vars_)[None, :, :] + diff**2 / self.vars_[None, :, :]).sum(axis=2)

    def _forward_backward(self, log_b: np.ndarray):
        """Forward-backward con scaling (Rabiner): stabile e molto più veloce della versione in log."""
        T, K = log_b.shape
        A = self.transmat_
        offset = log_b.max(axis=1, keepdims=True)
        b = np.exp(log_b - offset)
        alpha = np.empty((T, K))
        beta = np.empty((T, K))
        scale = np.empty(T)
        alpha[0] = self.startprob_ * b[0]
        scale[0] = alpha[0].sum()
        alpha[0] /= scale[0]
        for t in range(1, T):
            a = (alpha[t - 1] @ A) * b[t]
            scale[t] = a.sum()
            alpha[t] = a / scale[t]
        beta[-1] = 1.0
        for t in range(T - 2, -1, -1):
            beta[t] = A @ (b[t + 1] * beta[t + 1]) / scale[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        xi = (alpha[:-1, :, None] * A[None, :, :] * (b[1:] * beta[1:])[:, None, :]
              / scale[1:, None, None]).sum(axis=0)
        loglik = float(np.log(scale).sum() + offset.sum())
        return gamma, xi, loglik

    def _init_params(self, X: np.ndarray) -> None:
        K = self.n_states
        # Inizializzazione deterministica: quantili della prima caratteristica (rendimento)
        order = np.argsort(X[:, 0], kind="stable")
        chunks = np.array_split(order, K)
        self.means_ = np.array([X[c].mean(axis=0) for c in chunks])
        self.vars_ = np.array([np.maximum(X[c].var(axis=0), self.min_var) for c in chunks])
        self.startprob_ = np.full(K, 1.0 / K)
        self.transmat_ = np.full((K, K), 0.05 / max(K - 1, 1))
        np.fill_diagonal(self.transmat_, 0.95)

    # --------------------------------------------------------------- API
    def fit(self, X: np.ndarray) -> "GaussianHMM":
        X = np.asarray(X, dtype=float)
        self._init_params(X)  # inizializzazione deterministica: stessi dati -> stesso risultato
        prev = -np.inf
        for _ in range(self.n_iter):
            gamma, xi, loglik = self._forward_backward(self._log_emission(X))

            weights = gamma.sum(axis=0) + 1e-12
            self.startprob_ = np.clip(gamma[0], 1e-6, None)
            self.startprob_ /= self.startprob_.sum()
            self.transmat_ = np.clip(xi / xi.sum(axis=1, keepdims=True), 1e-6, None)
            self.transmat_ /= self.transmat_.sum(axis=1, keepdims=True)
            self.means_ = (gamma.T @ X) / weights[:, None]
            self.vars_ = np.maximum((gamma.T @ X**2) / weights[:, None] - self.means_**2, self.min_var)

            if abs(loglik - prev) < self.tol:
                self.converged = True
                break
            prev = loglik
        self.loglik_ = loglik
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        gamma, _, _ = self._forward_backward(self._log_emission(np.asarray(X, dtype=float)))
        return gamma

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Sequenza di stati più probabile (Viterbi)."""
        log_b = self._log_emission(np.asarray(X, dtype=float))
        T, K = log_b.shape
        log_A = np.log(self.transmat_)
        delta = np.log(self.startprob_) + log_b[0]
        back = np.zeros((T, K), dtype=int)
        for t in range(1, T):
            scores = delta[:, None] + log_A
            back[t] = scores.argmax(axis=0)
            delta = scores.max(axis=0) + log_b[t]
        states = np.empty(T, dtype=int)
        states[-1] = int(delta.argmax())
        for t in range(T - 1, 0, -1):
            states[t - 1] = back[t, states[t]]
        return states


@dataclass
class RegimeResult:
    label: str               # "Bull" | "Neutral" | "Bear"
    probability: float       # probabilità dello stato corrente
    method: str              # "hmm" o "rule_based"
    state_means: dict | None = None  # rendimento medio annualizzato per regime


class RegimeDetector:
    def __init__(self, cfg_regime: dict):
        self.n_states = int(cfg_regime.get("n_states", 3))
        if self.n_states not in LABELS:
            raise ValueError("n_states deve essere 2 o 3")
        self.lookback = int(cfg_regime.get("lookback_days", 756))
        self.vol_window = int(cfg_regime.get("vol_window", 20))
        self.min_obs = int(cfg_regime.get("min_observations", 250))

    def features(self, prices: pd.Series) -> pd.DataFrame:
        r = log_returns(prices)
        vol = r.rolling(self.vol_window).std()
        return pd.concat({"ret": r, "vol": vol}, axis=1).dropna()

    def detect(self, prices: pd.Series) -> RegimeResult:
        prices = pd.Series(prices, dtype=float).dropna().tail(self.lookback + self.vol_window + 1)
        feats = self.features(prices)
        if len(feats) < self.min_obs:
            return RegimeResult(rule_based_regime(prices), float("nan"), "rule_based")
        try:
            mu, sd = feats.mean(), feats.std().replace(0, 1.0)
            X = ((feats - mu) / sd).to_numpy()
            model = GaussianHMM(self.n_states).fit(X)
            states = model.predict(X)
            proba = model.predict_proba(X)[-1]
            # ordina gli stati per rendimento corretto per il rischio (media / deviazione standard):
            # la sola media è troppo rumorosa e può far sembrare "Bull" una fase di crollo volatile
            raw_means = model.means_[:, 0] * sd["ret"] + mu["ret"]
            raw_std = np.sqrt(model.vars_[:, 0]) * sd["ret"]
            order = np.argsort(raw_means / raw_std)
            label_of = {int(state): LABELS[self.n_states][rank] for rank, state in enumerate(order)}
            current = int(states[-1])
            means = {label_of[int(s)]: float(raw_means[s] * 252) for s in range(self.n_states)}
            return RegimeResult(label_of[current], float(proba[current]), "hmm", means)
        except Exception as exc:  # numericamente raro, ma non deve fermare il sistema
            logger.warning("HMM non riuscito (%s): uso il regime basato su regole", exc)
            return RegimeResult(rule_based_regime(prices), float("nan"), "rule_based")
