"""M1 pull for the 10 world equity indices, from 2015 onward.

WHY M1 EXISTS AT ALL. It is not an analysis timeframe -- nothing in the
backtest reads M1. It is the raw material for M3. Dukascopy has no 3-minute
interval (the minute intervals it offers are 1, 5, 10, 15 and 30), so the only
way to get an M3 series is to pull M1 and resample it, which
scripts/build_index_m3.py does. That is the whole reason this script is
separate from scripts/fetch_historical_data.py, which handles the D/H4/H1/M15
tiers that Dukascopy serves directly.

WHY 30-DAY CHUNKS AND NOT 180. fetch_historical_data.py uses 180-day chunks
and that is right for its granularities, but Dukascopy serves M1 out of
per-HOUR files. A 180-day M1 request is therefore ~4,300 underlying file
fetches inside a single fetch_candles() call, and that is exactly where a
retry budget exhausts and takes the whole chunk with it. 30 days is ~720
files, which completes reliably and checkpoints roughly six times more often
-- so a failure costs a month of work, not six.

WHY WORKERS ARE SPLIT BY INSTRUMENT. Sequentially this pull is ~5 hours
(benchmarked at 13.3s per instrument-month). The parallelism is
instrument-partitioned rather than chunk-partitioned on purpose: each worker
process gets a disjoint subset of INDEX_KEYS and therefore writes only its own
instruments' parquet files. No two processes ever touch the same path, so
there is no shared state, no lock, and no way for two writers to interleave a
checkpoint. Resume still works exactly as it does in the sequential script,
because resume state lives in the output file itself.

Everything else is deliberately the same as fetch_historical_data.py: the
_normalise de-duplicate-and-sort, the tmp-then-os.replace() checkpoint (atomic
on the same filesystem, so a kill mid-write cannot leave a truncated parquet
that would poison every later resume), the resume-from-existing-max-date, and
per-instrument failures printed and collected rather than aborting the run.

Usage:
    .venv/bin/python scripts/fetch_index_m1.py --workers 3
    .venv/bin/python scripts/fetch_index_m1.py --instrument SP500

Rerun to resume; each instrument picks up from its last checkpoint.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, ".")
from data.dukascopy_client import INDEX_KEYS, fetch_candles

RAW_DIR = "data/raw"
GRANULARITY = "M1"

# Dukascopy only carries these indices from 2015; earlier requests simply
# return nothing, so this is a real floor rather than a conservative guess.
EARLIEST_START = datetime(2015, 1, 1, tzinfo=timezone.utc)

# See the module docstring: M1 is served from per-hour files, so chunk size
# translates directly into files-per-request.
CHUNK_DAYS = 30

MAX_RETRIES = 10
CHUNK_PAUSE_SECONDS = 0.25

EMPTY_COLUMNS = ["date", "open", "high", "low", "close"]


def output_path(instrument_key):
    return os.path.join(RAW_DIR, f"{instrument_key}_{GRANULARITY}.parquet")


def _normalise(df):
    """De-duplicate and sort; the chunk boundaries can overlap by a bar."""
    return (
        df.drop_duplicates(subset="date")
        .sort_values("date")
        .reset_index(drop=True)
    )


def checkpoint(df, path):
    """Atomically replace the output parquet with df.

    Writing straight to `path` would leave a half-written, unreadable file if
    the process is killed mid-write -- and since resume reads that same file,
    a corrupt checkpoint would poison every later rerun.
    """
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def sanity_check(df, instrument_key):
    if df.empty:
        print(f"WARNING {instrument_key} M1: no rows at all")
        return
    if df["date"].duplicated().any():
        print(f"WARNING {instrument_key} M1: duplicate timestamps")
    if not df["date"].is_monotonic_increasing:
        print(f"WARNING {instrument_key} M1: timestamps not ascending")
    if df[["open", "high", "low", "close"]].isna().all(axis=1).any():
        print(f"WARNING {instrument_key} M1: fully-null OHLC rows")


def fetch_one(instrument_key, end):
    path = output_path(instrument_key)

    if os.path.exists(path):
        combined = pd.read_parquet(path)
        start = combined["date"].max() + timedelta(seconds=1)
    else:
        combined = pd.DataFrame(columns=EMPTY_COLUMNS)
        start = EARLIEST_START

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), end)
        chunk = fetch_candles(
            instrument_key, GRANULARITY, cursor, chunk_end,
            max_retries=MAX_RETRIES,
        )
        if not chunk.empty:
            combined = _normalise(pd.concat([combined, chunk], ignore_index=True))
            # Checkpoint after every chunk, not at the end: this is the whole
            # resume story.
            checkpoint(combined, path)
        cursor = chunk_end
        time.sleep(CHUNK_PAUSE_SECONDS)

    sanity_check(combined, instrument_key)
    # Final write covers the "already up to date, no chunks fetched" case,
    # where the file may not exist yet at all.
    checkpoint(combined, path)
    print(f"{instrument_key:8s} M1  {len(combined):9d} rows -> {path}", flush=True)
    return len(combined)


def run_subset(instrument_keys, end):
    """One worker process: pull this disjoint subset of instruments in order.

    Returns the list of (instrument, error-string) failures so the parent can
    summarise them; an exception on one instrument must not lose the rest of
    the subset.
    """
    failures = []
    for instrument_key in instrument_keys:
        started = time.time()
        print(f"START {instrument_key} M1 (pid {os.getpid()})", flush=True)
        try:
            fetch_one(instrument_key, end)
            print(
                f"DONE  {instrument_key} M1 in {time.time() - started:.0f}s",
                flush=True,
            )
        except Exception as err:
            print(
                f"FAILED {instrument_key} M1: {err} "
                f"(rerun to resume from checkpoint)",
                flush=True,
            )
            failures.append((instrument_key, str(err)))
    return failures


def split_round_robin(keys, n):
    """Split keys into n disjoint subsets, round-robin.

    Round-robin rather than contiguous slices so the part-day indices (which
    have far fewer bars per day) end up spread across workers instead of
    piling into one, which keeps the workers finishing at roughly the same
    time.
    """
    subsets = [[] for _ in range(n)]
    for i, key in enumerate(keys):
        subsets[i % n].append(key)
    return [s for s in subsets if s]


def main():
    parser = argparse.ArgumentParser(
        description="Pull M1 data for the world equity indices (raw material "
                    "for M3, which Dukascopy cannot serve directly)."
    )
    parser.add_argument("--workers", type=int, default=3,
                        help="parallel worker processes, split by instrument "
                             "(default 3)")
    parser.add_argument("--instrument", default=None,
                        help="pull a single instrument instead of all 10")
    args = parser.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    if args.instrument:
        if args.instrument not in INDEX_KEYS:
            print(f"Unknown index key {args.instrument!r}; "
                  f"expected one of {', '.join(INDEX_KEYS)}")
            return 2
        keys = [args.instrument]
    else:
        keys = list(INDEX_KEYS)

    workers = max(1, min(args.workers, len(keys)))
    print(f"Pulling M1 for {len(keys)} index/indices from "
          f"{EARLIEST_START.date()} to {end.date()} "
          f"in {CHUNK_DAYS}-day chunks, {workers} worker(s).", flush=True)

    failures = []
    if workers == 1:
        failures = run_subset(keys, end)
    else:
        subsets = split_round_robin(keys, workers)
        for subset in subsets:
            print(f"  worker subset: {', '.join(subset)}", flush=True)
        with ProcessPoolExecutor(max_workers=len(subsets)) as pool:
            futures = [pool.submit(run_subset, subset, end) for subset in subsets]
            for future in futures:
                failures.extend(future.result())

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} instrument(s) failed:")
        for instrument_key, err in failures:
            print(f"  {instrument_key:8s} {err}")
        print("Rerun this script to resume each from its last checkpoint.")
        return 1

    print(f"All {len(keys)} index M1 pull(s) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
