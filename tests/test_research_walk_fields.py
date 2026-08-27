"""The two walk fields the tuning study added: max_r_to_be and the
end-of-trade probability, plus the tunable reporting ceiling.

max_r_to_be answers "how much was this trade up before its stop moved to
entry". It exists because the study needs to price the breakeven rules:
if trades routinely show 2R before being moved to breakeven and then come
back, the rule is giving away money, and no existing column could show
that. max_r_reached cannot, because it keeps running after the move.

The semantics under test are STRICTLY BEFORE the move, matching how
max_r_reached is strictly before the terminal event. Both breakeven sites
sit ahead of the favourable-excursion step in the walk's precedence, so
this falls out of the ordering rather than needing its own bookkeeping.
"""

import pytest

from backtest.simulate import EXIT_BE_STOP, MAX_R_CEILING, simulate_trade
from conftest import DIRECTION, ENTRY, R_DISTANCE, SL, bar_range, ctx_for

FLAT = (1.1000, 1.1002, 1.0998, 1.1000)
FLAT_ABOVE_ENTRY = (1.1005, 1.1006, 1.1003, 1.1005)


def walk(ctx, idx=0, **kwargs):
    return simulate_trade(ctx, idx, DIRECTION, ENTRY, SL, R_DISTANCE, **kwargs)


class TestMaxRToBe:
    def test_none_when_the_trade_never_moved_to_breakeven(self):
        """None, not 0.0. "Never went to breakeven" and "went to breakeven
        having shown nothing" are different trades and must not collapse
        into the same value in the journal.
        """
        bars = [
            ("2024-01-08T08:00:00Z", *FLAT),
            ("2024-01-08T09:00:00Z", 1.1000, 1.1002, 1.0979, 1.0985),  # stop
        ]
        result = walk(ctx_for(bars))

        assert result["be_moved"] is False
        assert result["max_r_to_be"] is None

    def test_records_the_high_water_mark_before_the_19h_move(self):
        """Up 2R on the way to the checkpoint, still up at 19:00 so the
        stop moves, then price falls back to entry. max_r_to_be must be
        the 2R, not the 0R the trade actually returned.
        """
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            # 1.1040 is exactly +2R off a 0.0020 stop distance.
            + [("2024-01-08T09:00:00Z", 1.1000, 1.1040, 1.0999, 1.1030)]
            + bar_range("2024-01-08T10:00:00Z", 9, FLAT_ABOVE_ENTRY)
            + [
                ("2024-01-08T19:00:00Z", 1.1010, 1.1012, 1.1008, 1.1010),
                ("2024-01-08T20:00:00Z", 1.1010, 1.1010, 1.0999, 1.1005),
            ]
        )
        result = walk(ctx_for(bars))

        assert result["be_trigger"] == "19h_checkpoint"
        assert result["terminal_reason"] == EXIT_BE_STOP
        assert result["terminal_r"] == pytest.approx(0.0)
        assert result["max_r_to_be"] == pytest.approx(2.0)

    def test_excludes_the_bar_the_move_happened_on(self):
        """The checkpoint reads the bar's OPEN and both breakeven sites run
        before the favourable-excursion step, so a spike ON the checkpoint
        bar is not credited to max_r_to_be. Same "strictly before the
        event" reading max_r_reached uses.
        """
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            + bar_range("2024-01-08T09:00:00Z", 10, FLAT_ABOVE_ENTRY)
            # Opens +0.5R (so the stop moves) and then spikes to +3R.
            + [("2024-01-08T19:00:00Z", 1.1010, 1.1060, 1.1008, 1.1010)]
            + [("2024-01-08T20:00:00Z", 1.1010, 1.1010, 1.0999, 1.1005)]
        )
        result = walk(ctx_for(bars))

        assert result["be_moved"] is True
        # The filler bars' own high (1.1006, so +0.30R) and nothing from
        # the checkpoint bar itself.
        assert result["max_r_to_be"] == pytest.approx(0.30)
        # The spike still counts toward the overall high-water mark, which
        # is what makes the two columns say different things.
        assert result["max_r_reached"] == pytest.approx(3.0)

    def test_never_exceeds_max_r_reached(self):
        """A structural invariant: the high-water mark before an event
        cannot beat the high-water mark over the whole trade.
        """
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            + [("2024-01-08T09:00:00Z", 1.1000, 1.1040, 1.0999, 1.1030)]
            + bar_range("2024-01-08T10:00:00Z", 9, FLAT_ABOVE_ENTRY)
            + [("2024-01-08T19:00:00Z", 1.1010, 1.1080, 1.1008, 1.1010)]
            + [("2024-01-08T20:00:00Z", 1.1010, 1.1010, 1.0999, 1.1005)]
        )
        result = walk(ctx_for(bars))

        assert result["max_r_to_be"] <= result["max_r_reached"]


class TestMaxRCeiling:
    def test_defaults_to_the_journal_ceiling(self):
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            # +50R, far past the 10R reporting clamp.
            + [("2024-01-08T09:00:00Z", 1.1000, 1.2000, 1.0999, 1.1900)]
            + [("2024-01-08T10:00:00Z", 1.1900, 1.1900, 1.0979, 1.0985)]
        )
        assert walk(ctx_for(bars))["max_r_reached"] == pytest.approx(MAX_R_CEILING)

    def test_can_be_raised_for_the_study(self):
        """A liquidity-target take-profit can legitimately sit past 10R.
        Clipping there would score those as never reached and make the
        whole family look worse than it is.
        """
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            + [("2024-01-08T09:00:00Z", 1.1000, 1.2000, 1.0999, 1.1900)]
            + [("2024-01-08T10:00:00Z", 1.1900, 1.1900, 1.0979, 1.0985)]
        )
        result = walk(ctx_for(bars), max_r_ceiling=50.0)
        assert result["max_r_reached"] == pytest.approx(50.0)


class TestProbabilityRecordingIsOptIn:
    def test_off_by_default_so_existing_callers_pay_nothing(self):
        bars = (
            [("2024-01-08T08:00:00Z", *FLAT)]
            + [("2024-01-08T09:00:00Z", 1.1000, 1.1002, 1.0979, 1.0985)]
        )
        result = walk(ctx_for(bars))

        assert result["final_probability"] is None
        assert result["min_live_probability"] is None
