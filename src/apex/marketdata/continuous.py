"""Continuous futures: stitch individual contracts into one back-adjusted series.

Roll rule (causal): stay in the front contract until the first bar on which the next
contract's volume is higher (both trading), hold the old contract through that bar and
switch from the next bar. If volume never crosses, roll on the last bar the two overlap.
The price gap between the two contracts at the roll bar's close is then removed from all
earlier history, so the newest contract keeps its real prices:

* ``difference`` (default): add the gap in points. Point moves, and so P&L per contract,
  are preserved; very old prices can drift far from what was quoted (even below zero).
* ``ratio``: multiply by new/old. Percentage moves are preserved and prices stay positive.

Use the stitched series for research and backtests. Its old prices are not prices anyone
traded at.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from apex.core.errors import ProviderError

METHODS = ("difference", "ratio")
_PX = ["open", "high", "low", "close"]


@dataclass(frozen=True)
class Roll:
    ts: pd.Timestamp  # last bar held in the old contract (its close sets the gap)
    old: str
    new: str
    gap: float  # new close - old close at ``ts``
    reason: str  # "volume" or "overlap end"


def stitch(contracts: list[tuple[str, pd.DataFrame]],
           method: str = "difference") -> tuple[pd.DataFrame, list[Roll]]:
    """``contracts`` are canonical bar frames ordered from oldest to newest expiry."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    if not contracts:
        raise ProviderError("no contract files")
    segs: list[pd.DataFrame] = []
    rolls: list[Roll] = []
    since = None  # first ts to use from the current contract
    for (name, cur), (nxt_name, nxt) in zip(contracts, contracts[1:], strict=False):
        cur = cur if since is None else cur[cur["ts"] > since]
        both = cur.merge(nxt, on="ts", suffixes=("", "_n"))
        if both.empty:
            raise ProviderError(f"{name} and {nxt_name} never trade at the same time, so "
                                "there is no price to roll at")
        cross = both[both["volume_n"] > both["volume"]]
        row, reason = ((cross.iloc[0], "volume") if not cross.empty
                       else (both.iloc[-1], "overlap end"))
        segs.append(cur[cur["ts"] <= row["ts"]])
        gap = float(row["close_n"] - row["close"])
        if method == "ratio" and (row["close"] <= 0 or row["close_n"] <= 0):
            raise ProviderError(f"cannot ratio-adjust at {row['ts']}: non-positive price")
        rolls.append(Roll(row["ts"], name, nxt_name, gap, reason))
        since = row["ts"]
    last = contracts[-1][1]
    segs.append(last if since is None else last[last["ts"] > since])

    # Adjust each segment by every roll after it (newest segment untouched).
    out = []
    shift, factor = 0.0, 1.0
    for i in range(len(segs) - 1, -1, -1):
        seg = segs[i].copy()
        if i < len(rolls):
            r = rolls[i]
            if method == "difference":
                shift += r.gap
            else:
                old_close = float(segs[i].loc[segs[i]["ts"] == r.ts, "close"].iloc[0])
                factor *= (old_close + r.gap) / old_close
        seg[_PX] = seg[_PX] * factor if method == "ratio" else seg[_PX] + shift
        out.append(seg)
    frame = pd.concat(out[::-1], ignore_index=True)
    return frame.sort_values("ts", kind="stable").reset_index(drop=True), rolls
