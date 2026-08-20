"""Reconciling order blocks onto the H1 timeline without lookahead.

The property that matters here is not "the arithmetic is right", it is
"a zone cannot be read before the market could have known about it". So
the visibility assertions are made against pandas' own merge_asof, the
mechanism the rest of the pipeline already trusts for exactly this, rather
than against a second hand-rolled calculation that could be wrong in the
same direction.
"""

import numpy as np
import pandas as pd
import pytest

from smc.order_blocks import ob_state


def h1_timestamps(count, start="2024-01-01 00:00"):
    return pd.DatetimeIndex(
        pd.date_range(pd.Timestamp(start, tz="UTC"), periods=count, freq="1h")
    )


def daily_timestamps(count, start="2024-01-01 00:00"):
    return pd.DatetimeIndex(
        pd.date_range(pd.Timestamp(start, tz="UTC"), periods=count, freq="1D")
    )


def ob_table(rows, dates):
    """A minimal OB table in the shape compute_order_blocks emits."""
    records = []
    for row in rows:
        formed = row["formed_index"]
        trigger = row.get("earliest_trigger_index", formed)
        records.append({
            "direction": row.get("direction", "bullish"),
            "top": row.get("top", 20.0),
            "bottom": row.get("bottom", 10.0),
            "formed_index": formed,
            "earliest_trigger_index": trigger,
            "earliest_trigger_date": dates[trigger],
            "mitigated_index": row.get("mitigated_index"),
            "invalidated_index": row.get("invalidated_index"),
            "qualifying_touch_indices": row.get("qualifying_touch_indices", []),
            "swept_liquidity_fvg_stale_from_index": row.get(
                "swept_liquidity_fvg_stale_from_index"
            ),
        })
    return pd.DataFrame(records)


class TestVisibility:
    def test_visibility_matches_merge_asof_for_a_daily_zone(self):
        """The one assertion the whole design rests on. A Daily zone
        becomes readable on exactly the H1 bar that merge_asof would first
        attach that Daily row to, so the OB path and the column path can
        never disagree about what was knowable when.
        """
        h1_ts = h1_timestamps(24 * 5)
        d_dates = daily_timestamps(5)

        trigger = 2
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": trigger}], d_dates)
        series = ob_state.to_h1_space(table, d_dates, h1_ts, "Daily")

        # What merge_asof itself would do with that Daily row.
        daily = pd.DataFrame({"close_time": d_dates + pd.Timedelta(days=1),
                              "marker": range(len(d_dates))})
        merged = pd.merge_asof(
            pd.DataFrame({"date": h1_ts}),
            daily,
            left_on="date",
            right_on="close_time",
            direction="backward",
        )
        first_visible = int(np.flatnonzero(merged["marker"].to_numpy() == trigger)[0])

        assert int(series.visible_from[0]) == first_visible

    def test_an_h1_zone_becomes_visible_the_bar_after_its_trigger(self):
        """Matching _apply_mitigation, which scans from
        earliest_trigger_index + 1: the trigger candle itself is the one
        that revealed the zone, not one that could trade it.
        """
        h1_ts = h1_timestamps(10)
        table = ob_table([{"formed_index": 1, "earliest_trigger_index": 4}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.visible_from[0]) == 5

    def test_visibility_follows_the_trigger_not_the_anchor(self):
        """The anchor sits in the past relative to the break that reveals
        it. Reading formed_index here would be a lookahead of a whole leg.
        """
        h1_ts = h1_timestamps(20)
        table = ob_table([{"formed_index": 2, "earliest_trigger_index": 12}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.visible_from[0]) == 13


class TestValidWindow:
    def test_valid_through_is_the_killing_candle_itself(self):
        """valid_through carries the X+1 rule so callers never re-derive
        it: the zone is usable ON the bar that invalidates it.
        """
        h1_ts = h1_timestamps(10)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 1,
                           "invalidated_index": 6}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.valid_through[0]) == 6

    def test_a_zone_that_never_dies_stays_valid_to_the_last_bar(self):
        h1_ts = h1_timestamps(10)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 1}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.valid_through[0]) == 9

    def test_a_zone_never_mitigated_reports_beyond_the_data(self):
        """mitigated_at is compared against a bar number, so "never" has
        to be a value no bar can reach rather than a null.
        """
        h1_ts = h1_timestamps(10)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 1}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.mitigated_at[0]) >= len(h1_ts)


class TestFvgStaleFrom:
    """fvg_stale_from is the end-boundary mirror of flip_known_from: an
    H1-space index beyond which the swept_liquidity_fvg factor must go
    silent. Same conversion machinery, same visibility test, opposite
    reading of the result.
    """

    def test_h1_space_conversion_matches_flip_known_froms_own_mechanism(self):
        """No separate assertion needed for the underlying arithmetic:
        both fields go through the identical _to_h1_index call, so this
        just confirms fvg_stale_from is actually wired up to it.
        """
        h1_ts = h1_timestamps(24 * 5)
        d_dates = daily_timestamps(5)
        stale_from = 2

        table = ob_table([{
            "formed_index": 0, "earliest_trigger_index": 0,
            "swept_liquidity_fvg_stale_from_index": stale_from,
        }], d_dates)
        series = ob_state.to_h1_space(table, d_dates, h1_ts, "Daily")

        daily = pd.DataFrame({"close_time": d_dates + pd.Timedelta(days=1),
                              "marker": range(len(d_dates))})
        merged = pd.merge_asof(
            pd.DataFrame({"date": h1_ts}), daily,
            left_on="date", right_on="close_time", direction="backward",
        )
        expected = int(np.flatnonzero(merged["marker"].to_numpy() == stale_from)[0])
        assert int(series.fvg_stale_from[0]) == expected

    def test_a_row_with_no_matched_gap_never_goes_stale(self):
        """None (no gap matched swept_liquidity_fvg=False in the first
        place) maps to the same "beyond the last valid index" sentinel
        flip_known_from uses for "not applicable".
        """
        h1_ts = h1_timestamps(10)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 1}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        assert int(series.fvg_stale_from[0]) == len(h1_ts)

    def test_slicing_clips_a_boundary_already_passed_to_minus_one(self):
        h1_ts = h1_timestamps(40)
        table = ob_table([{
            "formed_index": 0, "earliest_trigger_index": 1,
            "swept_liquidity_fvg_stale_from_index": 5,
        }], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(40, 30.0), np.full(40, 25.0), np.full(40, 27.0),
        )
        window = ob_state.slice_universe(universe, 10, 40)
        assert int(window.series["H1"].fvg_stale_from[0]) == -1

    def test_slicing_clips_a_boundary_beyond_the_window_to_its_length(self):
        h1_ts = h1_timestamps(40)
        table = ob_table([{
            "formed_index": 0, "earliest_trigger_index": 1,
            "swept_liquidity_fvg_stale_from_index": 35,
        }], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(40, 30.0), np.full(40, 25.0), np.full(40, 27.0),
        )
        window = ob_state.slice_universe(universe, 0, 20)
        assert int(window.series["H1"].fvg_stale_from[0]) == 20

    def test_slicing_preserves_an_in_window_boundary_exactly(self):
        h1_ts = h1_timestamps(40)
        table = ob_table([{
            "formed_index": 0, "earliest_trigger_index": 1,
            "swept_liquidity_fvg_stale_from_index": 25,
        }], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(40, 30.0), np.full(40, 25.0), np.full(40, 27.0),
        )
        window = ob_state.slice_universe(universe, 10, 40)
        # On H1 the duration is one hour, so to_h1_index maps local row 25
        # to full-space index 26 (the first bar at or after that row's
        # close); windowed from 10, that lands at 16.
        assert int(window.series["H1"].fvg_stale_from[0]) == 16


class TestTriggers:
    def test_each_qualifying_touch_becomes_its_own_trigger_bar(self):
        h1_ts = h1_timestamps(12)
        table = ob_table([{
            "formed_index": 0,
            "earliest_trigger_index": 1,
            "qualifying_touch_indices": [4, 7],
        }], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(12, 30.0), np.full(12, 25.0), np.full(12, 27.0),
        )
        assert int(universe.trigger_ob[5]) == 0
        assert int(universe.trigger_touch_no[5]) == 1
        assert int(universe.trigger_ob[8]) == 0
        assert int(universe.trigger_touch_no[8]) == 2
        assert int(universe.trigger_ob[6]) == -1

    def test_the_freshest_zone_wins_a_same_bar_collision(self):
        """Two zones touched on one candle can imply opposite trades, so
        the tie-break has to be deliberate. The newer zone is the one
        price is actually reacting to.
        """
        h1_ts = h1_timestamps(12)
        table = ob_table([
            {"formed_index": 0, "earliest_trigger_index": 1,
             "qualifying_touch_indices": [6]},
            {"formed_index": 2, "earliest_trigger_index": 4,
             "direction": "bearish", "qualifying_touch_indices": [6]},
        ], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(12, 30.0), np.full(12, 25.0), np.full(12, 27.0),
        )
        assert int(universe.trigger_ob[7]) == 1


class TestWindowing:
    def test_slicing_preserves_the_usable_window(self):
        """run_year rebases per calendar year, so a zone straddling the
        boundary has to stay tradeable inside the window rather than
        vanishing with the bars that formed it.
        """
        h1_ts = h1_timestamps(40)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 2,
                           "invalidated_index": 30}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(40, 30.0), np.full(40, 25.0), np.full(40, 27.0),
        )

        window = ob_state.slice_universe(universe, 10, 40)
        sliced = window.series["H1"]
        assert window.n == 30
        # Formed before the window, so live from its first bar.
        assert int(sliced.visible_from[0]) == 0
        assert int(sliced.valid_through[0]) == 20

    def test_a_zone_dead_before_the_window_is_never_usable_in_it(self):
        h1_ts = h1_timestamps(40)
        table = ob_table([{"formed_index": 0, "earliest_trigger_index": 1,
                           "invalidated_index": 5}], h1_ts)
        series = ob_state.to_h1_space(table, h1_ts, h1_ts, "H1")
        universe = ob_state.build_ob_universe(
            {"H1": series},
            np.full(40, 30.0), np.full(40, 25.0), np.full(40, 27.0),
        )
        sliced = ob_state.slice_universe(universe, 20, 40).series["H1"]
        assert int(sliced.valid_through[0]) < int(sliced.visible_from[0])
