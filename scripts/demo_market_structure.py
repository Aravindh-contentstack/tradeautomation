"""Runs the market structure classifier on top of the same synthetic
dataset demo_swing_structure.py uses, and prints a readable table so
correctness can be checked by eye before any Pine porting starts.

NOTE July 29 2026: demonstrates compute_market_structure layered on the
retired lookback+timeout swing tier (swing_structure/detector.py), a
combination superseded as the live Daily swing tier by
scripts/demo_daily_structure.py. compute_market_structure itself is still
fully active, it's what compute_tier_structure calls internally for
every Daily/4H/H1 tier now.

The synthetic data already has a break of the swing high, then a break of
the swing low, then a 65-candle timeout stretch, then a manual_restart
flip (see demo_swing_structure.py for how each segment is built). That's
good coverage for this layer too: we can confirm market_structure flips on
the two real breaks, and stays unchanged through the later timeout and
manual_restart, which don't count as real breaks.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_swing_structure import build_synthetic_data
from swing_structure.detector import compute_daily_swing_structure
from swing_structure.market_structure import compute_market_structure


def main():
    df, manual_restart, hold_timeout = build_synthetic_data()
    swing_result = compute_daily_swing_structure(
        df,
        manual_restart=manual_restart,
        hold_timeout=hold_timeout,
    )
    result = compute_market_structure(swing_result)

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 140)

    columns = [
        "date",
        "close",
        "high_event",
        "low_event",
        "market_structure",
        "market_structure_event",
    ]

    print(f"Total candles: {len(result)}\n")

    print("=== Every row where market_structure_event fired ===")
    fired = result["market_structure_event"].notna()
    print(result.loc[fired, columns].to_string(index=False))

    print("\n=== market_structure across the whole run (confirm it holds steady ===")
    print("=== through the segment-4 timeout and segment-6 manual_restart)      ===")
    print(result[columns].to_string(index=False))


if __name__ == "__main__":
    main()
