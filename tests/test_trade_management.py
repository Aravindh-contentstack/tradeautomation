"""The 19:00 checkpoint, the Friday deadline, DST, and the holiday hole.

This is the state-machine half of simulate_trade that
test_max_r_attribution.py does not cover: it exercises steps 1, 2 and 4 of
the walk-loop precedence (week deadline, daily checkpoint, stop) rather than
step 3/5 (favourable attribution).

All fixtures use a full trading week in January 2024 (GMT, no DST) unless a
test is specifically about the DST transition, so a test failure points at
one rule rather than an interaction between two.
"""

import pytest

from backtest.simulate import (
    EXIT_BE_STOP,
    EXIT_CUT_19H,
    EXIT_FRIDAY_CLOSE,
    simulate_trade,
)
from conftest import DIRECTION, ENTRY, R_DISTANCE, SL, bar_range, ctx_for

FLAT = (1.1000, 1.1002, 1.0998, 1.1000)

# Once a trade is moved to breakeven the stop sits at ENTRY (1.1000).
# FLAT's low (1.0998) would touch that stop, so any filler used AFTER a
# breakeven move must stay strictly above entry.
FLAT_ABOVE_ENTRY = (1.1005, 1.1006, 1.1003, 1.1005)


def walk(ctx, idx=0):
    return simulate_trade(ctx, idx, DIRECTION, ENTRY, SL, R_DISTANCE)


def test_checkpoint_moves_stop_to_breakeven_and_lets_the_trade_run():
    """In profit at 19:00 -> stop moves to entry, once. Proven by then
    dipping to entry price (not the original SL) on the next bar and
    seeing a breakeven exit rather than a plain SL exit.
    """
    bars = (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + bar_range("2024-01-08T09:00:00Z", 10, FLAT)  # 09:00..18:00
        + [
            ("2024-01-08T19:00:00Z", 1.1010, 1.1012, 1.1008, 1.1010),  # checkpoint, +0.5R
            ("2024-01-08T20:00:00Z", 1.1010, 1.1010, 1.0999, 1.1005),  # dips through entry
        ]
    )
    result = walk(ctx_for(bars))

    assert result["be_moved"] is True
    assert result["be_idx"] == 11  # the 19:00Z bar
    assert result["terminal_reason"] == EXIT_BE_STOP
    assert result["terminal_r"] == pytest.approx(0.0)
    assert result["terminal_idx"] == 12


def test_checkpoint_cuts_a_losing_trade_at_the_fractional_r():
    """Not in profit at 19:00 -> cut immediately, at whatever fractional R
    that actually is, not a full -1R. This is the R1 headline behaviour.
    """
    bars = (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + bar_range("2024-01-08T09:00:00Z", 10, FLAT)
        + [("2024-01-08T19:00:00Z", 1.0992, 1.0995, 1.0990, 1.0992)]  # -0.4R
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_CUT_19H
    assert result["terminal_r"] == pytest.approx(-0.4)
    assert result["be_moved"] is False
    assert result["terminal_idx"] == 11


def test_checkpoint_exactly_flat_counts_as_not_in_profit():
    """Flat (0R) at 19:00 is not "in profit", so it cuts rather than moves
    to breakeven. Documented as a deliberate choice, not an oversight.
    """
    bars = (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + bar_range("2024-01-08T09:00:00Z", 10, FLAT)
        + [("2024-01-08T19:00:00Z", 1.1000, 1.1001, 1.0999, 1.1000)]  # exactly flat
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_CUT_19H
    assert result["terminal_r"] == pytest.approx(0.0)
    assert result["be_moved"] is False


def test_friday_deadline_closes_a_winning_trade_before_any_checkpoint_logic():
    """The deadline bar IS the checkpoint bar on a Friday, and the deadline
    must win: a profitable close at the deadline is reported as
    EXIT_FRIDAY_CLOSE, never as a breakeven move that happens to also be
    the end of the week.
    """
    bars = (
        [("2024-01-12T08:00:00Z", *FLAT)]  # Friday
        + bar_range("2024-01-12T09:00:00Z", 10, FLAT)
        + [("2024-01-12T19:00:00Z", 1.1030, 1.1032, 1.1028, 1.1030)]  # +1.5R
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_FRIDAY_CLOSE
    assert result["terminal_r"] == pytest.approx(1.5)
    assert result["be_moved"] is False


def test_friday_deadline_closes_a_losing_trade_as_friday_close_not_cut():
    """Same bar, losing side: the exit reason must still be
    EXIT_FRIDAY_CLOSE, not EXIT_CUT_19H. Downstream reporting (and the
    user reading the journal) needs to be able to tell "the week ran out"
    apart from "the daily checkpoint judged this a loser".
    """
    bars = (
        [("2024-01-12T08:00:00Z", *FLAT)]
        + bar_range("2024-01-12T09:00:00Z", 10, FLAT)
        + [("2024-01-12T19:00:00Z", 1.0990, 1.0992, 1.0988, 1.0990)]  # -0.5R
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_FRIDAY_CLOSE
    assert result["terminal_r"] == pytest.approx(-0.5)


def test_position_never_survives_past_its_entry_weeks_friday_cutoff():
    """A trade that stays in profit at every daily checkpoint through the
    week must still be forced closed at Friday 19:00, even though nothing
    in the daily-checkpoint rule alone would have stopped it. Weekend bars
    are appended AFTER the deadline with a price that would have stopped
    the trade out, to prove the walk never reaches them.
    """
    bars = [("2024-01-10T08:00:00Z", *FLAT)]  # Wednesday
    bars += bar_range("2024-01-10T09:00:00Z", 10, FLAT)
    bars += [("2024-01-10T19:00:00Z", 1.1005, 1.1006, 1.1004, 1.1005)]  # +0.25R -> BE
    bars += bar_range("2024-01-11T09:00:00Z", 10, FLAT_ABOVE_ENTRY)
    bars += [("2024-01-11T19:00:00Z", 1.1006, 1.1007, 1.1005, 1.1006)]  # still in profit
    bars += bar_range("2024-01-12T09:00:00Z", 10, FLAT_ABOVE_ENTRY)
    bars += [("2024-01-12T19:00:00Z", 1.1035, 1.1036, 1.1034, 1.1035)]  # Friday deadline, +1.75R
    # Weekend bars the walk must never reach: they would stop the trade out.
    bars += [("2024-01-14T22:00:00Z", 1.1035, 1.1036, 1.0900, 1.0950)]

    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_FRIDAY_CLOSE
    assert result["terminal_r"] == pytest.approx(1.75)
    assert result["be_moved"] is True


def test_cutoff_is_18utc_under_bst():
    """The real DST test: 19:00 London is 18:00 UTC in July (BST). A
    UTC-vs-London *weekday* comparison cannot fail at this hour (they never
    diverge there), so this checks the one thing that actually moves across
    the transition: which UTC hour the cutoff lands on.
    """
    bars = (
        [("2024-07-10T08:00:00Z", *FLAT)]
        + bar_range("2024-07-10T09:00:00Z", 9, FLAT)  # 09:00..17:00
        + [("2024-07-10T18:00:00Z", 1.0992, 1.0995, 1.0990, 1.0992)]  # -0.4R at 18:00Z
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_CUT_19H
    assert result["terminal_r"] == pytest.approx(-0.4)
    assert result["terminal_idx"] == 10  # the 18:00Z bar


def test_cutoff_is_19utc_under_gmt():
    """Same shape, six months earlier: 19:00 London is 19:00 UTC in
    January (GMT). Paired with the BST test above, this is what proves the
    cutoff genuinely tracks the DST transition rather than being hardcoded
    to one offset.
    """
    bars = (
        [("2024-01-10T08:00:00Z", *FLAT)]
        + bar_range("2024-01-10T09:00:00Z", 9, FLAT)  # 09:00..17:00
        + [("2024-01-10T18:00:00Z", *FLAT)]  # not yet the cutoff under GMT
        + [("2024-01-10T19:00:00Z", 1.0992, 1.0995, 1.0990, 1.0992)]  # -0.4R at 19:00Z
    )
    result = walk(ctx_for(bars))

    assert result["terminal_reason"] == EXIT_CUT_19H
    assert result["terminal_r"] == pytest.approx(-0.4)
    assert result["terminal_idx"] == 11  # the 19:00Z bar, not 18:00Z


def test_holiday_missing_1900_bar_cuts_at_the_next_available_bar():
    """The 19:00 London bar is genuinely absent on 19 real weekdays in the
    EUR_USD history. The checkpoint must fire at the first bar AT OR AFTER
    the cutoff instant, and the FOLLOWING checkpoint must still land on
    19:00 the next London day -- not "22:00 + 24h" and not "19:00 + 24h
    from the late bar".
    """
    bars = [("2024-01-08T08:00:00Z", *FLAT)]  # Monday
    bars += bar_range("2024-01-08T09:00:00Z", 8, FLAT)  # 09:00..16:00
    bars += [("2024-01-08T17:00:00Z", *FLAT)]
    # A hole: no 18:00 or 19:00 bar. The first bar at or after the cutoff
    # is 22:00Z, and it is in profit.
    bars += [("2024-01-08T22:00:00Z", 1.1006, 1.1008, 1.1004, 1.1006)]  # +0.3R
    bars += bar_range("2024-01-09T09:00:00Z", 9, FLAT_ABOVE_ENTRY)  # Tuesday morning, no trigger yet
    bars += [("2024-01-09T19:00:00Z", 1.0990, 1.0992, 1.0988, 1.0990)]  # -0.5R, the real Tue checkpoint

    result = walk(ctx_for(bars))

    assert result["be_moved"] is True
    assert result["be_idx"] == 10  # the 22:00Z bar

    # The second checkpoint lands on Tuesday 19:00Z, not Tuesday 22:00Z and
    # not "Monday 22:00 + 24h" (which would also land at 22:00 Tuesday).
    assert result["terminal_reason"] == EXIT_CUT_19H
    assert result["terminal_r"] == pytest.approx(-0.5)
