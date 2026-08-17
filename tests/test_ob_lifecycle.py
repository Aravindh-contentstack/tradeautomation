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

from smc.order_blocks.order_blocks import TOUCH_LIMIT, _apply_touch_lifecycle

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
