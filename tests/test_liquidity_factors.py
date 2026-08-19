"""The two standalone liquidity gates, and the H1 exception behind them.

Both gates read precomputed arrays, so the fixtures build a
LiquidityUniverse directly rather than driving a whole pipeline. That keeps
each test about one rule: which side supports the trade, when the roll-up
parent appears, where the distance cut falls, and what happens when a target
is covered.
"""

import numpy as np
import pytest

from backtest.entry_ob import TARGET_SEARCH_R, WEEKLY_TARGET_SEARCH_R
from backtest.factors import (
    ALL_FACTORS,
    LIQUIDITY_TARGET_FACTORS,
    SWEPT_GATE_TIMEFRAMES,
    SWEPT_LIQUIDITY_FACTORS_BY_TF,
    SWEPT_LIQUIDITY_GATE_FACTORS,
    evaluate_liquidity_target_factors,
    evaluate_swept_liquidity_factors,
)
from smc.liquidity.liq_state import LiquidityUniverse

BARS = 5
BAR = 2

CLOSE = 100.0
R = 1.0
MAX_DISTANCE = TARGET_SEARCH_R * R
WEEK_MAX_DISTANCE = WEEKLY_TARGET_SEARCH_R * R


def universe(swept=(), targets_above=(), targets_below=()):
    """A LiquidityUniverse with only the arrays the gates read.

    swept: iterable of (timeframe, kind, side) flagged on bar BAR.
    targets_above/below: iterable of (timeframe, kind, price) present on
        every bar.
    """
    swept_last_candle = {}
    for key in swept:
        flags = np.zeros(BARS, dtype=bool)
        flags[BAR] = True
        swept_last_candle[key] = flags

    above = {}
    below = {}
    for timeframe, kind, price in targets_above:
        above[(timeframe, kind)] = np.full(BARS, price, dtype=float)
    for timeframe, kind, price in targets_below:
        below[(timeframe, kind)] = np.full(BARS, price, dtype=float)

    return LiquidityUniverse(
        n=BARS,
        series={},
        swept_last_candle=swept_last_candle,
        target_above=above,
        target_below=below,
    )


def targets(liq, direction, high=CLOSE, low=CLOSE):
    return evaluate_liquidity_target_factors(
        liq, BAR, direction, high, low, MAX_DISTANCE, WEEK_MAX_DISTANCE
    )


class TestSweptGateSides:
    def test_a_long_scores_a_low_sweep(self):
        """The sell stops under the market were run, so what is left above
        is buyers. That is the story a demand setup rests on.
        """
        liq = universe(swept=[("Daily", "equals", "low")])
        results = evaluate_swept_liquidity_factors(liq, BAR, "bullish")
        assert results["daily_swept_liquidity_equals"] is True

    def test_a_long_ignores_a_high_sweep(self):
        """Omitted, not answered no. Price taking the highs before a long
        is a different story, not a weaker version of the same one.
        """
        liq = universe(swept=[("Daily", "equals", "high")])
        results = evaluate_swept_liquidity_factors(liq, BAR, "bullish")
        assert "daily_swept_liquidity_equals" not in results

    def test_a_short_scores_a_high_sweep(self):
        liq = universe(swept=[("Daily", "equals", "high")])
        results = evaluate_swept_liquidity_factors(liq, BAR, "bearish")
        assert results["daily_swept_liquidity_equals"] is True


class TestSweptGateRollUp:
    def test_the_parent_is_dropped_when_something_was_swept(self):
        liq = universe(swept=[("Daily", "equals", "low")])
        results = evaluate_swept_liquidity_factors(liq, BAR, "bullish")
        assert "daily_swept_liquidity" not in results

    def test_the_parent_alone_answers_no_when_nothing_was_swept(self):
        results = evaluate_swept_liquidity_factors(universe(), BAR, "bullish")
        assert results["daily_swept_liquidity"] is False
        assert results["h4_swept_liquidity"] is False

    def test_two_kinds_swept_score_higher_than_one(self):
        """The whole point of the roll-up: an OB that ran two pools of
        liquidity should out-score one that ran a single pool, rather than
        both collapsing to one yes.
        """
        one = evaluate_swept_liquidity_factors(
            universe(swept=[("Daily", "equals", "low")]), BAR, "bullish"
        )
        two = evaluate_swept_liquidity_factors(
            universe(swept=[("Daily", "equals", "low"), ("Daily", "lrlq", "low")]),
            BAR, "bullish",
        )
        # Scoped to Daily: 4H swept nothing in either fixture, so its
        # parent no is present in both and is not what this is about.
        one_daily = {k: v for k, v in one.items() if k.startswith("daily_")}
        two_daily = {k: v for k, v in two.items() if k.startswith("daily_")}
        assert len(two_daily) == 2
        assert len(one_daily) == 1
        assert all(two_daily.values())

    def test_the_two_timeframes_roll_up_independently(self):
        liq = universe(swept=[("Daily", "equals", "low")])
        results = evaluate_swept_liquidity_factors(liq, BAR, "bullish")
        assert "daily_swept_liquidity" not in results
        assert results["h4_swept_liquidity"] is False


class TestSweptGateHasNoH1:
    """Confirmed with the user: an H1 sweep that does not break structure
    leaves no order block, so there is nothing to trade from. Every H1 sweep
    therefore lives on the OB gates instead.
    """

    def test_the_gate_covers_daily_and_4h_only(self):
        assert SWEPT_GATE_TIMEFRAMES == ["Daily", "4H"]

    def test_no_h1_factor_name_exists_for_this_gate(self):
        assert not any(f.startswith("h1_swept_liquidity")
                       for f in SWEPT_LIQUIDITY_GATE_FACTORS)

    def test_h1_alone_carries_the_time_based_ob_children(self):
        h1 = {suffix for suffix, _ in SWEPT_LIQUIDITY_FACTORS_BY_TF["H1"]}
        daily = {suffix for suffix, _ in SWEPT_LIQUIDITY_FACTORS_BY_TF["Daily"]}
        assert "swept_liquidity_asian" in h1
        assert "swept_liquidity_asian" not in daily
        assert daily < h1


class TestTargetGateDistance:
    def test_a_target_inside_the_range_scores(self):
        liq = universe(targets_above=[("Daily", "equals", CLOSE + MAX_DISTANCE - 0.1)])
        assert targets(liq, "bullish")["daily_liquidity_target_equals"] is True

    def test_a_target_beyond_the_range_is_omitted(self):
        liq = universe(targets_above=[("Daily", "equals", CLOSE + MAX_DISTANCE + 0.1)])
        assert "daily_liquidity_target_equals" not in targets(liq, "bullish")

    def test_previous_week_reaches_further_than_everything_else(self):
        """7.5R against 5R, confirmed with the user. A weekly level is a
        bigger draw, so price is worth crediting for heading toward one
        from further away.
        """
        price = CLOSE + MAX_DISTANCE + 0.1
        liq = universe(targets_above=[
            ("H1", "previous_week", price),
            ("H1", "equals", price),
        ])
        results = targets(liq, "bullish")
        assert results["h1_liquidity_target_previous_week"] is True
        assert "h1_liquidity_target_equals" not in results

    def test_even_previous_week_has_a_limit(self):
        liq = universe(targets_above=[
            ("H1", "previous_week", CLOSE + WEEK_MAX_DISTANCE + 0.1),
        ])
        assert "h1_liquidity_target_previous_week" not in targets(liq, "bullish")

    def test_distance_is_measured_from_the_bars_leading_edge(self):
        """For a long that is the bar's HIGH, not its close: price has
        already travelled that far toward the target.
        """
        price = CLOSE + MAX_DISTANCE + 0.5
        liq = universe(targets_above=[("Daily", "equals", price)])
        assert "daily_liquidity_target_equals" not in targets(liq, "bullish")
        assert targets(liq, "bullish", high=CLOSE + 1.0)["daily_liquidity_target_equals"]


class TestTargetGateDirection:
    def test_a_long_reads_targets_above(self):
        liq = universe(targets_above=[("Daily", "equals", CLOSE + 1.0)])
        assert "daily_liquidity_target_equals" in targets(liq, "bullish")

    def test_a_long_ignores_targets_below(self):
        liq = universe(targets_below=[("Daily", "equals", CLOSE - 1.0)])
        assert "daily_liquidity_target_equals" not in targets(liq, "bullish")

    def test_a_short_reads_targets_below(self):
        liq = universe(targets_below=[("Daily", "equals", CLOSE - 1.0)])
        assert "daily_liquidity_target_equals" in targets(liq, "bearish", low=CLOSE)


class TestTargetGateExclusion:
    def test_a_kind_with_no_level_is_absent_not_false(self):
        """Dynamic exclusion. A kind that never had a level in range had
        nothing to say, and scoring it no would punish the setup for the
        market's shape rather than for anything about the setup.
        """
        results = targets(universe(), "bullish")
        assert results == {}

    def test_a_covered_target_drops_out_rather_than_turning_against(self):
        """The user's rule, and the one place this gate deliberately
        differs from OB Target: a liquidity target price has taken becomes
        NA, it does not become a no.
        """
        liq = universe(targets_above=[("Daily", "equals", CLOSE + 1.0)])
        assert targets(liq, "bullish")["daily_liquidity_target_equals"] is True

        liq.target_above[("Daily", "equals")][BAR] = np.nan
        assert "daily_liquidity_target_equals" not in targets(liq, "bullish")

    def test_this_gate_never_answers_no(self):
        """A deliberate consequence of the rule above, recorded so a later
        change to it is a decision rather than an accident: Liquidity
        Target can only ever raise a setup's score.
        """
        liq = universe(targets_above=[
            ("Daily", "equals", CLOSE + 1.0),
            ("H1", "asian", CLOSE + 1.0),
        ])
        assert all(targets(liq, "bullish").values())


class TestTargetGateTimeframes:
    def test_session_targets_are_h1_only(self):
        """Unlike the sweep side, where each timeframe owns its own clock
        level, every time-based target is hunted inside an H1 trade.
        """
        for child in ("asian", "london", "ny", "previous_day", "previous_week"):
            assert "h1_liquidity_target_%s" % child in LIQUIDITY_TARGET_FACTORS
            assert "daily_liquidity_target_%s" % child not in LIQUIDITY_TARGET_FACTORS
            assert "h4_liquidity_target_%s" % child not in LIQUIDITY_TARGET_FACTORS

    def test_structural_strong_points_are_never_targets(self):
        """Confirmed with the user: only Old Points can be a target. A
        swing, internal or fractal point is expected to HOLD, which is the
        opposite of being run.
        """
        for child in ("swing", "internal", "fractal"):
            assert not any(f.endswith("_liquidity_target_%s" % child)
                           for f in LIQUIDITY_TARGET_FACTORS)


class TestNoUniverse:
    @pytest.mark.parametrize("evaluate", [
        lambda: evaluate_swept_liquidity_factors(None, BAR, "bullish"),
        lambda: evaluate_liquidity_target_factors(
            None, BAR, "bullish", CLOSE, CLOSE, MAX_DISTANCE, WEEK_MAX_DISTANCE
        ),
    ])
    def test_a_missing_universe_omits_the_gate_entirely(self, evaluate):
        """Every caller that predates this work passes no liquidity, and
        must score exactly as it did before.
        """
        assert evaluate() == {}


class TestFactorRegistry:
    def test_both_gates_are_in_all_factors(self):
        assert set(SWEPT_LIQUIDITY_GATE_FACTORS) <= set(ALL_FACTORS)
        assert set(LIQUIDITY_TARGET_FACTORS) <= set(ALL_FACTORS)

    def test_factor_names_are_unique(self):
        assert len(ALL_FACTORS) == len(set(ALL_FACTORS))

    def test_the_two_gates_do_not_collide_with_the_ob_gates(self):
        """daily_swept_liquidity_swing (this gate) and
        daily_mitigation_ob_swept_liquidity_swing (the OB gate) are
        different facts about different things, and both exist.
        """
        assert "daily_swept_liquidity_swing" in ALL_FACTORS
        assert "daily_mitigation_ob_swept_liquidity_swing" in ALL_FACTORS
