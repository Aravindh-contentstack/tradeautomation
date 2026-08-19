"""Equal highs/lows and old points: pooling, sweeps and expiry.

Hand-built candles in the spirit of conftest.py. Everything is driven off
one shape, `zigzag`, which emits a run of alternating peaks and troughs at
prices the test names: that makes a fixture readable as "three peaks at
100, 100.1 and 130" rather than as a wall of OHLC tuples.

Prices are plain numbers, not FX quotes, and ATR is pinned by construction:
every candle has the same range, so ATR settles at that range and the
tolerance band is a number the test can state outright rather than
approximate. With RANGE = 1.0 and the default 0.25 tolerance, two pivots are
the same level when they are within 0.25 of each other, and the band is
0.125 either side.
"""

import pandas as pd
import pytest

from smc.liquidity.levels import (
    DEFAULT_TOLERANCE,
    EQUALS,
    OLD_POINT,
    compute_liquidity_levels,
)

# Every candle spans exactly this, so ATR converges to it immediately and
# stays there. Chosen as 1.0 so "within 0.25 ATR" reads as "within 0.25".
RANGE = 1.0
TOLERANCE = DEFAULT_TOLERANCE * RANGE

# ATR needs 14 candles before it reports anything, and a level cannot form
# without a tolerance. Every fixture pads past that first.
WARMUP = 20

BASE = 100.0
PIVOT_N = 2


def _candle(ts, high):
    """One candle of fixed range, topping out at `high`."""
    low = high - RANGE
    return {
        "date": ts,
        "open": high - RANGE / 2.0,
        "high": high,
        "low": low,
        "close": high - RANGE / 2.0,
    }


def frame(highs):
    """A frame whose candle i tops out at highs[i]."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    return pd.DataFrame([
        _candle(start + pd.Timedelta(hours=i), high) for i, high in enumerate(highs)
    ])


def zigzag(peaks, trough=BASE - 10.0, spacing=3):
    """Alternating peaks and troughs, `spacing` candles apart.

    Each peak is isolated by candles strictly below it on both sides, which
    is what a Williams Fractal at n=2 needs to confirm. Returns the high
    series, warmed up so ATR is live before the first peak.
    """
    highs = [BASE] * WARMUP
    for peak in peaks:
        highs.append(peak)
        highs.extend([trough] * spacing)
    return highs


def levels_for(highs, **kwargs):
    return compute_liquidity_levels(frame(highs), pivot_n=PIVOT_N, **kwargs)


def highs_only(levels):
    return levels[levels["side"] == "high"].reset_index(drop=True)


class TestPooling:
    def test_two_pivots_inside_tolerance_are_one_level(self):
        levels = highs_only(levels_for(zigzag([130.0, 130.0 + TOLERANCE / 2.0])))
        equals = levels[levels["kind"] == EQUALS]
        assert len(equals) == 1
        assert equals.iloc[0]["touch_count"] == 2

    def test_two_pivots_outside_tolerance_are_two_levels(self):
        levels = highs_only(levels_for(zigzag([130.0, 130.0 + TOLERANCE * 3.0])))
        assert (levels["kind"] == EQUALS).sum() == 0
        assert (levels["kind"] == OLD_POINT).sum() == 2

    def test_three_pivots_pool_into_one_level_not_three(self):
        """The cluster case. Three equal highs are one pool of liquidity,
        not three separate levels stacked on each other.
        """
        peaks = [130.0, 130.0 + TOLERANCE / 2.0, 130.0 - TOLERANCE / 2.0]
        levels = highs_only(levels_for(zigzag(peaks)))
        assert set(levels["pool_id"]) == {0}
        assert levels["touch_count"].max() == 3

    def test_the_level_is_the_running_mean_of_its_touches(self):
        levels = highs_only(levels_for(zigzag([130.0, 130.2])))
        final = levels.iloc[-1]
        assert final["touch_count"] == 2
        assert final["level"] == pytest.approx(130.1)

    def test_a_later_touch_moves_an_established_level_less(self):
        """Weighted by touch count, so a third touch shifts the level a
        third as far as the second did. An old, well-tested level should
        not be dragged around by one more tap.
        """
        levels = highs_only(levels_for(zigzag([130.0, 130.0, 130.3])))
        assert levels.iloc[-1]["level"] == pytest.approx(130.1)

    def test_highs_and_lows_never_pool_together(self):
        levels = levels_for(zigzag([130.0, 130.0]))
        for pool_id, group in levels.groupby("pool_id"):
            assert group["side"].nunique() == 1


class TestVersioning:
    """Each touch closes the previous version and opens a new one, so no
    row's kind or level was decided by a touch that had not happened yet.
    """

    def test_a_new_touch_supersedes_the_previous_version(self):
        levels = highs_only(levels_for(zigzag([130.0, 130.0])))
        first, second = levels.iloc[0], levels.iloc[1]
        assert first["ended_by"] == "superseded"
        assert first["kind"] == OLD_POINT
        assert second["kind"] == EQUALS

    def test_versions_of_one_level_never_overlap(self):
        levels = highs_only(levels_for(zigzag([130.0, 130.0, 130.0])))
        for _, group in levels.groupby("pool_id"):
            windows = group.sort_values("visible_from_index")
            previous_end = None
            for _, row in windows.iterrows():
                if previous_end is not None:
                    assert row["visible_from_index"] == previous_end + 1
                previous_end = row["valid_through_index"]

    def test_a_level_is_an_old_point_before_its_second_touch(self):
        """The no-lookahead case this whole shape exists for. Between the
        first and second pivot the level genuinely is a single old high,
        and a consumer scoring a candle in that window must read that.
        """
        levels = highs_only(levels_for(zigzag([130.0, 130.0])))
        first = levels.iloc[0]
        assert first["kind"] == OLD_POINT
        assert first["valid_through_index"] < levels.iloc[1]["visible_from_index"]


class TestSweep:
    def test_a_wick_past_the_band_sweeps_the_level(self):
        highs = zigzag([130.0]) + [140.0]
        levels = highs_only(levels_for(highs))
        swept = levels[levels["swept"]]
        assert len(swept) == 1
        assert swept.iloc[0]["ended_by"] == "swept"
        assert swept.iloc[0]["swept_index"] == len(highs) - 1

    def test_a_wick_short_of_the_band_does_not_sweep(self):
        """The band is above the pivot, so trading back to the pivot's own
        price is a retest, not a sweep.
        """
        highs = zigzag([130.0]) + [130.0]
        levels = highs_only(levels_for(highs))
        assert not levels["swept"].any()

    def test_a_swept_level_does_not_absorb_later_pivots(self):
        """Once taken, a level is gone. A later pivot at the same price
        opens a fresh pool rather than adding a touch to a dead one.

        The spike is padded away from the second peak so it cannot block
        that peak's own fractal: a candle at 140 sitting directly before a
        130 peak would stop 130 confirming at all, and the test would pass
        for the wrong reason.
        """
        trough = [BASE - 10.0] * 3
        highs = zigzag([130.0]) + [140.0] + trough + [130.0] + trough
        levels = highs_only(levels_for(highs))

        at_130 = levels[levels["level"] == 130.0]
        assert len(at_130) == 2
        assert at_130["pool_id"].nunique() == 2
        assert list(at_130["touch_count"]) == [1, 1]
        assert at_130.iloc[0]["swept"]

    def test_the_confirming_candles_never_sweep_their_own_pivot(self):
        """A fractal's own confirmation candles are strictly inside it by
        construction, so this can only break if the scan starts too early.
        """
        levels = highs_only(levels_for(zigzag([130.0])))
        assert not levels["swept"].any()


class TestExpiryAndDataEnd:
    def test_a_level_with_no_fresh_touch_expires(self):
        lookback = 30
        highs = zigzag([130.0]) + [BASE] * (lookback + 5)
        levels = highs_only(levels_for(highs, lookback=lookback))
        assert levels.iloc[0]["ended_by"] == "expired"

    def test_a_touch_resets_the_expiry_clock(self):
        """The clock runs from the LAST touch, not the first. A level
        re-tested just before it would have expired earns a fresh window,
        which is the whole reason equal highs matter more than single ones.
        """
        lookback = 20
        trough = [BASE - 10.0] * 3
        # The tail has to outlast the second touch's own window, or the
        # level ends as "data_end" and the expiry is never exercised.
        highs = zigzag([130.0]) + [BASE] * 8 + [130.0] + trough + [BASE] * 30
        levels = highs_only(levels_for(highs, lookback=lookback))

        first, second = levels.iloc[0], levels.iloc[1]
        assert second["touch_count"] == 2

        # Without the reset the level would have died a lookback after its
        # FIRST touch. It outlives that, and dies a lookback after its second.
        assert second["valid_through_index"] > first["visible_from_index"] + lookback
        assert second["ended_by"] == "expired"
        assert (
            second["valid_through_index"] - second["visible_from_index"] == lookback
        )

    def test_a_level_still_live_at_the_end_is_not_expired(self):
        """Data running out is not death, the same distinction
        order_blocks._expiry_index draws. The live bot's newest levels are
        all in this bucket on every run.
        """
        levels = highs_only(levels_for(zigzag([130.0])))
        assert levels.iloc[-1]["ended_by"] == "data_end"
        assert not levels.iloc[-1]["swept"]


class TestNoLookahead:
    def test_no_level_is_visible_before_its_pivot_confirms(self):
        levels = levels_for(zigzag([130.0, 130.0, 125.0, 140.0]))
        assert (levels["visible_from_index"] >= levels["pivot_index"] + PIVOT_N).all()

    def test_warmup_pivots_are_skipped_rather_than_given_a_fake_tolerance(self):
        """Before ATR reports, there is no answer to "how close is equal",
        and inventing one would fabricate levels over the opening fortnight
        of every instrument's history.
        """
        highs = [BASE] * 3 + [130.0] + [BASE] * 5
        assert len(compute_liquidity_levels(frame(highs), pivot_n=PIVOT_N)) == 0
