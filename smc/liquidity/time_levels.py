"""Time-based liquidity: previous day, previous week, and the three session
ranges.

Unlike smc/liquidity/levels.py, nothing here is derived from price structure.
These levels exist because a clock said so, which makes them a different
shape: each one is live over a fixed DATE WINDOW rather than until price does
something to it. Yesterday's high is liquidity for exactly today, and stops
being "yesterday's high" at midnight whether or not anyone took it.

That is why every function here returns date bounds and no sweep state. Two
different consumers ask two different questions of the same level, and
neither can be answered here:

  - the Swept Liquidity gate asks whether the last CLOSED Daily or 4H candle
    wicked the level, which is a question about that timeframe's candle;
  - the Liquidity Target gate asks whether the level is still untaken as of
    an H1 bar, which is a question about the H1 timeline.

smc/liquidity/liq_state.py answers the second for every level kind at once.

Boundaries, confirmed with the user
-----------------------------------
Day and week come from the stored Daily candles (00:00 UTC), not from the
London civil day, so these levels line up with the Daily structure and Daily
order blocks the rest of the engine is built on. "Previous day" over a
weekend is therefore Friday, because the Daily table has no weekend rows.

Sessions are the exception and are London civil time, because a session IS a
clock: the Asian range is midnight to 04:00 London whatever that is in UTC.
All three windows come from backtest/killzone.py's SESSION_HOURS, which is
also what the killzone gate uses, so a session cannot mean two things.

Session levels stay live through the END OF THE NEXT London day rather than
their own, which is what makes "previous day London high" and "previous day
NY high" readable. Today's NY session can hunt yesterday's London high, and
the user's spec calls for exactly that.
"""

import numpy as np
import pandas as pd

from backtest.killzone import SESSION_HOURS, london_calendar

PREVIOUS_DAY = "previous_day"
PREVIOUS_WEEK = "previous_week"

HIGH = "high"
LOW = "low"

_TIME_LEVEL_COLUMNS = [
    "kind",
    "side",
    "level",
    "visible_from_date",
    "valid_through_date",
]

_DAY = pd.Timedelta(days=1)
_HOUR = pd.Timedelta(hours=1)


def _frame(rows):
    return pd.DataFrame(rows, columns=_TIME_LEVEL_COLUMNS)


def _pair(kind, high, low, visible_from, valid_through):
    """The two sides of one time-based range, as level rows."""
    return [
        {
            "kind": kind,
            "side": HIGH,
            "level": high,
            "visible_from_date": visible_from,
            "valid_through_date": valid_through,
        },
        {
            "kind": kind,
            "side": LOW,
            "level": low,
            "visible_from_date": visible_from,
            "valid_through_date": valid_through,
        },
    ]


def compute_previous_day_levels(daily_df):
    """Yesterday's high and low, live for the whole of each Daily candle.

    daily_df: the Daily OHLC table (date, open, high, low, close),
        ascending. `date` is the candle's OPEN.

    The window opens at day i's open, which is also the instant day i-1
    closed, and runs to day i's own close. Nothing can read the level
    before the candle that produced it has finished, which is the same
    no-lookahead rule the pipeline's merge_asof enforces.
    """
    df = daily_df.reset_index(drop=True)
    dates = pd.DatetimeIndex(df["date"])
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    rows = []
    for i in range(1, len(df)):
        rows.extend(
            _pair(PREVIOUS_DAY, highs[i - 1], lows[i - 1], dates[i], dates[i] + _DAY)
        )
    return _frame(rows)


def compute_previous_week_levels(daily_df):
    """Last week's high and low, live for the whole of each week.

    Weeks are ISO year plus week number off the Daily rows, and "previous"
    means the previous week PRESENT IN THE DATA, not the previous calendar
    week. A holiday week with no rows at all would otherwise leave the
    following week with no levels, when what a trader means by "last week's
    high" is simply the last week that traded.
    """
    df = daily_df.reset_index(drop=True)
    dates = pd.DatetimeIndex(df["date"])
    iso = dates.isocalendar()
    keys = list(zip(iso["year"], iso["week"]))

    weeks = []
    for i, key in enumerate(keys):
        if weeks and weeks[-1]["key"] == key:
            weeks[-1]["high"] = max(weeks[-1]["high"], df["high"].iloc[i])
            weeks[-1]["low"] = min(weeks[-1]["low"], df["low"].iloc[i])
            weeks[-1]["end"] = dates[i] + _DAY
        else:
            weeks.append({
                "key": key,
                "high": df["high"].iloc[i],
                "low": df["low"].iloc[i],
                "start": dates[i],
                "end": dates[i] + _DAY,
            })

    rows = []
    for i in range(1, len(weeks)):
        previous = weeks[i - 1]
        rows.extend(
            _pair(
                PREVIOUS_WEEK,
                previous["high"],
                previous["low"],
                weeks[i]["start"],
                weeks[i]["end"],
            )
        )
    return _frame(rows)


def compute_session_levels(h1_df):
    """The Asian, London and NY ranges, one pair per session per London day.

    h1_df: the H1 OHLC table (date, open, high, low, close), ascending,
        tz-aware UTC.

    A session's levels become visible at the END of that session, since the
    range is not known until it has finished printing, and stay visible
    through the end of the FOLLOWING London day so the next day can hunt
    them. `kind` is the session name from killzone.SESSION_HOURS.

    Sessions with no bars at all (a holiday, or a gap in the feed) are
    skipped rather than emitted with a null range.
    """
    df = h1_df.reset_index(drop=True)
    dates = pd.DatetimeIndex(df["date"])
    local_dates, hours = london_calendar(dates)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    # Days are addressed as [start, stop) slices rather than by masking the
    # whole array once per day. local_dates is ascending (tz conversion is
    # order-preserving), so one searchsorted gives every boundary, and the
    # per-day work then costs a slice instead of a 155k-element compare.
    unique_days = np.unique(local_dates)
    starts = np.searchsorted(local_dates, unique_days, side="left")
    bounds = list(starts) + [len(local_dates)]

    # A London day ends when the next one's first bar opens. Taken from the
    # data rather than computed, so a DST shift or a missing day cannot put
    # the boundary somewhere no bar exists.
    day_end = [
        dates[bounds[i + 1]] if i + 1 < len(unique_days) else dates[-1] + _HOUR
        for i in range(len(unique_days))
    ]

    rows = []
    for i in range(len(unique_days)):
        start, stop = bounds[i], bounds[i + 1]
        day_hours = hours[start:stop]
        # The last day has no following one to stay visible through, so it
        # falls back to its own end.
        expires = day_end[i + 1] if i + 1 < len(unique_days) else day_end[i]

        for kind, (start_hour, end_hour) in SESSION_HOURS.items():
            mask = (day_hours >= start_hour) & (day_hours < end_hour)
            if not mask.any():
                continue

            # Visible from the close of the session's last bar, which is the
            # open of the bar after it. The range is not knowable a moment
            # sooner.
            last_bar = start + int(np.flatnonzero(mask)[-1])
            rows.append((
                kind,
                float(highs[start:stop][mask].max()),
                float(lows[start:stop][mask].min()),
                dates[last_bar] + _HOUR,
                expires,
            ))

    rows = [row for entry in rows for row in _pair(*entry)]

    frame = _frame(rows)
    if len(frame) == 0:
        return frame
    return frame.sort_values("visible_from_date").reset_index(drop=True)
