"""Provider that derives bars from bars already in the database (e.g. 1m -> 1h).

Running it through the normal ingestion pipeline gives resampled data the same validation,
run records and content hashes as vendor data. Source name: ``<source>:rs<source_tf>``.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from apex.domain.market import InstrumentSpec, Timeframe
from apex.marketdata.base import MarketDataProvider
from apex.marketdata.calendars import get_calendar
from apex.marketdata.repository import get_instrument, load_bars
from apex.marketdata.resample import resample_bars


class ResampledProvider(MarketDataProvider):
    def __init__(self, session: Session, source: str, source_tf: Timeframe) -> None:
        self.session, self.source, self.source_tf = session, source, source_tf
        self.name = f"{source}:rs{source_tf.value}"

    def supports(self, spec: InstrumentSpec, timeframe: Timeframe) -> bool:
        return timeframe is not Timeframe.TICK and timeframe.delta > self.source_tf.delta

    def _fetch(self, spec, timeframe, start: datetime, end: datetime) -> pd.DataFrame:  # noqa: ANN001
        inst = get_instrument(self.session, spec.symbol, spec.venue)
        raw = load_bars(self.session, inst.id, self.source_tf, self.source, start, end)
        return resample_bars(raw, self.source_tf, timeframe, get_calendar(spec.calendar))
