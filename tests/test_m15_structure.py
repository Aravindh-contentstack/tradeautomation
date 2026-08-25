"""M15 structure: the two tiers, their scales, and the no-lookahead rule.

These are thin-wrapper tests. compute_tier_structure and the Williams
Fractal detector underneath it are already covered by the Daily/4H/H1
work, so what is asserted here is only what this module decides: that
there are exactly two tiers, that they are wired to n=2 and n=5, that
they stay independent of each other, and that a pivot is published at
pivot_index + n rather than at pivot_index.

The last of those is the one worth having. It is the property the entry
models depend on and the one that would silently inflate every backtest
if it broke.
"""

import pandas as pd

from smc.market_structure.m15_structure import (
    M15_TIER_PERIODS,
    compute_m15_structures,
    m15_column_names,
)

START = pd.Timestamp("2024-01-01", tz="UTC")


def peaks(highs):
    """Candles whose highs are `highs`, with lows well below all of them.

    Lows are pinned to a single low value so no low-side pivot can form
    and the assertions stay about the high side.
    """
    return pd.DataFrame([
        {
            "date": START + pd.Timedelta(minutes=15 * i),
            "open": high - 0.5,
            "high": float(high),
            "low": 1.0,
            "close": high - 0.5,
        }
        for i, high in enumerate(highs)
    ])


# A single clean fractal high at index 5, tall enough to be a pivot at
# both n=2 and n=5, with five strictly lower candles on each side.
ONE_PEAK = [10.0, 11.0, 12.0, 13.0, 14.0, 20.0, 14.0, 13.0, 12.0, 11.0, 10.0]


class TestShape:
    def test_two_tiers_only(self):
        assert list(M15_TIER_PERIODS) == ["m15_fractal", "m15_internal"]

    def test_the_scales_are_two_and_five(self):
        assert M15_TIER_PERIODS["m15_fractal"] == 2
        assert M15_TIER_PERIODS["m15_internal"] == 5

    def test_twelve_columns_six_per_tier(self):
        names = m15_column_names()
        assert len(names) == 12
        assert names.count("m15_fractal_swing_high") == 1
        assert names.count("m15_internal_structure") == 1

    def test_every_named_column_is_actually_emitted(self):
        result = compute_m15_structures(peaks(ONE_PEAK))
        for name in m15_column_names():
            assert name in result.columns

    def test_no_swing_tier(self):
        result = compute_m15_structures(peaks(ONE_PEAK))
        assert not [c for c in result.columns if c.startswith("m15_swing")]


class TestNoLookahead:
    def test_a_pivot_is_published_n_candles_after_it_forms(self):
        # The pivot is at index 5. The fractal tier runs at n=2, so it may
        # not be visible until index 7, and the internal tier at n=5 not
        # until index 10. Anything earlier is reading the future.
        result = compute_m15_structures(peaks(ONE_PEAK))
        fractal = result["m15_fractal_swing_high"].tolist()
        internal = result["m15_internal_swing_high"].tolist()

        assert fractal[6] != 20.0
        assert fractal[7] == 20.0

        assert internal[9] != 20.0
        assert internal[10] == 20.0

    def test_the_slower_tier_publishes_strictly_later(self):
        # Not a restatement of the above: this is the property that makes
        # the two tiers useful together. If they published at the same
        # bar, n=5 would buy nothing over n=2.
        result = compute_m15_structures(peaks(ONE_PEAK))
        fractal_at = result["m15_fractal_swing_high"].tolist().index(20.0)
        internal_at = result["m15_internal_swing_high"].tolist().index(20.0)
        assert internal_at > fractal_at


class TestTierIndependence:
    def test_the_tiers_see_different_pivot_sets(self):
        # Two peaks three candles apart. The n=2 tier resolves both; the
        # n=5 tier cannot see the second as a separate pivot because it
        # is inside the first's window. If both tiers reported the same
        # thing, one of them is misconfigured.
        highs = [10.0, 11.0, 20.0, 12.0, 11.0, 18.0, 11.0, 10.0, 9.0,
                 8.0, 7.0, 6.0, 5.0]
        result = compute_m15_structures(peaks(highs))
        # dropna, not "is not None": the warm-up value lands in the frame
        # as NaN, so an identity check against None keeps it and the
        # comparison below would be counting warm-up rather than pivots.
        fractal = set(result["m15_fractal_swing_high"].dropna())
        internal = set(result["m15_internal_swing_high"].dropna())
        assert len(fractal) > len(internal)

    def test_computing_in_either_order_gives_the_same_answer(self):
        # No state is carried between loop iterations, which is what lets
        # LC-2A read the fractal tier while CE reads the internal tier
        # without either knowing the other exists.
        frame = peaks(ONE_PEAK)
        forward = compute_m15_structures(frame)
        reversed_periods = dict(reversed(list(M15_TIER_PERIODS.items())))
        backward = compute_m15_structures(frame, tier_periods=reversed_periods)
        for name in m15_column_names():
            # Series.equals, not ==, because the warm-up rows are NaN and
            # NaN != NaN would fail every column on its first bar.
            assert forward[name].equals(backward[name])


class TestOverrides:
    def test_tier_periods_can_be_swept_without_editing_the_module(self):
        # The tuning seam. n=5 is the user's stated figure and unverified,
        # so a sweep has to be possible from outside.
        result = compute_m15_structures(
            peaks(ONE_PEAK), tier_periods={"m15_internal": 2}
        )
        assert result["m15_internal_swing_high"].equals(
            result["m15_fractal_swing_high"].rename("m15_internal_swing_high")
        )

    def test_a_partial_override_leaves_the_other_tier_alone(self):
        result = compute_m15_structures(
            peaks(ONE_PEAK), tier_periods={"m15_internal": 3}
        )
        assert result["m15_fractal_swing_high"].tolist()[7] == 20.0
