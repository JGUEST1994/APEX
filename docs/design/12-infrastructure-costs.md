# 12. Estimated infrastructure costs

> Indicative monthly figures in USD from general knowledge on 2026-11-19, not live quotes. Exchange rates and vendor plans change; verify before buying.

## By stage

| Item | Stage 1a: local lab (now) | Stage 1b: lab on a VPS | Stage 2: paper trading 24/7 | Later: live-ready |
|---|---|---|---|---|
| Compute | Your own PC: $0 | VPS 4 vCPU / 8–16 GB (e.g. Hetzner, OVH): ~$10–$40 | 8 vCPU / 32 GB VPS or dedicated: ~$40–$120 | Primary + standby, closer to brokers: ~$100–$300 |
| Storage & backups | $0 | Block storage + object-storage backups: ~$2–$10 | ~$5–$20 | ~$10–$40 |
| Historical data | Free sources + one-off CSV purchases ($0–$200 one-off) | Polygon/Massive or Twelve Data starter: ~$30–$80 | Databento usage for futures history: variable, often $0–$100 | Same |
| Real-time data | $0 | $0 | IBKR exchange bundles ~$5–$30; Alpaca SIP ~$99 if needed | ~$50–$200 |
| Broker | $0 | $0 | Paper accounts: $0 | Commissions per trade |
| Monitoring | $0 | Free tiers (Uptime Kuma self-hosted, Grafana) | ~$0–$20 | ~$0–$50 |
| Optional external AI | $0 (off) | Pay-as-you-go, typically $0–$30 | $0–$50 | $0–$50 |
| **Total (monthly)** | **≈ $0–$30** | **≈ $40–$160** | **≈ $100–$400** | **≈ $250–$700+** |

## Cost controls

- Prove strategies with free and pay-as-you-go history before paying for real-time feeds.
- Run research jobs on your own machine; the VPS only needs to run ingestion, paper trading and the API.
- SQLite + local disk is enough for Stage 1a; PostgreSQL on the VPS from Stage 1b.
- External AI stays off by default; local embeddings avoid per-token costs for search.
