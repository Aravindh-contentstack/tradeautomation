"""Non-negotiable guardrails for running the strategy with real order
placement: a manual kill switch, a duplicate-candle guard (never
evaluate the same closed candle twice), and a daily loss circuit
breaker.

DAILY_RESET_HOUR_UTC and MAX_DAILY_LOSS_PCT are both things to verify
against your specific prop firm's actual rulebook before going live:
- Prop firms measure "daily" loss against their own reset time (often
  00:00 UTC, but some use 00:00 server/New York time) - confirm yours
  and adjust DAILY_RESET_HOUR_UTC if it differs.
- MAX_DAILY_LOSS_PCT should sit comfortably BELOW the firm's actual
  daily loss limit (e.g. if the firm disqualifies you at 5% down,
  tripping this breaker at 2-3% leaves margin for a trade that's still
  open and moving against you when the breaker checks).
"""

import json
import os
from datetime import datetime, timedelta, timezone

DAILY_RESET_HOUR_UTC = 0  # VERIFY against your prop firm's actual daily reset time.
MAX_DAILY_LOSS_PCT = 0.02  # VERIFY this sits below your prop firm's real daily loss limit.

# A second, ACCOUNT-WIDE ceiling, checked once per sweep across every pair
# combined rather than per magic number. MAX_DAILY_LOSS_PCT alone caps each
# pair's own loss at 2%, but does nothing to stop several correlated pairs
# (e.g. several JPY crosses) from stopping out the same day and adding up to
# far more than that on the account as a whole. This matters more now that
# RISK_PER_TRADE is 1% instead of 0.2% - five pairs losing on the same day is
# 5% down with nothing to stop it otherwise. VERIFY this sits below your prop
# firm's real daily loss limit, same as MAX_DAILY_LOSS_PCT.
MAX_ACCOUNT_DAILY_LOSS_PCT = 0.03

# The prop firm's real max daily loss is 5%. At 1% risked per trade, 5
# simultaneous trades all hitting stop the same day would exactly consume
# it - no margin for a trade still open and moving against the account when
# the breaker below (which reacts to REALIZED plus floating loss, not to
# trade COUNT) finally catches up. Capping at 4 rather than 5, per user
# decision (2026-09-02), leaves that margin. Counts open positions AND
# resting pending orders together, since a resting order becomes a real
# position - and real risk - the instant price reaches it, with the bot
# never consulted in between.
MAX_CONCURRENT_TRADES = 4

# ...and no more than this many of those slots from any ONE instrument.
# Without it, the cap above is satisfied by four trades on the same
# symbol, which is one directional bet at 4% risk rather than the
# diversified book the cap was written to protect - the same correlation
# argument MAX_ACCOUNT_DAILY_LOSS_PCT is built on. live/pending_plan.py's
# one_per_zone already limits one signal per order block, but a single
# instrument can produce several order blocks in one sweep.
MAX_TRADES_PER_INSTRUMENT = 1


def pause_file(instrument):
    return os.path.join("live", "PAUSE_%s" % instrument)


def kill_switch_active(instrument):
    """True if a `live/PAUSE_<INSTRUMENT>` file exists. Create it (e.g.
    `touch live/PAUSE_EUR_USD`) to stop that pair's bot from opening new
    trades without killing the process - it keeps monitoring/journaling/
    reconciling its existing trades. Delete the file to resume. Each
    pair's process only ever checks its own pause file, so pausing one
    pair never affects the other 9.
    """
    return os.path.exists(pause_file(instrument))


def _trading_day_for(now_utc):
    """The 'trading day' label a timestamp belongs to, given
    DAILY_RESET_HOUR_UTC - e.g. with a reset hour of 0, this is just
    the UTC calendar date.
    """
    shifted = now_utc - timedelta(hours=DAILY_RESET_HOUR_UTC)
    return shifted.date().isoformat()


def trading_day_start_utc(now_utc):
    """The UTC instant the current trading day began, per
    DAILY_RESET_HOUR_UTC - the lower bound callers pass to
    mt5_connector.today_realized_profit() so each pair's own realized
    P&L is scoped to just today, not its whole trade history.
    """
    shifted = now_utc - timedelta(hours=DAILY_RESET_HOUR_UTC)
    day_start_shifted = datetime(shifted.year, shifted.month, shifted.day, tzinfo=timezone.utc)
    return day_start_shifted + timedelta(hours=DAILY_RESET_HOUR_UTC)


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"last_processed_candle": None, "trading_day": None, "day_start_balance": None, "breach_alerted": False}


def save_state(state, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def roll_daily_state(state, current_balance):
    """Resets the day's starting balance whenever the trading day
    (per DAILY_RESET_HOUR_UTC) has changed since the last check.
    Mutates and returns state.
    """
    today = _trading_day_for(datetime.now(timezone.utc))
    if state.get("trading_day") != today:
        state["trading_day"] = today
        state["day_start_balance"] = current_balance
        state["breach_alerted"] = False
    return state


def daily_loss_breached(state, pair_pnl_today, threshold=MAX_DAILY_LOSS_PCT):
    """Breached when today's P&L (realized + floating) is down more than
    `threshold` of the account's day-start balance.

    Called two ways: per-pair, with pnl scoped to one magic number and
    threshold=MAX_DAILY_LOSS_PCT, so one pair losing money doesn't trip the
    breaker for the others sharing the account; and once per sweep with pnl
    summed across every pair and threshold=MAX_ACCOUNT_DAILY_LOSS_PCT, to
    catch several pairs losing together on the same day.
    """
    day_start = state.get("day_start_balance")
    if not day_start:
        return False
    loss_pct = -pair_pnl_today / day_start
    return loss_pct >= threshold


def is_new_candle(state, candle_time):
    return state.get("last_processed_candle") != str(candle_time)


def mark_candle_processed(state, candle_time):
    state["last_processed_candle"] = str(candle_time)
    return state
