"""Full historical OHLC pull from Dukascopy, with incremental refresh and
resume-on-crash.

Two things drive the shape of this script:

1. The download plan is explicit, not a cross-product. Daily/H4/H1 are pulled
   for all 39 keys in data.dukascopy_client.INSTRUMENTS (27 FX pairs, XAU_USD,
   NAS100 and the 10 world equity indices), while M15 is pulled for the 38
   keys in backtest/instruments.py PIP_SIZES, which is everything except
   NAS100. NAS100 is a point-based index CFD that no data was pulled for under
   the index plan and the backtest does not support it, so pulling ~620k M15
   rows for it would be dead weight. PIP_SIZES is imported rather than
   re-listed so that instrument list has exactly one definition in the repo.

   M1 is deliberately NOT in this plan. It is downloaded by
   scripts/fetch_index_m1.py, and adding it here would download it twice.

2. Every 180-day chunk is checkpointed to disk. The M15 pull is roughly 47
   chunks per instrument; before this, one exhausted retry at chunk 46 threw
   away the whole instrument's work. fetch_one already resumes from an
   existing file (start = existing["date"].max() + 1s), so checkpointing to
   the *real* output path gives resume for free -- no manifest, no state file,
   no progress DB. The write goes to path + ".tmp" first and is then moved
   with os.replace(), which is atomic on the same filesystem, so a kill
   mid-write cannot leave a truncated parquet behind.

A failure on one (instrument, granularity) pair is printed and skipped rather
than aborting the run, and the failures are summarised at the end. Rerunning
the script picks each one up from its last checkpoint.

Run scripts/probe_dukascopy_history.py first to know the earliest usable start
date per instrument/granularity; EARLIEST_START below is deliberately
conservative and empty leading months simply produce no rows.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, ".")
from data.dukascopy_client import INDEX_KEYS, INSTRUMENTS, fetch_candles
from backtest.instruments import PIP_SIZES

RAW_DIR = "data/raw"
EARLIEST_START = datetime(2003, 1, 1, tzinfo=timezone.utc)

# The equity indices have no usable history before 2015 on this feed, so
# starting them at 2003 would just burn ~24 empty chunks per instrument.
INDEX_START = datetime(2015, 1, 1, tzinfo=timezone.utc)
INSTRUMENT_START = {key: INDEX_START for key in INDEX_KEYS}

CHUNK_DAYS = 180

# Dukascopy's public feed occasionally 5xx's or stalls under a long sequential
# pull. 10 retries (library default is 7) plus a short pause between chunks
# costs ~2 minutes on a 15-25 minute run and materially cuts mid-run failures.
MAX_RETRIES = 10
CHUNK_PAUSE_SECONDS = 0.25

EMPTY_COLUMNS = ["date", "open", "high", "low", "close"]


def download_plan():
    """The explicit (instrument, granularity) list to pull, in order.

    D/H4/H1 first for everything so the cheap data lands before the long M15
    pass, which is where a crash is actually likely.
    """
    plan = []
    for instrument_key in INSTRUMENTS:
        for granularity_key in ("D", "H4", "H1"):
            plan.append((instrument_key, granularity_key))
    for instrument_key in PIP_SIZES:
        plan.append((instrument_key, "M15"))
    return plan


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


def fetch_one(instrument_key, granularity_key, end):
    path = output_path(instrument_key, granularity_key)

    if os.path.exists(path):
        combined = pd.read_parquet(path)
        start = combined["date"].max() + timedelta(seconds=1)
    else:
        combined = pd.DataFrame(columns=EMPTY_COLUMNS)
        start = INSTRUMENT_START.get(instrument_key, EARLIEST_START)

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), end)
        chunk = fetch_candles(
            instrument_key, granularity_key, cursor, chunk_end,
            max_retries=MAX_RETRIES,
        )
        if not chunk.empty:
            combined = _normalise(pd.concat([combined, chunk], ignore_index=True))
            # Checkpoint after every chunk, not at the end: this is the whole
            # resume story.
            checkpoint(combined, path)
        cursor = chunk_end
        time.sleep(CHUNK_PAUSE_SECONDS)

    sanity_check(combined, instrument_key, granularity_key)
    # Final write covers the "already up to date, no chunks fetched" case,
    # where the file may not exist yet at all.
    checkpoint(combined, path)
    print(f"{instrument_key:10s} {granularity_key:3s}  {len(combined):8d} rows -> {path}")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    failures = []
    for instrument_key, granularity_key in download_plan():
        try:
            fetch_one(instrument_key, granularity_key, end)
        except Exception as err:
            print(
                f"FAILED {instrument_key} {granularity_key}: {err} "
                f"(rerun to resume from checkpoint)"
            )
            failures.append((instrument_key, granularity_key, err))

    if failures:
        print(f"\n{len(failures)} pair(s) failed:")
        for instrument_key, granularity_key, err in failures:
            print(f"  {instrument_key:10s} {granularity_key:3s}  {err}")
        print("Rerun this script to resume each from its last checkpoint.")
    else:
        print("\nAll pairs completed.")


if __name__ == "__main__":
    main()
