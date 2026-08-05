"""Runs Daily order block identification on the existing Daily structure
demo fixture and prints the resulting OB table.

Reuses demo_daily_structure.py's build_synthetic_daily_data() rather than
building a new fixture, since order block formation only needs the same
tiered break events that fixture was already built to exercise (its
docstring explains why it separates the three n=2/8/20 scales). Bringing
that fixture up onto a real chart isn't a step this script can do, so treat
this as a smoke test that the identification/mitigation logic runs and
produces a sane-looking table, not as a validation that the OBs are
correctly placed. Real validation is against actual Daily/4H/H1 candles,
per roadmap/supply-and-demand.md's Next Items.

Run from the repo root:  python scripts/demo_order_blocks.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from swing_structure.daily_structure import compute_daily_structures  # noqa: E402
from swing_structure.order_blocks import compute_daily_order_blocks  # noqa: E402

from demo_daily_structure import build_synthetic_daily_data  # noqa: E402


def main():
    df, manual_restarts = build_synthetic_daily_data()
    structured = compute_daily_structures(df, manual_restarts=manual_restarts)
    order_blocks = compute_daily_order_blocks(structured)

    print("Daily candles: %d" % len(structured))
    print("Order blocks identified: %d" % len(order_blocks))
    if order_blocks.empty:
        return
    print(
        "  bullish: %d   bearish: %d   mitigated: %d   unmitigated: %d"
        % (
            (order_blocks["direction"] == "bullish").sum(),
            (order_blocks["direction"] == "bearish").sum(),
            order_blocks["mitigated"].sum(),
            (~order_blocks["mitigated"]).sum(),
        )
    )
    print(
        "  caused_displacement: %d   caused_imbalance: %d"
        % (
            order_blocks["caused_displacement"].sum(),
            order_blocks["caused_imbalance"].sum(),
        )
    )
    print()

    header = "%-10s | %-12s | %-8s | %-8s | %-10s | %-22s | %-4s | %-4s | %-12s" % (
        "direction",
        "formed",
        "top",
        "bottom",
        "primary",
        "trigger_tier",
        "disp",
        "imb",
        "mitigated_at",
    )
    print(header)
    print("-" * len(header))
    for _, ob in order_blocks.iterrows():
        mitigated_at = (
            ob["mitigated_date"].strftime("%Y-%m-%d") if ob["mitigated"] else "-"
        )
        print(
            "%-10s | %-12s | %-8.2f | %-8.2f | %-10s | %-22s | %-4s | %-4s | %-12s"
            % (
                ob["direction"],
                ob["formed_date"].strftime("%Y-%m-%d"),
                ob["top"],
                ob["bottom"],
                ob["primary_tier"],
                ",".join(ob["trigger_tier"]),
                "yes" if ob["caused_displacement"] else "no",
                "yes" if ob["caused_imbalance"] else "no",
                mitigated_at,
            )
        )


if __name__ == "__main__":
    main()
