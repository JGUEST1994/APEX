# Deployment

Status: the local install (venv + SQLite or PostgreSQL) is tested, and the Docker stack is built and started on every CI run (the `docker` job checks that migrations ran, `/health` reaches the database in research mode and `/ui/` is served). Use `docker compose up -d --build --wait` to block until the API is healthy.

## Local (Windows, Mac or Linux)

Mac: see [MAC_SETUP.md](MAC_SETUP.md) (double-click `start-apex.command` and `start-scheduler.command`; they keep the Mac awake while running).

See README quick start. On Windows use PowerShell and `.venv\Scripts\activate`.

## Docker Compose (Linux server / VPS)

```bash
cp .env.example .env            # set APEX_DB_PASSWORD=<strong password>
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The API is published on `127.0.0.1` only. Expose it remotely through a TLS reverse proxy or VPN, never directly, and set `APEX_API_TOKEN` (in `.env`) when you do: every request except `/health` then needs `Authorization: Bearer <token>`, or the cookie a browser gets by opening `/ui/?token=<token>` once (the token is swapped for an HttpOnly, SameSite=Strict cookie and removed from the address bar; access logs show it as `token=***`). The token is a single shared secret, not user accounts.

## Scheduled data updates

`apex update` only fetches completed bars after the newest stored one, so it is safe to run on a timer. Example crontab (every 5 minutes, crypto 1m bars):

```
*/5 * * * * cd /opt/apex && .venv/bin/apex update --provider binance --symbols BTCUSDT,ETHUSDT --timeframe 1m --backfill-start 2024-01-01 >> logs/update.log 2>&1
```

On Windows use Task Scheduler with the same command.

### Built-in scheduler (no cron needed)

`apex schedule --config jobs.json` keeps running and does the same work on a timer: data updates first, then paper accounts, so accounts always see the newest bars. A failing job is logged and written to the audit log and never stops the others. Use `--once` to run every job once (handy from cron or for a quick check).

```json
{"jobs": [
  {"name": "crypto-1h", "type": "update", "provider": "kraken", "symbols": ["BTCUSDT", "ETHUSDT"],
   "timeframe": "1h", "backfill_start": "2024-01-01", "every_minutes": 60},
  {"name": "stocks-1d", "type": "update", "provider": "alpaca", "symbols": ["SPY", "QQQ"],
   "timeframe": "1d", "backfill_start": "2020-01-01", "every_minutes": 1440},
  {"name": "practice", "type": "paper", "accounts": ["mix"], "every_minutes": 60}
]}
```

On Windows, `start-scheduler.bat` runs `apex schedule --config jobs.json` from the APEX folder (creating `jobs.json` from `jobs.example.json` on first use; `jobs.json` is git-ignored). Paper jobs only run with `APEX_TRADING_MODE=paper`; a halted account stays halted until a named person runs `apex paper-reset-kill`. Every step is incremental and idempotent, so stopping and restarting the scheduler (or running it next to cron) cannot duplicate bars or orders.

### Paper trading (simulated only)

Paper commands refuse to run unless `APEX_TRADING_MODE=paper`. There is no live mode.

```
apex paper-create --name sma-spy --strategy TF-SMA-001 --symbol SPY --timeframe 1d --source alpaca
# after each data update (cron-safe: only new stored bars are processed, each bar is one transaction)
apex paper-run --name sma-spy
# a tripped kill switch or a failed reconciliation halts the account until a person resets it
apex paper-reset-kill --name sma-spy --actor jake
```

## Restart behaviour

Containers use `restart: unless-stopped`; the API applies migrations on start. When trading arrives (Phase 9) the engine will start in a halted, reconciling state and will not resume trading automatically if account state is uncertain.

## Backups

SQLite installs: `apex backup [--folder backups] [--keep N]` copies the database with
SQLite's online backup API (a consistent snapshot even while the API or scheduler writes),
runs `PRAGMA integrity_check` on the copy, and only then names it
`apex-backup-YYYYMMDD-HHMMSS.db` (UTC). `--keep` prunes older `apex-backup-*` files in that
folder and nothing else. The scheduler accepts the same as a job:
`{"type": "backup", "folder": "backups", "keep": 14, "every_minutes": 1440}` (runs after
data and paper jobs; `jobs.example.json` includes it).

PostgreSQL installs: `apex backup` refuses and points here. Use `pg_dump -Fc` from cron
(planned Phase 13: encrypted off-site storage and a periodic restore drill).
