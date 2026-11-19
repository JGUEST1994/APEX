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
