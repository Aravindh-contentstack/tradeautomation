"""M15 market structure: two tiers, for the entry models only.

Same shape as h1_structure.py and h4_structure.py: every tier runs the
same Williams Fractal detector at a different n, so they collapse into
one module and one loop. See tiered_fractal_structure.py for why one
mechanism can serve several tiers, and for the explicit statement that
this does NOT couple the tiers to each other.

Why only two tiers, and not three
---------------------------------
Daily, 4H and H1 each get fractal/internal/swing. M15 gets fractal and
internal and stops there. Nothing in the four entry models reads an M15
swing tier: LC-2A needs a fractal structure that can flip, CE needs an
internal break, and LC-1's liquidity comes from raw n=1 pivots rather
than from any tier. An m15_swing tier would be three more columns nobody
queries, and a third state machine to keep correct. Add it when a factor
actually asks for it.

Choosing the two n values
-------------------------
  m15_fractal, n=2   LC-2A's fake break. The Pine reference default and
                     the same value every other timeframe's fractal tier
                     uses, so it is the one number here with any
                     provenance at all.
  m15_internal, n=5  CE's iBOS. The user's stated figure, chosen and not
                     derived.

Read roadmap/detection-method-decision.md's "For the H1 port" section
before touching either. Its warning is that Williams Fractal is
sensitive to the trend-to-noise ratio of the path, so the 4H numbers
were never assumed to transfer to H1. That warning applies MORE
strongly here, not less: M15's ratio is further from 4H's than H1's is,
and n=5 has not been checked against a single real chart. n=8 on H1 is
at least a documented geometric guess between two observed values;
n=5 on M15 is a starting point.

So treat both as unverified, and expect the walk-forward to want them
changed. What would move them: the fire rate of LC-2A (if the fractal
tier flips on every third candle, n=2 is too fast for M15) and of CE
(if the internal tier almost never breaks inside an OB's lifetime, n=5
is too slow).

The ATR significance filter is off for both (0.0), same reasoning as
every other timeframe: wired up rather than omitted so switching it on
later is a value change instead of a signature change. See
fractal_detector.py's docstring for what it does.
"""

from smc.market_structure.tiered_fractal_structure import (
    compute_tier_structure,
    tier_column_names,
)

# Tier name to its scale (n). Ordered fast to slow, which is also the
# order the demo prints them in.
M15_TIER_PERIODS = {
    "m15_fractal": 2,
    "m15_internal": 5,
}

# Tier name to its ATR significance threshold, in multiples of ATR. Both
# zero, i.e. filter disabled. Kept per-tier rather than as one shared
# number for the same reason as H1_TIER_ATR_SEPARATION: if regime testing
# shows a need, it is a value change rather than a signature change.
M15_TIER_ATR_SEPARATION = {
    "m15_fractal": 0.0,
    "m15_internal": 0.0,
}

M15_ATR_PERIOD = 14


def m15_column_names(tier=None):
    """Column names emitted for one tier, or for both in order."""
    if tier is not None:
        return tier_column_names(tier)
    names = []
    for name in M15_TIER_PERIODS:
        names.extend(tier_column_names(name))
    return names


def compute_m15_structures(
    df,
    tier_periods=None,
    tier_atr_separation=None,
    manual_restarts=None,
    atr_period=M15_ATR_PERIOD,
):
    """Computes both M15 tiers, returning one combined table.

    df: DataFrame of M15 OHLC candles with columns date, open, high, low,
        close, in ascending order. The "date" column holds a full
        timestamp, not a calendar date. Nothing in the detector reads it,
        so the name is kept as "date" purely to match every other
        timeframe's column layout.
    tier_periods: optional dict overriding M15_TIER_PERIODS, so a tuning
        sweep can vary n without editing this file. Keys not present fall
        back to the module default.
    tier_atr_separation: optional dict overriding M15_TIER_ATR_SEPARATION,
        same fallback behavior.
    manual_restarts: optional dict of tier name to boolean Series. Keyed
        PER TIER rather than one shared Series, so restarting the fractal
        tier does not disturb the internal tier. A missing key means no
        restart for that tier.
    atr_period: period for the optional significance filter's ATR. Unused
        while every tier's separation is 0.0.

    Returns a copy of df with twelve new columns, six per tier, named per
    m15_column_names().

    Each tier is computed by its own independent call, with no state
    carried between iterations of the loop below, so the result is the
    same regardless of the order the tiers run in. That property is what
    lets LC-2A read the fractal tier while CE reads the internal tier
    without either having to know the other exists.
    """
    periods = dict(M15_TIER_PERIODS)
    if tier_periods:
        periods.update(tier_periods)

    separations = dict(M15_TIER_ATR_SEPARATION)
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
