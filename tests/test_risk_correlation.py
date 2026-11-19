"""Correlated-exposure cap: grouping instruments that move together, and the risk check."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from apex.core.config import TradingMode
from apex.risk.correlation import correlation_clusters
from apex.risk.limits import RiskLimits
from apex.risk.service import AccountState, OrderIntent, RiskService

NOW = datetime(2025, 1, 2, tzinfo=UTC)


def test_clusters_group_correlated_and_mirror_series():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 300)
    df = pd.DataFrame({
        "SPY": base + rng.normal(0, 0.002, 300),
        "QQQ": base + rng.normal(0, 0.002, 300),
        "SH": -base + rng.normal(0, 0.002, 300),  # inverse fund: same risk, opposite sign
        "GLD": rng.normal(0, 0.01, 300),           # independent
    })
    df.loc[:279, "NEW"] = np.nan                   # only 20 observations
    df["NEW"] = df["NEW"].astype(float)
    df.loc[280:, "NEW"] = base[280:]
    c = correlation_clusters(df)
    assert c["SPY"] == c["QQQ"] == c["SH"] == "QQQ"
    assert c["GLD"] == "GLD" and c["NEW"] == "NEW"


def _acct(**kw):
    return AccountState(equity=100_000.0, day_start_equity=100_000.0, peak_equity=100_000.0,
                        **kw)


def test_risk_refuses_new_risk_in_a_crowded_group():
    r = RiskService(RiskLimits(), TradingMode.PAPER)
    clusters = {"SPY": "IWM", "QQQ": "IWM", "IWM": "IWM", "GLD": "GLD"}
    acct = _acct(positions={"QQQ": 19_000.0, "GLD": 19_000.0}, clusters=clusters)
    # 19k QQQ + 19k new SPY = 38% of equity in one group: allowed
    ok = r.check(OrderIntent("a", "SPY", 190, 100.0, 1.0, 99.0), acct, data_stale=False,
                 now=NOW)
    assert ok.approved, ok.reasons
    crowded = _acct(positions={"QQQ": 15_000.0, "IWM": 15_000.0, "SPY": 9_000.0},
                    clusters=clusters)
    d = r.check(OrderIntent("b", "SPY", 20, 100.0, 1.0, 99.0), crowded, data_stale=False,
                now=NOW)
    assert d.reasons == ("max correlated exposure",)
    # without the grouping the same order is fine
    plain = _acct(positions={"QQQ": 15_000.0, "IWM": 15_000.0, "SPY": 9_000.0})
    assert r.check(OrderIntent("c", "SPY", 20, 100.0, 1.0, 99.0), plain, data_stale=False,
                   now=NOW).approved
    # cutting a position in a crowded group is always allowed
    assert r.check(OrderIntent("d", "SPY", -50, 100.0, 1.0, None), crowded,
                   data_stale=False, now=NOW).approved


def test_cluster_limit_only_tightens():
    lim = RiskLimits()
    assert lim.tightened(max_cluster_exposure=0.3).max_cluster_exposure == 0.3
    try:
        lim.tightened(max_cluster_exposure=0.9)
    except ValueError:
        pass
    else:
        raise AssertionError("loosening must be refused")
