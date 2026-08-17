"""max_r_reached must exclude the terminal bar's favourable move.

This is the regression test for defect 2 in the plan, and it is the most
important single test in the suite.

The old engine updated its running max BEFORE checking the stop, so a
candle that spiked +3R and then traded through the stop was credited with
the full 3R. It never was 3R: OHLC alone cannot say whether the spike
came before or after the stop touch, and if the stop went first the spike
was never reachable at all. Since analysis.py projects every TP multiple
off max_r_reached, that inflated credit turned stopped-out trades into
recorded wins.

The fix has two halves, both tested here:

* On the terminal bar the provisional credit is DISCARDED outright, and
  with no M15 available nothing is credited back. Pessimistic by choice.
* With M15 available the credit is re-derived from the sub-bars, in
  order, stopping at the sub-bar that touches the stop.

The mirrored pair below is the whole point of the M15 work: two fixtures
whose H1 bar is byte-identical (same open, high, low, close) and whose
verdicts are opposite, because the sub-bar ORDER differs. Nothing that
reads only H1 can tell them apart.
"""

import pytest

from backtest.simulate import EXIT_SL, apply_tp, simulate_trade
from conftest import DIRECTION, ENTRY, R_DISTANCE, SL, ctx_for


def walk(ctx, idx=0):
    return simulate_trade(ctx, idx, DIRECTION, ENTRY, SL, R_DISTANCE)


# A single H1 bar that spikes to 1.1060 (+3R) AND trades down to the stop
# at 1.0980. Ambiguous by construction.
AMBIGUOUS_BARS = [
    ("2024-01-10T08:00:00Z", 1.1000, 1.1002, 1.0998, 1.1000),   # entry bar
    ("2024-01-10T09:00:00Z", 1.1000, 1.1060, 1.0980, 1.0990),   # +3R and the stop
]


def test_max_r_excludes_terminal_bar_favourable_move():
    """The defect-2 regression test. Pre-change code returns 3.0 here.

    There is no M15, so the terminal bar contributes nothing at all and
    the trade is a clean -1R with zero recorded excursion. Against the old
    implementation this assertion reads max_r_reached == 3.0 and the trade
    would then have been scored a 2.5R WIN by analysis.py despite being
    stopped out.
    """
    result = walk(ctx_for(AMBIGUOUS_BARS))

    assert result["max_r_reached"] == pytest.approx(0.0)
    assert result["terminal_reason"] == EXIT_SL
    assert result["terminal_r"] == pytest.approx(-1.0)
    assert result["intrabar_resolved"] is False


def test_ambiguous_bar_without_m15_scores_as_a_loss_at_every_tp():
    """The consequence that actually matters: with no favourable credit,
    no TP multiple can turn this stop-out into a win.
    """
    result = walk(ctx_for(AMBIGUOUS_BARS))

    for tp in (1.5, 2.0, 2.5, 3.0):
        outcome = apply_tp(result, tp, ENTRY, DIRECTION, R_DISTANCE)
        assert outcome["realised_r"] == pytest.approx(-1.0)
        assert outcome["result"] == "loss"


def test_favourable_move_on_a_non_terminal_bar_is_still_credited():
    """The fix must not throw away legitimate excursion. Same +3R spike,
    but on the bar BEFORE the stop-out, so it really was reachable.
    """
    bars = [
        ("2024-01-10T08:00:00Z", 1.1000, 1.1002, 1.0998, 1.1000),
        ("2024-01-10T09:00:00Z", 1.1000, 1.1060, 1.0995, 1.1055),  # +3R, no stop
        ("2024-01-10T10:00:00Z", 1.1055, 1.1056, 1.0980, 1.0985),  # the stop
    ]
    result = walk(ctx_for(bars))

    assert result["max_r_reached"] == pytest.approx(3.0)
    assert result["terminal_reason"] == EXIT_SL
    assert result["terminal_r"] == pytest.approx(-1.0)


# --- the mirrored M15 pair -------------------------------------------------
#
# Both fixtures share the SAME H1 bar: open 1.1002, high 1.1060,
# low 1.0980, close 1.0990. Only the order of the four M15 sub-bars
# differs, and that order flips the verdict from +3R to nothing.

_SUB_SPIKE = ("2024-01-10T09:00:00Z", 1.1002, 1.1060, 1.1000, 1.1050)
_SUB_STOP = ("2024-01-10T09:15:00Z", 1.1050, 1.1055, 1.0980, 1.0985)
_SUB_DRIFT_A = ("2024-01-10T09:30:00Z", 1.0985, 1.0990, 1.0982, 1.0988)
_SUB_DRIFT_B = ("2024-01-10T09:45:00Z", 1.0988, 1.0995, 1.0984, 1.0990)


def _with_stamps(subbars):
    """Re-stamps sub-bars onto 09:00/09:15/09:30/09:45 in list order.

    The mirrored fixture must keep the same four quarter-hour slots; only
    which OHLC sits in which slot changes. Re-stamping here is what makes
    "the same bar, replayed backwards" literally true rather than
    approximately true.
    """
    slots = [
        "2024-01-10T09:00:00Z",
        "2024-01-10T09:15:00Z",
        "2024-01-10T09:30:00Z",
        "2024-01-10T09:45:00Z",
    ]
    return [(slot, o, h, l, c) for slot, (_, o, h, l, c) in zip(slots, subbars)]


M15_TOWARD_TP = _with_stamps([_SUB_SPIKE, _SUB_STOP, _SUB_DRIFT_A, _SUB_DRIFT_B])
M15_TOWARD_SL = _with_stamps([_SUB_DRIFT_B, _SUB_DRIFT_A, _SUB_STOP, _SUB_SPIKE])


def test_m15_resolves_the_tie_toward_tp():
    """Spike first, stop second: the 3R really was available, so it is
    credited and a 2.5R TP fills.
    """
    result = walk(ctx_for(AMBIGUOUS_BARS, m15_bars=M15_TOWARD_TP))

    assert result["intrabar_resolved"] is True
    assert result["max_r_reached"] == pytest.approx(3.0)
    # The walk still terminates on the stop -- the TP is applied after.
    assert result["terminal_reason"] == EXIT_SL

    outcome = apply_tp(result, 2.5, ENTRY, DIRECTION, R_DISTANCE)
    assert outcome["realised_r"] == pytest.approx(2.5)
    assert outcome["result"] == "win"


def test_m15_mirrored_resolves_the_tie_toward_sl():
    """Same H1 bar, sub-bars reversed. The stop is touched in the third
    quarter-hour, before the spike, so the spike was never reachable and
    nothing is credited.
    """
    result = walk(ctx_for(AMBIGUOUS_BARS, m15_bars=M15_TOWARD_SL))

    assert result["intrabar_resolved"] is True
    assert result["max_r_reached"] == pytest.approx(0.0)
    assert result["terminal_reason"] == EXIT_SL

    outcome = apply_tp(result, 2.5, ENTRY, DIRECTION, R_DISTANCE)
    assert outcome["realised_r"] == pytest.approx(-1.0)
    assert outcome["result"] == "loss"


def test_the_mirrored_pair_share_an_identical_h1_bar():
    """Guards the fixtures themselves. If a future edit lets the two H1
    bars drift apart, the pair above stops proving anything and would
    quietly pass for the wrong reason.
    """
    tp_ctx = ctx_for(AMBIGUOUS_BARS, m15_bars=M15_TOWARD_TP)
    sl_ctx = ctx_for(AMBIGUOUS_BARS, m15_bars=M15_TOWARD_SL)

    for field in ("open_", "high", "low", "close"):
        assert list(getattr(tp_ctx, field)) == list(getattr(sl_ctx, field))

    # ...and that the M15 sub-bars really do aggregate to that H1 bar.
    for m15_bars in (M15_TOWARD_TP, M15_TOWARD_SL):
        assert max(b[2] for b in m15_bars) == pytest.approx(1.1060)
        assert min(b[3] for b in m15_bars) == pytest.approx(1.0980)


def test_missing_m15_degrades_to_the_pessimistic_assumption():
    """No M15 at all must be a graceful degrade, not a crash and not a
    silent fall back to the old whole-bar credit.
    """
    result = walk(ctx_for(AMBIGUOUS_BARS, m15_bars=None))

    assert result["intrabar_resolved"] is False
    assert result["max_r_reached"] == pytest.approx(0.0)
    assert result["terminal_r"] == pytest.approx(-1.0)


def test_m15_present_but_not_covering_the_terminal_bar_degrades_too():
    """A partial M15 history (WS-A's download is allowed to be partial) is
    the same situation as no M15 for any hour it does not cover.
    """
    elsewhere = [("2024-01-10T14:00:00Z", 1.1000, 1.1001, 1.0999, 1.1000)]
    result = walk(ctx_for(AMBIGUOUS_BARS, m15_bars=elsewhere))

    assert result["intrabar_resolved"] is False
    assert result["max_r_reached"] == pytest.approx(0.0)


def test_sl_excursion_pips_records_how_far_past_the_stop_the_bar_traded():
    """The diagnostic that replaced the deleted SLB rescue walk. The bar
    below trades to 1.0975, five pips beyond a stop at 1.0980.
    """
    bars = [
        ("2024-01-10T08:00:00Z", 1.1000, 1.1002, 1.0998, 1.1000),
        ("2024-01-10T09:00:00Z", 1.1000, 1.1005, 1.0975, 1.0985),
    ]
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_SL
    assert result["sl_excursion_pips"] == pytest.approx(5.0)
