"""Trade review: every practice trade at a glance, across all practice (paper) accounts.

Read-only report rebuilt from the fill ledger (``apex.paper_trades``); it changes nothing in
paper trading or its records. It answers "what's working and what isn't": headline numbers,
profit and win rate by market, strategy, asset class, exit reason, long/short, how long a
trade was held, weekday entered and month closed, the best and worst markets, cumulative
profit over time, and each account's health (today's change, drop from its best, how close it
is to each safety limit, cash against invested).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apex.db.models import Instrument, PaperAccount
from apex.paper_history import equity_history
from apex.paper_trades import _summary, trades
from apex.risk.limits import RiskLimits

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HOLD_BUCKETS = ((1, "1 day or less"), (5, "2 to 5 days"), (20, "6 to 20 days"),
                (60, "21 to 60 days"), (float("inf"), "over 60 days"))
ASSET_LABELS = {"equity": "Stocks and funds", "crypto": "Crypto", "forex": "Currencies",
                "futures": "Futures"}


def _hold(days: float) -> str:
    return next(label for top, label in HOLD_BUCKETS if days <= top)


def _group(rows: list[dict], key: Callable[[dict], str], order: list[str] | None = None,
           ) -> list[dict]:
    g: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        g[key(r)].append(r["pnl"])
    out = []
    for k, pnls in g.items():
        wins = sum(p > 0 for p in pnls)
        out.append({"key": k, "trades": len(pnls), "wins": wins,
                    "losses": sum(p < 0 for p in pnls), "win_rate": wins / len(pnls),
                    "pnl": sum(pnls), "average": sum(pnls) / len(pnls),
                    "best": max(pnls), "worst": min(pnls)})
    if order:
        out.sort(key=lambda x: order.index(x["key"]) if x["key"] in order else len(order))
    else:
        out.sort(key=lambda x: x["pnl"], reverse=True)
    return out


def health(session: Session, acc: PaperAccount) -> dict:
    """Account value now, change since the previous bar, drop from its best, how close it is
    to each safety limit that can stop it, and cash against money in the market."""
    lim = RiskLimits(**(acc.limits or {}))
    eq = equity_history(session, acc.name)
    value = float(eq.iloc[-1]) if len(eq) else acc.cash
    prev = float(eq.iloc[-2]) if len(eq) > 1 else None
    peak = max(acc.peak_equity or 0.0, float(eq.max()) if len(eq) else 0.0, value)
    t = trades(session, acc.name, limit=0)
    invested = sum(abs(r["units"] * (r["last_price"] or 0.0) * _mult(session, r["symbol"]))
                   for r in t["open"])
    drop = 1 - value / peak if peak > 0 else 0.0
    day_loss = (1 - value / acc.day_start_equity
                if acc.day_start_equity and acc.day_start_equity > 0 else 0.0)

    def gauge(label: str, now: float, limit: float, kind: str, note: str) -> dict:
        return {"label": label, "now": now, "limit": limit, "kind": kind,
                "used": max(now, 0.0) / limit if limit else None, "note": note}

    return {
        "account": acc.name, "strategy": acc.strategy_key, "simulated": True,
        "value": value, "initial": acc.initial_capital,
        "total_return": value / acc.initial_capital - 1 if acc.initial_capital else None,
        "day_change": value - prev if prev is not None else None,
        "day_change_pct": value / prev - 1 if prev else None,
        "peak": peak, "drop_from_peak": drop, "last_bar": (acc.last_bar_ts.isoformat()
                                                           if acc.last_bar_ts else None),
        "cash": acc.cash, "invested": invested,
        "invested_share": invested / value if value > 0 else None,
        "open_trades": len(t["open"]), "kill_switch": acc.kill_engaged,
        "kill_reason": acc.kill_reason if acc.kill_engaged else None,
        "limits": [
            gauge("Losing trades in a row", acc.consecutive_losses, lim.max_consecutive_losses,
                  "count", "stops the account when reached; a person must restart it"),
            gauge("Drop from best value", drop, lim.max_drawdown, "pct",
                  "stops the account when reached"),
            gauge("Loss so far today", day_loss, lim.max_daily_loss, "pct",
                  "stops the account when reached"),
            gauge("Open trades", len(t["open"]), lim.max_positions, "count",
                  "new trades are refused when full"),
            gauge("Money in the market", invested / value if value > 0 else 0.0,
                  lim.max_gross_exposure, "pct", "new trades are refused past this"),
        ],
    }


def _mult(session: Session, sym: str) -> float:
    row = session.scalar(select(Instrument).where(Instrument.symbol == sym).limit(1))
    return row.contract_multiplier if row is not None else 1.0


def review(session: Session, account: str | None = None, start: datetime | None = None,
           end: datetime | None = None, limit: int = 500) -> dict:
    """Finished trades closed in ``[start, end)`` (all of them if not given) for one account
    or all of them, grouped every useful way; plus open trades and account health."""
    q = select(PaperAccount).order_by(PaperAccount.name)
    if account:
        q = q.where(PaperAccount.name == account)
    accounts = session.scalars(q).all()
    if account and not accounts:
        raise KeyError(account)
    classes = {i.symbol: i.asset_class for i in session.scalars(select(Instrument))}
    closed, opened = [], []
    for acc in accounts:
        t = trades(session, acc.name, limit=10**9)
        for r in t["closed"] + t["open"]:
            r["account"], r["strategy"] = acc.name, acc.strategy_key
            cls = classes.get(r["symbol"], "")
            r["asset_class"] = ASSET_LABELS.get(cls, cls.title() or "Other")
        closed += t["closed"]
        opened += t["open"]
    lo = start.astimezone(UTC).isoformat() if start else ""
    hi = end.astimezone(UTC).isoformat() if end else "~"
    closed = [r for r in closed if lo <= r["closed"] < hi]
    closed.sort(key=lambda r: r["closed"], reverse=True)

    def month(r: dict) -> str:
        return r["closed"][:7]

    months = sorted({month(r) for r in closed})
    by_market = _group(closed, lambda r: r["symbol"])
    curve, run = [], 0.0
    for r in sorted(closed, key=lambda r: r["closed"]):
        run += r["pnl"]
        curve.append({"ts": r["closed"], "cumulative": run, "pnl": r["pnl"],
                      "symbol": r["symbol"]})
    s = _summary(closed)
    s["profit_per_trade"] = s["total_pnl"] / s["trades"] if s["trades"] else None
    return {
        "simulated": True, "account": account,
        "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
        "accounts": [a.name for a in session.scalars(
            select(PaperAccount).order_by(PaperAccount.name))],
        "summary": s,
        "by_market": by_market,
        "top_wins": [g for g in by_market if g["pnl"] > 0][:5],
        "top_losses": sorted([g for g in by_market if g["pnl"] < 0], key=lambda g: g["pnl"])[:5],
        "by_strategy": _group(closed, lambda r: r["strategy"]),
        "by_asset_class": _group(closed, lambda r: r["asset_class"]),
        "by_exit_reason": _group(closed, lambda r: r["exit_reason_label"]),
        "by_side": _group(closed, lambda r: r["side"], ["long", "short"]),
        "by_hold": _group(closed, lambda r: _hold(r["days_held"]),
                          [label for _, label in HOLD_BUCKETS]),
        "by_weekday": _group(closed, lambda r: WEEKDAYS[datetime.fromisoformat(
            r["opened"]).weekday()], list(WEEKDAYS)),
        "by_month": _group(closed, month, months),
        "curve": curve,
        "open": sorted(opened, key=lambda r: r["pnl"]),
        "trades": closed[:limit], "trades_total": len(closed),
        "health": [health(session, a) for a in accounts],
    }
