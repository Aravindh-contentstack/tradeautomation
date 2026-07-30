"""The "current range" a tier's premium/discount equilibrium should use,
as distinct from its raw swing_high/swing_low pivot columns.

A Williams Fractal pivot only updates once fully confirmed, which needs
`periods` candles to pass beyond it with no reversal (see
fractal_detector.py). That is fine for direction (bullish/bearish)
detection, but wrong for premium/discount: once price breaks a swing
high in a sustained trend, the raw swing_high column freezes at that
now-broken level (h4_swing_structure.py's own docstring already documents
this "goes quiet in a strong trend" property), so an equilibrium computed
straight from it would freeze right along with it instead of recalibrating
as the trend keeps extending.

The fix, one formula for both sides:
    effective_high = max(swing_high, running_max(high) since swing_high last changed)
    effective_low  = min(swing_low,  running_min(low)  since swing_low last changed)

This degrades to a no-op on whichever side hasn't been broken: price
staying on the correct side of that pivot means the running extreme never
exceeds it, so effective_* just equals the raw column, still updating
exactly when the raw column itself legitimately updates via the detector's
own mechanism (a fresh, higher-low pivot in an uptrend is a far more
common fractal shape than a fresh high during one, which is why the low
side mostly self-corrects on its own and must never be pinned to whatever
it was at the moment of the breakout). Only the side that's actually been
broken and kept running needs the running-extreme term to take over.

Reads swing_high/swing_low and high/low only. Writes new columns, leaves
the tier's own swing_high/swing_low/structure columns (and everything that
depends on them for direction/BOS purposes) completely untouched.
"""

import pandas as pd


def compute_current_range(
    df,
    swing_high_column,
    swing_low_column,
    output_high_column,
    output_low_column,
    high_column="high",
    low_column="low",
):
    """Computes the running-extended high/low a premium/discount range should use.

    df: DataFrame already carrying swing_high_column, swing_low_column
        (e.g. from compute_tier_structure), and high_column/low_column
        (the candles' own OHLC extremes).
    swing_high_column, swing_low_column: the tier's raw confirmed pivot
        columns. None/NaN rows (still warming up) leave the corresponding
        output None too.
    output_high_column, output_low_column: names to write the two new
        columns as.
    high_column, low_column: the OHLC extreme columns compared against
        the running window. Default "high"/"low".

    This is a forward walk, not a vectorized formula, since each row's
    running extreme depends on whether the raw pivot changed value on
    this row or not: the window resets to start counting fresh from
    today the moment swing_high (or swing_low) itself moves to a newly
    confirmed pivot, exactly mirroring the detector's own "every new
    confirmation immediately replaces the previous one" rule.

    Returns a copy of df with the two new columns added.
    """
    result = df.reset_index(drop=True).copy()

    swing_high = result[swing_high_column]
    swing_low = result[swing_low_column]
    high = result[high_column]
    low = result[low_column]

    n = len(result)
    current_high_col = [None] * n
    current_low_col = [None] * n

    running_max_high = None
    running_min_low = None
    prev_swing_high = None
    prev_swing_low = None

    for i in range(n):
        sh = swing_high.iloc[i]
        sl = swing_low.iloc[i]
        h = high.iloc[i]
        l = low.iloc[i]

        if not pd.isna(sh):
            if pd.isna(prev_swing_high) or sh != prev_swing_high:
                running_max_high = h
            else:
                running_max_high = max(running_max_high, h)
            current_high_col[i] = max(sh, running_max_high)

        if not pd.isna(sl):
            if pd.isna(prev_swing_low) or sl != prev_swing_low:
                running_min_low = l
            else:
                running_min_low = min(running_min_low, l)
            current_low_col[i] = min(sl, running_min_low)

        prev_swing_high = sh
        prev_swing_low = sl

    result[output_high_column] = current_high_col
    result[output_low_column] = current_low_col
    return result
