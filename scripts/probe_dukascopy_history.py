"""One-time check of how far back each instrument/granularity's history
actually goes on Dukascopy, before running the full 20-year pull.

Dukascopy's history length is not uniform across instruments (crosses
and indices commonly start later than FX majors), so this binary-searches
the earliest date at which fetch_candles returns any rows, per
instrument/granularity, and prints a summary table.
"""

import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
from data.dukascopy_client import INSTRUMENTS, GRANULARITIES, fetch_candles

EARLIEST_POSSIBLE = datetime(1990, 1, 1)
TODAY = datetime.now()
PROBE_WINDOW_DAYS = 31


def has_data(instrument_key, granularity_key, month_start):
    df = fetch_candles(
        instrument_key,
        granularity_key,
        month_start,
        month_start + timedelta(days=PROBE_WINDOW_DAYS),
    )
    return not df.empty


def find_earliest_month(instrument_key, granularity_key):
    lo = EARLIEST_POSSIBLE
    hi = TODAY

    if not has_data(instrument_key, granularity_key, hi - timedelta(days=PROBE_WINDOW_DAYS)):
        return None

    while (hi - lo).days > 31:
        mid = lo + (hi - lo) / 2
        mid = mid.replace(day=1)
        if has_data(instrument_key, granularity_key, mid):
            hi = mid
        else:
            lo = mid
    return hi


def main():
    results = {}
    for instrument_key in INSTRUMENTS:
        results[instrument_key] = {}
        for granularity_key in GRANULARITIES:
            earliest = find_earliest_month(instrument_key, granularity_key)
            results[instrument_key][granularity_key] = earliest
            label = earliest.strftime("%Y-%m") if earliest else "NO DATA"
            print(f"{instrument_key:10s} {granularity_key:3s}  earliest ~ {label}")

    return results


if __name__ == "__main__":
    main()
