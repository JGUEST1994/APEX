# APEX user guide (Windows, plain English)

This guide walks through using APEX day to day on a Windows PC: getting prices, testing a
trading idea, ranking ideas against each other, and running a practice (paper) account.
Every figure APEX shows is **simulated**. There is no real-money trading in this release.

All commands go in **PowerShell**, inside the APEX folder, after switching on the project's
Python environment:

```powershell
cd C:\path\to\APEX
.venv\Scripts\activate
```

If you have not installed APEX yet, follow the quick start in [README.md](../README.md)
first (it ends with `apex db-upgrade` and `apex seed-instruments`).

## 1. Look at the dashboard

The easy way: double-click **`start-apex.bat`** in the APEX folder. It switches on the
environment, brings the database up to date and opens the dashboard in your browser after
a few seconds (refresh the page if it opened too early). Close the black window to stop
APEX.

Or from PowerShell:

```powershell
apex serve
```

Then open <http://127.0.0.1:8000/ui/> in your browser. Leave the PowerShell window open while
you use it; press Ctrl+C there to stop it. The tabs are:

| Tab | What it shows |
|---|---|
| Overview | Is everything running (a check-up list), and what data APEX has stored |
| Markets | Price chart for each stored market |
| Strategy lab | Every strategy APEX can test and how far through testing it is |
| Trading ideas | The 53 ideas from the APEX spec, and which ones have a strategy yet |
| Tournament | Strategies ranked by how well they held up on data they had not seen |
| Backtests | Every test APEX has run, so it can be repeated exactly |
| Practice trading | Paper accounts, their trades and how closely they match a backtest |
| Research | Search the notes and papers you have imported |

The dashboard only reads. Everything below is done from PowerShell (open a second window
for it while `apex serve` is running).

## 2. Get some prices

APEX stores prices ("bars") per market, timeframe and source. You choose the source with
`--provider`.

**Free, no account needed (crypto):**

```powershell
apex ingest --provider coinbase --symbol BTCUSDT --timeframe 1h --start 2024-01-01 --end 2024-07-01
apex ingest --provider binance  --symbol ETHUSDT --timeframe 1d --start 2021-01-01 --end 2025-01-01
apex ingest --provider kraken   --symbol BTCUSDT --timeframe 1h --start 2024-01-01 --end 2024-02-01
```

Kraken only gives the most recent 720 bars, so use it for keeping data up to date rather
than for long history. Coinbase and Binance can fetch long history.

**US stocks and ETFs:** sign up for a free Alpaca account, create API keys, and put them
in the `.env` file in the APEX folder (never in the code, and never share that file):

```
APEX_ALPACA_KEY_ID=your-key-id
APEX_ALPACA_SECRET_KEY=your-secret
```

Then:

```powershell
apex ingest --provider alpaca --symbol SPY --timeframe 1d --start 2019-01-01 --end 2025-01-01
```

**Your own files:** save a CSV as `data\csv\<SYMBOL>_<timeframe>.csv` (for example
`data\csv\EURUSD_1h.csv`) and use `--provider csv`.

**Futures, one file per contract:** each futures contract (say the March and June S&P
contracts) expires, so a long history has to be stitched together. Put one CSV per contract
in a folder named after the market, for example `data\csv\ES\ESH5_1d.csv` and
`data\csv\ES\ESM5_1d.csv`, then:

```powershell
apex ingest --provider csv-cont --symbol ES --timeframe 1d --start 2020-01-01 --end 2025-07-01
```

APEX switches to the next contract the day after it trades more than the old one, and
shifts older prices so there is no false jump at the switch (it prints each switch). The
newest prices are real; older ones are adjusted, so use them for testing ideas, not for
looking up what something cost. `--provider csv-cont-ratio` adjusts by percentage instead,
which keeps old prices above zero.

**Made-up practice data** (useful for learning the commands; it means nothing about real
markets): `--provider synthetic`.

To top data up later without re-downloading everything:

```powershell
apex update --provider coinbase --symbols BTCUSDT,ETHUSDT --timeframe 1h --backfill-start 2024-01-01
```

**Keep prices up to date automatically:** double-click **`start-scheduler.bat`**. The first
time, it copies `jobs.example.json` to `jobs.json` and stops so you can edit it (it starts
with Bitcoin and Ether from Coinbase, hourly and daily). Run it again and leave the window
open: it tops the data up on each job's timer. Close the window to stop. To include a
practice account, add a job like `{"name": "practice", "type": "paper", "accounts":
["sma-spy"], "every_minutes": 60}` and put `APEX_TRADING_MODE=paper` in `.env`.

## 3. Test one idea (a backtest)

See which strategies exist with `apex strategies`, or the Strategy lab tab. Then:

```powershell
apex backtest --strategy TF-SMA-001 --symbol SPY --timeframe 1d --source alpaca --start 2019-01-01 --end 2025-01-01
```

`--source` must match the provider you downloaded the data with. The result prints in the
window and is saved to the Backtests tab. Change a setting with `--param`, for example
`--param fast=10 --param slow=50`. A good-looking backtest on its own proves very little;
the next step is the real test.

**Strategies that trade two or more markets together** (pairs, baskets, cross-asset) use
`backtest-pair`:

```powershell
apex backtest-pair --strategy STAT-PAIR-001 --symbols QQQ,SPY --timeframe 1d --source alpaca --start 2019-01-01 --end 2025-01-01
```

## 4. Rank ideas fairly (the tournament)

The tournament runs each strategy through **walk-forward testing**: it tunes on one stretch
of history, then scores the strategy on the next stretch it has never seen, and repeats.
It also corrects for having tried many ideas (so luck is not mistaken for skill).

```powershell
apex tournament --strategies TF-SMA-001,BO-DONCH-001,MR-RSI2-001 --symbols SPY,QQQ --timeframe 1d --source alpaca --start 2015-01-01 --end 2025-01-01 --train-bars 500 --test-bars 125
apex leaderboard
```

Leave out `--strategies` to try every strategy that suits the market. Most ideas **fail**
the gates, and that is the system working: the "Why not" column on the Tournament tab says
which test each one missed (too few trades, not enough out-of-sample profit, too big a
drawdown, and so on). Only strategies that pass move on towards paper trading.

## 5. Practice (paper) trading

Paper accounts trade on simulated fills, with the same risk limits and emergency stop
("kill switch") a real account would have. They need paper mode switched on for the
command:

```powershell
$env:APEX_TRADING_MODE = "paper"
apex paper-create --name sma-spy --strategy TF-SMA-001 --symbol SPY --timeframe 1d --source alpaca
apex paper-run --name sma-spy
apex paper-compare --name sma-spy
```

`paper-run` processes any stored bars the account has not seen yet, so run it again after
each `apex update`. `paper-compare` checks that the paper account behaved like a backtest
of the same strategy over the same bars. One account can trade several markets with shared
cash: `--symbol SPY,QQQ,BTCUSDT`.

If the kill switch trips (too big a loss, a losing streak, or a reconciliation problem),
the account goes flat and stays stopped until a named person resets it:

```powershell
apex paper-reset-kill --name sma-spy --actor "Jake"
```

To run updates and paper accounts automatically on a timer, use `start-scheduler.bat`
(section 2), or see "Built-in scheduler" in [DEPLOYMENT.md](DEPLOYMENT.md).

## 6. Research library

Import notes, papers or transcripts you have the right to use (the licence note is
required so APEX records where each document came from), then search them:

```powershell
apex research-import C:\notes\my-notes.md --licence-note "my own notes"
apex research-search "opening range breakout"
```

## 7. What the numbers mean

Click a backtest's number on the Backtests tab to see all its figures with a one-line
meaning next to each. The main ones:

| Figure | Plain English |
|---|---|
| Total return | Gain or loss over the whole test. |
| Yearly growth | The steady yearly rate that would give the same total return. |
| Sharpe ratio | Return per unit of ups and downs. Above 1 is decent, but on its own it says nothing about luck. |
| Worst drop (max drawdown) | Largest fall from a high point before recovering. |
| Trades | Completed round trips. Under about 100 means the result could easily be luck. |
| Win rate | Share of trades that made money. |
| Profit factor | Money made on winners divided by money lost on losers. Above 1 means net profit. |
| Costs paid | Commission, spread, slippage and funding charged in the test. |
| Unseen-data return / Sharpe | How the strategy did on the stretches of history it was not tuned on (tournament and `apex validate` only). This matters far more than the plain backtest. |
| Kept after tuning | Unseen Sharpe as a share of tuning Sharpe. Low means the tuning mostly fitted noise. |
| Luck-adjusted confidence (deflated Sharpe) | Chance the edge is real after allowing for how many variations were tried. The tournament wants 95%. |
| Overfitting risk (PBO) | Chance the best-looking settings are just the luckiest. The tournament wants 50% or less. |

## 8. Save results for Excel

Any of these writes a CSV file you can open in Excel. It will not overwrite a file that
already exists unless you add `--force`.

```powershell
apex export experiments --out C:\apex-exports\backtests.csv
apex export leaderboard --out C:\apex-exports\tournament.csv
apex export bars --symbol SPY --timeframe 1d --source alpaca --out C:\apex-exports\spy.csv
apex export fills --name sma-spy --out C:\apex-exports\sma-spy-fills.csv
```

Every results row has a `simulated` column set to true, as a reminder that none of it is
real trading.

## 9. Back up your data

Everything APEX knows (prices, backtests, practice accounts) lives in one file, `apex.db`.
To save a copy, run this in the APEX folder (it is safe while the dashboard is open):

```powershell
apex backup
```

It writes a dated copy such as `backups\apex-backup-20261120-071500.db`, checks the copy
is readable, and never changes `apex.db`. Add `--keep 14` to keep only the newest 14
copies, or `--folder D:\apex-backups` to put them on another drive. `start-scheduler.bat`
also makes one every day (the `nightly-backup` job in `jobs.json`).

To restore: close APEX, rename `apex.db` to something like `apex-old.db`, copy the backup
you want into the APEX folder, rename it to `apex.db`, and run `apex db-upgrade` once.

## When something goes wrong

Start with a check-up. It only reads, never changes anything, and never shows your keys:

```powershell
apex doctor
```

Each line says OK, WARN or FAIL, and a FAIL tells you which command fixes it (for example
"run: apex db-upgrade").

- **`apex` is not recognised:** the environment is not switched on. Run
  `.venv\Scripts\activate` in the APEX folder first.
- **"set APEX_ALPACA_KEY_ID ...":** the Alpaca keys are missing from `.env`.
- **"paper commands need APEX_TRADING_MODE=paper":** run
  `$env:APEX_TRADING_MODE = "paper"` in that PowerShell window first.
- **A backtest says there are no bars:** check that `--source`, `--symbol` and
  `--timeframe` match what you ingested (the Overview tab lists stored data).
- **New features from GitHub:** your copy is a ZIP download, so download the latest ZIP,
  copy your `.env` and `apex.db` into the new folder, and run `apex db-upgrade` once.
