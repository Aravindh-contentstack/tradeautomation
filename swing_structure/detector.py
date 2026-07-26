"""Daily swing structure detector.

Implements the confirmed rules from ethereal-coalescing-flute.md: a swing
high and swing low that each persist until a real break, with an
INDEPENDENT age clock and automatic timeout per side, and two manual
overrides (hold_timeout, manual_restart) that still apply globally to both
sides at once.

This was originally a single clock shared by both sides. It was revised
because a shared clock meant one side redrawing frequently (e.g. swing low
breaking repeatedly during a sustained trend) kept resetting the timeout
for BOTH sides, freezing the quiet side's level indefinitely; it could no
longer time out on its own no matter how stale it got. See
ethereal-coalescing-flute.md for the concrete example that surfaced this
(a swing high from months earlier still active, because the swing low kept
breaking often enough that the shared clock never reached 65). Each side
now gets its own clock so a quiet side can still refresh independently of
how active the other side is.

Every day is checked in a fixed order, independently for each side, so
that when more than one rule could fire for that side on the same candle,
only one of them actually does:

    1. Conflict check    (manual_restart + hold_timeout together, ignore both)
    2. Manual restart     (one-time trigger on the false to true flip)
    3. Real break         (close finishes beyond that side's level)
    4. Hold release       (hold_timeout true to false, fresh grace period)
    5. Automatic timeout  (65 candles with no redraw of THIS side)
    6. Ordinary day        (nothing happened, this side's clock ticks up by one)

Because the two sides no longer share a clock, they can now report
different events on the very same day (e.g. the high breaks while the low
independently times out). That's why there are two event columns
(high_event, low_event) instead of one shared event column.

Written as a plain row-by-row loop (not a vectorized pandas operation) on
purpose, since Pine Script processes one candle at a time and this makes
the eventual Pine translation line for line comparable.
"""

import pandas as pd


def compute_daily_swing_structure(
    df,
    lookback=45,
    timeout_candles=65,
    manual_restart=None,
    hold_timeout=None,
):
    """Computes the daily swing high/low pair for every row of df.

    df: DataFrame of Daily OHLC candles with columns date, open, high, low,
        close, in ascending date order.
    lookback: how many trailing candles define the window a swing point is
        picked from (default 45).
    timeout_candles: how many candles can pass with a given side never
        redrawing before that side alone is force-redrawn (default 65).
        The two sides time out independently of each other.
    manual_restart: optional boolean Series, same length and order as df.
        A one-time trigger on the exact candle it flips from False to True.
        Still global: forces an immediate redraw of BOTH sides at once.
    hold_timeout: optional boolean Series, same length and order as df.
        While True, suppresses only the automatic timeout, for BOTH sides.
        Real breaks still apply as normal regardless of this flag. Still
        global: releasing it resets BOTH sides' clocks together.

    Returns a copy of df with six new columns:
        swing_high, swing_low: the current levels as of that day.
        high_clock, low_clock: candles since the last redraw of that side
            specifically. These run independently of each other.
        high_event, low_event: plain-English reason for that day, for that
            side. One of "initial seed", "break of swing high" (only ever
            in high_event), "break of swing low" (only ever in low_event),
            "timeout", "manual restart", "hold released", "warming up", or
            None for an ordinary day on that side.

    Note on "hold released": the confirmed rules say releasing hold_timeout
    resets both sides' clocks, giving each an independent fresh 65-candle
    grace period from that moment. Without a label for that, a release
    would show up as a silent clock reset to 0 with event=None on
    whichever side didn't also redraw for some other reason that day,
    exactly the kind of silent guess the plan was written to avoid.
    "hold released" was added here to keep every redraw and every clock
    reset traceable.
    """
    n = len(df)
    df = df.reset_index(drop=True)

    if manual_restart is None:
        manual_restart = pd.Series([False] * n)
    else:
        manual_restart = manual_restart.reset_index(drop=True)

    if hold_timeout is None:
        hold_timeout = pd.Series([False] * n)
    else:
        hold_timeout = hold_timeout.reset_index(drop=True)

    def window_extremes(end_index):
        # The trailing `lookback` candles ending at (and including)
        # end_index. This is "the current window" the rules refer to.
        window = df.iloc[end_index - lookback + 1 : end_index + 1]
        return window["high"].max(), window["low"].min()

    swing_high_col = [None] * n
    swing_low_col = [None] * n
    high_clock_col = [None] * n
    low_clock_col = [None] * n
    high_event_col = [None] * n
    low_event_col = [None] * n

    # State carried from one candle to the next as we walk forward.
    swing_high = None
    swing_low = None
    high_clock = None
    low_clock = None
    prev_hold = False

    for i in range(n):
        manual_today = bool(manual_restart.iloc[i])
        manual_prev = bool(manual_restart.iloc[i - 1]) if i > 0 else False
        manual_triggered = manual_today and not manual_prev

        hold_today = bool(hold_timeout.iloc[i])
        hold_released = (not hold_today) and prev_hold

        close_today = df["close"].iloc[i]

        if swing_high is None:
            # Cold start: no swing pair exists yet. Wait for a full
            # lookback window of history before seeding both sides.
            if i >= lookback - 1:
                swing_high, swing_low = window_extremes(i)
                high_clock = 0
                low_clock = 0
                high_event = "initial seed"
                low_event = "initial seed"
            else:
                high_event = "warming up"
                low_event = "warming up"
                # clocks stay None. Nothing has been seeded yet, so
                # there is no "candles since last redraw" to count.
        else:
            # Step 1: conflict check, shared by both sides. If
            # manual_restart triggers on a candle where hold_timeout is
            # also true, bypass BOTH manual controls entirely, acting as
            # if neither exists today, for both sides.
            if manual_triggered and hold_today:
                manual_triggered = False
                hold_today = False

            window_high, window_low = window_extremes(i)

            # ---- High side: its own clock, its own event, independent
            # of whatever the low side does today. ----
            if manual_triggered:
                # Step 2: manual restart wins outright, same as before.
                swing_high = window_high
                high_clock = 0
                high_event = "manual restart"
            elif close_today > swing_high:
                # Step 3: real break of the swing high.
                swing_high = window_high
                high_clock = 0
                high_event = "break of swing high"
            elif hold_released:
                # Step 4: hold_timeout just switched back off. Fresh
                # grace period for this side's own clock.
                high_clock = 0
                high_event = "hold released"
            else:
                tentative_high_clock = high_clock + 1
                if tentative_high_clock >= timeout_candles and not hold_today:
                    # Step 5: automatic timeout, for the high side alone.
                    swing_high = window_high
                    high_clock = 0
                    high_event = "timeout"
                else:
                    # Step 6: an ordinary day for this side.
                    high_clock = tentative_high_clock
                    high_event = None

            # ---- Low side: same six steps, its own clock and event,
            # independent of whatever the high side did above. ----
            if manual_triggered:
                swing_low = window_low
                low_clock = 0
                low_event = "manual restart"
            elif close_today < swing_low:
                swing_low = window_low
                low_clock = 0
                low_event = "break of swing low"
            elif hold_released:
                low_clock = 0
                low_event = "hold released"
            else:
                tentative_low_clock = low_clock + 1
                if tentative_low_clock >= timeout_candles and not hold_today:
                    swing_low = window_low
                    low_clock = 0
                    low_event = "timeout"
                else:
                    low_clock = tentative_low_clock
                    low_event = None

        swing_high_col[i] = swing_high
        swing_low_col[i] = swing_low
        high_clock_col[i] = high_clock
        low_clock_col[i] = low_clock
        high_event_col[i] = high_event
        low_event_col[i] = low_event
        prev_hold = hold_today

    result = df.copy()
    result["swing_high"] = swing_high_col
    result["swing_low"] = swing_low_col
    result["high_clock"] = high_clock_col
    result["low_clock"] = low_clock_col
    result["high_event"] = high_event_col
    result["low_event"] = low_event_col
    return result
