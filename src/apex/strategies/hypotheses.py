"""The strategy hypothesis bank (spec Section 6: at least 50 hypotheses).

Each entry is a testable claim, not a recommendation. ``implemented`` names the strategy
key in the registry when code exists. ``data`` says what the hypothesis needs: "ohlcv"
works with today's data layer; "ticks", "l2" (order book) or "multi" (several instruments)
need data APEX does not collect yet.

Fields per hypothesis: id, name, category, markets, timeframes, idea, entry, exit, stop,
take_profit, regime, data, implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("scalping", "momentum", "mean_reversion", "trend_following", "breakout", "vwap",
              "volume_profile", "order_flow", "liquidity_sweep", "market_structure",
              "statistical_arbitrage", "volatility_expansion", "multi_timeframe",
              "machine_learning")


@dataclass(frozen=True)
class Hypothesis:
    id: str
    name: str
    category: str
    markets: str  # comma list of forex, equity, crypto, futures
    timeframes: str
    idea: str
    entry: str
    exit: str
    stop: str
    take_profit: str
    regime: str
    data: str = "ohlcv"
    implemented: str | None = None


H = Hypothesis
HYPOTHESES: list[Hypothesis] = [
    # --- trend following
    H("H01", "SMA crossover", "trend_following", "forex,equity,crypto,futures", "1h,1d",
      "Persistent trends are captured by moving-average crossovers.",
      "fast SMA crosses above slow SMA", "opposite cross", "none / ATR stop", "none",
      "trending",
      implemented="TF-SMA-001"),
    H("H02", "Donchian breakout", "trend_following", "forex,equity,crypto,futures", "1h,1d",
      "Breaking an N-bar extreme marks the start of a trend.",
      "close > prior 20-bar high", "close < prior 10-bar low", "10-bar channel", "none",
      "trending",
      implemented="BO-DONCH-001"),
    H("H03", "EMA ribbon alignment", "trend_following", "forex,crypto,futures", "1h,4h",
      "Ordered EMAs (8>21>55) identify strong trends.", "all EMAs ordered and price above 8 EMA",
      "ordering breaks", "below 55 EMA", "none", "trending",
      implemented="TF-EMA-RIB-001"),
    H("H04", "ATR trailing trend", "trend_following", "equity,futures,crypto", "1d",
      "Chandelier exits ride trends while capping giveback.", "close > 50-day high",
      "close < highest close - 3 ATR", "3 ATR trail", "none", "trending",
      implemented="TF-CHAND-001"),
    H("H05", "Cross-asset trend portfolio", "trend_following", "futures,forex", "1d",
      "Diversified trend following across uncorrelated markets improves risk-adjusted returns.",
      "12-month return > 0 per market", "monthly rebalance", "volatility-scaled sizing", "none",
      "any", data="multi", implemented="TF-XASSET-001"),
    # --- momentum
    H("H06", "Time-series momentum", "momentum", "futures,forex,crypto", "1d",
      "Past 1-12 month returns predict next-month returns in the same asset.",
      "sign of 60-bar return", "sign flip", "vol targeting", "none", "trending",
      implemented="MOM-TSMOM-001"),
    H("H07", "Cross-sectional momentum", "momentum", "equity,crypto", "1d",
      "Recent winners keep outperforming losers over 3-12 months.",
      "long top decile of 12-1 month return", "monthly rebalance", "portfolio-level DD stop",
      "none", "any", data="multi"),
    H("H08", "Intraday momentum (first half hour)", "momentum", "equity,futures", "30m",
      "The first 30-minute return predicts the last 30-minute return (Gao et al.).",
      "sign of first-30m return at 15:30 ET", "close", "none", "close", "high volatility days"),
    H("H09", "52-week high proximity", "momentum", "equity", "1d",
      "Stocks near 52-week highs under-react to good news.", "close within 2% of 52w high",
      "close 10% below entry high", "10% trail", "none", "trending", data="multi"),
    H("H10", "Crypto weekend momentum", "momentum", "crypto", "1h",
      "Thin weekend liquidity extends moves started on Friday.", "Friday 20:00 UTC 24h return sign",
      "Monday 00:00 UTC", "2 ATR", "time exit", "any"),
    # --- mean reversion
    H("H11", "RSI(2) pullback", "mean_reversion", "equity,futures", "1d",
      "Short-term oversold dips in uptrends revert.", "RSI2 < 10 and close > SMA200",
      "close > SMA5", "trend filter fails", "SMA5", "trending/range",
      implemented="MR-RSI2-001"),
    H("H12", "Bollinger band fade", "mean_reversion", "forex,crypto,equity", "15m,1h",
      "Closes beyond 2 sigma revert to the mean in ranges.", "close outside 2-sigma band",
      "middle band", "3.5 sigma", "middle band", "range",
      implemented="MR-BB-001"),
    H("H13", "Internal bar strength", "mean_reversion", "equity", "1d",
      "Closing near the day's low predicts next-day bounce in index ETFs.",
      "(close-low)/(high-low) < 0.2", "next close > prior high", "5 days", "time", "range",
      implemented="MR-IBS-001"),
    H("H14", "Consecutive down days", "mean_reversion", "equity,futures", "1d",
      "Three or more down closes in an uptrend tend to reverse.",
      "3 lower closes and close > SMA200", "first up close", "5 bars", "first up close",
      "trending",
      implemented="MR-DOWN-001"),
    H("H15", "FX overnight reversion", "mean_reversion", "forex", "1h",
      "Asia-session extensions revert during the London open.",
      "Asia range break > 1.5x average", "London 10:00", "2x Asia range", "Asia midpoint",
      "range"),
    # --- breakout
    H("H16", "Opening range breakout", "breakout", "equity,futures", "5m,15m",
      "The first 30 minutes' range break sets the day's direction.",
      "close beyond opening range", "session close", "opposite side of range", "session close",
      "trend days",
      implemented="BO-ORB-001"),
    H("H17", "Inside-day breakout", "breakout", "equity,futures,forex", "1d",
      "Compression inside the prior day's range precedes expansion.",
      "break of inside bar high/low", "2 days", "other side of inside bar", "2R", "any",
      implemented="BO-INSIDE-001"),
    H("H18", "London breakout", "breakout", "forex", "15m",
      "London open breaks the Asian range with follow-through.",
      "break of 00:00-07:00 UTC range", "16:00 UTC", "range midpoint", "1.5x range", "any",
      implemented="BO-LDN-001"),
    H("H19", "NR7 breakout", "breakout", "equity,futures,crypto", "1d",
      "The narrowest range in 7 days precedes range expansion.",
      "break of NR7 bar", "3 days", "other side", "2R", "any",
      implemented="BO-NR7-001"),
    H("H20", "Gap-and-go", "breakout", "equity", "5m",
      "Large opening gaps on volume continue in the gap direction.",
      "gap > 2% and first 5m bar breaks in gap direction", "session close",
      "first bar low/high", "2R", "news days", data="multi"),
    # --- VWAP
    H("H21", "VWAP deviation reversion", "vwap", "equity,futures", "1m,5m",
      "Extreme deviations from session VWAP revert intraday.", "z(close-VWAP) < -2",
      "touch VWAP", "z < -4", "VWAP", "range",
      implemented="VWAP-MR-001"),
    H("H22", "VWAP trend pullback", "vwap", "equity,futures", "5m",
      "On trend days, pullbacks to VWAP are bought.",
      "price above VWAP all morning, touches VWAP", "session close", "close below VWAP - 1 ATR",
      "prior high", "trending",
      implemented="VWAP-PB-001"),
    H("H23", "Anchored VWAP from earnings", "vwap", "equity", "1d",
      "AVWAP from the last earnings date acts as support/resistance.",
      "reclaim of AVWAP", "close below AVWAP", "AVWAP - 1 ATR", "none", "any", data="multi"),
    H("H24", "VWAP band breakout", "vwap", "futures,crypto", "5m",
      "Holding outside the 2-sigma VWAP band signals a trend day.",
      "two closes beyond 2-sigma band", "close back inside 1-sigma", "VWAP", "none", "trending",
      implemented="VWAP-BAND-001"),
    # --- volume profile
    H("H25", "Value area 80% rule", "volume_profile", "futures", "30m",
      "Opening inside yesterday's value area after outside tends to traverse it.",
      "two 30m closes inside prior value area", "opposite value area edge", "outside value area",
      "other edge", "range", data="ticks"),
    H("H26", "Point of control magnet", "volume_profile", "futures,equity", "5m",
      "Price reverts to the prior session's point of control.",
      "price > 1 ATR from POC with declining volume", "touch POC", "1 ATR further", "POC",
      "range", data="ticks"),
    H("H27", "Low-volume node rejection", "volume_profile", "futures", "5m",
      "Low-volume nodes act as fast-move zones; rejection at high-volume nodes.",
      "rejection candle at HVN", "next LVN", "beyond HVN", "next HVN", "any", data="ticks"),
    # --- order flow
    H("H28", "Cumulative delta divergence", "order_flow", "futures,crypto", "1m,5m",
      "Price highs without delta confirmation fail.", "new high with lower cumulative delta",
      "prior swing low", "new high + 1 tick", "1.5R", "range", data="ticks"),
    H("H29", "Order book imbalance", "order_flow", "crypto,futures", "tick",
      "Top-of-book imbalance predicts the next price move.", "bid/ask size ratio > 3",
      "imbalance reverts", "2 ticks", "2 ticks", "any", data="l2"),
    H("H30", "Absorption at extremes", "order_flow", "futures", "1m",
      "Large passive volume at a level without price progress marks a reversal.",
      "volume > 3x average with < 1 tick progress", "10 minutes", "level +2 ticks", "2R", "range",
      data="ticks"),
    # --- liquidity sweep
    H("H31", "Prior-low sweep reversal", "liquidity_sweep", "forex,crypto,futures,equity",
      "15m,1h", "Stops below obvious lows are swept then price reverses.",
      "low < prior 20-bar low and close back above", "5 bars", "sweep low", "time",
      "range",
      implemented="LIQ-SWEEP-001"),
    H("H32", "Asian range sweep (FX)", "liquidity_sweep", "forex", "15m",
      "London often sweeps one side of the Asian range then reverses.",
      "sweep of Asia high/low then close inside", "16:00 UTC", "sweep extreme",
      "opposite Asia edge", "any"),
    H("H33", "Previous day high/low sweep", "liquidity_sweep", "futures,equity", "5m",
      "Prior-day extremes attract liquidity grabs that fail.",
      "trade through PDH/PDL and close back", "session close", "sweep extreme", "VWAP", "range",
      implemented="LIQ-PDHL-001"),
    H("H34", "Equal highs raid", "liquidity_sweep", "crypto,forex", "1h",
      "Clusters of equal highs/lows are targeted then reversed.",
      "two highs within 0.1 ATR swept and rejected", "6 bars", "sweep extreme", "2R", "any"),
    # --- market structure
    H("H35", "Break of structure with higher low", "market_structure",
      "forex,crypto,futures,equity", "15m,1h", "HL then break of the last swing high starts a leg.",
      "close > last swing high with HL", "close < last swing low", "last swing low", "none",
      "trending",
      implemented="MS-BOS-001"),
    H("H36", "Change of character reversal", "market_structure", "forex,crypto", "15m",
      "The first break against the prevailing swing structure marks reversals.",
      "first lower low after series of HH/HL", "new swing high", "last swing high", "2R", "any"),
    H("H37", "Fair value gap fill", "market_structure", "forex,futures,crypto", "5m,15m",
      "Three-bar imbalances (gaps between bar 1 high and bar 3 low) get revisited.",
      "price returns into FVG in trend direction", "FVG far edge broken", "beyond FVG", "2R",
      "trending",
      implemented="MS-FVG-001"),
    # --- statistical arbitrage
    H("H38", "Cointegrated pairs", "statistical_arbitrage", "equity", "1d",
      "Spreads of cointegrated stocks revert.", "spread z < -2", "z > 0", "z < -4",
      "z = 0", "any", data="multi", implemented="STAT-PAIR-001"),
    H("H39", "ES/NQ ratio reversion", "statistical_arbitrage", "futures", "15m",
      "The ES/NQ ratio mean-reverts intraday.", "ratio z beyond 2", "z = 0", "z beyond 4",
      "z = 0", "range", data="multi"),
    H("H40", "BTC/ETH spread", "statistical_arbitrage", "crypto", "1h",
      "ETH/BTC deviations from a rolling hedge ratio revert.", "spread z beyond 2", "z = 0",
      "z beyond 4", "z = 0", "any", data="multi", implemented="STAT-PAIR-001"),
    H("H41", "ETF vs basket", "statistical_arbitrage", "equity", "5m",
      "Sector ETF deviations from its top holdings revert.", "z beyond 2.5", "z = 0",
      "time 1 day", "z = 0", "any", data="multi"),
    # --- volatility expansion
    H("H42", "Bollinger squeeze breakout", "volatility_expansion", "forex,crypto,futures,equity",
      "15m,1h,1d", "Volatility compression precedes expansion.",
      "bandwidth in bottom 10% then close outside band", "cross middle band", "middle band",
      "none", "any",
      implemented="VOL-SQZ-001"),
    H("H43", "ATR expansion day follow-through", "volatility_expansion", "futures,equity", "1d",
      "A range > 2x ATR closing near its extreme continues next day.",
      "range > 2 ATR and close in top 20%", "next close", "bar midpoint", "time", "any",
      implemented="VOL-ATRX-001"),
    H("H44", "Volatility-regime switch", "volatility_expansion", "futures,forex", "1d",
      "Trend strategies work in rising vol, reversion in falling vol.",
      "trade H01 when realised vol rising else H12", "regime flip", "underlying stops", "none",
      "regime-dependent"),
    # --- multi-timeframe
    H("H45", "HTF trend with LTF pullback", "multi_timeframe", "forex,crypto,futures,equity",
      "15m,1h", "Entering pullbacks in the higher-timeframe trend improves timing.",
      "HTF above SMA and LTF RSI < 35", "RSI > 50 or HTF flip", "HTF flip", "RSI 50",
      "trending",
      implemented="MTF-PB-001"),
    H("H46", "Daily bias, 5m execution", "multi_timeframe", "futures,equity", "5m",
      "Only take 5m breakouts in the direction of the daily trend.",
      "daily close > SMA20 and 5m ORB long", "session close", "ORB low", "2R", "trending"),
    H("H47", "Weekly level, daily trigger", "multi_timeframe", "forex,crypto", "1d",
      "Daily reversal patterns at weekly support/resistance work better.",
      "daily bullish engulfing at prior week low", "5 days", "pattern low", "prior week high",
      "range"),
    # --- machine learning assisted
    H("H48", "Gradient-boosted direction filter", "machine_learning", "forex,crypto,futures",
      "1h", "A LightGBM classifier on lagged returns/vol/time features filters H01 signals.",
      "base signal and P(up) > 0.55", "base exit", "base stop", "base", "any"),
    H("H49", "Regime classifier gating", "machine_learning", "futures,equity", "1d",
      "An HMM/cluster regime label selects trend vs reversion strategies.",
      "regime = trend -> H06; regime = range -> H11", "regime change", "underlying",
      "underlying", "regime-dependent"),
    H("H50", "Meta-labelling", "machine_learning", "forex,crypto,futures,equity", "1h",
      "A secondary model sizes primary signals by predicted hit probability (Lopez de Prado).",
      "primary signal; size by meta-model probability", "primary exit", "primary stop",
      "primary", "any", implemented="ML-META-001"),
    H("H53", "Walk-forward logistic triple-barrier", "machine_learning",
      "forex,equity,crypto,futures", "1h,1d",
      "Causal price/vol/volume features carry weak, regularisable information about which "
      "volatility barrier is hit first.",
      "walk-forward P(up) > 0.55 (long) / < 0.45 (short)", "probability back inside band",
      "triple-barrier width", "triple-barrier width", "any", implemented="ML-LOGIT-001"),
    # --- scalping
    H("H51", "Micro pullback scalp", "scalping", "futures,crypto", "1m",
      "In strong 1m trends, the first 2-3 bar pullback continues.",
      "3+ trend bars then 1-2 bar pullback holding 9 EMA", "next swing", "below pullback low",
      "1R", "trending"),
    H("H52", "Round-number fade", "scalping", "forex,crypto", "1m",
      "First touches of round numbers (e.g. 1.1000, 50 000) see short-term rejection.",
      "first touch of round number", "5 bars", "5 ticks through", "10 ticks", "range"),
]


def by_id() -> dict[str, Hypothesis]:
    return {h.id: h for h in HYPOTHESES}


def to_markdown() -> str:
    lines = ["# Strategy hypothesis bank", "",
             "Generated from `src/apex/strategies/hypotheses.py` (`apex hypotheses`). Every "
             "entry is an untested hypothesis. `data` = what it needs beyond OHLCV bars.", ""]
    for cat in CATEGORIES:
        rows = [h for h in HYPOTHESES if h.category == cat]
        lines += [f"## {cat.replace('_', ' ').title()} ({len(rows)})", "",
                  "| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for h in rows:
            lines.append(f"| {h.id} | {h.name} | {h.markets} | {h.timeframes} | {h.entry} | "
                         f"{h.exit} | {h.stop} | {h.data} | {h.implemented or '-'} |")
        lines.append("")
    return "\n".join(lines)
