"""update_weights must leave the table completely untouched on a 0R
breakeven, and take the expected win/loss branch otherwise.

This is the user's explicit decision (backtest/settings.py, R4 in the
plan): a breakeven trade neither confirmed nor refuted the factors, so
learning from it would be pure noise.
"""

import pytest

from backtest.weights import update_weights


def test_breakeven_leaves_every_weight_exactly_unchanged():
    weights = {"a": 1.0, "b": 1.0, "c": 1.0}
    factor_results = {"a": True, "b": False, "c": True}

    result = update_weights(weights, factor_results, 0.0)

    assert result == {"a": 1.0, "b": 1.0, "c": 1.0}
    assert result is weights  # mutated in place, not replaced


def test_positive_realised_r_takes_the_win_branch():
    weights = {"said_yes": 1.0, "said_no": 1.0}
    factor_results = {"said_yes": True, "said_no": False}

    update_weights(weights, factor_results, 0.4)

    assert weights["said_yes"] == pytest.approx(1.02)  # yes + win
    assert weights["said_no"] == pytest.approx(0.98)   # no + win


def test_negative_realised_r_takes_the_loss_branch():
    weights = {"said_yes": 1.0, "said_no": 1.0}
    factor_results = {"said_yes": True, "said_no": False}

    update_weights(weights, factor_results, -0.4)

    assert weights["said_yes"] == pytest.approx(0.98)  # yes + loss
    assert weights["said_no"] == pytest.approx(1.02)   # no + loss
