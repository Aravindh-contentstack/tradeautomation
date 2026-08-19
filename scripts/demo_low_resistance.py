"""Prints the Low Resistance Liquidity levels detected on real candles, with
the price of every stepping pivot in each run, so a run can be checked
against a chart in one look.

The pivot prices matter more here than in the other demos. LRLQ is the
fuzziest rule in the strategy and has no usable reference implementation, so
the thing to eyeball is not just "is there a level" but "are these three
turns really the grinding pullback I would have drawn a wedge around".

Run from the repo root:

    python scripts/demo_low_resistance.py                    # EUR_USD Daily, 2025
    python scripts/demo_low_resistance.py EUR_USD H1 2025
    python scripts/demo_low_resistance.py XAU_USD H4 2024 low
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from smc.liquidity.low_resistance import (  # noqa: E402
    DEFAULT_MAX_SPAN,
    DEFAULT_MIN_PIVOTS,
    DEFAULT_PIVOT_N,
    compute_low_resistance_liquidity,
)
from smc.market_structure.fractal_detector import compute_fractal_pivots  # noqa: E402

DEFAULT_INSTRUMENT = "EUR_USD"
DEFAULT_GRANULARITY = "D"
DEFAULT_YEAR = 2025


def main():
    instrument = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTRUMENT
    granularity = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GRANULARITY
    year = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_YEAR
    only_side = sys.argv[4] if len(sys.argv) > 4 else None

    df = pd.read_parquet("data/raw/%s_%s.parquet" % (instrument, granularity))
    df["date"] = df["date"].astype("datetime64[ns, UTC]")

    levels = compute_low_resistance_liquidity(df)
    pivots = compute_fractal_pivots(df, n=DEFAULT_PIVOT_N)
    dates = pd.DatetimeIndex(df["date"])
    digits = 2 if "JPY" in instrument or "XAU" in instrument else 5

    print("%s %s: %d candles, %d LRLQ levels"
          % (instrument, granularity, len(df), len(levels)))
    print("  rule: %d+ stepping pivots at n=%d, within %d candles"
          % (DEFAULT_MIN_PIVOTS, DEFAULT_PIVOT_N, DEFAULT_MAX_SPAN))
    print("  sell side (higher lows): %d   buy side (lower highs): %d"
          % ((levels["side"] == "low").sum(), (levels["side"] == "high").sum()))
    print("  ended by: %s" % dict(levels["ended_by"].value_counts()))
    print()

    window = levels[pd.DatetimeIndex(levels["visible_from_date"]).year == year]
    if only_side:
        window = window[window["side"] == only_side]
    if window.empty:
        print("nothing in %d" % year)
        return

    header = "%-6s | %-10s | %-5s | %-12s | %-9s | %-10s | %s" % (
        "side", "level", "steps", "known from", "ended", "swept", "the run",
    )
    print(header)
    print("-" * len(header))

    for _, level in window.iterrows():
        run = pivots[
            (pivots["side"] == level["side"])
            & (pivots["pivot_index"] >= level["first_pivot_index"])
            & (pivots["pivot_index"] <= level["last_pivot_index"])
        ]
        steps = "  ".join("%.*f" % (digits, price) for price in run["pivot_price"])
        swept = (
            dates[int(level["swept_index"])].strftime("%Y-%m-%d")
            if level["swept"] else "-"
        )
        print(
            "%-6s | %-10.*f | %-5d | %-12s | %-9s | %-10s | %s"
            % (
                level["side"],
                digits, level["level"],
                level["pivot_count"],
                pd.Timestamp(level["visible_from_date"]).strftime("%Y-%m-%d"),
                level["ended_by"],
                swept,
                steps,
            )
        )


if __name__ == "__main__":
    main()
