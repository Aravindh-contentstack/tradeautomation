"""Order block (OB) identification, one series per timeframe.

Stage 1 (+ 1b) of the OB roadmap in roadmap/supply-and-demand.md: identify
OBs and track whether/when each gets mitigated. Everything past that (swept
liquidity type, inducements, imbalance, displacement, flip zone,
cross-timeframe containment) is deliberately out of scope here, see that
roadmap doc for the staged plan.

Unlike the swing/internal/fractal tiers, an OB is identified per TIMEFRAME
(Daily, 4H, H1), not per tier. This is required by the factor sheet's
cross-timeframe containment checks (an H1 OB needs to ask "am I inside a 4H
OB", which only makes sense if there is one H1 OB series to ask about, not
three). A break on ANY of that timeframe's three tiers (swing, internal, or
fractal) can trigger an OB. Which tier(s) caused it is kept as metadata
(trigger_tier), feeding the future swept_liquidity_structural-* sub-factors.

Algorithm, adapted from temp-reference/order-blocks/newphewSam.py's Pine
Script logic (fractal-break triggered OB, scanning back to the last
opposite-colored candle) onto this project's own tiered break events instead
of a fresh fractal calculation:

  1. Reuse the {tier}_high_event/{tier}_low_event columns compute_daily_
     structures/compute_h4_structures/compute_h1_structures already produce.
     A "break of swing high" on any tier means price just broke out
     upward: the anchor is the most recent BEARISH candle before that break,
     and it becomes a new bullish OB (the last down-close candle before an
     up-move is read as the origin of that move). Symmetrically, "break of
     swing low" anchors on the most recent BULLISH candle, giving a bearish
     OB.
  2. Mitigation is checked candle-by-candle after the break confirms: the
     first candle whose wick overlaps the OB's [bottom, top] zone marks it
     mitigated. This is a wick-touch definition, not a close-through one,
     chosen as the simplest default. Revisit once the exact factor
     definition has been walked through with the user.

Must run AFTER compute_{tf}_structures, since it reads that function's
output columns rather than computing structure itself.
"""

import pandas as pd

DAILY_TIER_PREFIXES = ["daily_swing", "daily_internal", "daily_fractal"]
H4_TIER_PREFIXES = ["h4_swing", "h4_internal", "h4_fractal"]
H1_TIER_PREFIXES = ["h1_swing", "h1_internal", "h1_fractal"]

_OB_COLUMNS = [
    "timeframe",
    "direction",
    "formed_index",
    "formed_date",
    "top",
    "bottom",
    "trigger_tier",
    "trigger_index",
    "trigger_date",
    "mitigated",
    "mitigated_index",
    "mitigated_date",
]


def _find_anchor_candle(opens, closes, break_index, want_bullish):
    """Scans back from just before break_index for the last candle whose
    color matches want_bullish (True: close > open, False: close < open).

    Doji candles (close == open) are neither and are skipped over, same as
    the newphewSam.py reference (its close>open / close<open checks skip
    them too). Returns None if no matching candle exists further back,
    which only happens this early in the data that no real anchor exists
    yet.
    """
    for k in range(break_index - 1, -1, -1):
        if want_bullish and closes[k] > opens[k]:
            return k
        if not want_bullish and closes[k] < opens[k]:
            return k
    return None


def _apply_mitigation(order_blocks, highs, lows, dates):
    """Fills in mitigated/mitigated_index/mitigated_date on each OB dict in
    place, scanning forward from the candle right after its triggering
    break (not from formation, so the impulse leg that creates the OB is
    never mistaken for mitigating it).
    """
    n = len(highs)
    for ob in order_blocks:
        for k in range(ob["trigger_index"] + 1, n):
            if lows[k] <= ob["top"] and highs[k] >= ob["bottom"]:
                ob["mitigated"] = True
                ob["mitigated_index"] = k
                ob["mitigated_date"] = dates[k]
                break


def compute_order_blocks(df, tier_prefixes, timeframe):
    """Identifies every order block for one timeframe's OHLC data.

    df: DataFrame of OHLC candles (date, open, high, low, close) that has
        already been run through compute_{tf}_structures for every prefix
        in tier_prefixes, so {prefix}_high_event/{prefix}_low_event exist.
    tier_prefixes: the timeframe's tier column prefixes, e.g.
        DAILY_TIER_PREFIXES. Any tier's break can trigger an OB.
    timeframe: label stored on every OB row (e.g. "Daily", "4H", "H1"),
        matching the Timeframe column in factors/eu_probability_factors.csv.

    Returns a DataFrame with one row per identified OB (not one row per
    candle): timeframe, direction, formed_index/formed_date, top/bottom,
    trigger_tier (list of tier prefixes that triggered it), trigger_index/
    trigger_date, mitigated, mitigated_index, mitigated_date. Sorted by
    formed_index. This is a separate long-format table rather than extra
    wide columns on df, since several OBs can be simultaneously active and
    nested, which a per-candle scalar column can't represent.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    dates = df["date"].tolist()

    high_break_tiers = [[] for _ in range(length)]
    low_break_tiers = [[] for _ in range(length)]
    for prefix in tier_prefixes:
        high_events = df["%s_high_event" % prefix].tolist()
        low_events = df["%s_low_event" % prefix].tolist()
        for i in range(length):
            if high_events[i] == "break of swing high":
                high_break_tiers[i].append(prefix)
            if low_events[i] == "break of swing low":
                low_break_tiers[i].append(prefix)

    order_blocks = []
    for i in range(length):
        if high_break_tiers[i]:
            anchor = _find_anchor_candle(opens, closes, i, want_bullish=False)
            if anchor is not None:
                order_blocks.append({
                    "timeframe": timeframe,
                    "direction": "bullish",
                    "formed_index": anchor,
                    "formed_date": dates[anchor],
                    "top": highs[anchor],
                    "bottom": lows[anchor],
                    "trigger_tier": list(high_break_tiers[i]),
                    "trigger_index": i,
                    "trigger_date": dates[i],
                    "mitigated": False,
                    "mitigated_index": None,
                    "mitigated_date": None,
                })
        if low_break_tiers[i]:
            anchor = _find_anchor_candle(opens, closes, i, want_bullish=True)
            if anchor is not None:
                order_blocks.append({
                    "timeframe": timeframe,
                    "direction": "bearish",
                    "formed_index": anchor,
                    "formed_date": dates[anchor],
                    "top": highs[anchor],
                    "bottom": lows[anchor],
                    "trigger_tier": list(low_break_tiers[i]),
                    "trigger_index": i,
                    "trigger_date": dates[i],
                    "mitigated": False,
                    "mitigated_index": None,
                    "mitigated_date": None,
                })

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
