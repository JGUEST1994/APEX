"""Dukascopy hand-exported CSV import: day-first times, time zones, weekend filler bars."""

import pytest

from apex.cli import main as cli
from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.providers.dukascopy_csv import DukascopyCsvProvider
from tests.conftest import utc

# Friday 1 March 2024 20:00-21:00 GMT trades, then Saturday 2 March is flat zero-volume filler.
GMT = """Gmt time,Open,High,Low,Close,Volume
01.03.2024 20:00:00.000,1.08100,1.08200,1.08000,1.08150,1520.5
01.03.2024 21:00:00.000,1.08150,1.08250,1.08100,1.08200,980.25
02.03.2024 00:00:00.000,1.08200,1.08200,1.08200,1.08200,0
"""


def _write(tmp_path, text, name="EURUSD_1h.csv"):
    (tmp_path / name).write_text(text)
    return DukascopyCsvProvider(tmp_path)


def test_gmt_export_parses_day_first_and_drops_filler_bars(tmp_path):
    prov = _write(tmp_path, GMT)
    bars = prov.fetch_bars(by_symbol()["EURUSD"], Timeframe.H1, utc(2024, 3, 1), utc(2024, 3, 4))
    assert list(bars["ts"]) == [utc(2024, 3, 1, 20), utc(2024, 3, 1, 21)]
    assert bars["close"].tolist() == [1.0815, 1.082]


def test_local_time_offset_and_eet_summer_time(tmp_path):
    local = GMT.replace("Gmt time", "Local time").replace(".000,", ".000 GMT+0200,")
    bars = _write(tmp_path, local).read(tmp_path / "EURUSD_1h.csv")
    assert bars["ts"].iloc[0] == utc(2024, 3, 1, 18)
    # 1 July is summer time: EET is UTC+3
    eet = "Time (EET),Open,High,Low,Close,Volume\n01.07.2024 10:00:00.000,1,2,0.5,1.5,10\n"
    bars = _write(tmp_path, eet).read(tmp_path / "EURUSD_1h.csv")
    assert bars["ts"].iloc[0] == utc(2024, 7, 1, 7)


def test_wrong_formats_explain_themselves(tmp_path):
    with pytest.raises(ProviderError, match="dd.mm.yyyy"):
        _write(tmp_path, "Gmt time,Open,High,Low,Close,Volume\n2024-03-01 20:00,1,1,1,1,1\n") \
            .read(tmp_path / "EURUSD_1h.csv")
    with pytest.raises(ProviderError, match="GMT"):
        _write(tmp_path, GMT.replace("Gmt time", "Local time")).read(tmp_path / "EURUSD_1h.csv")
    with pytest.raises(ProviderError, match="time column"):
        _write(tmp_path, "Date,Open,High,Low,Close\n1,1,1,1,1\n").read(tmp_path / "EURUSD_1h.csv")


def test_cli_ingests_dukascopy_export(tmp_path, db_url, monkeypatch, capsys):
    _write(tmp_path, GMT)
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert cli(["seed-instruments"]) == 0
        assert cli(["ingest", "--provider", "dukascopy-csv", "--csv-root", str(tmp_path),
                    "--symbol", "EURUSD", "--timeframe", "1h", "--start", "2024-03-01",
                    "--end", "2024-03-04"]) == 0
    finally:
        get_settings.cache_clear()
    assert "stored=2" in capsys.readouterr().out
