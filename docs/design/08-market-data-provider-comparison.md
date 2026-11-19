# 08. Market data provider comparison

> **Prices and terms are indicative.** They come from general knowledge, not live quotes checked on 2026-11-19, and vendors change plans often. Verify on each vendor's site before subscribing, and check that the licence allows the use you intend (personal research vs. display vs. redistribution).

## Requirements recap

Four asset classes (FX, US stocks/ETFs, crypto, ES/MES/NQ/MNQ futures); history down to 1-minute (tick where possible); real-time streaming for paper trading; official APIs with clear terms.

## Comparison

| Provider | Asset classes | History depth / granularity | Real-time | Indicative cost | Strengths | Weaknesses |
|---|---|---|---|---|---|---|
| **Interactive Brokers** (TWS / IB Gateway API) | FX, stocks, futures, some crypto | Years of 1-min for many instruments; strict pacing limits | Yes, with exchange subscriptions | Account required; market data a few $ to tens of $ per month per exchange bundle | One source for all four markets; same account as execution | Pacing limits make bulk backfill slow; gateway process to babysit |
| **Polygon.io** (rebranded **Massive** in 2025) | US stocks/options/indices, FX, crypto | 15–20+ years US equities, minute and tick | Yes (WebSocket) | Free tier (5 calls/min, delayed); paid roughly $29–$199+/month per asset class | Excellent US equity history, clean REST + flat files | No futures; separate plans per asset class |
| **Alpaca Market Data** | US stocks/ETFs, crypto | ~2016+ minute bars | Yes | Free (IEX feed only); ~$99/month for full SIP | Free, simple, matches Alpaca paper trading | IEX-only free feed is a small share of volume; no FX or futures |
| **Twelve Data** | FX, stocks, ETFs, crypto, indices | Long daily history, intraday depth varies | Yes (WebSocket on paid tiers) | Free 800 credits/day; paid from roughly $29–$99+/month | Broad multi-asset coverage, easy API | Not institutional-grade for intraday microstructure; no CME futures |
| **Binance public data** | Crypto | Full kline history 1m+ for listed pairs; bulk dumps on data.binance.vision | Yes (WebSocket) | Free, no key for market data | Free, deep, fast | Availability of Binance *trading* varies by jurisdiction (check yours); crypto only |
| **Databento** | CME futures, US equities, options | Tick/MBO/MBP and OHLCV back to 2010+ for CME | Yes | Usage-based (pay per GB) plus a free starter credit; live needs a licence | Best-in-class futures history for ES/NQ | Usage pricing needs care; licence terms for live |
| **Dukascopy** historical | FX, some CFDs | Tick data back to ~2003 for majors | No (for our purposes) | Free download, terms apply | Free tick FX history | Bank feed, not a broker-neutral composite; download tooling required |
| **Kraken / Coinbase** public APIs | Crypto | Varies; Kraken offers downloadable OHLCVT history | Yes | Free | Exchanges available in more jurisdictions (incl. UK) | Each has its own symbology and limits |

## Recommendation

| Phase | FX | Stocks/ETFs | Crypto | Futures |
|---|---|---|---|---|
| Phase 1–5 research (now) | Dukascopy or purchased CSV via `CsvFileProvider` | Alpaca free (IEX) or Polygon/Massive Starter | Binance public (adapter implemented) or Kraken | Databento pay-as-you-go history for ES/NQ |
| Phase 9 paper trading | IBKR or OANDA stream | Alpaca (SIP) or Polygon/Massive | Exchange WebSocket | IBKR CME real-time subscription |
| Live (later, after review) | Broker's own feed for execution decisions | Broker feed + independent check feed | Exchange feed | Broker feed |

Rationale: use free or pay-per-use history first to find out whether any strategy deserves money, and pay for real-time feeds only when paper trading begins. Keep at least two independent sources per market for cross-validation of bars (the `bars.source` key already supports this).

## How a provider plugs in

Subclass `apex.marketdata.base.MarketDataProvider`, implement `supports()` and `_fetch()`; the base class normalises, filters the range and enforces timezone-aware inputs. Ingestion then validates and versions the data. Implemented today: `SyntheticProvider`, `CsvFileProvider`, `BinanceSpotProvider` (tested with a mocked HTTP transport; the build sandbox cannot reach Binance, so it has not yet been run against the live endpoint).
