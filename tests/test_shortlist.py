"""Plain-English shortlist from the tournament leaderboard."""

import pandas as pd

from apex.shortlist import shortlist, to_markdown


def _row(key, sym, passed, score=0.0, dsr=0.5, reasons=""):
    return {"strategy": key, "symbol": sym, "timeframe": "1d", "passed_gates": passed,
            "score": score, "deflated_sharpe": dsr, "oos_return": 0.2, "oos_max_drawdown": -0.1,
            "oos_trades": 150, "pct_profitable_folds": 0.7, "fail_reasons": reasons}


LUCK = "could be luck: confidence the edge is real 41%, needs 95% (deflated Sharpe)"
FEW = "too few trades on unseen data: 12, needs 100 (OOS trades)"
DROP = "falls too far: worst drop on unseen data -40%, limit -25% (OOS max drawdown)"


def test_passing_results_come_first_by_score_then_nearest_misses():
    df = pd.DataFrame([
        _row("A", "SPY", True, score=0.6, dsr=0.97),
        _row("B", "QQQ", True, score=0.8, dsr=0.99),
        _row("C", "SPY", False, dsr=0.9, reasons=LUCK),
        _row("D", "SPY", False, dsr=0.94, reasons=f"{LUCK}; {FEW}"),
        _row("E", "BTCUSDT", False, dsr=0.2, reasons=LUCK),
    ])
    sl = shortlist(df, top=4)
    assert sl["tested"] == 5 and sl["n_passed"] == 2 and sl["markets"] == 3
    assert [s.split("**")[1] for s in sl["passed"]] == ["B", "A"]  # by score
    # fewest failed tests first, then highest confidence: C (1, 0.9) before E (1, 0.2)
    assert [s.split("**")[1] for s in sl["near_misses"]] == ["C", "E"]
    assert sl["why_failed"] == {"could be luck": 3, "too few trades on unseen data": 1}
    md = to_markdown(sl)
    assert "Passed every test: 2." in md and "confidence the edge is real 99%" in md
    assert "profitable in 70% of unseen stretches" in md and "worst drop -10%" in md
    assert "for interest only, not for practice runs" in md and "not advice" in md


def test_nothing_passing_is_said_plainly():
    df = pd.DataFrame([_row("C", "SPY", False, reasons=f"{LUCK}; {DROP}")])
    md = to_markdown(shortlist(df))
    assert "None passed every test this time" in md
    assert "- could be luck: 1" in md and "- falls too far: 1" in md
    assert "Missed: could be luck" in md


def test_a_result_that_never_traded_is_not_a_near_miss():
    idle = _row("F", "SPY", False, dsr=0.5, reasons=FEW) | {"oos_trades": 0}
    sl = shortlist(pd.DataFrame([idle, _row("C", "QQQ", False, dsr=0.1, reasons=LUCK)]))
    assert [s.split("**")[1] for s in sl["near_misses"]] == ["C"]
    assert "(2 strategies, 2 markets)" in to_markdown(sl)
    assert "(1 strategy, 1 market)" in to_markdown(shortlist(pd.DataFrame([idle])))


def test_empty_leaderboard():
    assert "No tournament results yet" in to_markdown(shortlist(pd.DataFrame()))
