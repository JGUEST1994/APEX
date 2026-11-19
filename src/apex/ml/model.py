"""L2-regularised logistic regression (Newton / IRLS) with feature standardisation.

Small, deterministic and dependency-free. Saved as JSON so a model is a plain local file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class LogisticModel:
    l2: float = 1.0
    max_iter: int = 50
    features: list[str] = field(default_factory=list)

    def fit(self, x: np.ndarray, y: np.ndarray) -> LogisticModel:
        x, y = np.asarray(x, float), np.asarray(y, float)
        if len(np.unique(y)) < 2:
            raise ValueError("need both classes to fit")
        self.mu_, self.sd_ = x.mean(0), x.std(0)
        self.sd_[self.sd_ == 0] = 1.0
        z = np.column_stack([np.ones(len(x)), (x - self.mu_) / self.sd_])
        w = np.zeros(z.shape[1])
        reg = np.full(z.shape[1], self.l2)
        reg[0] = 0.0  # no penalty on the intercept
        for _ in range(self.max_iter):
            p = 1 / (1 + np.exp(-z @ w))
            grad = z.T @ (p - y) + reg * w
            hess = (z * (p * (1 - p))[:, None]).T @ z + np.diag(reg)
            step = np.linalg.solve(hess, grad)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        self.w_ = w
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        z = (np.asarray(x, float) - self.mu_) / self.sd_
        return 1 / (1 + np.exp(-(self.w_[0] + z @ self.w_[1:])))

    def to_dict(self) -> dict:
        return {"type": "logistic", "l2": self.l2, "features": self.features,
                "mu": self.mu_.tolist(), "sd": self.sd_.tolist(), "w": self.w_.tolist()}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: str | Path) -> LogisticModel:
        d = json.loads(Path(path).read_text())
        m = cls(l2=d["l2"], features=d["features"])
        m.mu_, m.sd_, m.w_ = (np.array(d[k]) for k in ("mu", "sd", "w"))
        return m


@dataclass
class GradientBoostedTrees:
    """Binary gradient boosting (log loss) with small regression trees on binned features.

    A dependency-free stand-in for LightGBM: Newton leaf values with L2 shrinkage, feature
    thresholds from training-set quantiles, deterministic. Captures non-linear effects and
    interactions the logistic model cannot.
    """

    n_trees: int = 100
    max_depth: int = 2
    learning_rate: float = 0.1
    l2: float = 1.0
    min_leaf: int = 20
    n_bins: int = 32
    features: list[str] = field(default_factory=list)

    def fit(self, x: np.ndarray, y: np.ndarray) -> GradientBoostedTrees:
        x, y = np.asarray(x, float), np.asarray(y, float)
        if len(np.unique(y)) < 2:
            raise ValueError("need both classes to fit")
        qs = np.linspace(0, 1, self.n_bins + 1)[1:-1]
        self.edges_ = [np.unique(np.quantile(x[:, j], qs)) for j in range(x.shape[1])]
        xb = self._bin(x)
        p0 = np.clip(y.mean(), 1e-6, 1 - 1e-6)
        self.base_ = float(np.log(p0 / (1 - p0)))
        f = np.full(len(y), self.base_)
        self.trees_: list[dict] = []
        for _ in range(self.n_trees):
            p = 1 / (1 + np.exp(-f))
            g, h = p - y, p * (1 - p)
            tree = self._grow(xb, g, h, np.arange(len(y)), 0)
            f += self.learning_rate * self._apply(tree, xb)
            self.trees_.append(tree)
        return self

    def _bin(self, x: np.ndarray) -> np.ndarray:
        return np.column_stack([np.searchsorted(e, x[:, j], side="right")
                                for j, e in enumerate(self.edges_)])

    def _leaf(self, g: np.ndarray, h: np.ndarray) -> dict:
        return {"v": float(-g.sum() / (h.sum() + self.l2))}

    def _grow(self, xb: np.ndarray, g: np.ndarray, h: np.ndarray, idx: np.ndarray,
              depth: int) -> dict:
        gi, hi = g[idx], h[idx]
        if depth >= self.max_depth or len(idx) < 2 * self.min_leaf:
            return self._leaf(gi, hi)
        parent = gi.sum() ** 2 / (hi.sum() + self.l2)
        best = (0.0, -1, -1)
        for j in range(xb.shape[1]):
            nb = len(self.edges_[j]) + 1
            gs = np.bincount(xb[idx, j], gi, nb)
            hs = np.bincount(xb[idx, j], hi, nb)
            cnt = np.bincount(xb[idx, j], minlength=nb)
            gl, hl, cl = np.cumsum(gs)[:-1], np.cumsum(hs)[:-1], np.cumsum(cnt)[:-1]
            gr, hr, cr = gs.sum() - gl, hs.sum() - hl, len(idx) - cl
            gain = gl ** 2 / (hl + self.l2) + gr ** 2 / (hr + self.l2) - parent
            gain[(cl < self.min_leaf) | (cr < self.min_leaf)] = -np.inf
            if len(gain) and gain.max() > best[0]:
                best = (float(gain.max()), j, int(gain.argmax()))
        if best[1] < 0:
            return self._leaf(gi, hi)
        _, j, b = best
        left = xb[idx, j] <= b
        return {"f": j, "b": b, "l": self._grow(xb, g, h, idx[left], depth + 1),
                "r": self._grow(xb, g, h, idx[~left], depth + 1)}

    def _apply(self, tree: dict, xb: np.ndarray) -> np.ndarray:
        out = np.empty(len(xb))
        stack = [(tree, np.arange(len(xb)))]
        while stack:
            node, rows = stack.pop()
            if "v" in node:
                out[rows] = node["v"]
                continue
            go_left = xb[rows, node["f"]] <= node["b"]
            stack += [(node["l"], rows[go_left]), (node["r"], rows[~go_left])]
        return out

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        xb = self._bin(np.asarray(x, float))
        f = self.base_ + self.learning_rate * sum(self._apply(t, xb) for t in self.trees_)
        return 1 / (1 + np.exp(-f))

    def to_dict(self) -> dict:
        return {"type": "gbt", "n_trees": self.n_trees, "max_depth": self.max_depth,
                "learning_rate": self.learning_rate, "l2": self.l2, "min_leaf": self.min_leaf,
                "n_bins": self.n_bins, "features": self.features, "base": self.base_,
                "edges": [e.tolist() for e in self.edges_], "trees": self.trees_}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: str | Path) -> GradientBoostedTrees:
        d = json.loads(Path(path).read_text())
        m = cls(**{k: d[k] for k in ("n_trees", "max_depth", "learning_rate", "l2",
                                     "min_leaf", "n_bins", "features")})
        m.base_, m.trees_ = d["base"], d["trees"]
        m.edges_ = [np.array(e) for e in d["edges"]]
        return m
