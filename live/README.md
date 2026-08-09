# Running the live bot

This turns the same EUR/USD strategy you already backtested into a bot
that watches the real market and places real trades on your prop firm's
MT5 account, once an hour, only during London/NY killzone hours.

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
need at minimum: `backtest/`, `swing_structure/`, `live/`, and
`data/settings/EUR_USD/` + `data/weights/EUR_USD/` (the bot reads the
most recent year's files from there).

Then, in a terminal on the VPS, inside the project folder:

```
pip install -r requirements-live.txt
```

## 3. Find your broker's exact symbol name and server time offset

1. Open MT5, look at the "Market Watch" panel - find EUR/USD's exact
   symbol name (often just `EURUSD`, but some brokers add a suffix like
   `EURUSD.a` or `EURUSDm`). You'll need this exact spelling.
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
$env:MT5_SYMBOL="EURUSD"   # or whatever you found in step 3
$env:TELEGRAM_BOT_TOKEN="from @BotFather"
$env:TELEGRAM_CHAT_ID="your numeric chat id"
```

(See `live/alerts.py`'s docstring for how to get the Telegram token/chat id.)

## 5. Do a dry run first

```
$env:DRY_RUN="true"
python live/run_live.py
```

This computes everything for real (reads live prices, checks signals,
calculates the exact lot size) but only sends you a Telegram message
saying what it *would* have done, instead of sending a real order.
Leave this running for a day or two and check the messages make sense
(right direction, sane lot size, sane SL/TP prices) before turning it off.

## 6. Go live

```
$env:DRY_RUN="false"
python live/run_live.py
```

Leave this running continuously (that's the whole point of the VPS -
it keeps going even when your Mac is off).

## Controlling it while it's running

- **Pause without stopping it**: create an empty file named `PAUSE`
  inside the `live/` folder. The bot keeps monitoring and journaling
  existing trades but won't open new ones. Delete the file to resume.
- **Stop completely**: Ctrl+C in the terminal running it (or close the window).
- **Check what it's doing**: watch the Telegram messages, or open
  `data/journal/EUR_USD/EUR_USD_live_trades_<year>.csv`.
- **Daily loss breaker**: if the account is down more than
  `MAX_DAILY_LOSS_PCT` (set in `live/safety.py`, default 2%) from the
  start of the trading day, the bot stops opening new trades until the
  next day automatically, and messages you when this happens.

## Known limitations of this first version

- Only EUR_USD, only the market-structure strategy you've already backtested.
- Trades already open when the bot restarts are picked up correctly by
  the reconciliation step, but if the VPS is offline when an H1 candle
  closes, that signal is simply missed (no catch-up logic) - reasonable
  for now since the VPS is meant to run 24/5, but worth knowing.
- The live journal's `max_r_reached` / `slb` columns are left blank
  (unlike the backtest journal) - only win/loss and timing are tracked
  for now.
