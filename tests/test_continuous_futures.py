"""Continuous futures: causal volume roll, back adjustment, CSV-per-contract provider."""

import numpy as np
import pandas as pd
import pytest

from apex.cli import main as cli
from apex.core.errors import ProviderError
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.continuous import stitch
from apex.marketdata.providers.continuous import ContinuousFuturesProvider
from tests.conftest import utc

DAYS = pd.date_range("2025-01-01", periods=10, freq="D", tz="UTC")


def _c(idx, close, vol):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"ts": DAYS[idx], "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": np.asarray(vol, dtype=float)})


# Old contract days 0-6 at 100 + day; new contract days 3-9 at 110 + day (10 points higher).
OLD = _c(slice(0, 7), 100 + np.arange(7), [900, 900, 900, 800, 400, 300, 100])
NEW = _c(slice(3, 10), 110 + np.arange(3, 10), [100, 500, 800, 900, 900, 900, 900])


def test_rolls_on_volume_cross_and_difference_keeps_point_moves():
    out, rolls = stitch([("ESH5", OLD), ("ESM5", NEW)])
    assert len(rolls) == 1 and rolls[0].old == "ESH5" and rolls[0].new == "ESM5"
    assert rolls[0].ts == DAYS[4] and rolls[0].reason == "volume" and rolls[0].gap == 10.0
    assert list(out["ts"]) == list(DAYS)  # one bar per day, no duplicates
    assert (out["close"].iloc[5:].to_numpy() == 110 + np.arange(5, 10)).all()  # newest real
    assert (out["close"].diff().iloc[1:] == 1.0).all()  # roll gap removed
    assert (out["high"] - out["close"] == 1.0).all()


def test_ratio_keeps_percentage_moves_and_the_roll_is_causal():
    out, rolls = stitch([("ESH5", OLD), ("ESM5", NEW)], method="ratio")
    old_ret = OLD["close"].pct_change().iloc[1:5].to_numpy()
    assert np.allclose(out["close"].pct_change().iloc[1:5].to_numpy(), old_ret)
    assert out["close"].iloc[4] == pytest.approx(NEW["close"].iloc[1])  # day 4 in new terms
    # Changing the data after the roll bar cannot move the roll earlier.
    later = NEW.copy()
    later.loc[later["ts"] > DAYS[4], "volume"] = 0.0
    assert stitch([("ESH5", OLD), ("ESM5", later)])[1][0].ts == DAYS[4]


def test_no_volume_cross_rolls_at_overlap_end_and_no_overlap_fails():
    quiet = NEW.assign(volume=0.0)
    _, rolls = stitch([("ESH5", OLD), ("ESM5", quiet)])
    assert rolls[0].ts == DAYS[6] and rolls[0].reason == "overlap end"
    with pytest.raises(ProviderError, match="never trade at the same time"):
        stitch([("A", _c(slice(0, 3), [1, 2, 3], [1] * 3)),
                ("B", _c(slice(5, 8), [1, 2, 3], [1] * 3))])


def test_three_contracts_accumulate_adjustments():
    third = _c(slice(6, 10), 130 + np.arange(6, 10), [2000] * 4)
    out, rolls = stitch([("H", OLD), ("M", NEW), ("U", third)])
    assert [r.new for r in rolls] == ["M", "U"] and rolls[1].ts == DAYS[6]
    assert out["close"].iloc[-1] == 139 and (out["close"].diff().iloc[1:] == 1.0).all()


def test_provider_orders_files_by_last_bar_and_cli_ingests(tmp_path, db_url, monkeypatch,
                                                          capsys):
    folder = tmp_path / "ES"
    folder.mkdir()
    NEW.assign(ts=NEW["ts"].dt.strftime("%Y-%m-%d")).to_csv(folder / "a_new_1d.csv",
                                                              index=False)
    OLD.to_csv(folder / "z_old_1d.csv", index=False)
    prov = ContinuousFuturesProvider(tmp_path)
    es = by_symbol()["ES"]
    bars = prov.fetch_bars(es, Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1))
    assert len(bars) == 10 and prov.last_rolls[0].old == "z_old"
    assert not prov.supports(by_symbol()["SPY"], Timeframe.D1)
    assert not prov.supports(es, Timeframe.H1)

    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert cli(["seed-instruments"]) == 0
        assert cli(["ingest", "--provider", "csv-cont-ratio", "--csv-root", str(tmp_path),
                    "--symbol", "ES", "--timeframe", "1d", "--start", "2025-01-01",
                    "--end", "2025-02-01"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "stored=10" in out and "rolled z_old -> a_new after 2025-01-05 00:00 UTC" in out
