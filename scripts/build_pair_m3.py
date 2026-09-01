"""Build M3 for the 27 live FX pairs by resampling their M1 parquets.

Dukascopy has no 3-minute interval, so M3 cannot be downloaded; it has to be
constructed. scripts/fetch_pair_m1.py pulls the M1 series purely as the raw
material, and this script folds every three M1 bars into one M3 bar
(open = first, high = max, low = min, close = last), which is exactly how a
broker's own 3-minute aggregation is defined.

WHY THE RESAMPLE IS A SEPARATE PASS, NOT PART OF THE DOWNLOAD. Rolling M1 up
to M3 inside the chunked fetch would leave a PARTIAL 3-minute bucket on every
chunk boundary -- a 12:57 bar built from one M1 bar instead of three. Resume
restarts at max(date) + 1s, so it would skip the rest of that bucket forever,
and _normalise's drop_duplicates (which keeps the first occurrence) would lock
the partial bar in permanently. Nothing would crash; the file would just be
quietly wrong at every seam. Resampling in one pass over the finished M1 file
has no chunk boundaries and therefore no such bucket.

WHY THIS SCRIPT IS A THIN WRAPPER. scripts/build_index_m3.py already
implements the resample, the atomic write, and all three verification checks,
and none of it is index-specific. This script supplies a different instrument
list and one different tolerance (see below).

THE ONE REAL DIFFERENCE FROM THE INDEX BUILD: the containment tolerance.
build_index_m3.py defaults to 1% of the instrument's median M15 bar range,
which is right for point-quoted indices whose price scales differ by a factor
of ~20. On an FX major that works out to roughly 0.05 pips, far tighter than
Dukascopy's own aggregations agree with each other, and it would fail on
healthy data. So this script passes an absolute tolerance of one pip from
backtest/instruments.py, which is the same threshold scripts/validate_m15.py
already justifies at length for the M15-vs-H1 cross-check.

Usage:
    .venv/bin/python scripts/build_pair_m3.py
    .venv/bin/python scripts/build_pair_m3.py --verify
    .venv/bin/python scripts/build_pair_m3.py --instrument EUR_USD

Exits non-zero if any pair fails to build or fails verification.
"""

import argparse
import sys

sys.path.insert(0, ".")
from live.pairs import PAIRS
from backtest.instruments import PIP_SIZES
from scripts.build_index_m3 import build_one, verify_one


def main():
    parser = argparse.ArgumentParser(
        description="Build (or verify) M3 parquets for the live FX pairs by "
                    "resampling their M1 series."
    )
    parser.add_argument("--verify", action="store_true",
                        help="verify existing M3 files instead of building them")
    parser.add_argument("--instrument", default=None,
                        help="operate on a single pair instead of all 27")
    args = parser.parse_args()

    if args.instrument and args.instrument not in PAIRS:
        print(f"Unknown pair {args.instrument!r}; "
              f"expected one of {', '.join(PAIRS)}")
        return 2
    keys = [args.instrument] if args.instrument else list(PAIRS)

    failed = []
    if args.verify:
        for key in keys:
            # One pip, not the index script's relative default; see the module
            # docstring.
            if not verify_one(key, tolerance=PIP_SIZES[key]):
                failed.append(key)
    else:
        print(f"Resampling M1 -> M3 for {len(keys)} pair(s).")
        for key in keys:
            if not build_one(key):
                failed.append(key)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAIL: {len(failed)} of {len(keys)}: {', '.join(failed)}")
        return 1
    print(f"PASS: all {len(keys)} pair(s) "
          f"{'verified' if args.verify else 'built'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
