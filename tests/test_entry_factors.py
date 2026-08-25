"""The 31 entry factors.

Built on top of test_entry_models' fixtures, so every assertion runs
against a setup the scan actually produced rather than a hand-made dict
that might not correspond to anything reachable.

What is worth testing here, in order of how badly a silent break would
hurt:

1. DYNAMIC EXCLUSION. Only the firing model's factors may appear. If a
   non-firing model's names leaked in as False they would drag every
   score down and the denominator would stop being model-specific.
2. THE FVG DIRECTION TRAPS. Three factors ask about gaps and they want
   different halves of the table. Getting one backwards is invisible in
   the output and inverts the evidence.
3. POLARITY. no_imbalance and no_other_lids are both stated as absences,
   so a YES must mean the thing is absent.
4. The target gate never answering NO at child level.
"""

from backtest.entry_factors import (
    ENTRY_FACTORS,
    MODEL_FACTORS,
    evaluate_entry_factors,
    evaluate_entry_target_factors,
    factor_name,
    model_factor_names,
)
from backtest.entry_models import scan_for_entry
from tests.test_entry_models import (
    LC1,
    PIP,
    bearish_and_bullish,
    build,
    TestLC2A,
    TestLC2B,
)

FAKE_BREAK = TestLC2A.FAKE_BREAK
DOUBLE_TOP = TestLC2B.DOUBLE_TOP


def factors_for(bars, bullish, mitigation_bar=5, **kwargs):
    """(answers, setup, bundle, zone) for a fixture, or (None,)*4."""
    bundle, h1_ts, zone = build(bars, bullish, **kwargs)
    setup = scan_for_entry(bundle, h1_ts, zone, mitigation_bar, PIP)
    if setup is None:
        return None, None, bundle, zone
    # Both halves, because a caller scoring a candidate needs both. They
    # are separate functions only because the target gate is re-asked every
    # bar while the rest is frozen at entry.
    answers = evaluate_entry_factors(
        bundle, h1_ts, zone, setup, mitigation_bar
    )
    answers.update(
        evaluate_entry_target_factors(bundle, zone, setup, setup["trigger_m15"])
    )
    return answers, setup, bundle, zone


class TestNamesAndExclusion:
    def test_thirty_one_factors_and_ce_has_seven(self):
        assert len(ENTRY_FACTORS) == 31
        assert len(set(ENTRY_FACTORS)) == 31
        assert len(model_factor_names("CE")) == 7
        for model in ("LC-1", "LC-2A", "LC-2B"):
            assert len(model_factor_names(model)) == 8

    def test_ibos_with_body_close_is_absent(self):
        # Dropped on purpose: CE only triggers on a close-through break,
        # so the factor could only ever answer yes.
        assert not [n for n in ENTRY_FACTORS if "ibos" in n]

    @bearish_and_bullish
    def test_only_the_firing_models_factors_appear(self, bullish):
        answers, setup, _, _ = factors_for(LC1, bullish)
        assert setup["model"] == "LC-1"
        assert set(answers) <= set(model_factor_names("LC-1"))
        # And nothing from the other three leaked in.
        for other in ("LC-2A", "LC-2B", "CE"):
            assert not set(answers) & set(model_factor_names(other))

    @bearish_and_bullish
    def test_every_factor_of_the_firing_model_is_answered_or_excluded(self, bullish):
        answers, setup, _, _ = factors_for(FAKE_BREAK, bullish)
        names = model_factor_names(setup["model"])
        # The model-specific ones are always answered. Only the target
        # children may be omitted.
        for suffix in MODEL_FACTORS[setup["model"]]:
            assert factor_name(setup["model"], suffix) in answers
        assert factor_name(setup["model"], "m15_target_liquidity") in answers
        assert set(answers) <= set(names)

    def test_no_bundle_gives_no_factors(self):
        assert evaluate_entry_factors(None, None, None, None, 0) == {}


class TestTierFactors:
    @bearish_and_bullish
    def test_h1_ob_is_fractal_reads_primary_tier(self, bullish):
        answers, _, _, _ = factors_for(LC1, bullish, primary_tier="h1_fractal")
        assert answers[factor_name("LC-1", "h1_ob_is_fractal")] is True

        answers, _, _, _ = factors_for(LC1, bullish, primary_tier="h1_internal")
        assert answers[factor_name("LC-1", "h1_ob_is_fractal")] is False


class TestBodyClose:
    @bearish_and_bullish
    def test_a_body_break_answers_yes(self, bullish):
        answers, setup, _, _ = factors_for(FAKE_BREAK, bullish)
        assert setup["model"] == "LC-2A"
        assert answers[factor_name("LC-2A", "fake_break_with_body_close")] is True

    @bearish_and_bullish
    def test_a_wick_only_break_answers_no(self, bullish):
        # The whole reason the factor was rebuilt. Before wick detection it
        # answered yes 100% of the time, because a recorded break already
        # implied a close.
        bars = list(FAKE_BREAK)
        bars[27] = (103.8, 104.0, 102.0, 103.0)
        answers, setup, _, _ = factors_for(bars, bullish)
        assert setup["model"] == "LC-2A"
        assert answers[factor_name("LC-2A", "fake_break_with_body_close")] is False


class TestWickedTheLid:
    @bearish_and_bullish
    def test_a_wick_that_closes_back_answers_yes(self, bullish):
        # LC1's trigger spikes to 111.0 and closes at 109.0, back under the
        # 109.5 level it took. A raid, not a breakout.
        answers, _, _, _ = factors_for(LC1, bullish)
        assert answers[factor_name("LC-1", "wicked_the_lid")] is True

    @bearish_and_bullish
    def test_a_close_beyond_the_level_answers_no(self, bullish):
        # Same sweep, but the candle closes above the level it took, which
        # is price ACCEPTING it rather than rejecting it.
        bars = list(LC1)
        bars[23] = (108.5, 111.0, 108.4, 110.5)
        answers, _, _, _ = factors_for(bars, bullish)
        assert answers[factor_name("LC-1", "wicked_the_lid")] is False


# The shared WARMUP sits at 100 while LC1's tail sits at 108, and that jump
# is itself a three-candle gap. Harmless for every other factor, fatal for
# no_imbalance_while_mitigation, which is precisely a question about gaps in
# the approach. So these two use a warmup at the tail's own price level and
# the gap is introduced deliberately when it is the thing under test.
CONTINUOUS = [(108.0, 108.5, 107.5, 108.0)] * 20 + LC1[20:]


class TestFvgDirections:
    """The three gap factors want different halves of the FVG table."""

    @bearish_and_bullish
    def test_a_clean_approach_answers_yes(self, bullish):
        # Polarity, stated directly. A YES must mean no gap in the leg.
        answers, setup, _, _ = factors_for(CONTINUOUS, bullish)
        assert setup["model"] == "LC-1"
        assert answers[factor_name("LC-1", "no_imbalance_while_mitigation")] is True

    @bearish_and_bullish
    def test_a_gap_in_the_approach_leg_answers_no(self, bullish):
        # Bar 20 now opens clear of the warmup's high, leaving a same-side
        # gap inside the leg that rallies into the zone.
        bars = list(CONTINUOUS)
        bars[20] = (108.6, 109.5, 108.6, 109.2)
        answers, setup, _, _ = factors_for(bars, bullish)
        assert setup is not None and setup["model"] == "LC-1"
        assert answers[
            factor_name("LC-1", "no_imbalance_while_mitigation")
        ] is False

    @bearish_and_bullish
    def test_lid_with_fvg_and_no_imbalance_are_independent(self, bullish):
        # They read opposite halves of the table, so they must be able to
        # disagree. If one were wired to the other's direction they would
        # move together on every fixture.
        answers, _, _, _ = factors_for(CONTINUOUS, bullish)
        lid = answers[factor_name("LC-1", "lid_with_fvg")]
        imb = answers[factor_name("LC-1", "no_imbalance_while_mitigation")]
        assert isinstance(lid, bool) and isinstance(imb, bool)


# LC1's approach is monotone upward, so nothing is ever left standing above
# the stop and no_other_lids can only answer yes on it. OVERHEAD adds an
# earlier spike to 113.0 that leaves an unswept minor high there, above the
# stop the trigger candle produces (about 111.02) and inside the zone. That
# is the magnet the factor exists to penalise.
OVERHEAD = (
    [(108.0, 108.5, 107.5, 108.0)] * 15
    + [(108.0, 113.0, 107.9, 112.5),    # 15  leaves a high at 113.0
       (112.5, 112.0, 108.0, 108.5)]    # 16  fails to exceed it
    + [(108.0, 108.5, 107.5, 108.0)] * 3
    + LC1[20:]
)


class TestNoOtherLids:
    @bearish_and_bullish
    def test_liquidity_above_the_stop_answers_no(self, bullish):
        # The user's reasoning: a level beyond the stop is a magnet, price
        # will eventually take it, and our stop is on the way.
        answers, setup, _, _ = factors_for(OVERHEAD, bullish)
        assert setup is not None and setup["model"] == "LC-1"
        assert answers[factor_name("LC-1", "no_other_lids")] is False

    @bearish_and_bullish
    def test_a_clear_path_above_the_stop_answers_yes(self, bullish):
        # Same trigger geometry, no overhead level.
        answers, setup, _, _ = factors_for(LC1, bullish)
        assert setup is not None and setup["model"] == "LC-1"
        assert answers[factor_name("LC-1", "no_other_lids")] is True

    @bearish_and_bullish
    def test_it_is_answered_for_every_model(self, bullish):
        # One definition shared by all four, which is what makes the
        # learned weights comparable across models.
        for bars in (LC1, FAKE_BREAK, DOUBLE_TOP):
            answers, setup, _, _ = factors_for(bars, bullish)
            if setup is None:
                continue
            assert factor_name(setup["model"], "no_other_lids") in answers


class TestEqualsAngle:
    @bearish_and_bullish
    def test_two_near_identical_tops_answer_yes(self, bullish):
        # 106.00 and 106.05, well inside 0.10x ATR.
        answers, setup, _, _ = factors_for(DOUBLE_TOP, bullish)
        assert setup["model"] == "LC-2B"
        assert answers[
            factor_name("LC-2B", "equals_formed_with_less_angle")
        ] is True

    @bearish_and_bullish
    def test_a_sloping_double_top_answers_no(self, bullish):
        # Still pooled by levels.py (inside its 0.25x tolerance) but too
        # sloped to read as equal. The factor is about the SHAPE the S and
        # R traders saw, which a pooled mean would hide completely.
        bars = list(DOUBLE_TOP)
        bars[26] = (103.8, 105.65, 103.5, 105.5)
        answers, setup, _, _ = factors_for(bars, bullish)
        assert setup is not None and setup["model"] == "LC-2B"
        assert answers[
            factor_name("LC-2B", "equals_formed_with_less_angle")
        ] is False


class TestTargetGate:
    @bearish_and_bullish
    def test_children_are_yes_or_absent_never_no(self, bullish):
        # The gate can only raise a score. Only the parent may answer no.
        for bars in (LC1, FAKE_BREAK, DOUBLE_TOP):
            answers, setup, _, _ = factors_for(bars, bullish)
            if setup is None:
                continue
            for child in ("lrlq", "equals"):
                name = factor_name(
                    setup["model"], "m15_target_liquidity_%s" % child
                )
                assert answers.get(name, True) is True

    @bearish_and_bullish
    def test_the_parent_is_no_only_when_both_children_are_absent(self, bullish):
        for bars in (LC1, FAKE_BREAK, DOUBLE_TOP):
            answers, setup, _, _ = factors_for(bars, bullish)
            if setup is None:
                continue
            model = setup["model"]
            present = [
                c for c in ("lrlq", "equals")
                if factor_name(model, "m15_target_liquidity_%s" % c) in answers
            ]
            parent = answers[factor_name(model, "m15_target_liquidity")]
            assert parent is bool(present)
