"""Read-only validation of the downloaded M15 data (Verification 10-13).

M15 exists for exactly one purpose: telling which came first inside an H1 bar
that touched both the stop and a favourable extreme. That makes silent
wrongness expensive -- a subtly misaligned or wrong-interval M15 series would
not crash anything, it would just quietly flip the verdict on the ambiguous
trades, which are the trades the whole plan is about. So this script checks
the properties that would catch that, in increasing order of strength:

 10. len(M15)/len(H1) between 3.8 and 4.05, measured over BACKTEST_START
     onward (2020-01-01), not the full file. Catches a truncated or partial
     pull in the window that is actually used.

     Measuring over the full file history gave two false failures on the
     real data. First, Dukascopy's own M15 history starts in 2007 for 9 of
     the 10 instruments (XAU_USD is the exception, back to 2003) while H1
     goes back to 2003 for all of them -- a genuine feed limit, not a
     truncated pull. Second, and less obviously, 5 of those 9 instruments
     ALSO have a genuine multi-year hole in M15 between their early sparse
     history and Dukascopy's real continuous start around 2011-2012 (e.g.
     USD_JPY: 2007-04-30 to 2011-03-18, nearly 4 years). Both are real,
     pre-2020 characteristics of the source data, not something this
     project's fetch introduced, and neither matters: the backtest never
     walks before 2020. Restricting the check to BACKTEST_START onward
     verifies exactly the coverage this project depends on; the full-history
     ratio is still reported in the detail line so a real regression there
     is still visible, it just does not fail the run.
 11. minute set exactly {0, 15, 30, 45}. Catches a wrong interval constant
     (INTERVAL_MIN_30 or INTERVAL_MIN_10 would still download happily).
 12. THE DECISIVE CHECK: resample M15 up to 1h and compare against the stored
     H1 parquet over a sample year. high and low should agree almost always.

     "Almost always," not "always": spot-checking the real data found 0-3
     mismatched hours per instrument-year out of ~6200, always a few pips
     apart, and it is consistent from year to year rather than growing --
     2022 was clean, 2023/2024/2025 each had a small handful. That is
     Dukascopy's H1 and M15 series occasionally disagreeing on a single
     hour's extreme (almost certainly a tick-aggregation rounding difference
     during a volatile hour, not a corrupt or misaligned file), and treating
     it as fatal would fail every instrument on real, healthy data. The
     threshold below (0.2% of bars) comfortably passes that noise while
     still catching genuine corruption, which shows up as either zero
     overlapping timestamps (alignment is wrong) or hundreds of mismatches,
     not a handful.
 13. Gap profile: every gap over 4 hours that is not the normal weekend gap.
     Reported, never failed on -- Christmas and Good Friday produce genuine
     multi-day gaps and failing on them would just train everyone to ignore
     the script.

Usage:  .venv/bin/python scripts/validate_m15.py [--year 2023]

Exits non-zero if any instrument fails a check or has no M15 file yet.
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, ".")
from backtest.instruments import PIP_SIZES

RAW_DIR = "data/raw"

MIN_RATIO = 3.8
MAX_RATIO = 4.05
EXPECTED_MINUTES = {0, 15, 30, 45}
GAP_HOURS = 4
MAX_GAPS_PRINTED = 10

# 0.2% of a year's ~6200 hourly bars is ~12 bars. The real data showed 0-3
# mismatched hours per instrument-year, consistent year over year rather
# than growing, so this comfortably passes that noise while still catching
# genuine corruption (which shows up as hundreds of mismatches or none of
# the timestamps lining up at all, not a handful of few-pip disagreements).
MAX_MISMATCH_RATE = 0.002

# The backtest never walks before this year (scripts/backtest_multi.py's
# YEARS starts at 2020), so the ratio check verifies coverage from here
# onward rather than over whatever history Dukascopy happens to have.
BACKTEST_START = pd.Timestamp("2020-01-01", tz="UTC")

FRIDAY = 4
SUNDAY = 6


def raw_path(instrument, granularity):
    return os.path.join(RAW_DIR, f"{instrument}_{granularity}.parquet")


def load(instrument, granularity):
    path = raw_path(instrument, granularity)
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path)


def check_ratio(m15, h1):
    """Check 10: from BACKTEST_START onward, M15 should be very close to 4x
    the H1 row count. That is the only window this project ever reads, so
    it is the only window this check holds to a hard standard; see the
    module docstring for the two genuine pre-2020 feed gaps this sidesteps.
    """
    if len(h1) == 0:
        return False, "H1 file is empty"

    h1_recent = h1[h1["date"] >= BACKTEST_START]
    m15_recent = m15[m15["date"] >= BACKTEST_START]
    if len(h1_recent) == 0:
        return False, f"H1 has no rows on or after {BACKTEST_START.date()}"

    ratio = len(m15_recent) / len(h1_recent)
    ok = MIN_RATIO <= ratio <= MAX_RATIO

    full_ratio = len(m15) / len(h1)
    return ok, (
        f"ratio {ratio:.3f} from {BACKTEST_START.date()} onward "
        f"(M15 {len(m15_recent)}, H1 {len(h1_recent)}); "
        f"full-history ratio {full_ratio:.3f} "
        f"(M15 starts {m15['date'].min().date()}, H1 starts {h1['date'].min().date()})"
    )


def check_minutes(m15):
    """Check 11: a wrong interval constant shows up here and nowhere else."""
    minutes = set(m15["date"].dt.minute.unique())
    ok = minutes == EXPECTED_MINUTES
    return ok, f"minutes {sorted(minutes)}"


def pick_sample_year(m15, h1, requested):
    """Pick a year present in both series, defaulting to the latest complete one.

    'Complete' here just means it has a plausible number of H1 bars (>4000,
    versus roughly 6200 in a full year), which excludes the partial current
    year and any partial first year.
    """
    if requested is not None:
        return requested
    m15_years = set(m15["date"].dt.year.unique())
    counts = h1.groupby(h1["date"].dt.year).size()
    usable = [y for y, n in counts.items() if n > 4000 and y in m15_years]
    return max(usable) if usable else None


def check_resample(m15, h1, pip, year):
    """Check 12: M15 resampled to 1h must reproduce the stored H1 bars.

    Only high and low are compared. open and close are single-tick values that
    can legitimately differ between Dukascopy's own aggregations, whereas high
    and low are the extremes the stop/target logic actually reads, and they are
    the fields an alignment error would corrupt.
    """
    m15_year = m15[m15["date"].dt.year == year]
    h1_year = h1[h1["date"].dt.year == year]
    if m15_year.empty or h1_year.empty:
        return False, f"no overlapping data in {year}"

    resampled = (
        m15_year.set_index("date")
        .resample("1h")
        .agg(open=("open", "first"), high=("high", "max"),
             low=("low", "min"), close=("close", "last"))
        .dropna()
    )

    joined = resampled.join(
        h1_year.set_index("date")[["high", "low"]],
        how="inner", lsuffix="_m15", rsuffix="_h1",
    )
    if joined.empty:
        return False, f"no timestamps in common in {year} (alignment is wrong)"

    high_diff = (joined["high_m15"] - joined["high_h1"]).abs()
    low_diff = (joined["low_m15"] - joined["low_h1"]).abs()
    worst_pips = max(high_diff.max(), low_diff.max()) / pip

    over = (high_diff > pip) | (low_diff > pip)
    mismatches = int(over.sum())
    rate = mismatches / len(joined)

    detail = (
        f"{year}: {len(joined)} bars compared, {mismatches} over 1 pip "
        f"({rate:.3%}), worst {worst_pips:.2f} pips"
    )
    if mismatches:
        worst_field = "high" if high_diff.max() >= low_diff.max() else "low"
        worst_ts = (high_diff if worst_field == "high" else low_diff).idxmax()
        detail += f" at {worst_ts} ({worst_field})"
    return rate <= MAX_MISMATCH_RATE, detail


def report_gaps(m15):
    """Check 13: informational only -- report, do not fail.

    The weekend gap runs Friday around 21:00Z to Sunday around 21:00 or 22:00Z,
    identified here by weekday alone (gap starts on a Friday bar, resumes on a
    Sunday bar) so the DST hour shift does not matter.
    """
    ts = m15["date"]
    prev = ts.shift(1)
    delta = ts - prev
    big = delta > pd.Timedelta(hours=GAP_HOURS)
    weekend = (prev.dt.dayofweek == FRIDAY) & (ts.dt.dayofweek == SUNDAY)
    odd = big & ~weekend & prev.notna()

    idx = odd[odd].index
    gaps = [(prev[i], ts[i], delta[i]) for i in idx]
    gaps.sort(key=lambda g: g[2], reverse=True)
    return gaps


def validate_instrument(instrument, requested_year):
    pip = PIP_SIZES[instrument]
    print(f"\n=== {instrument} ===")

    h1 = load(instrument, "H1")
    if h1 is None:
        print("  SKIP  no H1 parquet to compare against")
        return False

    m15 = load(instrument, "M15")
    if m15 is None:
        print("  SKIP  M15 not downloaded yet "
              "(run scripts/fetch_historical_data.py)")
        return False

    results = []

    ok, detail = check_ratio(m15, h1)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] row-count ratio     {detail}")

    ok, detail = check_minutes(m15)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] minute alignment    {detail}")

    year = pick_sample_year(m15, h1, requested_year)
    if year is None:
        results.append(False)
        print("  [FAIL] resample vs H1     no usable sample year")
    else:
        ok, detail = check_resample(m15, h1, pip, year)
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] resample vs H1      {detail}")

    gaps = report_gaps(m15)
    if not gaps:
        print("  [info] gaps > 4h          none outside the weekend gap")
    else:
        print(f"  [info] gaps > 4h          {len(gaps)} non-weekend "
              f"(holidays expected; not a failure)")
        for start, end, delta in gaps[:MAX_GAPS_PRINTED]:
            print(f"           {start} -> {end}  ({delta})")
        if len(gaps) > MAX_GAPS_PRINTED:
            print(f"           ... {len(gaps) - MAX_GAPS_PRINTED} more")

    passed = all(results)
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    return passed


def main():
    parser = argparse.ArgumentParser(description="Validate downloaded M15 data.")
    parser.add_argument("--year", type=int, default=None,
                        help="sample year for the resample cross-check")
    parser.add_argument("--instrument", default=None,
                        help="validate a single instrument instead of all 10")
    args = parser.parse_args()

    instruments = [args.instrument] if args.instrument else list(PIP_SIZES)

    failed = []
    for instrument in instruments:
        if not validate_instrument(instrument, args.year):
            failed.append(instrument)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAIL: {len(failed)} of {len(instruments)} instruments "
              f"not validated: {', '.join(failed)}")
        return 1
    print(f"PASS: all {len(instruments)} instruments validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
