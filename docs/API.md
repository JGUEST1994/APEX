# API

Run `apex serve` and open `http://127.0.0.1:8000/docs` for the live OpenAPI reference. The API is read-only in Phase 1.

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/health` | | `status`, `version`, `database`, `trading_mode`, `live_trading_available` (always false), `external_ai_enabled` |
| GET | `/instruments` | | list of `{symbol, venue, asset_class, calendar, quote_currency}` |
| GET | `/instruments/{symbol}/bars` | `timeframe` (1m,5m,15m,30m,1h,1d), `source` (e.g. synthetic, csv, binance), optional `start`, `end` (ISO 8601 with timezone), `venue`, `limit` ≤ 50000 | list of `{ts, open, high, low, close, volume}`; 404 if unknown symbol |
| GET | `/ingestion-runs` | `limit` ≤ 500 | recent ingestion runs with `content_sha256` data versions |
| GET | `/research/search` | `q`, `limit` ≤ 100 | keyword hits `{document_id, title, chunk_ordinal, score, snippet}` |

Authentication is not implemented yet; keep the API bound to localhost (the default).
