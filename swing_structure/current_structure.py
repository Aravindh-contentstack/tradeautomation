"""Exposes the current (most recent) structure state, per factor,
for a later stage to combine with the weighted factors in
factors/xau_probability_factors.csv / factors/eu_probability_factors.csv.

Nothing new is computed here. swing_structure/daily_structure.py's
compute_daily_structures produces daily_swing_structure,
daily_internal_structure and daily_fractal_structure (the three Daily
tiers, all via the same Williams Fractal detector at different n), and
swing_structure/h4_structure.py's and swing_structure/h1_structure.py's
compute_h4_structures/compute_h1_structures produce the matching
h4_*_structure/h1_*_structure columns for 4H and H1, all via the same
compute_market_structure rule underneath (only a genuine break flips it).
swing_structure/premium_discount.py separately produces the
{prefix}_swing_zone and {prefix}_internal_zone columns for each timeframe
(premium/discount within each tier's own range), via a different,
stateless rule, not a break/flip history like the structure columns.
This module just answers "what is the state right now" instead of every
caller reaching into a DataFrame and pulling the last row by hand.

Daily used to be wired to a different set of columns (market_structure,
internal_structure, fractal_structure), produced by three separate
mechanisms: swing_structure/detector.py plus market_structure.py for the
swing tier, swing_structure/internal_structure.py for the internal tier,
and swing_structure/fractal_structure.py for the fractal tier. That chain
is superseded now Daily has moved to the same one-mechanism-three-scales
approach as 4H, see swing_structure/daily_structure.py and
roadmap/detection-method-decision.md's "Daily port" section. The old
modules are kept, unused, as a comparison/rollback reference rather than
deleted outright.
"""

import pandas as pd

# Factor name to the DataFrame column that holds its current
# bullish/bearish state. The keys mirror Factor x Timeframe from the
# probability factors CSVs, lowercased: the three unprefixed keys are the
# Daily rows (Daily being the first timeframe implemented, so its keys
# predate the need for a prefix and are left alone rather than renamed),
# h4_ prefixed keys are the 4H rows, and h1_ prefixed keys are the H1
# rows, added the same way.
#
# The Daily values were renamed from market_structure/internal_structure/
# fractal_structure to daily_swing_structure/daily_internal_structure/
# daily_fractal_structure when Daily switched to compute_daily_structures.
# Only the column names these keys point at changed, not the keys
# themselves: nothing outside swing_structure/ and scripts/demo_*.py read
# the old column names directly, so the rename is contained to files this
# same change already touches.
#
# Flat rather than nested by timeframe, because callers combining these
# with the weighted factors need one lookup per factor row, and the CSVs
# themselves are flat rows rather than a nested structure.
_STRUCTURE_COLUMNS = {
    "swing": "daily_swing_structure",
    "internal": "daily_internal_structure",
    "fractal": "daily_fractal_structure",
    "h4_swing": "h4_swing_structure",
    "h4_internal": "h4_internal_structure",
    "h4_fractal": "h4_fractal_structure",
    "h1_swing": "h1_swing_structure",
    "h1_internal": "h1_internal_structure",
    "h1_fractal": "h1_fractal_structure",
    "daily_swing_zone": "daily_swing_zone",
    "daily_internal_zone": "daily_internal_zone",
    "h4_swing_zone": "h4_swing_zone",
    "h4_internal_zone": "h4_internal_zone",
    "h1_swing_zone": "h1_swing_zone",
    "h1_internal_zone": "h1_internal_zone",
}


def get_current_structure(df):
    """Returns the most recent bullish/bearish state (and 4H zone) per factor.

    df: a DataFrame that already has one or more of the columns listed
        in _STRUCTURE_COLUMNS computed on it (the same combined table
        produced by running compute_daily_structures, compute_h4_structures
        and/or compute_h1_structures on it), in ascending date order. Only
        the LAST row is read, "today" as far as this DataFrame goes. To get
        the state as of an earlier date instead, pass a DataFrame
        truncated up to that date.

    Returns a dict: {"date": <the last row's date>, "swing": <value>,
    "internal": <value>, "fractal": <value>, "h4_swing": <value>,
    "h4_internal": <value>, "h4_fractal": <value>, "h1_swing": <value>,
    "h1_internal": <value>, "h1_fractal": <value>,
    "daily_swing_zone": <value>, "daily_internal_zone": <value>,
    "h4_swing_zone": <value>, "h4_internal_zone": <value>,
    "h1_swing_zone": <value>, "h1_internal_zone": <value>}.
    The zone keys are "premium"/"discount" rather than "bullish"/"bearish",
    from compute_daily_premium_discount, compute_h4_premium_discount, and
    compute_h1_premium_discount, not compute_*_structures. Every key in
    _STRUCTURE_COLUMNS is always present. A value is None if that column
    isn't in df at all (not yet computed) or if the column's own value is
    None (computed, but still undetermined), the caller only ever needs
    to check for None, not care which of those it was.

    That "not in df at all" case is what makes mixing timeframes safe: a
    Daily-only table reports None for the h4_ and h1_ keys, an H1-only
    table reports None for the Daily and h4_ keys, and so on, without any
    caller needing to know which timeframes were computed. A caller
    wanting all three passes a frame that has had the Daily chain,
    compute_h4_structures, AND compute_h1_structures run on it.

    Note that the nine structure values are free to disagree, and disagreement is
    meaningful rather than a fault: swing bullish with internal bearish is
    a pullback within the swing, and internal bearish with fractal bullish
    is that pullback making its own pullback. Nothing here reconciles
    them, and nothing downstream should assume they agree.
    """
    last_row = df.iloc[-1]

    result = {"date": last_row["date"]}
    for factor, column in _STRUCTURE_COLUMNS.items():
        if column not in df.columns:
            result[factor] = None
            continue
        value = last_row[column]
        # A structure column holds strings plus None for "undetermined".
        # pandas stores that as a string dtype whose missing value reads
        # back as NaN, not as the None that was written, so the None this
        # function's contract promises has to be restored here. It matters
        # more than it looks: NaN is TRUTHY, so a caller writing
        # `if state:` would treat undetermined as a real direction, and
        # `state is None` would never fire.
        result[factor] = None if pd.isna(value) else value
    return result
