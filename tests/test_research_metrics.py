"""The study's strike rate, and the formatting layer.

The strike rate here is NOT runner.summarise's. That difference is the
single most load-bearing definition in the study, because it is what the
whole search optimises against, so it gets its own tests rather than
being trusted to a docstring.

  runner.summarise  win = realised_r > 0. A Friday close at +0.3R is a win.
  metrics here      win = the full take-profit was hit. A Friday close at
                    +0.3R is neither a win nor a loss, it is excluded.

The second is the user's definition, given directly. It answers "when the
market gave a verdict, how often was it in our favour", and the clock
running out is not a verdict.
"""

import pandas as pd
import pytest

from backtest.research import metrics, report
from backtest.simulate import (
    EXIT_BE_STOP,
    EXIT_CUT_19H,
    EXIT_FRIDAY_CLOSE,
    EXIT_SL,
    EXIT_TP,
)


def scored(*rows):
    """[(exit_reason, realised_r), ...] -> what metrics consumes."""
    return [
        {
            "exit_reason": reason,
            "realised_r": r,
            "exit_time": pd.Timestamp("2024-01-%02d" % (i + 1), tz="UTC"),
        }
        for i, (reason, r) in enumerate(rows)
    ]


class TestStrikeRate:
    def test_counts_only_tp_hits_and_sl_hits(self):
        rate, wins, losses = metrics.strike_rate(scored(
            (EXIT_TP, 2.0), (EXIT_TP, 2.0), (EXIT_SL, -1.0),
        ))
        assert (wins, losses) == (2, 1)
        assert rate == pytest.approx(2 / 3)

    def test_breakevens_and_time_cuts_are_excluded_from_both_sides(self):
        """The headline behaviour. Adding three unresolved trades to a
        2-from-3 book must leave the strike rate at 2-from-3, not dilute
        it to 2-from-6.
        """
        base = scored((EXIT_TP, 2.0), (EXIT_TP, 2.0), (EXIT_SL, -1.0))
        padded = base + scored(
            (EXIT_BE_STOP, 0.0),
            (EXIT_CUT_19H, -0.4),
            (EXIT_FRIDAY_CLOSE, 0.7),
        )
        assert metrics.strike_rate(padded)[0] == pytest.approx(
            metrics.strike_rate(base)[0]
        )

    def test_a_profitable_friday_close_is_not_a_win(self):
        """It would be under runner.summarise. Here nothing resolved, so
        there is no rate to report at all.
        """
        rate, wins, losses = metrics.strike_rate(scored((EXIT_FRIDAY_CLOSE, 0.7)))
        assert (rate, wins, losses) == (None, 0, 0)

    def test_none_rather_than_zero_when_nothing_resolved(self):
        """0.0 would mean "resolved and lost every one", which is a real
        and much worse result. Ranking has to be able to tell them apart.
        """
        assert metrics.strike_rate([])[0] is None


class TestSummarise:
    def test_roi_and_expectancy_include_every_taken_trade(self):
        """Deliberately a different population from the strike rate. A
        breakeven stop and a Friday close are real effects on the account
        even though neither is a verdict on the setup.
        """
        s = metrics.summarise(scored(
            (EXIT_TP, 2.0), (EXIT_SL, -1.0),
            (EXIT_BE_STOP, 0.0), (EXIT_FRIDAY_CLOSE, 0.7),
        ))
        assert s["trade_count"] == 4
        assert s["roi_r"] == pytest.approx(1.7)
        assert s["expectancy_r"] == pytest.approx(1.7 / 4)
        assert s["resolved"] == 2
        assert s["unresolved"] == 2
        assert s["strike_rate"] == pytest.approx(0.5)

    def test_drawdown_is_the_deepest_peak_to_trough(self):
        s = metrics.summarise(scored(
            (EXIT_TP, 3.0), (EXIT_SL, -1.0), (EXIT_SL, -1.0), (EXIT_TP, 2.0),
        ))
        # Equity runs 3, 2, 1, 3. Peak 3, trough 1.
        assert s["max_drawdown_r"] == pytest.approx(2.0)


class TestBest:
    def test_min_trades_guard_rejects_a_thin_cell(self):
        cells = [
            {"strike_rate": 1.0, "roi_r": 2.0, "expectancy_r": 2.0,
             "trade_count": 1, "tp_floor_ok": True},
            {"strike_rate": 0.5, "roi_r": 20.0, "expectancy_r": 0.2,
             "trade_count": 100, "tp_floor_ok": True},
        ]
        assert metrics.best(cells, "strike_rate")["trade_count"] == 1
        assert metrics.best(
            cells, "strike_rate", min_trades=8
        )["trade_count"] == 100

    def test_a_none_strike_rate_never_outranks_a_real_one(self):
        cells = [
            {"strike_rate": None, "roi_r": 99.0, "expectancy_r": 9.0,
             "trade_count": 50, "tp_floor_ok": True},
            {"strike_rate": 0.4, "roi_r": 1.0, "expectancy_r": 0.1,
             "trade_count": 50, "tp_floor_ok": True},
        ]
        assert metrics.best(cells, "strike_rate")["strike_rate"] == 0.4


class TestFormatting:
    def test_date_renders_without_zero_padding(self):
        assert report.fmt_date(pd.Timestamp("2020-01-01").date()) == "Jan 1 2020"

    def test_time_is_london_civil_not_utc(self):
        """June is BST, so 13:00 UTC is 14:00 in London. The strategy's
        clock is London, so a UTC time here would disagree with the
        engine's own killzone and checkpoint rules.
        """
        assert report.fmt_time(pd.Timestamp("2024-06-03T13:00:00Z")) == "14:00"

    def test_winter_time_needs_no_shift(self):
        assert report.fmt_time(pd.Timestamp("2024-01-08T13:00:00Z")) == "13:00"

    def test_duration_does_not_wrap_at_24_hours(self):
        """A three-day hold must not read like a twenty-minute one."""
        got = report.fmt_duration(
            pd.Timestamp("2024-01-08T10:00:00Z"),
            pd.Timestamp("2024-01-11T06:30:00Z"),
        )
        assert got == "68:30"

    def test_percentages_carry_two_decimals(self):
        assert report.fmt_pct(33.333333) == "33.33%"

    def test_a_missing_percentage_is_blank_not_zero(self):
        """0.00% is a real score the probability scale can take. Rendering
        "no score" as one would be a silent falsehood.
        """
        assert report.fmt_pct(None) == ""

    def test_sessions_get_their_display_names(self):
        assert report.fmt_session("ny") == "New York"
        assert report.fmt_session("london") == "London"
