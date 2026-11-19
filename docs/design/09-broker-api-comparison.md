# 09. Broker API comparison

> Indicative, from general knowledge on 2026-11-19; check current terms, fees and whether each broker accepts clients in your country before opening an account. Real-money trading is not enabled in APEX; this informs paper trading and future work.

| Broker | Markets for APEX | API | Paper trading | Notes on cost | Fit |
|---|---|---|---|---|---|
| **Interactive Brokers** | FX, stocks/ETFs, ES/MES/NQ/MNQ futures, limited crypto | TWS API / IB Gateway (socket), Client Portal Web API (REST); Python via `ib_async` | Yes, full paper account | Low commissions (e.g. fractions of a cent per share, ~$0.25–$0.85 per micro/standard futures contract plus exchange fees); market data subscriptions extra | **Primary recommendation**: one account covers all four markets; widely available internationally incl. UK |
| **Alpaca** | US stocks/ETFs, crypto (options too) | REST + WebSocket, clean modern API, official Python SDK | Yes, free and instant | Commission-free US equities; crypto fee per trade | Best for fast prototyping of US equity paper trading; no FX or futures |
| **Tradovate** | CME futures only | REST + WebSocket | Yes (demo) | Per-contract commissions; API access has historically needed an add-on subscription | Good futures-only alternative; check API access cost and region |
| **OANDA** | FX (and CFDs depending on region) | v20 REST + streaming | Yes, practice account | Spread-based pricing | Good FX-specific alternative; simple API |
| **Kraken** | Crypto (spot; futures in some regions) | REST + WebSocket | No native paper; use APEX paper broker | Maker/taker fees | Available in many jurisdictions; supports API keys without withdrawal permission |
| **Coinbase Advanced Trade** | Crypto spot | REST + WebSocket | Sandbox limited | Maker/taker fees | Regulated, simple; fees higher at low volume |
| **Binance** | Crypto spot/derivatives | REST + WebSocket | Testnet | Low fees | Availability depends on jurisdiction; check before relying on it |

## Recommendation

1. **Paper trading (Phase 9)** uses APEX's own paper broker driven by real-time data, so it works for every market regardless of broker. This also gives identical fill logic to the backtester.
2. **First broker adapter (Phase 11)**: Interactive Brokers paper account, because it is the only option that covers FX, US equities and CME micros in one account. Use IB Gateway in a container with automatic restart.
3. **Second adapter**: Alpaca (paper) for US equities, as a simpler API that is good for testing the adapter abstraction.
4. **Crypto**: Kraken adapter with trade-only API keys (withdrawals disabled).

## Adapter interface (Phase 11 design)

```python
class BrokerAdapter(Protocol):
    name: str
    def connect(self) -> None: ...
    def account(self) -> AccountSnapshot: ...
    def positions(self) -> list[Position]: ...
    def open_orders(self) -> list[Order]: ...
    def submit(self, order: Order) -> BrokerOrderId: ...   # idempotent on client_order_id
    def cancel(self, order_id: BrokerOrderId) -> None: ...
    def modify(self, order_id: BrokerOrderId, changes: OrderChanges) -> None: ...
    def events(self) -> Iterator[ExecutionEvent]: ...      # acks, fills, rejects
```

Every adapter must pass the same conformance test suite against a recorded or simulated session before it may be enabled.
