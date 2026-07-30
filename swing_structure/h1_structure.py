"""H1 market structure: three independent tiers, one per scale.

Same shape as swing_structure/h4_structure.py: all three tiers run the
same Williams Fractal detector at three different values of n, so they
collapse into one module and one loop. See
swing_structure/tiered_fractal_structure.py for why one mechanism can
serve all three, and for the explicit statement that this does NOT
couple the tiers to each other.

Choosing the three n values
---------------------------
Carried over verbatim from h4_structure.py's H4_TIER_PERIODS (n=2/8/20).
This is a placeholder, not a calibration. The 4H numbers came from the
user checking real 4H/Daily charts (n=2 the Pine reference default, n=20
from an observed Williams period, n=8 a provisional geometric guess in
between). None of that observation has been repeated on H1, and
roadmap/detection-method-decision.md's "For the H1 port" section
explicitly warns not to assume the 4H numbers transfer, since Williams
Fractal is sensitive to the trend-to-noise ratio of the path, and H1's
ratio is expected to differ from 4H's. Treat every value below as
unverified until the user's own three-regime table (tight consolidation,
strong trend, news spike) has been run against real H1 charts.

  h1_fractal, n=2   minor pulls. Unverified for H1, see above.
  h1_internal, n=8  intermediate legs. Unverified for H1, see above.
  h1_swing, n=20    major H1 legs. Unverified for H1, see above.

The ATR significance filter is off for all three (0.0), same reasoning
as h4_structure.py: wired up rather than omitted so switching it on
later is a settings change instead of a re-port across three FXR
scripts. See fractal_detector.py's docstring for the full reasoning.
"""

from swing_structure.tiered_fractal_structure import (
    compute_tier_structure,
    tier_column_names,
)

# Tier name to its scale (n). Ordered fast to slow, which is also the
# order the demo prints them in.
H1_TIER_PERIODS = {
    "h1_fractal": 2,
    "h1_internal": 8,
    "h1_swing": 20,
}

# Tier name to its ATR significance threshold, in multiples of ATR. All
# zero, i.e. filter disabled. Kept per-tier rather than as one shared
# number for the same reason as H4_TIER_ATR_SEPARATION: if regime
# testing shows a need, it is a value change rather than a signature
# change.
H1_TIER_ATR_SEPARATION = {
    "h1_fractal": 0.0,
    "h1_internal": 0.0,
    "h1_swing": 0.0,
}

H1_ATR_PERIOD = 14


def h1_column_names(tier=None):
    """Column names emitted for one tier, or for all three in order."""
    if tier is not None:
        return tier_column_names(tier)
    names = []
    for name in H1_TIER_PERIODS:
        names.extend(tier_column_names(name))
    return names


def compute_h1_structures(
    df,
    tier_periods=None,
    tier_atr_separation=None,
    manual_restarts=None,
    atr_period=H1_ATR_PERIOD,
):
    """Computes all three H1 tiers, returning one combined table.

    df: DataFrame of H1 OHLC candles with columns date, open, high, low,
        close, in ascending order. The "date" column holds a full
        timestamp here, not a calendar date, since there are about
        twenty-four candles per trading day. Nothing in the detector
        reads it, so the name is kept as "date" purely to match the
        Daily column layout.
    tier_periods: optional dict overriding H1_TIER_PERIODS, so the
        regime-testing step can sweep n without editing this file. Keys
        not present fall back to the module default.
    tier_atr_separation: optional dict overriding H1_TIER_ATR_SEPARATION,
        same fallback behavior.
    manual_restarts: optional dict of tier name to boolean Series. Keyed
        PER TIER rather than one shared Series, so restarting the fractal
        tier does not disturb the swing tier. This matches Daily and 4H,
        where each tier's wrapper takes its own manual_restart. A missing
        key means no restart for that tier.
    atr_period: period for the optional significance filter's ATR. Unused
        while every tier's separation is 0.0.

    Returns a copy of df with eighteen new columns, six per tier, named
    per h1_column_names().

    Each tier is computed by its own independent call, with no state
    carried between iterations of the loop below, so the result is the
    same regardless of the order the tiers run in. That property is
    asserted by scripts/verify_tier_nesting.py, since it is the thing the
    strategy actually relies on.
    """
    periods = dict(H1_TIER_PERIODS)
    if tier_periods:
        periods.update(tier_periods)

    separations = dict(H1_TIER_ATR_SEPARATION)
    if tier_atr_separation:
        separations.update(tier_atr_separation)

    restarts = manual_restarts or {}

    result = df
    for tier, n in periods.items():
        result = compute_tier_structure(
            result,
            prefix=tier,
            n=n,
            manual_restart=restarts.get(tier),
            min_atr_separation=separations.get(tier, 0.0),
            atr_period=atr_period,
        )
    return result
