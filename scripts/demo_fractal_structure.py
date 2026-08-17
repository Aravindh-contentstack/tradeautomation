"""Runs all three Daily tiers (swing, internal, fractal) on the same
synthetic dataset demo_swing_structure.py uses, and prints a combined
table so correctness can be checked by eye.

Rebuilt July 29 2026 to call compute_daily_structures instead of the old
compute_daily_swing_structure -> compute_market_structure ->
compute_internal_structure -> compute_fractal_structure chain, since all
three Daily tiers now run the same Williams Fractal mechanism at n=20/8/2
(see swing_structure/daily_structure.py and
roadmap/detection-method-decision.md's "Daily port" section). The old
per-mechanism modules (detector.py, internal_structure.py) are superseded
but kept for comparison; see their own module docstrings.

The synthetic dataset was engineered for the OLD swing tier's
lookback/timeout rules and the OLD internal tier's ATR zigzag, not for
fractal-specific edge cases like a tied-high plateau, so it exercises
ordinary fractal detection but may never hit the plateau-tolerant branch.
That's expected, not a bug; scripts/demo_daily_structure.py's fixture is
purpose-built for the fractal mechanism instead.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_swing_structure import build_synthetic_data
from smc.market_structure.daily_structure import compute_daily_structures


def main():
    df, _manual_restart, _hold_timeout = build_synthetic_data()

    result = compute_daily_structures(df)

    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 160)

    columns = [
        "date",
        "close",
        "daily_swing_structure",
        "daily_swing_structure_event",
        "daily_internal_structure",
        "daily_internal_structure_event",
        "daily_fractal_structure",
        "daily_fractal_structure_event",
    ]

    print(f"Total candles: {len(result)}\n")

    print("=== Every row where the swing tier's structure changed ===")
    swing_changed = result["daily_swing_structure_event"].notna()
    print(result.loc[swing_changed, columns].to_string(index=False))

    print("\n=== Every row where the internal tier's structure changed ===")
    internal_changed = result["daily_internal_structure_event"].notna()
    print(result.loc[internal_changed, columns].to_string(index=False))

    print("\n=== Every row where the fractal tier's structure changed ===")
    fractal_changed = result["daily_fractal_structure_event"].notna()
    print(result.loc[fractal_changed, columns].to_string(index=False))

    print(f"\nSwing tier changes: {swing_changed.sum()}")
    print(f"Internal tier changes: {internal_changed.sum()}")
    print(f"Fractal tier changes: {fractal_changed.sum()}")


if __name__ == "__main__":
    main()
