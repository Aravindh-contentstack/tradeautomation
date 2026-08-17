"""Builds a small synthetic Daily OHLC dataset engineered to exercise every
rule in the Daily Swing Structure Detector at least once, runs the
detector, and prints a readable table so correctness can be checked by eye
before any Pine porting starts.

NOTE July 29 2026: demonstrates the retired lookback+timeout mechanism
(swing_structure/detector.py), superseded as the live Daily swing tier by
scripts/demo_daily_structure.py. build_synthetic_data() below is still
reused by several other demo scripts, so this file itself is not retired,
only the mechanism it was originally written to exercise.

See ethereal-coalescing-flute.md for the confirmed rules this exercises:
cold start / initial seed, a break of the swing high, a break of the swing
low, a naked 65-candle automatic timeout on both sides at once, a run where
the low breaks repeatedly while the high times out entirely on its own
(independent per-side clocks), hold_timeout suppressing a timeout then
releasing, and a manual_restart flip that must not re-fire on the
following day.
"""

import datetime
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smc.market_structure.detector import compute_daily_swing_structure


# A repeating pattern of small body deltas used to build "ranging" days
# that wiggle back and forth around a price without trending away from it.
# The 8 deltas sum to exactly 0.0, so any run of ranging days stays inside
# a tight, predictable band no matter how long the run is.
RANGE_WIGGLE = [0.4, -0.3, 0.2, -0.5, 0.3, -0.2, 0.5, -0.4]

# Deliberately larger than the biggest single-day wiggle above (0.5), so
# that whichever candle ends up as the window's extreme after a redraw,
# the very next ordinary ranging day can never close beyond it by
# accident. Without this margin, ordinary noise right after a redraw can
# trigger a spurious extra break, which would make the demo table harder
# to read (each engineered event should fire exactly once).
WICK_PAD = 0.6


def _ranging_days(start_date, start_price, count):
    """Builds `count` daily candles that wiggle around start_price.

    Returns (candles, end_price, end_date) so the caller can chain
    further segments starting from where this one left off.
    """
    candles = []
    price = start_price
    date = start_date
    for i in range(count):
        delta = RANGE_WIGGLE[i % len(RANGE_WIGGLE)]
        o = round(price, 2)
        c = round(o + delta, 2)
        h = round(max(o, c) + WICK_PAD, 2)
        l = round(min(o, c) - WICK_PAD, 2)
        candles.append({"date": date, "open": o, "high": h, "low": l, "close": c})
        price = c
        date += datetime.timedelta(days=1)
    return candles, price, date


def _jump_day(date, price, close_target):
    """Builds a single decisive candle whose CLOSE lands at close_target.

    Used to force a real break of a swing level regardless of exactly
    where the ranging days before it happened to leave price.
    """
    o = round(price, 2)
    c = round(close_target, 2)
    h = round(max(o, c) + WICK_PAD, 2)
    l = round(min(o, c) - WICK_PAD, 2)
    return {"date": date, "open": o, "high": h, "low": l, "close": c}


def build_synthetic_data():
    candles = []
    price = 100.0
    date = datetime.date(2024, 1, 1)

    def extend_ranging(count):
        nonlocal price, date
        seg, price, date = _ranging_days(date, price, count)
        candles.extend(seg)

    def extend_jump(close_target):
        nonlocal price, date
        candles.append(_jump_day(date, price, close_target))
        price = candles[-1]["close"]
        date += datetime.timedelta(days=1)

    # --- Segment 1: cold start. 45 ranging days around 100, so the
    # detector has to wait for a full lookback window before it can seed
    # a swing pair on the 45th candle. ---
    extend_ranging(45)

    # --- Segment 2: break of swing high. A few more ranging days, then a
    # decisive close far above the ~100 band, forcing a real break. ---
    extend_ranging(5)
    extend_jump(130)

    # --- Segment 3: break of swing low. More ranging around the new
    # ~130 level, then a decisive close far below the original ~100 band
    # (the swing low was never touched by segment 2's break). ---
    extend_ranging(5)
    extend_jump(50)

    # --- Segment 4: naked 65-candle automatic timeout, both sides at
    # once. A long quiet stretch around the ~50 band with neither side
    # breaking, long enough for both independent clocks to hit 65
    # together since neither one gets reset in between. ---
    extend_ranging(65)

    # --- Segment 4b: independent per-side timeout. This is the exact
    # scenario that motivated splitting the clock in two: the swing low
    # breaks repeatedly (each break resets ONLY the low's own clock),
    # while price never comes close to the swing high, so the high's own
    # clock keeps climbing, uninterrupted, until it times out entirely on
    # its own, in the middle of the low actively breaking. Confirms a
    # busy side no longer freezes a quiet side. ---
    extend_ranging(20)
    extend_jump(35)  # break of swing low: low_clock -> 0, high_clock keeps climbing
    extend_ranging(20)
    extend_jump(22)  # break of swing low again: low_clock -> 0 again
    extend_ranging(30)  # high_clock crosses 65 partway through, on its own

    # --- Segment 5: hold_timeout demonstration. 40 ordinary quiet days
    # (both clocks climb 1..40), then hold_timeout switches on for 30
    # more quiet days (clocks keep climbing to ~70, past the 65
    # threshold, with no timeout firing on either side because both are
    # held), then hold is released, resetting both clocks together. ---
    extend_ranging(40)
    hold_start_index = len(candles)
    extend_ranging(30)
    hold_end_index = len(candles)

    # --- Segment 6: manual_restart demonstration. A few ordinary days,
    # then manual_restart flips false to true for one candle (must fire
    # on both sides), stays true for one more candle (must NOT re-fire),
    # then flips back to false (no event either way). ---
    extend_ranging(5)
    manual_on_index = len(candles)
    extend_ranging(2)

    # --- Tail: a few ordinary days after the last event. ---
    extend_ranging(5)

    df = pd.DataFrame(candles)

    manual_restart = pd.Series([False] * len(df))
    manual_restart.iloc[manual_on_index] = True
    manual_restart.iloc[manual_on_index + 1] = True

    hold_timeout = pd.Series([False] * len(df))
    hold_timeout.iloc[hold_start_index:hold_end_index] = True

    return df, manual_restart, hold_timeout


def main():
    df, manual_restart, hold_timeout = build_synthetic_data()
    result = compute_daily_swing_structure(
        df,
        manual_restart=manual_restart,
        hold_timeout=hold_timeout,
    )

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 140)

    columns = [
        "date",
        "close",
        "swing_high",
        "swing_low",
        "high_clock",
        "low_clock",
        "high_event",
        "low_event",
    ]

    print(f"Total candles: {len(result)}\n")

    print("=== Every row where something other than an ordinary day happened, on either side ===")
    something_happened = result["high_event"].notna() | result["low_event"].notna()
    print(result.loc[something_happened, columns].to_string(index=False))

    print("\n=== A few ordinary days right after the manual_restart flip, ===")
    print("=== to confirm it does NOT re-fire on the following candle       ===")
    around = result.iloc[max(0, len(result) - 12):]
    print(around[columns].to_string(index=False))


if __name__ == "__main__":
    main()
