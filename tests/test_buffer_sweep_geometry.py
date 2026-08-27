"""What widening the stop-loss buffer actually does to a setup.

The study sweeps this buffer, so the shape of its effect has to be pinned
down rather than assumed, and the shape is NOT uniform across the four
entry models:

  LC-1, LC-2A, LC-2B   entry_models._order_prices puts the buffer on BOTH
                       sides -- the resting stop order is pushed out by
                       one buffer and the stop by another -- so the R
                       distance grows by TWICE the buffer.
  CE                   places a limit at the range midpoint and only the
                       stop carries the buffer, so R grows by ONCE it.

That asymmetry means a buffer sweep is not a clean "wider stops" sweep.
It reweights the models against each other, and the study's results have
to be read with that in mind. Measured on real GBP_JPY 2015-2022, the
model mix does shift: LC-1 falls from 104 fills to 38 across the sweep
while CE rises from 79 to 98.

The second effect, and the one that costs money, is that pushing a
resting order further from price gets it filled less often. Same data:
356 candidates at 2 pips, 293 at 4, 222 at 8.
"""

import pytest

from backtest import entry_params
from backtest.entry_models import _resolve_ce
from test_entry_models import (
    AXIS,
    LC1,
    PIP,
    TestCE as _ce_fixture,
    bearish_and_bullish,
    build,
    scan,
)


def r_pips(setup):
    """The setup's stop distance in pips, however it names its entry.

    The LC models rest a stop ORDER and call it order_price; CE rests a
    limit and calls it fill_price. Reading whichever is present keeps this
    module about the buffer rather than about the naming.
    """
    entry = setup.get("order_price", setup.get("fill_price"))
    return abs(entry - setup["sl"]) / PIP


def resolve_ce(bullish):
    """One CE setup, driven directly.

    CE cannot be reached through scan() in these fixtures: a real
    m15_internal (n=5) break needs five bars either side of two pivots,
    and over a fixture that long an LC model fires first and takes
    precedence. test_entry_models.TestCE makes the same call for the same
    reason.
    """
    extreme = _ce_fixture.EXTREME
    if bullish:
        extreme = 2 * AXIS - extreme
    bundle, h1_ts, zone = build(_ce_fixture.BARS, bullish)
    return _resolve_ce(
        bundle, h1_ts, zone, _ce_fixture.BREAK_BAR, extreme, -1, None, PIP, None
    )


class TestBufferScaling:
    @bearish_and_bullish
    def test_lc_models_widen_by_twice_the_buffer(self, bullish):
        """The stop order and the stop move in opposite directions, so a
        one-pip increase costs two pips of R.
        """
        with entry_params.override(sl_buffer_pips=2.0):
            base = scan(LC1, bullish)
        with entry_params.override(sl_buffer_pips=5.0):
            wider = scan(LC1, bullish)

        assert base["model"].startswith("LC")
        assert r_pips(wider) - r_pips(base) == pytest.approx(2 * 3.0)

    @bearish_and_bullish
    def test_ce_widens_by_the_buffer_once(self, bullish):
        """CE's limit sits at the range midpoint, which the buffer does not
        move, so only the stop travels.
        """
        with entry_params.override(sl_buffer_pips=2.0):
            base = resolve_ce(bullish)
        with entry_params.override(sl_buffer_pips=5.0):
            wider = resolve_ce(bullish)

        assert base["model"] == "CE"
        assert r_pips(wider) - r_pips(base) == pytest.approx(3.0)

    @bearish_and_bullish
    def test_the_default_matches_an_explicit_two_pips(self, bullish):
        """The sweep's incumbent value must reproduce shipped behaviour
        exactly, or every comparison in the study is against the wrong
        baseline.
        """
        default = scan(LC1, bullish)
        with entry_params.override(sl_buffer_pips=2.0):
            explicit = scan(LC1, bullish)

        assert default["order_price"] == explicit["order_price"]
        assert default["sl"] == explicit["sl"]


class TestMinRGate:
    @bearish_and_bullish
    def test_a_setup_below_the_min_r_gate_is_dropped(self, bullish):
        """MIN_R_PIPS drops setups whose stop is too tight to be real.
        Raising it past this setup's R must remove the setup entirely
        rather than return it with a bad stop.
        """
        with entry_params.override(min_r_pips=10_000.0):
            assert scan(LC1, bullish) is None
