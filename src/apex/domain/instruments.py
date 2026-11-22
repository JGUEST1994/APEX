"""Initial instrument universe from the spec (Section 3).

Cost figures are conservative placeholder assumptions for research, not quotes from any
broker. They must be calibrated against real fills before results are trusted.
"""

from __future__ import annotations

from apex.domain.market import AssetClass, CostModel, InstrumentSpec

_FX_COST = CostModel(spread_bps=0.6, slippage_bps=0.3, funding_bps_per_day=0.2)
_EQ_COST = CostModel(commission_per_unit=0.005, spread_bps=1.0, slippage_bps=1.0)
_CRYPTO_COST = CostModel(commission_bps=10.0, spread_bps=1.0, slippage_bps=2.0)


def _fx(pair: str, tick: float = 0.00001) -> InstrumentSpec:
    return InstrumentSpec(
        pair, AssetClass.FOREX, "FX", "fx_24x5", quote_currency=pair[3:],
        base_currency=pair[:3], tick_size=tick, lot_size=1000, costs=_FX_COST,
    )


def _eq(sym: str, venue: str = "US") -> InstrumentSpec:
    return InstrumentSpec(sym, AssetClass.EQUITY, venue, "us_equity_rth", "USD", costs=_EQ_COST)


def _crypto(sym: str, base: str) -> InstrumentSpec:
    return InstrumentSpec(
        sym, AssetClass.CRYPTO, "BINANCE", "crypto_24x7", quote_currency="USDT",
        base_currency=base, tick_size=0.01, lot_size=0.00001, costs=_CRYPTO_COST,
    )


def _fut(sym: str, tick: float, mult: float, commission: float) -> InstrumentSpec:
    return InstrumentSpec(
        sym, AssetClass.FUTURES, "CME", "cme_globex", "USD", tick_size=tick,
        contract_multiplier=mult,
        costs=CostModel(commission_per_unit=commission, spread_bps=0.5, slippage_bps=0.5),
    )


DEFAULT_UNIVERSE: list[InstrumentSpec] = [
    *(_fx(p) for p in ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"]),
    _fx("USDJPY", 0.001), _fx("EURJPY", 0.001), _fx("EURGBP"),
    *(_eq(s) for s in ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN"]),
    # more US index and sector funds (Dow; materials, energy, financials, industrials, tech,
    # staples, utilities, health care, consumer discretionary), all listed before 2000
    *(_eq(s) for s in ["DIA", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]),
    # US-listed funds of overseas stock markets (developed and emerging; Japan, Germany, UK,
    # Canada, Australia, Brazil), used as a fresh basket no daily idea was first tried on
    *(_eq(s) for s in ["EFA", "EEM", "EWJ", "EWG", "EWU", "EWC", "EWA", "EWZ"]),
    _crypto("BTCUSDT", "BTC"), _crypto("ETHUSDT", "ETH"),
    _fut("ES", 0.25, 50.0, 2.50), _fut("MES", 0.25, 5.0, 0.75),
    _fut("NQ", 0.25, 20.0, 2.50), _fut("MNQ", 0.25, 2.0, 0.75),
]


def by_symbol() -> dict[str, InstrumentSpec]:
    return {i.symbol: i for i in DEFAULT_UNIVERSE}
