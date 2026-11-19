"""Minimal Gaussian hidden Markov model for regime detection.

Fit with Baum-Welch (EM) on a TRAINING window only. Use ``filter`` for anything that feeds
a decision: it returns forward (filtered) probabilities, where the value at t uses
observations <= t. ``smooth`` exists for research plots only and looks ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


@dataclass
class GaussianHMM:
    n_states: int = 2
    n_iter: int = 100
    tol: float = 1e-6
    seed: int = 0
    min_var: float = 1e-10

    def _emission(self, x: np.ndarray) -> np.ndarray:
        var = self.var_[None, :]
        return np.exp(-0.5 * (x[:, None] - self.mean_[None, :]) ** 2 / var) \
            / np.sqrt(2 * np.pi * var) + _EPS

    def _forward(self, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n, k = b.shape
        alpha, scale = np.zeros((n, k)), np.zeros(n)
        a = self.start_ * b[0]
        for t in range(n):
            if t:
                a = (alpha[t - 1] @ self.trans_) * b[t]
            scale[t] = a.sum()
            alpha[t] = a / scale[t]
        return alpha, scale

    def fit(self, x: np.ndarray) -> GaussianHMM:
        x = np.asarray(x, float)
        x = x[~np.isnan(x)]
        k = self.n_states
        if len(x) < 10 * k:
            raise ValueError("not enough observations to fit the HMM")
        # deterministic init: states ordered by quantiles of |x|-sorted chunks
        order = np.argsort(np.abs(x - x.mean()))
        chunks = np.array_split(x[order], k)
        self.mean_ = np.array([c.mean() for c in chunks])
        self.var_ = np.array([max(c.var(), self.min_var) for c in chunks])
        self.start_ = np.full(k, 1 / k)
        self.trans_ = np.full((k, k), 0.05 / max(k - 1, 1))
        np.fill_diagonal(self.trans_, 0.95)
        prev = -np.inf
        for _ in range(self.n_iter):
            b = self._emission(x)
            alpha, scale = self._forward(b)
            beta = np.ones_like(alpha)
            for t in range(len(x) - 2, -1, -1):
                beta[t] = (self.trans_ @ (b[t + 1] * beta[t + 1])) / scale[t + 1]
            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)
            xi = (alpha[:-1, :, None] * self.trans_[None] * (b[1:] * beta[1:])[:, None, :]
                  / scale[1:, None, None])
            self.start_ = gamma[0]
            self.trans_ = xi.sum(0) / (xi.sum((0, 2))[:, None] + _EPS)
            w = gamma.sum(0) + _EPS
            self.mean_ = (gamma * x[:, None]).sum(0) / w
            self.var_ = np.maximum((gamma * (x[:, None] - self.mean_) ** 2).sum(0) / w,
                                   self.min_var)
            ll = np.log(scale).sum()
            if ll - prev < self.tol:
                break
            prev = ll
        self.loglik_ = float(prev if np.isfinite(prev) else ll)
        # canonical order: state 0 = lowest variance
        idx = np.argsort(self.var_)
        self.mean_, self.var_ = self.mean_[idx], self.var_[idx]
        self.start_, self.trans_ = self.start_[idx], self.trans_[np.ix_(idx, idx)]
        return self

    def filter(self, x: np.ndarray) -> np.ndarray:
        """Causal state probabilities P(state_t | x_0..x_t). NaN rows get the prior."""
        x = np.asarray(x, float)
        out = np.zeros((len(x), self.n_states))
        p = self.start_.copy()
        first = True
        for t, xt in enumerate(x):
            prior = p if first else p @ self.trans_
            if np.isnan(xt):
                p = prior
            else:
                post = prior * self._emission(np.array([xt]))[0]
                p = post / post.sum()
                first = False
            out[t] = p
        return out

    def expected_durations(self) -> np.ndarray:
        return 1.0 / np.maximum(1 - np.diag(self.trans_), _EPS)
