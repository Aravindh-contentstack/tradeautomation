"""The M15 killzone gate and the probability normalisation.

London civil time is what the strategy trades against, so the killzone
fixtures are built from UTC timestamps chosen to land on the intended
LONDON hour, and two of them sit on DST boundary days precisely because
that is where a naive fixed-offset implementation would disagree.

The gate under test MOVED. It used to be entry_ob.resolve_entry_bar,
which gated the H1 order-block touch and deferred an entry from the hour
before a session into its open. Decision 4 moved the session gate to the
M15 candle that completes the setup, and resolve_entry_bar was deleted
rather than left unused. The DST coverage came with it, because that is
the part of it worth keeping: an M15 bundle derives london_hour through
killzone.london_fields, so the same fixed-offset bug is still reachable.
"""

import pandas as pd
import pytest

from backtest.entry_models import _in_killzone
from backtest.factors import compute_probability
from backtest.m15_pipeline import build_m15_bundle


def m15_at(london_hours, day="2024-06-03"):
    """An M15 bundle whose bars land on the given LONDON hours of one day.

    Built by naming the local times and converting, rather than adding a
    fixed offset to UTC, so the fixture stays correct in both BST and GMT.
    """
    rows = []
    for hour in london_hours:
        local = pd.Timestamp("%s %02d:00" % (day, hour), tz="Europe/London")
        rows.append({
            "date": local.tz_convert("UTC"),
            "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
        })
    return build_m15_bundle(pd.DataFrame(rows))


class TestM15KillzoneGate:
    def test_london_hours_are_inside(self):
        bundle = m15_at([7, 8, 9])
        assert [_in_killzone(bundle, j) for j in range(3)] == [True] * 3

    def test_new_york_hours_are_inside(self):
        bundle = m15_at([12, 13, 14])
        assert [_in_killzone(bundle, j) for j in range(3)] == [True] * 3

    def test_the_close_of_a_session_is_outside_it(self):
        """End-exclusive: 10:00 is not London and 15:00 is not New York."""
        bundle = m15_at([10, 15])
        assert [_in_killzone(bundle, j) for j in range(2)] == [False, False]

    def test_the_hour_before_a_session_is_outside_it(self):
        """The pre-window deferral went with resolve_entry_bar. A trigger
        candle either closes in a session or it does not count.
        """
        bundle = m15_at([6, 11])
        assert [_in_killzone(bundle, j) for j in range(2)] == [False, False]

    def test_the_gap_between_sessions_is_outside(self):
        bundle = m15_at([11, 16, 3])
        assert [_in_killzone(bundle, j) for j in range(3)] == [False] * 3

    @pytest.mark.parametrize("day", ["2024-03-31", "2024-10-27"])
    def test_the_gate_holds_across_both_dst_transitions(self, day):
        """The UK clocks change at 01:00/02:00 local on these two days, so
        the session hours are the same local hours but different UTC ones.
        A fixed-offset implementation gets exactly these two days wrong.
        """
        bundle = m15_at([6, 7, 9, 10], day=day)
        assert [_in_killzone(bundle, j) for j in range(4)] == [
            False, True, True, False
        ]

    def test_allowed_sessions_none_allows_both_unchanged(self):
        bundle = m15_at([7, 12])
        assert [
            _in_killzone(bundle, j, allowed_sessions=None) for j in range(2)
        ] == [True, True]

    def test_allowed_sessions_can_restrict_to_london_only(self):
        bundle = m15_at([7, 12])
        assert [
            _in_killzone(bundle, j, allowed_sessions=["london"])
            for j in range(2)
        ] == [True, False]

    def test_allowed_sessions_can_restrict_to_ny_only(self):
        bundle = m15_at([7, 12])
        assert [
            _in_killzone(bundle, j, allowed_sessions=["ny"]) for j in range(2)
        ] == [False, True]


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
