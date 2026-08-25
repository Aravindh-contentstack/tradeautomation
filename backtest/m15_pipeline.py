"""The M15 substrate the entry models read: structure, liquidity, gaps.

What this is
------------
One object per instrument, holding every M15 detector's output over the
instrument's FULL M15 history, plus the two helpers that map between H1
bar space and M15 bar space.

Why full history, and not per year
----------------------------------
The same reason build_pipeline_bundle refuses to build order blocks per
year: a level formed in December belongs to January too. Detect per year
and LC-1 goes blind to every piece of liquidity sitting just across a
year boundary, silently, on six boundaries per instrument.

This is the one place the M15 layer differs from backtest/intrabar.py,
and the difference is easy to get wrong. M15Index is YEAR-SCOPED, because
runner.run_year hands build_market_context an m15_df already windowed to
the walk period. Its indices address that window. This bundle's indices
address full history. The two index spaces are both plain integers, both
plausible, and mixing them misaddresses every level with no error.

So nothing here ever accepts an index from M15Index, and nothing here
hands an index back to it. The two helpers below cross between H1 and
M15 by TIMESTAMP only:

    m15_index_at_or_after(bundle, ts)   H1 bar's timestamp -> where to
                                        start walking M15
    h1_bar_containing(h1_ts, ts)        M15 bar's timestamp -> which H1
                                        bar's validity applies

Both are searchsorted on already-sorted arrays, and both are called once
per order-block mitigation rather than once per bar, so the cost is
irrelevant and the safety is worth it.

Why there is no slice_bundle
----------------------------
An earlier draft of the plan called for one, by analogy with
ob_state.slice_universe and liq_state.slice_universe. Those exist because
the OB and liquidity universes are indexed on the H1 walk frame, which
IS re-cut per year, so their indices have to be rebased to match.

Nothing re-cuts this bundle. It is built once per instrument and read by
every year through the timestamp helpers, so there is no offset to
rebase and no rebasing bug to have. Do not add slicing here: it would
introduce exactly the index-space confusion the section above exists to
prevent.

Memory: about twenty arrays over roughly 400k bars, so on the order of
tens of MB for one instrument. scripts/backtest_multi.py processes one
instrument at a time, and the bundle is reused across all six of its
years instead of being rebuilt per year, so this is cheaper than the
per-year alternative as well as more correct.

Not here yet
------------
`credit`, the 3x ATR sweep-credit window that LC-1 needs for its "swept
the first liquidity, pushed away, came back" case. sweep_credit's API
takes an EVENTS frame (kind, side, level, swept_index, expires_index),
and the exact projection from a minor-liquidity table into that shape is
decided by what LC-1 asks of it. Building it before its only consumer
exists would be guessing at the shape. It lands with the entry models.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smc.liquidity.fair_value_gaps import compute_fair_value_gaps
from smc.liquidity.levels import compute_liquidity_levels
from smc.liquidity.low_resistance import compute_low_resistance_liquidity
from smc.liquidity.minor_liquidity import compute_minor_liquidity
from smc.market_structure.atr import compute_atr_series
from smc.market_structure.m15_structure import (
    compute_m15_structures,
    m15_column_names,
)
from backtest.intrabar import H1_DURATION
from backtest.killzone import london_fields

# The scale the M15 equals/old-point pools are drawn at. levels.py's own
# default, restated here so the M15 call site is explicit about it rather
# than inheriting a number that could change under it: LC-2B's whole
# premise is a double top, and n is what decides whether the two tops are
# even seen as pivots.
LEVELS_PIVOT_N = 2

# The scale the M15 LRLQ runs are drawn at. low_resistance.py's own
# default, restated for the same reason.
LRLQ_PIVOT_N = 1

M15_ATR_PERIOD = 14


@dataclass(frozen=True, eq=False)
class SweepIndex:
    """A liquidity table in the two shapes the entry scan needs.

    Used for both compute_minor_liquidity (LC-1's inducements) and the
    equals half of compute_liquidity_levels (LC-2B's double tops). The two
    detectors emit different columns but the scan asks them identical
    questions, so they are projected into one shape here rather than
    branching at every call site.

    Why both shapes exist. The entry scan asks two different questions of
    the same table, on wildly different budgets:

    1. "Which levels did candle j SWEEP?" asked on every M15 bar of every
       order block's lifetime, so on the order of a million times per
       instrument-year. Answered by `swept_at`, an O(1) dict lookup.
    2. "Which levels are LIVE at bar j inside this price window?" asked
       once per formed setup, so a few thousand times. Answered by a
       vectorised mask over the arrays below.

    Serving question 1 from a mask would be a full pass over every level
    per bar, which does not run. Serving question 2 from a dict would need
    an interval tree for no benefit at that call count.

    Every index here is a bundle offset, i.e. full M15 history. `swept_at`
    keys are the sweeping bar; its values are row positions into these
    arrays, not into the DataFrame's index.
    """

    side_high: "np.ndarray"       # bool, True for a high-side level
    level: "np.ndarray"           # float64
    candle_index: "np.ndarray"    # int64, the bar whose extreme this is
    first_candle: "np.ndarray"    # int64, the FIRST bar of a pooled level
    visible_from: "np.ndarray"    # int64, first bar the level is known on
    live_through: "np.ndarray"    # int64, inclusive last bar it is unswept
    swept_at: dict                # {sweeping bar -> tuple of row positions}

    def __len__(self):
        return len(self.level)


def _build_sweep_index(table, candle_column, first_candle_column=None):
    """Projects a liquidity table into SweepIndex.

    table: any frame carrying side / level / swept / swept_index /
        visible_from_index / valid_through_index. Both detectors do, by
        design: minor_liquidity.py shapes its rows after levels.py's
        precisely so this projection can be shared.
    candle_column: the column naming the bar the level's price came from.
        "candle_index" for minor liquidity, "pivot_index" for pooled
        levels. It is what LC-1's freshness test measures against.
    first_candle_column: for a POOLED level, the column naming its first
        touch. LC-2B's angle test needs both ends of a double top to
        compare their heights. Defaults to candle_column, which is
        correct for minor liquidity: a single-candle level's first and
        last touch are the same bar.

    live_through is the last bar on which the level is still UNSWEPT, so a
    swept level's window ends the bar BEFORE the sweep. That off-by-one is
    the whole point: "no other LIDs" asks what is still standing at the
    moment the trigger fires, and a level the trigger itself just took is
    not standing.
    """
    if first_candle_column is None:
        first_candle_column = candle_column

    if len(table) == 0:
        empty_i = np.array([], dtype=np.int64)
        return SweepIndex(
            side_high=np.array([], dtype=bool),
            level=np.array([], dtype=np.float64),
            candle_index=empty_i,
            first_candle=empty_i,
            visible_from=empty_i,
            live_through=empty_i,
            swept_at={},
        )

    swept = table["swept"].to_numpy(dtype=bool)
    swept_index = table["swept_index"].to_numpy(dtype="float64")
    valid_through = table["valid_through_index"].to_numpy(dtype=np.int64)

    live_through = np.where(
        swept, np.nan_to_num(swept_index, nan=0.0).astype(np.int64) - 1, valid_through
    )

    swept_at = {}
    for pos in np.flatnonzero(swept):
        bar = int(swept_index[pos])
        swept_at.setdefault(bar, []).append(int(pos))

    return SweepIndex(
        side_high=(table["side"] == "high").to_numpy(dtype=bool),
        level=table["level"].to_numpy(dtype=np.float64),
        candle_index=table[candle_column].to_numpy(dtype=np.int64),
        first_candle=table[first_candle_column].to_numpy(dtype=np.int64),
        visible_from=table["visible_from_index"].to_numpy(dtype=np.int64),
        live_through=live_through,
        swept_at={bar: tuple(rows) for bar, rows in swept_at.items()},
    )


@dataclass(frozen=True, eq=False)
class FvgIndex:
    """compute_fair_value_gaps' table as arrays, for two different questions.

    "Did THIS candle mitigate a gap" (LID with FVG, CE's has_imbalance
    reads the same table a different way) and "did a gap FORM anywhere in
    this bar range" (no imbalance while mitigation). Both are cheap as
    masks over arrays and awkward as DataFrame filters at call rates in
    the thousands.

    A gap is active for formed_index <= k <= active_until, which is the
    contract fair_value_gaps.py states.
    """

    bullish: "np.ndarray"        # bool
    formed_index: "np.ndarray"   # int64, the THIRD candle of the triple
    top: "np.ndarray"            # float64
    bottom: "np.ndarray"         # float64
    active_until: "np.ndarray"   # int64, inclusive

    def __len__(self):
        return len(self.top)

    @property
    def midpoint(self):
        return (self.top + self.bottom) / 2.0


def _build_fvg_index(fvgs):
    if len(fvgs) == 0:
        empty_i = np.array([], dtype=np.int64)
        empty_f = np.array([], dtype=np.float64)
        return FvgIndex(
            bullish=np.array([], dtype=bool),
            formed_index=empty_i,
            top=empty_f,
            bottom=empty_f,
            active_until=empty_i,
        )
    return FvgIndex(
        bullish=(fvgs["direction"] == "bullish").to_numpy(dtype=bool),
        formed_index=fvgs["formed_index"].to_numpy(dtype=np.int64),
        top=fvgs["top"].to_numpy(dtype=np.float64),
        bottom=fvgs["bottom"].to_numpy(dtype=np.float64),
        active_until=fvgs["active_until_index"].to_numpy(dtype=np.int64),
    )


@dataclass(frozen=True, eq=False)
class M15Bundle:
    """Every M15 detector's output for one instrument, on one index.

    eq=False because the fields are numpy arrays and DataFrames: the
    generated __eq__ would raise "truth value of an array is ambiguous"
    the first time anything compared two bundles.

    Every index in every field below, and every index returned by the
    helpers in this module, is an offset into THIS bundle's full M15
    history. See the module docstring on why that is not the same number
    as an M15Index offset.
    """

    ts: "np.ndarray"            # datetime64[ns], UTC, tz-naive
    open_: "np.ndarray"
    high: "np.ndarray"
    low: "np.ndarray"
    close: "np.ndarray"
    london_hour: "np.ndarray"   # int8
    london_dow: "np.ndarray"    # int8, Monday=0
    atr: "np.ndarray"           # float64, NaN through the warm-up
    structure: dict             # column name -> array, per m15_column_names()
    minor: object               # compute_minor_liquidity table
    minor_idx: object           # SweepIndex over the same table
    levels: object              # compute_liquidity_levels table (equals + old_point)
    equals_idx: object          # SweepIndex over the equals rows of `levels`
    lrlq: object                # compute_low_resistance_liquidity table
    lrlq_idx: object            # SweepIndex over the same table
    fvgs: object                # compute_fair_value_gaps table
    fvg_idx: object             # FvgIndex over the same table


def _naive_utc(dates):
    """tz-aware or naive datetimes -> datetime64[ns] numpy array, UTC.

    Same normalisation MarketContext does, for the same reason: every
    comparison downstream is then a plain int64 compare, and the tz is
    not lost information because it is UTC by construction.
    """
    idx = pd.DatetimeIndex(dates)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return idx, idx.tz_localize(None).to_numpy(dtype="datetime64[ns]")


def _atr_array(df, atr_period):
    """compute_atr_series' list-with-None -> a float64 array with NaN.

    The detectors want None to mean "no answer yet" and the walk wants a
    float array. NaN carries the same meaning and compares False against
    everything, so a warm-up bar can never satisfy a threshold test by
    accident the way 0.0 would.
    """
    series = compute_atr_series(df, atr_period=atr_period)
    return np.array(
        [np.nan if value is None else float(value) for value in series],
        dtype=np.float64,
    )


def build_m15_bundle(m15_df, atr_period=M15_ATR_PERIOD):
    """Runs every M15 detector once over full history. None if no data.

    m15_df: the instrument's whole M15 frame (date, open, high, low,
        close), ascending. Pass the FULL history, not a year window: see
        the module docstring.
    atr_period: period for the ATR that LC-2B's angle test and LC-1's
        sweep credit are both multiples of.

    Returns an M15Bundle, or None when m15_df is missing or empty. None is
    a first-class answer, exactly as it is for M15Index: nine of the ten
    instruments have M15 and NAS100 does not, so every consumer must
    degrade to "no entry candidate" rather than raise.
    """
    if m15_df is None or len(m15_df) == 0:
        return None

    df = m15_df.reset_index(drop=True)
    idx, ts = _naive_utc(df["date"])
    london_hour, london_dow = london_fields(idx)

    structured = compute_m15_structures(df, atr_period=atr_period)
    structure = {
        name: structured[name].to_numpy() for name in m15_column_names()
    }
    minor = compute_minor_liquidity(df)
    levels = compute_liquidity_levels(df, pivot_n=LEVELS_PIVOT_N)
    # Only the equals rows. LC-2B raids a double top, and an old point is
    # by definition a level touched once, so it cannot be one.
    equals = levels[levels["kind"] == "equals"].reset_index(drop=True)
    lrlq = compute_low_resistance_liquidity(df, pivot_n=LRLQ_PIVOT_N)
    fvgs = compute_fair_value_gaps(df)

    return M15Bundle(
        ts=ts,
        open_=df["open"].to_numpy(dtype=np.float64),
        high=df["high"].to_numpy(dtype=np.float64),
        low=df["low"].to_numpy(dtype=np.float64),
        close=df["close"].to_numpy(dtype=np.float64),
        london_hour=london_hour,
        london_dow=london_dow,
        atr=_atr_array(df, atr_period),
        structure=structure,
        minor=minor,
        minor_idx=_build_sweep_index(minor, "candle_index"),
        levels=levels,
        equals_idx=_build_sweep_index(equals, "pivot_index", "first_pivot_index"),
        lrlq=lrlq,
        # LRLQ names its run's bars differently: the level is the FIRST
        # pivot of the stepping run, so last_pivot_index is what freshness
        # should measure against and first_pivot_index is the run's start.
        lrlq_idx=_build_sweep_index(lrlq, "last_pivot_index", "first_pivot_index"),
        fvgs=fvgs,
        fvg_idx=_build_fvg_index(fvgs),
    )


def m15_index_at_or_after(bundle, ts):
    """First bundle M15 index at or after ts, or -1 if ts is past the end.

    Where the entry-model scan starts walking. Given the H1 mitigation
    bar's timestamp, this is the first M15 bar of that hour, or the next
    available one when that hour has a data hole.

    "At or after" rather than "inside the hour", deliberately. An hour
    with no M15 coverage yields the next available sub-bar instead of
    nothing, because the scan is looking forward from that point anyway
    and refusing to start over a hole in the mitigating hour would drop
    the whole setup.
    """
    if bundle is None:
        return -1
    stamp = np.datetime64(pd.Timestamp(ts).tz_localize(None)
                          if pd.Timestamp(ts).tzinfo is None
                          else pd.Timestamp(ts).tz_convert("UTC").tz_localize(None))
    pos = int(np.searchsorted(bundle.ts, stamp, side="left"))
    if pos >= len(bundle.ts):
        return -1
    return pos


def h1_bar_containing(h1_ts, ts):
    """Which H1 bar of h1_ts contains timestamp ts, or -1.

    The other direction: the scan walks M15 bars, and every fact it needs
    about an order block's validity (valid_through, the touch table) is
    indexed on the H1 walk frame. This is how an M15 bar asks "am I still
    inside a live zone".

    h1_ts: the walk frame's timestamp array, i.e. ctx.ts. Naive UTC
        datetime64, ascending.
    ts: one M15 bar's timestamp, from bundle.ts.

    Returns -1 when ts falls before the frame, or in a hole where the H1
    bar is itself missing, or past the frame's last hour. Callers must
    treat -1 as "unresolvable" and produce no candidate. Reading it as
    bar zero would score a setup against whatever zone happened to be
    live at the start of the walk window.
    """
    if len(h1_ts) == 0:
        return -1
    stamp = np.datetime64(ts)
    k = int(np.searchsorted(h1_ts, stamp, side="right")) - 1
    if k < 0:
        return -1
    # The half-open [bar, bar+1h) convention, enforced explicitly rather
    # than assumed. Without this an M15 bar an hour past the frame's end
    # would be attributed to its last bar, and a bar sitting in a gap
    # where the H1 candle is missing would be attributed to the candle
    # before the gap.
    if stamp >= h1_ts[k] + H1_DURATION:
        return -1
    return k
