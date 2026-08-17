"""MarketContext: everything the walk loop needs, precomputed once.

Why precompute
--------------
The walk loop is the hot path. With the Friday deadline in place a
single trade walks at most ~120 bars, but across every candidate in an
instrument-year that is still on the order of 480k bar-visits. Anything
done per-visit that could have been done once per year is paid ~480k
times over.

Two things in particular must never happen inside the loop:

1. ``df.iloc[k]`` -- constructing a pandas Series per bar costs tens of
   microseconds. The OHLC columns are extracted to flat float64 arrays
   instead, and the loop indexes those.
2. Scalar ``tz_convert`` -- roughly 2 microseconds each. Every
   London-time question the loop asks (what hour, what weekday, when is
   this bar's 19:00) is answered from a precomputed array.

Timestamps are held as datetime64[ns] in UTC with the tz metadata
stripped, so comparisons are plain int64 integer compares. The tz is not
lost information: it is UTC by construction, and every conversion back
to London civil time goes through backtest/killzone.py.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.intrabar import build_m15_index
from backtest.killzone import london_cutoff_utc, london_fields


@dataclass(frozen=True, eq=False)
class MarketContext:
    """One instrument-year, in the shape the walk loop wants.

    eq=False because the fields are numpy arrays: the generated __eq__
    would raise "truth value of an array is ambiguous" the first time
    anything compared two contexts.
    """

    df: "pd.DataFrame"          # the year slice, RangeIndex, still the source of truth
    ts: "np.ndarray"            # datetime64[ns], UTC, tz-naive
    open_: "np.ndarray"
    high: "np.ndarray"
    low: "np.ndarray"
    close: "np.ndarray"
    london_hour: "np.ndarray"   # int8
    london_dow: "np.ndarray"    # int8, Monday=0
    cutoff_ts: "np.ndarray"     # datetime64[ns] UTC: 19:00 London on this bar's London date
    pip_size: float
    m15: object                 # M15Index, or None when M15 is unavailable
    obs: object = None          # ObUniverse rebased on this window, or None


def build_market_context(year_df, pip_size, m15_df=None, obs=None):
    """Builds the MarketContext for one instrument-year.

    year_df must carry date/open/high/low/close, be sorted ascending, and
    have a RangeIndex (the walk addresses bars by integer position, so a
    non-contiguous index would silently mis-address them).

    m15_df is optional. When it is None or empty the context's m15 is
    None and the walk degrades to the pessimistic branch on ambiguous
    bars, which is intended behaviour rather than a failure.

    obs is an ObUniverse ALREADY REBASED onto this window (see
    ob_state.slice_universe). It is not sliced here, because this function
    only sees the windowed frame and cannot know its offset into full
    history. None means no OB state, which the signal engine treats as
    "no candidates" rather than an error.
    """
    year_df = year_df.reset_index(drop=True)

    dates = pd.DatetimeIndex(year_df["date"])
    if dates.tz is None:
        dates = dates.tz_localize("UTC")
    else:
        dates = dates.tz_convert("UTC")

    ts = dates.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    london_hour, london_dow = london_fields(dates)

    # 19:00 London on each bar's own London civil date. Derived by
    # localising normalised local midnight and adding 19 hours in WALL
    # time (see killzone.py) -- never by +24h arithmetic, which drifts an
    # hour across a DST transition.
    cutoff_ts = (
        london_cutoff_utc(dates)
        .dt.tz_localize(None)
        .to_numpy(dtype="datetime64[ns]")
    )

    return MarketContext(
        df=year_df,
        ts=ts,
        open_=year_df["open"].to_numpy(dtype=np.float64),
        high=year_df["high"].to_numpy(dtype=np.float64),
        low=year_df["low"].to_numpy(dtype=np.float64),
        close=year_df["close"].to_numpy(dtype=np.float64),
        london_hour=london_hour,
        london_dow=london_dow,
        cutoff_ts=cutoff_ts,
        pip_size=pip_size,
        m15=build_m15_index(dates, m15_df),
        obs=obs,
    )


def bar_timestamp(ctx, k):
    """Bar k's timestamp as a tz-aware UTC pd.Timestamp.

    The context stores tz-naive UTC for comparison speed; anything that
    leaves the engine (journal rows, the London-clock helpers) wants the
    tz back on it.
    """
    return pd.Timestamp(ctx.ts[k], tz="UTC")
