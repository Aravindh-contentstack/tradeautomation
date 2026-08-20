"""The sweep-event layer under smc/liquidity/sweeps.py.

sweeps.py grew a second consumer: the bool arrays the Swept Liquidity gates
read, and the richer event rows smc/liquidity/sweep_credit.py needs. Both come
from one producer, so these tests pin the contract between them: every event
scatters back to exactly the candle it names, and nothing else is marked.

The `expires_index` column is the reason the events exist at all, so it gets
its own tests. It must be the level's NATURAL expiry rather than
valid_through_index, which for a swept level is the sweep candle itself.
"""

import numpy as np
import pandas as pd
import pytest

from smc.liquidity import levels as levels_mod
from smc.liquidity import low_resistance as lrlq_mod
from smc.liquidity import sweeps
from smc.liquidity.levels import EQUALS, OLD_POINT
from smc.liquidity.low_resistance import LRLQ

HIGH = "high"
LOW = "low"

LENGTH = 60


def structured(length=LENGTH, swing_high=200.0, swing_low=50.0):
    """A frame carrying only what pooled_level_sweep_events reads.

    The swing range is flat and wide, so every old point in these fixtures
    is external to it and the range test never accidentally filters a row
    the test meant to keep. TestOldPointRange narrows it deliberately.
    """
    start = pd.Timestamp("2024-01-01", tz="UTC")
    return pd.DataFrame(
        {
            "date": [start + pd.Timedelta(hours=i) for i in range(length)],
            "open": [100.0] * length,
            "high": [101.0] * length,
            "low": [99.0] * length,
            "close": [100.0] * length,
            # The tier prefix is "h1_swing" and the suffix is "_swing_high",
            # so the real column name doubles the word. Matches
            # structural_sweeps and the frames backtest/pipeline.py builds.
            "h1_swing_swing_high": [swing_high] * length,
            "h1_swing_swing_low": [swing_low] * length,
        }
    )


def level_row(kind, side, level, swept_index, visible_from_index, swept=True):
    """One row shaped like smc/liquidity/levels.py's output."""
    return {
        "side": side,
        "kind": kind,
        "level": level,
        "level_top": level + 0.1,
        "level_bot": level - 0.1,
        "touch_count": 2 if kind == EQUALS else 1,
        "pool_id": 0,
        "pivot_index": visible_from_index,
        "first_pivot_index": visible_from_index,
        "visible_from_index": visible_from_index,
        "visible_from_date": pd.Timestamp("2024-01-01", tz="UTC"),
        "valid_through_index": swept_index if swept else visible_from_index + 100,
        "ended_by": "swept" if swept else "expired",
        "swept": swept,
        "swept_index": swept_index if swept else None,
        "swept_date": None,
    }


def levels_table(rows):
    return pd.DataFrame(rows, columns=levels_mod._LEVEL_COLUMNS)


def lrlq_row(side, level, swept_index, visible_from_index, swept=True):
    return {
        "side": side,
        "kind": LRLQ,
        "level": level,
        "pivot_count": 3,
        "first_pivot_index": visible_from_index - 5,
        "last_pivot_index": visible_from_index,
        "visible_from_index": visible_from_index,
        "visible_from_date": pd.Timestamp("2024-01-01", tz="UTC"),
        "valid_through_index": swept_index if swept else visible_from_index + 100,
        "ended_by": "swept" if swept else "expired",
        "swept": swept,
        "swept_index": swept_index if swept else None,
        "swept_date": None,
    }


def lrlq_table(rows):
    return pd.DataFrame(rows, columns=lrlq_mod._LRLQ_COLUMNS)


class TestScatterRoundTrip:
    """Every event lands on its own candle, and no other candle is marked."""

    def test_pooled_events_scatter_to_their_own_indices(self):
        table = levels_table([
            level_row(OLD_POINT, LOW, 40.0, swept_index=30, visible_from_index=10),
            level_row(EQUALS, HIGH, 210.0, swept_index=42, visible_from_index=20),
        ])
        df = structured()

        events = sweeps.pooled_level_sweep_events(table, df, "h1_swing")
        arrays = sweeps.pooled_level_sweeps(table, df, "h1_swing")

        assert len(events) == 2
        for row in events.itertuples(index=False):
            assert arrays[(row.kind, row.side)][row.swept_index]
        # Exactly one candle marked per (kind, side) that had an event, and
        # zero for the two combinations no fixture row produced.
        assert arrays[(OLD_POINT, LOW)].sum() == 1
        assert arrays[(EQUALS, HIGH)].sum() == 1
        assert arrays[(OLD_POINT, HIGH)].sum() == 0
        assert arrays[(EQUALS, LOW)].sum() == 0

    def test_unswept_rows_produce_no_events(self):
        table = levels_table([
            level_row(OLD_POINT, LOW, 40.0, swept_index=30,
                      visible_from_index=10, swept=False),
        ])
        df = structured()

        assert len(sweeps.pooled_level_sweep_events(table, df, "h1_swing")) == 0
        arrays = sweeps.pooled_level_sweeps(table, df, "h1_swing")
        assert not any(array.any() for array in arrays.values())

    def test_lrlq_events_scatter(self):
        table = lrlq_table([lrlq_row(LOW, 45.0, swept_index=25, visible_from_index=8)])

        events = sweeps.lrlq_sweep_events(table)
        arrays = sweeps.lrlq_sweeps(table, LENGTH)

        assert len(events) == 1
        assert arrays[(LRLQ, LOW)][25]
        assert arrays[(LRLQ, LOW)].sum() == 1
        assert arrays[(LRLQ, HIGH)].sum() == 0

    def test_empty_tables_give_empty_events_and_blank_arrays(self):
        df = structured()
        empty_levels = levels_table([])
        empty_lrlq = lrlq_table([])

        assert len(sweeps.pooled_level_sweep_events(empty_levels, df, "h1_swing")) == 0
        assert len(sweeps.lrlq_sweep_events(empty_lrlq)) == 0

        arrays = sweeps.pooled_level_sweeps(empty_levels, df, "h1_swing")
        assert set(arrays) == {
            (EQUALS, HIGH), (EQUALS, LOW), (OLD_POINT, HIGH), (OLD_POINT, LOW),
        }
        assert all(len(array) == LENGTH for array in arrays.values())
        assert not any(array.any() for array in arrays.values())


class TestExpiresIndex:
    """The natural expiry, not valid_through_index."""

    def test_pooled_expiry_is_visible_from_plus_lookback(self):
        table = levels_table([
            level_row(OLD_POINT, LOW, 40.0, swept_index=30, visible_from_index=10),
        ])
        events = sweeps.pooled_level_sweep_events(table, structured(), "h1_swing")

        assert events["expires_index"].iloc[0] == 10 + levels_mod.DEFAULT_LOOKBACK

    def test_expiry_ignores_valid_through_index(self):
        """A swept level's valid_through_index IS the sweep candle.

        Reading it would cap the credit at zero candles and silently disable
        the whole mitigation-leg gate, so this is the regression that matters
        most in the file.
        """
        table = levels_table([
            level_row(OLD_POINT, LOW, 40.0, swept_index=30, visible_from_index=10),
        ])
        assert table["valid_through_index"].iloc[0] == 30

        events = sweeps.pooled_level_sweep_events(table, structured(), "h1_swing")
        assert events["expires_index"].iloc[0] > 30

    def test_lrlq_expiry_uses_its_own_lookback(self):
        table = lrlq_table([lrlq_row(LOW, 45.0, swept_index=25, visible_from_index=8)])
        events = sweeps.lrlq_sweep_events(table)

        assert events["expires_index"].iloc[0] == 8 + lrlq_mod.DEFAULT_LOOKBACK


class TestOldPointRange:
    """The external-to-swing-range filter survives the refactor."""

    def test_old_point_inside_the_range_is_dropped(self):
        # Level 120 sits inside a 50-to-200 swing range, so it is part of the
        # leg price is working through rather than an old extreme left behind.
        table = levels_table([
            level_row(OLD_POINT, LOW, 120.0, swept_index=30, visible_from_index=10),
        ])
        events = sweeps.pooled_level_sweep_events(table, structured(), "h1_swing")
        assert len(events) == 0

    def test_equals_inside_the_range_is_kept(self):
        table = levels_table([
            level_row(EQUALS, LOW, 120.0, swept_index=30, visible_from_index=10),
        ])
        events = sweeps.pooled_level_sweep_events(table, structured(), "h1_swing")
        assert len(events) == 1


class TestTimeLevelEvents:
    """Session and previous-day/week levels, whose cap is a clock."""

    def time_table(self, kind, side, level, visible_from, valid_through):
        return pd.DataFrame([{
            "kind": kind,
            "side": side,
            "level": level,
            "visible_from_date": visible_from,
            "valid_through_date": valid_through,
        }])

    def frame_with_low_at(self, index, low, length=LENGTH):
        start = pd.Timestamp("2024-01-01", tz="UTC")
        lows = [99.0] * length
        lows[index] = low
        return pd.DataFrame({
            "date": [start + pd.Timedelta(hours=i) for i in range(length)],
            "open": [100.0] * length,
            "high": [101.0] * length,
            "low": lows,
            "close": [100.0] * length,
        })

    def test_first_taking_candle_becomes_the_event(self):
        df = self.frame_with_low_at(20, 90.0)
        start = pd.Timestamp("2024-01-01", tz="UTC")
        table = self.time_table(
            "previous_day", LOW, 95.0,
            start + pd.Timedelta(hours=10), start + pd.Timedelta(hours=40),
        )

        events = sweeps.time_level_sweep_events(table, "previous_day", df)

        assert len(events) == 1
        assert events["swept_index"].iloc[0] == 20
        assert events["level"].iloc[0] == 95.0

    def test_expiry_is_the_end_of_the_validity_window(self):
        """A session level's credit inherits the clock it was built from,
        not the 100-candle lookback the pivot-derived kinds use.
        """
        df = self.frame_with_low_at(20, 90.0)
        start = pd.Timestamp("2024-01-01", tz="UTC")
        table = self.time_table(
            "asian", LOW, 95.0,
            start + pd.Timedelta(hours=10), start + pd.Timedelta(hours=40),
        )

        events = sweeps.time_level_sweep_events(table, "asian", df)
        assert events["expires_index"].iloc[0] == 39

    def test_a_level_outside_its_window_is_never_taken(self):
        # The only candle that reaches 90 sits at index 20, well before the
        # window opens at hour 30.
        df = self.frame_with_low_at(20, 90.0)
        start = pd.Timestamp("2024-01-01", tz="UTC")
        table = self.time_table(
            "previous_day", LOW, 95.0,
            start + pd.Timedelta(hours=30), start + pd.Timedelta(hours=40),
        )

        assert len(sweeps.time_level_sweep_events(table, "previous_day", df)) == 0

    def test_scatter_matches_the_event(self):
        df = self.frame_with_low_at(20, 90.0)
        start = pd.Timestamp("2024-01-01", tz="UTC")
        table = self.time_table(
            "previous_day", LOW, 95.0,
            start + pd.Timedelta(hours=10), start + pd.Timedelta(hours=40),
        )

        arrays = sweeps.time_level_sweeps(table, "previous_day", df)
        assert arrays[("previous_day", LOW)][20]
        assert arrays[("previous_day", LOW)].sum() == 1
        assert arrays[("previous_day", HIGH)].sum() == 0
