"""Loads one instrument's Daily/H4/H1 history, runs the existing
swing_structure detectors and premium/discount zones on each, and merges
Daily and H4 state onto the H1 timeline for the backtest engine.

Reuses swing_structure/daily_structure.py, h4_structure.py, h1_structure.py
and premium_discount.py as-is: this module only loads data and merges
timeframes, it does not reimplement any structure/zone logic.
"""

import pandas as pd

from swing_structure.daily_structure import compute_daily_structures
from swing_structure.h4_structure import compute_h4_structures
from swing_structure.h1_structure import compute_h1_structures
from swing_structure.premium_discount import (
    compute_daily_premium_discount,
    compute_h4_premium_discount,
    compute_h1_premium_discount,
)

DAILY_DURATION = pd.Timedelta(days=1)
H4_DURATION = pd.Timedelta(hours=4)


def _load_raw(instrument, granularity):
    path = "data/raw/%s_%s.parquet" % (instrument, granularity)
    return pd.read_parquet(path)


def build_instrument_pipeline(instrument):
    """Returns one H1-indexed DataFrame carrying every Daily/H4/H1 factor
    column the strategy needs, with Daily/H4 state merged in without
    lookahead (only fully-closed higher-timeframe candles are visible to
    each H1 row).
    """
    daily_df = _load_raw(instrument, "D")
    h4_df = _load_raw(instrument, "H4")
    h1_df = _load_raw(instrument, "H1")
    return build_pipeline_from_frames(daily_df, h4_df, h1_df)


def build_pipeline_from_frames(daily_df, h4_df, h1_df):
    """Same merge/no-lookahead logic as build_instrument_pipeline, but
    takes already-loaded Daily/H4/H1 DataFrames (date, open, high, low,
    close, ascending, only fully-closed candles) instead of reading the
    backtest's Parquet files. Used by live/ so the live signal engine
    runs through the identical code path as the backtest rather than a
    parallel reimplementation.
    """
    daily_df = compute_daily_structures(daily_df)
    daily_df = compute_daily_premium_discount(daily_df)

    h4_df = compute_h4_structures(h4_df)
    h4_df = compute_h4_premium_discount(h4_df)

    h1_df = compute_h1_structures(h1_df)
    h1_df = compute_h1_premium_discount(h1_df)

    # Each higher-timeframe row's CLOSE time, not its open time, is what
    # must be <= an H1 row's own time for that row to be visible without
    # lookahead: a Daily candle opening the same morning as an H1 signal
    # hasn't closed yet and must not be readable by that signal.
    daily_df = daily_df.sort_values("date").reset_index(drop=True)
    daily_df["date"] = daily_df["date"].astype("datetime64[ns, UTC]")
    daily_df["close_time"] = daily_df["date"] + DAILY_DURATION

    h4_df = h4_df.sort_values("date").reset_index(drop=True)
    h4_df["date"] = h4_df["date"].astype("datetime64[ns, UTC]")
    h4_df["close_time"] = h4_df["date"] + H4_DURATION

    h1_df = h1_df.sort_values("date").reset_index(drop=True)
    h1_df["date"] = h1_df["date"].astype("datetime64[ns, UTC]")

    # Excludes raw OHLC and the raw "date" (open time) columns: only the
    # already-prefixed structure/zone columns and "close_time" (the merge
    # key) are carried over, so nothing collides with h1_df's own "date".
    daily_cols = [c for c in daily_df.columns if c not in ("date", "open", "high", "low", "close")]
    h4_cols = [c for c in h4_df.columns if c not in ("date", "open", "high", "low", "close")]

    merged = pd.merge_asof(
        h1_df,
        daily_df[daily_cols],
        left_on="date",
        right_on="close_time",
        direction="backward",
    )
    merged = merged.drop(columns=["close_time"])

    merged = pd.merge_asof(
        merged,
        h4_df[h4_cols],
        left_on="date",
        right_on="close_time",
        direction="backward",
    )
    merged = merged.drop(columns=["close_time"])

    return merged
