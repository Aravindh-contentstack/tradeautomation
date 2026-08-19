"""The one expression of "when does a higher-timeframe row become readable".

Every detector in this project runs on its own timeframe's candles and
numbers its output by that timeframe's own row positions. The backtest walks
a single H1-indexed frame. Reconciling those two coordinate systems is where
lookahead gets in, and the rule that keeps it out is one line:

    searchsorted(h1_ts, tf_date + tf_duration, "left")

A Daily row is visible to an H1 row only once that Daily candle has CLOSED,
and a candle's close is its open plus its own duration. This is the numpy
mirror of backtest/pipeline.py's merge_asof(left_on="date",
right_on="close_time", direction="backward"), and the two must not drift.

Extracted here from smc/order_blocks/ob_state.py, which had it first and
still documents the reasoning at length. It moved because the liquidity
state needs the identical rule, and a second copy is exactly the drift
ob_state.py's own docstring warns about.
"""

import numpy as np
import pandas as pd

DAILY_DURATION = pd.Timedelta(days=1)
H4_DURATION = pd.Timedelta(hours=4)
H1_DURATION = pd.Timedelta(hours=1)

TIMEFRAME_DURATIONS = {
    "Daily": DAILY_DURATION,
    "4H": H4_DURATION,
    "H1": H1_DURATION,
}


def to_h1_index(values, tf_dates, h1_ts, tf_duration, n, missing):
    """Maps a column of timeframe-local ROW INDICES to H1 positions.

    A local index i means "that timeframe's candle i", which finishes at
    tf_dates[i] + tf_duration and is therefore readable from the first H1
    bar at or after that instant. Missing entries (never mitigated, never
    swept) map to `missing`, chosen by the caller so the resulting array
    can be compared against a bar number without a null check.
    """
    out = np.full(len(values), missing, dtype=np.int64)
    for row, value in enumerate(values):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        close_time = tf_dates[int(value)] + tf_duration
        out[row] = min(int(np.searchsorted(h1_ts, close_time, side="left")), n)
    return out


def dates_to_h1_index(dates, h1_ts, n, missing):
    """Maps a column of INSTANTS to H1 positions.

    The sibling of to_h1_index, for levels that carry a timestamp rather
    than a row on some timeframe. The time-based levels in
    smc/liquidity/time_levels.py are all of this kind: "yesterday's high is
    live from 00:00" is a fact about a clock, not about a candle index.

    The instants those detectors emit are already close times (a session's
    level is dated from the close of its last bar, a previous-day level
    from the open of the day it applies to, which is the previous day's
    close), so no duration is added here. Anything at or after the instant
    is visible; a bar opening exactly on it counts, which is why the search
    is "left".
    """
    stamps = pd.DatetimeIndex(dates)
    positions = pd.DatetimeIndex(h1_ts).searchsorted(stamps, side="left")
    out = np.minimum(positions.astype(np.int64), n)
    out[stamps.isna()] = missing
    return out
