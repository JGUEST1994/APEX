#!/usr/bin/env bash
# Rank every built strategy on real, free price data (research only: nothing is traded).
#
# Downloads prices into the database named by APEX_DATABASE_URL, runs the tournament
# (walk-forward, trial-adjusted gates) per market group and timeframe, and writes the
# leaderboard, the shortlist and the raw tournament output to OUT (default: research-out/).
#
# RESEARCH_GROUPS picks the groups (default: all of them, run in this order):
#   crypto-1d crypto-1h crypto-5m stocks-1d stocks-5m stocks-15m fx-1h fx-30m
# Each group runs on the markets whose prices downloaded; a pair or basket needs every leg,
# so one failed download no longer skips a whole group.
#
# Crypto needs no account (Coinbase US-dollar books back to 2016; Binance 5-minute bars with
# buy volume). Daily US stocks come from Tiingo back to 2000 when APEX_TIINGO_API_KEY is set,
# else from Alpaca, whose free feed reaches back to about 2020. Intraday stocks need
# APEX_ALPACA_KEY_ID/SECRET; forex needs the Tiingo key. Tiingo's free plan allows about 50
# requests an hour: daily stocks take one each, an hourly forex pair about 8 from FX_START, a
# 30-minute pair about 16, and a download refused for that limit is tried once more an hour
# later (so a run of every group waits about an hour once).
# Used by .github/workflows/research.yml; also runs on a Mac or Linux machine.
set -u
OUT="${OUT:-research-out}"
END="${END:-$(date -u +%Y-%m-01)}"
ALL_GROUPS="crypto-1d crypto-1h crypto-5m stocks-1d stocks-5m stocks-15m fx-1h fx-30m"
RESEARCH_GROUPS="${RESEARCH_GROUPS:-$ALL_GROUPS}"
months_ago() { date -u -d "-$1 months" +%Y-%m-01 2>/dev/null || date -u -v-"$1"m +%Y-%m-01; }
FLOW_START="${FLOW_START:-$(months_ago 6)}"       # 5-minute bars
INTRADAY_START="${INTRADAY_START:-$(months_ago 24)}"  # 15-minute stock bars
FX_START="${FX_START:-2022-06-01}"
mkdir -p "$OUT"
log() { echo "== $*" | tee -a "$OUT/run.log"; }
want() { case " $RESEARCH_GROUPS " in *" $1 "*) return 0 ;; esac; return 1; }

GOT=""        # markets downloaded for the group being run
LAST_ERROR=""  # why the latest download failed
: >>"$OUT/ingest.log"
ingest() {  # provider symbol timeframe start
  local seen
  seen=$(wc -l <"$OUT/ingest.log")
  if apex ingest --provider "$1" --symbol "$2" --timeframe "$3" --start "$4" --end "$END" \
      >>"$OUT/ingest.log" 2>&1; then
    log "prices ok: $1 $2 $3 from $4"; GOT="$GOT $2"; return 0
  fi
  LAST_ERROR=$(tail -n +"$((seen + 1))" "$OUT/ingest.log" | grep -o 'error=.*' | tail -n 1 \
    | cut -c7-200)
  log "prices FAILED: $1 $2 $3 from $4: ${LAST_ERROR:-see ingest.log}"
  return 1
}

ingest_patiently() {  # like ingest, but waits out a free plan's hourly limit once
  ingest "$@" && return 0
  case "$LAST_ERROR" in *429*) ;; *) return 1 ;; esac
  log "waiting an hour for the free plan's hourly request limit"
  sleep 3660
  ingest "$@"
}

markets() {  # candidates (A, or A/B/... for pairs and baskets) whose every leg downloaded
  local out="" m leg ok
  for m in "$@"; do
    ok=1
    for leg in ${m//\// }; do
      case " $GOT " in *" $leg "*) ;; *) ok=0 ;; esac
    done
    [ $ok = 1 ] && out="$out,$m"
  done
  echo "${out#,}"
}

tour() {  # name source timeframe start train test candidates...
  local name=$1 src=$2 tf=$3 start=$4 train=$5 test=$6 symbols
  shift 6
  symbols=$(markets "$@")
  if [ -z "$symbols" ]; then log "tournament $name skipped: no prices"; return; fi
  log "tournament $name on $symbols"
  apex tournament --strategies all --only-suited --no-promote --source "$src" --timeframe "$tf" \
    --symbols "$symbols" --start "$start" --end "$END" --train-bars "$train" --test-bars "$test" \
    >"$OUT/tournament-$name.txt" 2>>"$OUT/run.log" || log "tournament $name FAILED"
  # also print it, so the results can be read from the job log as soon as each one finishes
  echo "----- tournament-$name.txt"; cat "$OUT/tournament-$name.txt"; echo "----- end"
}

apex db-upgrade >/dev/null 2>&1
log "groups: $RESEARCH_GROUPS (prices up to $END)"
for g in $RESEARCH_GROUPS; do
  case " $ALL_GROUPS " in *" $g "*) ;; *) log "unknown group $g (ignored); known: $ALL_GROUPS" ;; esac
done
CRYPTO="BTCUSDT ETHUSDT ETHUSDT/BTCUSDT"
STOCKS="SPY QQQ IWM AAPL MSFT NVDA AMZN"
# Daily only: the Dow and the nine original sector funds, markets the daily ideas were not
# first tried on (one Tiingo request each).
FUNDS="DIA XLB XLE XLF XLI XLK XLP XLU XLV XLY"
STOCK_SETS="$STOCKS $FUNDS QQQ/SPY MSFT/AAPL SPY/QQQ/IWM/AAPL/MSFT/NVDA/AMZN"

# --- crypto (Coinbase: long history; Binance: buy volume for the order-flow strategies)
if want crypto-1d; then
  GOT=""; for s in BTCUSDT ETHUSDT; do ingest coinbase $s 1d 2016-06-01; done
  tour crypto-1d coinbase 1d 2016-06-01 730 182 $CRYPTO
fi
if want crypto-1h; then
  GOT=""; for s in BTCUSDT ETHUSDT; do ingest coinbase $s 1h 2021-01-01; done
  tour crypto-1h coinbase 1h 2021-01-01 4000 1000 $CRYPTO
fi
if want crypto-5m; then
  GOT=""; for s in BTCUSDT ETHUSDT; do ingest binance $s 5m "$FLOW_START"; done
  tour crypto-5m binance 5m "$FLOW_START" 8000 2000 $CRYPTO
fi

# --- US stocks and ETFs
if want stocks-1d; then
  GOT=""
  if [ -n "${APEX_TIINGO_API_KEY:-}" ]; then
    for s in $STOCKS $FUNDS; do ingest_patiently tiingo $s 1d 2000-01-03; done
    tour stocks-1d tiingo 1d 2000-01-03 504 126 $STOCK_SETS
  elif [ -n "${APEX_ALPACA_KEY_ID:-}" ]; then
    for s in $STOCKS $FUNDS; do ingest alpaca $s 1d 2016-06-01; done
    tour stocks-1d alpaca 1d 2016-06-01 504 126 $STOCK_SETS
  else
    log "stocks-1d skipped: no Tiingo or Alpaca key"
  fi
fi
for tf in 5m 15m; do
  want stocks-$tf || continue
  if [ -z "${APEX_ALPACA_KEY_ID:-}" ]; then log "stocks-$tf skipped: no Alpaca keys"; continue; fi
  GOT=""
  if [ $tf = 5m ]; then start=$FLOW_START train=4000 test=1000
  else start=$INTRADAY_START train=2600 test=650; fi
  for s in SPY QQQ; do ingest alpaca $s $tf "$start"; done
  tour stocks-$tf alpaca $tf "$start" $train $test SPY QQQ QQQ/SPY
done

# --- forex (Tiingo free account)
if want fx-1h; then
  if [ -n "${APEX_TIINGO_API_KEY:-}" ]; then
    GOT=""; for s in EURUSD GBPUSD USDJPY AUDUSD USDCAD; do ingest_patiently tiingo $s 1h "$FX_START"; done
    tour fx-1h tiingo 1h "$FX_START" 4000 1000 EURUSD GBPUSD USDJPY AUDUSD USDCAD GBPUSD/EURUSD
  else
    log "fx-1h skipped: no Tiingo key"
  fi
fi
# 30-minute bars for the ideas built for them, among them the Asian range sweep (LIQ-ASIA-001),
# which needs 5- to 30-minute prices. The two London-session majors only: each 30-minute pair
# costs about 16 of Tiingo's ~50 requests an hour.
if want fx-30m; then
  if [ -n "${APEX_TIINGO_API_KEY:-}" ]; then
    GOT=""; for s in EURUSD GBPUSD; do ingest_patiently tiingo $s 30m "$FX_START"; done
    tour fx-30m tiingo 30m "$FX_START" 8000 2000 EURUSD GBPUSD
  else
    log "fx-30m skipped: no Tiingo key"
  fi
fi

apex export leaderboard --out "$OUT/leaderboard.csv" --force >>"$OUT/run.log" 2>&1
apex leaderboard >"$OUT/leaderboard.txt" 2>&1
apex shortlist --top 8 >"$OUT/shortlist.md" 2>&1
echo "----- leaderboard.csv"; cat "$OUT/leaderboard.csv"; echo "----- end"
echo "----- shortlist.md"; cat "$OUT/shortlist.md"; echo "----- end"
log "done"
