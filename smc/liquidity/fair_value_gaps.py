"""Fair Value Gap (FVG) identification, standalone (not anchored to any OB).

Confirmed with the user as part of the OB-quality-factors work
(swept_liquidity_fvg in swing_structure/order_block_quality.py), but kept as
its own module since FVGs are a general-purpose concept, not just an OB
confluence input.

Detection is the same 3-candle gap test order_blocks.py already uses for
caused_imbalance, run over every consecutive triple in the series rather
than only at OB anchors: a bullish FVG is candle i+2's low sitting above
candle i's high (a gap up nothing traded through), a bearish FVG is candle
i+2's high sitting below candle i's low. Confirmed "very aggressive": no
minimum width filter, unlike the temp-reference/fvg/tflab-liquidity.py Pine
script's optional width bands. Inversion FVGs (that same reference's IFVG
concept) are deliberately not implemented here, per the user's explicit
"avoid the IFVG" instruction.

Lifecycle: an FVG is "filled" (done, no longer available for confluence)
the first time a later candle's wick reaches at least the zone's midpoint,
i.e. a 50% fill, not a full close-through and not merely touching the near
edge. If it never reaches 50% within `lookback` candles of its own
formation, it expires instead, at expiry_index = formed_index + lookback.
Whichever happens first ends the FVG's active life; a consumer checking
whether an FVG was still available at some candle k should treat it as
active for formed_index <= k <= active_until_index, where
active_until_index is filled_index if filled, else min(expiry_index, the
last available candle).
"""

import pandas as pd

DEFAULT_LOOKBACK = 100

_FVG_COLUMNS = [
    "direction",
    "formed_index",
    "formed_date",
    "top",
    "bottom",
    "filled",
    "filled_index",
    "filled_date",
    "expiry_index",
    "active_until_index",
]


def compute_fair_value_gaps(df, lookback=DEFAULT_LOOKBACK):
    """Identifies every FVG in df's OHLC candles.

    df: DataFrame of OHLC candles (date, open, high, low, close), in
        ascending order.
    lookback: candles since formation an FVG stays valid without being
        50%-filled, before it expires (default 100).

    Returns a DataFrame with one row per FVG (not one row per candle),
    columns per _FVG_COLUMNS, sorted by formed_index, built the same way
    order_blocks.py builds its output: a list of dicts turned into a
    DataFrame at the end.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    dates = df["date"].tolist()

    fvgs = []
    for i in range(length - 2):
        if lows[i + 2] > highs[i]:
            fvgs.append(
                {"direction": "bullish", "formed_index": i + 2, "top": lows[i + 2], "bottom": highs[i]}
            )
        if highs[i + 2] < lows[i]:
            fvgs.append(
                {"direction": "bearish", "formed_index": i + 2, "top": lows[i], "bottom": highs[i + 2]}
            )

    for fvg in fvgs:
        formed_index = fvg["formed_index"]
        midpoint = (fvg["top"] + fvg["bottom"]) / 2.0
        expiry_index = min(formed_index + lookback, length - 1)

        filled = False
        filled_index = None
        for k in range(formed_index + 1, expiry_index + 1):
            if fvg["direction"] == "bullish":
                reached_midpoint = lows[k] <= midpoint
            else:
                reached_midpoint = highs[k] >= midpoint
            if reached_midpoint:
                filled = True
                filled_index = k
                break

        fvg["formed_date"] = dates[formed_index]
        fvg["filled"] = filled
        fvg["filled_index"] = filled_index
        fvg["filled_date"] = dates[filled_index] if filled else None
        fvg["expiry_index"] = expiry_index
        fvg["active_until_index"] = filled_index if filled else expiry_index

    fvgs.sort(key=lambda fvg: fvg["formed_index"])
    return pd.DataFrame(fvgs, columns=_FVG_COLUMNS)
