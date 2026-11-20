"""Practice (paper) account value over time, rebuilt from the fill ledger (read-only report;
it changes nothing in the paper trading code or its records).

Value at each processed bar's close = cash (initial capital minus every fill's cash flow
and costs up to that bar) + units held x that bar's close x contract multiplier. The same
arithmetic as the paper portfolio, so the last point equals the account's value.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import PaperAccount, PaperFill
from apex.domain.market import Timeframe
from apex.marketdata.repository import get_instrument, load_bars, to_spec


def equity_history(session: Session, name: str) -> pd.Series:
    acc = session.scalar(select(PaperAccount).where(PaperAccount.name == name))
    if acc is None:
        raise KeyError(name)
    if acc.last_bar_ts is None:
        return pd.Series(dtype=float)
    fills = session.scalars(select(PaperFill).where(PaperFill.account_id == acc.id)
                            .order_by(PaperFill.id)).all()
    closes, mults = {}, {}
    for sym in acc.all_symbols:
        inst = get_instrument(session, sym)
        bars = load_bars(session, inst.id, Timeframe(acc.timeframe), acc.source, None, None)
        bars = bars[bars["ts"] <= acc.last_bar_ts]
        closes[sym] = pd.Series(bars["close"].to_numpy(), index=pd.DatetimeIndex(bars["ts"]))
        mults[sym] = to_spec(inst).contract_multiplier
    px = pd.DataFrame(closes).sort_index().ffill()  # a closed market keeps its last close
    if px.empty:
        return pd.Series(dtype=float)
    flow = pd.Series(0.0, index=px.index)
    units = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for f in fills:  # fills happen at a bar's open, so they count from that bar's close
        ts = pd.Timestamp(f.ts)
        at = px.index.searchsorted(ts)
        if at >= len(px.index):
            continue
        flow.iloc[at] += f.qty * f.price * mults[f.symbol] + f.cost
        units.iloc[at, units.columns.get_loc(f.symbol)] += f.qty
    held = units.cumsum()
    marked = (held * px.fillna(0.0) * pd.Series(mults)).sum(axis=1)
    value = acc.initial_capital - flow.cumsum() + marked
    return value.rename("equity")
