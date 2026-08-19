"""Order blocks re-expressed on the H1 timeline, plus the per-candle
lookups the signal engine needs.

order_blocks.py emits one long-format table per timeframe, and every
*_index column in it addresses that timeframe's OWN reset-index frame. The
backtest walks a single H1-indexed frame. Nothing can ask "which Daily OBs
are live on this H1 candle" until those two coordinate systems are
reconciled, and this module is the reconciliation: it happens once, at
pipeline build time, and no timeframe-local index is ever carried past it.

No-lookahead, the part that matters
-----------------------------------
An OB becomes readable at its EARLIEST TRIGGER, never at its formation
candle. The anchor candle sits in the past relative to the break that
reveals it, sometimes by a whole leg, so treating formed_index as the
visibility point would hand the engine a zone it could not have known
about yet.

The conversion from a timeframe-local index to an H1 index is
`searchsorted(h1_ts, tf_date + tf_duration, "left")`, which is the exact
numpy mirror of pipeline.py's `merge_asof(left_on="date",
right_on="close_time", direction="backward")`. Both express the same rule:
a higher-timeframe row is visible to an H1 row only once that row's candle
has CLOSED. Written as one expression with tf_duration as the only
difference, so the two visibility models cannot drift apart. For H1 itself
tf_duration is one hour, which reproduces exactly the
`range(earliest_trigger_index + 1, n)` start that _apply_mitigation
already uses.

That expression now lives in smc/timeline.py, since smc/liquidity/
liq_state.py needs the identical rule and a second copy of it is precisely
the drift this paragraph exists to prevent.

Liveness is stored as index THRESHOLDS per OB (visible_from,
valid_through), never as lifetime booleans. That is what makes it safe to
build this over full history and then window it per backtest year: the
thresholds carry their own time semantics, so a slice cannot accidentally
reveal a zone early or hide one that was still alive.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smc.timeline import (
    DAILY_DURATION,
    H1_DURATION,
    H4_DURATION,
    TIMEFRAME_DURATIONS,
    to_h1_index as _to_h1_index,
)

# Re-exported: several callers and tests import these from here, which is
# where they lived before smc/timeline.py existed.
__all__ = [
    "DAILY_DURATION",
    "H4_DURATION",
    "H1_DURATION",
    "TIMEFRAME_DURATIONS",
    "ObSeries",
    "ObUniverse",
    "QUALITY_COLUMNS",
    "to_h1_space",
    "build_ob_universe",
    "slice_universe",
]

# Quality columns carried onto ObSeries. Each is a fixed property of the
# OB settled at its own trigger (see order_block_quality.py's docstring),
# so they need no per-candle recomputation. Columns absent from a given
# table are skipped rather than erroring, so a caller that ran only part
# of the quality stack still gets a usable series.
QUALITY_COLUMNS = [
    "caused_displacement",
    "caused_imbalance",
    "has_inducement",
    "is_flip_zone",
    "swept_liquidity_swing",
    "swept_liquidity_internal",
    "swept_liquidity_fractal",
    "swept_liquidity_old_point",
    "swept_liquidity_equals",
    "swept_liquidity_lrlq",
    "swept_liquidity_fvg",
    "swept_liquidity_previous_candle",
    # H1 only. An H1 sweep of a session or daily/weekly level only counts
    # when it produced an order block, so these live on the zone rather
    # than in a gate of their own. See order_block_quality.py's docstring.
    "swept_liquidity_asian",
    "swept_liquidity_london",
    "swept_liquidity_ny",
    "swept_liquidity_previous_day",
    "swept_liquidity_previous_week",
    "within_daily_ob",
    "within_h4_ob",
]


@dataclass(frozen=True, eq=False)
class ObSeries:
    """One timeframe's OB table, with every index in H1 space.

    An OB is usable on H1 bar k iff visible_from[j] <= k <= valid_through[j].
    valid_through absorbs the "still tradeable on the killing candle" rule
    from order_blocks._kill, so no caller has to remember a +1.
    """

    timeframe: str
    top: np.ndarray
    bottom: np.ndarray
    midpoint: np.ndarray
    sign: np.ndarray
    visible_from: np.ndarray
    mitigated_at: np.ndarray
    valid_through: np.ndarray
    flip_known_from: np.ndarray
    touch_at: list
    quality: dict
    src_index: np.ndarray

    def __len__(self):
        return len(self.top)


@dataclass(frozen=True, eq=False)
class ObUniverse:
    """Every timeframe's ObSeries plus the per-candle arrays the signal
    engine reads, all on the same H1 index space.

    trigger_ob / trigger_touch_no answer "did a valid H1 OB take a
    qualifying (progressively deeper) touch on this candle, and which".
    target_above / target_below answer "what is the nearest valid,
    unmitigated OB in each direction from this candle's close". mitigated_htf
    answers "is price currently inside a valid higher-timeframe zone".
    All are plain int arrays of length n, filled by one forward sweep.
    """

    n: int
    series: dict
    trigger_ob: np.ndarray
    trigger_touch_no: np.ndarray
    target_above: dict
    target_below: dict
    mitigated_htf: dict


def to_h1_space(ob_table, tf_dates, h1_ts, timeframe):
    """Converts one OB table into an ObSeries on the H1 timeline.

    tf_dates: that timeframe's own reset-index date column, tz-aware UTC.
    h1_ts: the merged frame's date column, same dtype, ascending.
    """
    n = len(h1_ts)
    tf_duration = TIMEFRAME_DURATIONS[timeframe]
    table = ob_table.reset_index(drop=True)
    tf_dates = pd.DatetimeIndex(tf_dates)
    count = len(table)

    if count == 0:
        empty_i = np.zeros(0, dtype=np.int64)
        empty_f = np.zeros(0, dtype=np.float64)
        return ObSeries(
            timeframe=timeframe,
            top=empty_f,
            bottom=empty_f,
            midpoint=empty_f,
            sign=np.zeros(0, dtype=np.int8),
            visible_from=empty_i,
            mitigated_at=empty_i,
            valid_through=empty_i,
            flip_known_from=empty_i,
            touch_at=[],
            quality={},
            src_index=empty_i,
        )

    top = table["top"].to_numpy(dtype=np.float64)
    bottom = table["bottom"].to_numpy(dtype=np.float64)

    visible_from = _to_h1_index(
        table["earliest_trigger_index"].tolist(), tf_dates, h1_ts, tf_duration, n, n
    )
    mitigated_at = _to_h1_index(
        table["mitigated_index"].tolist(), tf_dates, h1_ts, tf_duration, n, n
    )

    # One bar BEFORE the mapped position. The mapping answers "from when
    # is this knowable", and validity runs up to the last bar on which the
    # zone was not yet known to be dead.
    #
    # Both timeframes agree under that reading, which is why it stays one
    # expression. On H1 the mapping is index+1, so subtracting one lands
    # back on the killing candle itself: exactly the "still tradeable on
    # the candle that kills it" rule. On Daily it lands on the last H1 bar
    # before that Daily candle closed, which is the last bar at which the
    # zone's death could not yet have been observed.
    #
    # Never invalidated maps to n, leaving n-1: live to the end of the data.
    valid_through = (
        _to_h1_index(
            table["invalidated_index"].tolist(), tf_dates, h1_ts, tf_duration, n, n
        )
        - 1
    )

    if "flip_zone_known_from" in table.columns:
        flip_known_from = _to_h1_index(
            table["flip_zone_known_from"].tolist(), tf_dates, h1_ts, tf_duration, n, n
        )
    else:
        flip_known_from = np.full(count, n, dtype=np.int64)

    touch_at = [
        _to_h1_index(list(indices or []), tf_dates, h1_ts, tf_duration, n, n)
        for indices in table["qualifying_touch_indices"].tolist()
    ]

    quality = {
        column: table[column].to_numpy(dtype=bool)
        for column in QUALITY_COLUMNS
        if column in table.columns
    }

    return ObSeries(
        timeframe=timeframe,
        top=top,
        bottom=bottom,
        midpoint=(top + bottom) / 2.0,
        sign=np.where(table["direction"].to_numpy() == "bullish", 1, -1).astype(np.int8),
        visible_from=visible_from,
        mitigated_at=mitigated_at,
        valid_through=valid_through,
        flip_known_from=flip_known_from,
        touch_at=touch_at,
        quality=quality,
        src_index=np.arange(count, dtype=np.int64),
    )


def _live_rows(series, n):
    """Per-bar lists of the OB rows usable on that bar.

    Built by walking entry/exit events rather than testing every OB on
    every bar: 99%+ of zones die within a handful of candles, so the live
    set at any instant is tiny and this stays linear in (bars + events).
    """
    starts = {}
    for row in range(len(series)):
        start = int(series.visible_from[row])
        if start >= n:
            continue
        starts.setdefault(start, []).append(row)

    live = []
    current = []
    for k in range(n):
        for row in starts.get(k, ()):
            current.append(row)
        current = [row for row in current if k <= series.valid_through[row]]
        live.append(list(current))
    return live


def _build_h1_triggers(series, n):
    """trigger_ob/trigger_touch_no: which valid H1 OB took a qualifying
    touch on each bar, and which touch it was.

    A qualifying touch is one that penetrated deeper than the previous one
    (order_blocks._apply_touch_lifecycle already recorded exactly those, so
    this reads them rather than re-deriving). When two OBs are touched on
    the same candle the FRESHEST wins, i.e. the one whose trigger is most
    recent: a nearer, newer zone is what price is actually reacting to,
    and the two can imply opposite trades, so an arbitrary tie-break here
    would silently pick a direction.
    """
    trigger_ob = np.full(n, -1, dtype=np.int64)
    trigger_touch_no = np.zeros(n, dtype=np.int8)

    for row in range(len(series)):
        for touch_no, bar in enumerate(series.touch_at[row], start=1):
            k = int(bar)
            if k >= n or k > series.valid_through[row]:
                continue
            incumbent = trigger_ob[k]
            if incumbent >= 0 and series.visible_from[incumbent] >= series.visible_from[row]:
                continue
            trigger_ob[k] = row
            trigger_touch_no[k] = touch_no

    return trigger_ob, trigger_touch_no


def _build_targets(series, n, live, closes):
    """Nearest valid, UNMITIGATED OB above and below each bar's close.

    Unmitigated is the requirement that makes something a target: a zone
    price has already traded into is no longer somewhere it is being drawn
    to. Distance is left to the caller to range-check, since the 5R radius
    depends on a signal's own stop and is not known here.
    """
    above = np.full(n, -1, dtype=np.int64)
    below = np.full(n, -1, dtype=np.int64)

    for k in range(n):
        close = closes[k]
        best_above = -1
        best_below = -1
        best_above_gap = np.inf
        best_below_gap = np.inf
        for row in live[k]:
            if series.mitigated_at[row] <= k:
                continue
            if series.bottom[row] > close:
                gap = series.bottom[row] - close
                if gap < best_above_gap:
                    best_above_gap = gap
                    best_above = row
            elif series.top[row] < close:
                gap = close - series.top[row]
                if gap < best_below_gap:
                    best_below_gap = gap
                    best_below = row
        above[k] = best_above
        below[k] = best_below

    return above, below


def _build_mitigated_htf(series, n, live, highs, lows):
    """The valid OB whose zone this bar's range overlaps, if any.

    This is what makes a higher-timeframe gate scorable: the 4H and Daily
    Mitigation OB blocks are only evaluated when price is actually inside
    one of those zones, and excluded entirely otherwise.
    """
    out = np.full(n, -1, dtype=np.int64)
    for k in range(n):
        for row in live[k]:
            if lows[k] <= series.top[row] and highs[k] >= series.bottom[row]:
                out[k] = row
                break
    return out


def build_ob_universe(series_by_tf, highs, lows, closes):
    """Assembles the per-candle lookup arrays from the converted series."""
    n = len(closes)
    target_above = {}
    target_below = {}
    mitigated_htf = {}
    trigger_ob = np.full(n, -1, dtype=np.int64)
    trigger_touch_no = np.zeros(n, dtype=np.int8)

    for timeframe, series in series_by_tf.items():
        live = _live_rows(series, n)
        above, below = _build_targets(series, n, live, closes)
        target_above[timeframe] = above
        target_below[timeframe] = below
        mitigated_htf[timeframe] = _build_mitigated_htf(series, n, live, highs, lows)
        if timeframe == "H1":
            trigger_ob, trigger_touch_no = _build_h1_triggers(series, n)

    return ObUniverse(
        n=n,
        series=series_by_tf,
        trigger_ob=trigger_ob,
        trigger_touch_no=trigger_touch_no,
        target_above=target_above,
        target_below=target_below,
        mitigated_htf=mitigated_htf,
    )


def _rebase_series(series, start, stop):
    """Shifts one ObSeries into a window's local index space.

    visible_from is clamped at 0 rather than dropped, because an OB formed
    before the window can still be traded inside it, and valid_through is
    clamped at the last bar for the mirror reason. Rows with no overlap at
    all are kept rather than filtered, so row ids stay stable and the
    per-bar arrays (which store row ids) need no remapping.
    """
    length = stop - start
    visible_from = np.clip(series.visible_from - start, 0, length)
    valid_through = np.clip(series.valid_through - start, -1, length - 1)
    return ObSeries(
        timeframe=series.timeframe,
        top=series.top,
        bottom=series.bottom,
        midpoint=series.midpoint,
        sign=series.sign,
        visible_from=visible_from,
        mitigated_at=np.clip(series.mitigated_at - start, -1, length),
        valid_through=valid_through,
        flip_known_from=np.clip(series.flip_known_from - start, -1, length),
        touch_at=[np.clip(t - start, -1, length) for t in series.touch_at],
        quality=series.quality,
        src_index=series.src_index,
    )


def slice_universe(universe, start, stop):
    """Rebases a full-history universe onto a [start, stop) H1 window."""
    return ObUniverse(
        n=stop - start,
        series={
            timeframe: _rebase_series(series, start, stop)
            for timeframe, series in universe.series.items()
        },
        trigger_ob=universe.trigger_ob[start:stop],
        trigger_touch_no=universe.trigger_touch_no[start:stop],
        target_above={
            timeframe: values[start:stop]
            for timeframe, values in universe.target_above.items()
        },
        target_below={
            timeframe: values[start:stop]
            for timeframe, values in universe.target_below.items()
        },
        mitigated_htf={
            timeframe: values[start:stop]
            for timeframe, values in universe.mitigated_htf.items()
        },
    )
