# Running the live bot

This turns the same strategy you already backtested into a bot that
watches the real market and places real trades on your prop firm's MT5
account, once an hour, only during London/NY killzone hours - across
all 10 pairs in `live/pairs.py` (AUD_USD, EUR_JPY, EUR_USD, GBP_JPY,
GBP_USD, NZD_USD, USD_CAD, USD_CHF, USD_JPY, XAU_USD), each running as
its own process on the same account.

**Before you do anything else:** confirm your prop firm's challenge
rules actually allow automated/EA trading. If they don't, none of this
should be pointed at the real challenge account.

## 1. Set up the Windows VPS

1. Finish setting up your AWS Windows instance (or whichever VPS you land on).
2. Remote into it (Windows shows this as "Remote Desktop Connection").
3. Install Python 3.11+ from python.org (check "Add to PATH" during install).
4. Install your prop firm's MT5 terminal on the VPS and log into your
   real challenge account inside it once, manually, to confirm the
   login works.

## 2. Copy the project onto the VPS

Copy this whole `trade-automation` folder onto the VPS (e.g. zip it up
on your Mac, copy the zip over Remote Desktop, unzip on the VPS). You
need at minimum: `backtest/`, `swing_structure/`, `live/`, and, for
every pair in `live/pairs.py`, `data/settings/<PAIR>/` +
`data/weights/<PAIR>/` (the bot reads the most recent year's files from
there).

Then, in a terminal on the VPS, inside the project folder:

```
pip install -r requirements-live.txt
```

## 3. Find your broker's exact symbol names and server time offset

1. Open MT5, look at the "Market Watch" panel - find each pair's exact
   symbol name (often just `EURUSD`, but some brokers add a suffix like
   `EURUSD.a` or `EURUSDm`). The bot derives each pair's symbol as the
   instrument name with the underscore stripped (`EUR_USD` -> `EURUSD`)
   plus whatever suffix you set in `MT5_SYMBOL_SUFFIX` below - confirm
   that combination matches what Market Watch shows for every one of
   the 10 pairs, not just EUR_USD.
2. Check what timezone the MT5 terminal's clock (bottom-right corner)
   is showing versus real UTC (search "UTC time now" online). Most
   brokers run 2 or 3 hours ahead of UTC. Note the difference in hours.
3. Open `live/mt5_connector.py` and set `BROKER_UTC_OFFSET_HOURS` to
   that number. Getting this wrong silently shifts every killzone
   decision, so double check it.

## 4. Set your credentials (environment variables, never hardcoded)

In the Windows terminal (PowerShell), before running the bot:

```
$env:MT5_LOGIN="your account number"
$env:MT5_PASSWORD="your account password"
$env:MT5_SERVER="your broker's server name, shown in MT5 login screen"
$env:MT5_SYMBOL_SUFFIX=""   # e.g. ".a" if your broker suffixes every symbol, blank otherwise
$env:TELEGRAM_BOT_TOKEN="from @BotFather"
$env:TELEGRAM_CHAT_ID="your numeric chat id"
```

These are shared across all 10 pairs' processes - there's no per-pair
`MT5_SYMBOL` override anymore, since `MT5_SYMBOL_SUFFIX` combined with
the instrument name covers it.

(See `live/alerts.py`'s docstring for how to get the Telegram token/chat id.)

## 5. Do a dry run first - one pair at a time

```
$env:DRY_RUN="true"
python live/run_live.py EUR_USD
```

This computes everything for real (reads live prices, checks signals,
calculates the exact lot size) but only sends you a Telegram message
saying what it *would* have done, instead of sending a real order.
Run this for every one of the 10 pairs in `live/pairs.py` in turn (not
just EUR_USD), confirming for each: the symbol resolves on your broker,
settings load with `threshold=55, tp_multiple=2.5` in the printed
settings line, and the lot size/SL/TP prices look sane - pay particular
attention to XAU_USD, since gold's margin and lot-step behavior differs
from the FX pairs. Leave each running for a bit and check the messages
make sense before moving to the next pair or turning it off.

## 6. Go live - via the supervisor, all 10 pairs at once

Once every pair's dry run looks right, use `live/supervisor.ps1` (see
below) rather than running `run_live.py` by hand - it starts one
process per pair, keeps each one restarted independently if it crashes,
and restarts all 10 whenever new code is pulled from GitHub. Set
`$env:DRY_RUN="false"` (and set it as a persistent System/User
environment variable, not just for one PowerShell session, so it's
still set whenever the Scheduled Task launches the supervisor) before
starting it for real.

Leave the supervisor running continuously (that's the whole point of
the VPS - it keeps going even when your Mac is off).

## Controlling it while it's running

- **Pause one pair without stopping it**: create an empty file named
  `PAUSE_<PAIR>` (e.g. `PAUSE_EUR_USD`) inside the `live/` folder. That
  pair's bot keeps monitoring and journaling its existing trades but
  won't open new ones - the other 9 pairs are unaffected. Delete the
  file to resume.
- **Stop completely**: Ctrl+C in the terminal running it (or close the
  window), or stop the supervisor's Scheduled Task, which also ends
  every pair's process.
- **Check what it's doing**: watch the Telegram messages, or open
  `data/journal/<PAIR>/<PAIR>_live_trades_<year>.csv` for the pair
  you're checking.
- **Daily loss breaker**: each pair tracks its own realized + floating
  P&L independently. If a single pair is down more than
  `MAX_DAILY_LOSS_PCT` (set in `live/safety.py`, default 2%) of the
  account's balance at the start of the trading day, only that pair's
  bot stops opening new trades until the next day, and messages you
  when this happens - the other pairs keep trading.

## Known limitations of this version

- Trades already open when a pair's bot restarts are picked up
  correctly by the reconciliation step, but if the VPS is offline when
  an H1 candle closes, that signal is simply missed (no catch-up logic)
  - reasonable for now since the VPS is meant to run 24/5, but worth
  knowing.
- The live journal's `max_r_reached` / `slb` columns are left blank
  (unlike the backtest journal) - only win/loss and timing are tracked
  for now.
- All 10 pairs share one account balance and one `MAX_DAILY_LOSS_PCT`
  reference point. The breaker is per-pair, but the account's overall
  risk still scales with however many pairs signal on the same day.


## M15 entry models (Phase 4)

The bot no longer sends market orders. Entries come from the M15 entry
models (LC-1, LC-2A, LC-2B, CE), which means:

- **The clock is M15, not H1.** `run_once` wakes on each new closed M15
  candle. Setups form and orders re-host on M15 boundaries, so waking only
  on the hour would place orders up to 45 minutes stale.
- **Orders REST at the broker.** A stop for the LC models, a limit for CE.
  The backtest's fill is the first M15 candle whose wick reaches the order
  price, and only a real pending order reproduces that. Market-ordering
  once the fill candle closed would enter at that candle's close, which
  can be most of the way to the stop.
- **The plan is recomputed every candle.** `live/pending_plan.py` compares
  what should be resting against what is, then places, keeps or cancels.
  Nothing is remembered and diffed forward, so a failed send or a missed
  fill self-corrects on the next poll instead of drifting permanently.
- **A price change is a replace, not a modify.** An LC order re-hosting
  moves its price and its stop together, and a partial modify would leave a
  live order with the wrong stop attached.

### What is NOT verified

`live/` imports MetaTrader5, which is Windows-only, so none of it runs on
the development machine. The decision logic was extracted into
`live/pending_plan.py` precisely so it could be tested without MT5, and it
is (`tests/test_pending_plan.py`). Everything else in this directory,
including every `order_send` call, has only ever been read, not executed.

Run with `DRY_RUN=true` first. It exercises the whole path including the
plan, and alerts what it would have rested, without sending anything.

### Two open findings from the backtest

Both are in `roadmap/m15-entry-plan.md` and neither is resolved:

- **LC-1 loses money** on the EUR_USD 2020-2025 walk-forward: -0.451 R per
  trade over 50 candidates, against +0.29 for LC-2A and +0.27 for CE. It is
  still enabled.
- **Longs underperform shorts** across every model. The asymmetry predates
  the entry layer, but the entry layer amplifies it.
