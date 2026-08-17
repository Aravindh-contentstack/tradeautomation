"""4H market structure: three independent tiers, one per scale.

The Daily equivalent of this is three separate modules
(swing_structure/detector.py plus market_structure.py, then
internal_structure.py, then fractal_structure.py), because each Daily tier
runs a different mechanism. Here all three tiers run the same Williams
Fractal detector at three different values of n, so they collapse into one
module and one loop. See swing_structure/tiered_fractal_structure.py for
why one mechanism can serve all three, and for the explicit statement that
this does NOT couple the tiers to each other.

Choosing the three n values
---------------------------
Calibrated by BAR COUNT parity with Daily, not by wall-clock parity. A
4H candle is six hours' worth less than a Daily one, so scaling the
numbers by bars-per-day (45 -> 270, 20 -> 120) would make 4H structure
span the same wall-clock horizon as Daily structure and therefore mostly
re-draw the Daily levels. The point of a 4H tier is intermediate
structure, so the bar counts stay put and the horizon shortens: n=20 on
4H is roughly a week and a half rather than roughly nine weeks.

  h4_fractal, n=2   the minor pulls. The Pine reference's own default.
  h4_internal, n=8  intermediate legs. PROVISIONAL. This is the only one
                    of the three not taken from an observation: 2 and 20
                    both come from the user checking real charts, while 8
                    is picked as roughly geometric between them. Expect
                    to move it once the regime testing runs.
  h4_swing, n=20    the major 4H legs. From the user's own finding that a
                    Williams period near 20 mapped swing structure
                    cleanly in TradingView.

The ATR significance filter is off for all three (0.0). It is wired up
rather than omitted so that switching it on later is a settings change
instead of a re-port across six FXR scripts, which is the expensive part.
Need for it is concentrated in h4_internal: the n=2 tier positively wants
its noisy pivots, and at n=20 the market would have to stay range-bound
for 41 straight candles for a pivot to be trivial. See
fractal_detector.py's docstring for the full reasoning.
"""

from smc.market_structure.tiered_fractal_structure import (
    compute_tier_structure,
    tier_column_names,
)

# Tier name to its scale (n). Ordered fast to slow, which is also the
# order the demo prints them in.
H4_TIER_PERIODS = {
    "h4_fractal": 2,
    "h4_internal": 8,
    "h4_swing": 20,
}

# Tier name to its ATR significance threshold, in multiples of ATR. All
# zero, i.e. filter disabled. Kept per-tier rather than as one shared
# number because if the regime testing shows a need, it will almost
# certainly be on h4_internal alone, and this way that is a value change
# rather than a signature change.
H4_TIER_ATR_SEPARATION = {
    "h4_fractal": 0.0,
    "h4_internal": 0.0,
    "h4_swing": 0.0,
}

H4_ATR_PERIOD = 14


def h4_column_names(tier=None):
    """Column names emitted for one tier, or for all three in order."""
    if tier is not None:
        return tier_column_names(tier)
    names = []
    for name in H4_TIER_PERIODS:
        names.extend(tier_column_names(name))
    return names


def compute_h4_structures(
    df,
    tier_periods=None,
    tier_atr_separation=None,
    manual_restarts=None,
    atr_period=H4_ATR_PERIOD,
):
    """Computes all three 4H tiers, returning one combined table.

    df: DataFrame of 4H OHLC candles with columns date, open, high, low,
        close, in ascending order. The "date" column holds a full
        timestamp here, not a calendar date, since there are six candles
        per trading day. Nothing in the detector reads it, so the name is
        kept as "date" purely to match the Daily column layout.
    tier_periods: optional dict overriding H4_TIER_PERIODS, so the
        regime-testing step can sweep n without editing this file. Keys
        not present fall back to the module default.
    tier_atr_separation: optional dict overriding H4_TIER_ATR_SEPARATION,
        same fallback behavior.
    manual_restarts: optional dict of tier name to boolean Series. Keyed
        PER TIER rather than one shared Series, so restarting the fractal
        tier does not disturb the swing tier. This matches Daily, where
        each tier's wrapper takes its own manual_restart. A missing key
        means no restart for that tier.
    atr_period: period for the optional significance filter's ATR. Unused
        while every tier's separation is 0.0.

    Returns a copy of df with eighteen new columns, six per tier, named
    per h4_column_names().

    Each tier is computed by its own independent call, with no state
    carried between iterations of the loop below, so the result is the
    same regardless of the order the tiers run in. That property is
    asserted by scripts/verify_tier_nesting.py, since it is the thing the
    strategy actually relies on.
    """
    periods = dict(H4_TIER_PERIODS)
    if tier_periods:
        periods.update(tier_periods)

    separations = dict(H4_TIER_ATR_SEPARATION)
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
