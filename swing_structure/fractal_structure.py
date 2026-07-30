"""Daily fractal structure: Williams Fractal pivot tracking layered with
the same bullish/bearish classification already used by the swing and
internal tiers.

Fractal, internal, and swing (also called "external") are not different
concepts, they're the same idea (the current, most relevant swing high
and swing low, and which side was most recently broken) applied at three
different scales on the same Daily candles (confirmed against
factors/xau_probability_factors.csv, which lists Swing, Internal, and
Fractal as three separate rows within the same timeframe). Each is
powered by a different mechanism suited to its scale, see
swing_structure/fractal_detector.py's module docstring for the fractal
tier's mechanism (a bar-for-bar port of temp-reference/fractal/
williams_fractal.py, n=2 by default) and why it has no break-first gate
the way the other two tiers do.

Fractal structure runs fully independently of the swing and internal
tiers: its own pivot tracking and its own break/manual state machine over
the whole dataset, not scoped or reset by whatever those tiers are
currently doing.

NOTE July 29 2026: no longer part of the live Daily chain.
swing_structure/daily_structure.py's compute_daily_structures now covers
the Daily fractal tier too, via swing_structure/tiered_fractal_structure.py's
compute_tier_structure(prefix="daily_fractal", n=2, ...), which is a
generalization of exactly this module's own machinery. This module is
kept, not orphaned like detector.py/pivot_detector.py/internal_structure.py,
because scripts/verify_tier_nesting.py's verify_wrapper_parity uses it as
the oracle proving compute_tier_structure is a faithful generalization at
n=2. Fix here first if this and compute_tier_structure(n=2) ever disagree.
"""

from swing_structure.fractal_detector import compute_fractal_swing_structure
from swing_structure.market_structure import compute_market_structure

_RENAMED_COLUMNS = {
    "swing_high": "fractal_swing_high",
    "swing_low": "fractal_swing_low",
    "high_event": "fractal_high_event",
    "low_event": "fractal_low_event",
    "market_structure": "fractal_structure",
    "market_structure_event": "fractal_structure_event",
}


def compute_fractal_structure(df, n=2, manual_restart=None):
    """Computes fractal swing points and fractal bullish/bearish structure.

    df: DataFrame of Daily OHLC candles (date, open, high, low, close, in
        ascending date order). May already carry swing-tier or
        internal-tier columns from an earlier compute_daily_swing_structure
        /compute_internal_structure call, those are ignored here since
        only open/high/low/close/date are read.
    n: same meaning as in compute_fractal_swing_structure, candles
        required strictly beyond the pivot to confirm a fractal (default
        2, matching the Pine reference's default Periods input).
    manual_restart: same optional boolean Series as
        compute_fractal_swing_structure, independent of whatever the
        swing or internal tier's own manual_restart might be doing.

    Returns a copy of df with six new columns: fractal_swing_high,
    fractal_swing_low, fractal_high_event, fractal_low_event,
    fractal_structure, fractal_structure_event. Running this after the
    swing and internal tiers have already been computed on the same df
    produces one combined table with all three tiers side by side.
    """
    # compute_fractal_swing_structure always writes to the fixed column
    # names swing_high/swing_low/etc, unconditionally overwriting
    # anything already using those names. Feeding it df directly would
    # silently clobber the swing or internal tier's own swing_high/
    # swing_low/etc if this is called after either already ran on the
    # same df, so it's run here on a plain OHLC-only slice instead, and
    # only the renamed fractal_ columns are attached back onto the
    # caller's df.
    ohlc_only = df[["date", "open", "high", "low", "close"]]
    fractal = compute_fractal_swing_structure(
        ohlc_only,
        n=n,
        manual_restart=manual_restart,
    )
    fractal = compute_market_structure(fractal)
    fractal = fractal.rename(columns=_RENAMED_COLUMNS)

    result = df.reset_index(drop=True).copy()
    for column in _RENAMED_COLUMNS.values():
        result[column] = fractal[column]
    return result
