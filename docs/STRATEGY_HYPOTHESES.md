# Strategy hypothesis bank

Generated from `src/apex/strategies/hypotheses.py` (`apex hypotheses`). Every entry is an untested hypothesis. `data` = what it needs beyond OHLCV bars.

## Scalping (2)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H51 | Micro pullback scalp | futures,crypto | 1m | 3+ trend bars then 1-2 bar pullback holding 9 EMA | next swing | below pullback low | ohlcv | SC-MICRO-001 |
| H52 | Round-number fade | forex,crypto | 1m | first touch of round number | 5 bars | 5 ticks through | ohlcv | SC-ROUND-001 |

## Momentum (5)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H06 | Time-series momentum | futures,forex,crypto | 1d | sign of 60-bar return | sign flip | vol targeting | ohlcv | MOM-TSMOM-001 |
| H07 | Cross-sectional momentum | equity,crypto | 1d | long top decile of 12-1 month return | monthly rebalance | portfolio-level DD stop | multi | MOM-XSEC-001 |
| H08 | Intraday momentum (first half hour) | equity,futures | 30m | sign of first-30m return at 15:30 ET | close | none | ohlcv | MOM-ID30-001 |
| H09 | 52-week high proximity | equity | 1d | close within 2% of 52w high | close 10% below entry high | 10% trail | multi | MOM-52WH-001 |
| H10 | Crypto weekend momentum | crypto | 1h | Friday 20:00 UTC 24h return sign | Monday 00:00 UTC | 2 ATR | ohlcv | MOM-WKND-001 |

## Mean Reversion (5)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H11 | RSI(2) pullback | equity,futures | 1d | RSI2 < 10 and close > SMA200 | close > SMA5 | trend filter fails | ohlcv | MR-RSI2-001 |
| H12 | Bollinger band fade | forex,crypto,equity | 15m,1h | close outside 2-sigma band | middle band | 3.5 sigma | ohlcv | MR-BB-001 |
| H13 | Internal bar strength | equity | 1d | (close-low)/(high-low) < 0.2 | next close > prior high | 5 days | ohlcv | MR-IBS-001 |
| H14 | Consecutive down days | equity,futures | 1d | 3 lower closes and close > SMA200 | first up close | 5 bars | ohlcv | MR-DOWN-001 |
| H15 | FX overnight reversion | forex | 1h | Asia range break > 1.5x average | London 10:00 | 2x Asia range | ohlcv | MR-ASIA-001 |

## Trend Following (5)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H01 | SMA crossover | forex,equity,crypto,futures | 1h,1d | fast SMA crosses above slow SMA | opposite cross | none / ATR stop | ohlcv | TF-SMA-001 |
| H02 | Donchian breakout | forex,equity,crypto,futures | 1h,1d | close > prior 20-bar high | close < prior 10-bar low | 10-bar channel | ohlcv | BO-DONCH-001 |
| H03 | EMA ribbon alignment | forex,crypto,futures | 1h,4h | all EMAs ordered and price above 8 EMA | ordering breaks | below 55 EMA | ohlcv | TF-EMA-RIB-001 |
| H04 | ATR trailing trend | equity,futures,crypto | 1d | close > 50-day high | close < highest close - 3 ATR | 3 ATR trail | ohlcv | TF-CHAND-001 |
| H05 | Cross-asset trend portfolio | futures,forex | 1d | 12-month return > 0 per market | monthly rebalance | volatility-scaled sizing | multi | TF-XASSET-001 |

## Breakout (5)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H16 | Opening range breakout | equity,futures | 5m,15m | close beyond opening range | session close | opposite side of range | ohlcv | BO-ORB-001 |
| H17 | Inside-day breakout | equity,futures,forex | 1d | break of inside bar high/low | 2 days | other side of inside bar | ohlcv | BO-INSIDE-001 |
| H18 | London breakout | forex | 15m | break of 00:00-07:00 UTC range | 16:00 UTC | range midpoint | ohlcv | BO-LDN-001 |
| H19 | NR7 breakout | equity,futures,crypto | 1d | break of NR7 bar | 3 days | other side | ohlcv | BO-NR7-001 |
| H20 | Gap-and-go | equity | 5m | gap > 2% and first 5m bar breaks in gap direction | session close | first bar low/high | multi | - |

## Vwap (4)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H21 | VWAP deviation reversion | equity,futures | 1m,5m | z(close-VWAP) < -2 | touch VWAP | z < -4 | ohlcv | VWAP-MR-001 |
| H22 | VWAP trend pullback | equity,futures | 5m | price above VWAP all morning, touches VWAP | session close | close below VWAP - 1 ATR | ohlcv | VWAP-PB-001 |
| H23 | Anchored VWAP from earnings | equity | 1d | reclaim of AVWAP | close below AVWAP | AVWAP - 1 ATR | multi | - |
| H24 | VWAP band breakout | futures,crypto | 5m | two closes beyond 2-sigma band | close back inside 1-sigma | VWAP | ohlcv | VWAP-BAND-001 |

## Volume Profile (3)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H25 | Value area 80% rule | futures | 30m | two 30m closes inside prior value area | opposite value area edge | outside value area | ticks | VP-VA80-001 |
| H26 | Point of control magnet | futures,equity | 5m | price > 1 ATR from POC with declining volume | touch POC | 1 ATR further | ticks | VP-POC-001 |
| H27 | Low-volume node rejection | futures | 5m | rejection candle at HVN | next LVN | beyond HVN | ticks | - |

## Order Flow (3)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H28 | Cumulative delta divergence | futures,crypto | 1m,5m | new high with lower cumulative delta | prior swing low | new high + 1 tick | ticks | - |
| H29 | Order book imbalance | crypto,futures | tick | bid/ask size ratio > 3 | imbalance reverts | 2 ticks | l2 | - |
| H30 | Absorption at extremes | futures | 1m | volume > 3x average with < 1 tick progress | 10 minutes | level +2 ticks | ticks | - |

## Liquidity Sweep (4)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H31 | Prior-low sweep reversal | forex,crypto,futures,equity | 15m,1h | low < prior 20-bar low and close back above | 5 bars | sweep low | ohlcv | LIQ-SWEEP-001 |
| H32 | Asian range sweep (FX) | forex | 15m | sweep of Asia high/low then close inside | 16:00 UTC | sweep extreme | ohlcv | LIQ-ASIA-001 |
| H33 | Previous day high/low sweep | futures,equity | 5m | trade through PDH/PDL and close back | session close | sweep extreme | ohlcv | LIQ-PDHL-001 |
| H34 | Equal highs raid | crypto,forex | 1h | two highs within 0.1 ATR swept and rejected | 6 bars | sweep extreme | ohlcv | LIQ-EQH-001 |

## Market Structure (3)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H35 | Break of structure with higher low | forex,crypto,futures,equity | 15m,1h | close > last swing high with HL | close < last swing low | last swing low | ohlcv | MS-BOS-001 |
| H36 | Change of character reversal | forex,crypto | 15m | first lower low after series of HH/HL | new swing high | last swing high | ohlcv | MS-CHOCH-001 |
| H37 | Fair value gap fill | forex,futures,crypto | 5m,15m | price returns into FVG in trend direction | FVG far edge broken | beyond FVG | ohlcv | MS-FVG-001 |

## Statistical Arbitrage (4)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H38 | Cointegrated pairs | equity | 1d | spread z < -2 | z > 0 | z < -4 | multi | STAT-PAIR-001 |
| H39 | ES/NQ ratio reversion | futures | 15m | ratio z beyond 2 | z = 0 | z beyond 4 | multi | STAT-PAIR-001 |
| H40 | BTC/ETH spread | crypto | 1h | spread z beyond 2 | z = 0 | z beyond 4 | multi | STAT-PAIR-001 |
| H41 | ETF vs basket | equity | 5m | z beyond 2.5 | z = 0 | time 1 day | multi | STAT-BASKET-001 |

## Volatility Expansion (3)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H42 | Bollinger squeeze breakout | forex,crypto,futures,equity | 15m,1h,1d | bandwidth in bottom 10% then close outside band | cross middle band | middle band | ohlcv | VOL-SQZ-001 |
| H43 | ATR expansion day follow-through | futures,equity | 1d | range > 2 ATR and close in top 20% | next close | bar midpoint | ohlcv | VOL-ATRX-001 |
| H44 | Volatility-regime switch | futures,forex | 1d | trade H01 when realised vol rising else H12 | regime flip | underlying stops | ohlcv | VOL-SWITCH-001 |

## Multi Timeframe (3)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H45 | HTF trend with LTF pullback | forex,crypto,futures,equity | 15m,1h | HTF above SMA and LTF RSI < 35 | RSI > 50 or HTF flip | HTF flip | ohlcv | MTF-PB-001 |
| H46 | Daily bias, 5m execution | futures,equity | 5m | daily close > SMA20 and 5m ORB long | session close | ORB low | ohlcv | MTF-ORB-001 |
| H47 | Weekly level, daily trigger | forex,crypto | 1d | daily bullish engulfing at prior week low | 5 days | pattern low | ohlcv | MTF-WKENG-001 |

## Machine Learning (4)

| ID | Name | Markets | TF | Entry | Exit | Stop | Data | Code |
|---|---|---|---|---|---|---|---|---|
| H48 | Gradient-boosted direction filter | forex,crypto,futures | 1h | base signal and P(up) > 0.55 | base exit | base stop | ohlcv | ML-GBT-001 |
| H49 | Regime classifier gating | futures,equity | 1d | regime = trend -> H06; regime = range -> H11 | regime change | underlying | ohlcv | ML-HMM-001 |
| H50 | Meta-labelling | forex,crypto,futures,equity | 1h | primary signal; size by meta-model probability | primary exit | primary stop | ohlcv | ML-META-001 |
| H53 | Walk-forward logistic triple-barrier | forex,equity,crypto,futures | 1h,1d | walk-forward P(up) > 0.55 (long) / < 0.45 (short) | probability back inside band | triple-barrier width | ohlcv | ML-LOGIT-001 |

