"""Order block (OB) identification, one series per timeframe.

Implements the design confirmed in roadmap/supply-and-demand.md's "OB
lifecycle" and "Candle sizing and zone shaping" sections, worked out against
five reference indicators in temp-reference/order-blocks/. Covers OB
identification, zone shaping, caused_displacement, caused_imbalance, and
basic mitigation. Invalidation, swept liquidity, has-inducements, flipzone,
and cross-timeframe containment are deliberately out of scope, see that
roadmap doc for the staged plan.

An OB is identified per TIMEFRAME (Daily, 4H, H1), not per tier. A break on
ANY of that timeframe's three tiers (swing, internal, or fractal) can
trigger an OB. Which tier(s) caused it is kept as metadata (trigger_tier,
primary_tier), feeding the future swept_liquidity_structural-* sub-factors.
When multiple tiers independently resolve to the exact same anchor candle,
they are merged into one OB row rather than kept as duplicates, classified
by the largest tier involved (primary_tier). Different anchor candles are
NEVER merged, even if their zones overlap in price, since a nearer,
smaller-tier OB sitting in front of a farther, larger-tier one is a genuine
inducement setup, not clutter to clean up.

Algorithm, per (tier, break event) candidate:

  1. The "leg" for a break is the span from wherever that tier's OWN pivot
     (its existing swing_high/swing_low column, produced by the existing
     Williams Fractal detector, no new fractal logic here) was last
     confirmed, up to and including the break candle itself.
  2. Scan that leg forward for the first candle whose range exceeds 1.0x
     ATR (the "displacement candle"). If none qualifies, the break candle
     itself is used as the fallback displacement point, so a break never
     fails to produce an OB for want of a standout candle.
  3. The anchor is the candle immediately before the displacement candle.
     Its color is irrelevant.
  4. The anchor's own range against the same ATR series decides how its
     zone is shaped: medium (0.5x-1x ATR) uses the full wick both edges,
     high (>1x ATR) trims the near/unimportant edge to the candle's body,
     low (<0.5x ATR) is too small to use alone and gets merged with a
     neighbor (forward first, then backward, then both) until the combined
     range reaches medium, accepting a still-oversized or still-undersized
     result rather than extending past 3 candles.
  5. caused_displacement and caused_imbalance are checked on the same
     3-candle window (anchor, anchor+1, anchor+2): displacement asks
     whether anchor+1 alone closed past the anchor's high/low, imbalance
     asks whether the gap reaches all the way out to anchor+2.
  6. Mitigation is checked candle-by-candle starting from the EARLIEST
     trigger index across any tier that resolved to this anchor (the OB is
     live the moment any contributing tier confirms it): the first candle
     whose wick overlaps the OB's [bottom, top] zone marks it mitigated,
     wick-touch, not close-through, a one-time lifetime flag.

Must run AFTER compute_{tf}_structures, since it reads that function's
output columns rather than computing structure itself.
"""

import pandas as pd

from swing_structure.atr import compute_atr_series

DAILY_TIER_PREFIXES = ["daily_swing", "daily_internal", "daily_fractal"]
H4_TIER_PREFIXES = ["h4_swing", "h4_internal", "h4_fractal"]
H1_TIER_PREFIXES = ["h1_swing", "h1_internal", "h1_fractal"]

# One ATR-relative meaning of "unusually large" throughout this module: the
# same threshold marks a displacement candle during anchor selection and the
# "high" zone-shaping band, and the same 0.5x boundary marks both "too small
# to use alone" and (implicitly) the upper edge of "medium".
DISPLACEMENT_ATR_MULTIPLE = 1.0
LOW_BAND_ATR_MULTIPLE = 0.5
HIGH_BAND_ATR_MULTIPLE = 1.0

ATR_PERIOD = 14

_OB_COLUMNS = [
    "timeframe",
    "direction",
    "formed_index",
    "formed_date",
    "top",
    "bottom",
    "trigger_tier",
    "primary_tier",
    "trigger_index",
    "trigger_date",
    "caused_displacement",
    "caused_imbalance",
    "mitigated",
    "mitigated_index",
    "mitigated_date",
]


def _values_equal(a, b):
    """NaN-safe equality, so two "not confirmed yet" values compare equal
    instead of the usual NaN != NaN, matching the pattern already used in
    scripts/demo_daily_structure.py's _same().
    """
    a_missing = pd.isna(a)
    b_missing = pd.isna(b)
    if a_missing and b_missing:
        return True
    if a_missing or b_missing:
        return False
    return a == b


def _pivot_confirmation_indices(values):
    """For every index k, the most recent index where the currently-active
    value (as of k) was first confirmed, i.e. a ta.valuewhen(value !=
    value[1], bar_index, 0)-equivalent computed with one forward pass.

    Used to find where a tier's OWN swing_high/swing_low pivot was last
    confirmed, so the anchor-selection leg can start there rather than at
    an arbitrary fixed lookback.
    """
    length = len(values)
    result = [0] * length
    last_change = 0
    for k in range(1, length):
        if not _values_equal(values[k], values[k - 1]):
            last_change = k
        result[k] = last_change
    return result


def _find_displacement_anchor(highs, lows, atr_series, leg_start, break_index, atr_multiple):
    """Scans [leg_start, break_index] (inclusive) for the first candle whose
    range exceeds atr_multiple times its own ATR, the "displacement
    candle". Falls back to break_index itself if none qualifies. Returns
    the candle immediately before the displacement candle (the anchor), or
    None if that would fall before the start of the data.
    """
    displacement_index = None
    for k in range(leg_start, break_index + 1):
        atr_k = atr_series[k]
        if atr_k is None:
            continue
        if (highs[k] - lows[k]) > atr_multiple * atr_k:
            displacement_index = k
            break

    if displacement_index is None:
        displacement_index = break_index

    anchor = displacement_index - 1
    if anchor < 0:
        return None
    return anchor


def _band(range_value, atr_value):
    """Classifies a range as "low" (< 0.5x ATR), "high" (> 1x ATR), or
    "medium" (in between). Defaults to "medium" (the no-trim, full-wick
    case) if ATR isn't known yet, the safest behavior during warm-up.
    """
    if atr_value is None:
        return "medium"
    if range_value < LOW_BAND_ATR_MULTIPLE * atr_value:
        return "low"
    if range_value > HIGH_BAND_ATR_MULTIPLE * atr_value:
        return "high"
    return "medium"


def _combined_range(indices, highs, lows):
    """Highest high to lowest low across a set of candle indices, treating
    them as one merged virtual candle.
    """
    top = max(highs[j] for j in indices)
    bottom = min(lows[j] for j in indices)
    return top, bottom


def _shape_zone(anchor, direction, opens, highs, lows, closes, atr_series):
    """Returns (top, bottom) for an OB anchored at `anchor`, per the
    confirmed "Candle sizing and zone shaping" rules.
    """
    atr_at_anchor = atr_series[anchor]
    anchor_range = highs[anchor] - lows[anchor]
    band = _band(anchor_range, atr_at_anchor)

    if band == "medium":
        return highs[anchor], lows[anchor]

    if band == "high":
        if direction == "bullish":
            # Keep the far (low-side) wick, trim the near (high-side) edge
            # to the body.
            return max(opens[anchor], closes[anchor]), lows[anchor]
        # Bearish: keep the far (high-side) wick, trim the near (low-side)
        # edge to the body.
        return highs[anchor], min(opens[anchor], closes[anchor])

    # band == "low": too small to use alone, try merging with a neighbor.
    length = len(highs)

    forward_result = None
    if anchor + 1 < length:
        f_top, f_bottom = _combined_range([anchor, anchor + 1], highs, lows)
        f_band = _band(f_top - f_bottom, atr_at_anchor)
        if f_band == "medium":
            return f_top, f_bottom
        forward_result = (f_top, f_bottom, f_band)

    backward_result = None
    if anchor - 1 >= 0:
        b_top, b_bottom = _combined_range([anchor - 1, anchor], highs, lows)
        b_band = _band(b_top - b_bottom, atr_at_anchor)
        if b_band == "medium":
            return b_top, b_bottom
        backward_result = (b_top, b_bottom, b_band)

    # Neither 2-candle attempt was medium (or one/both didn't exist).
    both_high = (
        forward_result is not None
        and forward_result[2] == "high"
        and backward_result is not None
        and backward_result[2] == "high"
    )
    if both_high:
        # Adding a third candle can only keep the range the same size or
        # grow it, never shrink it back down, so it can't help here.
        return forward_result[0], forward_result[1]

    if anchor - 1 >= 0 and anchor + 1 < length:
        return _combined_range([anchor - 1, anchor, anchor + 1], highs, lows)

    # Too close to the edge of the data for a 3-candle combo. Fall back to
    # whichever single attempt exists, else the anchor's own wick.
    if forward_result is not None:
        return forward_result[0], forward_result[1]
    if backward_result is not None:
        return backward_result[0], backward_result[1]
    return highs[anchor], lows[anchor]


def _apply_mitigation(order_blocks, highs, lows, dates):
    """Fills in mitigated/mitigated_index/mitigated_date on each OB dict in
    place, scanning forward from the candle right after the EARLIEST
    trigger index across any tier that resolved to this anchor (not from
    formation, so the impulse leg that creates the OB is never mistaken for
    mitigating it).
    """
    n = len(highs)
    for ob in order_blocks:
        for k in range(ob["mitigation_start_index"] + 1, n):
            if lows[k] <= ob["top"] and highs[k] >= ob["bottom"]:
                ob["mitigated"] = True
                ob["mitigated_index"] = k
                ob["mitigated_date"] = dates[k]
                break


def compute_order_blocks(df, tier_prefixes, timeframe, atr_period=ATR_PERIOD):
    """Identifies every order block for one timeframe's OHLC data.

    df: DataFrame of OHLC candles (date, open, high, low, close) that has
        already been run through compute_{tf}_structures for every prefix
        in tier_prefixes, so {prefix}_swing_high/{prefix}_swing_low and
        {prefix}_high_event/{prefix}_low_event exist.
    tier_prefixes: the timeframe's tier column prefixes, ordered LARGEST to
        SMALLEST (e.g. DAILY_TIER_PREFIXES), since that order is also used
        to pick primary_tier when multiple tiers merge into one OB.
    timeframe: label stored on every OB row (e.g. "Daily", "4H", "H1"),
        matching the Timeframe column in factors/eu_probability_factors.csv.
    atr_period: passed straight to compute_atr_series, shared by both the
        displacement-candle scan and the zone-shaping bands.

    Returns a DataFrame with one row per identified OB (not one row per
    candle), columns per _OB_COLUMNS. Sorted by formed_index. This is a
    separate long-format table rather than extra wide columns on df, since
    several OBs can be simultaneously active and nested, which a per-candle
    scalar column can't represent.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    dates = df["date"].tolist()

    atr_series = compute_atr_series(df, atr_period=atr_period)

    confirmed_high = {}
    confirmed_low = {}
    for prefix in tier_prefixes:
        confirmed_high[prefix] = _pivot_confirmation_indices(
            df["%s_swing_high" % prefix].tolist()
        )
        confirmed_low[prefix] = _pivot_confirmation_indices(
            df["%s_swing_low" % prefix].tolist()
        )

    # One candidate per (tier, break event), each independently running its
    # own leg-scan and anchor selection.
    candidates = []
    for prefix in tier_prefixes:
        high_events = df["%s_high_event" % prefix].tolist()
        low_events = df["%s_low_event" % prefix].tolist()
        for i in range(length):
            if high_events[i] == "break of swing high":
                leg_start = confirmed_high[prefix][i - 1] if i > 0 else 0
                anchor = _find_displacement_anchor(
                    highs, lows, atr_series, leg_start, i, DISPLACEMENT_ATR_MULTIPLE
                )
                if anchor is not None:
                    candidates.append(
                        {"direction": "bullish", "anchor": anchor, "tier": prefix, "trigger_index": i}
                    )
            if low_events[i] == "break of swing low":
                leg_start = confirmed_low[prefix][i - 1] if i > 0 else 0
                anchor = _find_displacement_anchor(
                    highs, lows, atr_series, leg_start, i, DISPLACEMENT_ATR_MULTIPLE
                )
                if anchor is not None:
                    candidates.append(
                        {"direction": "bearish", "anchor": anchor, "tier": prefix, "trigger_index": i}
                    )

    # Group by (direction, anchor): the only way two candidates ever merge.
    # Different anchors, even overlapping in price, are never merged.
    groups = {}
    for candidate in candidates:
        key = (candidate["direction"], candidate["anchor"])
        groups.setdefault(key, []).append(candidate)

    order_blocks = []
    for (direction, anchor), group in groups.items():
        tiers_in_group = [p for p in tier_prefixes if any(c["tier"] == p for c in group)]
        primary_tier = tiers_in_group[0]
        primary_candidate = next(c for c in group if c["tier"] == primary_tier)
        earliest_trigger_index = min(c["trigger_index"] for c in group)

        top, bottom = _shape_zone(anchor, direction, opens, highs, lows, closes, atr_series)

        if direction == "bullish":
            caused_displacement = (anchor + 1 < length) and (closes[anchor + 1] > highs[anchor])
            caused_imbalance = (anchor + 2 < length) and (lows[anchor + 2] > highs[anchor])
        else:
            caused_displacement = (anchor + 1 < length) and (closes[anchor + 1] < lows[anchor])
            caused_imbalance = (anchor + 2 < length) and (highs[anchor + 2] < lows[anchor])

        order_blocks.append(
            {
                "timeframe": timeframe,
                "direction": direction,
                "formed_index": anchor,
                "formed_date": dates[anchor],
                "top": top,
                "bottom": bottom,
                "trigger_tier": tiers_in_group,
                "primary_tier": primary_tier,
                "trigger_index": primary_candidate["trigger_index"],
                "trigger_date": dates[primary_candidate["trigger_index"]],
                "caused_displacement": caused_displacement,
                "caused_imbalance": caused_imbalance,
                "mitigated": False,
                "mitigated_index": None,
                "mitigated_date": None,
                # Internal-only, dropped by the explicit `columns=` below.
                "mitigation_start_index": earliest_trigger_index,
            }
        )

    _apply_mitigation(order_blocks, highs, lows, dates)
    order_blocks.sort(key=lambda ob: ob["formed_index"])

    return pd.DataFrame(order_blocks, columns=_OB_COLUMNS)


def compute_daily_order_blocks(df):
    """Order blocks for Daily, triggered by a break on any of daily_swing/
    daily_internal/daily_fractal. df must already carry compute_daily_
    structures's output columns.
    """
    return compute_order_blocks(df, DAILY_TIER_PREFIXES, "Daily")


def compute_h4_order_blocks(df):
    """Order blocks for 4H, triggered by a break on any of h4_swing/
    h4_internal/h4_fractal. df must already carry compute_h4_structures's
    output columns.
    """
    return compute_order_blocks(df, H4_TIER_PREFIXES, "4H")


def compute_h1_order_blocks(df):
    """Order blocks for H1, triggered by a break on any of h1_swing/
    h1_internal/h1_fractal. df must already carry compute_h1_structures's
    output columns.
    """
    return compute_order_blocks(df, H1_TIER_PREFIXES, "H1")
