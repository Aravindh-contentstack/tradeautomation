"""Build M3 for the world equity indices by resampling their M1 parquets.

WHY THIS SCRIPT EXISTS. Dukascopy has no 3-minute interval -- the minute
intervals it offers are 1, 5, 10, 15 and 30. So M3 cannot be downloaded; it
has to be constructed. scripts/fetch_index_m1.py pulls the M1 series purely as
the raw material for this step, and this script folds every three M1 bars into
one M3 bar (open = first, high = max, low = min, close = last), which is
exactly how a broker's own 3-minute aggregation is defined.

WHY .dropna() IS LOAD-BEARING. pandas' resample() emits a slot for every
3-minute boundary in the range, including the ones with no underlying bars.
Four of these ten indices are not 24/5 -- IBXEUR, ESXEUR and F40EUR trade
roughly 06:00-19:00 UTC, and HSIHKD roughly 01:00-16:00 UTC with a midday
break -- so more than half of every calendar day is outside trading hours for
them. Without the dropna() those slots would be written out as null OHLC rows:
millions of fake bars per instrument, which would break the row-ratio sanity
checks, inflate the files, and quietly feed NaNs into anything that reads
them. Dropping them means the output contains exactly the 3-minute windows
that actually had ticks, which is the same convention every other parquet in
data/raw/ follows.

The output has the same shape as every other file in data/raw/ (columns date,
open, high, low, close, ascending) and is written with the same
tmp-then-os.replace() atomic pattern used by the fetch scripts.

VERIFY MODE (--verify) checks, per instrument:
 1. Minute alignment: every M3 timestamp's minute is divisible by 3. This is
    the M3 analogue of validate_m15.py's check 11 -- a wrong interval or a
    shifted grid would otherwise produce a perfectly valid-looking file.
 2. Containment against M15, the decisive check and the analogue of
    validate_m15.py's check 12. Each M3 bar must sit inside its parent M15 bar:
    high <= parent high, low >= parent low, where the parent is the M15 bar
    whose 15-minute window contains that M3 timestamp. A silent misalignment
    (an off-by-one grid, a timezone shift, the wrong source file) shows up
    here and essentially nowhere else.

    A SMALL MISMATCH RATE IS NORMAL AND IS NOT CORRUPTION. validate_m15.py
    found that Dukascopy's own M15 and H1 series occasionally disagree by a
    few units on a single bar's extreme -- a tick-aggregation rounding
    difference during a volatile bar, not a broken file -- and so allows up to
    0.2% of bars to mismatch rather than failing on healthy real data. The
    same reasoning applies here, and more so, because M3 and M15 are two
    independent aggregations of the same tick stream. Genuine corruption does
    not look like a handful of tiny disagreements; it looks like either zero
    overlapping timestamps (alignment is wrong) or hundreds of mismatches. The
    actual measured rate is always reported.
 3. No duplicate and no out-of-order timestamps.
 4. Reported: first date, last date, row count, and the M1:M3 row ratio, which
    should sit close to 3 within trading hours.

Check 2 needs <KEY>_M15.parquet, produced by scripts/fetch_historical_data.py.
If it is missing, the check is reported as a FAIL with a clear reason rather
than silently skipped.

Usage:
    .venv/bin/python scripts/build_index_m3.py
    .venv/bin/python scripts/build_index_m3.py --verify
    .venv/bin/python scripts/build_index_m3.py --instrument SP500

Exits non-zero if any instrument fails to build or fails verification.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, ".")
from data.dukascopy_client import INDEX_KEYS

RAW_DIR = "data/raw"

# A few units of price difference on an extreme is Dukascopy's own tick
# aggregation, not a misalignment; see the module docstring. These are
# point-quoted indices with wildly different scales (a median H1 range of ~12
# points on ESXEUR vs ~268 on JPN225), so the tolerance is relative to the
# instrument's own typical bar range rather than a fixed number of points.
MISMATCH_TOLERANCE_FRACTION = 0.01  # 1% of the median M15 bar range
MAX_MISMATCH_RATE = 0.002  # same 0.2% budget validate_m15.py settled on


def raw_path(instrument_key, granularity):
    return os.path.join(RAW_DIR, f"{instrument_key}_{granularity}.parquet")


def load(instrument_key, granularity):
    path = raw_path(instrument_key, granularity)
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path)


def checkpoint(df, path):
    """Atomically replace the output parquet with df.

    Same reasoning as the fetch scripts: a kill mid-write must not leave a
    truncated parquet behind, since a later run would read it back.
    """
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def resample_m3(m1):
    """Fold M1 into M3 bars.

    The .dropna() is essential, not cosmetic. resample() emits an empty slot
    for every 3-minute boundary in the range, so for the part-day indices
    (IBXEUR, ESXEUR, F40EUR trade ~06:00-19:00 UTC; HSIHKD ~01:00-16:00 UTC
    with a midday break) it would otherwise write out millions of null-OHLC
    bars covering the hours the market was closed. Dropping them leaves
    exactly the windows that actually had ticks.
    """
    m3 = (
        m1.set_index("date")
        .resample("3min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    # Back to a plain `date` column so the file matches every other parquet in
    # data/raw/ rather than being the one with a DatetimeIndex.
    return m3.reset_index()[["date", "open", "high", "low", "close"]]


def build_one(instrument_key):
    m1 = load(instrument_key, "M1")
    if m1 is None:
        print(f"  SKIP  {instrument_key}: no M1 parquet "
              f"(run scripts/fetch_index_m1.py first)")
        return False
    if m1.empty:
        print(f"  FAIL  {instrument_key}: M1 parquet is empty")
        return False

    m3 = resample_m3(m1)
    path = raw_path(instrument_key, "M3")
    checkpoint(m3, path)
    ratio = len(m1) / len(m3) if len(m3) else float("nan")
    print(f"  {instrument_key:8s} M1 {len(m1):9d} -> M3 {len(m3):9d} "
          f"(ratio {ratio:.2f})  {m3['date'].min()} .. {m3['date'].max()}")
    return True


def check_minutes(m3):
    """Check 1: every M3 timestamp must land on a multiple of 3 minutes."""
    minutes = sorted(m3["date"].dt.minute.unique())
    bad = [m for m in minutes if m % 3 != 0]
    if bad:
        return False, f"{len(bad)} minute value(s) not divisible by 3: {bad[:10]}"
    return True, f"all {len(minutes)} distinct minute values divisible by 3"


def check_ordering(m3):
    """Check 3: no duplicates, strictly ascending."""
    dupes = int(m3["date"].duplicated().sum())
    ascending = bool(m3["date"].is_monotonic_increasing)
    ok = dupes == 0 and ascending
    return ok, f"{dupes} duplicate timestamp(s), ascending={ascending}"


def check_containment(m3, m15, tolerance=None):
    """Check 2: every M3 bar must sit inside its parent M15 bar.

    The parent is found by flooring each M3 timestamp to its 15-minute
    boundary and joining onto the stored M15 series -- so this is a real
    cross-check against an independently downloaded file, not a self-check.

    Tolerance defaults to a small fraction of the instrument's own median M15
    bar range, because these are point-quoted indices whose price scales
    differ by a factor of ~20 and a fixed point tolerance would be
    meaninglessly tight on one and meaninglessly loose on another.

    Callers with a natural absolute unit pass `tolerance` explicitly.
    scripts/build_pair_m3.py passes one pip, which is the same threshold
    validate_m15.py already settled on for FX; the relative default would work
    out to about 0.05 pips on a major and fail on healthy data.
    """
    if m15 is None:
        return False, ("M15 parquet missing -- cannot run the decisive check "
                       "(scripts/fetch_historical_data.py produces it)")
    if m15.empty:
        return False, "M15 parquet is empty"

    parents = m3.copy()
    parents["parent"] = parents["date"].dt.floor("15min")

    joined = parents.merge(
        m15[["date", "high", "low"]].rename(columns={"date": "parent"}),
        on="parent", how="inner", suffixes=("_m3", "_m15"),
    )
    if joined.empty:
        return False, ("no M3 timestamp maps onto any M15 bar "
                       "(alignment is wrong)")

    bar_range = (m15["high"] - m15["low"]).median()
    if tolerance is None:
        tolerance = max(bar_range * MISMATCH_TOLERANCE_FRACTION, 1e-9)

    high_excess = joined["high_m3"] - joined["high_m15"]
    low_excess = joined["low_m15"] - joined["low_m3"]
    over = (high_excess > tolerance) | (low_excess > tolerance)
    mismatches = int(over.sum())
    rate = mismatches / len(joined)
    worst = max(float(high_excess.max()), float(low_excess.max()))

    detail = (
        f"{len(joined)} of {len(m3)} M3 bars matched to an M15 parent, "
        f"{mismatches} outside it ({rate:.4%}), "
        f"tolerance {tolerance:.6f} (median M15 range {bar_range:.6f}), "
        f"worst excess {worst:.4f}"
    )
    return rate <= MAX_MISMATCH_RATE, detail


def verify_one(instrument_key, tolerance=None):
    print(f"\n=== {instrument_key} ===")

    m3 = load(instrument_key, "M3")
    if m3 is None:
        print("  FAIL  no M3 parquet (run this script without --verify first)")
        return False
    if m3.empty:
        print("  FAIL  M3 parquet is empty")
        return False

    m1 = load(instrument_key, "M1")
    m15 = load(instrument_key, "M15")

    results = []

    ok, detail = check_minutes(m3)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] minute alignment    {detail}")

    ok, detail = check_containment(m3, m15, tolerance)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] M15 containment     {detail}")

    ok, detail = check_ordering(m3)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] dupes / ordering    {detail}")

    ratio = f"{len(m1) / len(m3):.3f}" if m1 is not None and len(m3) else "n/a"
    print(f"  [info] coverage           {len(m3)} M3 rows, "
          f"{m3['date'].min()} .. {m3['date'].max()}, M1:M3 ratio {ratio}")

    passed = all(results)
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    return passed


def main():
    parser = argparse.ArgumentParser(
        description="Build (or verify) M3 parquets for the equity indices by "
                    "resampling their M1 series."
    )
    parser.add_argument("--verify", action="store_true",
                        help="verify existing M3 files instead of building them")
    parser.add_argument("--instrument", default=None,
                        help="operate on a single index instead of all 10")
    args = parser.parse_args()

    if args.instrument and args.instrument not in INDEX_KEYS:
        print(f"Unknown index key {args.instrument!r}; "
              f"expected one of {', '.join(INDEX_KEYS)}")
        return 2
    keys = [args.instrument] if args.instrument else list(INDEX_KEYS)

    failed = []
    if args.verify:
        for key in keys:
            if not verify_one(key):
                failed.append(key)
    else:
        print(f"Resampling M1 -> M3 for {len(keys)} index/indices.")
        for key in keys:
            if not build_one(key):
                failed.append(key)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAIL: {len(failed)} of {len(keys)}: {', '.join(failed)}")
        return 1
    print(f"PASS: all {len(keys)} instrument(s) "
          f"{'verified' if args.verify else 'built'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
