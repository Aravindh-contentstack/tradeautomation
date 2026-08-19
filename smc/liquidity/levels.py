"""Horizontal liquidity levels built from fractal pivots: Equal Highs/Lows
and Old Points.

One detector, two factor names. Both concepts are the same object seen at
different touch counts, so splitting them into two modules would mean two
copies of the pooling, the sweep test and the expiry clock. A level touched
once is an old point (an unliquidated high or low price has left behind); the
same level touched again is an equal high or low. Nothing else distinguishes
them.

Reference: temp-reference/liquidity/old-points/willy.py, which is Pine, not
Python. What was taken from it is the POOL idea (nearby pivots are one level
with a touch count, not several levels), its ATR-multiple tolerance, and its
band-edge sweep test. What was left behind is everything volume-driven (the
data has no volume column), its 0-100 strength score, its adaptive pivot
length, and its swept-versus-mitigated close-back distinction. The two equals
references were rejected outright: temp-reference/liquidity/equals/alpha.py
only ever compares adjacent candles, and equals/outofoptions.py tests exact
float equality, which never fires on this data.

Why one row per TOUCH, not one per pool
---------------------------------------
A pool's level, band and touch count all change every time another pivot
joins it, and its classification changes with them: it is an old point until
the second touch arrives and an equals from then on. Emitting one row per
pool would mean a row whose `kind` and `level` were decided by touches that
had not happened yet, and every consumer scoring an earlier candle would read
the future's answer. That is the same lookahead trap
smc/order_blocks/order_block_quality.py's docstring describes.

So each touch closes the previous version of its level and opens a new one.
Every row is then a fixed fact over a closed index window
[visible_from_index, valid_through_index], and a consumer asking about candle
k reads exactly the version that was true at k.

Lifecycle, in the order a level can end (`ended_by`):

  "superseded"  another pivot joined the pool, so a newer version of this
                same level takes over from the next candle.
  "swept"       price wicked past the band edge. This is what the Swept
                Liquidity gate reads, and it ends the pool outright: a level
                that has been taken is not liquidity any more.
  "expired"     LOOKBACK candles passed with no new touch and no sweep.
  "data_end"    the history ran out while the level was still live. NOT the
                same as expired, and deliberately distinguished, for the same
                reason order_blocks.py distinguishes them: the live bot's
                newest levels would otherwise be reported dead.
"""

import pandas as pd

from smc.market_structure.atr import compute_atr_series
from smc.market_structure.fractal_detector import compute_fractal_pivots

# The fractal scale pivots are drawn at. n=2 is the finest tier the structure
# side uses, confirmed with the user as the right seed for old points: "any
# high or low external to the swing range", not only the major ones.
DEFAULT_PIVOT_N = 2

# Two pivots are the same level when they sit within this multiple of ATR of
# each other. Confirmed with the user. An ATR multiple rather than ticks or
# pips so the same number serves EUR_USD and XAU_USD without re-tuning, which
# is the one thing willy.py is explicit about in its own tooltip.
DEFAULT_TOLERANCE = 0.25

# Candles a level survives without a fresh touch. The same 100 used by
# fair_value_gaps.py and by order_blocks.py's OB_LOOKBACK, on every
# timeframe, so zones, gaps and levels all age on one rule.
DEFAULT_LOOKBACK = 100

# Touches at or above this count make the level an "equals" rather than an
# "old point". Two highs at the same price is the whole concept.
EQUALS_MIN_TOUCHES = 2

HIGH = "high"
LOW = "low"

EQUALS = "equals"
OLD_POINT = "old_point"

_LEVEL_COLUMNS = [
    "side",
    "kind",
    "level",
    "level_top",
    "level_bot",
    "touch_count",
    "pool_id",
    "pivot_index",
    "first_pivot_index",
    "visible_from_index",
    "visible_from_date",
    "valid_through_index",
    "ended_by",
    "swept",
    "swept_index",
    "swept_date",
]


class _Pool:
    """One level, accumulating touches until something ends it.

    Mutable and short-lived: it exists only during the forward walk, and
    emits an immutable row every time it changes.
    """

    __slots__ = (
        "pool_id",
        "side",
        "level",
        "half_band",
        "touch_count",
        "first_pivot_index",
        "pivot_index",
        "visible_from_index",
    )

    def __init__(self, pool_id, side, price, half_band, pivot_index, confirmed_index):
        self.pool_id = pool_id
        self.side = side
        self.level = price
        self.half_band = half_band
        self.touch_count = 1
        self.first_pivot_index = pivot_index
        self.pivot_index = pivot_index
        self.visible_from_index = confirmed_index

    @property
    def top(self):
        return self.level + self.half_band

    @property
    def bottom(self):
        return self.level - self.half_band

    def absorb(self, price, half_band, pivot_index, confirmed_index):
        """Folds another pivot in, moving the level to the running mean.

        A plain touch-count-weighted average, so a third touch shifts the
        level a third as far as the second did. Later touches should not be
        able to drag an established level around, and this is the cheapest
        expression of that.
        """
        self.level = (self.level * self.touch_count + price) / (self.touch_count + 1)
        self.half_band = half_band
        self.touch_count += 1
        self.pivot_index = pivot_index
        self.visible_from_index = confirmed_index


def _row(pool, valid_through_index, ended_by, dates, swept_index=None):
    """One immutable version of a level, over a closed index window."""
    return {
        "side": pool.side,
        "kind": OLD_POINT if pool.touch_count < EQUALS_MIN_TOUCHES else EQUALS,
        "level": pool.level,
        "level_top": pool.top,
        "level_bot": pool.bottom,
        "touch_count": pool.touch_count,
        "pool_id": pool.pool_id,
        "pivot_index": pool.pivot_index,
        "first_pivot_index": pool.first_pivot_index,
        "visible_from_index": pool.visible_from_index,
        "visible_from_date": dates[pool.visible_from_index],
        "valid_through_index": valid_through_index,
        "ended_by": ended_by,
        "swept": swept_index is not None,
        "swept_index": swept_index,
        "swept_date": dates[swept_index] if swept_index is not None else None,
    }


def compute_liquidity_levels(
    df,
    pivot_n=DEFAULT_PIVOT_N,
    tolerance=DEFAULT_TOLERANCE,
    lookback=DEFAULT_LOOKBACK,
    atr_period=14,
):
    """Identifies every equal-high/low and old-point level in df's candles.

    df: DataFrame of OHLC candles (date, open, high, low, close), ascending.
        Timeframe-agnostic, same as every other detector here.
    pivot_n: fractal scale the pivots are drawn at.
    tolerance: ATR multiple within which two pivots are the same level.
    lookback: candles a level survives without a fresh touch.
    atr_period: period for the ATR the tolerance is a multiple of.

    Returns a DataFrame with one row per level VERSION (see the module
    docstring), columns per _LEVEL_COLUMNS, sorted by visible_from_index.

    Pivots confirming before ATR has warmed up are skipped rather than
    given a fallback tolerance: without an ATR there is no answer to "how
    close is equal", and inventing one would quietly fabricate levels over
    the first fortnight of every instrument's history.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    dates = df["date"].tolist()
    atr_series = compute_atr_series(df, atr_period=atr_period)

    pivots = compute_fractal_pivots(df, n=pivot_n)
    pivots_at = {}
    for pivot in pivots.itertuples(index=False):
        pivots_at.setdefault(pivot.confirmed_index, []).append(pivot)

    rows = []
    active = []
    next_pool_id = 0

    for k in range(length):
        # --- 1. NEW TOUCHES ---------------------------------------------
        # Admitted before this candle's sweep test, so a pivot confirming
        # on k is never swept by k itself. It cannot be: the n candles
        # after a fractal are strictly inside it by construction, which is
        # what makes it a fractal at all.
        for pivot in pivots_at.get(k, ()):
            atr = atr_series[k]
            if atr is None:
                continue
            half_band = tolerance * atr / 2.0
            price = pivot.pivot_price

            match = None
            for pool in active:
                if pool.side != pivot.side:
                    continue
                if abs(price - pool.level) <= tolerance * atr:
                    match = pool
                    break

            if match is None:
                active.append(
                    _Pool(next_pool_id, pivot.side, price, half_band,
                          pivot.pivot_index, k)
                )
                next_pool_id += 1
                continue

            # The version that was true up to yesterday is closed off
            # before the new one opens, so the two windows never overlap.
            rows.append(_row(match, k - 1, "superseded", dates))
            match.absorb(price, half_band, pivot.pivot_index, k)

        # --- 2. SWEEPS AND EXPIRY ---------------------------------------
        # Only levels that have been standing since before k, for the
        # reason above.
        survivors = []
        for pool in active:
            if pool.visible_from_index >= k:
                survivors.append(pool)
                continue

            swept = highs[k] > pool.top if pool.side == HIGH else lows[k] < pool.bottom
            if swept:
                rows.append(_row(pool, k, "swept", dates, swept_index=k))
                continue

            if k - pool.visible_from_index >= lookback:
                rows.append(_row(pool, k, "expired", dates))
                continue

            survivors.append(pool)
        active = survivors

    # Whatever is still standing when the data ends is still LIVE. Marked
    # "data_end" rather than "expired" so a consumer can tell "we do not
    # know yet" from "this is dead", which is the same distinction
    # order_blocks._expiry_index draws and for the same reason: the live
    # bot's newest levels are all in this bucket on every run.
    for pool in active:
        rows.append(_row(pool, length - 1, "data_end", dates))

    rows.sort(key=lambda row: (row["visible_from_index"], row["pool_id"]))
    return pd.DataFrame(rows, columns=_LEVEL_COLUMNS)
