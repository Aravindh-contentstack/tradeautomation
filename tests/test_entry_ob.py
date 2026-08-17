"""The killzone gate and the probability normalisation.

London civil time is what the strategy trades against, so the killzone
fixtures are built from UTC timestamps chosen to land on the intended
LONDON hour, and two of them sit on DST boundary days precisely because
that is where a naive fixed-offset implementation would disagree.
"""

import pandas as pd
import pytest

from backtest.entry_ob import resolve_entry_bar
from backtest.factors import compute_probability
from tests.conftest import ctx_for


def ctx_at(london_hours, day="2024-06-03"):
    """A context whose bars land on the given LONDON hours of one day.

    Built by naming the local times and converting, rather than adding a
    fixed offset to UTC, so the fixture stays correct in both BST and GMT.
    """
    bars = []
    for hour in london_hours:
        local = pd.Timestamp("%s %02d:00" % (day, hour), tz="Europe/London")
        bars.append((local.tz_convert("UTC"), 1.1, 1.1, 1.1, 1.1))
    return ctx_for(bars)


class TestKillzoneGate:
    def test_a_touch_inside_london_enters_on_its_own_candle(self):
        ctx = ctx_at([7, 8, 9])
        assert resolve_entry_bar(ctx, 0) == 0
        assert resolve_entry_bar(ctx, 2) == 2

    def test_a_touch_inside_new_york_enters_on_its_own_candle(self):
        ctx = ctx_at([12, 13, 14])
        assert resolve_entry_bar(ctx, 1) == 1

    def test_the_hour_before_london_defers_to_the_open(self):
        ctx = ctx_at([6, 7])
        assert resolve_entry_bar(ctx, 0) == 1

    def test_the_hour_before_new_york_defers_to_the_open(self):
        ctx = ctx_at([11, 12])
        assert resolve_entry_bar(ctx, 0) == 1

    def test_the_close_of_a_session_is_outside_it(self):
        """10:00 and 15:00 are the exclusive ends of the two windows, so a
        touch there is not in a session and is not a pre-window either.
        """
        ctx = ctx_at([10, 11])
        assert resolve_entry_bar(ctx, 0) is None

    def test_a_touch_outside_every_window_produces_nothing(self):
        ctx = ctx_at([2, 3, 4])
        assert resolve_entry_bar(ctx, 0) is None
        assert resolve_entry_bar(ctx, 1) is None

    def test_a_pre_window_touch_not_followed_by_a_session_is_rejected(self):
        """06:00 only counts because 07:00 opens London. Without that next
        bar in the data there is nothing to defer to.
        """
        ctx = ctx_at([6])
        assert resolve_entry_bar(ctx, 0) is None

    @pytest.mark.parametrize("day", ["2024-03-31", "2024-10-27"])
    def test_the_gate_holds_across_both_dst_transitions(self, day):
        """The UK clocks change at 01:00/02:00 local on these two days, so
        the session hours are the same local hours but different UTC ones.
        """
        ctx = ctx_at([6, 7], day=day)
        assert resolve_entry_bar(ctx, 0) == 1
        assert resolve_entry_bar(ctx, 1) == 1


class TestProbabilityNormalisation:
    def test_a_full_factor_set_reproduces_the_old_formula(self):
        """Before exclusion existed the denominator was the whole weight
        table. It still is whenever every factor answered, so no
        historical probability moves.
        """
        weights = {"a": 1.0, "b": 2.0, "c": 3.0}
        results = {"a": True, "b": False, "c": True}
        expected = (1.0 + 3.0 - 0.5 * 2.0) / 6.0 * 100
        assert compute_probability(results, weights) == pytest.approx(expected)

    def test_an_omitted_factor_leaves_the_denominator(self):
        """Omission has to be free. If the excluded weight stayed in the
        denominator, a timeframe with no order block would silently
        penalise the setup for the market's shape.
        """
        weights = {"a": 1.0, "b": 2.0, "c": 3.0}
        assert compute_probability({"a": True}, weights) == pytest.approx(100.0)

    def test_omission_and_a_no_are_not_the_same_answer(self):
        weights = {"a": 1.0, "b": 1.0}
        omitted = compute_probability({"a": True}, weights)
        answered_no = compute_probability({"a": True, "b": False}, weights)
        assert omitted == pytest.approx(100.0)
        assert answered_no == pytest.approx(25.0)

    def test_an_empty_factor_set_scores_zero_rather_than_dividing_by_zero(self):
        assert compute_probability({}, {"a": 1.0}) == 0.0
