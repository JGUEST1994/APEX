"""`apex export`: save results as CSV files that open in Excel.

Read-only. Every exported result row is marked ``simulated`` because none of it is real
trading.
"""

from __future__ import annotations

import json

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import Experiment, PaperAccount, PaperFill, StrategyVersion
from apex.db.models import Strategy as StrategyRow

KINDS = ("experiments", "leaderboard", "bars", "fills")


def experiments(s: Session) -> pd.DataFrame:
    """One row per recorded backtest/validation run, with every scalar metric as a column."""
    rows = s.execute(select(Experiment, StrategyRow.key, StrategyVersion.version)
                     .join(StrategyVersion, Experiment.strategy_version_id == StrategyVersion.id)
                     .join(StrategyRow, StrategyVersion.strategy_id == StrategyRow.id)
                     .order_by(Experiment.id))
    out = []
    for e, key, ver in rows:
        m = e.data_manifest or {}
        rec = {"id": e.id, "created_at": e.created_at.isoformat(), "kind": e.kind,
               "strategy": key, "strategy_version": ver, "symbol": m.get("symbol"),
               "timeframe": m.get("timeframe"), "source": m.get("source"),
               "start": m.get("start"), "end": m.get("end"),
               "params": json.dumps(e.params, sort_keys=True, default=str),
               "data_sha256": m.get("data_sha256"), "code_version": e.code_version,
               "simulated": True}
        for k, v in sorted((e.metrics or {}).items()):
            if isinstance(v, (int, float, str, bool)) or v is None:
                rec[f"m_{k}"] = v
        out.append(rec)
    return pd.DataFrame(out)


def leaderboard(s: Session) -> pd.DataFrame:
    from apex.backtest.tournament import leaderboard as lb

    df = lb(s)
    return df.assign(simulated=True) if not df.empty else df


def bars(s: Session, symbol: str, timeframe: str, source: str) -> pd.DataFrame:
    from apex.domain.market import Timeframe
    from apex.marketdata.repository import get_instrument, load_bars

    inst = get_instrument(s, symbol)
    df = load_bars(s, inst.id, Timeframe(timeframe), source)
    if not df.empty:
        df["ts"] = df["ts"].map(lambda t: t.isoformat())
    return df


def fills(s: Session, name: str) -> pd.DataFrame:
    acct = s.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acct is None:
        raise LookupError(f"no practice account called {name!r}")
    rows = s.scalars(select(PaperFill).where(PaperFill.account_id == acct.id)
                     .order_by(PaperFill.id)).all()
    return pd.DataFrame([{"ts": f.ts.isoformat(), "symbol": f.symbol, "qty": f.qty,
                          "price": f.price, "cost": f.cost,
                          "client_order_id": f.client_order_id, "simulated": True}
                         for f in rows],
                        columns=["ts", "symbol", "qty", "price", "cost", "client_order_id",
                                 "simulated"])
