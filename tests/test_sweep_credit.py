"""The mitigation-leg credit chain: when a sweep still counts at entry.

Every fixture uses one geometry, stated once here so the tests read as
narrative rather than as OHLC:

    bullish (demand) order block   1.0800 - 1.0820
    previous day low (the level)   1.0830, ABOVE the zone

Price descends, wicks below 1.0830 running the sell stops under it, and keeps
falling into the zone. The credit for that sweep survives to the entry bar
only while no candle CLOSES below 1.0830 and no candle CLOSES more than
3 x ATR(14) above the sweep candle's HIGH.

ATR is pinned by construction: every filler candle spans exactly RANGE, so
ATR settles at RANGE and "3 x ATR" is a number each test can state outright.
"""

import numpy as np
import pandas as pd
import pytest

from smc.liquidity import sweep_credit
from smc.liquidity.levels import EQUALS, OLD_POINT
from smc.liquidity.low_resistance import LRLQ

HIGH = "high"
LOW = "low"

OB_TOP = 1.0820
OB_BOTTOM = 1.0800
LEVEL = 1.0830

# Every filler candle spans this, so ATR converges here immediately.
RANGE = 0.0010
SPEND = sweep_credit.CREDIT_SPENT_ATR_MULTIPLE * RANGE

# ATR reports nothing for the first ATR_PERIOD - 1 candles, so fixtures pad
# past that before anything interesting happens.
WARMUP = 20

START = pd.Timestamp("2024-01-01", tz="UTC")


def candle(high, low, close):
    return {"high": high, "low": low, "close": close}


def filler(close=1.0840):
    """A quiet candle well above the level, spanning exactly RANGE."""
    return candle(close + RANGE / 2.0, close - RANGE / 2.0, close)


def frame(bars):
    """An H1 frame from a list of candle dicts, with dates and opens filled."""
    rows = []
    for i, bar in enumerate(bars):
        rows.append({
            "date": START + pd.Timedelta(hours=i),
            "open": bar["close"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
        })
    return pd.DataFrame(rows)


def sweep_candle(close=1.0834, low=1.0826, high=1.0836):
    """Wicks below the level and closes back above it."""
    return candle(high, low, close)


def event(swept_index, kind=OLD_POINT, side=LOW, level=LEVEL, expires_index=10_000):
    """One sweep event, in the shape sweeps.py's *_sweep_events return."""
    return {
        "kind": kind,
        "side": side,
        "level": level,
        "swept_index": swept_index,
        "expires_index": expires_index,
    }


def events_frame(rows):
    return pd.DataFrame(rows, columns=[
        "kind", "side", "level", "swept_index", "expires_index",
    ])


def windows(bars, rows):
    return sweep_credit.compute_credit_windows(events_frame(rows), frame(bars))


def credit_through(bars, rows):
    """The single credit_through in a one-event fixture, or None if dropped."""
    result = windows(bars, rows)
    if len(result) == 0:
        return None
    return int(result["credit_through"].iloc[0])


def alive_at(bars, rows, bar_index):
    result = windows(bars, rows)
    if len(result) == 0:
        return False
    row = result.iloc[0]
    return int(row["swept_index"]) <= bar_index <= int(row["credit_through"])


class TestChainSurvives:
    """The sequences the user described as still deserving credit."""

    def test_entry_candle_wicks_the_level_and_closes_above_it(self):
        """S sweeps, the next candle taps the OB on a wick and closes back
        above the level. This is the whole pattern the gate exists to find.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        # Entry candle: deep wick into the zone, close back above the level.
        bars.append(candle(1.0836, 1.0812, 1.0833))
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)

    def test_inside_bar_then_repeated_re_sweeps(self):
        """S sweeps, S+1 is a doji, S+2 and S+3 both re-wick below the level
        and close above it. A re-sweep is price coming back for more, not a
        failure, so the credit is untouched.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.append(candle(1.0835, 1.0833, 1.0834))       # doji, inside
        bars.append(candle(1.0836, 1.0824, 1.0833))       # re-wick
        bars.append(candle(1.0835, 1.0815, 1.0832))       # re-wick, taps OB
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)

    def test_sweep_candle_can_itself_be_the_entry_candle(self):
        """A candle that sweeps the level and taps the zone in one move
        cannot kill its own credit, because the scan starts after S.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(candle(1.0836, 1.0812, 1.0833))

        assert alive_at(bars, [event(s)], s)

    def test_credit_persists_across_many_quiet_candles(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.extend([candle(1.0835, 1.0831, 1.0833)] * 30)
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)


class TestCloseThrough:
    """Rule (a). A close below the level ends it, permanently."""

    def test_close_below_the_level_kills_the_credit(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.append(candle(1.0834, 1.0820, 1.0825))       # closes below 1.0830
        killer = len(bars) - 1
        bars.extend([filler()] * 3)

        assert credit_through(bars, [event(s)]) == killer - 1

    def test_a_later_re_wick_does_not_resurrect_it(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.append(candle(1.0834, 1.0820, 1.0825))       # dead here
        bars.append(candle(1.0836, 1.0824, 1.0833))       # re-wicks and recovers
        entry = len(bars) - 1

        assert not alive_at(bars, [event(s)], entry)

    def test_the_entry_candle_itself_is_tested(self):
        """The deferred-entry case in miniature: the candle we would enter on
        closes below the level, so there is no credit at entry.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.append(candle(1.0836, 1.0812, 1.0818))       # taps OB, closes under
        entry = len(bars) - 1

        assert not alive_at(bars, [event(s)], entry)

    def test_a_close_exactly_at_the_level_survives(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.append(candle(1.0836, 1.0812, LEVEL))
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)


class TestLiquiditySpent:
    """Rule (b). A big enough push means the pool already paid out."""

    def test_push_beyond_three_atr_above_the_sweep_high_kills_it(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        sweep = sweep_candle()
        bars.append(sweep)
        spent = sweep["high"] + SPEND + RANGE
        bars.append(candle(spent + RANGE, spent - RANGE, spent))
        killer = len(bars) - 1

        assert credit_through(bars, [event(s)]) == killer - 1

    def test_the_push_can_land_many_candles_later(self):
        """S+1 and S+2 are dojis and the real move is S+3. There is no bar
        count in the rule, so the kill lands where the move does.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        sweep = sweep_candle()
        bars.append(sweep)
        bars.append(candle(1.0835, 1.0833, 1.0834))
        bars.append(candle(1.0835, 1.0833, 1.0834))
        spent = sweep["high"] + SPEND + RANGE
        bars.append(candle(spent + RANGE, spent - RANGE, spent))
        killer = len(bars) - 1

        assert credit_through(bars, [event(s)]) == killer - 1

    def test_returning_after_the_push_does_not_resurrect_it(self):
        """The user's own reasoning: price wicked the level, pushed hard, and
        came back. The liquidity is gone and the return reads as bearish
        order flow rather than a second helping.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        sweep = sweep_candle()
        bars.append(sweep)
        spent = sweep["high"] + SPEND + RANGE
        bars.append(candle(spent + RANGE, spent - RANGE, spent))
        bars.append(candle(1.0836, 1.0824, 1.0833))       # re-wicks the level
        entry = len(bars) - 1

        assert not alive_at(bars, [event(s)], entry)

    def test_a_close_exactly_at_three_atr_survives(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        sweep = sweep_candle()
        bars.append(sweep)
        edge = sweep["high"] + SPEND
        bars.append(candle(edge + RANGE, edge - RANGE, edge))
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)

    def test_the_anchor_is_the_sweep_high_not_its_low(self):
        """Measuring from S's low would make the band 10 pips of wick
        narrower and kill this chain. The anchor is the far extreme.
        """
        bars = [filler()] * WARMUP
        s = len(bars)
        sweep = candle(1.0836, 1.0810, 1.0834)           # a tall sweep candle
        bars.append(sweep)
        # Above low + 3 ATR, but below high + 3 ATR.
        between = sweep["low"] + SPEND + RANGE
        assert between < sweep["high"] + SPEND
        bars.append(candle(between + RANGE, between - RANGE, between))
        entry = len(bars) - 1

        assert alive_at(bars, [event(s)], entry)


class TestAgeCap:
    """Rule 6. Credit dies when the level itself dies."""

    def test_expiry_caps_a_chain_nothing_else_killed(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.extend([candle(1.0835, 1.0831, 1.0833)] * 40)
        expiry = s + 10

        assert credit_through(bars, [event(s, expires_index=expiry)]) == expiry

    def test_an_event_swept_after_its_window_closed_is_dropped(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())
        bars.extend([filler()] * 3)

        assert credit_through(bars, [event(s, expires_index=s - 1)]) is None

    def test_credit_never_runs_past_the_data(self):
        bars = [filler()] * WARMUP
        s = len(bars)
        bars.append(sweep_candle())

        assert credit_through(bars, [event(s)]) == len(bars) - 1


class TestAtrWarmup:
    def test_a_sweep_during_warmup_is_dropped(self):
        """No fabricated ATR fallback: without a real volatility number the
        spend rule would fire off something meaningless.
        """
        bars = [filler()] * 30
        assert credit_through(bars, [event(2)]) is None

    def test_a_sweep_after_warmup_is_kept(self):
        bars = [filler()] * 30
        assert credit_through(bars, [event(WARMUP)]) is not None


class TestCreditExtremes:
    """Records collapse to per-bar arrays, and the direction is the trap."""

    def test_low_side_keeps_the_maximum(self):
        """Two live low-side levels above the zone. The gate asks whether any
        sits above ob_top, so the HIGHEST is the sufficient one.
        """
        rows = pd.DataFrame([
            {"kind": OLD_POINT, "side": LOW, "level": 1.0830,
             "swept_index": 5, "expires_index": 99, "credit_through": 20},
            {"kind": OLD_POINT, "side": LOW, "level": 1.0860,
             "swept_index": 6, "expires_index": 99, "credit_through": 20},
        ])
        arrays = sweep_credit.credit_extremes(rows, 30)

        assert arrays[(OLD_POINT, LOW)][10] == pytest.approx(1.0860)

    def test_high_side_keeps_the_minimum(self):
        rows = pd.DataFrame([
            {"kind": OLD_POINT, "side": HIGH, "level": 1.0780,
             "swept_index": 5, "expires_index": 99, "credit_through": 20},
            {"kind": OLD_POINT, "side": HIGH, "level": 1.0750,
             "swept_index": 6, "expires_index": 99, "credit_through": 20},
        ])
        arrays = sweep_credit.credit_extremes(rows, 30)

        assert arrays[(OLD_POINT, HIGH)][10] == pytest.approx(1.0750)

    def test_nan_before_the_sweep_and_after_expiry(self):
        rows = pd.DataFrame([
            {"kind": OLD_POINT, "side": LOW, "level": 1.0830,
             "swept_index": 10, "expires_index": 99, "credit_through": 15},
        ])
        arrays = sweep_credit.credit_extremes(rows, 30)
        series = arrays[(OLD_POINT, LOW)]

        assert np.isnan(series[9])
        assert series[10] == pytest.approx(1.0830)
        assert series[15] == pytest.approx(1.0830)
        assert np.isnan(series[16])

    def test_the_better_level_expiring_falls_back_to_the_other(self):
        rows = pd.DataFrame([
            {"kind": OLD_POINT, "side": LOW, "level": 1.0860,
             "swept_index": 5, "expires_index": 99, "credit_through": 10},
            {"kind": OLD_POINT, "side": LOW, "level": 1.0830,
             "swept_index": 5, "expires_index": 99, "credit_through": 20},
        ])
        arrays = sweep_credit.credit_extremes(rows, 30)
        series = arrays[(OLD_POINT, LOW)]

        assert series[10] == pytest.approx(1.0860)
        assert series[11] == pytest.approx(1.0830)

    def test_every_eligible_key_is_present_even_with_no_events(self):
        arrays = sweep_credit.credit_extremes(
            pd.DataFrame(columns=[
                "kind", "side", "level", "swept_index",
                "expires_index", "credit_through",
            ]),
            30,
        )

        for kind in sweep_credit.ELIGIBLE_KINDS:
            for side in (HIGH, LOW):
                assert (kind, side) in arrays
                assert len(arrays[(kind, side)]) == 30
                assert np.isnan(arrays[(kind, side)]).all()

    def test_kinds_stay_separate(self):
        rows = pd.DataFrame([
            {"kind": OLD_POINT, "side": LOW, "level": 1.0830,
             "swept_index": 5, "expires_index": 99, "credit_through": 20},
        ])
        arrays = sweep_credit.credit_extremes(rows, 30)

        assert arrays[(OLD_POINT, LOW)][10] == pytest.approx(1.0830)
        assert np.isnan(arrays[(EQUALS, LOW)][10])
        assert np.isnan(arrays[(LRLQ, LOW)][10])


class TestEligibleKinds:
    def test_fvg_is_never_eligible(self):
        """An order block's own displacement leg leaves an imbalance directly
        in front of the zone, so price cannot mitigate the block without
        sweeping it. Crediting that would score caused_imbalance twice.
        """
        assert "fvg" not in sweep_credit.ELIGIBLE_KINDS

    @pytest.mark.parametrize("kind", ["swing", "internal", "fractal"])
    def test_structural_kinds_are_never_eligible(self, kind):
        assert kind not in sweep_credit.ELIGIBLE_KINDS

    def test_the_external_and_time_kinds_are_all_present(self):
        assert set(sweep_credit.ELIGIBLE_KINDS) == {
            OLD_POINT, EQUALS, LRLQ,
            "asian", "london", "ny", "previous_day", "previous_week",
        }
