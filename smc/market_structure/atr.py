"""Wilder's ATR, as a series, shared by the detectors that need it.

This logic was originally written inline inside
compute_pivot_swing_structure's main loop (swing_structure/
pivot_detector.py), where the ATR-based zigzag consumes it one candle at
a time. The fractal detector's optional significance filter needs the
exact same numbers, so rather than let a second (and eventually a third)
hand-rolled copy drift out of step, it is lifted here once.

Extracting it is safe and produces identical values because ATR at
candle i depends only on candles up to and including i. Precomputing the
whole series up front and indexing into it is therefore not a lookahead,
it is the same forward walk with the bookkeeping moved out of the way.

The three details below are the ones a "cleaner" rewrite tends to get
wrong, so they are preserved deliberately rather than tidied:

  1. The first candle has no previous close, so its True Range is just
     high - low, not the usual three-way max.
  2. The seed is a SIMPLE mean of the first atr_period True Ranges, not
     a Wilder smoothing applied from candle zero. Wilder smoothing only
     takes over from candle atr_period onward.
  3. Before the seed exists the value is None, not 0.0 and not the
     partial mean so far. Callers must treat None as "not known yet" and
     stay inert, since a filter comparing against 0.0 would silently
     accept everything during warm-up.
"""


def compute_atr_series(df, atr_period=14):
    """Computes Wilder's ATR for every row of df.

    df: DataFrame of OHLC candles with columns high, low, close, in
        ascending date order. Timeframe-agnostic: the caller decides what
        one candle means.
    atr_period: how many candles the seed averages, and the smoothing
        constant thereafter (default 14).

    Returns a plain list, the same length as df, where element i is the
    ATR as of candle i, or None for the first atr_period - 1 candles
    where no seed exists yet.
    """
    length = len(df)
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()

    atr_series = [None] * length

    atr = None
    tr_values = []

    for i in range(length):
        high_today = highs[i]
        low_today = lows[i]

        # See detail 1 in the module docstring: no previous close exists
        # for the very first candle.
        if i == 0:
            true_range = high_today - low_today
        else:
            prev_close = closes[i - 1]
            true_range = max(
                high_today - low_today,
                abs(high_today - prev_close),
                abs(low_today - prev_close),
            )

        # See detail 2: simple mean to seed, Wilder smoothing after.
        if atr is None:
            tr_values.append(true_range)
            if len(tr_values) == atr_period:
                atr = sum(tr_values) / atr_period
        else:
            atr = (atr * (atr_period - 1) + true_range) / atr_period

        atr_series[i] = atr

    return atr_series
