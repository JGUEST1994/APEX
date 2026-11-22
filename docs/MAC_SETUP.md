# Running APEX on a Mac

This guide sets APEX up on a Mac that stays on at home, so it can keep prices up to date
and run practice (paper) trading for weeks. APEX never trades real money: it has no live
trading mode.

You will end up with two windows that stay open:

- **APEX dashboard** (`start-apex.command`): the website at http://127.0.0.1:8000/ui/
- **APEX scheduler** (`start-scheduler.command`): downloads new prices on a timer, runs
  practice accounts, and makes nightly backups

## 1. Install Python (once)

1. Go to https://www.python.org/downloads/macos/ and download the latest **Python 3.12**
   (or newer) "macOS 64-bit universal2 installer".
2. Open the downloaded file and click through the installer.

APEX needs Python 3.11 or newer. The `python3` that comes with Apple's developer tools is
too old, so install it from python.org even if you already have a `python3`.

## 2. Get APEX (once)

1. On the APEX GitHub page click **Code**, then **Download ZIP**.
2. Unzip it. Safari usually does this for you.
3. Move the `APEX-main` folder into your **home** folder: in Finder, Go, then Home. Use your
   home folder, not Desktop, Documents or iCloud Drive, because macOS asks for extra
   permissions in those folders and iCloud can move files away.

## 3. First start

1. Open **Terminal**: press Cmd+Space, type Terminal and press Return.
2. Type these two lines, pressing Return after each:

   ```bash
   cd ~/APEX-main
   bash start-apex.command
   ```

The first run takes a few minutes while it installs APEX into a `.venv` folder. Then the
dashboard opens in your browser. Leave the Terminal window open, because closing it stops
APEX.

**Later you can just double-click `start-apex.command` in Finder.** The first time, macOS may
say it "cannot be opened because it is from an unidentified developer". If so, right-click
the file, choose **Open**, then confirm **Open**. macOS remembers the choice after that.

## 4. Your settings and keys (optional)

1. In Terminal, in the `APEX-main` folder, run `cp .env.example .env` and then `open -e .env`.
2. Add any free keys you have, such as Alpaca for US stocks and Tiingo for forex. Never share
   this file.
3. **Practice trading only:** when you are ready to start the 30-day practice runs, change
   `APEX_TRADING_MODE=research` to `APEX_TRADING_MODE=paper`. Paper mode is still simulated
   money only.
4. Save, then close and reopen the APEX windows so they pick up the change.

## 5. Keep prices up to date (the scheduler)

1. Double-click `start-scheduler.command`. Or, in Terminal: `bash start-scheduler.command`.
2. The first time, it creates `jobs.json` and stops. Open it with `open -e jobs.json`, choose
   your markets, and save. The example keeps BTC and ETH up to date and backs up every night.
3. Double-click `start-scheduler.command` again and leave that window open too.
4. To check it is still working later, look at **Automatic jobs** on the dashboard's
   Overview page. Any job marked **late** means the scheduler stopped or the Mac slept.

## 6. Stop the Mac going to sleep

While APEX is running, both start files keep the Mac awake with macOS's own `caffeinate`
tool, so it won't fall asleep from inactivity. The screen can still turn off, which is fine.

These settings help as well:

- **MacBook:** keep it plugged in. Open System Settings, then **Battery**, then **Options**,
  and turn on **"Prevent automatic sleeping on power adapter when the display is off"**.
  Closing the lid still puts a MacBook to sleep, so leave it open. The screen can be off.
- **iMac / Mac mini / Mac Studio:** open System Settings, then **Energy**, and turn on
  **"Prevent automatic sleeping when the display is off"**.
- **Start after a restart:** open System Settings, then **General**, then **Login Items**.
  Press **+** and add `start-apex.command` and `start-scheduler.command`. After a power cut
  or a macOS update, they start again when you log in.
- **Updates:** macOS updates can restart the Mac overnight. Login Items cover that. You can
  also turn off automatic installs under General, then Software Update, then the ⓘ next to
  Automatic updates.

## 7. Stopping APEX

Click into a window and press **Ctrl+C**, or simply close the window. Practice accounts pick
up where they left off next time, because they only process prices they haven't seen.

## 8. Updating APEX

1. Close both APEX windows.
2. Download the new ZIP and unzip it.
3. Copy these from the old folder into the new one: `apex.db` (all your data), `.env`,
   `jobs.json`, and the `data` and `backups` folders if you have them.
4. Double-click `start-apex.command` in the new folder. It sets itself up again and upgrades
   the database.

## 9. Start the fund basket's 30-day practice run

This starts the practice run of the US fund basket (MR-IBS-BASKET-001 on 13 US index and
sector funds) at about 40% of the money, allowed to hold all 13 funds at once. It trades
simulated money only. You need APEX set up as in sections 1 to 3 (from a ZIP downloaded on
22 November 2026 or later), a free Tiingo key (on tiingo.com, sign in, then Account, then
API, then Token), and the Mac kept awake (section 6).

1. **Settings.** In Terminal, in the APEX folder, run `open -e .env` (if it says there is no
   such file, run `cp .env.example .env` first). Make these two lines read as below, with
   your own Tiingo key and no `#` in front, then save:

   ```
   APEX_TRADING_MODE=paper
   APEX_TIINGO_API_KEY=your-tiingo-key
   ```

   Close the APEX dashboard window and start it again, so it picks up the change.

2. **Download the 13 funds' prices (once).** In Terminal:

   ```bash
   cd ~/APEX-main
   source .venv/bin/activate
   apex update --provider tiingo --symbols SPY,QQQ,IWM,DIA,XLB,XLE,XLF,XLI,XLK,XLP,XLU,XLV,XLY --timeframe 1d --backfill-start 2000-01-03
   ```

   It takes a minute or two and prints a line for each fund. These older prices only warm
   the strategy up. Nothing is traded on them.

3. **Create the practice account (once).** In the same Terminal, with your own name after
   `--by`:

   ```bash
   apex paper-create --name fund-basket --strategy MR-IBS-BASKET-001 --symbol SPY,QQQ,IWM,DIA,XLB,XLE,XLF,XLI,XLK,XLP,XLU,XLV,XLY --source tiingo --start-now --param gross=0.38 --limit max_positions=13 --by "Your Name"
   ```

   It should answer with two lines like these (the date is the last trading day it has
   prices for):

   ```
   created paper account fund-basket (SIMULATED), trading prices after 2026-11-20 00:00 UTC
   safety limits for this account only: max_positions 13 (standard 10); set by Your Name, recorded in the audit log
   ```

   `--start-now` makes it trade only prices that arrive from now on. `--param gross=0.38`
   holds about 38% of the money, and `--limit max_positions=13` lets this account hold all 13
   funds at once. Every other safety rule stays as standard.

4. **Keep it running.** In Terminal, run `cp jobs.practice.example.json jobs.json`. This
   replaces any `jobs.json` you already have, and keeps the nightly backup. Then double-click
   `start-scheduler.command` and leave its window open. Every 4 hours it downloads any new
   daily prices for the 13 funds and runs the practice account on them. Keep both APEX
   windows open, and the Mac awake, for the whole 30 days. If the Mac was off for a while,
   start both windows again: the account catches up on the days it missed.

5. **Check on it.** On the dashboard, open **Practice trading** and click **fund-basket** to
   see its simulated value, whether it matches the backtest, its trades and its practice
   record. The first trades come after the next US trading day's prices arrive. After 30
   days, `apex paper-record --name fund-basket` (or the same page) says whether the record is
   READY.

6. **If the kill switch trips.** After 6 losing trades in a row, a 10% fall or a 2% fall in
   one day, the account sells everything, stops, and the dashboard shows the kill switch as
   ON. The practice record can't be READY after that, so don't just reset it: look at what
   happened first, and the usual next step is a fresh 30 days under a new account name.
   Replaying this basket over 2002 to 2026 stopped it in about 1 month in 5.

## If something goes wrong

- **"APEX needs Python 3.11 or newer"**: do step 1, then try again.
- **"Address already in use"**: APEX is already running in another window.
- **The dashboard doesn't open**: go to http://127.0.0.1:8000/ui/ in your browser.
- **Anything else**: run `source .venv/bin/activate` then `apex doctor` in the APEX folder.
  It checks the setup and says what is missing.
