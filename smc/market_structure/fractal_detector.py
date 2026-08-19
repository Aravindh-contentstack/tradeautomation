"""Daily Williams Fractal pivot detector, for the fractal tier.

Sibling to swing_structure/pivot_detector.py's compute_pivot_swing_structure
(same shape: takes an OHLC DataFrame, returns swing_high, swing_low,
high_event, low_event), but powered by a fourth mechanism: the classic
Williams Fractal, ported faithfully from temp-reference/fractal/
williams_fractal.py with its default period (n=2), rather than the
ATR-based zigzag the internal tier uses.

Where the ATR zigzag only confirms a new pivot once price has genuinely
reversed by reversal_multiplier x ATR, a Williams Fractal confirms a pivot
purely from candle shape: a high (or low) that stands out from n candles
on each side of it. This makes fractal pivots far more frequent than
either the swing or the internal tier, by design, and there is no
"must break first" gate the way the zigzag has: every new confirmed
fractal on a side immediately replaces the previous one, whether or not
the level in between ever got broken. This is what makes fractal the
fastest-turning of the three tiers.

A fractal pivot can only be confirmed once n candles exist after it (its
"future" side), so every value in swing_high/swing_low is written as of
the day it actually becomes knowable (pivot day + n), never earlier. This
matches the no-lookahead discipline the other two detectors already
follow, just with a fixed n-candle lag instead of an unpredictable one.

Tie handling is ported bar-for-bar from the Pine reference, not
simplified to a textbook symmetric fractal: the future side must be
strictly beyond the pivot with no ties tolerated, but the past side
tolerates a run of up to 4 candles tied with the pivot's own high/low
before requiring a strict move away from it (the reference's
upflagUpFrontier0..4 / downflagUpFrontier0..4 checks). Confirmed against
the user directly: they want the default-values behavior of that exact
script, plateaus included, not an approximation.
"""

import pandas as pd

from smc.market_structure.atr import compute_atr_series

_TIE_TOLERANCE = 4


def _future_side_strict(values, i, n, pivot_value, higher):
    """True if all n candles after i are strictly beyond pivot_value.

    higher=True checks values[i+1..i+n] < pivot_value (down frontier for an
    up-fractal); higher=False checks values[i+1..i+n] > pivot_value (up
    frontier for a down-fractal). Named for readability at the call site.
    """
    for t in range(1, n + 1):
        idx = i + t
        if idx >= len(values):
            return False
        if higher and not (values[idx] < pivot_value):
            return False
        if not higher and not (values[idx] > pivot_value):
            return False
    return True


def _past_side_tolerant(values, i, n, pivot_value, higher):
    """True if the n candles before i are beyond pivot_value, tolerating a
    run of up to _TIE_TOLERANCE ties immediately before i.

    Mirrors the Pine reference's upflagUpFrontier0..4 (or
    downflagUpFrontier0..4): the first k candles immediately before i may
    equal pivot_value, but the n candles before that run must be strictly
    beyond it. Every k from 0 to _TIE_TOLERANCE is tried; any one
    succeeding confirms the pivot.
    """
    for k in range(0, _TIE_TOLERANCE + 1):
        tie_run_ok = True
        for t in range(1, k + 1):
            idx = i - t
            if idx < 0:
                tie_run_ok = False
                break
            if higher and not (values[idx] <= pivot_value):
                tie_run_ok = False
                break
            if not higher and not (values[idx] >= pivot_value):
                tie_run_ok = False
                break
        if not tie_run_ok:
            continue

        strict_run_ok = True
        for t in range(1, n + 1):
            idx = i - k - t
            if idx < 0:
                strict_run_ok = False
                break
            if higher and not (values[idx] < pivot_value):
                strict_run_ok = False
                break
            if not higher and not (values[idx] > pivot_value):
                strict_run_ok = False
                break
        if strict_run_ok:
            return True
    return False


def _is_up_fractal(highs, i, n):
    high_i = highs[i]
    return _future_side_strict(highs, i, n, high_i, higher=True) and _past_side_tolerant(
        highs, i, n, high_i, higher=True
    )


def _is_down_fractal(lows, i, n):
    low_i = lows[i]
    return _future_side_strict(lows, i, n, low_i, higher=False) and _past_side_tolerant(
        lows, i, n, low_i, higher=False
    )


def _passes_atr_filter(pivot_value, opposite_value, atr, min_atr_separation):
    """True if a newly confirmed pivot is a big enough swing to keep.

    Inert (always True) in three cases, each deliberate:
      - min_atr_separation is 0 or less, i.e. the filter is switched off.
      - atr is None, i.e. ATR has not seeded yet. A filter that compared
        against 0.0 during warm-up would accept everything anyway, but
        being explicit keeps it from ever silently rejecting instead.
      - opposite_value is None, i.e. no pivot has confirmed on the other
        side yet, so there is no leg to measure. The first pivot on each
        side can never be filtered out, which also guarantees the
        detector still seeds.
    """
    if min_atr_separation <= 0:
        return True
    if atr is None or opposite_value is None:
        return True
    return abs(pivot_value - opposite_value) >= min_atr_separation * atr


_PIVOT_COLUMNS = [
    "side",
    "pivot_index",
    "pivot_price",
    "confirmed_index",
    "confirmed_date",
]


def compute_fractal_pivots(df, n=2):
    """Every confirmed fractal pivot as its own row, rather than as a
    running "latest confirmed" column.

    compute_fractal_swing_structure below answers "what is the current
    pivot on each side", which is what structure and premium/discount
    need. The liquidity detectors need the opposite: the full list of
    pivots, each one only once. Walking that function's swing_high column
    for changes is not a substitute, because two consecutive pivots at the
    same price leave the column unchanged and would silently merge into
    one.

    Runs the identical _is_up_fractal / _is_down_fractal tests, so this
    cannot drift from the structure tiers' idea of what a pivot is. The
    manual_restart and min_atr_separation machinery is deliberately absent:
    both are off everywhere, and neither is meaningful for a liquidity
    level, which is a fact about where price turned rather than about a
    structure state machine.

    df: DataFrame of OHLC candles (date, open, high, low, close), ascending.
    n: the fractal scale, same meaning as everywhere else in this module.

    Returns a DataFrame with one row per pivot, columns per _PIVOT_COLUMNS,
    sorted by confirmed_index. A candle can legitimately produce both a high
    and a low pivot, in which case it appears twice.

    confirmed_index is pivot_index + n, the first candle the pivot could
    have been known on. Nothing downstream may read a pivot before it,
    which is the same no-lookahead rule the structure columns obey by
    applying a pivot at i + n rather than at i.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    dates = df["date"].tolist()

    pivots = []
    for i in range(length):
        if i + n >= length:
            break
        if _is_up_fractal(highs, i, n):
            pivots.append(("high", i, highs[i]))
        if _is_down_fractal(lows, i, n):
            pivots.append(("low", i, lows[i]))

    rows = [
        {
            "side": side,
            "pivot_index": pivot_index,
            "pivot_price": price,
            "confirmed_index": pivot_index + n,
            "confirmed_date": dates[pivot_index + n],
        }
        for side, pivot_index, price in pivots
    ]
    rows.sort(key=lambda row: row["confirmed_index"])
    return pd.DataFrame(rows, columns=_PIVOT_COLUMNS)


def compute_fractal_swing_structure(
    df,
    n=2,
    manual_restart=None,
    min_atr_separation=0.0,
    atr_period=14,
):
    """Computes the Williams Fractal swing high/low pair for every row.

    df: DataFrame of OHLC candles with columns date, open, high, low,
        close, in ascending date order. Timeframe-agnostic: the caller
        decides whether one row is a Daily or a 4H candle. Only the
        defaults below are calibrated for Daily.
    n: candles required strictly beyond the pivot on the future side, and
        (tie run aside) on the past side, to confirm it as a fractal
        (default 2, matching the Pine reference's default Periods input).
        This is the single knob that sets the scale of the structure: n=2
        gives minor pulls, n=20 gives major swings, and because a larger
        n's conditions are a strict superset of a smaller n's, the pivots
        found at a large n are always a subset of those found at a small
        one.
    manual_restart: optional boolean Series, same length and order as
        df. A one-time trigger on the exact candle it flips from False to
        True: immediately resets both sides to today's high/low and
        forgets any fractal confirmed before this candle.
    min_atr_separation: OFF BY DEFAULT (0.0). When greater than zero, a
        newly confirmed fractal is rejected unless its distance from the
        last confirmed pivot on the OPPOSITE side is at least
        min_atr_separation x ATR. See the note below on why this exists,
        why it is off, and why the opposite side is the right reference.
    atr_period: period for the ATR the filter compares against (default
        14). Ignored entirely when min_atr_separation is 0.0.

    Returns a copy of df with four new columns:
        swing_high, swing_low: the latest confirmed fractal price on that
            side, as of that day (None before that side's first fractal
            is ever confirmed). Every new confirmation immediately
            replaces the previous one, there is no break-first gate.
        high_event, low_event: plain-English reason for that day, on
            that side. One of "warming up" (no fractal confirmed yet on
            this side), "initial seed" (this side's first-ever fractal is
            confirmed), "break of swing high" (high_event only) / "break
            of swing low" (low_event only), "manual restart", or None for
            a day the pivot silently updates to a fresh confirmation, or
            an ordinary day where nothing happens.

    Note on min_atr_separation, and why it ships disabled
    -----------------------------------------------------
    A Williams Fractal is a purely ORDINAL test: is this candle's high
    the highest of the 2n+1 candles centred on it? It never looks at
    magnitude. That is most of why it is the most robust of this
    project's three mechanisms, since it is unaffected by price level, by
    instrument, and even by volatility LEVEL (scaling every price
    preserves the ordering, so the identical candles stay pivots). What
    it is not immune to is the trend-to-noise ratio of the path, and
    there is exactly one regime where that bites: in a tight
    consolidation, a small n confirms pivots separated by a fraction of a
    single candle's range. Ordinally valid, but not a swing anyone can
    trade.

    This filter is the fix for that one regime and nothing else. It does
    NOT help with the other two known awkward regimes: in a strong
    one-directional trend large-n fractals simply become rare (each
    candidate high gets exceeded before n candles pass on its future
    side), which is the honest answer rather than a fault, and a lone
    news-spike candle confirms as a pivot at ANY n with its wick as the
    price, which would need a wick or body filter instead.

    It defaults to 0.0 (fully disabled, no ATR even computed) because the
    need for it is unproven and concentrated: the fastest tier positively
    wants its noisy pivots, and at large n the market would have to stay
    range-bound for the whole 2n+1 window for a pivot to be trivial. It
    exists as a switchable parameter purely so that turning it on later
    is a settings change rather than a re-port across the FXR scripts,
    which is the expensive part.

    The distance is measured against the last confirmed pivot on the
    OPPOSITE side on purpose. That makes it a leg-size test ("was this
    swing big enough"), which is the question worth asking. Measuring
    against the same side would instead ask "did the level move far
    enough", which says nothing about whether a swing occurred.

    Three costs, if it is ever switched on. It reintroduces a warm-up
    period and path dependence, two of the properties whose absence made
    the fractal mechanism attractive. It breaks the subset relationship
    between different n, since each n would measure its legs against its
    own last opposite pivot. And, least obviously, THE THRESHOLD IS NOT A
    MONOTONIC DIAL: the opposite-side pivot it measures against is itself
    subject to the filter, so rejecting more pivots moves the reference
    point and can re-admit pivots a lower threshold rejected. Measured on
    the 4H demo fixture at n=2, a 2.0 threshold keeps 38 high levels while
    4.0 keeps 56. Anyone tuning this should expect to search rather than
    turn a knob one way, and scripts/verify_tier_nesting.py reports the
    curve for exactly that reason.
    """
    length = len(df)
    df = df.reset_index(drop=True)

    if manual_restart is None:
        manual_restart = pd.Series([False] * length)
    else:
        manual_restart = manual_restart.reset_index(drop=True)

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()

    # Only computed when the filter is actually on, so the default path
    # does exactly the work it did before the filter existed.
    filter_on = min_atr_separation > 0
    atr_series = compute_atr_series(df, atr_period=atr_period) if filter_on else None

    # Precompute which candles confirm as a fractal, on the day they are
    # first knowable (candle i needs n future candles to exist), not the
    # day they are applied to the output (i + n).
    is_up_fractal = [False] * length
    is_down_fractal = [False] * length
    for i in range(length):
        if i + n < length:
            is_up_fractal[i] = _is_up_fractal(highs, i, n)
            is_down_fractal[i] = _is_down_fractal(lows, i, n)

    swing_high_col = [None] * length
    swing_low_col = [None] * length
    high_event_col = [None] * length
    low_event_col = [None] * length

    swing_high = None
    high_crossed = False
    swing_low = None
    low_crossed = False
    # Confirmations for a pivot candle before this index are forgotten
    # once a manual restart happens, even if they'd otherwise apply today.
    earliest_pivot_index = 0

    for j in range(length):
        manual_today = bool(manual_restart.iloc[j])
        manual_prev = bool(manual_restart.iloc[j - 1]) if j > 0 else False
        manual_triggered = manual_today and not manual_prev

        if manual_triggered:
            swing_high = highs[j]
            high_crossed = False
            swing_low = lows[j]
            low_crossed = False
            earliest_pivot_index = j
            high_event = "manual restart"
            low_event = "manual restart"
        else:
            # A genuine break is checked against whatever level was known
            # BEFORE today's own confirmation is applied, same ordering
            # pivot_detector.py uses.
            if swing_high is None:
                high_event = "warming up"
            else:
                high_event = None
                if not high_crossed and closes[j] > swing_high:
                    high_crossed = True
                    high_event = "break of swing high"

            if swing_low is None:
                low_event = "warming up"
            else:
                low_event = None
                if not low_crossed and closes[j] < swing_low:
                    low_crossed = True
                    low_event = "break of swing low"

            pivot_index = j - n
            if pivot_index >= earliest_pivot_index:
                # The ATR the filter measures against, as of today (the
                # day the confirmation becomes knowable), not as of the
                # pivot candle. None while ATR is still warming up, and
                # None whenever the filter is off, in which case
                # _passes_atr_filter accepts unconditionally.
                atr_today = atr_series[j] if filter_on else None

                if is_up_fractal[pivot_index]:
                    # Leg measured down to the last confirmed LOW, so
                    # this asks "was the swing big enough", not "did the
                    # high move far enough".
                    if _passes_atr_filter(
                        highs[pivot_index], swing_low, atr_today, min_atr_separation
                    ):
                        first_ever = swing_high is None
                        swing_high = highs[pivot_index]
                        high_crossed = False
                        high_event = "initial seed" if first_ever else None
                if is_down_fractal[pivot_index]:
                    if _passes_atr_filter(
                        lows[pivot_index], swing_high, atr_today, min_atr_separation
                    ):
                        first_ever = swing_low is None
                        swing_low = lows[pivot_index]
                        low_crossed = False
                        low_event = "initial seed" if first_ever else None

        swing_high_col[j] = swing_high
        swing_low_col[j] = swing_low
        high_event_col[j] = high_event
        low_event_col[j] = low_event

    result = df.copy()
    result["swing_high"] = swing_high_col
    result["swing_low"] = swing_low_col
    result["high_event"] = high_event_col
    result["low_event"] = low_event_col
    return result
