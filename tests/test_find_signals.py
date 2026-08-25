"""find_signals: the H1-to-M15 seam, end to end.

This was never covered. The old version was not tested either, so nothing
regressed, but the new one is where the two index spaces meet and where a
mistake is both silent and total: every candidate would be scored against
the wrong bar.

An ObUniverse is built by hand rather than through build_pipeline_bundle,
the same way tests/test_ob_factors.py does it. Detecting a real order
block needs enough H1 history to form structure, which would bury the
three bars that actually matter.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.context import build_market_context
from backtest.factors import ALL_FACTORS, ALWAYS_FACTORS, ENTRY_FACTORS
from backtest.m15_pipeline import build_m15_bundle
from backtest.simulate import find_signals, zone_for
from smc.order_blocks.ob_state import ObSeries, ObUniverse
from tests.test_entry_models import AXIS, LC1, PIP, mirror_bar

# The LC1 fixture puts its trigger at M15 bar 23, which is 07:45 when the
# frame starts at 02:00, so the mitigation lands in H1 bar 5.
MITIGATION_H1 = 5
ZONE_TOP, ZONE_BOTTOM = 115.0, 110.0


def frames(bullish):
    """(h1_df, m15_df) for the LC1 fixture, mirrored when bullish."""
    bars = [mirror_bar(b) for b in LC1] if bullish else LC1
    m15 = pd.DataFrame([
        {
            "date": pd.Timestamp("2024-01-01 02:00", tz="UTC")
            + pd.Timedelta(minutes=15 * i),
            "open": o, "high": h, "low": l, "close": c,
        }
        for i, (o, h, l, c) in enumerate(bars)
    ])
    hours = int(len(bars) / 4) + 2
    h1 = pd.DataFrame([
        {
            "date": pd.Timestamp("2024-01-01 02:00", tz="UTC")
            + pd.Timedelta(hours=i),
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
        }
        for i in range(hours)
    ])
    # The Always gate reads its answers straight off the merged frame's
    # structure and zone columns, and the factor names ARE the column
    # names. Present but None means every one of them answers no, which is
    # what a fixture with no higher-timeframe context should score. Absent
    # would be a KeyError, and rightly so: a real frame always has them.
    for name in ALWAYS_FACTORS:
        h1[name] = None
    return h1, m15


def universe(n, bullish, primary_tier="h1_fractal", trigger_bar=MITIGATION_H1):
    """A one-zone ObUniverse whose only qualifying touch is at trigger_bar."""
    top, bottom = ZONE_TOP, ZONE_BOTTOM
    if bullish:
        top, bottom = 2 * AXIS - bottom, 2 * AXIS - top
    series = ObSeries(
        timeframe="H1",
        top=np.array([top]),
        bottom=np.array([bottom]),
        midpoint=np.array([(top + bottom) / 2.0]),
        sign=np.array([1 if bullish else -1], dtype=np.int8),
        visible_from=np.array([0], dtype=np.int64),
        mitigated_at=np.array([trigger_bar], dtype=np.int64),
        valid_through=np.array([n - 1], dtype=np.int64),
        flip_known_from=np.array([n], dtype=np.int64),
        fvg_stale_from=np.array([n], dtype=np.int64),
        touch_at=[np.array([trigger_bar], dtype=np.int64)],
        quality={},
        src_index=np.array([0], dtype=np.int64),
        primary_tier=np.array([primary_tier], dtype=object),
    )
    trigger_ob = np.full(n, -1, dtype=np.int64)
    trigger_ob[trigger_bar] = 0
    touch_no = np.zeros(n, dtype=np.int64)
    touch_no[trigger_bar] = 1
    return ObUniverse(
        n=n,
        series={"H1": series},
        trigger_ob=trigger_ob,
        trigger_touch_no=touch_no,
        target_above={"H1": np.full(n, -1, dtype=np.int64)},
        target_below={"H1": np.full(n, -1, dtype=np.int64)},
        mitigated_htf={},
    )


def ctx_for(bullish, with_m15=True, **kwargs):
    h1, m15 = frames(bullish)
    bundle = build_m15_bundle(m15) if with_m15 else None
    return build_market_context(
        h1, PIP,
        obs=universe(len(h1), bullish, **kwargs),
        m15_bundle=bundle,
    )


def signals_for(bullish, weights=None, **kwargs):
    ctx = ctx_for(bullish, **kwargs)
    if weights is None:
        weights = flat_weights()
    return find_signals(ctx, weights, PIP)


def flat_weights():
    """Every factor at 1.0. compute_probability indexes `weights` by every
    factor in factor_results, so a partial table raises rather than
    scoring zero.
    """
    return {name: 1.0 for name in ALL_FACTORS}


bearish_and_bullish = pytest.mark.parametrize(
    "bullish", [False, True], ids=["bearish", "bullish"]
)


class TestItProducesCandidates:
    @bearish_and_bullish
    def test_one_signal_for_the_lc1_setup(self, bullish):
        signals = signals_for(bullish)
        assert len(signals) == 1
        assert signals[0]["entry_model"] == "LC-1"
        assert signals[0]["direction"] == ("bullish" if bullish else "bearish")

    @bearish_and_bullish
    def test_the_price_and_stop_come_from_the_m15_setup(self, bullish):
        # Not from the zone. This is the whole point of the entry layer:
        # entry used to be the H1 close with the stop beyond the zone edge.
        s = signals_for(bullish)[0]
        buffer_price = 2.0 * PIP
        if bullish:
            trigger = mirror_bar(LC1[23])
            assert s["entry_price"] == pytest.approx(trigger[1] + buffer_price)
        else:
            assert s["entry_price"] == pytest.approx(108.4 - buffer_price)
        assert s["r_distance"] == pytest.approx(111.0 - 108.4 + 4.0 * PIP)
        # And it is far tighter than the zone, which is 5.0 wide.
        assert s["r_distance"] < (ZONE_TOP - ZONE_BOTTOM)


class TestIndexSpaces:
    @bearish_and_bullish
    def test_idx_is_the_h1_bar_containing_the_m15_fill(self, bullish):
        # The seam. The fill is M15 bar 24, which is 08:00, so H1 bar 6.
        s = signals_for(bullish)[0]
        assert s["idx"] == 6
        assert s["m15_fill_time"] == pd.Timestamp("2024-01-01 08:00", tz="UTC")

    @bearish_and_bullish
    def test_the_mitigation_bar_is_reported_separately(self, bullish):
        s = signals_for(bullish)[0]
        assert s["mitigation_idx"] == MITIGATION_H1
        # The entry is LATER than the mitigation, which is new: the old
        # engine entered on the mitigating candle itself.
        assert s["idx"] > s["mitigation_idx"]

    @bearish_and_bullish
    def test_the_trigger_time_is_an_m15_timestamp(self, bullish):
        s = signals_for(bullish)[0]
        assert s["m15_trigger_time"] == pd.Timestamp("2024-01-01 07:45", tz="UTC")


class TestTwoProbabilities:
    @bearish_and_bullish
    def test_both_are_reported_and_probability_aliases_the_total(self, bullish):
        s = signals_for(bullish)[0]
        assert s["htf_probability"] is not None
        assert s["total_probability"] is not None
        assert s["probability"] == s["total_probability"]

    @bearish_and_bullish
    def test_the_htf_gate_blocks_the_scan(self, bullish):
        ctx = ctx_for(bullish)
        weights = flat_weights()
        wide_open = find_signals(ctx, weights, PIP, htf_threshold=None)
        assert len(wide_open) == 1
        htf = wide_open[0]["htf_probability"]
        # Just above what this candidate scored: nothing survives.
        assert find_signals(ctx, weights, PIP, htf_threshold=htf + 1.0) == []
        # At or below it: it does.
        assert len(find_signals(ctx, weights, PIP, htf_threshold=htf)) == 1

    @bearish_and_bullish
    def test_only_the_firing_models_entry_factors_are_scored(self, bullish):
        s = signals_for(bullish)[0]
        scored = set(s["factor_results"]) & set(ENTRY_FACTORS)
        assert scored
        assert all(name.startswith("entry_lc1_") for name in scored)

    @bearish_and_bullish
    def test_the_other_models_factors_are_reported_as_excluded(self, bullish):
        s = signals_for(bullish)[0]
        excluded = set(s["excluded_gates"])
        assert any(n.startswith("entry_ce_") for n in excluded)
        assert not any(n in excluded for n in s["factor_results"])


class TestDegradation:
    @bearish_and_bullish
    def test_no_m15_bundle_yields_nothing(self, bullish):
        # NAS100 has no M15 at all. No candidates, not an error.
        assert signals_for(bullish, with_m15=False) == []

    @bearish_and_bullish
    def test_no_ob_state_yields_nothing(self, bullish):
        h1, m15 = frames(bullish)
        ctx = build_market_context(h1, PIP, m15_bundle=build_m15_bundle(m15))
        assert find_signals(ctx, flat_weights(), PIP) == []

    @bearish_and_bullish
    def test_a_swing_tier_zone_yields_nothing_for_an_lc1_only_setup(self, bullish):
        # LC-1 is banned on the swing tier and nothing else fires here, so
        # the precedence rule has to survive the whole way out to a signal.
        assert signals_for(bullish, primary_tier="h1_swing") == []


class TestFrozenFactors:
    @bearish_and_bullish
    def test_the_frozen_set_carries_the_entry_model_factors(self, bullish):
        # simulate_trade rebuilds its live score on top of this, so the
        # entry factors have to be in it or the mid-trade recheck would
        # compare a partial score against a total threshold.
        s = signals_for(bullish)[0]
        frozen = set(s["mitigation_factor_results"])
        assert frozen & set(ENTRY_FACTORS)

    @bearish_and_bullish
    def test_the_m15_target_gate_is_NOT_frozen(self, bullish):
        # It is the one entry factor re-asked every bar, because a target
        # price has covered drops out of the calculation.
        s = signals_for(bullish)[0]
        frozen = set(s["mitigation_factor_results"])
        assert not any("m15_target_liquidity" in n for n in frozen)

    @bearish_and_bullish
    def test_the_zone_and_setup_travel_with_the_signal(self, bullish):
        # simulate_trade needs both to re-ask the target gate.
        s = signals_for(bullish)[0]
        assert s["entry_zone"] is not None
        assert s["entry_setup"]["model"] == "LC-1"


class TestZoneFor:
    @bearish_and_bullish
    def test_it_reads_primary_tier_off_the_series(self, bullish):
        ctx = ctx_for(bullish, primary_tier="h1_internal")
        assert zone_for(ctx, 0).primary_tier == "h1_internal"
