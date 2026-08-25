"""The M15 entry-model scan: the eq/N=2 interaction, LC-1, and the order.

How the mirroring is tested
---------------------------
Every fixture is written BEARISH and the bullish case is DERIVED by
reflecting every price through `AXIS`, which also swaps each bar's high
and low and turns every green candle red. So the two directions are the
same fixture seen upside down, and a rule implemented for only one side
fails rather than going quietly untested. That is the single highest-value
property in this file: a mirrored comparison is the likeliest bug in
entry_models.py and the hardest to spot by reading.

`bearish_and_bullish` is the parametrisation. Read any test as "build
these bars, reflect them if we are testing long, assert the same thing".

Why the fixtures start at 02:00
-------------------------------
Two constraints collide. ATR(14) needs fourteen bars before the proximity
bound means anything, and the trigger candle has to close inside a
killzone (07:00 London at the earliest). Twenty warm-up bars from 02:00
satisfies both, and January dates mean London is UTC so the hours read
literally.
"""

import pandas as pd
import pytest

from backtest.entry_models import (
    CE_RUNAWAY_MULTIPLE,
    LC1_FRESHNESS_BARS,
    MODEL_PRECEDENCE,
    Zone,
    _internal_break_at,
    _resolve_ce,
    pending_order_for,
    scan_for_entry,
)
from backtest.m15_pipeline import build_m15_bundle

AXIS = 100.0
PIP = 0.01
START = "2024-01-01 02:00"

# Twenty bars of identical 1.0-range candles: enough to seed ATR(14) at
# 1.0 exactly, and they end at 06:45 so nothing here can trigger inside a
# killzone by accident.
WARMUP = [(100.0, 100.5, 99.5, 100.0)] * 20

# The bearish zone every fixture aims at. eq is 112.5, so a trigger that
# reaches 111.0 has NOT breached it.
ZONE_TOP = 115.0
ZONE_BOTTOM = 110.0


def mirror_bar(bar):
    """Reflect one bar through AXIS. High and low necessarily swap."""
    o, h, l, c = bar
    return (2 * AXIS - o, 2 * AXIS - l, 2 * AXIS - h, 2 * AXIS - c)


def build(bars, bullish, top=ZONE_TOP, bottom=ZONE_BOTTOM,
          primary_tier="h1_fractal", valid_through=None):
    """(bundle, h1_ts, zone) for `bars`, reflected when bullish."""
    if bullish:
        bars = [mirror_bar(bar) for bar in bars]
        top, bottom = 2 * AXIS - bottom, 2 * AXIS - top

    frame = pd.DataFrame([
        {
            "date": pd.Timestamp(START, tz="UTC") + pd.Timedelta(minutes=15 * i),
            "open": o, "high": h, "low": l, "close": c,
        }
        for i, (o, h, l, c) in enumerate(bars)
    ])
    bundle = build_m15_bundle(frame)

    # One H1 bar per hour across the whole M15 span, so h1_bar_containing
    # always resolves and the zone's life is bounded only by valid_through.
    hours = int(len(bars) / 4) + 2
    h1_ts = pd.DatetimeIndex([
        pd.Timestamp(START) + pd.Timedelta(hours=i) for i in range(hours)
    ]).to_numpy(dtype="datetime64[ns]")

    zone = Zone(
        top=top,
        bottom=bottom,
        bullish=bullish,
        primary_tier=primary_tier,
        valid_through=hours - 1 if valid_through is None else valid_through,
    )
    return bundle, h1_ts, zone


def scan(bars, bullish, mitigation_bar=5, **kwargs):
    bundle, h1_ts, zone = build(bars, bullish, **kwargs)
    return scan_for_entry(bundle, h1_ts, zone, mitigation_bar, PIP)


bearish_and_bullish = pytest.mark.parametrize(
    "bullish", [False, True], ids=["bearish", "bullish"]
)


# --- The LC-1 fixture, bar by bar -------------------------------------
#
#   20  green to 109.5, an attempt up that fails            -> the level
#   21  high 109.3, does not exceed it                      -> confirms it
#   22  filler
#   23  high 111.0: sweeps 109.5 AND reaches the zone       -> the trigger
#   24  low 108.0: trades through the resting order         -> the fill
#
# 07:00 London lands on bar 20, so bars 20 onward are in the killzone.
LC1 = WARMUP + [
    (108.0, 109.5, 107.9, 109.2),
    (109.2, 109.3, 108.0, 108.2),
    (108.2, 109.0, 108.0, 108.5),
    (108.5, 111.0, 108.4, 109.0),
    (109.0, 109.2, 108.0, 108.2),
]


class TestLC1:
    @bearish_and_bullish
    def test_it_fires_when_one_candle_sweeps_and_mitigates(self, bullish):
        setup = scan(LC1, bullish)
        assert setup is not None
        assert setup["model"] == "LC-1"
        assert setup["trigger_m15"] == 23
        assert setup["direction"] == ("bullish" if bullish else "bearish")

    @bearish_and_bullish
    def test_the_swept_level_is_reported_as_evidence(self, bullish):
        setup = scan(LC1, bullish)
        expected = 2 * AXIS - 109.5 if bullish else 109.5
        assert setup["evidence"]["level"] == pytest.approx(expected)
        assert setup["evidence"]["sweep_m15"] == 23

    @bearish_and_bullish
    def test_a_level_beyond_the_proximity_bound_does_not_fire(self, bullish):
        # Same shape, but the level sits at 105.0 rather than 109.5, which
        # is more than 1x ATR below the zone's 110.0 bottom. This is the
        # bound that took LC-1's hit rate from 90% to 16%.
        bars = list(LC1)
        bars[20] = (104.0, 105.0, 103.9, 104.8)
        bars[21] = (104.8, 104.9, 104.0, 104.2)
        bars[22] = (104.2, 104.5, 104.0, 104.3)
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_a_stale_level_does_not_fire(self, bullish):
        # The level forms, then nothing happens for longer than the
        # freshness window before the sweep arrives.
        filler = [(108.2, 109.0, 108.0, 108.5)] * (LC1_FRESHNESS_BARS + 2)
        bars = LC1[:22] + filler + LC1[23:]
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_a_sweep_that_never_reaches_the_zone_does_not_fire(self, bullish):
        # Sweeps 109.5 but tops out at 109.8, short of the 110.0 edge. The
        # rule is an AND: liquidity taken AND zone mitigated.
        bars = list(LC1)
        bars[23] = (108.5, 109.8, 108.4, 109.0)
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_a_trigger_outside_a_killzone_does_not_fire(self, bullish):
        # Same bars, shifted so the trigger lands at 04:45 London instead
        # of 07:45. Decision 4 moved the session gate to the entry candle.
        bundle, h1_ts, zone = build(LC1, bullish)
        assert scan_for_entry(bundle, h1_ts, zone, 5, PIP) is not None

        frame = pd.DataFrame([
            {
                "date": pd.Timestamp("2023-12-31 21:00", tz="UTC")
                + pd.Timedelta(minutes=15 * i),
                "open": o, "high": h, "low": l, "close": c,
            }
            for i, (o, h, l, c) in enumerate(
                [mirror_bar(b) for b in LC1] if bullish else LC1
            )
        ])
        shifted = build_m15_bundle(frame)
        h1_shifted = pd.DatetimeIndex([
            pd.Timestamp("2023-12-31 21:00") + pd.Timedelta(hours=i)
            for i in range(12)
        ]).to_numpy(dtype="datetime64[ns]")
        top, bottom = ZONE_TOP, ZONE_BOTTOM
        if bullish:
            top, bottom = 2 * AXIS - bottom, 2 * AXIS - top
        off_hours = Zone(top=top, bottom=bottom, bullish=bullish,
                         primary_tier="h1_fractal", valid_through=11)
        assert scan_for_entry(shifted, h1_shifted, off_hours, 0, PIP) is None

    @bearish_and_bullish
    def test_no_lc1_on_the_swing_tier(self, bullish):
        # A swing range is too large to predict a break off one swept
        # minor high. The freshness rule does not enforce this, the
        # absence from MODEL_PRECEDENCE does.
        assert "LC-1" not in MODEL_PRECEDENCE["h1_swing"]
        assert scan(LC1, bullish, primary_tier="h1_swing") is None
        assert scan(LC1, bullish, primary_tier="h1_internal") is not None


class TestLC2A:
    # Rally, swing high at 106.0, pull back to a swing low at 101.0, bounce,
    # then CLOSE below 101.0. That break flips m15_fractal bearish and traps
    # early shorts whose stops sit above 106.0, which is the PBID. Bar 29
    # raids it and reaches the zone in one candle.
    FAKE_BREAK = WARMUP + [
        (100.0, 102.00, 99.8, 101.8),   # 20
        (101.8, 104.00, 101.5, 103.8),  # 21
        (103.8, 106.00, 103.5, 105.8),  # 22 swing high 106.0
        (105.8, 105.00, 103.0, 103.2),  # 23
        (103.2, 103.50, 101.0, 101.2),  # 24 swing low 101.0
        (101.2, 103.00, 101.1, 102.8),  # 25
        (102.8, 104.00, 102.5, 103.8),  # 26
        (103.8, 104.00, 100.5, 100.8),  # 27 closes below 101.0: fake break
        (100.8, 107.00, 100.6, 106.5),  # 28 raids 106.0
        (106.5, 111.00, 106.0, 107.0),  # 29 raids and mitigates: trigger
        (107.0, 107.50, 104.0, 104.5),  # 30 fill
    ]

    @bearish_and_bullish
    def test_it_fires_on_a_fake_break(self, bullish):
        setup = scan(self.FAKE_BREAK, bullish)
        assert setup is not None
        assert setup["model"] == "LC-2A"
        assert setup["trigger_m15"] == 29

    @bearish_and_bullish
    def test_the_pbid_is_the_swing_high_live_at_the_break(self, bullish):
        # Read at the break bar, not at the trigger. A later fractal
        # replacing it does not move the trapped traders' stops.
        setup = scan(self.FAKE_BREAK, bullish)
        expected = 2 * AXIS - 106.0 if bullish else 106.0
        assert setup["evidence"]["level"] == pytest.approx(expected)
        assert setup["evidence"]["sweep_m15"] == 27

    @bearish_and_bullish
    def test_a_body_break_reports_body_closed(self, bullish):
        setup = scan(self.FAKE_BREAK, bullish)
        assert setup["evidence"]["body_closed"] is True

    @bearish_and_bullish
    def test_a_wick_only_break_still_fires_but_is_not_a_body_close(self, bullish):
        # The user's scenario 1. Bar 27 no longer CLOSES below the swing
        # low, so the detector reports no break at all. Bar 28 pokes
        # through it and closes back above, which is a fake break an LTF
        # reader sees and the detector never records.
        #
        # LC-2A must still fire, otherwise the whole setup is invisible and
        # the factor has nothing to answer NO about. What changes is the
        # factor, not the setup.
        bars = list(self.FAKE_BREAK)
        bars[27] = (103.8, 104.0, 102.0, 103.0)
        setup = scan(bars, bullish)
        assert setup is not None
        assert setup["model"] == "LC-2A"
        assert setup["evidence"]["body_closed"] is False

    @bearish_and_bullish
    def test_with_neither_kind_of_break_it_does_not_fire(self, bullish):
        # Nothing ever reaches the swing low, by wick or by close, so
        # there is no fake break of either kind and LC-2A has nothing to
        # build on.
        bars = list(self.FAKE_BREAK)
        bars[27] = (103.8, 104.0, 102.5, 103.0)
        bars[28] = (103.0, 107.0, 102.5, 106.5)
        setup = scan(bars, bullish)
        assert setup is None or setup["model"] != "LC-2A"

    @bearish_and_bullish
    def test_it_is_tried_before_lc1(self, bullish):
        # Precedence: a structural break is the stronger inducement. This
        # is a chosen default, so it is pinned rather than assumed.
        assert MODEL_PRECEDENCE["h1_fractal"].index("LC-2A") < \
            MODEL_PRECEDENCE["h1_fractal"].index("LC-1")
        assert scan(self.FAKE_BREAK, bullish)["model"] == "LC-2A"
        # And LC-1 still wins when no break exists, so the ordering is not
        # simply swallowing everything.
        assert scan(LC1, bullish)["model"] == "LC-1"

    @bearish_and_bullish
    def test_it_survives_on_the_swing_tier(self, bullish):
        # Only LC-1 is banned there. LC-2X and CE remain.
        assert scan(self.FAKE_BREAK, bullish,
                    primary_tier="h1_swing")["model"] == "LC-2A"


class TestLC2B:
    # Two fractal highs at 106.00 and 106.05, close enough to pool into an
    # equals level at 106.025. Bar 29 raids the pool's band and reaches the
    # zone in one candle.
    DOUBLE_TOP = WARMUP + [
        (100.0, 102.00, 99.8, 101.8),   # 20
        (101.8, 104.00, 101.5, 103.8),  # 21
        (103.8, 106.00, 103.5, 105.8),  # 22 top A
        (105.8, 105.00, 103.0, 103.2),  # 23
        (103.2, 103.50, 102.0, 102.2),  # 24
        (102.2, 104.00, 102.0, 103.8),  # 25
        (103.8, 106.05, 103.5, 105.9),  # 26 top B, within tolerance of A
        (105.9, 105.00, 103.0, 103.2),  # 27
        (103.2, 105.50, 103.0, 105.4),  # 28
        (105.4, 111.00, 105.3, 106.5),  # 29 raids the pool and mitigates
        (106.5, 107.00, 104.0, 104.5),  # 30 fill
    ]

    @bearish_and_bullish
    def test_it_fires_on_a_swept_equals_pool(self, bullish):
        setup = scan(self.DOUBLE_TOP, bullish)
        assert setup is not None
        assert setup["model"] == "LC-2B"
        assert setup["trigger_m15"] == 29

    @bearish_and_bullish
    def test_the_pool_level_is_reported(self, bullish):
        # The pooled mean of the two tops, not either top on its own.
        setup = scan(self.DOUBLE_TOP, bullish)
        expected = 2 * AXIS - 106.025 if bullish else 106.025
        assert setup["evidence"]["level"] == pytest.approx(expected)

    @bearish_and_bullish
    def test_a_single_top_does_not_fire(self, bullish):
        # Top B moved far enough away that levels.py keeps them as two
        # separate old points. An old point is touched once by definition,
        # so it can never be the double top LC-2B raids.
        bars = list(self.DOUBLE_TOP)
        bars[26] = (103.8, 104.5, 103.5, 104.2)
        setup = scan(bars, bullish)
        assert setup is None or setup["model"] != "LC-2B"


class TestCE:
    """CE's resolution, driven directly rather than through the scan.

    An end-to-end CE fixture would need a real m15_internal (n=5) break,
    which needs five bars either side of two pivots, and over a fixture
    that long some LC model fires first and takes precedence. So the
    range arithmetic and the three aborts are tested by calling
    _resolve_ce with an explicit range extreme, and the DETECTION of a
    real break is tested separately below. Between them they cover the
    same ground with fixtures small enough to argue about.
    """

    # break at bar 20 with a low of 100.0, so the range is 110.0 down to
    # 100.0 and the first leg is 10.0. Bar 21 extends the low to 98.0,
    # putting fib50 at 104.0. Bar 22 rallies into it.
    EXTREME = 110.0
    BREAK_BAR = 20
    BARS = WARMUP + [
        (100.5, 101.0, 100.0, 100.2),   # 20 the break bar
        (100.0, 101.0, 98.0, 99.0),     # 21 extends the low to 98.0
        (99.0, 105.0, 99.0, 104.5),     # 22 rallies through fib50 at 104.0
    ]

    def resolve(self, bars, bullish, extreme=None, top=ZONE_TOP,
                bottom=ZONE_BOTTOM, valid_through=None):
        if extreme is None:
            extreme = self.EXTREME
        if bullish:
            extreme = 2 * AXIS - extreme
        bundle, h1_ts, zone = build(
            bars, bullish, top=top, bottom=bottom, valid_through=valid_through
        )
        # extreme_bar is -1 and broken is None because the synthetic range
        # extreme is not any candle's high in this fixture. Both only feed
        # evidence, never the arithmetic under test, and TestCEDetection
        # covers the real values.
        return _resolve_ce(
            bundle, h1_ts, zone, self.BREAK_BAR, extreme, -1, None, PIP, None
        )

    @bearish_and_bullish
    def test_it_fills_at_the_midpoint_of_the_range(self, bullish):
        setup = self.resolve(self.BARS, bullish)
        assert setup is not None
        assert setup["model"] == "CE"
        assert setup["fill_m15"] == 22
        expected = 104.0 if not bullish else 2 * AXIS - 104.0
        assert setup["fill_price"] == pytest.approx(expected)

    @bearish_and_bullish
    def test_the_stop_sits_beyond_the_fixed_end_of_the_range(self, bullish):
        # Beyond the broken swing, not beyond the running extreme. Getting
        # this backwards puts the stop inside the range where ordinary
        # retracement clips it.
        setup = self.resolve(self.BARS, bullish)
        buffer_price = 2.0 * PIP
        if bullish:
            assert setup["sl"] == pytest.approx(2 * AXIS - 110.0 - buffer_price)
        else:
            assert setup["sl"] == pytest.approx(110.0 + buffer_price)

    @bearish_and_bullish
    def test_the_level_moves_as_the_range_extends(self, bullish):
        # The dynamic half of decision 15. The low reaches 90.0 instead of
        # 98.0, so fib50 drops from 104.0 to 100.0 and the same rally no
        # longer reaches it.
        #
        # Bar 21's own high is kept below 100.0 deliberately. The bar that
        # extends the range is itself checked against the NEW level, so a
        # bar that reaches down to 90.0 and back up through 100.0 fills in
        # that same candle. Correct, but it would mask what this asserts.
        bars = list(self.BARS)
        bars[21] = (99.0, 99.5, 90.0, 91.0)
        bars[22] = (91.0, 99.0, 91.0, 98.0)
        setup = self.resolve(bars, bullish)
        assert setup is None

        # Reaching the NEW level does fill, which pins that the level
        # moved rather than simply became unreachable.
        bars[22] = (91.0, 100.5, 91.0, 100.2)
        setup = self.resolve(bars, bullish)
        assert setup is not None
        expected = 100.0 if not bullish else 2 * AXIS - 100.0
        assert setup["fill_price"] == pytest.approx(expected)

    @bearish_and_bullish
    def test_a_runaway_leg_aborts(self, bullish):
        # Price never pulls back and just runs. The leg counter would
        # follow this forever, so the multiple is the backstop. First leg
        # is 10.0, so the abort is at 50.0.
        runaway = self.EXTREME - CE_RUNAWAY_MULTIPLE * 10.0 - 5.0
        bars = list(self.BARS)
        bars[21] = (100.0, 101.0, runaway, runaway + 0.5)
        bars[22] = (99.0, 105.0, 99.0, 104.5)
        assert self.resolve(bars, bullish) is None

    @bearish_and_bullish
    def test_price_leaving_the_zone_aborts(self, bullish):
        # The limit may still be pending long after the break, so the zone
        # can die under it.
        bars = list(self.BARS)
        bars[21] = (100.0, 116.0, 98.0, 99.0)
        assert self.resolve(bars, bullish) is None

    @bearish_and_bullish
    def test_a_zone_h1_has_retired_aborts(self, bullish):
        # The H1 lifecycle outranks CE's pending order too, not just the
        # formation scan.
        assert self.resolve(self.BARS, bullish, valid_through=4) is None
        assert self.resolve(self.BARS, bullish, valid_through=8) is not None

    @bearish_and_bullish
    def test_a_range_too_narrow_for_a_usable_stop_does_not_fill(self, bullish):
        # MIN_R_PIPS still applies. The range is 0.02 wide, so fib50 sits
        # a hair from the stop.
        bars = list(self.BARS)
        bars[20] = (109.99, 110.0, 109.98, 109.99)
        bars[21] = (109.99, 110.0, 109.98, 109.99)
        bars[22] = (109.99, 110.0, 109.98, 109.99)
        assert self.resolve(bars, bullish) is None


class TestCEDetection:
    """_internal_break_at against a real m15_internal (n=5) break."""

    # A long, clean V: down into a trough, back up to a peak, then a
    # decisive close below the trough. n=5 needs five bars either side of
    # each pivot, hence the length.
    HIGHS = ([100.5] * 20
             + [104.0, 103.0, 102.0, 101.0, 100.0, 99.0, 98.0]
             + [99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
             + [104.0, 103.0, 102.0, 101.0, 100.0, 96.0, 95.0])

    def bars(self):
        out = []
        for i, high in enumerate(self.HIGHS):
            low = high - 1.0
            out.append((high - 0.5, high, low, low + 0.2))
        return out

    def test_a_real_break_is_detected_with_the_far_swing_as_the_range(self):
        bundle, h1_ts, zone = build(self.bars(), False)
        breaks = []
        for j in range(len(self.HIGHS)):
            found = _internal_break_at(bundle, zone, j)
            if found is not None:
                breaks.append((j,) + found)
        assert breaks, "expected at least one bearish internal break"
        for bar, extreme, extreme_bar, broken in breaks:
            # The break cleared the near-side swing, so it sits below the
            # far-side one for a bearish break.
            assert broken is None or broken < extreme
            # The range extreme is a swing HIGH for a bearish break, so it
            # must sit above the price that broke.
            assert extreme > bundle.low[bar]
            # And it must be locatable, because CE's has_imbalance and
            # has_inducements describe the leg from it to the break.
            assert extreme_bar >= 0
            assert bundle.high[extreme_bar] == extreme
            assert extreme_bar < bar

    def test_a_bearish_break_is_not_reported_for_a_bullish_zone(self):
        # The direction switch. The same bars must not produce a break in
        # the opposite direction on the same bar.
        bundle, h1_ts, bear = build(self.bars(), False)
        bull = Zone(top=bear.top, bottom=bear.bottom, bullish=True,
                    primary_tier="h1_fractal", valid_through=bear.valid_through)
        both = [
            j for j in range(len(self.HIGHS))
            if _internal_break_at(bundle, bear, j) is not None
            and _internal_break_at(bundle, bull, j) is not None
        ]
        assert both == []


class TestZoneLifecycle:
    @bearish_and_bullish
    def test_a_zone_h1_has_retired_yields_nothing(self, bullish):
        # The H1 lifecycle outranks the M15 geometry, always. The trigger
        # is at M15 bar 23, which is the 07:45 hour, i.e. H1 bar 5.
        assert scan(LC1, bullish, valid_through=4) is None
        assert scan(LC1, bullish, valid_through=5) is not None

    @bearish_and_bullish
    def test_no_bundle_yields_nothing(self, bullish):
        _, h1_ts, zone = build(LC1, bullish)
        assert scan_for_entry(None, h1_ts, zone, 5, PIP) is None

    @bearish_and_bullish
    def test_a_mitigation_bar_off_the_end_yields_nothing(self, bullish):
        bundle, h1_ts, zone = build(LC1, bullish)
        assert scan_for_entry(bundle, h1_ts, zone, 9999, PIP) is None


class TestEqAndTheN2Window:
    @bearish_and_bullish
    def test_a_trigger_that_breaches_eq_still_forms(self, bullish):
        # The user's case. eq is 112.5; the trigger wicks to 113.0, past
        # it. The breaching candle IS the reaction, so it is still
        # tradeable, exactly as order_blocks._kill treats
        # invalidated_index.
        bars = list(LC1)
        bars[23] = (108.5, 113.0, 108.4, 109.0)
        setup = scan(bars, bullish)
        assert setup is not None
        assert setup["trigger_m15"] == 23
        assert setup["evidence"]["eq_breach_m15"] == 23

    @bearish_and_bullish
    def test_a_trigger_one_candle_after_an_eq_breach_is_rejected(self, bullish):
        # Bar 22 breaches eq carrying no setup, so bar 23's trigger is
        # dead. This is where M15 is deliberately STRICTER than H1: both
        # bars sit in one H1 candle, whose own off-by-one would allow it.
        bars = list(LC1)
        bars[22] = (108.2, 113.0, 108.0, 108.5)
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_formation_stops_after_the_breach_rather_than_scanning_on(self, bullish):
        # A perfectly good LC-1 arriving well after the breach is not
        # picked up. Same assertion as above, further out, to pin that the
        # scan RETURNS rather than merely skipping one candle.
        bars = WARMUP + [(108.0, 113.0, 107.9, 108.2)] + LC1[20:]
        assert scan(bars, bullish) is None


class TestOrderAndFill:
    @bearish_and_bullish
    def test_prices_come_from_the_trigger_candle_not_the_zone(self, bullish):
        # Decision 6. The stop is 2 pips beyond the trigger's own extreme,
        # which is what collapses R from zone width to candle width.
        setup = scan(LC1, bullish)
        buffer_price = 2.0 * PIP
        if bullish:
            trigger = mirror_bar(LC1[23])
            assert setup["order_price"] == pytest.approx(trigger[1] + buffer_price)
            assert setup["sl"] == pytest.approx(trigger[2] - buffer_price)
        else:
            assert setup["order_price"] == pytest.approx(108.4 - buffer_price)
            assert setup["sl"] == pytest.approx(111.0 + buffer_price)

    @bearish_and_bullish
    def test_r_distance_is_the_candle_range_plus_both_buffers(self, bullish):
        setup = scan(LC1, bullish)
        assert setup["r_distance"] == pytest.approx(111.0 - 108.4 + 4.0 * PIP)

    @bearish_and_bullish
    def test_the_fill_is_the_first_candle_to_reach_the_order(self, bullish):
        setup = scan(LC1, bullish)
        assert setup["fill_m15"] == 24
        assert setup["fill_price"] == pytest.approx(setup["order_price"])

    @bearish_and_bullish
    def test_an_order_never_reached_yields_nothing(self, bullish):
        # The trigger forms but price walks away without tagging it, and
        # the zone's far edge is never crossed either.
        bars = list(LC1)
        bars[24] = (109.0, 109.5, 108.9, 109.4)
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_the_order_expires_rather_than_resting_forever(self, bullish):
        # The bound that makes the re-host below possible at all. Bar 24
        # does not fill and does not re-host, so the order dies. Bar 25
        # would have filled it, and must not.
        bars = list(LC1)
        bars[24] = (109.0, 109.5, 108.9, 109.4)
        bars.append((109.4, 109.5, 100.0, 100.5))
        assert scan(bars, bullish) is None


class TestReHost:
    # Bar 24 closes beyond the trigger's high without filling the order,
    # so the order moves to bar 24's low. Bar 25 then tags it there.
    RE_HOST = LC1[:24] + [
        (109.0, 112.0, 109.0, 111.5),
        (111.0, 111.2, 108.5, 108.8),
    ]

    @bearish_and_bullish
    def test_the_order_moves_to_candle_b(self, bullish):
        setup = scan(self.RE_HOST, bullish)
        assert setup is not None
        assert setup["trigger_m15"] == 23
        assert setup["host_m15"] == 24
        assert setup["fill_m15"] == 25

    @bearish_and_bullish
    def test_the_stop_moves_with_the_host(self, bullish):
        # Not a restatement: this is the consequence that matters, because
        # R is measured from the host candle and a stale stop would
        # misreport every R multiple downstream.
        setup = scan(self.RE_HOST, bullish)
        buffer_price = 2.0 * PIP
        if bullish:
            host = mirror_bar(self.RE_HOST[24])
            assert setup["sl"] == pytest.approx(host[2] - buffer_price)
        else:
            assert setup["sl"] == pytest.approx(112.0 + buffer_price)

    @bearish_and_bullish
    def test_a_candle_that_does_not_close_beyond_does_not_re_host(self, bullish):
        # Bar 24 makes a higher HIGH but closes back below the trigger's
        # high, so the order stays where it is and then expires.
        #
        # Bar 24 also mitigates the zone, and any candle with a higher
        # high must, so it cannot be built otherwise. That it does not
        # produce a second trigger off the same sweep is the consumption
        # rule in _try_lc1 doing its job.
        bars = LC1[:24] + [
            (109.0, 112.0, 109.0, 110.0),
            (110.0, 110.2, 108.5, 108.8),
        ]
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_one_sweep_produces_one_trigger_only(self, bullish):
        # Stated directly rather than left implicit in the test above.
        # Without consumption, bar 23's sweep triggers at 23 and again at
        # 24, so the order lives from 23 to 25 and the N=2 bound is a
        # fiction.
        bars = LC1[:24] + [
            (109.0, 112.0, 109.0, 110.0),
            (110.0, 110.2, 100.0, 100.5),
        ]
        setup = scan(bars, bullish)
        assert setup is None or setup["trigger_m15"] == 23

    @bearish_and_bullish
    def test_a_candle_past_the_far_edge_does_not_re_host(self, bullish):
        # Bar 24 closes beyond, which would normally re-host, but its wick
        # cleared the zone's far edge (115.0). Price all the way through
        # the zone has nothing left to trade against.
        bars = LC1[:24] + [
            (109.0, 115.5, 109.0, 111.5),
            (111.0, 111.2, 108.5, 108.8),
        ]
        assert scan(bars, bullish) is None

    @bearish_and_bullish
    def test_candle_c_cancels(self, bullish):
        # One re-host only. Bar 24 re-hosts, bar 25 neither fills nor gets
        # to re-host again, so bar 26 cannot rescue it.
        bars = LC1[:24] + [
            (109.0, 112.0, 109.0, 111.5),
            (111.5, 112.5, 110.0, 110.5),
            (110.5, 110.6, 100.0, 100.5),
        ]
        assert scan(bars, bullish) is None


class TestPendingOrders:
    """pending_order_for: what should be RESTING now, not what filled.

    The live counterpart to scan_for_entry, and the pair has to disagree in
    a specific way: scan_for_entry is silent until the fill has happened,
    pending_order_for speaks from the trigger candle onward and goes silent
    when the order expires. If they agreed everywhere, one of them is
    answering the wrong question.
    """

    def pending(self, bars, bullish, as_of, **kwargs):
        bundle, h1_ts, zone = build(bars, bullish, **kwargs)
        return pending_order_for(bundle, h1_ts, zone, 5, PIP, as_of)

    @bearish_and_bullish
    def test_it_reports_the_order_on_the_trigger_candle(self, bullish):
        # scan_for_entry says nothing here: the fill is at bar 24 and this
        # is bar 23. The live bot has to place the order anyway.
        order = self.pending(LC1, bullish, as_of=23)
        assert order is not None
        assert order["model"] == "LC-1"
        assert order["order_kind"] == "stop"
        assert order["trigger_m15"] == 23
        assert order["host_m15"] == 23
        assert order["expires_m15"] == 24

    @bearish_and_bullish
    def test_the_price_matches_what_the_backtest_would_have_used(self, bullish):
        # The property that makes live and backtest comparable at all.
        order = self.pending(LC1, bullish, as_of=23)
        filled = scan(LC1, bullish)
        assert order["order_price"] == pytest.approx(filled["order_price"])
        assert order["sl"] == pytest.approx(filled["sl"])
        assert order["r_distance"] == pytest.approx(filled["r_distance"])

    @bearish_and_bullish
    def test_nothing_before_the_trigger(self, bullish):
        assert self.pending(LC1, bullish, as_of=22) is None

    @bearish_and_bullish
    def test_it_expires_after_the_host_window(self, bullish):
        # Still live on the bar after the host, gone on the one after that.
        assert self.pending(LC1, bullish, as_of=24) is not None
        bars = LC1 + [(108.2, 108.5, 107.5, 108.0)]
        assert self.pending(bars, bullish, as_of=25) is None

    @bearish_and_bullish
    def test_it_reports_the_re_hosted_price_on_the_re_host_bar(self, bullish):
        # The ordering trap: advancing the host has to happen BEFORE
        # liveness is judged, or the bar that re-hosts reports the stale
        # price and the live bot rests the order in the wrong place.
        bars = TestReHost.RE_HOST
        at_a = self.pending(bars, bullish, as_of=23)
        at_b = self.pending(bars, bullish, as_of=24)
        assert at_a["host_m15"] == 23
        assert at_b["host_m15"] == 24
        assert at_b["order_price"] != pytest.approx(at_a["order_price"])

    @bearish_and_bullish
    def test_no_bundle_or_negative_as_of_is_silent(self, bullish):
        bundle, h1_ts, zone = build(LC1, bullish)
        assert pending_order_for(None, h1_ts, zone, 5, PIP, 23) is None
        assert pending_order_for(bundle, h1_ts, zone, 5, PIP, -1) is None
        assert pending_order_for(bundle, h1_ts, zone, 5, PIP, None) is None

    @bearish_and_bullish
    def test_a_swing_tier_zone_still_refuses_lc1(self, bullish):
        assert self.pending(LC1, bullish, as_of=23,
                            primary_tier="h1_swing") is None
