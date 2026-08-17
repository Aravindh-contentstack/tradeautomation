"""Runs the full Daily structure chain (all three tiers) on the same
synthetic dataset the other demo scripts use, then prints what
get_current_structure returns, so it's obvious by eye that it matches
the last row of the combined table.

Rebuilt July 29 2026 to call compute_daily_structures (the live wiring
current_structure.py's _STRUCTURE_COLUMNS now points at) instead of the
old compute_daily_swing_structure -> compute_market_structure ->
compute_internal_structure chain, so this demo exercises the real,
current wiring rather than a chain current_structure.py no longer reads.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_swing_structure import build_synthetic_data
from smc.market_structure.current_structure import get_current_structure
from smc.market_structure.daily_structure import compute_daily_structures


def main():
    df, manual_restart, _hold_timeout = build_synthetic_data()

    result = compute_daily_structures(
        df, manual_restarts={"daily_swing": manual_restart}
    )

    print("Last row of the combined table:")
    print(
        result[
            ["date", "daily_swing_structure", "daily_internal_structure", "daily_fractal_structure"]
        ]
        .iloc[-1]
        .to_string()
    )

    print("\nget_current_structure(result):")
    print(get_current_structure(result))


if __name__ == "__main__":
    main()
