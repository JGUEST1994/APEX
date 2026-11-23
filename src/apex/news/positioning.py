"""Who is short and who is positioned: FINRA daily short-sale volume and CFTC Commitments
of Traders (traders in financial futures). Both free and official, no key.

Stored as macro series (``macro_observations``) so the same point-in-time rules apply:

* ``SVR:<SYMBOL>``: short-sale volume as a share of reported volume that day (FINRA
  consolidated file ``CNMSshvol<YYYYMMDD>.txt``, published the same evening). Vintage = the
  trading day, known from the end of that day UTC.
* ``COT:<CODE>:LEV`` / ``:AM``: hedge funds' ("leveraged money") and asset managers' net
  futures position as a share of open interest, for positions as of Tuesday. CFTC publishes
  on Friday, so the vintage is the Tuesday + 3 days.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from apex.core.errors import ProviderError
from apex.news.macro import store_observations

FINRA_DAILY = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt"
COT_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
USER_AGENT = "APEX-research/1.0"
# CFTC contract market codes (Traders in Financial Futures, futures only).
COT_MARKETS = {"13874A": "E-mini S&P 500", "209742": "E-mini Nasdaq-100"}
COT_RELEASE_LAG = timedelta(days=3)  # Tuesday positions, published Friday


def _get(client: httpx.Client, url: str, **params: object) -> httpx.Response:
    try:
        return client.get(url, params=params or None, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise ProviderError(f"{httpx.URL(url).host}: {type(exc).__name__}") from None


def finra_short_volume(client: httpx.Client, day: date,
                       symbols: set[str]) -> dict[str, float] | None:
    """Short share of volume per symbol for one day, or None when FINRA has no file (a
    weekend or holiday)."""
    r = _get(client, FINRA_DAILY.format(d=day))
    if r.status_code in (403, 404):
        return None
    if r.status_code >= 400:
        raise ProviderError(f"FINRA {day}: HTTP {r.status_code}")
    out = {}
    for line in r.text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5 or parts[1] not in symbols:
            continue
        try:
            short, total = float(parts[2]), float(parts[4])
        except ValueError:
            continue
        if total > 0:
            out[parts[1]] = short / total
    return out


def fetch_finra(s: Session, client: httpx.Client, symbols: list[str], *, days: int = 10,
                today: date | None = None, now: datetime | None = None,
                sleep: Callable[[float], None] = _time.sleep) -> dict:
    """The last ``days`` calendar days of FINRA files for ``symbols``."""
    now = now or datetime.now(UTC)
    today = today or now.date()
    want, stored, errors = set(symbols), {sym: 0 for sym in symbols}, []
    for k in range(days, 0, -1):
        day = today - timedelta(days=k)
        if day.weekday() >= 5:
            continue
        try:
            got = finra_short_volume(client, day, want)
        except ProviderError as exc:
            errors.append(str(exc))
            continue
        for sym, v in (got or {}).items():
            stored[sym] += store_observations(s, f"SVR:{sym}",
                                              [{"date": day, "value": v, "vintage": day}], now)
        s.commit()
        sleep(0.5)
    return {"stored": stored, "errors": errors}


def fetch_cot(s: Session, client: httpx.Client, *, since: date | None = None,
              now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = since or date(2010, 1, 1)
    stored, errors = {}, []
    for code, name in COT_MARKETS.items():
        r = _get(client, COT_TFF, **{
            "$where": f"cftc_contract_market_code='{code}' AND "
                      f"report_date_as_yyyy_mm_dd >= '{since.isoformat()}'",
            "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000})
        if r.status_code >= 400:
            errors.append(f"CFTC {name}: HTTP {r.status_code}")
            continue
        lev, am = [], []
        for row in r.json():
            try:
                d = date.fromisoformat(str(row["report_date_as_yyyy_mm_dd"])[:10])
                oi = float(row["open_interest_all"])
                lv = (float(row["lev_money_positions_long"])
                      - float(row["lev_money_positions_short"]))
                av = (float(row["asset_mgr_positions_long"])
                      - float(row["asset_mgr_positions_short"]))
            except (KeyError, TypeError, ValueError):
                continue
            if oi > 0:
                vint = d + COT_RELEASE_LAG
                lev.append({"date": d, "value": lv / oi, "vintage": vint})
                am.append({"date": d, "value": av / oi, "vintage": vint})
        if not lev:
            errors.append(f"CFTC {name}: no rows (contract code {code} may have changed)")
            continue
        stored[f"COT:{code}:LEV"] = store_observations(s, f"COT:{code}:LEV", lev, now)
        stored[f"COT:{code}:AM"] = store_observations(s, f"COT:{code}:AM", am, now)
        s.commit()
    return {"stored": stored, "errors": errors}


def latest_positioning(s: Session) -> dict:
    """Latest short-sale share per symbol (with its 20-day average) and the latest futures
    positioning, newest vintage of each observation."""
    from sqlalchemy import select

    from apex.db.models import MacroObservation

    rows = s.execute(select(MacroObservation.series_id, MacroObservation.obs_date,
                            MacroObservation.value)
                     .where(MacroObservation.series_id.like("SVR:%")
                            | MacroObservation.series_id.like("COT:%"))
                     .order_by(MacroObservation.series_id, MacroObservation.obs_date.desc(),
                               MacroObservation.vintage.desc())).all()
    by: dict[str, dict[date, float]] = {}
    for sid, d, v in rows:
        by.setdefault(sid, {}).setdefault(d, v)
    short, futures = [], []
    for sid, vals in sorted(by.items()):
        dates = sorted(vals, reverse=True)
        if sid.startswith("SVR:"):
            recent = [vals[d] for d in dates[:20]]
            short.append({"symbol": sid[4:], "date": dates[0].isoformat(),
                          "short_share": vals[dates[0]], "avg_20d": sum(recent) / len(recent)})
        else:
            _, code, who = sid.split(":")
            futures.append({"market": COT_MARKETS.get(code, code),
                            "who": "Hedge funds" if who == "LEV" else "Asset managers",
                            "date": dates[0].isoformat(), "net_share": vals[dates[0]],
                            "previous": vals[dates[1]] if len(dates) > 1 else None,
                            "source_url": "https://www.cftc.gov/MarketReports/"
                                          "CommitmentsofTraders/index.htm"})
    return {"short": short, "futures": futures}
