"""The order block touch/invalidation lifecycle.

Hand-built zones and candle ranges, in the spirit of conftest.py: each
rule is decided by two or three bars, so the fixtures spell out only
those and leave everything else safely outside the zone.

Every fixture uses the same zone, 10.0 to 20.0, whose midpoint is
therefore 15.0. Prices are plain numbers rather than FX quotes because
nothing here is instrument-specific and round numbers make the
penetration depths readable at a glance.
"""

import pytest

from smc.order_blocks.order_blocks import (
    OB_LOOKBACK,
    TOUCH_LIMIT,
    _apply_mitigation,
    _apply_touch_lifecycle,
)

TOP = 20.0
BOTTOM = 10.0
MIDPOINT = 15.0
OUTSIDE_ABOVE = 25.0


def make_ob(direction="bullish", trigger=0):
    return {
        "direction": direction,
        "top": TOP,
        "bottom": BOTTOM,
        "earliest_trigger_index": trigger,
        "mitigated": False,
        "mitigated_index": None,
        "mitigated_date": None,
        "invalidated": False,
        "invalidated_index": None,
        "invalidated_from_index": None,
        "invalidated_date": None,
        "invalidated_rule": None,
        "touch_count": 0,
        "qualifying_touch_indices": [],
    }


def run(lows, highs=None, break_up=None, break_down=None, direction="bullish"):
    """Applies the lifecycle to one OB over a series of candle extremes."""
    n = len(lows)
    highs = highs if highs is not None else [OUTSIDE_ABOVE] * n
    ob = make_ob(direction=direction)
    _apply_touch_lifecycle(
        [ob],
        highs,
        lows,
        list(range(n)),
        break_up or [False] * n,
        break_down or [False] * n,
    )
    return ob


def run_mitigation(lows, highs=None):
    """Applies only the mitigation scan, which carries the same bound."""
    n = len(lows)
    highs = highs if highs is not None else [OUTSIDE_ABOVE] * n
    ob = make_ob()
    _apply_mitigation([ob], highs, lows, list(range(n)))
    return ob


def quiet(n):
    """n candles that never come near the zone."""
    return [OUTSIDE_ABOVE] * n


class TestEqRule:
    def test_wick_to_the_midpoint_kills_the_zone(self):
        ob = run([OUTSIDE_ABOVE, OUTSIDE_ABOVE, MIDPOINT, OUTSIDE_ABOVE])
        assert ob["invalidated"]
        assert ob["invalidated_rule"] == "eq"
        assert ob["invalidated_index"] == 2

    def test_a_wick_past_the_far_edge_reports_as_eq_not_a_separate_rule(self):
        """The old full-break-through rule is subsumed, not merely
        reordered: price cannot reach below the bottom without crossing
        the midpoint on the same candle.
        """
        ob = run([OUTSIDE_ABOVE, BOTTOM - 5.0])
        assert ob["invalidated_rule"] == "eq"
        assert ob["invalidated_index"] == 1

    def test_a_touch_short_of_the_midpoint_leaves_the_zone_alive(self):
        ob = run([OUTSIDE_ABOVE, MIDPOINT + 1.0, OUTSIDE_ABOVE])
        assert not ob["invalidated"]
        assert ob["touch_count"] == 1


class TestTouchCounting:
    def test_each_touch_must_go_deeper_to_count(self):
        # 19 (deeper than nothing), 19.5 (shallower, ignored), 18 (deeper).
        ob = run([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE, 19.5, OUTSIDE_ABOVE, 18.0])
        assert ob["touch_count"] == 2
        assert ob["qualifying_touch_indices"] == [1, 5]
        assert not ob["invalidated"]

    def test_a_multi_bar_stay_inside_is_one_touch(self):
        """Price sitting in the zone for three candles has not tested it
        three times, so the counter must not treat a slow drift through
        as three separate absorptions.
        """
        ob = run([OUTSIDE_ABOVE, 19.0, 18.5, 18.2, OUTSIDE_ABOVE])
        assert ob["touch_count"] == 1
        assert ob["qualifying_touch_indices"] == [1]

    def test_the_third_qualifying_touch_kills_the_zone(self):
        ob = run([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE, 18.0, OUTSIDE_ABOVE, 17.0])
        assert ob["invalidated_rule"] == "third_touch"
        assert ob["touch_count"] == TOUCH_LIMIT
        assert ob["invalidated_index"] == 5

    def test_bearish_zones_count_depth_upward(self):
        lows = [BOTTOM - 5.0] * 6
        highs = [BOTTOM - 5.0, 11.0, BOTTOM - 5.0, 12.0, BOTTOM - 5.0, 13.0]
        ob = run(lows, highs=highs, direction="bearish")
        assert ob["invalidated_rule"] == "third_touch"
        assert ob["qualifying_touch_indices"] == [1, 3, 5]


class TestStructureBreakRule:
    def test_a_break_away_after_a_touch_kills_the_zone(self):
        breaks = [False, False, False, True, False]
        ob = run([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE, OUTSIDE_ABOVE, OUTSIDE_ABOVE],
                 break_up=breaks)
        assert ob["invalidated_rule"] == "structure_break"
        assert ob["invalidated_index"] == 3

    def test_an_untouched_zone_survives_a_structure_break(self):
        """Price breaking structure elsewhere says nothing about a zone it
        never traded into. This is the case the rule deliberately excludes.
        """
        breaks = [False, True, False]
        ob = run([OUTSIDE_ABOVE] * 3, break_up=breaks)
        assert not ob["invalidated"]

    def test_a_break_toward_a_bullish_zone_does_not_kill_it(self):
        """Only a break AWAY counts. A down-break is price coming into a
        demand zone, which is the setup, not its failure.
        """
        breaks = [False, False, True]
        ob = run([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE], break_down=breaks)
        assert not ob["invalidated"]


class TestExpiryRule:
    """The OB_LOOKBACK bound.

    The other three rules all need price to do something. This one exists
    precisely for the zone price never came back to, which
    "structure_break" deliberately refuses to kill and which would
    otherwise stay valid forever and keep surfacing as a target.

    Every fixture triggers at index 0, so the expiry candle is OB_LOOKBACK.
    """

    def test_an_untouched_zone_expires_a_lookback_after_its_trigger(self):
        ob = run(quiet(OB_LOOKBACK + 2))
        assert ob["invalidated"]
        assert ob["invalidated_rule"] == "expired"
        assert ob["invalidated_index"] == OB_LOOKBACK
        assert ob["touch_count"] == 0

    def test_the_expiry_candle_is_still_inside_the_valid_window(self):
        """Same x-plus-one gap as the other three rules, so ob_state's
        valid_through needs no special case for this one.
        """
        ob = run(quiet(OB_LOOKBACK + 2))
        assert ob["invalidated_from_index"] == ob["invalidated_index"] + 1

    def test_data_running_out_early_is_not_expiry(self):
        """Not knowing yet and being dead are different facts. A zone whose
        lookback has not finished inside the available history is still
        live, or the live bot would lose its newest zones every run.
        """
        ob = run(quiet(OB_LOOKBACK // 2))
        assert not ob["invalidated"]

    def test_the_bound_is_exclusive_at_the_last_candle(self):
        """Right on the boundary: the expiry candle has to EXIST in the
        data, not merely be the index one past its end.
        """
        assert not run(quiet(OB_LOOKBACK))["invalidated"]
        assert run(quiet(OB_LOOKBACK + 1))["invalidated_rule"] == "expired"

    def test_a_rule_that_fires_first_keeps_its_own_label(self):
        lows = quiet(50) + [MIDPOINT] + quiet(OB_LOOKBACK)
        ob = run(lows)
        assert ob["invalidated_rule"] == "eq"
        assert ob["invalidated_index"] == 50

    def test_a_rule_firing_on_the_last_candle_before_expiry_still_wins(self):
        lows = quiet(OB_LOOKBACK - 1) + [MIDPOINT] + quiet(2)
        ob = run(lows)
        assert ob["invalidated_rule"] == "eq"
        assert ob["invalidated_index"] == OB_LOOKBACK - 1

    def test_a_rule_firing_on_the_expiry_candle_itself_still_wins(self):
        """The zone is live THROUGH its expiry candle, so a touch there is
        a real touch rather than a post-mortem.
        """
        lows = quiet(OB_LOOKBACK) + [MIDPOINT] + quiet(2)
        ob = run(lows)
        assert ob["invalidated_rule"] == "eq"
        assert ob["invalidated_index"] == OB_LOOKBACK


class TestMitigationBound:
    """_apply_mitigation carries the identical bound, so the table cannot
    report a mitigation on a candle the zone was already dead for.
    """

    def test_a_touch_after_expiry_is_not_a_mitigation(self):
        lows = quiet(OB_LOOKBACK + 1) + [MIDPOINT] + quiet(2)
        assert not run_mitigation(lows)["mitigated"]

    def test_a_touch_on_the_expiry_candle_still_counts(self):
        lows = quiet(OB_LOOKBACK) + [MIDPOINT] + quiet(2)
        ob = run_mitigation(lows)
        assert ob["mitigated"]
        assert ob["mitigated_index"] == OB_LOOKBACK


class TestTheXPlusOneRule:
    """The zone stays usable ON the candle that kills it.

    Reaching the midpoint is the evidence that the block's resting orders
    were absorbed, which is precisely when a reaction is expected, so that
    candle is a signal rather than a disqualification. Collapsing
    invalidated_index and invalidated_from_index would delete the
    deepest-penetration entries the strategy has.
    """

    def test_the_killing_candle_is_still_inside_the_valid_window(self):
        ob = run([OUTSIDE_ABOVE, OUTSIDE_ABOVE, MIDPOINT, OUTSIDE_ABOVE])
        assert ob["invalidated_index"] == 2
        assert ob["invalidated_from_index"] == 3

    def test_a_third_touch_is_tradeable_on_its_own_candle_too(self):
        ob = run([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE, 18.0, OUTSIDE_ABOVE, 17.0])
        assert ob["invalidated_index"] == 5
        assert ob["invalidated_from_index"] == 6

    @pytest.mark.parametrize("rule_lows,expected", [
        ([OUTSIDE_ABOVE, MIDPOINT], "eq"),
        ([OUTSIDE_ABOVE, 19.0, OUTSIDE_ABOVE, 18.0, OUTSIDE_ABOVE, 17.0], "third_touch"),
    ])
    def test_every_rule_reports_the_gap(self, rule_lows, expected):
        ob = run(rule_lows)
        assert ob["invalidated_rule"] == expected
        assert ob["invalidated_from_index"] == ob["invalidated_index"] + 1
