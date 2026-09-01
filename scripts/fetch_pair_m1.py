"""M1 pull for the 27 live FX pairs, from 2015 onward.

WHY M1 EXISTS AT ALL. Same reason as for the equity indices: it is not an
analysis timeframe, it is the raw material for M3. Dukascopy has no 3-minute
interval (the minute intervals it offers are 1, 5, 10, 15 and 30), so an M3
series has to be constructed by resampling M1, which scripts/build_pair_m3.py
does.

WHY THIS SCRIPT IS A THIN WRAPPER. scripts/fetch_index_m1.py already solved
every hard part of an M1 pull -- the 30-day chunking (Dukascopy serves M1 out
of per-hour files, so a 180-day request is ~4,300 underlying fetches and is
exactly where a retry budget exhausts), the per-chunk atomic checkpoint that
makes the run resumable, and the instrument-partitioned worker pool where no
two processes ever touch the same parquet path. None of that is
index-specific, so this script imports it wholesale and supplies a different
instrument list, exactly the way scripts/fetch_cross_pairs.py reuses
scripts/fetch_historical_data.py.

WHY 2015 AND NOT EARLIER. Unlike the D/H4/H1/M15 tiers, which reach back to
2003-2007 for these pairs, Dukascopy's M1 history starts around mid-2012.
Probing confirmed January 2012 and earlier return nothing while July 2012
onward works. 2015 is the requested floor and sits comfortably inside the
available range; EARLIEST_START is inherited from fetch_index_m1.py, which
already uses 2015.

WHY THE PAIR LIST IS IMPORTED. live/pairs.py is the single source of truth for
which instruments actually run, so re-listing 27 strings here would create a
second definition that could drift. XAU_USD is not in PAIRS (it was dropped
from the live list) and so is deliberately not pulled.

This is a DATA-ONLY pull. Nothing in backtest/ or live/ reads M1 or M3.

Usage:
    .venv/bin/python scripts/fetch_pair_m1.py --workers 3
    .venv/bin/python scripts/fetch_pair_m1.py --instrument EUR_USD

Rerun to resume; each pair picks up from its last checkpoint.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from live.pairs import PAIRS
from scripts.fetch_index_m1 import (
    CHUNK_DAYS,
    EARLIEST_START,
    RAW_DIR,
    run_subset,
    split_round_robin,
)


def main():
    parser = argparse.ArgumentParser(
        description="Pull M1 data for the live FX pairs (raw material for M3, "
                    "which Dukascopy cannot serve directly)."
    )
    parser.add_argument("--workers", type=int, default=3,
                        help="parallel worker processes, split by instrument "
                             "(default 3)")
    parser.add_argument("--instrument", default=None,
                        help="pull a single pair instead of all 27")
    args = parser.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    if args.instrument:
        if args.instrument not in PAIRS:
            print(f"Unknown pair {args.instrument!r}; "
                  f"expected one of {', '.join(PAIRS)}")
            return 2
        keys = [args.instrument]
    else:
        keys = list(PAIRS)

    workers = max(1, min(args.workers, len(keys)))
    print(f"Pulling M1 for {len(keys)} pair(s) from "
          f"{EARLIEST_START.date()} to {end.date()} "
          f"in {CHUNK_DAYS}-day chunks, {workers} worker(s).", flush=True)

    failures = []
    if workers == 1:
        failures = run_subset(keys, end)
    else:
        from concurrent.futures import ProcessPoolExecutor

        subsets = split_round_robin(keys, workers)
        for subset in subsets:
            print(f"  worker subset: {', '.join(subset)}", flush=True)
        with ProcessPoolExecutor(max_workers=len(subsets)) as pool:
            futures = [pool.submit(run_subset, subset, end) for subset in subsets]
            for future in futures:
                failures.extend(future.result())

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} pair(s) failed:")
        for instrument_key, err in failures:
            print(f"  {instrument_key:8s} {err}")
        print("Rerun this script to resume each from its last checkpoint.")
        return 1

    print(f"All {len(keys)} pair M1 pull(s) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
