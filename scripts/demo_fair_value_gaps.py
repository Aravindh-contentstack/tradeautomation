"""Runs standalone FVG identification on the existing Daily structure demo
fixture and prints the resulting FVG table.

Reuses demo_daily_structure.py's build_synthetic_daily_data() rather than
building a new fixture, same reasoning as demo_order_blocks.py: this is a
smoke test that the detection/lifecycle logic runs and produces a
sane-looking table, not a validation that the FVGs line up with a real
chart.

Run from the repo root:  python scripts/demo_fair_value_gaps.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smc.liquidity.fair_value_gaps import compute_fair_value_gaps  # noqa: E402

from demo_daily_structure import build_synthetic_daily_data  # noqa: E402


def main():
    df, _ = build_synthetic_daily_data()
    fvgs = compute_fair_value_gaps(df)

    print("Daily candles: %d" % len(df))
    print("FVGs identified: %d" % len(fvgs))
    if fvgs.empty:
        return
    print(
        "  bullish: %d   bearish: %d   filled: %d   expired unfilled: %d"
        % (
            (fvgs["direction"] == "bullish").sum(),
            (fvgs["direction"] == "bearish").sum(),
            fvgs["filled"].sum(),
            (~fvgs["filled"]).sum(),
        )
    )
    print()

    header = "%-10s | %-12s | %-8s | %-8s | %-12s" % (
        "direction",
        "formed",
        "top",
        "bottom",
        "filled_at",
    )
    print(header)
    print("-" * len(header))
    for _, fvg in fvgs.iterrows():
        filled_at = fvg["filled_date"].strftime("%Y-%m-%d") if fvg["filled"] else "-"
        print(
            "%-10s | %-12s | %-8.2f | %-8.2f | %-12s"
            % (
                fvg["direction"],
                fvg["formed_date"].strftime("%Y-%m-%d"),
                fvg["top"],
                fvg["bottom"],
                filled_at,
            )
        )


if __name__ == "__main__":
    main()
