"""Daily market structure (bullish/bearish) classifier.

Layers on top of compute_daily_swing_structure's output. That function
already tells us, for every day, whether the swing high or swing low just
got genuinely broken (high_event == "break of swing high" / low_event ==
"break of swing low"). This module answers a different question: given
those breaks, is the market currently bullish or bearish?

The rule, confirmed with the user: whichever side was most recently broken
by a REAL break decides the direction. A swing high taken out means
bullish. A swing low taken out means bearish. Nothing else moves this
needle: a timeout redraw or a manual_restart redraw changes the swing
level, but it is not a real price break, so it must not flip the
structure. Only genuine breaks count.

Before the very first genuine break anywhere in the data, there is no
direction yet, so market_structure is None ("undetermined"), the same way
swing_high/swing_low are None during the swing detector's own "warming up"
phase.

This is a single forward walk over rows that are already computed (not a
separate historical bootstrap): the first genuine break encountered, in
date order, sets the initial direction, and every later genuine break on
the opposite side flips it. This mirrors the user's own manual method of
comparing successive lookback windows to see which side got taken out
first, just driven off events the swing detector already produces instead
of a separate windowed procedure.
"""


def compute_market_structure(df):
    """Computes the daily bullish/bearish market structure for every row.

    df: DataFrame as returned by compute_daily_swing_structure, in
        ascending date order. Must contain high_event and low_event.

    Returns a copy of df with two new columns:
        market_structure: "bullish", "bearish", or None if no genuine
            break has happened yet anywhere in the data so far.
        market_structure_event: plain-English reason for that day, one of
            "initial structure: bullish (break of swing high)",
            "initial structure: bearish (break of swing low)",
            "flipped to bullish (break of swing high)",
            "flipped to bearish (break of swing low)",
            or None on a day the structure didn't change (this covers
            ordinary days, warming up, initial seed, timeout, manual
            restart, hold released, and a break that continues the
            existing direction rather than flipping it).
    """
    n = len(df)
    df = df.reset_index(drop=True)

    structure_col = [None] * n
    structure_event_col = [None] * n

    # State carried from one candle to the next as we walk forward.
    structure = None

    for i in range(n):
        high_broke = df["high_event"].iloc[i] == "break of swing high"
        low_broke = df["low_event"].iloc[i] == "break of swing low"

        # A single candle's close can't finish both above the swing high
        # and below the swing low at once (the swing high always stays
        # above the swing low once seeded), so these two are mutually
        # exclusive in practice. No same-day conflict case is needed.
        if high_broke:
            if structure is None:
                structure = "bullish"
                structure_event = "initial structure: bullish (break of swing high)"
            elif structure == "bearish":
                structure = "bullish"
                structure_event = "flipped to bullish (break of swing high)"
            else:
                # Already bullish: this break continues the existing
                # direction, it doesn't change it.
                structure_event = None
        elif low_broke:
            if structure is None:
                structure = "bearish"
                structure_event = "initial structure: bearish (break of swing low)"
            elif structure == "bullish":
                structure = "bearish"
                structure_event = "flipped to bearish (break of swing low)"
            else:
                structure_event = None
        else:
            # Nothing broke today on either side (warming up, initial
            # seed, timeout, manual restart, hold released, or an
            # ordinary day). Structure just carries forward unchanged.
            structure_event = None

        structure_col[i] = structure
        structure_event_col[i] = structure_event

    result = df.copy()
    result["market_structure"] = structure_col
    result["market_structure_event"] = structure_event_col
    return result
