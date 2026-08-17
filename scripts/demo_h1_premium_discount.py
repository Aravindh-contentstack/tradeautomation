"""Runs the H1 swing + internal premium/discount zones on the same
synthetic candles demo_h1_structure.py uses, and prints them.

Reuses build_synthetic_h1_data() rather than a new fixture: the tiers'
swing_high/swing_low/structure columns it produces are exactly what
compute_h1_premium_discount needs, and duplicating the generator would
only risk the two demos drifting out of sync.

Run from the repo root:  python scripts/demo_h1_premium_discount.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_h1_structure import build_synthetic_h1_data
from smc.market_structure.h1_structure import compute_h1_structures
from smc.market_structure.premium_discount import compute_h1_premium_discount, h1_zone_column_names
from smc.market_structure.current_structure import get_current_structure

TIERS = ("h1_swing", "h1_internal")


def _same(a, b):
    """Equality that treats two missing values as equal (NaN != NaN)."""
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return a == b


def _zone(row, tier):
    value = row["%s_zone" % tier]
    return "-" if pd.isna(value) else value


def main():
    df, manual_restarts = build_synthetic_h1_data()
    result = compute_h1_structures(df, manual_restarts=manual_restarts)
    result = compute_h1_premium_discount(result, tiers=TIERS)

    print("H1 candles: %d" % len(result))
    print("First: %s   Last: %s" % (result["date"].iloc[0], result["date"].iloc[-1]))
    print()

    print("Rows where either tier's zone changed:")
    header = "%-18s | %10s | %-24s | %-24s" % ("date", "close", "h1_swing", "h1_internal")
    print(header)
    print("-" * len(header))
    prev = {tier: object() for tier in TIERS}
    for _, row in result.iterrows():
        changed = False
        for tier in TIERS:
            current = row["%s_zone" % tier]
            if not _same(current, prev[tier]):
                changed = True
            prev[tier] = current
        if not changed:
            continue
        print(
            "%-18s | %10.4f | %-24s | %-24s"
            % (
                row["date"].strftime("%Y-%m-%d %H:%M"),
                row["close"],
                _zone(row, "h1_swing"),
                _zone(row, "h1_internal"),
            )
        )
    print()

    print("Zone distribution per tier (premium / discount / undetermined):")
    for tier in TIERS:
        column = result["%s_zone" % tier]
        premium = (column == "premium").sum()
        discount = (column == "discount").sum()
        undetermined = column.isna().sum()
        print(
            "  %-12s premium: %3d   discount: %3d   undetermined: %3d   total: %3d"
            % (tier, premium, discount, undetermined, premium + discount + undetermined)
        )
    print()

    print("Raw swing_high/swing_low vs. the recalibrated current_high/current_low,")
    print("printed only on rows where either differs, per tier:")
    for tier in TIERS:
        print("  -- %s --" % tier)
        header2 = "  %-18s | %10s | %12s | %12s | %12s | %12s" % (
            "date", "close", "raw_high", "current_high", "raw_low", "current_low",
        )
        print(header2)
        print("  " + "-" * (len(header2) - 2))
        prev_raw_high = object()
        prev_current_high = object()
        prev_raw_low = object()
        prev_current_low = object()
        for _, row in result.iterrows():
            raw_high = row["%s_swing_high" % tier]
            current_high = row["%s_current_high" % tier]
            raw_low = row["%s_swing_low" % tier]
            current_low = row["%s_current_low" % tier]
            if (
                _same(raw_high, prev_raw_high)
                and _same(current_high, prev_current_high)
                and _same(raw_low, prev_raw_low)
                and _same(current_low, prev_current_low)
            ):
                prev_raw_high, prev_current_high = raw_high, current_high
                prev_raw_low, prev_current_low = raw_low, current_low
                continue
            prev_raw_high, prev_current_high = raw_high, current_high
            prev_raw_low, prev_current_low = raw_low, current_low
            if pd.isna(raw_high) and pd.isna(raw_low):
                continue
            print(
                "  %-18s | %10.4f | %12.4f | %12.4f | %12.4f | %12.4f"
                % (
                    row["date"].strftime("%Y-%m-%d %H:%M"),
                    row["close"],
                    raw_high,
                    current_high,
                    raw_low,
                    current_low,
                )
            )
        print()

    print("Hand-pickable rows (verify by eye against the printed high/low/close):")
    picks = [
        ("h1_swing", "bullish", True, "near swing high, expect premium"),
        ("h1_swing", "bullish", False, "near swing low, expect discount"),
        ("h1_swing", "bearish", False, "near swing low, expect premium"),
        ("h1_swing", "bearish", True, "near swing high, expect discount"),
        ("h1_internal", "bullish", True, "near swing high, expect premium"),
        ("h1_internal", "bullish", False, "near swing low, expect discount"),
        ("h1_internal", "bearish", False, "near swing low, expect premium"),
        ("h1_internal", "bearish", True, "near swing high, expect discount"),
    ]
    for tier, direction, near_high, note in picks:
        high_col = "%s_current_high" % tier
        low_col = "%s_current_low" % tier
        structure_col = "%s_structure" % tier
        zone_col = "%s_zone" % tier

        candidates = result[result[structure_col] == direction]
        if candidates.empty:
            print("  %-10s %-8s %-40s (no rows found)" % (tier, direction, note))
            continue

        span = candidates[high_col] - candidates[low_col]
        position = (candidates["close"] - candidates[low_col]) / span
        row = candidates.loc[(position - (1.0 if near_high else 0.0)).abs().idxmin()]

        print(
            "  %-10s %-8s %-40s high=%.4f low=%.4f close=%.4f -> zone=%s"
            % (
                tier,
                direction,
                note,
                row[high_col],
                row[low_col],
                row["close"],
                row[zone_col],
            )
        )
    print()

    print("get_current_structure(result):")
    print(get_current_structure(result))

    zone_columns = h1_zone_column_names(TIERS)
    print("\nZone columns present: %s" % zone_columns)
    print("h1_fractal_zone present: %s" % ("h1_fractal_zone" in result.columns))
    print(
        "current_high/_low present for both tiers: %s"
        % all(
            "%s_current_high" % tier in result.columns and "%s_current_low" % tier in result.columns
            for tier in TIERS
        )
    )


if __name__ == "__main__":
    main()
