"""Single-instrument entry point: EUR_USD only, one year at a time.

This used to be the primary script and backtest_multi.py was the copy. They
had drifted into two byte-identical per-trade loops differing only in
`PIP_SIZE` versus `pip_size`, so both loops now live in backtest/runner.py and
the path/reporting layer lives in scripts/backtest_multi.py. What survives here
is the only thing that was ever really different: the instrument is pinned and
YEARS is meant to be narrow.

Keep it that way. Running one year, reading the journal, and only then
extending YEARS is the workflow this script exists for; backtest_multi.py is
for the full 10-instrument sweep. The walk-forward rationale that used to live
in this docstring (frozen weights, the learning accumulator, the per-year
settings search) has moved into backtest/runner.py, next to the code it
describes.
"""

import sys

sys.path.insert(0, ".")

from scripts.backtest_multi import run_instrument

INSTRUMENT = "EUR_USD"

YEARS = [2025]


def main():
    run_instrument(INSTRUMENT, years=YEARS)


if __name__ == "__main__":
    main()
