"""Full historical OHLC pull from Dukascopy for all 11 instruments across
Daily/H4/H1, with incremental refresh support.

Run scripts/probe_dukascopy_history.py first to know the earliest usable
start date per instrument/granularity; hardcode that here once known, or
pass a conservative EARLIEST_START and let empty leading months just
produce no rows (fetch_candles handles that fine, they simply are not
written).
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, ".")
from data.dukascopy_client import INSTRUMENTS, GRANULARITIES, fetch_candles

RAW_DIR = "data/raw"
EARLIEST_START = datetime(2003, 1, 1, tzinfo=timezone.utc)
CHUNK_DAYS = 180


def output_path(instrument_key, granularity_key):
    return os.path.join(RAW_DIR, f"{instrument_key}_{granularity_key}.parquet")


def sanity_check(df, instrument_key, granularity_key):
    if df.empty:
        return
    if df["date"].duplicated().any():
        print(f"WARNING {instrument_key} {granularity_key}: duplicate timestamps")
    if not df["date"].is_monotonic_increasing:
        print(f"WARNING {instrument_key} {granularity_key}: timestamps not ascending")
    if df[["open", "high", "low", "close"]].isna().all(axis=1).any():
        print(f"WARNING {instrument_key} {granularity_key}: fully-null OHLC rows")


def fetch_range_in_chunks(instrument_key, granularity_key, start, end):
    chunks = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), end)
        chunk = fetch_candles(instrument_key, granularity_key, cursor, chunk_end)
        if not chunk.empty:
            chunks.append(chunk)
        cursor = chunk_end
    if not chunks:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    return pd.concat(chunks, ignore_index=True).drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def fetch_one(instrument_key, granularity_key, end):
    path = output_path(instrument_key, granularity_key)

    if os.path.exists(path):
        existing = pd.read_parquet(path)
        start = existing["date"].max() + timedelta(seconds=1)
        new_rows = fetch_range_in_chunks(instrument_key, granularity_key, start, end)
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    else:
        combined = fetch_range_in_chunks(instrument_key, granularity_key, EARLIEST_START, end)

    sanity_check(combined, instrument_key, granularity_key)
    combined.to_parquet(path, index=False)
    print(f"{instrument_key:10s} {granularity_key:3s}  {len(combined):8d} rows -> {path}")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    for instrument_key in INSTRUMENTS:
        for granularity_key in GRANULARITIES:
            fetch_one(instrument_key, granularity_key, end)


if __name__ == "__main__":
    main()
