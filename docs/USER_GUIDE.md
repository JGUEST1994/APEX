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

**Fastest first run:** one command sets up the database, adds the markets, downloads about
three years of free daily Bitcoin prices from Coinbase (no account needed) and runs one
example backtest, so the dashboard has something to show:

```powershell
apex quickstart
```

It is safe to run again: it only downloads days that are missing. For another free market,
add for example `--symbol ETHUSDT`.

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
| Strategy lab | Every strategy APEX can test and how far through testing it is; click one to see its rules and every step it has passed, who moved it and why |
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
than for long history. Coinbase and Binance can fetch long history. Coinbase prices
BTCUSDT and ETHUSDT from its US-dollar books (BTC-USD, ETH-USD), which go back to 2016;
its Tether (USDT) books only started in 2021.

Binance also reports how much of each bar's volume came from buyers who were in a hurry
(they bought at the asking price). APEX keeps that as "buy volume", and the order-flow
strategies OF-DELTA-001 and OF-ABSORB-001 need it, so use Binance (1m or 5m) for them. Bars
you downloaded before this was added don't have it; download a fresh date range to get it.

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

Alpaca's free plan serves the IEX exchange's prices, which only reach back to about 2020.
For longer daily stock history use Tiingo (below), which goes back decades.

**US stocks and forex from Tiingo (free key):** sign up at tiingo.com, copy your API token
from your account page and add it to `.env`:

```
APEX_TIINGO_API_KEY=your-token
```

Then, for daily stock prices (adjusted for splits and dividends) or hourly forex:

```powershell
apex ingest --provider tiingo --symbol SPY --timeframe 1d --start 2015-01-01 --end 2025-01-01
apex ingest --provider tiingo --symbol EURUSD --timeframe 1h --start 2021-01-01 --end 2025-01-01
```

The free plan allows about 50 requests an hour and 1,000 a day, which is plenty for daily
prices; long minute-by-minute forex downloads may need spreading over a few hours. The data
is for your own use only; don't share it. For daily forex, download 1h and use
`apex resample`.

**Your own files:** save a CSV as `data\csv\<SYMBOL>_<timeframe>.csv` (for example
`data\csv\EURUSD_1h.csv`) and use `--provider csv`.

**Forex from Dukascopy (free):** on Dukascopy's Historical Data Export page, pick the
pair, the timeframe and "GMT" time, download the CSV yourself, save it as for example
`data\csv\EURUSD_1h.csv`, then:

```powershell
apex ingest --provider dukascopy-csv --symbol EURUSD --timeframe 1h --start 2020-01-01 --end 2025-01-01
```

APEX reads Dukascopy's day-first dates and drops the flat weekend filler bars it writes.
Dukascopy's website rules forbid automated downloading without their written permission,
so APEX does not download from Dukascopy for you; files you download yourself for personal,
non-commercial use are fine. Don't share the files or put them in a public place.

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
The **Automatic jobs** box on the dashboard's Overview shows when each job last ran and
whether it had problems. A job marked **late** has not run for more than twice its interval:
usually the scheduler window was closed or the computer went to sleep.

## 3. Test one idea (a backtest)

See which strategies exist with `apex strategies`, or the Strategy lab tab. Then:

```powershell
apex backtest --strategy TF-SMA-001 --symbol SPY --timeframe 1d --source alpaca --start 2019-01-01 --end 2025-01-01
```

`--source` must match the provider you downloaded the data with. The result prints in the
window and is saved to the Backtests tab, where clicking its number shows a chart of the
simulated account value and every figure explained. Change a setting with `--param`, for example
`--param fast=10 --param slow=50`. A good-looking backtest on its own proves very little;
the next step is the real test.

**Strategies that trade two or more markets together** (pairs, baskets, cross-asset) use
`backtest-pair`:

```powershell
apex backtest-pair --strategy STAT-PAIR-001 --symbols QQQ,SPY --timeframe 1d --source alpaca --start 2019-01-01 --end 2025-01-01
```

**Earnings-day idea (VWAP-EARN-001)** needs each company's earnings dates. APEX gets them
free from the US regulator's EDGAR website. The SEC asks automated tools to say who is
asking, so first add your name and email to `.env`:

```
APEX_SEC_USER_AGENT="Your Name you@example.com"
```

Then save the dates and use them in a backtest:

```powershell
apex earnings-dates --symbol AAPL
apex backtest --strategy VWAP-EARN-001 --symbol AAPL --timeframe 1d --source tiingo --start 2019-01-01 --end 2025-01-01 --param anchors=@data/events/AAPL_earnings.json
```

APEX reads every page of the company's EDGAR filings, so the dates reach back to 2003,
when results releases got their own filing item; bars before the first date simply don't
trade.

## 4. Rank ideas fairly (the tournament)

The tournament runs each strategy through **walk-forward testing** with its standard
settings, fixed in advance, so nothing is tuned to the prices: history is cut into
stretches, and the strategy is scored on each stretch using only what came before it. It
also corrects for having tried many ideas and markets (so luck is not mistaken for skill),
judging that on all the unseen history, so more history helps a real edge show through.

```powershell
apex tournament --strategies TF-SMA-001,BO-DONCH-001,MR-RSI2-001 --symbols SPY,QQQ --timeframe 1d --source alpaca --start 2015-01-01 --end 2025-01-01 --train-bars 500 --test-bars 125
apex leaderboard
```

Leave out `--strategies` to try every strategy that suits the market and timeframe
(strategies meant for other markets are skipped). Most ideas **fail**
the gates, and that is the system working: the "Why not" column on the Tournament tab says
which test each one missed (too few trades, not enough out-of-sample profit, too big a
drawdown, and so on). Click a strategy's name there to see every figure from that run,
explained. Only strategies that pass move on towards paper trading.

To rank everything on real prices in one go, run `scripts/research-run.sh` (Mac or Linux),
or on GitHub open the Actions tab, pick **research-run** and press **Run workflow**. It
downloads free crypto prices (plus US stocks and forex if your Alpaca and Tiingo keys are
set) and saves the leaderboard and shortlist in `research-out/`. Daily US stocks come from
Tiingo back to 2000 when its key is set. To run only some market groups, list them in
`RESEARCH_GROUPS` (or the workflow's *groups* box), for example `stocks-1d fx-1h`; the
groups are `crypto-1d crypto-1h crypto-5m stocks-1d stocks-5m stocks-15m fx-1h fx-30m`. A
market whose download fails is left out and the rest of its group still runs. Tiingo's free
plan allows about 50 requests an hour, so a run of every group pauses for about an hour once.
The earnings-day idea runs only when `APEX_SEC_USER_AGENT` is set (on GitHub: a repository
secret with that name, holding your own name and email); it then fetches each company's
earnings dates and tests the idea on Apple, Microsoft, Nvidia and Amazon.

Each result also shows what simply holding the same market made over the same unseen
stretches, so you can see whether a strategy did better than buying and waiting.

`apex shortlist` turns the latest results into a short, plain-English list: the strategies
that passed every test (the ones worth a 30-day practice run), the nearest misses, and how
many failed each test.

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
cash: `--symbol SPY,QQQ,BTCUSDT`. `--param name=value` (repeatable) changes one of the
strategy's settings, for example `--param gross=0.38` to run a fund basket smaller.

**For a 30-day practice run, add `--start-now`.** Without it, the account's first run
replays every price already stored, and if the kill switch trips during that replay (a
losing streak somewhere in years of history is likely), the practice record counts it and
can never be ready. With `--start-now` the account trades only prices that arrive after you
create it; the stored history just warms the strategy up, and the comparison with the
backtest covers only the account's own days. Download a few years of prices first, so the
strategy has history to warm up on.

**Safety limits for one account.** Every account gets the standard safety limits (for
example at most 10 open positions, at most 40% of the money in markets that move together,
and the kill switch after 6 losing trades in a row or a 10% fall). `--limit name=value`
(repeatable) changes one for this account only, for example `--limit max_positions=13` so a
13-fund basket can hold every fund at once. A tighter limit needs nothing more. A looser one
is your decision, so APEX asks for your name with `--by "Your Name"` and records it, with the
old and new values, in the audit log. The practice record, its page on the dashboard and any
approval then list every limit that differs from the standard. The names are those in
`src/apex/risk/limits.py`.

Before you trust a strategy, it needs a practice record: `apex paper-record --name sma-spy`
says READY only after at least 30 days of new prices processed since the account was
created (old history replayed into a new account doesn't count), with the account still
running, no kill-switch trips or halts, and practice trades matching the backtest.
Otherwise it lists what is missing. It only reports; it never approves anything.

When it says READY, and the strategy has passed the tournament (see section 4), you can
approve it:

```powershell
apex strategy-approve --account sma-spy --by "Your Name" --reason "why you are happy with it"
```

APEX refuses if the record isn't READY, if the strategy hasn't passed the tournament, or if
its code has changed since. Your name, the reason and the evidence go into the log.
Approving a strategy does not switch on any real-money trading; APEX has none.

On the dashboard's Practice trading tab, click an account's name to see how its simulated
value moved, whether it matched the backtest, and its recent fills.

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
| Just holding the market | What simply buying and holding the market made over the same unseen stretches (single markets only; the Tournament tab's **Just holding** column). A strategy should beat it, or come close with smaller drops. |
| Kept after tuning | Unseen Sharpe as a share of tuning Sharpe. Low means the tuning mostly fitted noise. |
| Luck-adjusted confidence (deflated Sharpe) | Chance the edge is real after allowing for how many tries were made (every setting tried on every market counts as a try). It is judged on all the unseen history, so more history helps a real edge pass and makes luck easier to spot. The tournament wants 95%. |
| Overfitting risk (PBO) | Chance the best-looking settings are just the luckiest. The tournament wants 50% or less. |

## 8. Save results for Excel

On the dashboard, the Backtests and Tournament tabs, each market on the Markets tab, and
each practice account have a **Download for Excel** link. You can also make the same files
from the command line.

Each of these writes a CSV file you can open in Excel. It will not overwrite a file that
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
also makes one every day (the `nightly-backup` job in `jobs.json`). `apex doctor` (and the
Check-up card on the dashboard) warns when there is no backup in `backups` from the last
week.

To restore: close APEX, rename `apex.db` to something like `apex-old.db`, copy the backup
you want into the APEX folder, rename it to `apex.db`, and run `apex db-upgrade` once.

## When something goes wrong

Start with a check-up. It only reads, never changes anything, and never shows your keys:

```powershell
apex doctor
```

Each line says OK, WARN or FAIL, and a FAIL tells you which command fixes it (for example
"run: apex db-upgrade"). It also warns when downloaded prices are more than a week out of
date, or when there is no recent backup.

- **`apex` is not recognised:** the environment is not switched on. Run
  `.venv\Scripts\activate` in the APEX folder first.
- **"set APEX_ALPACA_KEY_ID ...":** the Alpaca keys are missing from `.env`.
- **"paper commands need APEX_TRADING_MODE=paper":** run
  `$env:APEX_TRADING_MODE = "paper"` in that PowerShell window first.
- **A backtest says there are no bars:** check that `--source`, `--symbol` and
  `--timeframe` match what you ingested (the Overview tab lists stored data).
- **New features from GitHub:** your copy is a ZIP download, so download the latest ZIP,
  copy your `.env` and `apex.db` into the new folder, and run `apex db-upgrade` once.
