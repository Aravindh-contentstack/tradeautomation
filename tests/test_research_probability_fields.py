"""Reporting the trade's probability without acting on it.

The study runs Stage A with no threshold at all, because every candidate
is taken. That broke a fused assumption in simulate_trade: the live score
was only ever COMPUTED when there was a threshold to compare it against,
so an unfiltered run could not report a probability at all.

Two flags came out of separating those concerns:

  record_min_probability    score every bar and keep the lowest, without
                            acting on it. This is how the breakeven rule
                            stays measurable while switched off.
  record_final_probability  score ONCE at the terminal bar. Deliberately
                            not a by-product of the per-bar loop, which
                            stops the moment a trade goes to breakeven and
                            so can never see the exit.

Fixtures are reused from test_live_probability_recheck: the same isolated
single-factor weighting, so a failure points at the recording mechanism
rather than at factor arithmetic.
"""

import pytest

from backtest.simulate import simulate_trade
from conftest import DIRECTION, ENTRY, R_DISTANCE, SL
from test_live_probability_recheck import (
    BASE_PRICES,
    DIP_THROUGH_ENTRY,
    STRUCTURE_FLIP_WITH_DIP,
    _ctx_with_flip,
    _weights_isolating,
)

WEIGHTS = _weights_isolating("h1_internal_structure")


def ctx():
    return _ctx_with_flip(
        STRUCTURE_FLIP_WITH_DIP, BASE_PRICES + [DIP_THROUGH_ENTRY]
    )


def walk(**kwargs):
    return simulate_trade(ctx(), 0, DIRECTION, ENTRY, SL, R_DISTANCE, **kwargs)


class TestScoringWithoutActing:
    def test_min_probability_is_recorded_with_no_threshold(self):
        """The headline case. The structure flip drives the score from
        100% to -50%, and with no threshold the trade must NOT be moved to
        breakeven, but the -50% must still be reported.
        """
        result = walk(
            weights=WEIGHTS,
            mitigation_factor_results={},
            record_min_probability=True,
        )

        assert result["be_moved"] is False
        assert result["min_live_probability"] == pytest.approx(-50.0)

    def test_recording_is_off_by_default(self):
        """Scoring every bar costs a full factor evaluation per bar. The
        buffer sweep runs seven passes and must not pay for a diagnostic
        it does not read.
        """
        result = walk(weights=WEIGHTS, mitigation_factor_results={})
        assert result["min_live_probability"] is None

    def test_a_threshold_still_acts_as_before(self):
        """The split must not have disarmed the real rule."""
        result = walk(
            weights=WEIGHTS, mitigation_factor_results={}, threshold=60,
        )
        assert result["be_moved"] is True
        assert result["be_trigger"] == "target_ob_probability"

    def test_the_minimum_covers_only_the_at_risk_window(self):
        """Scoring stops once the stop is at entry, so the minimum means
        "lowest score seen while the trade could still lose", which is
        exactly the window the rule operates in. With the threshold armed,
        the trade goes to breakeven on the flip bar, so the minimum is
        that same flip reading and nothing later.
        """
        result = walk(
            weights=WEIGHTS, mitigation_factor_results={}, threshold=60,
            record_min_probability=True,
        )
        assert result["min_live_probability"] == pytest.approx(-50.0)
        assert result["be_idx"] == 2


class TestFinalProbability:
    def test_scored_at_the_terminal_bar(self):
        """The trade ends on bar 3, by which point structure has flipped
        bearish against this bullish trade, so the isolated factor reads
        no and the score is -50%.
        """
        result = walk(
            weights=WEIGHTS,
            mitigation_factor_results={},
            record_final_probability=True,
        )

        assert result["terminal_idx"] == 3
        assert result["final_probability"] == pytest.approx(-50.0)

    def test_reported_even_for_a_trade_that_went_to_breakeven(self):
        """This is why it is not a by-product of the per-bar loop. That
        loop stops at the breakeven move on bar 2 and never sees bar 3, so
        only a separate terminal-bar score can answer this.
        """
        result = walk(
            weights=WEIGHTS, mitigation_factor_results={}, threshold=60,
            record_final_probability=True,
        )

        assert result["be_moved"] is True
        assert result["be_idx"] == 2
        assert result["terminal_idx"] == 3
        assert result["final_probability"] == pytest.approx(-50.0)

    def test_off_by_default(self):
        assert walk(
            weights=WEIGHTS, mitigation_factor_results={}
        )["final_probability"] is None

    def test_none_without_the_inputs_to_score(self):
        """Asked for but unscoreable is None, not an exception. Every
        pre-existing caller omits weights entirely.
        """
        assert walk(record_final_probability=True)["final_probability"] is None
