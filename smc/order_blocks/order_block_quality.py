"""OB quality/confluence factors, layered on top of swing_structure/
order_blocks.py's output rather than folding into that module directly, per
its own docstring's "deliberately out of scope" list.

Caused Imbalance and Caused Displacement need no code here: they already
exist as columns on order_blocks.py's output, just corrected there for
multi-candle zones.

Swept-liquidity children come in two shapes. The structural ones (swing,
internal, fractal) are derived from the structure columns; the rest come
from the standalone detectors in smc/liquidity/ via apply_candle_sweeps,
which takes any {(kind, side): per-candle bool array} and answers it over
each OB's formation leg. That is how Old Points, Equals and LRLQ arrive,
and on H1 the time-based kinds too.

H1 is the reason the time-based kinds are OB-anchored rather than a gate of
their own. Confirmed with the user: an H1 sweep that does not break
structure produces no order block, and therefore nothing to trade from, so
every H1 sweep has to be attached to a zone to mean anything.

Every function here takes an OB DataFrame (as produced by
compute_daily_order_blocks / compute_h4_order_blocks / compute_h1_order_
blocks) and returns a copy with new boolean columns appended, the same
"copy of df with N new columns" convention order_blocks.py itself uses.
Each is timeframe-agnostic: the same code serves Daily, 4H, and H1, driven
by which OB table and which tier_prefixes list gets passed in.

Rule confirmed with the user and applied consistently below: an OB only
stops counting as still-available liquidity for another OB's confluence
(inducement's nearer OB, containment's parent, flip_zone's flipped OB)
once it is INVALIDATED, not merely mitigated, matching order_blocks.py's
own "mitigated OBs can still be tradeable" rule.

That exclusion is a POINT-IN-TIME comparison, not a blanket one. Nearly
every OB is invalidated eventually, so testing the lifetime `invalidated`
flag would let events from the future decide an answer that gets scored
on earlier candles. Each function instead compares against the candle the
subject OB itself went live (its earliest_trigger_index, or
earliest_trigger_date where the two tables are on different timeframes
and indices are not comparable). The result is that every column here is
a fixed property of the OB, settled once at its trigger and never
revisited, which is what lets the Mitigation OB's factors freeze at entry
without any per-candle recomputation. The single exception is
flip_zone, which by definition cannot be known that early; it carries a
flip_zone_known_from index so callers can gate on it instead.
"""

import numpy as np
import pandas as pd

BULLISH = "bullish"
BEARISH = "bearish"

HIGH = "high"
LOW = "low"

# One-third of the parent's own zone height, the confirmed containment
# threshold when a child OB isn't fully engulfed by its parent.
CONTAINMENT_MIN_OVERLAP_FRACTION = 1.0 / 3.0


def _as_float(column):
    """An index column as float64, with "never happened" as NaN.

    The index columns hold None where an event never occurred. NaN carries
    that through arithmetic and compares False against anything, so the
    accompanying boolean flag (invalidated, mitigated) stays the only
    thing deciding whether the index means anything.
    """
    return pd.to_numeric(column, errors="coerce").to_numpy(dtype=float)


def _tier_suffix(prefix):
    """"daily_swing" -> "swing", "h4_internal" -> "internal", etc, so the
    same output column names (swept_liquidity_swing/internal/fractal) are
    used regardless of which timeframe's tier_prefixes list is passed in.
    """
    return prefix.rsplit("_", 1)[-1]


def apply_candle_sweeps(order_blocks, per_candle, kinds):
    """Adds one swept_liquidity_{kind} column per kind in `kinds`.

    per_candle: {(kind, side): bool array over that timeframe's candles},
        as produced by smc/liquidity/sweeps.py. "side" is which way price
        had to travel to take the liquidity.
    kinds: the kinds to emit columns for. A kind absent from per_candle is
        skipped rather than erroring, so a caller running only part of the
        stack still gets a usable table.

    For each OB, scans [leg_start_index, earliest_trigger_index] (the leg
    that produced it, regardless of which tier actually triggered it) for a
    candle that took liquidity on the side the OB's own direction wants: a
    demand zone is interesting because the sell stops UNDER the market were
    run first, so a bullish OB looks for a low-side sweep.

    Whether a given CANDLE swept something does not depend on which OB is
    asking, so it is computed once (upstream) and answered per leg as a
    range query here. A cumulative count turns "was there a sweep anywhere
    in [a, b]" into one subtraction, instead of re-walking every leg candle
    by candle, which matters because H1 carries tens of thousands of zones.
    """
    result = order_blocks.reset_index(drop=True).copy()
    bullish = (result["direction"].to_numpy() == BULLISH)

    if len(result) == 0:
        for kind in kinds:
            result["swept_liquidity_%s" % kind] = []
        return result

    leg_starts = result["leg_start_index"].to_numpy(dtype=int)
    ends = result["earliest_trigger_index"].to_numpy(dtype=int) + 1

    for kind in kinds:
        low_side = per_candle.get((kind, LOW))
        high_side = per_candle.get((kind, HIGH))
        if low_side is None or high_side is None:
            continue

        cum_low = np.concatenate(([0], np.cumsum(low_side)))
        cum_high = np.concatenate(([0], np.cumsum(high_side)))
        any_low = (cum_low[ends] - cum_low[leg_starts]) > 0
        any_high = (cum_high[ends] - cum_high[leg_starts]) > 0
        result["swept_liquidity_%s" % kind] = np.where(bullish, any_low, any_high)

    return result


def compute_swept_liquidity_structural(order_blocks, structured_df, tier_prefixes):
    """Adds swept_liquidity_swing/internal/fractal (one column per prefix
    in tier_prefixes, named by its tier suffix).

    "Strong vs weak pivot" is read straight off structured_df's existing
    columns, not stored anywhere new: for tier T, whichever side is
    currently holding T's trend (swing_low while T is bullish, swing_high
    while T is bearish) is the strong point being checked for a sweep.

    The per-candle test lives in smc/liquidity/sweeps.structural_sweeps,
    which the standalone Swept Liquidity gate reads too. This function is
    the OB-anchored view of the same fact: the same candles, asked about
    over one order block's formation leg.
    """
    from smc.liquidity.sweeps import structural_sweeps

    per_candle = structural_sweeps(structured_df, tier_prefixes)
    return apply_candle_sweeps(
        order_blocks, per_candle, [_tier_suffix(prefix) for prefix in tier_prefixes]
    )


def compute_fvg_confluence(order_blocks, fvg_table, ohlc_df):
    """Adds swept_liquidity_fvg: True if the OB's own formation candles
    wick into a same-direction FVG that was already active (unfilled,
    within its validity window) before the OB formed, without any of
    those candles closing past the FVG's far edge.
    """
    closes = ohlc_df.reset_index(drop=True)["close"].to_numpy(dtype=float)

    result = order_blocks.reset_index(drop=True).copy()
    if len(result) == 0 or len(fvg_table) == 0:
        result["swept_liquidity_fvg"] = [False] * len(result)
        return result

    gaps = fvg_table.reset_index(drop=True)
    g_bullish = (gaps["direction"].to_numpy() == BULLISH)
    g_top = gaps["top"].to_numpy(dtype=float)
    g_bottom = gaps["bottom"].to_numpy(dtype=float)
    g_formed = gaps["formed_index"].to_numpy(dtype=float)
    g_until = gaps["active_until_index"].to_numpy(dtype=float)

    ob_bullish = (result["direction"].to_numpy() == BULLISH)
    ob_top = result["top"].to_numpy(dtype=float)
    ob_bottom = result["bottom"].to_numpy(dtype=float)
    ob_anchor = result["formed_index"].to_numpy(dtype=int)
    ob_zone_end = result["zone_end_index"].to_numpy(dtype=int)

    # The cheap tests (direction, active window, price overlap) are done
    # against every gap at once. Only the survivors get the per-candle
    # far-edge check, which is a handful of bars at most and usually finds
    # its answer on the first candidate.
    column = np.zeros(len(result), dtype=bool)
    for i in range(len(result)):
        anchor = int(ob_anchor[i])
        eligible = (
            (g_bullish == ob_bullish[i])
            & (g_formed < anchor)
            & (g_until >= anchor)
            & (ob_bottom[i] <= g_top)
            & (ob_top[i] >= g_bottom)
        )
        candidates = np.flatnonzero(eligible)
        if len(candidates) == 0:
            continue

        window = closes[anchor : int(ob_zone_end[i]) + 1]
        for j in candidates:
            if ob_bullish[i]:
                breached = bool((window < g_bottom[j]).any())
            else:
                breached = bool((window > g_top[j]).any())
            if not breached:
                column[i] = True
                break

    result["swept_liquidity_fvg"] = column
    return result


def compute_previous_candle_sweep(order_blocks, ohlc_df):
    """Adds swept_liquidity_previous_candle: only meaningful for
    single-candle OBs (zone_end_index == formed_index, i.e. _shape_zone
    never merged a neighbor in). True if the anchor candle wicks below
    (bullish) / above (bearish) the immediately preceding candle without
    closing past it.
    """
    df = ohlc_df.reset_index(drop=True)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    result = order_blocks.reset_index(drop=True).copy()
    if len(result) == 0:
        result["swept_liquidity_previous_candle"] = []
        return result

    anchor = result["formed_index"].to_numpy(dtype=int)
    zone_end = result["zone_end_index"].to_numpy(dtype=int)
    bullish = (result["direction"].to_numpy() == BULLISH)

    # Only single-candle zones qualify, and an anchor at index 0 has no
    # preceding candle to sweep. Both are masked out rather than branched
    # on, with prev clamped so the gathers stay in bounds.
    usable = (zone_end == anchor) & (anchor > 0)
    prev = np.maximum(anchor - 1, 0)

    swept_bull = (lows[anchor] < lows[prev]) & (closes[anchor] >= lows[prev])
    swept_bear = (highs[anchor] > highs[prev]) & (closes[anchor] <= highs[prev])
    column = usable & np.where(bullish, swept_bull, swept_bear)

    result["swept_liquidity_previous_candle"] = column
    return result


def compute_inducement(order_blocks):
    """Adds has_inducement: True for OB X if some earlier-formed,
    same-direction OB Y that was still alive when X went live sits nearer
    to price with gap < max(height(X), height(Y)), per the confirmed
    formula (compared against the larger of the two zones' heights, no
    price-reaction confirmation required).

    "Still alive when X went live" is an AS-OF test against X's own
    earliest_trigger_index, deliberately not the lifetime `invalidated`
    flag. Y is almost always invalidated eventually, so reading the
    lifetime flag would let the future decide X's answer, and X's answer
    is scored on candles that come before Y dies. The as-of form also
    makes every quality column a fixed property of the OB, evaluated once
    at its trigger, which is exactly the frozen semantics the Mitigation
    OB factors need.
    """
    result = order_blocks.reset_index(drop=True).copy()
    n = len(result)
    if n == 0:
        result["has_inducement"] = []
        return result

    # Vectorised per X over all candidate Ys at once. The pairwise scan
    # this replaces is quadratic, which was fine on Daily's few hundred
    # zones and is not on H1's tens of thousands.
    bullish = (result["direction"].to_numpy() == BULLISH)
    tops = result["top"].to_numpy(dtype=float)
    bottoms = result["bottom"].to_numpy(dtype=float)
    heights = tops - bottoms
    formed = result["formed_index"].to_numpy(dtype=float)
    invalidated = result["invalidated"].to_numpy(dtype=bool)
    invalidated_index = _as_float(result["invalidated_index"])
    earliest_trigger = result["earliest_trigger_index"].to_numpy(dtype=float)

    has_inducement = np.zeros(n, dtype=bool)
    for x in range(n):
        eligible = (bullish == bullish[x]) & (formed < formed[x])
        eligible[x] = False
        eligible &= ~(invalidated & (invalidated_index < earliest_trigger[x]))
        if not eligible.any():
            continue

        if bullish[x]:
            gap = bottoms - tops[x]
        else:
            gap = bottoms[x] - tops
        nearer = gap > 0
        limit = np.maximum(heights, heights[x])
        has_inducement[x] = bool((eligible & nearer & (gap < limit)).any())

    result["has_inducement"] = has_inducement
    return result


def compute_flip_zone(order_blocks):
    """Adds is_flip_zone, flipped_ob_index, and flip_zone_known_from.

    Z is a flip zone if some earlier, opposite-direction OB W was
    mitigated by one of Z's own formation candles (the "reacts on W"
    condition, read straight off W's existing mitigated_index rather than
    re-scanning OHLC), and W was later invalidated at or after Z went
    live.

    Unlike every other quality column, this one is NOT knowable at Z's own
    trigger: it depends on W dying, which happens at or after that point.
    flip_zone_known_from records the candle the answer first becomes
    available (W's invalidated_index), so a caller scoring Z on candle k
    can gate on `k >= flip_zone_known_from` instead of reading a flag the
    future filled in. Rows that are not flip zones get None.
    """
    result = order_blocks.reset_index(drop=True).copy()
    n = len(result)
    if n == 0:
        result["is_flip_zone"] = []
        result["flipped_ob_index"] = []
        result["flip_zone_known_from"] = []
        return result

    bullish = (result["direction"].to_numpy() == BULLISH)
    formed_index = result["formed_index"].to_numpy(dtype=float)
    zone_end_index = result["zone_end_index"].to_numpy(dtype=float)
    earliest_trigger_index = result["earliest_trigger_index"].to_numpy(dtype=float)
    mitigated = result["mitigated"].to_numpy(dtype=bool)
    mitigated_index = _as_float(result["mitigated_index"])
    invalidated = result["invalidated"].to_numpy(dtype=bool)
    invalidated_index = _as_float(result["invalidated_index"])

    is_flip_zone = np.zeros(n, dtype=bool)
    flipped_ob_index = [None] * n
    flip_zone_known_from = [None] * n

    # Vectorised per Z for the same reason as compute_inducement: the
    # pairwise form is quadratic and H1 carries tens of thousands of rows.
    candidate_w = mitigated & invalidated
    for z in range(n):
        eligible = candidate_w & (bullish != bullish[z]) & (formed_index < formed_index[z])
        eligible[z] = False
        eligible &= mitigated_index >= formed_index[z]
        eligible &= mitigated_index <= zone_end_index[z]
        eligible &= invalidated_index >= earliest_trigger_index[z]
        matches = np.flatnonzero(eligible)
        if len(matches) == 0:
            continue
        w = int(matches[0])
        is_flip_zone[z] = True
        flipped_ob_index[z] = w
        flip_zone_known_from[z] = invalidated_index[w]

    result["is_flip_zone"] = is_flip_zone
    result["flipped_ob_index"] = flipped_ob_index
    result["flip_zone_known_from"] = flip_zone_known_from
    return result


def compute_containment(child_order_blocks, parent_order_blocks, column_name):
    """Adds `column_name` to a copy of child_order_blocks: True if a
    same-direction parent OB that was alive and visible when the child
    went live either fully engulfs the child's zone, or overlaps it by at
    least a third of the PARENT's own height.

    Cross-timeframe, so parent/child row indices address different frames
    and are never comparable. Prices are compared directly; the two
    as-of time tests go through DATES, which are the only common
    coordinate the two tables share:

      - the parent must already be visible (its own trigger candle has
        closed) at the child's earliest trigger, otherwise a Daily zone
        would be readable hours before the Daily candle confirming it
        closed, which is the exact lookahead merge_asof exists to prevent;
      - the parent must not have died before that point.

    Intended calls: compute_containment(h4_obs, daily_obs, "within_daily_ob")
    and compute_containment(h1_obs, h4_obs, "within_h4_ob").
    """
    children = child_order_blocks.reset_index(drop=True).copy()
    parents = parent_order_blocks.reset_index(drop=True)

    if len(children) == 0 or len(parents) == 0:
        children[column_name] = [False] * len(children)
        return children

    p_bullish = (parents["direction"].to_numpy() == BULLISH)
    p_top = parents["top"].to_numpy(dtype=float)
    p_bottom = parents["bottom"].to_numpy(dtype=float)
    p_height = p_top - p_bottom
    p_live_from = pd.DatetimeIndex(parents["earliest_trigger_date"]).to_numpy()
    p_invalidated = parents["invalidated"].to_numpy(dtype=bool)
    p_dead_at = pd.DatetimeIndex(parents["invalidated_date"]).to_numpy()

    c_bullish = (children["direction"].to_numpy() == BULLISH)
    c_top = children["top"].to_numpy(dtype=float)
    c_bottom = children["bottom"].to_numpy(dtype=float)
    c_live_from = pd.DatetimeIndex(children["earliest_trigger_date"]).to_numpy()

    within = np.zeros(len(children), dtype=bool)
    for i in range(len(children)):
        live_at = c_live_from[i]
        eligible = (p_bullish == c_bullish[i]) & (p_live_from <= live_at)
        eligible &= ~(p_invalidated & (p_dead_at < live_at))
        if not eligible.any():
            continue

        overlap = np.minimum(c_top[i], p_top) - np.maximum(c_bottom[i], p_bottom)
        engulfed = (c_top[i] <= p_top) & (c_bottom[i] >= p_bottom)
        with np.errstate(divide="ignore", invalid="ignore"):
            fraction = np.where(p_height > 0, overlap / p_height, 0.0)
        big_enough = engulfed | (fraction >= CONTAINMENT_MIN_OVERLAP_FRACTION)
        within[i] = bool((eligible & (overlap > 0) & big_enough).any())

    children[column_name] = within
    return children
