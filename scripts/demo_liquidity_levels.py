"""Prints the Equal Highs/Lows and Old Points detected on real candles, so
they can be checked against a chart.

Deliberately NOT built on demo_daily_structure.py's synthetic fixture, unlike
demo_fair_value_gaps.py and demo_order_blocks.py. Those two smoke-test that
the logic runs; this one exists to answer "are these actually the equal highs
I would have drawn", and only real data can answer that. Every row prints the
dates of the pivots that formed the level, so it can be found on a chart in
one step.

Run from the repo root:

    python scripts/demo_liquidity_levels.py                    # EUR_USD Daily, 2025
    python scripts/demo_liquidity_levels.py XAU_USD D 2024
    python scripts/demo_liquidity_levels.py EUR_USD H1 2025 equals
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from smc.liquidity.levels import compute_liquidity_levels  # noqa: E402

DEFAULT_INSTRUMENT = "EUR_USD"
DEFAULT_GRANULARITY = "D"
DEFAULT_YEAR = 2025


def main():
    instrument = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTRUMENT
    granularity = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GRANULARITY
    year = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_YEAR
    only_kind = sys.argv[4] if len(sys.argv) > 4 else None

    df = pd.read_parquet("data/raw/%s_%s.parquet" % (instrument, granularity))
    df["date"] = df["date"].astype("datetime64[ns, UTC]")
    levels = compute_liquidity_levels(df)

    dates = pd.DatetimeIndex(df["date"])
    digits = 2 if "JPY" in instrument or "XAU" in instrument else 5

    print("%s %s: %d candles, %d level versions"
          % (instrument, granularity, len(df), len(levels)))
    print("  equals: %d   old points: %d"
          % ((levels["kind"] == "equals").sum(), (levels["kind"] == "old_point").sum()))
    print("  ended by: %s" % dict(levels["ended_by"].value_counts()))
    print()

    window = levels[pd.DatetimeIndex(levels["visible_from_date"]).year == year]
    if only_kind:
        window = window[window["kind"] == only_kind]
    if window.empty:
        print("nothing in %d" % year)
        return

    print("%d levels visible from within %d" % (len(window), year))
    print()

    header = "%-10s | %-5s | %-10s | %-10s | %-5s | %-12s | %-12s | %-12s" % (
        "kind", "side", "level", "band", "touch", "visible from", "pivots", "ended",
    )
    print(header)
    print("-" * len(header))

    for _, level in window.iterrows():
        first = dates[int(level["first_pivot_index"])].strftime("%m-%d")
        last = dates[int(level["pivot_index"])].strftime("%m-%d")
        band = (level["level_top"] - level["level_bot"])
        print(
            "%-10s | %-5s | %-10.*f | %-10.*f | %-5d | %-12s | %-12s | %-12s"
            % (
                level["kind"],
                level["side"],
                digits, level["level"],
                digits, band,
                level["touch_count"],
                pd.Timestamp(level["visible_from_date"]).strftime("%Y-%m-%d"),
                "%s/%s" % (first, last),
                level["ended_by"],
            )
        )


if __name__ == "__main__":
    main()
