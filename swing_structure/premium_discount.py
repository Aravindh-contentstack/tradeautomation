"""Premium/discount zone classification: where price sits inside a tier's
current swing range.

Different question than market_structure.py's bullish/bearish call. That
answers "which direction is this tier in", this answers "given that
direction, is the CURRENT price expensive or cheap relative to the
tier's own range". The two combine: a bullish tier's premium zone is
where a pullback entry is favored (price has already run up into the
top half, wait for a discount pullback), and vice versa for bearish.

Equilibrium (the range's midpoint) decides the split:
    equilibrium = (range_high + range_low) / 2
For a bullish range, above equilibrium is premium, at-or-below is
discount. For a bearish range, it's flipped: at-or-below equilibrium is
premium, above is discount. An exact tie at equilibrium therefore always
buckets into whichever side the "at-or-below" comparison lands on
(discount for bullish, premium for bearish), so the output only ever
takes two non-null values, never a third "equilibrium" label.

Unlike market_structure.py, this is stateless: no forward walk, no
break/flip history, just one row-independent calculation, so it is
written as vectorized pandas comparisons rather than a loop. NaN in
high/low (tier still warming up, no range yet) or None/NaN in structure
(direction undetermined) makes every comparison in this module False by
IEEE-754 rules, pandas included, so those rows land outside both the
premium and discount masks with no separate isna() guard needed and stay
at the initialized None.

Generic across any (high, low, structure) column triple rather than
hardcoded to one timeframe's naming convention, because 4H names its
columns "{tier}_swing_high" while Daily uses bare "swing_high" /
"internal_swing_high". compute_h4_premium_discount below is the 4H-
specific wrapper, alongside compute_daily_premium_discount for Daily and
compute_h1_premium_discount for H1.

compute_h4_premium_discount feeds compute_premium_discount each tier's
swing_high/swing_low columns only after running them through
swing_structure/current_range.py's compute_current_range first, since
those raw columns go stale mid-trend (see that module's docstring). This
module itself stays agnostic to that, compute_premium_discount doesn't
care whether its high_column/low_column came straight from the detector
or were extended by compute_current_range first.
"""

import pandas as pd

from swing_structure.current_range import compute_current_range


def compute_premium_discount(
    df,
    high_column,
    low_column,
    structure_column,
    output_column,
    close_column="close",
):
    """Computes one premium/discount zone column for one range.

    df: DataFrame already carrying high_column, low_column,
        structure_column (as produced by, e.g., compute_tier_structure or
        compute_daily_swing_structure + compute_market_structure), and
        close_column.
    high_column, low_column: that range's current swing high/low columns.
    structure_column: that range's bullish/bearish column. Values other
        than exactly "bullish" or "bearish" (None, NaN, anything else)
        leave the output None for that row.
    output_column: name to write the zone column as. Taken as an
        explicit argument rather than derived from a prefix, since no
        single prefix convention covers both Daily and 4H column names.
    close_column: price column compared against the range's equilibrium.

    Returns a copy of df with one new column, output_column, holding
    "premium", "discount", or None.
    """
    result = df.reset_index(drop=True).copy()

    high = result[high_column]
    low = result[low_column]
    close = result[close_column]
    structure = result[structure_column]

    equilibrium = (high + low) / 2.0

    is_bullish = structure == "bullish"
    is_bearish = structure == "bearish"

    # NaN in high/low/close propagates through equilibrium and these
    # comparisons to False on both sides (not True on either), which is
    # exactly what leaves a warming-up row at the initialized None below
    # instead of misclassifying it.
    premium = (is_bullish & (close > equilibrium)) | (is_bearish & (close <= equilibrium))
    discount = (is_bullish & (close <= equilibrium)) | (is_bearish & (close > equilibrium))

    zone = pd.Series([None] * len(result), index=result.index, dtype=object)
    zone[premium] = "premium"
    zone[discount] = "discount"

    result[output_column] = zone
    return result


def h4_zone_column_names(tiers=("h4_swing", "h4_internal")):
    """The zone column names compute_h4_premium_discount emits."""
    return ["%s_zone" % tier for tier in tiers]


def compute_h4_premium_discount(df, tiers=("h4_swing", "h4_internal"), close_column="close"):
    """Computes premium/discount zones for the given 4H tiers.

    df: the combined table already produced by compute_h4_structures
        (swing_structure/h4_structure.py), carrying each tier's
        {tier}_swing_high, {tier}_swing_low, {tier}_structure columns.
    tiers: which tiers to compute a zone for. Defaults to h4_swing and
        h4_internal only, h4_fractal is deliberately excluded: at n=2 its
        range is too short-lived to read as a meaningful premium/discount
        range. Pass a tuple including "h4_fractal" to opt in later.
    close_column: price column compared against each tier's equilibrium.

    Returns a copy of df with one new "{tier}_zone" column per tier in
    tiers, named per h4_zone_column_names(tiers). Each tier additionally
    gets "{tier}_current_high"/"{tier}_current_low" (see below).
    """
    result = df
    for tier in tiers:
        high_column = "%s_swing_high" % tier
        low_column = "%s_swing_low" % tier

        # Both tiers' raw swing_high/swing_low freeze once a side is
        # broken and price keeps running (the same "goes quiet in a
        # strong trend" property h4_swing_structure.py documents applies
        # equally to h4_internal_structure.py's Williams Fractal pivots),
        # which would freeze the equilibrium right along with it instead
        # of recalibrating as the trend extends. compute_current_range
        # extends the broken side with the running extreme actually made
        # since, while degrading to a no-op (reads the raw column live)
        # on whichever side hasn't been broken.
        result = compute_current_range(
            result,
            swing_high_column=high_column,
            swing_low_column=low_column,
            output_high_column="%s_current_high" % tier,
            output_low_column="%s_current_low" % tier,
        )
        high_column = "%s_current_high" % tier
        low_column = "%s_current_low" % tier

        result = compute_premium_discount(
            result,
            high_column=high_column,
            low_column=low_column,
            structure_column="%s_structure" % tier,
            output_column="%s_zone" % tier,
            close_column=close_column,
        )
    return result


def daily_zone_column_names(tiers=("daily_swing", "daily_internal")):
    """The zone column names compute_daily_premium_discount emits."""
    return ["%s_zone" % tier for tier in tiers]


def compute_daily_premium_discount(df, tiers=("daily_swing", "daily_internal"), close_column="close"):
    """Computes premium/discount zones for the given Daily tiers.

    df: the combined table already produced by compute_daily_structures
        (swing_structure/daily_structure.py), carrying each tier's
        {tier}_swing_high, {tier}_swing_low, {tier}_structure columns.
    tiers: which tiers to compute a zone for. Defaults to daily_swing and
        daily_internal only. daily_fractal is deliberately excluded for the
        same reason h4_fractal is: at n=2 its range is too short-lived to
        read as a meaningful premium/discount range.
    close_column: price column compared against each tier's equilibrium.

    Returns a copy of df with one new "{tier}_zone" column per tier in
    tiers, named per daily_zone_column_names(tiers). Each tier additionally
    gets "{tier}_current_high"/"{tier}_current_low".
    """
    result = df
    for tier in tiers:
        high_column = "%s_swing_high" % tier
        low_column = "%s_swing_low" % tier

        # Both tiers' raw swing_high/swing_low freeze once a side is broken
        # (same logic as compute_h4_premium_discount).
        # compute_current_range extends the broken side with the running extreme.
        result = compute_current_range(
            result,
            swing_high_column=high_column,
            swing_low_column=low_column,
            output_high_column="%s_current_high" % tier,
            output_low_column="%s_current_low" % tier,
        )
        high_column = "%s_current_high" % tier
        low_column = "%s_current_low" % tier

        result = compute_premium_discount(
            result,
            high_column=high_column,
            low_column=low_column,
            structure_column="%s_structure" % tier,
            output_column="%s_zone" % tier,
            close_column=close_column,
        )
    return result


def h1_zone_column_names(tiers=("h1_swing", "h1_internal")):
    """The zone column names compute_h1_premium_discount emits."""
    return ["%s_zone" % tier for tier in tiers]


def compute_h1_premium_discount(df, tiers=("h1_swing", "h1_internal"), close_column="close"):
    """Computes premium/discount zones for the given H1 tiers.

    df: the combined table already produced by compute_h1_structures
        (swing_structure/h1_structure.py), carrying each tier's
        {tier}_swing_high, {tier}_swing_low, {tier}_structure columns.
    tiers: which tiers to compute a zone for. Defaults to h1_swing and
        h1_internal only, h1_fractal is deliberately excluded: at n=2 its
        range is too short-lived to read as a meaningful premium/discount
        range. Pass a tuple including "h1_fractal" to opt in later.
    close_column: price column compared against each tier's equilibrium.

    Returns a copy of df with one new "{tier}_zone" column per tier in
    tiers, named per h1_zone_column_names(tiers). Each tier additionally
    gets "{tier}_current_high"/"{tier}_current_low" (see below).
    """
    result = df
    for tier in tiers:
        high_column = "%s_swing_high" % tier
        low_column = "%s_swing_low" % tier

        # Both tiers' raw swing_high/swing_low freeze once a side is
        # broken and price keeps running (the same "goes quiet in a
        # strong trend" property h1_swing_structure.py documents applies
        # equally to h1_internal_structure.py's Williams Fractal pivots),
        # which would freeze the equilibrium right along with it instead
        # of recalibrating as the trend extends. compute_current_range
        # extends the broken side with the running extreme actually made
        # since, while degrading to a no-op (reads the raw column live)
        # on whichever side hasn't been broken.
        result = compute_current_range(
            result,
            swing_high_column=high_column,
            swing_low_column=low_column,
            output_high_column="%s_current_high" % tier,
            output_low_column="%s_current_low" % tier,
        )
        high_column = "%s_current_high" % tier
        low_column = "%s_current_low" % tier

        result = compute_premium_discount(
            result,
            high_column=high_column,
            low_column=low_column,
            structure_column="%s_structure" % tier,
            output_column="%s_zone" % tier,
            close_column=close_column,
        )
    return result
