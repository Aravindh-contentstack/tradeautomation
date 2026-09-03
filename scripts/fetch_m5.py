"""M5 pull for every instrument in data.dukascopy_client.INSTRUMENTS, from
2012 onward: the 27 live FX pairs, the 5 metals (XAU_USD plus the four
data-only ones -- silver, palladium, platinum, copper) and the 11 world
equity indices (the ten in INDEX_KEYS plus NAS100). 43 instruments in all.

WHY M5 IS PULLED FOR EVERYTHING AT ONCE. Unlike the D/H4/H1/M15 plan in
scripts/fetch_historical_data.py, which splits its instrument list by
whether a key is in PIP_SIZES, M5 has no such split: it is a data-only tier
(nothing in backtest/ or live/ reads it yet) requested across the whole
board, so the plan is simply "every key in INSTRUMENTS". Iterating
INSTRUMENTS rather than re-listing 43 strings keeps that list with exactly
one definition in the repo.

WHY M5 IS NOT IN scripts/fetch_historical_data.py. That script's download
plan is frozen and keyed on PIP_SIZES for its M15 pass; wiring a 43rd-wide
M5 pass into it would drag this pure data grab into a file whose shape is
tied to trading-model decisions. Same reasoning that keeps fetch_index_m1.py
and fetch_metals.py separate.

WHY M5 NEEDS NO RESAMPLING. M1 exists only as raw material for M3, because
Dukascopy has no 3-minute interval. M5 IS one of the minute intervals the
feed serves directly (1, 5, 10, 15, 30), so this script fetches it the same
way fetch_historical_data.py fetches M15 -- no build step, no companion
resampler.

WHY 90-DAY CHUNKS. The feed paginates candles 30,000 at a time. A 180-day
M5 request for a 24/5 pair is ~44,000 bars, i.e. two paginated fetches per
chunk; 90 days is ~22,000, a single fetch, and it checkpoints twice as
often. M15's 180-day chunk stays a single fetch because M15 is a third the
density -- the chunk size tracks bar density, not calendar span.

WHY 2012. Probing the M5 feed returns nothing for June 2003/2007/2010/2011
and real bars from June 2012 for FX pairs, indices and metals alike -- the
same floor fetch_index_m1.py found for M1. 2012-01-01 is a hair below the
first live month; the empty leading chunks simply produce no rows.

WHY WORKERS ARE SPLIT BY INSTRUMENT. Each worker process gets a disjoint
subset of the instrument list and writes only its own instruments' parquet
files, so no two processes ever touch the same path: no lock, no shared
state, no interleaved checkpoint. Resume state lives in the output file
itself (start = existing max date + 1s), so rerunning after a crash picks
each instrument up where it stopped. Copied wholesale from
scripts/fetch_index_m1.py, which is where this pattern was first proven.

This is a DATA-ONLY pull. Nothing in backtest/ or live/ reads M5.

Usage:
    .venv/bin/python scripts/fetch_m5.py --workers 3
    .venv/bin/python scripts/fetch_m5.py --instrument EUR_USD

Rerun to resume; each instrument picks up from its last checkpoint.
"""

import argparse
import os
import socket
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

# dukascopy_python calls requests.get with no timeout, so a single stalled
# TCP read hangs a worker forever -- the library's retry budget never trips
# because the request never returns or raises. A process-wide default socket
# timeout turns that stall into a normal exception, which the library's
# retry loop then handles like any other transient feed error. Seen live on
# 2026-09-03: two workers wedged for over an hour on one chunk each.
socket.setdefaulttimeout(120)

sys.path.insert(0, ".")
from data.dukascopy_client import INSTRUMENTS, fetch_candles

RAW_DIR = "data/raw"
GRANULARITY = "M5"

# The M5 feed returns nothing before 2012 and real bars from mid-2012 for
# every instrument class here; see the module docstring. A hair below the
# first live month -- empty leading chunks just produce no rows.
EARLIEST_START = datetime(2012, 1, 1, tzinfo=timezone.utc)

# See the module docstring: chunk size tracks bar density against the feed's
# 30,000-candle pagination limit, not calendar span.
CHUNK_DAYS = 90

MAX_RETRIES = 10
CHUNK_PAUSE_SECONDS = 0.25

EMPTY_COLUMNS = ["date", "open", "high", "low", "close"]


def output_path(instrument_key):
    return os.path.join(RAW_DIR, f"{instrument_key}_{GRANULARITY}.parquet")


def _normalise(df):
    """De-duplicate, sort, and pin the date column to a real datetime dtype.

    The coercion matters: `combined` starts life as an object-dtype empty
    frame (EMPTY_COLUMNS), and pandas 3.x no longer drops an empty operand
    from pd.concat, so the first concat would otherwise leave `date` as
    object -- which sort/dedupe tolerate but the `.dt` minute check in
    sanity_check does not.
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
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
        print(f"WARNING {instrument_key} M5: no rows at all")
        return
    if df["date"].duplicated().any():
        print(f"WARNING {instrument_key} M5: duplicate timestamps")
    if not df["date"].is_monotonic_increasing:
        print(f"WARNING {instrument_key} M5: timestamps not ascending")
    if df[["open", "high", "low", "close"]].isna().all(axis=1).any():
        print(f"WARNING {instrument_key} M5: fully-null OHLC rows")
    bad_minute = df["date"].dt.minute % 5 != 0
    if bad_minute.any():
        print(f"WARNING {instrument_key} M5: {int(bad_minute.sum())} bars "
              f"not on a 5-minute boundary")


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
    print(f"{instrument_key:10s} M5  {len(combined):10d} rows -> {path}",
          flush=True)
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
        print(f"START {instrument_key} M5 (pid {os.getpid()})", flush=True)
        try:
            fetch_one(instrument_key, end)
            print(
                f"DONE  {instrument_key} M5 in {time.time() - started:.0f}s",
                flush=True,
            )
        except Exception as err:
            print(
                f"FAILED {instrument_key} M5: {err} "
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
        description="Pull M5 data for every instrument in INSTRUMENTS "
                    "(27 FX pairs, 5 metals, 11 equity indices). Data-only."
    )
    parser.add_argument("--workers", type=int, default=3,
                        help="parallel worker processes, split by instrument "
                             "(default 3)")
    parser.add_argument("--instrument", default=None,
                        help="pull a single instrument instead of all 43")
    args = parser.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    if args.instrument:
        if args.instrument not in INSTRUMENTS:
            print(f"Unknown instrument {args.instrument!r}; "
                  f"expected one of {', '.join(INSTRUMENTS)}")
            return 2
        keys = [args.instrument]
    else:
        keys = list(INSTRUMENTS)

    workers = max(1, min(args.workers, len(keys)))
    print(f"Pulling M5 for {len(keys)} instrument(s) from "
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
            print(f"  {instrument_key:10s} {err}")
        print("Rerun this script to resume each from its last checkpoint.")
        return 1

    print(f"All {len(keys)} M5 pull(s) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
