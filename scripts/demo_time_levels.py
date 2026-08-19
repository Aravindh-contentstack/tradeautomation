"""Prints the previous-day, previous-week and session liquidity levels for a
window of real candles, so the session boundaries can be checked against a
chart.

Real data rather than a synthetic fixture, same reasoning as
demo_liquidity_levels.py: the thing worth checking here is whether the Asian
range really is midnight to 04:00 London on both sides of a daylight-saving
change, and no fixture can answer that.

Every timestamp is printed in BOTH UTC and London civil time, because that
mismatch is the only place these detectors can plausibly be wrong.

Run from the repo root:

    python scripts/demo_time_levels.py                  # EUR_USD, one week either side of a DST change
    python scripts/demo_time_levels.py XAU_USD 2025-01-13
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from backtest.killzone import to_london  # noqa: E402
from smc.liquidity.time_levels import (  # noqa: E402
    compute_previous_day_levels,
    compute_previous_week_levels,
    compute_session_levels,
)

DEFAULT_INSTRUMENT = "EUR_USD"

# Two windows, one in GMT and one in BST. The session levels must land on
# different UTC hours in each while sitting on identical London hours, and
# printing both together is the fastest way to see that.
DEFAULT_WINDOWS = ["2025-01-13", "2025-07-14"]

DAYS = 3


def _load(instrument, granularity):
    df = pd.read_parquet("data/raw/%s_%s.parquet" % (instrument, granularity))
    df["date"] = df["date"].astype("datetime64[ns, UTC]")
    return df


def _both_clocks(ts):
    ts = pd.Timestamp(ts)
    return "%s   %s" % (
        ts.strftime("%Y-%m-%d %H:%M"),
        to_london(ts).strftime("%a %H:%M"),
    )


def show(levels, start, stop, title, digits):
    window = levels[
        (levels["visible_from_date"] >= start) & (levels["visible_from_date"] < stop)
    ]
    print(title)
    if window.empty:
        print("  (none)")
        print()
        return

    header = "  %-14s | %-5s | %-10s | %-26s | %-26s" % (
        "kind", "side", "level", "visible from (UTC / London)", "valid through",
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for _, level in window.iterrows():
        print(
            "  %-14s | %-5s | %-10.*f | %-26s | %-26s"
            % (
                level["kind"],
                level["side"],
                digits, level["level"],
                _both_clocks(level["visible_from_date"]),
                _both_clocks(level["valid_through_date"]),
            )
        )
    print()


def main():
    instrument = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTRUMENT
    windows = sys.argv[2:] or DEFAULT_WINDOWS
    digits = 2 if "JPY" in instrument or "XAU" in instrument else 5

    daily = _load(instrument, "D")
    h1 = _load(instrument, "H1")

    previous_day = compute_previous_day_levels(daily)
    previous_week = compute_previous_week_levels(daily)
    sessions = compute_session_levels(h1)

    print("%s: %d daily candles, %d H1 candles" % (instrument, len(daily), len(h1)))
    print("  previous day levels: %d" % len(previous_day))
    print("  previous week levels: %d" % len(previous_week))
    print("  session levels: %s" % dict(sessions["kind"].value_counts()))
    print()

    for day in windows:
        start = pd.Timestamp(day, tz="UTC")
        stop = start + pd.Timedelta(days=DAYS)
        print("=" * 78)
        print("%s to %s" % (start.date(), stop.date()))
        print("=" * 78)
        show(sessions, start, stop, "sessions", digits)
        show(previous_day, start, stop, "previous day", digits)
        show(previous_week, start, stop, "previous week", digits)


if __name__ == "__main__":
    main()
