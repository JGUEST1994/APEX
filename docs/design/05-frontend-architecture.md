# 05. Frontend architecture

A thin read-only dashboard exists (Phase 10 slice 1): a single dependency-free page served by the API at `/ui/` (`src/apex/api/static/index.html`) with Overview, Markets, Strategy lab, Tournament, Backtests and Research tabs, and the persistent RESEARCH/PAPER banner. It renders text only via `textContent`, loads no third-party scripts, and talks only to the API. The full Next.js app below remains the target once paper trading and risk pages need interaction.

## Stack

Next.js (App Router) + React + TypeScript, TanStack Query for server state, TradingView Lightweight Charts for price and equity charts, a small component library (shadcn/ui on Tailwind), OpenAPI-generated TypeScript client from the FastAPI schema so the two never drift.

Desktop use: the same web app, optionally wrapped with Tauri later for a native window on Windows/Linux. Tauri is preferred over Electron for size and security.

## Pages (spec Section 14)

| Page | Main content | Backend dependency |
|---|---|---|
| Overview | Equity, daily P&L, open risk, system status | portfolio, risk |
| Live markets | Watchlists per asset class, charts, data freshness badges | bars, staleness |
| Strategy lab | Strategy catalogue, versions, parameters, status | strategies |
| Backtesting | Equity curve, drawdown, trade list, metrics, data manifest | experiments |
| Paper trading | Simulated accounts, positions, orders | execution (paper) |
| Live trading | Broker connections, positions, orders. Disabled in this release | execution |
| Risk management | Limits, exposure, kill switch | risk |
| Research library | Documents, search, extracted concepts | research |
| AI assistant | Optional chat; hidden when `external_ai_enabled` is false | ai |
| System monitoring | Service health, feeds, errors, ingestion runs | health, ingestion_runs |

## Rules

- **Simulated vs real must be unmistakable**: a persistent banner and colour per environment (`RESEARCH`, `PAPER`; `LIVE` would be red and require re-authentication). Every P&L figure carries a `SIMULATED` tag unless it came from a real account.
- The kill switch is visible on every page once trading exists, and needs a single click plus confirmation.
- The UI never talks to brokers or AI providers directly; everything goes through the API.
- Data freshness shown next to every live price.
