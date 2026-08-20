"""Reconciling liquidity levels onto the H1 timeline without lookahead.

Same standard as tests/test_ob_state.py, and for the same reason: the
property worth asserting is not "the arithmetic is right" but "a level
cannot be read before the market could have known about it". So the
visibility assertions are made against pandas' own merge_asof, the mechanism
the rest of the pipeline already trusts for exactly this, rather than
against a second hand-rolled calculation that could be wrong in the same
direction.
"""

import numpy as np
import pandas as pd
import pytest

from smc.liquidity import liq_state
from smc.liquidity.liq_state import ABOVE, BELOW, ZONE, LevelSeries

HOUR = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)


def h1_timestamps(count, start="2024-01-01 00:00"):
    return pd.DatetimeIndex(
        pd.date_range(pd.Timestamp(start, tz="UTC"), periods=count, freq="1h")
    )


def daily_timestamps(count, start="2024-01-01 00:00"):
    return pd.DatetimeIndex(
        pd.date_range(pd.Timestamp(start, tz="UTC"), periods=count, freq="1D")
    )


def level_table(rows):
    """A minimal table in the shape levels.py and low_resistance.py emit."""
    return pd.DataFrame([
        {
            "side": row.get("side", "high"),
            "level": row.get("level", 100.0),
            "level_top": row.get("level_top", row.get("level", 100.0)),
            "level_bot": row.get("level_bot", row.get("level", 100.0)),
            "visible_from_index": row["visible_from_index"],
            "valid_through_index": row["valid_through_index"],
        }
        for row in rows
    ])


def merge_asof_visibility(tf_dates, h1_ts, duration, tf_index):
    """The H1 bar merge_asof would first attach tf row `tf_index` to.

    The mechanism the pipeline already uses, run in miniature: a
    higher-timeframe row carries its own close time, and an H1 bar sees the
    newest row whose close time is at or before the bar.
    """
    right = pd.DataFrame({
        "close_time": pd.DatetimeIndex(tf_dates) + duration,
        "tf_index": np.arange(len(tf_dates)),
    })
    left = pd.DataFrame({"date": h1_ts})
    merged = pd.merge_asof(
        left, right, left_on="date", right_on="close_time", direction="backward"
    )
    visible = merged.index[merged["tf_index"] >= tf_index]
    return int(visible[0])


class TestVisibility:
    def test_a_daily_level_appears_exactly_where_merge_asof_puts_it(self):
        """The assertion the whole design rests on. A Daily level becomes
        readable on exactly the H1 bar merge_asof would first attach that
        Daily row to, so the liquidity path and the column path can never
        disagree about what was knowable when.
        """
        h1_ts = h1_timestamps(24 * 5)
        daily = daily_timestamps(5)
        table = level_table([{"visible_from_index": 2, "valid_through_index": 3}])

        series = liq_state.indexed_series(table, "equals", "Daily", daily, h1_ts)
        expected = merge_asof_visibility(daily, h1_ts, DAY, 2)
        assert int(series.visible_from[0]) == expected

    def test_an_h1_level_is_readable_the_bar_after_it_confirms(self):
        """On H1 the duration is one hour, so the same expression lands on
        index + 1. A level confirmed by a candle cannot be traded on that
        candle, which has not closed yet.
        """
        h1_ts = h1_timestamps(20)
        table = level_table([{"visible_from_index": 5, "valid_through_index": 9}])
        series = liq_state.indexed_series(table, "equals", "H1", h1_ts, h1_ts)
        assert int(series.visible_from[0]) == 6

    def test_a_level_live_to_the_end_of_history_is_not_cut_short(self):
        """Data running out is not death. Clamping here would hide the live
        bot's newest levels on every run, the same trap
        order_blocks._expiry_index avoids.
        """
        h1_ts = h1_timestamps(24 * 4)
        daily = daily_timestamps(4)
        table = level_table([{"visible_from_index": 1, "valid_through_index": 3}])
        series = liq_state.indexed_series(table, "equals", "Daily", daily, h1_ts)
        assert int(series.valid_through[0]) == len(h1_ts) - 1

    def test_a_dead_level_stops_at_the_end_of_its_last_live_candle(self):
        """A level swept on Daily candle 2 is a live target through candle
        2's final H1 bar and dead from candle 3's first. That is as
        precisely as a Daily fact can be placed on an H1 timeline, and it
        is the same rule ob_state.py applies to a zone's valid_through.
        """
        h1_ts = h1_timestamps(24 * 5)
        daily = daily_timestamps(5)
        table = level_table([{"visible_from_index": 1, "valid_through_index": 2}])
        series = liq_state.indexed_series(table, "equals", "Daily", daily, h1_ts)

        first_dead = merge_asof_visibility(daily, h1_ts, DAY, 2)
        assert int(series.valid_through[0]) == first_dead - 1

    def test_a_dead_level_does_not_get_an_extra_candle_of_life(self):
        """The off-by-one this is here to catch. Mapping candle i+1 instead
        of candle i reads as "dead from the candle after the one that killed
        it", which hands every swept level a full extra Daily candle as a
        target: 24 H1 bars of a draw that is no longer there.
        """
        h1_ts = h1_timestamps(24 * 5)
        daily = daily_timestamps(5)
        table = level_table([{"visible_from_index": 1, "valid_through_index": 2}])
        series = liq_state.indexed_series(table, "equals", "Daily", daily, h1_ts)

        one_candle_too_far = merge_asof_visibility(daily, h1_ts, DAY, 3) - 1
        assert int(series.valid_through[0]) < one_candle_too_far
        assert one_candle_too_far - int(series.valid_through[0]) == 24


class TestTimeLevelSweeps:
    """Time-based levels are the one kind with no sweep state of their own,
    because a session high is a fact about a clock and has no timeframe of
    its own to be swept on. liq_state resolves theirs against H1.

    Without this, the Liquidity Target gate keeps offering yesterday's high
    as a draw long after price has run it, which is the exact opposite of
    the user's "becomes NA as and when price covers these liquidities".
    """

    def table(self, level, side, visible_from, valid_through):
        return pd.DataFrame([{
            "kind": "previous_day",
            "side": side,
            "level": level,
            "visible_from_date": visible_from,
            "valid_through_date": valid_through,
        }])

    def test_a_level_stops_being_live_once_price_takes_it(self):
        h1_ts = h1_timestamps(10)
        table = self.table(110.0, "high", h1_ts[0], h1_ts[-1])

        highs = np.full(10, 100.0)
        highs[4] = 111.0
        series = liq_state.time_series(
            table, "previous_day", "H1", h1_ts, highs=highs, lows=np.full(10, 90.0)
        )
        assert int(series.valid_through[0]) == 4

    def test_it_is_live_through_the_bar_that_takes_it(self):
        """Same rule order_blocks._kill applies to a zone: the candle that
        runs the liquidity is the one price reacts on, so it counts.
        """
        h1_ts = h1_timestamps(10)
        table = self.table(110.0, "high", h1_ts[0], h1_ts[-1])
        highs = np.full(10, 100.0)
        highs[4] = 111.0
        series = liq_state.time_series(
            table, "previous_day", "H1", h1_ts, highs=highs, lows=np.full(10, 90.0)
        )
        assert int(series.valid_through[0]) >= 4

    def test_touching_without_exceeding_is_not_taking_it(self):
        h1_ts = h1_timestamps(10)
        table = self.table(110.0, "high", h1_ts[0], h1_ts[-1])
        highs = np.full(10, 110.0)
        series = liq_state.time_series(
            table, "previous_day", "H1", h1_ts, highs=highs, lows=np.full(10, 90.0)
        )
        assert int(series.valid_through[0]) > 4

    def test_the_low_side_is_taken_downward(self):
        h1_ts = h1_timestamps(10)
        table = self.table(90.0, "low", h1_ts[0], h1_ts[-1])
        lows = np.full(10, 100.0)
        lows[6] = 89.0
        series = liq_state.time_series(
            table, "previous_day", "H1", h1_ts, highs=np.full(10, 110.0), lows=lows
        )
        assert int(series.valid_through[0]) == 6

    def test_a_sweep_before_the_level_is_visible_does_not_count(self):
        """Yesterday's high is obviously exceeded during yesterday. Only
        bars inside the level's own window can take it.
        """
        h1_ts = h1_timestamps(10)
        table = self.table(110.0, "high", h1_ts[5], h1_ts[-1])
        highs = np.full(10, 100.0)
        highs[2] = 120.0
        series = liq_state.time_series(
            table, "previous_day", "H1", h1_ts, highs=highs, lows=np.full(10, 90.0)
        )
        assert int(series.valid_through[0]) > 5

    def test_without_extremes_the_window_is_left_alone(self):
        h1_ts = h1_timestamps(10)
        table = self.table(110.0, "high", h1_ts[0], h1_ts[-1])
        series = liq_state.time_series(table, "previous_day", "H1", h1_ts)
        assert int(series.valid_through[0]) == 8


class TestLastClosedCandle:
    def test_it_is_the_mirror_of_the_visibility_rule(self):
        """Visibility asks "when does this row become readable"; this asks
        "which row is the newest readable one". They have to agree, or the
        Swept Liquidity gate would read a different candle than the one the
        rest of the pipeline thinks is current.
        """
        # The H1 range runs a day PAST the daily range, so every daily
        # candle's close falls inside it. Without the extra day the last
        # daily candle never closes and has no first-visible bar at all.
        h1_ts = h1_timestamps(24 * 5)
        daily = daily_timestamps(4)
        last_closed = liq_state._last_closed_candle(daily, h1_ts, "Daily")

        for tf_index in range(1, 4):
            first_visible = merge_asof_visibility(daily, h1_ts, DAY, tf_index)
            assert int(last_closed[first_visible]) == tf_index
            assert int(last_closed[first_visible - 1]) == tf_index - 1

    def test_bars_before_the_first_close_report_minus_one(self):
        h1_ts = h1_timestamps(24 * 3)
        daily = daily_timestamps(3)
        last_closed = liq_state._last_closed_candle(daily, h1_ts, "Daily")
        assert int(last_closed[0]) == -1

    def test_carry_to_h1_reads_false_before_the_first_close(self):
        """A bare negative index would silently read from the END of the
        per-candle array, reporting a sweep that has not happened yet.
        """
        h1_ts = h1_timestamps(24 * 3)
        daily = daily_timestamps(3)
        last_closed = liq_state._last_closed_candle(daily, h1_ts, "Daily")

        # Daily candle 2 has not closed by the last H1 bar here, so the
        # flag under test is candle 1's. Bar 0 sees no closed candle at all
        # and must read False rather than wrapping to the end of the array.
        per_candle = np.array([False, True, False])
        carried = liq_state.carry_to_h1(per_candle, last_closed, len(h1_ts))
        assert not carried[0]
        assert carried[-1]

        # The wrap this guards against: candle 2's flag must not leak
        # backwards onto bars where no candle had closed.
        leaky = liq_state.carry_to_h1(
            np.array([False, False, True]), last_closed, len(h1_ts)
        )
        assert not leaky.any()


class TestTargets:
    def series_of(self, sign, level, top=None, bottom=None, n=5):
        return LevelSeries(
            timeframe="H1",
            kind="equals",
            sign=np.array([sign], dtype=np.int8),
            level=np.array([level], dtype=float),
            top=np.array([top if top is not None else level], dtype=float),
            bottom=np.array([bottom if bottom is not None else level], dtype=float),
            visible_from=np.array([0], dtype=np.int64),
            valid_through=np.array([n - 1], dtype=np.int64),
        )

    def test_a_level_above_the_close_is_a_target_above(self):
        series = self.series_of(ABOVE, 110.0)
        closes = np.full(5, 100.0)
        above, below = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes
        )
        assert above[0] == 110.0
        assert np.isnan(below[0])

    def test_the_nearest_level_wins(self):
        series = LevelSeries(
            timeframe="H1", kind="equals",
            sign=np.array([ABOVE, ABOVE], dtype=np.int8),
            level=np.array([120.0, 105.0]),
            top=np.array([120.0, 105.0]),
            bottom=np.array([120.0, 105.0]),
            visible_from=np.array([0, 0], dtype=np.int64),
            valid_through=np.array([4, 4], dtype=np.int64),
        )
        closes = np.full(5, 100.0)
        above, _ = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes
        )
        assert above[0] == 105.0

    def test_a_dead_level_is_no_longer_a_target(self):
        series = self.series_of(ABOVE, 110.0)
        series.valid_through[0] = 1
        closes = np.full(5, 100.0)
        above, _ = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes
        )
        assert above[1] == 110.0
        assert np.isnan(above[2])

    def test_a_zone_is_measured_to_its_near_edge(self):
        """Price reaches the bottom of a gap above it first, so that is the
        distance worth measuring, not the far side or the middle.
        """
        series = self.series_of(ZONE, np.nan, top=115.0, bottom=110.0)
        closes = np.full(5, 100.0)
        above, _ = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes
        )
        assert above[0] == 110.0

    def test_an_old_point_inside_the_swing_range_is_not_a_target(self):
        """"External to the swing range" is part of what makes a level an
        old point, not an extra condition on top. A high inside the range is
        part of the leg price is currently working through.
        """
        series = self.series_of(ABOVE, 110.0)
        closes = np.full(5, 100.0)
        live = liq_state._live_rows(series, 5)

        inside = np.full(5, 120.0)   # swing high above the level
        above, _ = liq_state._build_targets(
            series, 5, live, closes, range_high=inside, range_low=np.full(5, 80.0)
        )
        assert np.isnan(above).all()

        outside = np.full(5, 105.0)  # swing high below the level
        above, _ = liq_state._build_targets(
            series, 5, live, closes, range_high=outside, range_low=np.full(5, 80.0)
        )
        assert above[0] == 110.0

    def test_the_range_test_is_asked_per_bar(self):
        """The range moves, so a level can be external one bar and internal
        the next. Baking the answer in at detection time would let a later
        swing decide an earlier bar's score.
        """
        series = self.series_of(ABOVE, 110.0)
        closes = np.full(5, 100.0)
        range_high = np.array([105.0, 105.0, 120.0, 120.0, 105.0])
        above, _ = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes,
            range_high=range_high, range_low=np.full(5, 80.0),
        )
        assert above[0] == 110.0
        assert np.isnan(above[2])
        assert above[4] == 110.0

    def test_a_warming_up_range_admits_nothing(self):
        """NaN comparisons are False, so an unconfirmed swing range keeps no
        old points rather than keeping all of them.
        """
        series = self.series_of(ABOVE, 110.0)
        above, _ = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), np.full(5, 100.0),
            range_high=np.full(5, np.nan), range_low=np.full(5, np.nan),
        )
        assert np.isnan(above).all()

    def test_only_old_points_are_range_filtered(self):
        """Equals inside the range are still liquidity: two highs at the
        same price are a stack of stops wherever they sit.
        """
        series = self.series_of(ABOVE, 110.0)
        universe = liq_state.build_liquidity_universe(
            {("Daily", "equals"): series, ("Daily", "old_point"): series},
            {},
            np.full(5, 100.0),
            swing_ranges={"Daily": (np.full(5, 120.0), np.full(5, 80.0))},
        )
        assert universe.target_above[("Daily", "equals")][0] == 110.0
        assert np.isnan(universe.target_above[("Daily", "old_point")][0])

    def test_a_zone_straddling_the_close_is_neither_above_nor_below(self):
        """Price is already inside it, so it is not somewhere to be drawn
        to.
        """
        series = self.series_of(ZONE, np.nan, top=110.0, bottom=90.0)
        closes = np.full(5, 100.0)
        above, below = liq_state._build_targets(
            series, 5, liq_state._live_rows(series, 5), closes
        )
        assert np.isnan(above[0])
        assert np.isnan(below[0])


class TestSliceUniverse:
    def universe(self, n=10, credit=True):
        series = LevelSeries(
            timeframe="H1", kind="equals",
            sign=np.array([ABOVE], dtype=np.int8),
            level=np.array([110.0]),
            top=np.array([110.0]),
            bottom=np.array([110.0]),
            visible_from=np.array([3], dtype=np.int64),
            valid_through=np.array([7], dtype=np.int64),
        )
        mitigation_credit = None
        if credit:
            mitigation_credit = {
                ("equals", "low"): np.arange(n, dtype=float) + 100.0
            }
        return liq_state.LiquidityUniverse(
            n=n,
            series={("H1", "equals"): series},
            swept_last_candle={("H1", "equals", "high"): np.arange(n) % 2 == 0},
            target_above={("H1", "equals"): np.arange(n, dtype=float)},
            target_below={("H1", "equals"): np.full(n, np.nan)},
            mitigation_credit=mitigation_credit,
        )

    def test_a_window_rebases_every_index(self):
        sliced = liq_state.slice_universe(self.universe(), 2, 8)
        series = sliced.series[("H1", "equals")]
        assert sliced.n == 6
        assert int(series.visible_from[0]) == 1
        assert int(series.valid_through[0]) == 5

    def test_a_level_formed_before_the_window_is_live_from_its_start(self):
        """Clamped rather than dropped: a level that formed in December can
        still be traded in January.
        """
        sliced = liq_state.slice_universe(self.universe(), 5, 10)
        assert int(sliced.series[("H1", "equals")].visible_from[0]) == 0

    def test_per_bar_arrays_are_cut_to_the_same_window(self):
        sliced = liq_state.slice_universe(self.universe(), 2, 8)
        assert len(sliced.swept_last_candle[("H1", "equals", "high")]) == 6
        assert sliced.target_above[("H1", "equals")][0] == 2.0

    def test_prices_survive_windowing_untouched(self):
        """Target arrays hold PRICES, not row ids, so a window needs no
        remapping of them. This is why.
        """
        full = self.universe()
        sliced = liq_state.slice_universe(full, 3, 9)
        assert sliced.series[("H1", "equals")].level[0] == 110.0

    def test_mitigation_credit_is_windowed(self):
        """Live parity. build_live_context keeps only the last 200 bars, so a
        credit chain that opened months earlier has to still read correctly
        inside the window. It does because the array carries the ANSWER (the
        surviving level's price) rather than the indices behind it, so the
        slice equals the full-history array over the same range.
        """
        full = self.universe()
        sliced = liq_state.slice_universe(full, 3, 9)

        expected = full.mitigation_credit[("equals", "low")][3:9]
        np.testing.assert_array_equal(
            sliced.mitigation_credit[("equals", "low")], expected
        )
        assert len(sliced.mitigation_credit[("equals", "low")]) == 6

    def test_a_universe_without_mitigation_credit_slices_without_raising(self):
        """Nothing constructs one of these any more, but the field carries a
        default so older callers and fixtures keep working.
        """
        sliced = liq_state.slice_universe(self.universe(credit=False), 2, 8)
        assert sliced.mitigation_credit is None
