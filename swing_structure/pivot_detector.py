"""Daily ATR-based zigzag pivot detector, for the internal tier.

Sibling to swing_structure/detector.py's compute_daily_swing_structure
(same shape: takes an OHLC DataFrame, returns swing_high, swing_low,
high_event, low_event), but powered by a third mechanism (this file's
second, after a fixed-length fractal window, see "Mechanism history"
below): a classic ATR-based zigzag.

Mechanism history: the first version of this file used a fixed pivot_len
(candles required on each side of a candle to confirm it as a fractal
pivot). Testing showed this doesn't fit both trending and ranging
conditions with one number: a fast trend's pullbacks are often shorter,
in candle-count terms, than a fixed pivot_len, so a real, meaningful
retracement could fail to ever confirm as a pivot (a synthetic uptrend
with 3-day pullbacks and pivot_len=8 never confirmed a single high-side
pivot in 90 candles, since every pullback recovered before 8 candles
could pass on both sides of it).

This version fixes that by making the confirmation threshold track
volatility directly: instead of waiting a fixed number of candles, a
pivot confirms as soon as price has reversed from the running extreme by
more than reversal_multiplier x ATR. A sharp, volatile reversal confirms
quickly. A shallow, slow one takes longer, exactly like a real trader
reading "has this actually turned, or is it just noise" using volatility
as the yardstick, rather than a fixed clock.

One structural difference from the previous version worth calling out:
the two sides are no longer fully independent. A true zigzag alternates,
only one side is "live" (accumulating toward its next reversal) at a
time, the other side just holds its last confirmed value until the
zigzag swings back to it. This is what makes staleness structurally
impossible rather than something to tune away: the live side, by
definition, cannot go stale, and the other side simply hasn't had its
turn yet.

ATR itself uses Wilder's smoothing (the traditional formula most
platforms default to): the first value is a simple average of the first
atr_period True Range values, every value after that blends the
previous ATR with today's True Range, weighted 1/atr_period.
"""

import pandas as pd


def compute_pivot_swing_structure(df, atr_period=14, reversal_multiplier=1.5, manual_restart=None):
    """Computes the ATR-based zigzag swing high/low pair for every row.

    df: DataFrame of Daily OHLC candles with columns date, open, high,
        low, close, in ascending date order.
    atr_period: how many True Range values feed Wilder's ATR (default 14,
        the standard default almost everywhere).
    reversal_multiplier: how many ATRs of pullback from the running
        extreme count as a genuine reversal, confirming it as a pivot
        (default 1.5, a common moderate zigzag starting point).
    manual_restart: optional boolean Series, same length and order as
        df. A one-time trigger on the exact candle it flips from False to
        True: immediately resets both sides to today's high/low and
        restarts the zigzag from scratch (direction undetermined again),
        bypassing whatever reversal was in progress.

    Returns a copy of df with four new columns:
        swing_high, swing_low: the current confirmed pivot on each side,
            as of that day (None before that side's first pivot is ever
            confirmed).
        high_event, low_event: plain-English reason for that day, on
            that side. One of "warming up" (ATR not ready yet, or this
            side has never been confirmed), "initial seed" (this side's
            first-ever pivot is confirmed), "break of swing high"
            (high_event only) / "break of swing low" (low_event only),
            "manual restart", or None for a day the pivot silently
            updates to a fresh confirmation, or an ordinary day where
            nothing happens.
    """
    n = len(df)
    df = df.reset_index(drop=True)

    if manual_restart is None:
        manual_restart = pd.Series([False] * n)
    else:
        manual_restart = manual_restart.reset_index(drop=True)

    swing_high_col = [None] * n
    swing_low_col = [None] * n
    high_event_col = [None] * n
    low_event_col = [None] * n

    # State carried from one candle to the next as we walk forward.
    atr = None
    tr_values = []  # only accumulated until atr_period is reached

    direction = None  # None (bootstrap), "up", or "down"
    running_extreme = None
    running_extreme_index = None
    # During bootstrap (direction is None) both sides are tracked at once,
    # since we don't yet know which one will confirm first.
    bootstrap_high = None
    bootstrap_high_index = None
    bootstrap_low = None
    bootstrap_low_index = None

    swing_high = None
    swing_high_index = None
    high_crossed = False
    swing_low = None
    swing_low_index = None
    low_crossed = False

    for i in range(n):
        high_today = df["high"].iloc[i]
        low_today = df["low"].iloc[i]
        close_today = df["close"].iloc[i]

        manual_today = bool(manual_restart.iloc[i])
        manual_prev = bool(manual_restart.iloc[i - 1]) if i > 0 else False
        manual_triggered = manual_today and not manual_prev

        # True Range and Wilder's ATR, updated one candle at a time.
        if i == 0:
            true_range = high_today - low_today
        else:
            prev_close = df["close"].iloc[i - 1]
            true_range = max(
                high_today - low_today,
                abs(high_today - prev_close),
                abs(low_today - prev_close),
            )
        if atr is None:
            tr_values.append(true_range)
            if len(tr_values) == atr_period:
                atr = sum(tr_values) / atr_period
        else:
            atr = (atr * (atr_period - 1) + true_range) / atr_period

        if manual_triggered:
            # Deliberate override: forget whatever was in progress and
            # restart the zigzag from today, both sides reset to today's
            # own high/low, immediately visible, same "explicit action,
            # immediate effect" role manual_restart plays elsewhere.
            swing_high = high_today
            swing_high_index = i
            high_crossed = False
            swing_low = low_today
            swing_low_index = i
            low_crossed = False
            direction = None
            bootstrap_high = high_today
            bootstrap_high_index = i
            bootstrap_low = low_today
            bootstrap_low_index = i
            high_event = "manual restart"
            low_event = "manual restart"
        elif atr is None:
            # Not enough candles yet for a first ATR value at all.
            high_event = "warming up"
            low_event = "warming up"
        elif direction is None:
            # Bootstrap: ATR is ready, but we haven't confirmed which
            # side goes first. Track both running extremes at once and
            # see which one reverses first.
            if bootstrap_high is None:
                # First candle ATR became available on: start tracking.
                bootstrap_high = high_today
                bootstrap_high_index = i
                bootstrap_low = low_today
                bootstrap_low_index = i
                high_event = "warming up"
                low_event = "warming up"
            else:
                if high_today > bootstrap_high:
                    bootstrap_high = high_today
                    bootstrap_high_index = i
                if low_today < bootstrap_low:
                    bootstrap_low = low_today
                    bootstrap_low_index = i

                reversal_down = close_today <= bootstrap_high - reversal_multiplier * atr
                reversal_up = close_today >= bootstrap_low + reversal_multiplier * atr

                if reversal_down:
                    swing_high = bootstrap_high
                    swing_high_index = bootstrap_high_index
                    high_crossed = False
                    high_event = "initial seed"
                    low_event = "warming up"
                    direction = "down"
                    running_extreme = low_today
                    running_extreme_index = i
                elif reversal_up:
                    swing_low = bootstrap_low
                    swing_low_index = bootstrap_low_index
                    low_crossed = False
                    low_event = "initial seed"
                    high_event = "warming up"
                    direction = "up"
                    running_extreme = high_today
                    running_extreme_index = i
                else:
                    high_event = "warming up"
                    low_event = "warming up"
        else:
            # Direction is established: check both sides for a genuine
            # break first (independent of direction, same rule
            # compute_market_structure already keys off), then advance
            # whichever side is currently "live" toward its next pivot.
            if swing_high is None:
                high_event = "warming up"
            else:
                high_event = None
                if not high_crossed and close_today > swing_high:
                    high_crossed = True
                    high_event = "break of swing high"

            if swing_low is None:
                low_event = "warming up"
            else:
                low_event = None
                if not low_crossed and close_today < swing_low:
                    low_crossed = True
                    low_event = "break of swing low"

            if direction == "up":
                if high_today > running_extreme:
                    running_extreme = high_today
                    running_extreme_index = i
                if close_today <= running_extreme - reversal_multiplier * atr:
                    first_ever = swing_high is None
                    swing_high = running_extreme
                    swing_high_index = running_extreme_index
                    high_crossed = False
                    if first_ever:
                        high_event = "initial seed"
                    direction = "down"
                    running_extreme = low_today
                    running_extreme_index = i
            elif direction == "down":
                if low_today < running_extreme:
                    running_extreme = low_today
                    running_extreme_index = i
                if close_today >= running_extreme + reversal_multiplier * atr:
                    first_ever = swing_low is None
                    swing_low = running_extreme
                    swing_low_index = running_extreme_index
                    low_crossed = False
                    if first_ever:
                        low_event = "initial seed"
                    direction = "up"
                    running_extreme = high_today
                    running_extreme_index = i

        swing_high_col[i] = swing_high
        swing_low_col[i] = swing_low
        high_event_col[i] = high_event
        low_event_col[i] = low_event

    result = df.copy()
    result["swing_high"] = swing_high_col
    result["swing_low"] = swing_low_col
    result["high_event"] = high_event_col
    result["low_event"] = low_event_col
    return result
