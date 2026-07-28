"""Runs the full daily structure chain (swing then internal) on the same
synthetic dataset the other demo scripts use, then prints what
get_current_structure returns, so it's obvious by eye that it matches
the last row of the combined table.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_swing_structure import build_synthetic_data
from swing_structure.detector import compute_daily_swing_structure
from swing_structure.market_structure import compute_market_structure
from swing_structure.internal_structure import compute_internal_structure
from swing_structure.current_structure import get_current_structure


def main():
    df, manual_restart, hold_timeout = build_synthetic_data()

    result = compute_daily_swing_structure(
        df,
        manual_restart=manual_restart,
        hold_timeout=hold_timeout,
    )
    result = compute_market_structure(result)
    result = compute_internal_structure(result)

    print("Last row of the combined table:")
    print(result[["date", "market_structure", "internal_structure"]].iloc[-1].to_string())

    print("\nget_current_structure(result):")
    print(get_current_structure(result))


if __name__ == "__main__":
    main()
