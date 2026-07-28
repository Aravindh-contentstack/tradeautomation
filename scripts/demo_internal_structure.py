"""Runs the internal structure tier on top of the same synthetic dataset
demo_swing_structure.py and demo_market_structure.py use, and prints a
combined table (swing + internal side by side) so correctness can be
checked by eye.

Internal structure now uses an ATR-based zigzag (atr_period=14,
reversal_multiplier=1.5) rather than a fixed-candle-count pivot window
or the swing tier's persist-until-broken plus timeout. See
swing_structure/internal_structure.py's module docstring ("Mechanism
history") for the full path that led here: a fixed pivot_len fixed the
staleness problem the persist-until-broken mechanism had, but didn't
adapt to how much faster real pullbacks can be in a strong trend than in
a quiet range. An ATR-based reversal threshold adapts to that
automatically, since ATR itself moves with volatility. It still redraws
far more often than the swing tier, independently of whatever the swing
tier is doing at the same time.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_swing_structure import build_synthetic_data
from swing_structure.detector import compute_daily_swing_structure
from swing_structure.market_structure import compute_market_structure
from swing_structure.internal_structure import compute_internal_structure


def main():
    df, manual_restart, hold_timeout = build_synthetic_data()

    swing_result = compute_daily_swing_structure(
        df,
        manual_restart=manual_restart,
        hold_timeout=hold_timeout,
    )
    swing_result = compute_market_structure(swing_result)
    result = compute_internal_structure(swing_result)

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 160)

    columns = [
        "date",
        "close",
        "market_structure",
        "market_structure_event",
        "internal_structure",
        "internal_structure_event",
    ]

    print(f"Total candles: {len(result)}\n")

    print("=== Every row where the swing tier's structure changed ===")
    swing_changed = result["market_structure_event"].notna()
    print(result.loc[swing_changed, columns].to_string(index=False))

    print("\n=== Every row where the internal tier's structure changed ===")
    internal_changed = result["internal_structure_event"].notna()
    print(result.loc[internal_changed, columns].to_string(index=False))

    print(f"\nSwing tier changes: {swing_changed.sum()}")
    print(f"Internal tier changes: {internal_changed.sum()}")


if __name__ == "__main__":
    main()
