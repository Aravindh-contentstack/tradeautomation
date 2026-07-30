"""Daily internal structure: ATR-based zigzag pivot tracking layered
with the same bullish/bearish classification already used by the swing
tier.

SUPERSEDED July 29 2026: this mechanism is no longer part of the live
Daily chain. swing_structure/daily_structure.py's compute_daily_structures
now covers the Daily internal tier the same way it covers the swing and
fractal tiers, all three via a Williams Fractal at a different n (see
roadmap/detection-method-decision.md's "Daily port" section for why). This
module is kept, unused, rather than deleted, as a comparison/rollback
reference until nobody needs the old mechanism.

"Internal" and "swing" (also called "external") are not two different
concepts, they're the same idea (the current, most relevant swing high
and swing low, and which side was most recently broken) applied at two
different scales on the same Daily candles (confirmed against
factors/xau_probability_factors.csv, which lists Swing, Internal, and
Fractal as three separate rows within the same timeframe). They ARE,
however, now powered by different mechanisms, see "Mechanism history"
below for why.

Internal structure runs fully independently of the swing tier: its own
pivot tracking and its own break/manual state machine over the whole
dataset, not scoped or reset by whatever the swing tier is currently
doing.

Mechanism history:

Rounds 1-2 (real-chart testing) used the same persist-until-broken plus
timeout mechanism as the swing tier (swing_structure/detector.py), just
at a shorter lookback and timeout. That fixed a string of bugs (spurious
fractal-sized noise, missed first legs, a frozen-forever opposite side),
but surfaced a structural limitation, not a bug: because a level only
ever moved via a genuine break or a periodic forced timeout, the
eventual drawn segment could still span an enormous, stale range.

Round 3 used swing_structure/pivot_detector.py's fixed-window fractal
version of compute_pivot_swing_structure (pivot_len candles on both
sides). That fixed the staleness problem, but a fixed candle-count
window doesn't fit both trending and ranging conditions with one number:
a fast trend's pullbacks are often shorter, in candle-count terms, than
a fixed pivot_len, so a real, meaningful retracement could fail to ever
confirm as a pivot.

Round 4 (current): pivot_detector.py now uses an ATR-based zigzag
instead, matching the mechanism both reference scripts in
temp-reference/internal/ conceptually point at, but adaptive: a pivot
confirms as soon as price reverses from the running extreme by more than
reversal_multiplier x ATR, rather than after a fixed number of candles.
timeout_candles and hold_timeout stay dropped, they existed solely to
fix staleness, which a zigzag can't have by construction. manual_restart
is kept, since it's a deliberate override, not a staleness fix.
"""

from swing_structure.pivot_detector import compute_pivot_swing_structure
from swing_structure.market_structure import compute_market_structure

_RENAMED_COLUMNS = {
    "swing_high": "internal_swing_high",
    "swing_low": "internal_swing_low",
    "high_event": "internal_high_event",
    "low_event": "internal_low_event",
    "market_structure": "internal_structure",
    "market_structure_event": "internal_structure_event",
}


def compute_internal_structure(df, atr_period=14, reversal_multiplier=1.5, manual_restart=None):
    """Computes internal swing points and internal bullish/bearish structure.

    df: DataFrame of Daily OHLC candles (date, open, high, low, close, in
        ascending date order). May already carry swing-tier columns from
        an earlier compute_daily_swing_structure/compute_market_structure
        call, those are ignored here since only open/high/low/close/date
        are read.
    atr_period: same meaning as in compute_pivot_swing_structure, how
        many True Range values feed Wilder's ATR (default 14).
    reversal_multiplier: same meaning as in compute_pivot_swing_structure,
        how many ATRs of pullback from the running extreme count as a
        genuine reversal (default 1.5).
    manual_restart: same optional boolean Series as
        compute_pivot_swing_structure, independent of whatever the swing
        tier's own manual_restart might be doing.

    Returns a copy of df with six new columns: internal_swing_high,
    internal_swing_low, internal_high_event, internal_low_event,
    internal_structure, internal_structure_event. Running this after the
    swing tier has already been computed on the same df produces one
    combined table with both tiers side by side.
    """
    # compute_pivot_swing_structure always writes to the fixed column
    # names swing_high/swing_low/etc, unconditionally overwriting
    # anything already using those names. Feeding it df directly would
    # silently clobber the swing tier's own swing_high/swing_low/etc if
    # this is called after the swing tier already ran on the same df, so
    # it's run here on a plain OHLC-only slice instead, and only the
    # renamed internal_ columns are attached back onto the caller's df.
    ohlc_only = df[["date", "open", "high", "low", "close"]]
    internal = compute_pivot_swing_structure(
        ohlc_only,
        atr_period=atr_period,
        reversal_multiplier=reversal_multiplier,
        manual_restart=manual_restart,
    )
    internal = compute_market_structure(internal)
    internal = internal.rename(columns=_RENAMED_COLUMNS)

    result = df.reset_index(drop=True).copy()
    for column in _RENAMED_COLUMNS.values():
        result[column] = internal[column]
    return result
