"""Which candle of a timeframe took which liquidity, one bool array per
(kind, side).

The Swept Liquidity gate asks a question about a CANDLE, not about a price
level: "did the Daily candle that just closed wick the previous week's low".
So the answer is computed here, on the owning timeframe's own candles, and
smc/liquidity/liq_state.py carries it onto the H1 timeline afterwards.

Every function returns {(kind, side): bool array of length len(df)}, where
side is "high" for liquidity price had to RISE to take and "low" for
liquidity it had to FALL to take. A long setup wants a low swept: the sell
stops under the market have been run, and what is left is buyers.

Only the FIRST candle to take a level is marked. A level that stays taken is
not being swept again on every subsequent candle, and marking it so would
turn one event into a run of them and let a single sweep dominate the gate
for as long as price stayed beyond it.
"""

import numpy as np
import pandas as pd

from smc.liquidity import levels, low_resistance
from smc.liquidity.levels import EQUALS, OLD_POINT
from smc.liquidity.low_resistance import LRLQ

BULLISH = "bullish"
BEARISH = "bearish"

HIGH = "high"
LOW = "low"

FVG = "fvg"

# Which side of the book an FVG's fill takes. A bullish gap was left by an
# up-move, so price returning to it is coming DOWN, which is a low-side
# sweep and supports a long. The mirror for a bearish gap.
_FVG_SIDE = {"bullish": LOW, "bearish": HIGH}


def _blank(length):
    return np.zeros(length, dtype=bool)


def _tier_suffix(prefix):
    """"daily_swing" -> "swing", matching order_block_quality._tier_suffix."""
    return prefix.rsplit("_", 1)[-1]


def structural_sweeps(structured_df, tier_prefixes):
    """Sweeps of each tier's own currently-active STRONG point.

    Strong is read off the structure columns rather than stored anywhere
    new, exactly as order_block_quality.compute_swept_liquidity_structural
    does: while a tier is bullish its swing LOW is the point expected to
    hold, and while it is bearish its swing HIGH is.

    That module answers the same question over an order block's formation
    leg. This one answers it per candle, for the standalone gate, and the
    two share the expression rather than the plumbing because their inputs
    (a leg, a candle) are different shapes.

    A close beyond the level cannot happen without the tier's own structure
    flipping on that same candle, which is market_structure.py's invariant,
    so no separate close test is needed.
    """
    df = structured_df.reset_index(drop=True)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    out = {}
    for prefix in tier_prefixes:
        structure = df["%s_structure" % prefix].to_numpy()
        swing_low = pd.to_numeric(df["%s_swing_low" % prefix], errors="coerce").to_numpy()
        swing_high = pd.to_numeric(df["%s_swing_high" % prefix], errors="coerce").to_numpy()

        # NaN comparisons are False, which is the wanted behaviour while a
        # tier is still warming up and has no confirmed pivot.
        with np.errstate(invalid="ignore"):
            out[(_tier_suffix(prefix), LOW)] = (structure == BULLISH) & (lows < swing_low)
            out[(_tier_suffix(prefix), HIGH)] = (structure == BEARISH) & (highs > swing_high)

    return out


# One row per sweep EVENT, the shape the *_sweep_events functions below
# return. The bool arrays the gates consume are a projection of this: they
# keep only "some level of this kind was taken on this candle" and discard
# which level and how long it had left to live.
#
# smc/liquidity/sweep_credit.py needs both of the discarded columns, so the
# events are produced first and scattered second. Keeping one producer means
# a level cannot be swept according to one consumer and not the other.
#
# expires_index is the level's own NATURAL expiry, the last candle it would
# have lived to had nothing taken it. Deliberately not valid_through_index,
# which for a swept level IS the sweep candle (ended_by == "swept") and would
# cap every credit at zero candles.
_EVENT_COLUMNS = ["kind", "side", "level", "swept_index", "expires_index"]


def _events_frame(rows):
    return pd.DataFrame(rows, columns=_EVENT_COLUMNS)


def _table_sweep_events(table, kinds, lookback, keep=None):
    """A level table's swept rows, as sweep events.

    keep: optional predicate (row, swept_index) -> bool, applied to rows
        that were swept. Used for the old-point range test below.
    """
    if len(table) == 0:
        return _events_frame([])

    rows = []
    for row in table.itertuples(index=False):
        if not row.swept or row.kind not in kinds:
            continue
        swept_index = int(row.swept_index)
        if keep is not None and not keep(row, swept_index):
            continue
        rows.append(
            {
                "kind": row.kind,
                "side": row.side,
                "level": float(row.level),
                "swept_index": swept_index,
                "expires_index": int(row.visible_from_index) + lookback,
            }
        )
    return _events_frame(rows)


def _scatter_events(events, length, kinds):
    """Sweep events as per-candle bool arrays, one per (kind, side)."""
    out = {(kind, side): _blank(length) for kind in kinds for side in (HIGH, LOW)}
    for row in events.itertuples(index=False):
        if row.kind not in kinds:
            continue
        out[(row.kind, row.side)][int(row.swept_index)] = True
    return out


def pooled_level_sweep_events(levels_table, structured_df, swing_prefix):
    """Equal highs/lows and old points, from smc/liquidity/levels.py.

    The table already resolved which candle took each level, against the
    tolerance band rather than the bare price, so this is mostly a projection
    rather than a fresh scan.

    The exception is old points, which carry one condition the detector
    deliberately does not bake in: the level has to sit OUTSIDE the swing
    range, per the user's "any high or low external to the swing range". A
    level inside the range is part of the leg price is currently working
    through, not an old extreme left behind, and the range moves, so the
    answer is only meaningful as of a particular candle.

    It is applied here, on the candle that did the sweeping, using that
    tier's own columns as of that candle. Equals are not filtered: two
    highs at the same price are liquidity wherever they sit.

    swing_prefix: the tier whose range counts, e.g. "daily_swing".
    """
    df = structured_df.reset_index(drop=True)
    swing_high = pd.to_numeric(df["%s_swing_high" % swing_prefix], errors="coerce")
    swing_high = swing_high.to_numpy()
    swing_low = pd.to_numeric(df["%s_swing_low" % swing_prefix], errors="coerce")
    swing_low = swing_low.to_numpy()

    def external(row, swept_index):
        if row.kind != OLD_POINT:
            return True
        # NaN during warm-up compares False, so an unconfirmed range keeps
        # nothing rather than keeping everything.
        if row.side == HIGH:
            return bool(row.level > swing_high[swept_index])
        return bool(row.level < swing_low[swept_index])

    return _table_sweep_events(
        levels_table, (EQUALS, OLD_POINT), levels.DEFAULT_LOOKBACK, keep=external
    )


def lrlq_sweep_events(lrlq_table):
    """Low resistance liquidity sweeps, as events."""
    return _table_sweep_events(lrlq_table, (LRLQ,), low_resistance.DEFAULT_LOOKBACK)


def pooled_level_sweeps(levels_table, structured_df, swing_prefix):
    """The bool-array view of pooled_level_sweep_events."""
    events = pooled_level_sweep_events(levels_table, structured_df, swing_prefix)
    return _scatter_events(events, len(structured_df), (EQUALS, OLD_POINT))


def lrlq_sweeps(lrlq_table, length):
    """The bool-array view of lrlq_sweep_events."""
    return _scatter_events(lrlq_sweep_events(lrlq_table), length, (LRLQ,))


def fvg_sweeps(fvg_table, df):
    """The first candle to wick into each still-active fair value gap.

    A TOUCH, not a fill. fair_value_gaps.py's own `filled` flag is the 50%
    rule that ends a gap's life; sweeping it is the shallower event of price
    reaching into the gap at all, which is what the user means by "price
    wicking the FVG gaps". A gap can therefore be swept well before it is
    filled, and only the first touch counts.
    """
    frame = df.reset_index(drop=True)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    length = len(frame)

    out = {(FVG, HIGH): _blank(length), (FVG, LOW): _blank(length)}
    if len(fvg_table) == 0:
        return out

    for gap in fvg_table.itertuples(index=False):
        side = _FVG_SIDE[gap.direction]
        start = int(gap.formed_index) + 1
        stop = min(int(gap.active_until_index), length - 1)
        for k in range(start, stop + 1):
            if lows[k] <= gap.top and highs[k] >= gap.bottom:
                out[(FVG, side)][k] = True
                break

    return out


def time_level_sweep_events(time_table, kind, df):
    """The first candle inside each time level's window to take it.

    Unlike the pivot-derived kinds, these carry no sweep state of their own:
    a session high is a fact about a clock, and which timeframe is entitled
    to sweep it is the caller's decision. Previous week is checked on Daily
    candles and previous day on 4H candles, per the user's spec.

    Each level's window is resolved to a candle range by searchsorted, so a
    level is only ever taken by a candle that was actually inside its own
    validity window.

    expires_index is the last candle of that same window, which for these
    kinds is the natural cap rather than a lookback: time_levels.py already
    encodes "live through the end of the FOLLOWING London day", so a session
    level's credit inherits the clock it was built from.
    """
    frame = df.reset_index(drop=True)
    dates = pd.DatetimeIndex(frame["date"]).to_numpy()
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    length = len(frame)

    subset = time_table[time_table["kind"] == kind]
    if len(subset) == 0:
        return _events_frame([])

    starts = np.searchsorted(
        dates, pd.DatetimeIndex(subset["visible_from_date"]).to_numpy(), side="left"
    )
    stops = np.searchsorted(
        dates, pd.DatetimeIndex(subset["valid_through_date"]).to_numpy(), side="left"
    )

    rows = []
    for row, (start, stop) in enumerate(zip(starts, stops)):
        level = float(subset["level"].iloc[row])
        side = subset["side"].iloc[row]
        end = min(int(stop), length)
        for k in range(int(start), end):
            taken = highs[k] > level if side == HIGH else lows[k] < level
            if taken:
                rows.append(
                    {
                        "kind": kind,
                        "side": side,
                        "level": level,
                        "swept_index": k,
                        "expires_index": end - 1,
                    }
                )
                break

    return _events_frame(rows)


def time_level_sweeps(time_table, kind, df):
    """The bool-array view of time_level_sweep_events."""
    events = time_level_sweep_events(time_table, kind, df)
    return _scatter_events(events, len(df), (kind,))


def merge(*groups):
    """Union of several {(kind, side): array} dicts, for one timeframe."""
    merged = {}
    for group in groups:
        merged.update(group)
    return merged
