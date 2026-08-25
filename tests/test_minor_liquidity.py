"""Minor liquidity: the two sources, the no-lookahead rule, and sweeps.

Fixtures are built by `candles`, which takes explicit OHLC so the green/
red test and the next-candle test can both be controlled directly. That
matters more here than in the other liquidity tests: this detector is the
only one that reads open and close at all, so a fixture helper that
synthesised them from high and low (the way test_low_resistance.py's
`_candle` does) could not express the rules under test.

The bearish reading is the one the docstrings use, so the high side is
asserted in full and the low side is asserted as a mirror spot check on
every rule that has a direction.
"""

import pandas as pd

from smc.liquidity.minor_liquidity import (
    BOTH,
    DEFAULT_LOOKBACK,
    FRACTAL,
    HIGH,
    LOW,
    MINOR,
    REJECTION,
    compute_minor_liquidity,
)

START = pd.Timestamp("2024-01-01", tz="UTC")


def candles(bars):
    """[(o, h, l, c), ...] -> an M15 frame at 15-minute spacing."""
    return pd.DataFrame([
        {
            "date": START + pd.Timedelta(minutes=15 * i),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        }
        for i, (o, h, l, c) in enumerate(bars)
    ])


def flat(count, price=100.0):
    """Filler candles that neither confirm nor sweep anything.

    Doji at a price far below every high-side fixture level, so they
    cannot sweep a high, and they are neither green nor red so they
    contribute no rejection level of their own.
    """
    return [(price, price + 0.5, price - 0.5, price)] * count


def levels_at(table, side):
    return table[table["side"] == side].reset_index(drop=True)


class TestRejectionSource:
    def test_green_candle_rejected_by_next_makes_a_high(self):
        # Candle 1 is green and reaches 110. Candle 2 fails to exceed it.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + flat(3)
        ))
        highs = levels_at(table, HIGH)
        assert len(highs) == 1
        assert highs.loc[0, "level"] == 110.0
        assert highs.loc[0, "candle_index"] == 1
        assert highs.loc[0, "kind"] == MINOR

    def test_green_candle_exceeded_by_next_makes_nothing(self):
        # The staircase case. Candle 1 is green to 110, but candle 2 goes
        # to 111, so the attempt did not fail and there is no ceiling.
        # This is the rule that stops every candle in a rally qualifying.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 111.0, 108.0, 110.5)]
            + flat(3)
        ))
        assert 1 not in set(levels_at(table, HIGH)["candle_index"])

    def test_equal_next_high_still_counts(self):
        # high[i+1] <= high[i], so an exact tie is a failed attempt. The
        # test is <=, not <, and this pins that.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 110.0, 108.0, 108.5)]
            + flat(3)
        ))
        assert 1 in set(levels_at(table, HIGH)["candle_index"])

    def test_red_candle_makes_a_low_not_a_high(self):
        # The mirror. Candle 1 is red down to 90, candle 2 does not go
        # below it.
        table = compute_minor_liquidity(candles(
            [(100.0, 101.0, 100.0, 100.5)]
            + [(100.0, 100.5, 90.0, 91.0), (91.0, 99.0, 92.0, 98.0)]
            + [(120.0, 121.0, 119.0, 120.0)] * 3
        ))
        lows = levels_at(table, LOW)
        assert 1 in set(lows["candle_index"])
        assert lows[lows["candle_index"] == 1].iloc[0]["level"] == 90.0

    def test_doji_contributes_no_rejection_level(self):
        # close == open is neither an attempt up nor down. It can still
        # appear via the fractal source, so this asserts on `source`
        # rather than on absence.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 100.0), (100.0, 105.0, 99.0, 104.0)]
            + flat(3)
        ))
        doji = table[table["candle_index"] == 1]
        assert REJECTION not in set(doji["source"])

    def test_last_candle_never_produces_a_level(self):
        # The rule needs a candle after it. Nothing to assert about the
        # final bar, and reading past the end would raise.
        table = compute_minor_liquidity(candles(
            flat(3) + [(100.0, 110.0, 99.0, 109.0)]
        ))
        assert 3 not in set(table["candle_index"])


class TestFractalSource:
    def test_fractal_high_with_a_higher_left_neighbour_is_missed(self):
        # Row two of the module docstring's table, and the reason the
        # rejection rule exists at all. Candle 2's high is BELOW candle
        # 1's, so it is not an n=1 fractal, but it is a green candle
        # whose high candle 3 failed to exceed.
        table = compute_minor_liquidity(candles(
            [(100.0, 107.0, 99.0, 106.0)]
            + [(106.0, 107.0, 105.0, 105.5)]
            + [(100.0, 106.0, 99.0, 105.5)]
            + [(105.0, 105.5, 100.0, 101.0)]
            + flat(3)
        ))
        at_two = table[(table["candle_index"] == 2) & (table["side"] == HIGH)]
        assert len(at_two) == 1
        assert at_two.iloc[0]["source"] == REJECTION
        assert at_two.iloc[0]["level"] == 106.0

    def test_a_level_both_rules_find_is_reported_once_as_both(self):
        # The overlap case. One level, one row, source "both".
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + flat(3)
        ))
        at_one = table[(table["candle_index"] == 1) & (table["side"] == HIGH)]
        assert len(at_one) == 1
        assert at_one.iloc[0]["source"] == BOTH

    def test_a_red_pivot_high_comes_from_the_fractal_source_alone(self):
        # A fractal high on a RED candle. The rejection rule needs green,
        # so only the pivot source can find it.
        table = compute_minor_liquidity(candles(
            flat(1) + [(109.0, 110.0, 99.0, 100.0), (100.0, 105.0, 99.0, 104.0)]
            + flat(3)
        ))
        at_one = table[(table["candle_index"] == 1) & (table["side"] == HIGH)]
        assert len(at_one) == 1
        assert at_one.iloc[0]["source"] == FRACTAL


class TestNoLookahead:
    def test_visible_from_index_is_the_candle_after_the_level(self):
        # The lookahead guard. The rule reads candle i+1, so a level from
        # candle i cannot be known at i. Collapsing these two fields
        # would date every level one candle early.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + flat(3)
        ))
        row = table[table["candle_index"] == 1].iloc[0]
        assert row["visible_from_index"] == 2
        assert row["candle_index"] == 1

    def test_a_level_is_never_swept_by_its_own_first_visible_candle(self):
        # Guaranteed by both rules' construction, and relied on by the
        # forward walk admitting levels before running the sweep test.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + flat(3)
        ))
        row = table[table["candle_index"] == 1].iloc[0]
        assert row["swept_index"] != row["visible_from_index"]


class TestSweeps:
    def test_a_wick_one_tick_past_the_level_sweeps_it(self):
        # Wick-based and strict, with no band. levels.py gives its pools a
        # band because they average several pivots; a minor level is one
        # candle's extreme, so the level IS the price.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + [(101.0, 110.01, 100.0, 109.0)] + flat(2)
        ))
        row = table[table["candle_index"] == 1].iloc[0]
        assert row["swept"]
        assert row["ended_by"] == "swept"
        assert row["swept_index"] == 3

    def test_a_wick_exactly_to_the_level_does_not_sweep_it(self):
        # Strict >, so touching is not taking.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + [(101.0, 110.0, 100.0, 109.0)] + flat(2)
        ))
        row = table[table["candle_index"] == 1].iloc[0]
        assert not row["swept"]

    def test_low_side_sweep_is_the_mirror(self):
        table = compute_minor_liquidity(candles(
            [(100.0, 101.0, 100.0, 100.5)]
            + [(100.0, 100.5, 90.0, 91.0), (91.0, 99.0, 92.0, 98.0)]
            + [(98.0, 99.0, 89.99, 90.5)]
            + [(120.0, 121.0, 119.0, 120.0)] * 2
        ))
        row = table[(table["candle_index"] == 1) & (table["side"] == LOW)].iloc[0]
        assert row["swept"]
        assert row["swept_index"] == 3


class TestLifecycle:
    def test_an_untouched_level_expires_after_the_lookback(self):
        table = compute_minor_liquidity(
            candles(
                flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
                + flat(DEFAULT_LOOKBACK + 3)
            ),
            lookback=DEFAULT_LOOKBACK,
        )
        row = table[table["candle_index"] == 1].iloc[0]
        assert row["ended_by"] == "expired"
        assert not row["swept"]

    def test_a_still_live_level_ends_as_data_end_not_expired(self):
        # The distinction the live bot depends on: its newest levels are
        # all in this bucket on every run and must not read as dead.
        table = compute_minor_liquidity(candles(
            flat(1) + [(100.0, 110.0, 99.0, 109.0), (109.0, 108.0, 100.0, 101.0)]
            + flat(2)
        ))
        row = table[table["candle_index"] == 1].iloc[0]
        assert row["ended_by"] == "data_end"

    def test_an_empty_frame_returns_an_empty_table(self):
        table = compute_minor_liquidity(candles([]))
        assert len(table) == 0
        assert "candle_index" in table.columns
