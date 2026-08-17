"""Daily market structure: three independent tiers, one per scale.

Round 4 of the Daily swing/internal tiers, and the point where Daily
catches up with 4H rather than staying the odd one out. Daily used to run
three DIFFERENT mechanisms, one per tier: swing_structure/detector.py plus
market_structure.py for the swing tier (a trailing lookback window with a
65-candle timeout), swing_structure/internal_structure.py for the internal
tier (an ATR zigzag), and swing_structure/fractal_structure.py for the
fractal tier (a Williams Fractal at n=2, already matching what 4H settled
on). Here all three tiers run the SAME Williams Fractal detector at three
different values of n, exactly mirroring swing_structure/h4_structure.py.
See swing_structure/tiered_fractal_structure.py for why one mechanism can
serve all three tiers, and for the explicit statement that this does NOT
couple the tiers to each other.

Why this port happened now, out of the roadmap's original order
-----------------------------------------------------------------
roadmap/market-structure.md had filed "bring the fractal-family approach
back to Daily" as a Later Item, gated on 4H being calibrated for about
three months first, with H1 planned as the next timeframe port instead.
This module exists because that was deliberately reordered: Daily was
ported now rather than waiting, per an explicit decision recorded in
roadmap/detection-method-decision.md's "Daily port" section. Read that
section, not this docstring, for the reasoning and the caveats on the n
values below.

Choosing the three n values
----------------------------
Mirrors H4_TIER_PERIODS exactly (swing_structure/h4_structure.py), by bar
count, not by any wall-clock conversion between Daily and 4H bars.

  daily_fractal, n=2   the minor pulls. Unchanged from the old
                       fractal_structure.py default, and the Pine
                       reference's own default.
  daily_internal, n=8  intermediate legs. PROVISIONAL, more so than 4H's
                       own n=8: that value was picked as roughly geometric
                       between 4H's n=2 and n=20, itself never checked
                       against a real chart. It has now been reused here
                       without even that much justification specific to
                       Daily. Expect to move it once the Daily regime
                       table in detection-method-decision.md is filled in.
  daily_swing, n=20    the major Daily legs. This is the one number in
                       this module with the STRONGEST backing: n=20 is the
                       original value the user found, on TradingView, to
                       map Daily swing structure cleanly. It was used on
                       4H first (see h4_structure.py), and now finally
                       lands on the timeframe it was actually observed on.

The ATR significance filter is off for all three (0.0), for the same
reason h4_structure.py leaves it off: wiring it up unused means switching
it on later is a settings change, not a re-port across three more FXR
scripts. See fractal_detector.py's docstring for the full reasoning.
"""

from smc.market_structure.tiered_fractal_structure import (
    compute_tier_structure,
    tier_column_names,
)

# Tier name to its scale (n). Ordered fast to slow, which is also the
# order the demo prints them in.
DAILY_TIER_PERIODS = {
    "daily_fractal": 2,
    "daily_internal": 8,
    "daily_swing": 20,
}

# Tier name to its ATR significance threshold, in multiples of ATR. All
# zero, i.e. filter disabled. Kept per-tier rather than as one shared
# number for the same reason as H4_TIER_ATR_SEPARATION: if the regime
# testing shows a need, it will most likely be on daily_internal alone,
# and this way that is a value change rather than a signature change.
DAILY_TIER_ATR_SEPARATION = {
    "daily_fractal": 0.0,
    "daily_internal": 0.0,
    "daily_swing": 0.0,
}

DAILY_ATR_PERIOD = 14


def daily_column_names(tier=None):
    """Column names emitted for one tier, or for all three in order."""
    if tier is not None:
        return tier_column_names(tier)
    names = []
    for name in DAILY_TIER_PERIODS:
        names.extend(tier_column_names(name))
    return names


def compute_daily_structures(
    df,
    tier_periods=None,
    tier_atr_separation=None,
    manual_restarts=None,
    atr_period=DAILY_ATR_PERIOD,
):
    """Computes all three Daily tiers, returning one combined table.

    df: DataFrame of Daily OHLC candles with columns date, open, high, low,
        close, in ascending order.
    tier_periods: optional dict overriding DAILY_TIER_PERIODS, so the
        regime-testing step can sweep n without editing this file. Keys
        not present fall back to the module default.
    tier_atr_separation: optional dict overriding DAILY_TIER_ATR_SEPARATION,
        same fallback behavior.
    manual_restarts: optional dict of tier name to boolean Series. Keyed
        PER TIER rather than one shared Series, so restarting the fractal
        tier does not disturb the swing tier. A missing key means no
        restart for that tier.
    atr_period: period for the optional significance filter's ATR. Unused
        while every tier's separation is 0.0.

    Returns a copy of df with eighteen new columns, six per tier, named
    per daily_column_names().

    Each tier is computed by its own independent call, with no state
    carried between iterations of the loop below, so the result is the
    same regardless of the order the tiers run in. That property is
    asserted by scripts/verify_tier_nesting.py, since it is the thing the
    strategy actually relies on.
    """
    periods = dict(DAILY_TIER_PERIODS)
    if tier_periods:
        periods.update(tier_periods)

    separations = dict(DAILY_TIER_ATR_SEPARATION)
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
