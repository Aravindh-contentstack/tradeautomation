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
    MITIGATION_LEG_CHILDREN,
    MITIGATION_LEG_FACTORS,
    SWEPT_GATE_TIMEFRAMES,
    SWEPT_LIQUIDITY_FACTORS_BY_TF,
    SWEPT_LIQUIDITY_GATE_FACTORS,
    evaluate_liquidity_target_factors,
    evaluate_mitigation_leg_swept_factors,
    evaluate_swept_liquidity_factors,
)
from backtest.target_log import TARGET_LOG_BITS
from smc.liquidity.liq_state import LiquidityUniverse

BARS = 5
BAR = 2

CLOSE = 100.0
R = 1.0
MAX_DISTANCE = TARGET_SEARCH_R * R
WEEK_MAX_DISTANCE = WEEKLY_TARGET_SEARCH_R * R


def universe(swept=(), targets_above=(), targets_below=(), credit=None):
    """A LiquidityUniverse with only the arrays the gates read.

    swept: iterable of (timeframe, kind, side) flagged on bar BAR.
    targets_above/below: iterable of (timeframe, kind, price) present on
        every bar.
    credit: iterable of (kind, side, price) alive on every bar, for the
        mitigation-leg gate. None leaves the field unset, which is how a
        universe built before this gate existed behaves.
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

    mitigation_credit = None
    if credit is not None:
        mitigation_credit = {}
        for kind, side, price in credit:
            mitigation_credit[(kind, side)] = np.full(BARS, price, dtype=float)

    return LiquidityUniverse(
        n=BARS,
        series={},
        swept_last_candle=swept_last_candle,
        target_above=above,
        target_below=below,
        mitigation_credit=mitigation_credit,
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


OB_TOP = 100.2
OB_BOTTOM = 100.0


def mitigation_leg(liq, direction, ob_top=OB_TOP, ob_bottom=OB_BOTTOM):
    return evaluate_mitigation_leg_swept_factors(
        liq, BAR, direction, ob_top, ob_bottom
    )


class TestMitigationLegGeometry:
    """Eligible liquidity sits ABOVE a bullish OB and BELOW a bearish one."""

    def test_a_long_scores_a_low_side_level_above_the_zone(self):
        """The previous day's low resting above a demand zone: price wicks
        under it, running the sell stops, then falls into the zone where that
        liquidity fills the buy orders.
        """
        liq = universe(credit=[("previous_day", "low", 100.3)])
        answers = mitigation_leg(liq, "bullish")

        assert answers == {"h1_mitigation_leg_swept_liquidity_previous_day": True}

    def test_a_long_ignores_a_level_below_the_zone(self):
        """Price would have to destroy the order block to reach it, so there
        would be no setup left to score.
        """
        liq = universe(credit=[("previous_day", "low", 99.5)])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_a_long_ignores_a_level_inside_the_zone(self):
        """Taking it IS the mitigation, not a stop run preceding it."""
        liq = universe(credit=[("previous_day", "low", 100.1)])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_a_level_exactly_at_the_zone_top_is_excluded(self):
        liq = universe(credit=[("previous_day", "low", OB_TOP)])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_a_short_scores_a_high_side_level_below_the_zone(self):
        liq = universe(credit=[("previous_day", "high", 99.7)])
        answers = mitigation_leg(liq, "bearish")

        assert answers == {"h1_mitigation_leg_swept_liquidity_previous_day": True}

    def test_a_short_ignores_a_level_above_the_zone(self):
        liq = universe(credit=[("previous_day", "high", 100.5)])

        assert mitigation_leg(liq, "bearish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_the_wanted_side_is_the_only_one_read(self):
        """A long wants the sell stops under the market run. A high-side
        sweep before a long is the opposite story.
        """
        liq = universe(credit=[("previous_day", "high", 100.3)])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }


class TestMitigationLegRollUp:
    def test_two_surviving_kinds_give_two_yeses_and_no_parent(self):
        liq = universe(credit=[
            ("old_point", "low", 100.3),
            ("previous_day", "low", 100.4),
        ])
        answers = mitigation_leg(liq, "bullish")

        assert answers == {
            "h1_mitigation_leg_swept_liquidity_old_points": True,
            "h1_mitigation_leg_swept_liquidity_previous_day": True,
        }

    def test_nothing_surviving_gives_the_parent_alone(self):
        liq = universe(credit=[])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_an_expired_credit_reads_as_nan_and_scores_nothing(self):
        liq = universe(credit=[("previous_day", "low", float("nan"))])

        assert mitigation_leg(liq, "bullish") == {
            "h1_mitigation_leg_swept_liquidity": False
        }

    def test_a_universe_without_the_field_omits_the_gate(self):
        """Not a no. A universe built before this gate existed must score
        exactly as it did before.
        """
        assert mitigation_leg(universe(), "bullish") == {}


class TestNoUniverse:
    @pytest.mark.parametrize("evaluate", [
        lambda: evaluate_swept_liquidity_factors(None, BAR, "bullish"),
        lambda: evaluate_liquidity_target_factors(
            None, BAR, "bullish", CLOSE, CLOSE, MAX_DISTANCE, WEEK_MAX_DISTANCE
        ),
        lambda: evaluate_mitigation_leg_swept_factors(
            None, BAR, "bullish", OB_TOP, OB_BOTTOM
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

    def test_the_mitigation_leg_gate_is_in_all_factors(self):
        assert set(MITIGATION_LEG_FACTORS) <= set(ALL_FACTORS)

    def test_factor_names_are_unique(self):
        assert len(ALL_FACTORS) == len(set(ALL_FACTORS))

    def test_the_two_gates_do_not_collide_with_the_ob_gates(self):
        """daily_swept_liquidity_swing (this gate) and
        daily_mitigation_ob_swept_liquidity_swing (the OB gate) are
        different facts about different things, and both exist.
        """
        assert "daily_swept_liquidity_swing" in ALL_FACTORS
        assert "daily_mitigation_ob_swept_liquidity_swing" in ALL_FACTORS

    def test_the_mitigation_leg_gate_does_not_collide_with_the_ob_gate(self):
        """The formation leg and the approach leg are different legs, weeks
        apart, and both get their own name.
        """
        assert "h1_mitigation_leg_swept_liquidity_old_points" in ALL_FACTORS
        assert "h1_mitigation_ob_swept_liquidity_old_points" in ALL_FACTORS

    def test_the_mitigation_leg_gate_is_h1_only(self):
        for factor in MITIGATION_LEG_FACTORS:
            assert factor.startswith("h1_")
        for prefix in ("daily_", "h4_"):
            assert not any(
                f.startswith("%smitigation_leg" % prefix) for f in ALL_FACTORS
            )

    @pytest.mark.parametrize("child", ["swing", "internal", "fractal", "fvg"])
    def test_structural_and_fvg_children_do_not_exist_on_this_gate(self, child):
        """FVG especially: an order block's own displacement leg leaves an
        imbalance in front of the zone, so every mitigation would sweep it.
        A name that can never be legitimately emitted is not created.
        """
        assert child not in MITIGATION_LEG_CHILDREN
        assert "h1_mitigation_leg_swept_liquidity_%s" % child not in ALL_FACTORS


class TestTargetLogBitsUnchanged:
    """The new gate must not touch the stored bitmask positions."""

    def test_no_mitigation_leg_entry_in_the_bit_table(self):
        assert not any("mitigation_leg" in gate for gate, _ in TARGET_LOG_BITS)

    def test_the_original_bit_positions_are_intact(self):
        """target_log.py derives bit positions from factor list ORDER, so an
        insertion into the wrong list silently reinterprets every stored
        parquet. This pins the first twelve.
        """
        assert TARGET_LOG_BITS[:12] == [
            ("ob_target", "caused_displacement"),
            ("ob_target", "caused_imbalance"),
            ("ob_target", "has_inducement"),
            ("ob_target", "flip_zone"),
            ("ob_target", "swept_liquidity_swing"),
            ("ob_target", "swept_liquidity_internal"),
            ("ob_target", "swept_liquidity_fractal"),
            ("ob_target", "swept_liquidity_fvg"),
            ("ob_target", "swept_liquidity_previous_candle"),
            ("ob_target", "swept_liquidity"),
            ("ob_target", "within_daily_ob"),
            ("ob_target", "within_h4_ob"),
        ]
