#!/usr/bin/env bash
# Rank every built strategy on real, free price data (research only: nothing is traded).
#
# Downloads prices into the database named by APEX_DATABASE_URL, runs the tournament
# (walk-forward, trial-adjusted gates) per market group and timeframe, and writes the
# leaderboard and the raw tournament output to OUT (default: research-out/).
# Crypto needs no account. US stocks run only if APEX_ALPACA_KEY_ID/SECRET are set; forex
# only if APEX_TIINGO_API_KEY is set. A source that cannot be reached is skipped, not fatal.
# Used by .github/workflows/research.yml; also runs on a Mac or Linux machine.
set -u
OUT="${OUT:-research-out}"
END="${END:-$(date -u +%Y-%m-01)}"
mkdir -p "$OUT"
log() { echo "== $*" | tee -a "$OUT/run.log"; }

ingest() {  # provider symbol timeframe start
  if apex ingest --provider "$1" --symbol "$2" --timeframe "$3" --start "$4" --end "$END" \
      >>"$OUT/ingest.log" 2>&1; then
    log "prices ok: $1 $2 $3 from $4"; return 0
  fi
  log "prices FAILED: $1 $2 $3 (see ingest.log)"; return 1
}

tour() {  # name source timeframe symbols start train test
  log "tournament $1"
  apex tournament --strategies all --only-suited --no-promote --source "$2" --timeframe "$3" \
    --symbols "$4" --start "$5" --end "$END" --train-bars "$6" --test-bars "$7" \
    >"$OUT/tournament-$1.txt" 2>>"$OUT/run.log" || log "tournament $1 FAILED"
  # also print it, so the results can be read from the job log as soon as each one finishes
  echo "----- tournament-$1.txt"; cat "$OUT/tournament-$1.txt"; echo "----- end"
}

apex db-upgrade >/dev/null 2>&1

# --- crypto (Coinbase: long history; Binance: buy volume for the order-flow strategies)
ok=1
for s in BTCUSDT ETHUSDT; do
  ingest coinbase $s 1d 2016-06-01 || ok=0
  ingest coinbase $s 1h 2021-01-01 || ok=0
done
if [ $ok = 1 ]; then
  tour crypto-1d coinbase 1d BTCUSDT,ETHUSDT,ETHUSDT/BTCUSDT 2016-06-01 730 182
  tour crypto-1h coinbase 1h BTCUSDT,ETHUSDT,ETHUSDT/BTCUSDT 2021-01-01 4000 1000
fi
FLOW_START="${FLOW_START:-$(date -u -d '-6 months' +%Y-%m-01 2>/dev/null || date -u -v-6m +%Y-%m-01)}"
ok=1
for s in BTCUSDT ETHUSDT; do ingest binance $s 5m "$FLOW_START" || ok=0; done
[ $ok = 1 ] && tour crypto-5m binance 5m BTCUSDT,ETHUSDT,ETHUSDT/BTCUSDT "$FLOW_START" 8000 2000

# --- US stocks and ETFs (Alpaca free account)
STOCKS="SPY QQQ IWM AAPL MSFT NVDA AMZN"
if [ -n "${APEX_ALPACA_KEY_ID:-}" ]; then
  ok=1
  for s in $STOCKS; do ingest alpaca $s 1d 2016-06-01 || ok=0; done
  [ $ok = 1 ] && tour stocks-1d alpaca 1d \
    SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMZN,QQQ/SPY,MSFT/AAPL,SPY/QQQ/IWM/AAPL/MSFT/NVDA/AMZN \
    2016-06-01 504 126
  ok=1
  for s in SPY QQQ; do ingest alpaca $s 5m "$FLOW_START" || ok=0; done
  [ $ok = 1 ] && tour stocks-5m alpaca 5m SPY,QQQ,QQQ/SPY "$FLOW_START" 4000 1000
else
  log "stocks skipped: no Alpaca keys"
fi

# --- forex (Tiingo free account)
FX="EURUSD GBPUSD USDJPY AUDUSD USDCAD"
if [ -n "${APEX_TIINGO_API_KEY:-}" ]; then
  ok=1
  for s in $FX; do ingest tiingo $s 1h 2021-01-01 || ok=0; done
  [ $ok = 1 ] && tour fx-1h tiingo 1h EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,GBPUSD/EURUSD \
    2021-01-01 4000 1000
else
  log "forex skipped: no Tiingo key"
fi

apex export leaderboard --out "$OUT/leaderboard.csv" --force >>"$OUT/run.log" 2>&1
apex leaderboard >"$OUT/leaderboard.txt" 2>&1
echo "----- leaderboard.csv"; cat "$OUT/leaderboard.csv"; echo "----- end"
log "done"
