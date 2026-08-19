"""Low Resistance Liquidity: run detection, the level's anchor, and the
span and count thresholds.

Fixtures are built by `waves`, which lays out alternating peak and trough
candles so that every trough is a Williams Fractal low at n=1 and every peak
a fractal high. Peaks are all at the SAME price on purpose, so they can never
form a stepping run of their own: a sell-side fixture then produces exactly
one buy-side answer, none, and the assertions stay about the side under test.

The two sides are mirror images, so most rules are asserted once on the sell
side (higher lows) and once, as a spot check, on the buy side.
"""

import pandas as pd

from smc.liquidity.low_resistance import (
    DEFAULT_MAX_SPAN,
    DEFAULT_MIN_PIVOTS,
    compute_low_resistance_liquidity,
)

PEAK_HIGH = 200.0
PEAK_LOW = 150.0
TROUGH_HIGH = 190.0

# Mirror image, used by the buy-side fixtures.
VALLEY_LOW = 100.0
VALLEY_HIGH = 150.0
CREST_LOW = 110.0

PIVOT_N = 1


def _candle(ts, high, low):
    return {
        "date": ts,
        "open": (high + low) / 2.0,
        "high": high,
        "low": low,
        "close": (high + low) / 2.0,
    }


def waves(troughs, gap=1):
    """Peak/trough candles whose trough lows are `troughs`, in order.

    `gap` peak candles sit between consecutive troughs, which is what
    stretches a run's span without changing its shape. gap=1 gives the
    tightest legal run.
    """
    lows_and_highs = [(PEAK_HIGH, PEAK_LOW)]
    for trough in troughs:
        lows_and_highs.append((TROUGH_HIGH, trough))
        lows_and_highs.extend([(PEAK_HIGH, PEAK_LOW)] * gap)

    start = pd.Timestamp("2024-01-01", tz="UTC")
    return pd.DataFrame([
        _candle(start + pd.Timedelta(hours=i), high, low)
        for i, (high, low) in enumerate(lows_and_highs)
    ])


def crests(peaks, gap=1):
    """The mirror of `waves`: crest highs are `peaks`, in order."""
    lows_and_highs = [(VALLEY_HIGH, VALLEY_LOW)]
    for peak in peaks:
        lows_and_highs.append((peak, CREST_LOW))
        lows_and_highs.extend([(VALLEY_HIGH, VALLEY_LOW)] * gap)

    start = pd.Timestamp("2024-01-01", tz="UTC")
    return pd.DataFrame([
        _candle(start + pd.Timedelta(hours=i), high, low)
        for i, (high, low) in enumerate(lows_and_highs)
    ])


def append(df, bars):
    """Extra (high, low) candles on the end of a fixture."""
    start = pd.Timestamp(df["date"].iloc[-1])
    extra = pd.DataFrame([
        _candle(start + pd.Timedelta(hours=i + 1), high, low)
        for i, (high, low) in enumerate(bars)
    ])
    return pd.concat([df, extra], ignore_index=True)


def levels_for(df, **kwargs):
    return compute_low_resistance_liquidity(df, pivot_n=PIVOT_N, **kwargs)


def sell_side(levels):
    return levels[levels["side"] == "low"].reset_index(drop=True)


def buy_side(levels):
    return levels[levels["side"] == "high"].reset_index(drop=True)


class TestRunDetection:
    def test_three_higher_lows_are_a_run(self):
        levels = sell_side(levels_for(waves([100.0, 110.0, 120.0])))
        assert len(levels) == 1
        assert levels.iloc[0]["pivot_count"] == 3

    def test_two_higher_lows_are_not(self):
        """Two steps is an ordinary pullback. Three is a grind, and that is
        the whole distinction the factor is built on.
        """
        assert len(sell_side(levels_for(waves([100.0, 110.0])))) == 0

    def test_lows_that_do_not_step_are_not_a_run(self):
        assert len(sell_side(levels_for(waves([100.0, 90.0, 110.0])))) == 0

    def test_three_lower_highs_are_a_buy_side_run(self):
        levels = buy_side(levels_for(crests([200.0, 190.0, 180.0])))
        assert len(levels) == 1
        assert levels.iloc[0]["pivot_count"] == 3

    def test_a_sell_side_fixture_produces_no_buy_side_answer(self):
        """Guards the fixture itself: the peaks are all one price, so any
        buy-side row here would mean the stepping test is not strict.
        """
        assert len(buy_side(levels_for(waves([100.0, 110.0, 120.0])))) == 0


class TestTheAnchor:
    def test_the_level_is_the_first_low_of_the_run(self):
        """The far end of the stack. Price running the liquidity travels
        through every step to reach it, so that is what to target.
        """
        assert sell_side(levels_for(waves([100.0, 110.0, 120.0]))).iloc[0]["level"] == 100.0

    def test_the_buy_side_level_is_the_first_high_of_the_run(self):
        assert buy_side(levels_for(crests([200.0, 190.0, 180.0]))).iloc[0]["level"] == 200.0

    def test_a_fourth_step_extends_the_run_rather_than_opening_a_second(self):
        """Overlapping runs would report one stack of stops twice and let a
        single pullback outvote everything else in the gate.
        """
        levels = sell_side(levels_for(waves([100.0, 110.0, 120.0, 130.0])))
        assert len(levels) == 1
        assert levels.iloc[0]["pivot_count"] == 4
        assert levels.iloc[0]["level"] == 100.0


class TestSpan:
    def test_a_run_inside_the_span_registers(self):
        levels = sell_side(levels_for(waves([100.0, 110.0, 120.0], gap=3)))
        assert len(levels) == 1

    def test_a_run_stretched_past_the_span_does_not(self):
        """Same three steps, spread far enough apart that they are a slow
        drift rather than a compressed pullback.
        """
        gap = DEFAULT_MAX_SPAN
        assert len(sell_side(levels_for(waves([100.0, 110.0, 120.0], gap=gap)))) == 0

    def test_the_span_is_measured_from_the_runs_first_pivot(self):
        levels = sell_side(levels_for(waves([100.0, 110.0, 120.0], gap=3)))
        level = levels.iloc[0]
        span = level["last_pivot_index"] - level["first_pivot_index"]
        assert span <= DEFAULT_MAX_SPAN


class TestLifecycle:
    def test_price_below_the_level_sweeps_it(self):
        df = append(waves([100.0, 110.0, 120.0]), [(PEAK_HIGH, 90.0)])
        level = sell_side(levels_for(df)).iloc[0]
        assert level["swept"]
        assert level["ended_by"] == "swept"
        assert level["swept_index"] == len(df) - 1

    def test_price_stopping_short_of_the_level_does_not(self):
        df = append(waves([100.0, 110.0, 120.0]), [(PEAK_HIGH, 100.0)])
        assert not sell_side(levels_for(df)).iloc[0]["swept"]

    def test_an_untouched_level_expires_after_the_lookback(self):
        lookback = 10
        df = append(waves([100.0, 110.0, 120.0]),
                    [(PEAK_HIGH, PEAK_LOW)] * (lookback + 3))
        level = sell_side(levels_for(df, lookback=lookback)).iloc[0]
        assert level["ended_by"] == "expired"
        assert level["valid_through_index"] - level["visible_from_index"] == lookback

    def test_a_level_still_live_at_the_end_is_not_expired(self):
        level = sell_side(levels_for(waves([100.0, 110.0, 120.0]))).iloc[0]
        assert level["ended_by"] == "data_end"
        assert not level["swept"]


class TestNoLookahead:
    def test_a_run_is_not_visible_until_its_last_pivot_confirms(self):
        """A run of three is not known to be a run until the third pivot
        confirms, which is n candles after that pivot printed.
        """
        level = sell_side(levels_for(waves([100.0, 110.0, 120.0]))).iloc[0]
        assert level["visible_from_index"] == level["last_pivot_index"] + PIVOT_N

    def test_the_sweep_scan_starts_after_the_run_is_visible(self):
        """A run of higher lows cannot contain a dip below its own anchor,
        so nothing inside it can sweep it. This asserts the scan does not
        start early enough to find one anyway.
        """
        level = sell_side(levels_for(waves([100.0, 110.0, 120.0]))).iloc[0]
        assert not level["swept"]


class TestThresholdsAreConfigurable:
    def test_min_pivots_is_honoured(self):
        assert DEFAULT_MIN_PIVOTS == 3
        levels = sell_side(levels_for(waves([100.0, 110.0]), min_pivots=2))
        assert len(levels) == 1
        assert levels.iloc[0]["pivot_count"] == 2
