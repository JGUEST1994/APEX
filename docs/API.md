# API

Run `apex serve` and open `http://127.0.0.1:8000/docs` for the live OpenAPI reference. The API is read-only in Phase 1. If `APEX_API_TOKEN` is set, every endpoint except `/health` needs `Authorization: Bearer <token>` (or the browser cookie set by opening `/ui/?token=<token>`); otherwise it answers 401.

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/health` | | `status`, `version`, `database`, `trading_mode`, `live_trading_available` (always false), `external_ai_enabled` |
| GET | `/instruments` | | list of `{symbol, venue, asset_class, calendar, quote_currency}` |
| GET | `/instruments/{symbol}/bars` | `timeframe` (1m,5m,15m,30m,1h,1d), `source` (e.g. synthetic, csv, binance), optional `start`, `end` (ISO 8601 with timezone), `venue`, `limit` ≤ 50000 | list of `{ts, open, high, low, close, volume}`; 404 if unknown symbol |
| GET | `/ingestion-runs` | `limit` ≤ 500 | recent ingestion runs with `symbol`, `content_sha256` data versions, `issues` (number of data-quality findings) and `error` |
| GET | `/ingestion-runs/{id}/issues` | `limit` ≤ 5000 | that run's data-quality findings: `kind` (gap, spike, duplicate, ...), `severity` (error rows were not stored), `ts`, `detail`; 404 if no such run |
| GET | `/experiments` | `limit` ≤ 500 | backtest experiments with strategy key and version, params, data manifest (incl. data SHA-256), code version, metrics; all `simulated: true` |
| GET | `/experiments/{id}` | | one experiment in the same shape; 404 if unknown (the Tournament tab links each row here via `experiment_id`) |
| GET | `/experiments/{id}/equity` | `points` (10-5000, default 600) | re-runs a recorded plain backtest exactly (same strategy version and code, params, costs, stored bars checked against the recorded data hash) without recording anything, and returns `ts` / `equity` thinned to about `points`; `available: false` with a plain `reason` if the strategy code or prices changed or it is not a plain backtest; `matches_record` compares with the stored total return; 404 for an unknown id |
| GET | `/leaderboard` | `asset_class`, `symbol`, `timeframe`, `category` | latest walk-forward result per strategy/market ranked by robustness score, with gate pass/fail reasons and status; all `simulated: true` |
| GET | `/strategies` | | registered strategies with metadata (description, `entry_rules`, `exit_rules`, `stop_loss`, `take_profit`, `asset_classes`, `default_params`, `ideas` = hypothesis ids), current lifecycle status and `instruments` (1, 2, or `"2+"` for a basket) |
| GET | `/strategies/{key}/history` | | every recorded status change, oldest first: `at`, `version`, `from` (null when created), `to`, `by` (system or the named person), `reason`; 404 for an unknown key |
| GET | `/system/checks` | | the `apex doctor` check-up: list of `level` (OK/WARN/FAIL), `name`, `detail`; says whether keys are set, never their values |
| GET | `/export/experiments.csv`, `/export/leaderboard.csv` | | CSV download (same columns as `apex export`; every results row `simulated=true`) |
| GET | `/export/bars.csv` | `symbol`, `timeframe`, `source` | stored prices as CSV; 404 for an unknown symbol |
| GET | `/export/fills.csv` | `name` | a practice account's fills as CSV; 404 if no such account. Text cells starting with `= + - @` get a leading `'` in all downloads so Excel never runs them as formulas |
| GET | `/hypotheses` | | the strategy hypothesis bank (spec section 8): id, name, category, markets, timeframes, idea, entry/exit/stop/target, regime, `data` needed and `implemented` (strategy key or null) |
| GET | `/datasets` | | stored bar datasets (symbol, timeframe, source) with counts and first/last bar |
| GET | `/paper/accounts` | | simulated paper accounts: status, kill switch, cash, realised P&L, holdings |
| GET | `/paper/accounts/{name}` | `limit` | one paper account plus its most recent orders and fills (newest first) |
| GET | `/paper/accounts/{name}/equity` | `points` (10-5000, default 600) | simulated account value at each processed bar's close, rebuilt from the fill ledger (cash + units x close x multiplier; the last point equals the account value); empty before the first run; 404 for an unknown account |
| GET | `/paper/accounts/{name}/consistency` | | paper-vs-backtest comparison over the bars the account processed: direction agreement, position changes followed/missed, fill price gap, `consistent`, notes |
| GET | `/paper/accounts/{name}/record` | | the practice record: days since the account was created (forward days only), still running, kill-switch/halt events, consistency, `ready` and plain-English `reasons` (read-only) |
| GET | `/ui/` | | read-only dashboard (`/` redirects here) |
| GET | `/research/search` | `q`, `limit` ≤ 100 | hybrid hits `{document_id, title, chunk_ordinal, score, keyword_score, vector_score, snippet}` |
| GET | `/research/documents` | `limit` ≤ 1000 | documents `{id, title, authors, source_type, published, subjects, imported_at}` |
| GET | `/research/documents/{id}` | | document with `extracted` concepts, risk techniques, formulas, rules; 404 if missing |

Authentication is not implemented yet; keep the API bound to localhost (the default).
