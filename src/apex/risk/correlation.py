"""Group instruments whose returns move together, for the correlated-exposure limit.

Single-linkage grouping: two symbols share a group when the absolute correlation of their
returns over the window is at least ``threshold`` (directly or through a chain). Uses
absolute correlation so that a long in one and a short in a mirror-image instrument still
count against the same budget. Pure function; the caller supplies the return history.
"""

from __future__ import annotations

import pandas as pd


def correlation_clusters(returns: pd.DataFrame, threshold: float = 0.7,
                         min_obs: int = 30) -> dict[str, str]:
    """``returns``: one column per symbol, aligned by time. Returns symbol -> group name
    (the alphabetically first member). Symbols with too little history stand alone."""
    cols = sorted(returns.columns)
    parent = {c: c for c in cols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    corr = returns[cols].corr(min_periods=min_obs).abs()
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            c = corr.at[a, b]
            if c == c and c >= threshold:  # skips NaN (too little overlap)
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    return {c: find(c) for c in cols}
