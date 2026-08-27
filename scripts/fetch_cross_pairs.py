"""Historical OHLC pull for the 18 liquid FX cross pairs added on top of the
original 10-pair backtest set.

Reuses fetch_one from fetch_historical_data.py so resume/checkpoint/atomic
write/sanity-check behavior is identical to the existing pairs. The only
difference is the instrument list: this script deliberately does NOT touch
PIP_SIZES (backtest/instruments.py) or PAIRS (live/pairs.py), so these
crosses stay data-only until a separate decision is made to actually
backtest/trade them. download_plan() in fetch_historical_data.py is
untouched, so the existing production download job is unaffected by this.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from scripts.fetch_historical_data import fetch_one, RAW_DIR

CROSS_PAIRS = [
    "EUR_GBP", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD",
    "GBP_CHF", "GBP_AUD", "GBP_CAD", "GBP_NZD",
    "AUD_JPY", "AUD_CAD", "AUD_CHF", "AUD_NZD",
    "NZD_JPY", "NZD_CAD", "NZD_CHF",
    "CAD_JPY", "CHF_JPY",
]

GRANULARITIES = ("D", "H4", "H1", "M15")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)

    failures = []
    for instrument_key in CROSS_PAIRS:
        for granularity_key in GRANULARITIES:
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
        print("\nAll cross pairs completed.")


if __name__ == "__main__":
    main()
