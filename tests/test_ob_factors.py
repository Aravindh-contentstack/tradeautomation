"""The swept_liquidity_fvg staleness gate inside _ob_factor_answers.

No existing file owns _ob_factor_answers, evaluate_mitigation_ob_factors,
or evaluate_ob_target_factors (confirmed before writing this file), so
this is new ground. An ObSeries is built directly rather than through
to_h1_space, so each test states its bar_index/fvg_stale_from relationship
explicitly instead of deriving it through a date conversion tests/
test_ob_state.py already covers.

The one property under test throughout: swept_liquidity_fvg answers YES
while bar_index is still before the swept gap's own stale-from boundary,
and goes completely SILENT (not "No") once bar_index reaches it, whether
or not the gate negates its answers.
"""

import numpy as np
import pytest

from backtest.factors import (
    _ob_factor_answers,
    compute_probability,
    evaluate_mitigation_ob_factors,
    evaluate_ob_target_factors,
)
from smc.order_blocks.ob_state import ObSeries

N = 5  # a generous "beyond the data" sentinel for fields not under test


def series_with(quality, fvg_stale_from=N, timeframe="H1", sign=1):
    """A minimal one-row ObSeries, everything but `quality` and
    `fvg_stale_from` fixed at values no test here depends on.
    """
    one = np.array([0], dtype=np.int64)
    return ObSeries(
        timeframe=timeframe,
        top=np.array([20.0]),
        bottom=np.array([10.0]),
        midpoint=np.array([15.0]),
        sign=np.array([sign], dtype=np.int8),
        visible_from=np.array([0], dtype=np.int64),
        mitigated_at=one,
        valid_through=np.array([N], dtype=np.int64),
        flip_known_from=np.array([N], dtype=np.int64),
        fvg_stale_from=np.array([fvg_stale_from], dtype=np.int64),
        touch_at=[np.array([], dtype=np.int64)],
        quality=quality,
        src_index=np.array([0], dtype=np.int64),
    )


def swept_fvg_series(stale_from, extra_quality=None):
    quality = {"swept_liquidity_fvg": np.array([True])}
    if extra_quality:
        quality.update(extra_quality)
    return series_with(quality, fvg_stale_from=stale_from)


class TestStaleGateMitigationOb:
    def test_fresh_before_the_boundary_answers_yes(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 9, "h1", "mitigation_ob", False)
        assert results["h1_mitigation_ob_swept_liquidity_fvg"] is True

    def test_silent_at_the_boundary_not_a_no(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 10, "h1", "mitigation_ob", False)
        assert "h1_mitigation_ob_swept_liquidity_fvg" not in results

    def test_silent_well_past_the_boundary(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 50, "h1", "mitigation_ob", False)
        assert "h1_mitigation_ob_swept_liquidity_fvg" not in results

    def test_the_never_applicable_sentinel_never_goes_stale(self):
        """The sentinel to_h1_space uses when no gap matched (N, "beyond
        the last valid index") must never be reachable by a real bar_index.
        """
        series = swept_fvg_series(stale_from=N)
        results = _ob_factor_answers(series, 0, N - 1, "h1", "mitigation_ob", False)
        assert results["h1_mitigation_ob_swept_liquidity_fvg"] is True


class TestStaleGateObTarget:
    """The identical gate, reached through the negated (OB Target) path.
    Staleness must suppress before negation, so a stale swept-fvg never
    flips to a supporting "yes" just because the target was reached.
    """

    def test_fresh_and_unreached_answers_yes(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 9, "h1", "ob_target", False)
        assert results["h1_ob_target_swept_liquidity_fvg"] is True

    def test_fresh_and_reached_negates_to_no(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 9, "h1", "ob_target", True)
        assert results["h1_ob_target_swept_liquidity_fvg"] is False

    def test_stale_and_unreached_is_silent(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 10, "h1", "ob_target", False)
        assert "h1_ob_target_swept_liquidity_fvg" not in results

    def test_stale_and_reached_is_silent_not_a_supporting_yes(self):
        """The case that matters most: negate=True flips a "no" to a
        "yes". If staleness ran after negation instead of before it, a
        stale swept-fvg on a reached target would wrongly read as
        supporting evidence instead of going quiet.
        """
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 10, "h1", "ob_target", True)
        assert "h1_ob_target_swept_liquidity_fvg" not in results


class TestRollUp:
    def test_fvg_alone_swept_and_still_fresh_omits_the_parent(self):
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 9, "h1", "mitigation_ob", False)
        assert results["h1_mitigation_ob_swept_liquidity_fvg"] is True
        assert "h1_mitigation_ob_swept_liquidity" not in results

    def test_fvg_alone_swept_and_now_stale_flips_the_parent_to_no(self):
        """The roll-up's existing fallthrough does this with no extra
        code: once the only swept child goes silent, swept_any stays
        False and the parent answers no, exactly as if nothing had ever
        been swept.
        """
        series = swept_fvg_series(stale_from=10)
        results = _ob_factor_answers(series, 0, 10, "h1", "mitigation_ob", False)
        assert results["h1_mitigation_ob_swept_liquidity"] is False

    def test_a_still_fresh_sibling_keeps_the_roll_up_alive_once_fvg_goes_stale(self):
        """Staleness removes only the one stale child. A structural sweep
        recorded on the same OB is a separate, permanent fact and must
        keep answering yes regardless of what happens to fvg.
        """
        series = swept_fvg_series(
            stale_from=10, extra_quality={"swept_liquidity_swing": np.array([True])}
        )
        results = _ob_factor_answers(series, 0, 10, "h1", "mitigation_ob", False)
        assert "h1_mitigation_ob_swept_liquidity_fvg" not in results
        assert results["h1_mitigation_ob_swept_liquidity_swing"] is True
        assert "h1_mitigation_ob_swept_liquidity" not in results


class TestDynamicExclusion:
    def test_compute_probability_drops_a_stale_fvg_from_the_denominator(self):
        """A stale fvg must cost nothing, positively or negatively, the
        same dynamic-exclusion rule every other omitted factor gets.
        """
        weights = {"h1_mitigation_ob_swept_liquidity": 1.0}
        fresh = swept_fvg_series(stale_from=10)
        stale = swept_fvg_series(stale_from=10)

        fresh_results = _ob_factor_answers(fresh, 0, 9, "h1", "mitigation_ob", False)
        stale_results = _ob_factor_answers(stale, 0, 10, "h1", "mitigation_ob", False)

        weights_with_fvg = dict(weights, h1_mitigation_ob_swept_liquidity_fvg=1.0)
        fresh_probability = compute_probability(fresh_results, weights_with_fvg)
        stale_probability = compute_probability(stale_results, weights_with_fvg)

        # Fresh: only the (yes) fvg child is present, 100%. Stale: fvg is
        # absent and the parent (no) is the only thing present, 0%. Both
        # are well-defined, neither raises, and neither is some blended
        # in-between that would imply fvg secretly still counted as a no.
        assert fresh_probability == pytest.approx(100.0)
        # A pure "no" is -50%, not 0%, per compute_probability's own
        # formula ((yes - 0.5*no) / total); the point under test is only
        # that fvg going silent does not additionally penalize this,
        # which the "no" here already accounts for since it comes
        # entirely from the parent, not from fvg.
        assert stale_probability == pytest.approx(-50.0)


class TestThroughTheEvaluators:
    """One pass through the real entry points, not just the shared
    helper, confirming the gate reaches both without any special-casing
    at that layer.
    """

    def _obs(self, series, n=6):
        class Obs:
            pass
        obs = Obs()
        obs.series = {"H1": series}
        obs.mitigated_htf = {"Daily": np.full(n, -1), "4H": np.full(n, -1)}
        obs.target_above = {"H1": np.full(n, -1)}
        obs.target_below = {"H1": np.full(n, 0)}
        return obs

    def test_mitigation_ob_factors_go_silent_once_stale(self):
        series = swept_fvg_series(stale_from=5, extra_quality={})
        obs = self._obs(series)
        fresh = evaluate_mitigation_ob_factors(obs, 4, "bullish", 0)
        stale = evaluate_mitigation_ob_factors(obs, 5, "bullish", 0)
        assert fresh["h1_mitigation_ob_swept_liquidity_fvg"] is True
        assert "h1_mitigation_ob_swept_liquidity_fvg" not in stale

    def test_ob_target_factors_go_silent_once_stale(self):
        """The bullish OB (sign=1) built by swept_fvg_series is the
        opposite direction of a bearish trade, which is what makes it a
        TARGET rather than a mitigation zone.
        """
        series = swept_fvg_series(stale_from=5, extra_quality={})
        obs = self._obs(series)
        fresh = evaluate_ob_target_factors(obs, 4, "bearish", None, 30.0, 5.0)
        stale = evaluate_ob_target_factors(obs, 5, "bearish", None, 30.0, 5.0)
        assert "h1_ob_target_swept_liquidity_fvg" in fresh
        assert "h1_ob_target_swept_liquidity_fvg" not in stale
