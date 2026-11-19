# API

Run `apex serve` and open `http://127.0.0.1:8000/docs` for the live OpenAPI reference. The API is read-only in Phase 1.

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/health` | | `status`, `version`, `database`, `trading_mode`, `live_trading_available` (always false), `external_ai_enabled` |
| GET | `/instruments` | | list of `{symbol, venue, asset_class, calendar, quote_currency}` |
| GET | `/instruments/{symbol}/bars` | `timeframe` (1m,5m,15m,30m,1h,1d), `source` (e.g. synthetic, csv, binance), optional `start`, `end` (ISO 8601 with timezone), `venue`, `limit` ≤ 50000 | list of `{ts, open, high, low, close, volume}`; 404 if unknown symbol |
| GET | `/ingestion-runs` | `limit` ≤ 500 | recent ingestion runs with `content_sha256` data versions |
| GET | `/experiments` | `limit` ≤ 500 | backtest experiments with params, data manifest (incl. data SHA-256), code version, metrics; all `simulated: true` |
| GET | `/leaderboard` | `asset_class`, `symbol`, `timeframe`, `category` | latest walk-forward result per strategy/market ranked by robustness score, with gate pass/fail reasons and status; all `simulated: true` |
| GET | `/strategies` | | registered strategies with metadata and current lifecycle status |
| GET | `/datasets` | | stored bar datasets (symbol, timeframe, source) with counts and first/last bar |
| GET | `/paper/accounts` | | simulated paper accounts: status, kill switch, cash, realised P&L, holdings |
| GET | `/paper/accounts/{name}` | `limit` | one paper account plus its most recent orders and fills (newest first) |
| GET | `/paper/accounts/{name}/consistency` | | paper-vs-backtest comparison over the bars the account processed: direction agreement, position changes followed/missed, fill price gap, `consistent`, notes |
| GET | `/ui/` | | read-only dashboard (`/` redirects here) |
| GET | `/research/search` | `q`, `limit` ≤ 100 | hybrid hits `{document_id, title, chunk_ordinal, score, keyword_score, vector_score, snippet}` |
| GET | `/research/documents` | `limit` ≤ 1000 | documents `{id, title, authors, source_type, published, subjects, imported_at}` |
| GET | `/research/documents/{id}` | | document with `extracted` concepts, risk techniques, formulas, rules; 404 if missing |

Authentication is not implemented yet; keep the API bound to localhost (the default).
