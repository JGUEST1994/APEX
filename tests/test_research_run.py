"""scripts/research-run.sh, run against a stand-in `apex` command (no network, no database)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apex.domain.instruments import by_symbol
from apex.domain.market import AssetClass

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "research-run.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"),
                                reason="needs a Unix shell")

# Stand-in for the apex CLI: downloads fail for markets listed in FAIL (and once, with
# Tiingo's hourly-limit error, for those in LIMIT_ONCE); `tournament` prints what it was given.
FAKE_APEX = """#!/usr/bin/env bash
cmd=$1; shift
case $cmd in
  ingest)
    while [ $# -gt 0 ]; do [ "$1" = --symbol ] && sym=$2; shift; done
    case " $LIMIT_ONCE " in *" $sym "*)
      if [ ! -f "$STATE/$sym" ]; then
        touch "$STATE/$sym"
        echo "run 9: failed received=0 stored=0 error=tiingo request failed: HTTPStatusError 429"
        exit 1
      fi ;;
    esac
    case " $FAIL " in *" $sym "*)
      echo "Traceback (most recent call last): ..." >&2
      echo "run 3: failed received=0 stored=0 error=tiingo: not found"; exit 1 ;;
    esac
    echo "run 1: ok received=10 stored=10 issues=0" ;;
  earnings-dates)
    while [ $# -gt 0 ]; do [ "$1" = --symbol ] && sym=$2; shift; done
    case " $EARN_FAIL " in *" $sym "*)
      echo "SEC EDGAR request failed: HTTPStatusError 403"; exit 1 ;;
    esac
    echo "88 earnings releases for $sym (2003-07-17 to 2026-10-31) saved to events"
    echo "use them with: --strategy VWAP-EARN-001 --param anchors=@events" ;;
  tournament) echo "TOURNAMENT $*" ;;
  shortlist) echo "# APEX shortlist" ;;
esac
"""


def _run(tmp_path, groups, **env):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in (("apex", FAKE_APEX), ("sleep", '#!/usr/bin/env bash\necho "slept $1"\n')):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    (tmp_path / "state").mkdir(exist_ok=True)
    full = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "OUT": str(tmp_path / "out"),
            "STATE": str(tmp_path / "state"), "RESEARCH_GROUPS": groups, "END": "2026-11-01",
            "FAIL": "", "LIMIT_ONCE": "", "EARN_FAIL": "", **env}
    r = subprocess.run(["bash", str(SCRIPT)], env=full, capture_output=True, text=True,
                       timeout=60, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _symbols(out, group):
    line = next(x for x in out.splitlines() if x.startswith(f"== tournament {group} on "))
    return line.split(" on ", 1)[1].split(",")


def test_a_failed_download_drops_only_that_market_and_the_sets_that_need_it(tmp_path):
    out = _run(tmp_path, "stocks-1d", APEX_TIINGO_API_KEY="k", FAIL="NVDA")
    assert "prices FAILED: tiingo NVDA 1d from 2000-01-03: tiingo: not found" in out
    syms = _symbols(out, "stocks-1d")
    assert "NVDA" not in syms and "SPY" in syms and "QQQ/SPY" in syms and "MSFT/AAPL" in syms
    assert not any("NVDA" in s for s in syms)  # the 7-stock basket needs every leg
    assert "--source tiingo --timeframe 1d" in out and "--train-bars 504" in out


def test_daily_stocks_also_rank_the_dow_and_sector_funds(tmp_path):
    out = _run(tmp_path, "stocks-1d", APEX_TIINGO_API_KEY="k")
    funds = ["DIA", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
    for f in funds:
        assert f"prices ok: tiingo {f} 1d from 2000-01-03" in out
    syms = _symbols(out, "stocks-1d")
    assert syms[:17] == ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", *funds]
    assert "SPY/QQQ/IWM/AAPL/MSFT/NVDA/AMZN" in syms  # the basket is unchanged
    universe = by_symbol()
    assert all(universe[f].asset_class is AssetClass.EQUITY for f in funds)


def test_the_earnings_idea_runs_on_each_company_with_its_own_dates(tmp_path):
    out = _run(tmp_path, "stocks-1d", APEX_TIINGO_API_KEY="k", FAIL="NVDA", EARN_FAIL="MSFT",
               APEX_SEC_USER_AGENT="Test Person test@example.com")
    main = next(x for x in out.splitlines() if "--symbols SPY," in x)
    assert "--exclude VWAP-EARN-001" in main  # it cannot trade without its dates
    runs = [x for x in out.splitlines() if x.startswith("TOURNAMENT --strategies VWAP-EARN-001")]
    # companies only (funds have no earnings); NVDA had no prices and MSFT no dates
    assert [x.split("--symbols ")[1].split()[0] for x in runs] == ["AAPL", "AMZN"]
    assert f"--param anchors=@{tmp_path / 'out'}/events/AAPL_earnings.json" in runs[0]
    assert "--source tiingo --timeframe 1d" in runs[0] and "--train-bars 504" in runs[0]
    assert "88 earnings releases for AAPL (2003-07-17 to 2026-10-31)" in out
    assert "earnings dates FAILED: MSFT: SEC EDGAR request failed: HTTPStatusError 403" in out
    assert "test@example.com" not in out  # the contact is never printed


def test_without_an_sec_contact_the_earnings_idea_is_left_out_and_says_why(tmp_path):
    out = _run(tmp_path, "stocks-1d", APEX_TIINGO_API_KEY="k")
    assert "earnings idea (VWAP-EARN-001) skipped: set APEX_SEC_USER_AGENT" in out
    assert "--exclude VWAP-EARN-001" in out and "--strategies VWAP-EARN-001" not in out


def test_daily_stocks_fall_back_to_alpaca_and_groups_can_be_picked(tmp_path):
    out = _run(tmp_path, "stocks-1d crypto-1d", APEX_ALPACA_KEY_ID="a")
    assert "--source alpaca --timeframe 1d" in out and "tiingo" not in out
    assert _symbols(out, "crypto-1d") == ["BTCUSDT", "ETHUSDT", "ETHUSDT/BTCUSDT"]
    assert "tournament crypto-1h" not in out and "fx-1h" not in out  # not picked
    assert "unknown group" not in out
    assert "unknown group stocks-long (ignored)" in _run(tmp_path, "stocks-long")


def test_forex_waits_out_the_hourly_limit_once_then_runs_every_pair(tmp_path):
    out = _run(tmp_path, "fx-1h", APEX_TIINGO_API_KEY="k", LIMIT_ONCE="USDCAD")
    assert "waiting an hour for the free plan's hourly request limit" in out
    assert "slept 3660" in out and "prices ok: tiingo USDCAD 1h" in out
    assert _symbols(out, "fx-1h") == ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
                                      "GBPUSD/EURUSD"]


def test_thirty_minute_forex_tests_the_ideas_built_for_it(tmp_path):
    out = _run(tmp_path, "fx-30m", APEX_TIINGO_API_KEY="k")
    assert "prices ok: tiingo EURUSD 30m from 2022-06-01" in out
    assert _symbols(out, "fx-30m") == ["EURUSD", "GBPUSD"]
    assert "--source tiingo --timeframe 30m" in out and "--train-bars 8000" in out
    assert "tournament fx-1h" not in out
    assert "fx-30m skipped: no Tiingo key" in _run(tmp_path, "fx-30m")


def test_nothing_downloaded_skips_the_tournament_and_missing_keys_skip_groups(tmp_path):
    out = _run(tmp_path, "crypto-5m fx-1h stocks-15m", FAIL="BTCUSDT ETHUSDT")
    assert "tournament crypto-5m skipped: no prices" in out and "TOURNAMENT" not in out
    assert "fx-1h skipped: no Tiingo key" in out and "stocks-15m skipped: no Alpaca keys" in out
    assert "slept" not in out  # an ordinary failure is not retried
