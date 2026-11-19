"""Labels for supervised learning. Labels LOOK AHEAD by definition; every label records the
last bar it depends on (``end``) so training can purge overlapping samples."""

from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier(bars: pd.DataFrame, horizon: int = 10, pt: float = 1.0, sl: float = 1.0,
                   vol_n: int = 20) -> pd.DataFrame:
    """Triple-barrier labels (Lopez de Prado). From each bar's close, the upper/lower
    barriers sit ``pt``/``sl`` x the trailing per-bar vol x sqrt(horizon) away. Label +1 if
    the upper barrier is touched first (by close), -1 for the lower, else the sign of the
    return at the vertical barrier. Columns: ``label``, ``ret``, ``end`` (position of the
    last bar the label can depend on, i + horizon). Rows without a full horizon or vol get NaN."""
    c = bars["close"].to_numpy(float)
    n = len(c)
    vol = np.log(bars["close"]).diff().rolling(vol_n, min_periods=vol_n).std().to_numpy()
    label, ret, end = np.full(n, np.nan), np.full(n, np.nan), np.full(n, -1)
    for i in range(n - horizon):
        if np.isnan(vol[i]):
            continue
        width = vol[i] * np.sqrt(horizon)
        path = np.log(c[i + 1:i + horizon + 1] / c[i])
        up = np.nonzero(path >= pt * width)[0]
        dn = np.nonzero(path <= -sl * width)[0]
        first_up = up[0] if len(up) else horizon
        first_dn = dn[0] if len(dn) else horizon
        if first_up < first_dn:
            label[i], j = 1.0, first_up
        elif first_dn < first_up:
            label[i], j = -1.0, first_dn
        else:
            j = horizon - 1
            label[i] = float(np.sign(path[-1])) or 0.0
        # purge window is the full horizon: whether a label exists at all depends on it
        ret[i], end[i] = path[j], i + horizon
    return pd.DataFrame({"label": label, "ret": ret, "end": end}, index=bars.index)
