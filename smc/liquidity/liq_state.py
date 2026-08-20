"""Every liquidity level re-expressed on the H1 timeline, plus the per-candle
lookups the two liquidity gates read.

The direct analogue of smc/order_blocks/ob_state.py, and for the same reason:
each detector numbers its output by its own timeframe's row positions, the
backtest walks one H1-indexed frame, and nothing can ask "which levels are
live on this H1 candle" until those coordinate systems are reconciled. That
happens once, at pipeline build time, and no timeframe-local index survives
past it.

Two very different questions, two very different arrays
------------------------------------------------------
The Swept Liquidity gate asks a question about a CANDLE: did the most
recently closed Daily or 4H candle wick a level. That is not an H1 fact at
all, so it is computed on the owning timeframe and then carried across with
"which of its candles had closed by H1 bar k". Confirmed with the user as
last-completed-candle-only: a sweep two candles back scores nothing.

The Liquidity Target gate asks a question about a PRICE: is there a live,
untaken level within reach in the direction of travel. That is answered per
H1 bar against the live set, exactly as ob_state._build_targets does for
zones, and it is re-asked on every bar of an open trade so a target can go
quiet once price has covered it.

Levels versus zones
-------------------
Most kinds are a single price with a thin band around it. FVGs are a zone,
and which side of price they sit on changes as price moves, so their side is
resolved per bar rather than stored. `sign` is 0 for those, and the zone's
near edge is what the target distance is measured to.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smc.timeline import TIMEFRAME_DURATIONS, dates_to_h1_index, to_h1_index

# The pivot-derived and time-derived kinds, as they appear in factor names.
EQUALS = "equals"
OLD_POINT = "old_point"
LRLQ = "lrlq"
FVG = "fvg"
PREVIOUS_DAY = "previous_day"
PREVIOUS_WEEK = "previous_week"
ASIAN = "asian"
LONDON = "london"
NY = "ny"

HIGH = "high"
LOW = "low"

ABOVE = 1
BELOW = -1
ZONE = 0


@dataclass(frozen=True, eq=False)
class LevelSeries:
    """One timeframe's levels of one kind, with every index in H1 space.

    A level is usable on H1 bar k iff visible_from[j] <= k <= valid_through[j].
    `sign` is ABOVE for a level price must rise to take, BELOW for one it
    must fall to take, and ZONE for an FVG, whose side depends on where
    price currently is.

    top and bottom are the SWEEP band: for a pooled level they are the
    tolerance band's edges, for an LRLQ or time level they both equal the
    level itself, and for an FVG they are the gap. `level` is the
    representative price distance is measured to, and is NaN for zones.
    """

    timeframe: str
    kind: str
    sign: np.ndarray
    level: np.ndarray
    top: np.ndarray
    bottom: np.ndarray
    visible_from: np.ndarray
    valid_through: np.ndarray

    def __len__(self):
        return len(self.top)


@dataclass(frozen=True, eq=False)
class LiquidityUniverse:
    """Every kind's LevelSeries plus the per-candle arrays the gates read.

    swept_last_candle[(timeframe, kind, side)] answers "did the most recently
    closed candle of that timeframe wick a live level of this kind on this
    side", as a bool array of length n.

    target_above[(timeframe, kind)] / target_below[(timeframe, kind)] hold
    the PRICE of the nearest live, unswept level in that direction from each
    bar's close, or NaN where there is none. Prices rather than row ids
    because the gate only ever needs the distance, and a price survives
    windowing without any rebasing.

    mitigation_credit[(kind, side)] holds the PRICE of the surviving swept
    level whose credit is still alive on each bar, or NaN where none is, from
    smc/liquidity/sweep_credit.py. Keyed without a timeframe because the fact
    is H1-only by construction: the chain rule measures from the sweeping
    candle's own high, and a Daily candle's high is a whole day of range.

    The low side holds a MAX and the high side a MIN, which is the opposite of
    what the names suggest and is explained in sweep_credit.py's docstring:
    eligible liquidity sits ABOVE a demand zone, so the highest surviving
    low-side level is the useful one. None means the gate is omitted from
    scoring entirely rather than answering no.
    """

    n: int
    series: dict
    swept_last_candle: dict
    target_above: dict
    target_below: dict
    mitigation_credit: dict = None


def _empty_series(timeframe, kind):
    empty_f = np.zeros(0, dtype=np.float64)
    return LevelSeries(
        timeframe=timeframe,
        kind=kind,
        sign=np.zeros(0, dtype=np.int8),
        level=empty_f,
        top=empty_f,
        bottom=empty_f,
        visible_from=np.zeros(0, dtype=np.int64),
        valid_through=np.zeros(0, dtype=np.int64),
    )


def _sign_from_side(sides):
    return np.where(np.asarray(sides) == HIGH, ABOVE, BELOW).astype(np.int8)


def _inclusive_to_h1(values, tf_dates, h1_ts, duration, n):
    """An INCLUSIVE last-live index on some timeframe, as an inclusive
    last-live H1 bar.

    Deliberately the SAME expression ob_state.py uses for valid_through:
    map the last-live candle, then step back one bar. to_h1_index reports
    the first H1 bar at or after that candle's close, so one bar earlier is
    the last H1 bar inside the candle itself. A Daily level swept on
    Tuesday is therefore a live target through Tuesday's final H1 bar and
    dead from Wednesday's first, which is as precisely as a Daily fact can
    be placed on an H1 timeline.

    Getting this wrong in the obvious direction is easy and expensive:
    mapping candle i+1 instead of candle i reads as "dead from the candle
    after the one that killed it" and hands every swept level a full extra
    candle of life as a target.

    A level whose window runs to the LAST candle is not one that died
    there, it is one still standing when the data ran out, so it stays live
    to the end of the H1 frame rather than being cut at that candle's own
    close.
    """
    length = len(tf_dates)
    mapped = to_h1_index(values, tf_dates, h1_ts, duration, n, n) - 1
    for row, value in enumerate(values):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        if int(value) >= length - 1:
            mapped[row] = n - 1
    return mapped


def indexed_series(table, kind, timeframe, tf_dates, h1_ts, top=None, bottom=None):
    """A LevelSeries from a table whose windows are TIMEFRAME ROW INDICES.

    Covers levels.py, low_resistance.py and fair_value_gaps.py, all of which
    number their windows by the candles they were detected on.

    top/bottom name the sweep-band columns. They default to the level itself
    (LRLQ, which has no band); levels.py passes its tolerance band and the
    FVG caller passes the gap.
    """
    n = len(h1_ts)
    if len(table) == 0:
        return _empty_series(timeframe, kind)

    table = table.reset_index(drop=True)
    duration = TIMEFRAME_DURATIONS[timeframe]
    tf_dates = pd.DatetimeIndex(tf_dates)

    level = table["level"].to_numpy(dtype=np.float64)
    visible_from = to_h1_index(
        table["visible_from_index"].tolist(), tf_dates, h1_ts, duration, n, n
    )
    valid_through = _inclusive_to_h1(
        table["valid_through_index"].tolist(), tf_dates, h1_ts, duration, n
    )

    return LevelSeries(
        timeframe=timeframe,
        kind=kind,
        sign=_sign_from_side(table["side"].to_numpy()),
        level=level,
        top=table[top].to_numpy(dtype=np.float64) if top else level,
        bottom=table[bottom].to_numpy(dtype=np.float64) if bottom else level,
        visible_from=visible_from,
        valid_through=valid_through,
    )


def fvg_series(fvg_table, timeframe, tf_dates, h1_ts):
    """A LevelSeries for fair value gaps.

    Zones, so `sign` is ZONE and `level` is NaN: which side of price a gap
    sits on is a fact about the bar asking, not about the gap.

    An FVG's own lifecycle columns already carry the 50%-fill rule and the
    100-candle expiry, so active_until_index is read straight off rather
    than recomputed here.
    """
    n = len(h1_ts)
    if len(fvg_table) == 0:
        return _empty_series(timeframe, FVG)

    table = fvg_table.reset_index(drop=True)
    duration = TIMEFRAME_DURATIONS[timeframe]
    tf_dates = pd.DatetimeIndex(tf_dates)

    visible_from = to_h1_index(
        table["formed_index"].tolist(), tf_dates, h1_ts, duration, n, n
    )
    valid_through = _inclusive_to_h1(
        table["active_until_index"].tolist(), tf_dates, h1_ts, duration, n
    )

    return LevelSeries(
        timeframe=timeframe,
        kind=FVG,
        sign=np.full(len(table), ZONE, dtype=np.int8),
        level=np.full(len(table), np.nan, dtype=np.float64),
        top=table["top"].to_numpy(dtype=np.float64),
        bottom=table["bottom"].to_numpy(dtype=np.float64),
        visible_from=visible_from,
        valid_through=valid_through,
    )


def time_series(table, kind, timeframe, h1_ts, highs=None, lows=None):
    """A LevelSeries from a table whose windows are INSTANTS.

    time_levels.py's output. Its dates are already close times (see
    smc/timeline.dates_to_h1_index), so no duration is added.

    highs/lows are the H1 bars' own extremes. Given them, a level's window
    is CLIPPED at the first bar that takes it, which is what stops the
    Liquidity Target gate offering yesterday's high as a draw after price
    has already run it.

    That clipping cannot live in time_levels.py, which is the one detector
    that emits no sweep state of its own: a session high is a fact about a
    clock, and it has no timeframe of its own to be swept on. Every other
    kind resolves its sweep against the candles it was detected on, and
    these resolve theirs here, against H1, which is the finest data there
    is and the timeline both gates are evaluated on anyway.
    """
    n = len(h1_ts)
    subset = table[table["kind"] == kind]
    if len(subset) == 0:
        return _empty_series(timeframe, kind)

    subset = subset.reset_index(drop=True)
    level = subset["level"].to_numpy(dtype=np.float64)
    sign = _sign_from_side(subset["side"].to_numpy())
    visible_from = dates_to_h1_index(subset["visible_from_date"], h1_ts, n, n)
    valid_through = dates_to_h1_index(subset["valid_through_date"], h1_ts, n, n) - 1

    if highs is not None:
        for row in range(len(subset)):
            start = int(visible_from[row])
            stop = int(valid_through[row])
            if start > stop or start >= n:
                continue
            window = (
                highs[start : stop + 1] > level[row]
                if sign[row] == ABOVE
                else lows[start : stop + 1] < level[row]
            )
            taken = np.flatnonzero(window)
            if len(taken):
                # Live THROUGH the bar that takes it, dead after. The same
                # rule order_blocks._kill applies to a zone: the candle
                # that runs the liquidity is the one price reacts on.
                valid_through[row] = start + int(taken[0])

    return LevelSeries(
        timeframe=timeframe,
        kind=kind,
        sign=sign,
        level=level,
        top=level,
        bottom=level,
        visible_from=visible_from,
        valid_through=valid_through,
    )


def _live_rows(series, n):
    """Per-bar lists of the level rows usable on that bar.

    Event-driven rather than testing every level on every bar, same shape as
    ob_state._live_rows. The 100-candle expiry every detector applies is what
    keeps the live set at any instant tiny, and it is what makes this linear
    in (bars + events) rather than in their product.
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


def _build_targets(series, n, live, closes, range_high=None, range_low=None):
    """Nearest live level above and below each bar's close, as prices.

    Levels already gone are absent from `live` (a swept level's window ends
    on the candle that swept it), so "unswept" needs no separate test here,
    unlike ob_state's version where mitigation and death are different
    events.

    A ZONE is measured to its NEAR edge, which is the first price of it that
    price would reach. A zone straddling the close is neither above nor
    below: price is already inside it, so it is not somewhere to be drawn to.

    range_high/range_low, when given, restrict the answer to levels OUTSIDE
    that range on the bar being asked about. Only old points pass them, and
    only because "external to the swing range" is part of what makes a level
    an old point rather than an extra condition on top: a high inside the
    range is part of the leg price is currently working through.

    They are per-bar arrays rather than a single number because the range
    moves. Baking the answer in when the level was detected would let a
    later swing decide an earlier bar's score, which is the lookahead every
    other point-in-time rule here exists to avoid.
    """
    above = np.full(n, np.nan, dtype=np.float64)
    below = np.full(n, np.nan, dtype=np.float64)
    bounded = range_high is not None

    for k in range(n):
        close = closes[k]
        best_above = np.inf
        best_below = np.inf
        # NaN while a tier is warming up, and NaN comparisons are False, so
        # an unconfirmed range admits nothing rather than everything.
        high_bound = range_high[k] if bounded else None
        low_bound = range_low[k] if bounded else None

        for row in live[k]:
            sign = series.sign[row]
            if sign == ABOVE:
                price = series.level[row]
                if bounded and not price > high_bound:
                    continue
                if price > close and price - close < best_above:
                    best_above = price - close
                    above[k] = price
            elif sign == BELOW:
                price = series.level[row]
                if bounded and not price < low_bound:
                    continue
                if price < close and close - price < best_below:
                    best_below = close - price
                    below[k] = price
            else:
                if series.bottom[row] > close:
                    price = series.bottom[row]
                    if price - close < best_above:
                        best_above = price - close
                        above[k] = price
                elif series.top[row] < close:
                    price = series.top[row]
                    if close - price < best_below:
                        best_below = close - price
                        below[k] = price

    return above, below


def _last_closed_candle(tf_dates, h1_ts, timeframe):
    """For each H1 bar, the index of the most recently CLOSED candle of
    `timeframe`, or -1 before the first one closes.

    The same visibility rule as everything else here, expressed the other
    way round: instead of asking when one higher-timeframe row becomes
    readable, it asks which row is the newest readable one.
    """
    duration = TIMEFRAME_DURATIONS[timeframe]
    # DatetimeIndex arithmetic and searchsorted throughout, never a detour
    # through to_numpy(): a tz-aware index lands as dtype object there, and
    # adding a timedelta64 to that raises rather than doing the right thing.
    close_times = pd.DatetimeIndex(tf_dates) + duration
    return close_times.searchsorted(pd.DatetimeIndex(h1_ts), side="right") - 1


def _swept_on_candle(swept_indices, sides, side, length):
    """Scatter a level table's sweep candles into a per-candle bool array."""
    out = np.zeros(length, dtype=bool)
    for index, level_side in zip(swept_indices, sides):
        if level_side != side or index is None:
            continue
        if isinstance(index, float) and np.isnan(index):
            continue
        out[int(index)] = True
    return out


def carry_to_h1(per_candle, last_closed, n):
    """A per-candle flag on some timeframe, read on the H1 timeline.

    Bars before that timeframe's first close read False rather than
    indexing backwards from the end, which is what a bare negative index
    would silently do.
    """
    out = np.zeros(n, dtype=bool)
    usable = last_closed >= 0
    out[usable] = per_candle[last_closed[usable]]
    return out


def build_liquidity_universe(
    series_by_key, swept_by_key, closes, swing_ranges=None, mitigation_credit=None
):
    """Assembles the per-candle lookup arrays from the converted series.

    series_by_key: {(timeframe, kind): LevelSeries}
    swept_by_key: {(timeframe, kind, side): bool array of length n}, already
        carried onto the H1 timeline by carry_to_h1.
    swing_ranges: {timeframe: (high array, low array)} on the H1 timeline,
        used to keep old-point targets outside their tier's swing range.
        Only the old_point series consults it, and only because that is part
        of the definition of an old point. Omitted means no filtering, which
        is what every other kind wants.
    mitigation_credit: {(kind, side): float array of length n} from
        sweep_credit.build_mitigation_leg_credit. Already in H1 index space,
        so it is passed through untouched. Deliberately NOT routed through
        carry_to_h1: that shifts to the last CLOSED candle, which is right for
        a higher timeframe and wrong here, because the sweeping candle is
        allowed to be the entry candle itself.
    """
    n = len(closes)
    target_above = {}
    target_below = {}
    swing_ranges = swing_ranges or {}

    for key, series in series_by_key.items():
        timeframe, kind = key
        bounds = swing_ranges.get(timeframe) if kind == OLD_POINT else None
        live = _live_rows(series, n)
        above, below = _build_targets(
            series, n, live, closes,
            range_high=bounds[0] if bounds else None,
            range_low=bounds[1] if bounds else None,
        )
        target_above[key] = above
        target_below[key] = below

    return LiquidityUniverse(
        n=n,
        series=series_by_key,
        swept_last_candle=swept_by_key,
        target_above=target_above,
        target_below=target_below,
        mitigation_credit=mitigation_credit,
    )


def _rebase_series(series, start, stop):
    """Shifts one LevelSeries into a window's local index space.

    Clamped rather than filtered, same as ob_state._rebase_series: a level
    formed before the window can still be traded inside it, and dropping
    rows would break the row ids the per-bar arrays are built from.
    """
    length = stop - start
    return LevelSeries(
        timeframe=series.timeframe,
        kind=series.kind,
        sign=series.sign,
        level=series.level,
        top=series.top,
        bottom=series.bottom,
        visible_from=np.clip(series.visible_from - start, 0, length),
        valid_through=np.clip(series.valid_through - start, -1, length - 1),
    )


def slice_universe(universe, start, stop):
    """Rebases a full-history universe onto a [start, stop) H1 window."""
    return LiquidityUniverse(
        n=stop - start,
        series={
            key: _rebase_series(series, start, stop)
            for key, series in universe.series.items()
        },
        swept_last_candle={
            key: values[start:stop]
            for key, values in universe.swept_last_candle.items()
        },
        target_above={
            key: values[start:stop] for key, values in universe.target_above.items()
        },
        target_below={
            key: values[start:stop] for key, values in universe.target_below.items()
        },
        # A plain slice, with no rebasing: these arrays carry the ANSWER (the
        # surviving level's price) rather than the indices it was derived
        # from, so a chain that opened long before the window still reports
        # correctly inside it. That is what keeps the live path, which keeps
        # only the last 200 bars, in step with a full-history backtest.
        mitigation_credit=(
            None if universe.mitigation_credit is None
            else {
                key: values[start:stop]
                for key, values in universe.mitigation_credit.items()
            }
        ),
    )
