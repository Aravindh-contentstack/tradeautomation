"""Runs the OB quality/confluence factors (swing_structure/
order_block_quality.py) on top of Daily order block identification, plus a
4H-in-Daily containment smoke test.

Reuses the existing synthetic fixtures rather than building new ones, same
smoke-test framing as demo_order_blocks.py and demo_fair_value_gaps.py:
confirms the pipeline runs end-to-end and produces sane-looking columns,
not that the factors are correctly placed against a real chart.

Run from the repo root:  python scripts/demo_order_block_quality.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smc.market_structure.daily_structure import compute_daily_structures  # noqa: E402
from smc.market_structure.h4_structure import compute_h4_structures  # noqa: E402
from smc.liquidity.fair_value_gaps import compute_fair_value_gaps  # noqa: E402
from smc.order_blocks.order_blocks import (  # noqa: E402
    DAILY_TIER_PREFIXES,
    compute_daily_order_blocks,
    compute_h4_order_blocks,
)
from smc.order_blocks.order_block_quality import (  # noqa: E402
    compute_containment,
    compute_flip_zone,
    compute_fvg_confluence,
    compute_inducement,
    compute_previous_candle_sweep,
    compute_swept_liquidity_structural,
)

from demo_daily_structure import build_synthetic_daily_data  # noqa: E402
from demo_h4_structure import build_synthetic_h4_data  # noqa: E402


def _count(order_blocks, column):
    return int(order_blocks[column].sum())


def main():
    df, manual_restarts = build_synthetic_daily_data()
    structured = compute_daily_structures(df, manual_restarts=manual_restarts)
    order_blocks = compute_daily_order_blocks(structured)

    fvgs = compute_fair_value_gaps(structured)

    order_blocks = compute_swept_liquidity_structural(order_blocks, structured, DAILY_TIER_PREFIXES)
    order_blocks = compute_fvg_confluence(order_blocks, fvgs, structured)
    order_blocks = compute_previous_candle_sweep(order_blocks, structured)
    order_blocks = compute_inducement(order_blocks)
    order_blocks = compute_flip_zone(order_blocks)

    # These print the raw, pre-H1 formation-time facts, not the gated
    # runtime factor backtest/factors.py actually scores. In particular
    # swept_liquidity_fvg here is unaffected by staleness: this demo shows
    # whether an OB touched a gap at formation, which never changes,
    # regardless of what later happens to that gap or to the OB itself.
    print("Daily order blocks: %d" % len(order_blocks))
    for column in (
        "swept_liquidity_swing",
        "swept_liquidity_internal",
        "swept_liquidity_fractal",
        "swept_liquidity_fvg",
        "swept_liquidity_previous_candle",
        "has_inducement",
        "is_flip_zone",
    ):
        print("  %-32s %d" % (column, _count(order_blocks, column)))
    print()

    header = "%-10s | %-12s | %-4s | %-4s | %-4s | %-4s | %-4s | %-4s" % (
        "direction",
        "formed",
        "swg",
        "int",
        "frc",
        "fvg",
        "ind",
        "flip",
    )
    print(header)
    print("-" * len(header))
    for _, ob in order_blocks.iterrows():
        print(
            "%-10s | %-12s | %-4s | %-4s | %-4s | %-4s | %-4s | %-4s"
            % (
                ob["direction"],
                ob["formed_date"].strftime("%Y-%m-%d"),
                "y" if ob["swept_liquidity_swing"] else "n",
                "y" if ob["swept_liquidity_internal"] else "n",
                "y" if ob["swept_liquidity_fractal"] else "n",
                "y" if ob["swept_liquidity_fvg"] else "n",
                "y" if ob["has_inducement"] else "n",
                "y" if ob["is_flip_zone"] else "n",
            )
        )

    # Containment is inherently cross-timeframe, so it's demoed against the
    # 4H module's own independent synthetic fixture rather than a shared
    # price path: this only exercises the mechanism, it says nothing about
    # whether these two fixtures' OBs would actually nest on a real chart.
    h4_df, h4_manual_restarts = build_synthetic_h4_data()
    h4_structured = compute_h4_structures(h4_df, manual_restarts=h4_manual_restarts)
    h4_order_blocks = compute_h4_order_blocks(h4_structured)
    h4_order_blocks = compute_containment(h4_order_blocks, order_blocks, "within_daily_ob")

    print()
    print("4H order blocks: %d, within_daily_ob: %d" % (len(h4_order_blocks), _count(h4_order_blocks, "within_daily_ob")))


if __name__ == "__main__":
    main()
