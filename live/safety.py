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

PAUSE_FILE = os.path.join("live", "PAUSE")


def kill_switch_active():
    """True if a `live/PAUSE` file exists. Create it (e.g. `touch
    live/PAUSE`) to stop the bot from opening new trades without killing
    the process - it keeps monitoring/journaling/reconciling existing
    trades. Delete the file to resume.
    """
    return os.path.exists(PAUSE_FILE)


def _trading_day_for(now_utc):
    """The 'trading day' label a timestamp belongs to, given
    DAILY_RESET_HOUR_UTC - e.g. with a reset hour of 0, this is just
    the UTC calendar date.
    """
    shifted = now_utc - timedelta(hours=DAILY_RESET_HOUR_UTC)
    return shifted.date().isoformat()


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


def daily_loss_breached(state, current_balance):
    day_start = state.get("day_start_balance")
    if not day_start:
        return False
    loss_pct = (day_start - current_balance) / day_start
    return loss_pct >= MAX_DAILY_LOSS_PCT


def is_new_candle(state, candle_time):
    return state.get("last_processed_candle") != str(candle_time)


def mark_candle_processed(state, candle_time):
    state["last_processed_candle"] = str(candle_time)
    return state
