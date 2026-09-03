"""Full historical OHLC pull for the extra metals added 2026-09-03:
XAG_USD (silver), XPD_USD (palladium), XPT_USD (platinum) and COPPER_USD.

This is a one-off companion to scripts/fetch_historical_data.py and
scripts/fetch_index_m1.py, kept separate for the same reason the M1 index
pull is: those two have explicit, frozen download plans (39 D/H4/H1 keys,
38 M15 keys, 10 M1 keys) and wiring four new instruments into both -- plus
into backtest/instruments.py PIP_SIZES, which M15's plan is keyed on --
would drag trading-model decisions into what is really just a data grab.
These four are data-only: not in PAIRS, not in PIP_SIZES, not traded.

Tiers pulled: D, H4, H1, M15, M1.

Everything operational is copied from fetch_index_m1.py deliberately:
  * 30-day chunks for M1 (Dukascopy serves M1 from per-hour files, so a
    180-day M1 request is ~4,300 file fetches in one call -- exactly where
    a retry budget exhausts); 180-day chunks for D/H4/M15.
  * tmp-then-os.replace() checkpoint after every chunk -- atomic on the
    same filesystem, so a kill mid-write cannot leave a truncated parquet
    that would poison the next resume.
  * resume reads the existing file's max date and restarts one second
    later, so rerunning the script picks up each (instrument, tier) from
    its last checkpoint. No manifest, no state file.
  * a failure on one (instrument, tier) is printed and skipped, then all
    failures are summarised at the end.

Per-instrument start dates come from a binary-search probe of the daily
feed (see scripts/probe_dukascopy_history.py for the technique). They are
deliberately a few months earlier than the probe's answer; empty leading
chunks simply produce no rows.

Usage:
    .venv/bin/python scripts/fetch_metals.py
    .venv/bin/python scripts/fetch_metals.py --instrument XAG_USD
    .venv/bin/python scripts/fetch_metals.py --tier M1
Rerun to resume.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, ".")
from data.dukascopy_client import fetch_candles

RAW_DIR = "data/raw"

METAL_KEYS = ["XAG_USD", "XPD_USD", "XPT_USD", "COPPER_USD"]
TIERS = ["D", "H4", "H1", "M15", "M1"]

# A few months earlier than the daily-feed probe reported (XAG ~2014-07,
# COPPER ~2012-02, XPT ~2021-11, XPD ~2022-02). XAG is a spot FX-metal like
# XAU_USD, so it is worth reaching back further in case the intraday feed
# predates the daily one; the empty years cost only skipped chunks.
INSTRUMENT_START = {
    "XAG_USD": datetime(2003, 1, 1, tzinfo=timezone.utc),
    "COPPER_USD": datetime(2011, 1, 1, tzinfo=timezone.utc),
    "XPT_USD": datetime(2021, 1, 1, tzinfo=timezone.utc),
    "XPD_USD": datetime(2021, 1, 1, tzinfo=timezone.utc),
}

CHUNK_DAYS = {"D": 180, "H4": 180, "H1": 180, "M15": 180, "M1": 30}

MAX_RETRIES = 10
CHUNK_PAUSE_SECONDS = 0.25

EMPTY_COLUMNS = ["date", "open", "high", "low", "close"]


def output_path(instrument_key, tier):
    return os.path.join(RAW_DIR, f"{instrument_key}_{tier}.parquet")


def _normalise(df):
    """De-duplicate and sort; chunk boundaries can overlap by a bar."""
    return (
        df.drop_duplicates(subset="date")
        .sort_values("date")
        .reset_index(drop=True)
    )


def checkpoint(df, path):
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def sanity_check(df, instrument_key, tier):
    if df.empty:
        print(f"WARNING {instrument_key} {tier}: no rows at all")
        return
    if df["date"].duplicated().any():
        print(f"WARNING {instrument_key} {tier}: duplicate timestamps")
    if not df["date"].is_monotonic_increasing:
        print(f"WARNING {instrument_key} {tier}: timestamps not ascending")
    if df[["open", "high", "low", "close"]].isna().all(axis=1).any():
        print(f"WARNING {instrument_key} {tier}: fully-null OHLC rows")


def fetch_one(instrument_key, tier, end):
    path = output_path(instrument_key, tier)
    chunk_days = CHUNK_DAYS[tier]

    if os.path.exists(path):
        combined = pd.read_parquet(path)
        start = combined["date"].max() + timedelta(seconds=1)
    else:
        combined = pd.DataFrame(columns=EMPTY_COLUMNS)
        start = INSTRUMENT_START[instrument_key]

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        chunk = fetch_candles(
            instrument_key, tier, cursor, chunk_end, max_retries=MAX_RETRIES,
        )
        if not chunk.empty:
            combined = _normalise(pd.concat([combined, chunk], ignore_index=True))
            checkpoint(combined, path)
        cursor = chunk_end
        time.sleep(CHUNK_PAUSE_SECONDS)

    sanity_check(combined, instrument_key, tier)
    checkpoint(combined, path)
    print(f"{instrument_key:10s} {tier:3s}  {len(combined):9d} rows -> {path}",
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", default=None,
                        help=f"one of {', '.join(METAL_KEYS)} (default: all)")
    parser.add_argument("--tier", default=None,
                        help=f"one of {', '.join(TIERS)} (default: all)")
    args = parser.parse_args()

    keys = [args.instrument] if args.instrument else list(METAL_KEYS)
    tiers = [args.tier] if args.tier else list(TIERS)
    if args.instrument and args.instrument not in METAL_KEYS:
        print(f"Unknown instrument {args.instrument!r}; "
              f"expected one of {', '.join(METAL_KEYS)}")
        return 2
    if args.tier and args.tier not in TIERS:
        print(f"Unknown tier {args.tier!r}; expected one of {', '.join(TIERS)}")
        return 2

    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)
    print(f"Pulling {', '.join(tiers)} for {', '.join(keys)} up to "
          f"{end.date()}. Rerun to resume.", flush=True)

    # D/H4/M15 first for every instrument, then the long M1 pass -- a crash
    # is far likelier in M1, and this way the cheap tiers are already banked.
    ordered = [t for t in ("D", "H4", "H1", "M15", "M1") if t in tiers]

    failures = []
    for tier in ordered:
        for instrument_key in keys:
            started = time.time()
            print(f"START {instrument_key} {tier}", flush=True)
            try:
                fetch_one(instrument_key, tier, end)
                print(f"DONE  {instrument_key} {tier} in "
                      f"{time.time() - started:.0f}s", flush=True)
            except Exception as err:
                print(f"FAILED {instrument_key} {tier}: {err} "
                      f"(rerun to resume from checkpoint)", flush=True)
                failures.append((instrument_key, tier, str(err)))

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} (instrument, tier) pull(s) failed:")
        for instrument_key, tier, err in failures:
            print(f"  {instrument_key:10s} {tier:3s}  {err}")
        print("Rerun this script to resume each from its last checkpoint.")
        return 1

    print(f"All {len(keys) * len(ordered)} metal pull(s) completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
