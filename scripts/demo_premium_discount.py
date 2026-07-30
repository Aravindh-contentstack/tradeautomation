"""Runs the 4H swing + internal premium/discount zones on the same
synthetic candles demo_h4_structure.py uses, and prints them.

Reuses build_synthetic_h4_data() rather than a new fixture: the tiers'
swing_high/swing_low/structure columns it produces are exactly what
compute_h4_premium_discount needs, and duplicating the generator would
only risk the two demos drifting out of sync.

Run from the repo root:  python scripts/demo_premium_discount.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_h4_structure import build_synthetic_h4_data
from swing_structure.h4_structure import compute_h4_structures
from swing_structure.premium_discount import compute_h4_premium_discount, h4_zone_column_names
from swing_structure.current_structure import get_current_structure

TIERS = ("h4_swing", "h4_internal")


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
    df, manual_restarts = build_synthetic_h4_data()
    result = compute_h4_structures(df, manual_restarts=manual_restarts)
    result = compute_h4_premium_discount(result, tiers=TIERS)

    print("4H candles: %d" % len(result))
    print("First: %s   Last: %s" % (result["date"].iloc[0], result["date"].iloc[-1]))
    print()

    print("Rows where either tier's zone changed:")
    header = "%-18s | %10s | %-24s | %-24s" % ("date", "close", "h4_swing", "h4_internal")
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
                _zone(row, "h4_swing"),
                _zone(row, "h4_internal"),
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

    print("Raw swing_high/swing_low vs. the recalibrated current_high/current_low")
    print("h4_swing uses for premium/discount (h4_internal is unaffected, still on")
    print("its raw columns), printed only on rows where either differs:")
    header2 = "%-18s | %10s | %12s | %12s | %12s | %12s" % (
        "date", "close", "raw_high", "current_high", "raw_low", "current_low",
    )
    print(header2)
    print("-" * len(header2))
    prev_raw_high = object()
    prev_current_high = object()
    prev_raw_low = object()
    prev_current_low = object()
    for _, row in result.iterrows():
        raw_high = row["h4_swing_swing_high"]
        current_high = row["h4_swing_current_high"]
        raw_low = row["h4_swing_swing_low"]
        current_low = row["h4_swing_current_low"]
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
            "%-18s | %10.4f | %12.4f | %12.4f | %12.4f | %12.4f"
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
        ("h4_swing", "bullish", True, "near swing high, expect premium"),
        ("h4_swing", "bullish", False, "near swing low, expect discount"),
        ("h4_swing", "bearish", False, "near swing low, expect premium"),
        ("h4_swing", "bearish", True, "near swing high, expect discount"),
    ]
    for tier, direction, near_high, note in picks:
        if tier == "h4_swing":
            high_col = "h4_swing_current_high"
            low_col = "h4_swing_current_low"
        else:
            high_col = "%s_swing_high" % tier
            low_col = "%s_swing_low" % tier
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

    zone_columns = h4_zone_column_names(TIERS)
    print("\nZone columns present: %s" % zone_columns)
    print("h4_fractal_zone present: %s" % ("h4_fractal_zone" in result.columns))
    print(
        "h4_internal_current_high/_low present: %s (should be False, that tier still uses raw columns)"
        % ("h4_internal_current_high" in result.columns or "h4_internal_current_low" in result.columns)
    )


if __name__ == "__main__":
    main()
