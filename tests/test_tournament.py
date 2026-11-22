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
from apex.backtest.validation import walk_forward
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
    ok, reasons, _ = evaluate(_m(deflated_sharpe=0.5, dsr_inputs=None, pbo=0.7,
                                 oos_max_drawdown=-0.4, folds_detail=[]), Gates(), 4)
    assert not ok and len(reasons) == 4
    text = " | ".join(reasons)
    assert "too few trades on unseen data: 0, needs 100" in text
    assert "falls too far: worst drop on unseen data -40%, limit -25%" in text
    assert "likely overfitted: 70% chance" in text and "limit 50% (PBO)" in text
    assert "(deflated Sharpe)" in text and "needs 95%" in text


def test_a_near_miss_is_never_rounded_onto_the_limit():
    _, reasons, _ = evaluate(_m(deflated_sharpe=0.9476, dsr_inputs=None,
                                oos_max_drawdown=-0.2531, pct_profitable_folds=0.597),
                             Gates(), 4)
    text = " | ".join(reasons)
    assert "confidence the edge is real 94.8%, needs 95%" in text
    assert "worst drop on unseen data -25.3%, limit -25%" in text
    assert "profitable in 59.7% of unseen stretches, needs 60%" in text


def test_gate_reasons_when_figures_are_missing():
    ok, reasons, _ = evaluate(_m(deflated_sharpe=None, dsr_inputs=None,
                                 pct_profitable_folds=None, oos_max_drawdown=None),
                              Gates(), 4)
    assert not ok and sum("not measurable" in r for r in reasons) == 3


def test_family_trials_deflate():
    _, _, few = evaluate(_m(), Gates(), 4)
    _, _, many = evaluate(_m(), Gates(), 5000)
    assert many["deflated_sharpe_family"] < few["deflated_sharpe_family"]


def test_a_try_is_a_setting_on_a_market_not_a_stretch():
    from apex.backtest.tournament import tries

    assert tries({"n_trials": 1, "folds": 47, "trials_per": "setting"}) == 1
    assert tries({"n_trials": 4, "folds": 10, "trials_per": "setting"}) == 4
    # recorded before the change: every setting on every stretch was counted
    assert tries({"n_trials": 47, "folds": 47}) == 1
    assert tries({"n_trials": 40, "folds": 10}) == 4
    assert tries({}) == 0
    # so the same result reads the same, whichever rule recorded it
    old = evaluate(_m(n_trials=4 * 30, folds=30), Gates(), 0)[2]
    new = evaluate(_m(n_trials=4, folds=30, trials_per="setting"), Gates(), 0)[2]
    assert old["deflated_sharpe_family"] == new["deflated_sharpe_family"]


def test_luck_test_judges_the_whole_unseen_history():
    inp = _m()["dsr_inputs"] | {"sharpe_per_period": 0.06}  # about 0.95 a year, daily
    short = evaluate(_m(dsr_inputs=inp | {"n_obs": 1000}), Gates(), 300)[2]
    long_ = evaluate(_m(dsr_inputs=inp | {"n_obs": 8000}), Gates(), 300)[2]
    assert long_["deflated_sharpe_family"] > 0.95 > short["deflated_sharpe_family"]
    # how noisy the short training stretches were no longer sets the bar
    noisy = evaluate(_m(dsr_inputs=inp | {"n_obs": 8000, "sr_variance": 0.5}), Gates(), 300)[2]
    assert noisy["deflated_sharpe_family"] == long_["deflated_sharpe_family"]


def _bounce_bars(n: int, bounce: float, drift: float = 0.0002, seed: int = 1) -> pd.DataFrame:
    """Daily bars where, when ``bounce`` > 0, a close near the day's low is followed by a
    rise of that size (a real edge for MR-IBS-001); otherwise a plain random walk."""
    rng = np.random.default_rng(seed)
    c, prev_ibs, rows = 100.0, 0.5, []
    for t in pd.bdate_range("2000-01-03", periods=n, tz="UTC"):
        r = drift + (bounce if prev_ibs < 0.2 else 0.0) + 0.012 * rng.standard_normal()
        o, cl = c, c * (1 + r)
        h = max(o, cl) * (1 + abs(rng.normal(0, 0.004)))
        lo = min(o, cl) * (1 - abs(rng.normal(0, 0.004)))
        rows.append((t, o, h, lo, cl, 1e6))
        prev_ibs, c = (cl - lo) / (h - lo), cl
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def _family_confidence(bars: pd.DataFrame) -> float:
    wf = walk_forward(bars, all_strategies()["MR-IBS-001"](), by_symbol()["SPY"], {},
                      train_bars=504, test_bars=126)
    # judged as the tournament would if the idea had been tried on 7 markets
    return evaluate(wf.summary, Gates(), 7 * wf.n_trials)[2]["deflated_sharpe_family"]


def test_more_history_makes_a_real_edge_easier_to_confirm():
    edge = _bounce_bars(20 * 252, bounce=0.002)
    six, twenty = _family_confidence(edge.iloc[:6 * 252]), _family_confidence(edge)
    assert twenty > six and twenty >= 0.95
    # and the same long history without the edge still reads as luck
    assert _family_confidence(_bounce_bars(20 * 252, bounce=0.0, drift=0.0)) < 0.5


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
    assert family_trials(session, "TF-SMA-001") == 4 == entries[0].metrics["n_trials"]

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
    assert all(r["buy_hold_return"] is not None for r in rows)  # the dashboard's Just holding
    engine.dispose()


def test_cli_tournament_leaves_out_strategies_and_sets_their_settings(db_url, monkeypatch,
                                                                     capsys, tmp_path):
    import json

    from apex.core.config import get_settings

    monkeypatch.setenv("APEX_DATABASE_URL", db_url)
    get_settings.cache_clear()
    dates = tmp_path / "SPY_earnings.json"
    dates.write_text(json.dumps([f"{y}-{m:02d}-05" for y in range(2018, 2025)
                                 for m in (1, 4, 7, 10)]))
    run = ["tournament", "--symbols", "SPY", "--source", "synthetic", "--start", "2018-01-01",
           "--end", "2025-01-01", "--train-bars", "500", "--test-bars", "250"]
    try:
        assert cli(["ingest", "--provider", "synthetic", "--symbol", "SPY", "--timeframe", "1d",
                    "--start", "2018-01-01", "--end", "2025-01-01"]) == 0
        capsys.readouterr()
        assert cli([*run, "--strategies", "TF-SMA-001,VWAP-EARN-001", "--exclude", "TF-SMA-001",
                    "--param", f"anchors=@{dates}"]) == 0
        out = capsys.readouterr().out
        assert "VWAP-EARN-001" in out and "TF-SMA-001" not in out
        # a setting no chosen strategy has is a typo, not something to ignore
        assert cli([*run, "--strategies", "TF-SMA-001", "--param", "anchors=[]"]) == 2
        assert "none of the chosen strategies has that setting" in capsys.readouterr().err
        assert cli([*run, "--exclude", "NOPE"]) == 2
    finally:
        get_settings.cache_clear()
    engine = make_engine(db_url)
    with make_session_factory(engine)() as s:
        lb = leaderboard(s, symbol="SPY")
    engine.dispose()
    assert list(lb["strategy"]) == ["VWAP-EARN-001"]
    assert lb["oos_trades"].iloc[0] > 0  # the dates reached it: without them it never trades


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


def test_every_market_is_judged_against_all_of_the_familys_tries(session):
    """Running order must not matter: the first market faces as many tries as the last,
    so the tournament's verdicts match the leaderboard's."""
    spy, qqq = by_symbol()["SPY"], by_symbol()["QQQ"]
    for spec in (spy, qqq):
        ingest_bars(session, SyntheticProvider(), spec, Timeframe.D1, utc(2016, 1, 1),
                    utc(2024, 1, 1))
    session.commit()
    entries = run_tournament(session, [all_strategies()["MR-IBS-001"]()],
                             symbols=["SPY", "QQQ"], timeframe=Timeframe.D1,
                             source="synthetic", start=utc(2016, 1, 1), end=utc(2024, 1, 1),
                             train_bars=504, test_bars=252, promote=False)
    session.commit()
    total = family_trials(session, "MR-IBS-001")
    assert len(entries) == 2 and {e.family_trials for e in entries} == {total}
    assert total == sum(e.metrics["n_trials"] for e in entries)
    lb = leaderboard(session).set_index("symbol")
    for e in entries:
        assert lb.loc[e.symbol, "family_trials"] == e.family_trials
        assert lb.loc[e.symbol, "deflated_sharpe"] == e.metrics["deflated_sharpe_family"]
        assert bool(lb.loc[e.symbol, "passed_gates"]) == e.passed
