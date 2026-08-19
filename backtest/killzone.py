"""London civil-time clock: killzone session gate and trade-management deadlines.

"UTC+1" in the strategy spec means British civil time (GMT in winter,
BST in summer), confirmed directly by the user, not a fixed offset and
not CET/CEST. Everything in the engine that asks "what day is it" or
"has 19:00 passed" must ask it in London terms, because that is the
clock the user actually trades against.

Every conversion in this module lives here rather than being re-inlined
at each call site, so there is exactly one place where a DST bug could
hide.

The DST rule that matters
-------------------------
Adding a Timedelta to a *tz-aware* pandas Timestamp is ABSOLUTE
(elapsed-time) arithmetic, not wall-clock arithmetic. So
``midnight_london + 19h`` lands on 18:00 or 20:00 local on the two days
a year the clocks change, not 19:00. Every cutoff below is therefore
built the only safe way: do the hour arithmetic on a NAIVE local
timestamp, then localise the result to Europe/London. 19:00 local is
never ambiguous and never non-existent (the UK transitions happen at
01:00 and 02:00), so the localisation is unambiguous by construction.

The same reasoning forbids "+24h" to step a daily deadline forward: use
next_london_cutoff, which steps one *calendar* day in local wall time.
"""

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LONDON_TZ = ZoneInfo("Europe/London")

LONDON_START_HOUR = 7
LONDON_END_HOUR = 10
NY_START_HOUR = 12
NY_END_HOUR = 15

# The Asian range, midnight to 04:00 London. Unlike the two above it is NOT
# a killzone: nothing is ever entered during it. It is here because its high
# and low are liquidity the London and NY sessions go hunting for, which
# smc/liquidity/time_levels.py reads, and because putting a fourth session
# anywhere else would mean a second London clock in the codebase.
ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 4

# Every session as (start, end) in London civil hours, end-exclusive.
# Ordered as they occur in the trading day.
SESSION_HOURS = {
    "asian": (ASIAN_START_HOUR, ASIAN_END_HOUR),
    "london": (LONDON_START_HOUR, LONDON_END_HOUR),
    "ny": (NY_START_HOUR, NY_END_HOUR),
}

# 19:00 London is the user's hard trade-management checkpoint: every open
# position is either moved to breakeven (if in profit) or cut (if not).
LONDON_CUTOFF_HOUR = 19

# Monday=0, so Friday=4. Friday's 19:00 checkpoint is also the weekly
# deadline: nothing is ever carried over a weekend.
FRIDAY = 4


def to_london(ts):
    """UTC-aware timestamp (or Series/DatetimeIndex of them) -> London civil time."""
    if isinstance(ts, pd.Series):
        return ts.dt.tz_convert(LONDON_TZ)
    return ts.tz_convert(LONDON_TZ)


def london_fields(dates):
    """Vectorised (hour, weekday) in London civil time, as int8 arrays.

    Scalar tz_convert costs roughly 2 microseconds, which is nothing on
    its own but dominates a walk loop that visits ~480k bars per
    instrument-year. Everything the loop needs is computed once, here.
    """
    local = to_london(pd.Series(pd.DatetimeIndex(dates)))
    hour = local.dt.hour.to_numpy(dtype=np.int8)
    dow = local.dt.dayofweek.to_numpy(dtype=np.int8)
    return hour, dow


def london_calendar(dates):
    """Vectorised (London civil date, hour) for a series of UTC instants.

    The date half is what session grouping needs and london_fields does not
    provide: an H1 bar at 23:00 UTC in summer belongs to the NEXT London
    day, and grouping on the UTC date would put it in the wrong session
    day exactly often enough to be hard to spot.

    Returns (dates as a datetime64[ns] numpy array of local midnights,
    hours as int8).
    """
    local = to_london(pd.Series(pd.DatetimeIndex(dates))).dt.tz_localize(None)
    return local.dt.normalize().to_numpy(), local.dt.hour.to_numpy(dtype=np.int8)


def london_cutoff_utc(dates):
    """Vectorised: for each bar, the UTC instant of 19:00 London on THAT
    BAR'S London civil date. Returns a tz-aware UTC Series.

    Built as "normalise to local midnight, add 19 hours in wall time,
    then localise" -- see the module docstring for why the obvious
    alternatives are wrong.
    """
    local_naive = to_london(pd.Series(pd.DatetimeIndex(dates))).dt.tz_localize(None)
    cutoff_naive = local_naive.dt.normalize() + pd.Timedelta(hours=LONDON_CUTOFF_HOUR)
    return cutoff_naive.dt.tz_localize(LONDON_TZ).dt.tz_convert("UTC")


def london_cutoff_for(ts):
    """Scalar form of london_cutoff_utc: 19:00 London on ts's London date."""
    local_naive = to_london(ts).tz_localize(None)
    cutoff_naive = local_naive.normalize() + pd.Timedelta(hours=LONDON_CUTOFF_HOUR)
    return cutoff_naive.tz_localize(LONDON_TZ).tz_convert("UTC")


def next_london_cutoff(cutoff_utc):
    """The next daily checkpoint after cutoff_utc: 19:00 London on the
    following London calendar day.

    Stepping one calendar day in local wall time, not +24h in absolute
    time. On the two DST days a year those differ by an hour.
    """
    local_naive = to_london(cutoff_utc).tz_localize(None)
    next_naive = (
        local_naive.normalize()
        + pd.Timedelta(days=1)
        + pd.Timedelta(hours=LONDON_CUTOFF_HOUR)
    )
    return next_naive.tz_localize(LONDON_TZ).tz_convert("UTC")


def friday_cutoff_for(ts):
    """The weekly deadline for the London trading week containing ts:
    19:00 London on that week's Friday.

    A Friday entry gets the same day's 19:00 (days_ahead == 0), which is
    the "a Friday entry closes the same day" half of the user's rule. A
    Sunday-evening bar (the forex week opens Sunday ~22:00 London) maps
    forward five days to the coming Friday, which is correct: it belongs
    to the week that is only just starting.
    """
    local = to_london(ts)
    days_ahead = (FRIDAY - local.weekday()) % 7
    friday_naive = (
        local.tz_localize(None).normalize()
        + pd.Timedelta(days=days_ahead)
        + pd.Timedelta(hours=LONDON_CUTOFF_HOUR)
    )
    return friday_naive.tz_localize(LONDON_TZ).tz_convert("UTC")


def session_for(utc_timestamp):
    """Returns "london", "ny", or None for a UTC-aware timestamp, based on
    its British civil-time hour.
    """
    local_hour = to_london(utc_timestamp).hour
    if LONDON_START_HOUR <= local_hour < LONDON_END_HOUR:
        return "london"
    if NY_START_HOUR <= local_hour < NY_END_HOUR:
        return "ny"
    return None
