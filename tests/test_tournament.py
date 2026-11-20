import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from apex.api.app import create_app
from apex.backtest.experiments import ensure_strategy_version
from apex.backtest.tournament import (
    Gates,
    evaluate,
    family_trials,
    leaderboard,
    run_tournament,
    score,
)
from apex.cli import main as cli
from apex.core.config import Settings
from apex.db.models import AuditEvent, StrategyStatusChange
from apex.db.session import make_engine, make_session_factory
from apex.domain.instruments import by_symbol
from apex.domain.market import Timeframe
from apex.marketdata.base import MarketDataProvider
from apex.marketdata.calendars import get_calendar
from apex.marketdata.ingest import ingest_bars
from apex.marketdata.providers.synthetic import SyntheticProvider
from apex.strategies.base import StrategyStatus as S
from apex.strategies.lifecycle import IllegalTransition, transition
from apex.strategies.registry import all_strategies
from tests.conftest import utc

SMA = all_strategies()["TF-SMA-001"]
BB = all_strategies()["MR-BB-001"]


def test_transitions_are_audited_and_guarded(session):
    ver = ensure_strategy_version(session, SMA())
    with pytest.raises(IllegalTransition):
        transition(session, ver, S.APPROVED, reason="skip")
    for st in (S.BACKTESTING, S.VALIDATION, S.PAPER_TRADING):
        transition(session, ver, st, reason="test")
    with pytest.raises(IllegalTransition, match="human"):
        transition(session, ver, S.APPROVED, reason="auto")
    transition(session, ver, S.APPROVED, reason="reviewed", actor="jake")
    session.commit()
    changes = session.scalars(select(StrategyStatusChange)).all()
    assert [c.to_status for c in changes][-1] == "approved" and changes[-1].actor == "jake"
    assert session.scalars(select(AuditEvent)).first().action == "strategy_status"


def _m(**kw):
    base = {"n_trials": 4, "deflated_sharpe": 0.99, "pct_profitable_folds": 0.8,
            "oos_max_drawdown": -0.1, "pbo": 0.1, "param_stability": 1.0,
            "folds_detail": [{"oos": {"trades": 60}}, {"oos": {"trades": 60}}],
            "dsr_inputs": {"sharpe_per_period": 0.15, "n_obs": 1000, "skew": 0.0,
                           "kurtosis": 3.0, "sr_variance": 0.002}}
    base.update(kw)
    return base


def test_gates():
    ok, reasons, m = evaluate(_m(), Gates(), 4)
    assert ok and not reasons and m["oos_trades"] == 120
    ok, reasons, _ = evaluate(_m(deflated_sharpe=0.5, pbo=0.7, oos_max_drawdown=-0.4,
                                 folds_detail=[]), Gates(), 4)
    assert not ok and len(reasons) == 4
    text = " | ".join(reasons)
    assert "too few trades on unseen data: 0, needs 100" in text
    assert "falls too far: worst drop on unseen data -40%, limit -25%" in text
    assert "likely overfitted: 70% chance" in text and "limit 50% (PBO)" in text
    assert "(deflated Sharpe)" in text and "needs 95%" in text


def test_gate_reasons_when_figures_are_missing():
    ok, reasons, _ = evaluate(_m(deflated_sharpe=None, dsr_inputs=None,
                                 pct_profitable_folds=None, oos_max_drawdown=None),
                              Gates(), 4)
    assert not ok and sum("not measurable" in r for r in reasons) == 3


def test_family_trials_deflate():
    _, _, few = evaluate(_m(), Gates(), 4)
    _, _, many = evaluate(_m(), Gates(), 5000)
    assert many["deflated_sharpe_family"] < few["deflated_sharpe_family"]


def test_score_ignores_raw_return():
    a = _m(oos_total_return=0.05)
    b = _m(oos_total_return=5.0)
    assert score(evaluate(a, Gates(), 4)[2]) == score(evaluate(b, Gates(), 4)[2])


class Trending(MarketDataProvider):
    """Smooth cycles: trend-following works almost every fold (to exercise promotion)."""

    name = "trend"

    def supports(self, spec, timeframe):
        return True

    def _fetch(self, spec, timeframe, start, end):
        ts = list(get_calendar(spec.calendar).expected_bars(start, end, timeframe))
        t = np.arange(len(ts))
        close = 100 * np.exp(0.15 * np.sin(t / 40) + 0.0002 * t)
        rng = np.random.default_rng(0)
        close = close * (1 + rng.normal(0, 0.001, len(t)))
        open_ = np.concatenate([[close[0]], close[:-1]])
        return pd.DataFrame({"ts": ts, "open": open_, "high": np.maximum(open_, close) * 1.001,
                             "low": np.minimum(open_, close) * 0.999, "close": close,
                             "volume": 1.0})


def test_tournament_promotes_only_to_validation(session):
    btc = by_symbol()["BTCUSDT"]
    ingest_bars(session, Trending(), btc, Timeframe.D1, utc(2015, 1, 1), utc(2025, 1, 1))
    ingest_bars(session, SyntheticProvider(), btc, Timeframe.D1, utc(2015, 1, 1), utc(2025, 1, 1))
    session.commit()
    lenient = Gates(min_oos_trades=10, min_dsr=0.5, min_profitable_folds=0.5, max_drawdown=0.5,
                    max_pbo=1.0)
    entries = run_tournament(session, [SMA()], symbols=["BTCUSDT"], timeframe=Timeframe.D1,
                             source="trend", start=utc(2015, 1, 1), end=utc(2025, 1, 1),
                             grids={"TF-SMA-001": {"fast": [5, 10], "slow": [30, 60]}},
                             train_bars=1000, test_bars=365, gates=lenient)
    session.commit()
    assert entries[0].passed, entries[0].reasons
    ver = ensure_strategy_version(session, SMA())
    assert ver.status == "validation"  # never beyond, automatically

    # random walk data: strict default gates must fail, strategy stays in backtesting
    noise = run_tournament(session, [BB()], symbols=["BTCUSDT"], timeframe=Timeframe.D1,
                           source="synthetic", start=utc(2015, 1, 1), end=utc(2025, 1, 1),
                           train_bars=1000, test_bars=365)
    session.commit()
    assert not noise[0].passed and noise[0].reasons
    assert ensure_strategy_version(session, BB()).status == "backtesting"
    n_folds = entries[0].metrics["folds"]
    assert family_trials(session, "TF-SMA-001") == 4 * n_folds == entries[0].metrics["n_trials"]

    lb = leaderboard(session)
    assert set(lb["strategy"]) == {"TF-SMA-001", "MR-BB-001"}
    assert lb["simulated"].all()
    assert len(leaderboard(session, category="mean_reversion")) == 1
    assert leaderboard(session, asset_class="forex").empty


def test_cli_and_api(db_url, monkeypatch, capsys):
    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert cli(["leaderboard"]) == 0
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
                    "--start", "2018-01-01", "--end", "2025-01-01"]) == 0
        assert cli(["tournament", "--strategies", "TF-SMA-001,MOM-TSMOM-001", "--symbols", "SPY",
                    "--source", "synthetic", "--start", "2018-01-01", "--end", "2025-01-01",
                    "--train-bars", "500", "--test-bars", "250"]) == 0
        assert cli(["tournament", "--strategies", "NOPE", "--symbols", "SPY", "--source", "x",
                    "--start", "2018-01-01", "--end", "2025-01-01", "--train-bars", "1",
                    "--test-bars", "1"]) == 2
        # crypto-only strategy on a stock: skipped, so no extra leaderboard row
        assert cli(["tournament", "--strategies", "OF-DELTA-001", "--symbols", "SPY",
                    "--source", "synthetic", "--start", "2018-01-01", "--end", "2025-01-01",
                    "--train-bars", "500", "--test-bars", "250", "--only-suited"]) == 0
        assert cli(["leaderboard", "--symbol", "SPY"]) == 0
        assert cli(["shortlist", "--top", "3"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "no walk-forward results yet" in out and "TOURNAMENT" in out
    assert "# APEX shortlist" in out and "Tested 2 strategy x market x timeframe" in out
    engine = make_engine(db_url)
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url),
                                   make_session_factory(engine)))
    rows = client.get("/leaderboard").json()
    assert len(rows) == 2 and all(r["simulated"] for r in rows)
    assert all("pct_profitable_folds" in r for r in rows)
    engine.dispose()


def test_regime_filtered_variants_share_trial_family(session):
    btc = by_symbol()["BTCUSDT"]
    ingest_bars(session, SyntheticProvider(), btc, Timeframe.D1, utc(2018, 1, 1),
                utc(2024, 1, 1))
    session.commit()
    reg = all_strategies()
    strats = [reg["BO-DONCH-001"](), reg["LIQ-SWEEP-001"]()]  # trending-only vs "any"
    entries = run_tournament(session, strats, symbols=["BTCUSDT"], timeframe=Timeframe.D1,
                             source="synthetic", start=utc(2018, 1, 1), end=utc(2024, 1, 1),
                             train_bars=700, test_bars=250, regime_filter=True,
                             promote=False)
    session.commit()
    keys = {e.strategy_key for e in entries}
    assert keys == {"BO-DONCH-001", "BO-DONCH-001+RF", "LIQ-SWEEP-001"}
    by_key = {e.strategy_key: e for e in entries}
    total = family_trials(session, "BO-DONCH-001") + family_trials(session, "BO-DONCH-001+RF")
    assert by_key["BO-DONCH-001+RF"].family_trials == total
    assert family_trials(session, "BO-DONCH-001+RF") > 0


def test_param_stability_handles_list_settings():
    from apex.backtest.validation import _param_stability

    folds = [{"anchors": ["2024-01-05"], "atr_n": 14}, {"anchors": ["2024-01-05"], "atr_n": 14},
             {"anchors": [], "atr_n": 14}]
    assert _param_stability(folds) == pytest.approx(2 / 3)


def test_only_suited_skips_markets_and_timeframes_a_strategy_is_not_for():
    from apex.backtest.tournament import suits
    from apex.strategies.multi import all_multi_strategies
    from apex.strategies.registry import all_strategies

    reg = all_strategies()
    assert suits(reg["TF-SMA-001"](), "SPY", Timeframe.D1)
    assert not suits(reg["OF-DELTA-001"](), "SPY", Timeframe.M5)  # crypto only
    assert suits(reg["OF-DELTA-001"](), "BTCUSDT", Timeframe.M5)
    assert not suits(reg["OF-DELTA-001"](), "BTCUSDT", Timeframe.D1)  # intraday only
    pair = all_multi_strategies()["STAT-PAIR-001"]()
    assert not suits(pair, "QQQ/SPY/IWM", pair.meta.timeframes[0])  # exactly two legs
