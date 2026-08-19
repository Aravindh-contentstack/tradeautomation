"""Previous day, previous week and session liquidity levels.

The only thing that can really go wrong in these detectors is a clock, so
that is what the fixtures are built to attack: DST on both sides, the
weekend gap, and the boundary between one London day and the next.

Session fixtures are generated hourly across whole London days, so a
session's range is a fact about which candles fall inside it rather than
about hand-picked prices. Each candle's high is stamped with its own UTC
hour, which makes an expected session high readable straight off the clock:
the Asian range (00:00 to 04:00 London) in winter covers UTC hours 0 to 3,
so its high is the marker for hour 3.
"""

import pandas as pd
import pytest

from backtest.killzone import to_london
from smc.liquidity.time_levels import (
    compute_previous_day_levels,
    compute_previous_week_levels,
    compute_session_levels,
)

# Each candle's high encodes its UTC hour as BASE + hour, so an assertion
# can name the hour it expects rather than a price.
BASE = 100.0


def h1_frame(start, hours):
    """`hours` hourly candles from `start`, each high stamped with its hour."""
    start = pd.Timestamp(start, tz="UTC")
    rows = []
    for i in range(hours):
        ts = start + pd.Timedelta(hours=i)
        high = BASE + ts.hour
        rows.append({
            "date": ts,
            "open": high - 0.5,
            "high": high,
            "low": high - 1.0,
            "close": high - 0.5,
        })
    return pd.DataFrame(rows)


def daily_frame(rows):
    """[(date, high, low), ...] -> a Daily frame."""
    return pd.DataFrame([
        {
            "date": pd.Timestamp(date, tz="UTC"),
            "open": (high + low) / 2.0,
            "high": high,
            "low": low,
            "close": (high + low) / 2.0,
        }
        for date, high, low in rows
    ])


def session(levels, kind, side, day):
    """The one level of a kind and side visible on a given London day."""
    local = to_london(pd.Series(pd.DatetimeIndex(levels["visible_from_date"])))
    mask = (
        (levels["kind"] == kind)
        & (levels["side"] == side)
        & (local.dt.strftime("%Y-%m-%d").to_numpy() == day)
    )
    matches = levels[mask]
    assert len(matches) == 1, "expected one %s %s on %s, got %d" % (
        kind, side, day, len(matches),
    )
    return matches.iloc[0]


class TestPreviousDay:
    def test_the_level_is_yesterdays_range(self):
        daily = daily_frame([
            ("2024-03-04", 110.0, 100.0),
            ("2024-03-05", 120.0, 90.0),
        ])
        levels = compute_previous_day_levels(daily)
        assert set(levels["level"]) == {110.0, 100.0}

    def test_the_level_is_not_readable_before_its_own_day_closed(self):
        """The window opens at the NEXT day's open, which is the instant
        the candle that produced the level finished. Anything earlier
        would be reading a level off a candle still printing.
        """
        daily = daily_frame([
            ("2024-03-04", 110.0, 100.0),
            ("2024-03-05", 120.0, 90.0),
        ])
        level = compute_previous_day_levels(daily).iloc[0]
        assert level["visible_from_date"] == pd.Timestamp("2024-03-05", tz="UTC")

    def test_the_first_day_has_no_previous_day(self):
        daily = daily_frame([("2024-03-04", 110.0, 100.0)])
        assert len(compute_previous_day_levels(daily)) == 0

    def test_previous_day_over_a_weekend_is_friday(self):
        """The Daily table has no weekend rows, so "the previous row" is
        already the right answer. Asserted rather than assumed, since it is
        the one place a naive minus-one-day would differ.
        """
        daily = daily_frame([
            ("2024-03-08", 115.0, 105.0),   # Friday
            ("2024-03-11", 120.0, 90.0),    # Monday
        ])
        monday = compute_previous_day_levels(daily)
        assert monday.iloc[0]["visible_from_date"] == pd.Timestamp("2024-03-11", tz="UTC")
        assert set(monday["level"]) == {115.0, 105.0}


class TestPreviousWeek:
    def test_the_level_is_the_whole_of_last_weeks_range(self):
        daily = daily_frame([
            ("2024-03-04", 110.0, 100.0),
            ("2024-03-06", 130.0, 95.0),
            ("2024-03-08", 115.0, 105.0),
            ("2024-03-11", 120.0, 90.0),
        ])
        levels = compute_previous_week_levels(daily)
        assert set(levels["level"]) == {130.0, 95.0}

    def test_the_window_spans_the_whole_of_the_current_week(self):
        daily = daily_frame([
            ("2024-03-04", 110.0, 100.0),
            ("2024-03-08", 115.0, 105.0),
            ("2024-03-11", 120.0, 90.0),
            ("2024-03-15", 125.0, 85.0),
        ])
        level = compute_previous_week_levels(daily).iloc[0]
        assert level["visible_from_date"] == pd.Timestamp("2024-03-11", tz="UTC")
        assert level["valid_through_date"] == pd.Timestamp("2024-03-16", tz="UTC")

    def test_previous_means_the_last_week_that_traded(self):
        """A whole week absent from the data (a shutdown, a feed gap) must
        not leave the following week with no levels. What a trader means by
        "last week's high" is the last week there was one.
        """
        daily = daily_frame([
            ("2024-03-04", 110.0, 100.0),   # week 10
            ("2024-03-18", 120.0, 90.0),    # week 12, week 11 missing entirely
        ])
        levels = compute_previous_week_levels(daily)
        assert set(levels["level"]) == {110.0, 100.0}
        assert levels.iloc[0]["visible_from_date"] == pd.Timestamp("2024-03-18", tz="UTC")


class TestSessionsAcrossDst:
    """The three ranges are defined in LONDON civil hours, which cover a
    different span of UTC hours in winter than in summer. Both are
    asserted, because a fixed-offset bug passes one and fails the other.

    Every case reads the SECOND London day of its fixture, never the first.
    A frame starting at 00:00 UTC starts mid-session in summer (01:00
    London), so day one's Asian range is truncated and would agree with a
    buggy implementation by accident.

    The summer Asian case is the sharpest of the six: London midnight is
    23:00 UTC the previous day, so a correct implementation reaches back
    across the UTC date boundary and reports hour 23, while anything
    grouping on the UTC date reports hour 2.
    """

    @pytest.mark.parametrize("start,day,expected_high_hour", [
        ("2024-01-15", "2024-01-16", 3),    # GMT: London 00-04 is UTC 00-03
        ("2024-07-15", "2024-07-16", 23),   # BST: London 00-04 is UTC 23-02
    ])
    def test_the_asian_range_tracks_london_civil_time(self, start, day, expected_high_hour):
        levels = compute_session_levels(h1_frame(start, 24 * 4))
        assert session(levels, "asian", "high", day)["level"] == BASE + expected_high_hour

    @pytest.mark.parametrize("start,day,expected_high_hour", [
        ("2024-01-15", "2024-01-16", 9),   # GMT: London 07-10 is UTC 07-09
        ("2024-07-15", "2024-07-16", 8),   # BST: London 07-10 is UTC 06-08
    ])
    def test_the_london_range_tracks_london_civil_time(self, start, day, expected_high_hour):
        levels = compute_session_levels(h1_frame(start, 24 * 4))
        assert session(levels, "london", "high", day)["level"] == BASE + expected_high_hour

    @pytest.mark.parametrize("start,day,expected_high_hour", [
        ("2024-01-15", "2024-01-16", 14),  # GMT: London 12-15 is UTC 12-14
        ("2024-07-15", "2024-07-16", 13),  # BST: London 12-15 is UTC 11-13
    ])
    def test_the_ny_range_tracks_london_civil_time(self, start, day, expected_high_hour):
        levels = compute_session_levels(h1_frame(start, 24 * 4))
        assert session(levels, "ny", "high", day)["level"] == BASE + expected_high_hour


class TestSessionWindows:
    def test_a_session_is_not_readable_until_it_has_finished(self):
        """The range is not knowable a moment before its last candle
        closes, so the window opens at that close, not at the session's
        start.
        """
        levels = compute_session_levels(h1_frame("2024-01-15", 24 * 3))
        asian = session(levels, "asian", "high", "2024-01-15")
        assert asian["visible_from_date"] == pd.Timestamp("2024-01-15 04:00", tz="UTC")

    def test_a_session_stays_readable_through_the_following_day(self):
        """This is what makes "previous day London high" a thing at all:
        today's NY session has to be able to hunt yesterday's London high.
        """
        levels = compute_session_levels(h1_frame("2024-01-15", 24 * 4))
        london = session(levels, "london", "high", "2024-01-15")
        assert london["valid_through_date"] >= pd.Timestamp("2024-01-17", tz="UTC")

    def test_every_session_produces_both_sides(self):
        levels = compute_session_levels(h1_frame("2024-01-15", 24 * 3))
        for kind in ("asian", "london", "ny"):
            for side in ("high", "low"):
                assert session(levels, kind, side, "2024-01-16") is not None

    def test_a_day_with_no_session_bars_is_skipped_not_nulled(self):
        """A feed gap over a session must produce no level, rather than a
        level with a null range that every downstream comparison would
        silently answer False for.
        """
        frame = h1_frame("2024-01-15", 24 * 3)
        local_hour = to_london(pd.Series(pd.DatetimeIndex(frame["date"]))).dt.hour
        gap = (frame["date"] >= pd.Timestamp("2024-01-16", tz="UTC")) & (local_hour < 4)
        levels = compute_session_levels(frame[~gap].reset_index(drop=True))

        asian = levels[levels["kind"] == "asian"]
        local = to_london(pd.Series(pd.DatetimeIndex(asian["visible_from_date"])))
        assert "2024-01-16" not in set(local.dt.strftime("%Y-%m-%d"))
        assert not levels["level"].isna().any()
