"""Per-candle record of how each trade's Target OBs behaved while it ran.

Record-only. Nothing here influences entries, exits, sizing, or the
probability a trade was taken on. It exists so the question "what would
acting on a decaying target have been worth?" can be asked of real data
later, rather than guessed at now. The eventual answers under discussion
are moving to breakeven, taking partials, or cancelling outright as the
score falls, and none of them can be designed without first seeing how
often and how sharply the score actually moves.

Why a separate table
--------------------
This is one row per (trade, bar, timeframe), not per trade, so it does
not belong in the journal. A year of candidates held for a few dozen bars
each across three timeframes runs to tens of thousands of rows per
instrument-year, which would swamp a CSV whose whole point is that a
human can read it.

Off by default (`run_year(log_targets=False)`), because it costs a
per-bar factor evaluation over the life of every candidate and changes no
result.

The factor answers are packed into a bitmask rather than one column per
factor. There are around thirty of them per timeframe, most absent on any
given bar, and a wide sparse frame would be mostly nulls. TARGET_LOG_BITS
is the decoder, and it is ordered, so appending a new factor is safe but
reordering silently reinterprets every stored row.
"""

import os

import pandas as pd

from backtest.context import bar_timestamp
from backtest.entry_ob import TARGET_SEARCH_R, WEEKLY_TARGET_SEARCH_R
from backtest.factors import (
    CONTAINMENT_FACTOR,
    OB_QUALITY_FACTORS,
    SWEPT_LIQUIDITY_FACTORS,
    TARGET_GATE_CHILDREN,
    TARGET_GATE_H1_CHILDREN,
    evaluate_liquidity_target_factors,
    evaluate_ob_target_factors,
)

TIMEFRAMES = ["Daily", "4H", "H1"]

# Ordered, as (gate, factor suffix). APPEND ONLY: reordering silently
# reinterprets every stored mask.
#
# The first twelve are spelled out rather than derived, because they are the
# positions any already-stored log was written against. Deriving them from
# SWEPT_LIQUIDITY_FACTORS was fine while that list was frozen; it grew from
# five entries to thirteen with the liquidity work, which would have pushed
# swept_liquidity from bit 9 to bit 17 and the two containment bits along
# with it. Everything new goes on the end instead.
_ORIGINAL_BITS = [
    ("ob_target", "caused_displacement"),
    ("ob_target", "caused_imbalance"),
    ("ob_target", "has_inducement"),
    ("ob_target", "flip_zone"),
    ("ob_target", "swept_liquidity_swing"),
    ("ob_target", "swept_liquidity_internal"),
    ("ob_target", "swept_liquidity_fractal"),
    ("ob_target", "swept_liquidity_fvg"),
    ("ob_target", "swept_liquidity_previous_candle"),
    ("ob_target", "swept_liquidity"),
    ("ob_target", "within_daily_ob"),
    ("ob_target", "within_h4_ob"),
]

# Two gates share the table because they answer the same question about the
# same trade, and the liquidity half is arguably the more interesting one:
# an OB target that gets reached flips to opposing, while a liquidity target
# that gets reached simply disappears, and "how fast does the fuel run out"
# is exactly what this log exists to make answerable.
TARGET_LOG_BITS = (
    _ORIGINAL_BITS
    + [
        ("ob_target", suffix)
        for suffix, _ in OB_QUALITY_FACTORS + SWEPT_LIQUIDITY_FACTORS
        if ("ob_target", suffix) not in _ORIGINAL_BITS
    ]
    + [
        ("ob_target", column)
        for column in sorted(set(CONTAINMENT_FACTOR.values()))
        if ("ob_target", column) not in _ORIGINAL_BITS
    ]
    + [
        ("liquidity_target", child)
        for child in TARGET_GATE_CHILDREN + TARGET_GATE_H1_CHILDREN
    ]
)

TARGET_LOG_COLUMNS = [
    "trade_id",
    "bar_index",
    "bar_time",
    "timeframe",
    "answers_mask",
    "present_mask",
    "reached",
]


def trade_id_for(instrument, signal):
    """Stable join key back to the journal.

    Entry time plus direction, which together identify a candidate
    uniquely: two candidates cannot enter the same bar in the same
    direction, since only one order block can be the trigger.
    """
    return "%s-%s-%s" % (
        instrument,
        pd.Timestamp(signal["entry_time"]).strftime("%Y%m%dT%H%M"),
        signal["direction"],
    )


def _pack(factor_results, prefix):
    """Factor answers for one timeframe as (answers, present) bitmasks.

    Two masks, not one, because a factor that was omitted and a factor
    that answered no are different facts and a single bit cannot tell them
    apart.
    """
    answers = 0
    present = 0
    for bit, (gate, suffix) in enumerate(TARGET_LOG_BITS):
        key = "%s_%s_%s" % (prefix, gate, suffix)
        if key not in factor_results:
            continue
        present |= 1 << bit
        if factor_results[key]:
            answers |= 1 << bit
    return answers, present


def collect_target_log(ctx, signal, walk, trade_id):
    """One row per (bar, timeframe) over the life of one trade.

    Walks from the entry bar to whichever bar the trade actually ended on,
    re-resolving the target each time. The target can change mid-trade:
    the nearest one may be invalidated, at which point the next one out
    takes over, and that hand-off is exactly what this is here to capture.
    """
    obs = getattr(ctx, "obs", None)
    if obs is None:
        return []

    from backtest.factors import TIMEFRAME_KEYS

    start = signal["idx"]
    end = walk.get("terminal_idx")
    if end is None:
        end = start
    max_distance = TARGET_SEARCH_R * signal["r_distance"]
    week_max_distance = WEEKLY_TARGET_SEARCH_R * signal["r_distance"]
    liq = getattr(ctx, "liq", None)

    rows = []
    for k in range(start, min(end, obs.n - 1) + 1):
        high = float(ctx.high[k])
        low = float(ctx.low[k])
        results = evaluate_ob_target_factors(
            obs, k, signal["direction"], max_distance, high, low
        )
        results.update(
            evaluate_liquidity_target_factors(
                liq, k, signal["direction"], high, low,
                max_distance, week_max_distance,
            )
        )
        if not results:
            continue

        for timeframe in TIMEFRAMES:
            prefix = TIMEFRAME_KEYS[timeframe]
            answers, present = _pack(results, prefix)
            if present == 0:
                continue

            series = obs.series.get(timeframe)
            lookup = obs.target_above if signal["direction"] == "bullish" else obs.target_below
            ob_row = int(lookup[timeframe][k])
            reached = False
            if series is not None and ob_row >= 0:
                reached = low <= series.top[ob_row] and high >= series.bottom[ob_row]

            rows.append({
                "trade_id": trade_id,
                "bar_index": k,
                "bar_time": bar_timestamp(ctx, k),
                "timeframe": timeframe,
                "answers_mask": answers,
                "present_mask": present,
                "reached": reached,
            })

    return rows


def save_target_log(rows, path):
    """Parquet, not CSV: this table is machine-read and large."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    pd.DataFrame(rows, columns=TARGET_LOG_COLUMNS).to_parquet(path, index=False)


def decode_mask(answers_mask, present_mask):
    """A stored row back into {"gate.suffix": bool}, omissions dropped."""
    out = {}
    for bit, (gate, suffix) in enumerate(TARGET_LOG_BITS):
        if present_mask & (1 << bit):
            out["%s.%s" % (gate, suffix)] = bool(answers_mask & (1 << bit))
    return out
