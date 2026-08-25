"""Minor unswept highs and lows: the liquidity LC-1 hunts for.

What this is for
----------------
The four M15 entry models all wait for price to take something out
before they will trade. LC-2A engineers its own liquidity with a fake
break, LC-2B uses an equals pool from levels.py, and CE waits for a
structure break instead. LC-1 is the one that needs PRE-EXISTING minor
liquidity, and nothing in this repo detected it before.

Take the bearish case. An H1 bearish order block sits above price, and
price is rallying up into it. What LC-1 wants is an unswept HIGH sitting
below or inside that zone. The reason such a high is liquidity: price was
in that area earlier and then dropped away, and the last up candle before
that drop pushed to some high and failed. Two kinds of orders were left
behind there, breakout buyers who bought the push and shorts who later
rested stops just above it, and nobody has come back for them because
price left downward. On the return trip up, price has to trade through
that high to reach the zone, and when it does those orders fill. That is
the fuel for the move down that follows.

Two sources, deliberately unioned
---------------------------------
1. REJECTION CANDLE. A green candle whose high the very next candle
   fails to exceed. Green means it was an attempt to go up; the next
   candle failing to exceed it means the attempt died.
2. FRACTAL PIVOT at n=1, from compute_fractal_pivots, taken verbatim.

These overlap heavily, and the overlap is fine. What matters is the case
where they DIVERGE, because a fractal requires the LEFT neighbour to be
lower too and the rejection rule does not look left at all:

    highs      11        12        13     n=1 fractal at 12?  rejection?
    -------------------------------------------------------------------
    push dies  1.1041    1.1050    1.1045       yes               yes
    in decline 1.1070    1.1060    1.1055       NO, left higher    yes
    flat left  1.1060    1.1060    1.1055       sometimes          yes

Row two is the one that earns the rejection rule its place: a green
candle that failed, sitting inside a move down, with a higher candle
before it. That is the textbook last-up-candle-before-continuation whose
high nobody came back for, and a fractal steps straight over it. Row
three is the tie case, which compute_fractal_pivots handles with a
tie-tolerant past side (_TIE_TOLERANCE) and so often catches, but not
reliably.

Taking the union means never having to reason about which source wins.

Why the "next candle" test is not optional
------------------------------------------
Without it, every green candle in a rally qualifies and the concept means
nothing: a stair-step advance would mark a level on every step, LC-1
would fire on all of them, and "price swept liquidity" would degrade to
"price went up". The test is what restricts the set to highs where the
market tried to continue and could not, which is what makes a high a
ceiling rather than one step.

No lookahead
------------
The rule reads candle i+1, so a level from candle i is only VISIBLE from
i+1 onward. candle_index and visible_from_index are therefore different
numbers and both are stored. Collapsing them would date every level one
candle early and quietly inflate every backtest that reads them.

The same guarantee that makes the rule work also makes a level unsweepable
by its own first visible candle: high[i+1] <= high[i] is exactly the
statement that i+1 did not take it. The n=1 fractal side has the same
property by construction. So the forward walk below can admit levels
before running the sweep test on the same candle, the way levels.py does,
without a level ever being born already dead.

Lifecycle, in the order a level can end (`ended_by`):

  "swept"     price wicked past the level. Wick-based and strict, the
              same test every other detector here uses. A level that has
              been taken is not liquidity any more.
  "expired"   LOOKBACK candles passed with no sweep.
  "data_end"  the history ran out while the level was still live. NOT the
              same as expired, and deliberately distinguished, for the
              same reason levels.py and order_blocks.py distinguish them:
              the live bot's newest levels are all in this bucket on
              every run, and must not be reported dead.

Deliberately absent
-------------------
No band. levels.py gives its pools a +/- tolerance*ATR/2 sweep band
because a pool is an average of several pivots and needs width to absorb
them. A minor level is one candle's one extreme, so the level IS the
price and the sweep test is against the bare number. Adding a band here
would let a level count as swept by price that never reached it.

No pooling either, for the same reason: two nearby minor highs are two
separate pieces of liquidity, and LC-1's stacked-liquidity rule wants to
know which one was taken last.
"""

import pandas as pd

from smc.market_structure.fractal_detector import compute_fractal_pivots

# The fractal scale the pivot source is drawn at. n=1 is the finest scale
# there is, and is what the user specified for LC-1: "even a fractal with
# n=1 is enough". low_resistance.py uses the same value for the same kind
# of reason, that a complex pullback is made of minor turns and n=2 steps
# over most of them.
DEFAULT_PIVOT_N = 1

# Candles a level survives without being swept. The same 100 used by
# levels.py, fair_value_gaps.py and order_blocks.py's OB_LOOKBACK, on
# every timeframe, so zones, gaps and levels all age on one rule.
DEFAULT_LOOKBACK = 100

HIGH = "high"
LOW = "low"

MINOR = "minor"

REJECTION = "rejection"
FRACTAL = "fractal"
BOTH = "both"

_MINOR_COLUMNS = [
    "side",
    "kind",
    "level",
    "source",
    "candle_index",
    "visible_from_index",
    "visible_from_date",
    "valid_through_index",
    "ended_by",
    "swept",
    "swept_index",
    "swept_date",
]


def _rejection_candidates(opens, highs, lows, closes):
    """Green/red candles whose next candle failed to exceed them.

    Returns {(side, candle_index): level}. The last candle is skipped
    because the rule needs a candle after it, which is the whole point of
    the rule rather than an edge case to paper over.
    """
    found = {}
    for i in range(len(closes) - 1):
        if closes[i] > opens[i]:
            if highs[i + 1] <= highs[i]:
                found[(HIGH, i)] = highs[i]
        elif closes[i] < opens[i]:
            if lows[i + 1] >= lows[i]:
                found[(LOW, i)] = lows[i]
        # A doji (close == open) is neither an attempt up nor an attempt
        # down, so it contributes nothing. The n=1 fractal source still
        # picks it up if its extreme is a genuine turn.
    return found


def _fractal_candidates(df, pivot_n):
    """n=1 fractal pivots, keyed the same way as the rejection source."""
    found = {}
    pivots = compute_fractal_pivots(df, n=pivot_n)
    for pivot in pivots.itertuples(index=False):
        found[(pivot.side, pivot.pivot_index)] = pivot.pivot_price
    return found


def _row(level, valid_through_index, ended_by, dates, swept_index=None):
    """One minor level over a closed index window."""
    return {
        "side": level["side"],
        "kind": MINOR,
        "level": level["level"],
        "source": level["source"],
        "candle_index": level["candle_index"],
        "visible_from_index": level["visible_from_index"],
        "visible_from_date": dates[level["visible_from_index"]],
        "valid_through_index": valid_through_index,
        "ended_by": ended_by,
        "swept": swept_index is not None,
        "swept_index": swept_index,
        "swept_date": dates[swept_index] if swept_index is not None else None,
    }


def compute_minor_liquidity(
    df,
    pivot_n=DEFAULT_PIVOT_N,
    lookback=DEFAULT_LOOKBACK,
):
    """Identifies every minor unswept high and low in df's candles.

    df: DataFrame of OHLC candles (date, open, high, low, close),
        ascending. Timeframe-agnostic, same as every other detector here,
        though M15 is the only caller today.
    pivot_n: fractal scale the pivot source is drawn at.
    lookback: candles a level survives without being swept.

    Returns a DataFrame with one row per level, columns per
    _MINOR_COLUMNS, sorted by visible_from_index then candle_index.

    One row per level, not per version. This is the deliberate difference
    from levels.py, whose pools change level and classification as pivots
    join them and therefore need a row per version to stay free of
    lookahead. A minor level never changes: it is one candle's extreme,
    fixed the moment it is confirmed, and the only thing that can happen
    to it is being swept. So there is nothing a later candle could
    retroactively alter, and one row says everything.
    """
    df = df.reset_index(drop=True)
    length = len(df)
    if length == 0:
        return pd.DataFrame([], columns=_MINOR_COLUMNS)

    opens = df["open"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    dates = df["date"].tolist()

    rejections = _rejection_candidates(opens, highs, lows, closes)
    fractals = _fractal_candidates(df, pivot_n)

    # Union keyed on (side, candle_index). Both sources price a level at
    # the same candle's same extreme, so an overlap is genuinely the same
    # level twice and the price never disagrees. `source` records which
    # rules found it, which is what the demo script reports on to show how
    # much each source uniquely contributes.
    pending = {}
    for key in set(rejections) | set(fractals):
        side, candle_index = key
        in_rejection = key in rejections
        in_fractal = key in fractals
        if in_rejection and in_fractal:
            source = BOTH
        elif in_rejection:
            source = REJECTION
        else:
            source = FRACTAL
        pending.setdefault(candle_index + 1, []).append(
            {
                "side": side,
                "level": rejections[key] if in_rejection else fractals[key],
                "source": source,
                "candle_index": candle_index,
                # Both rules read exactly one candle past the pivot, so
                # both confirm at candle_index + 1. Kept as one expression
                # rather than read off the pivot table, so the rejection
                # source cannot silently drift from the fractal source.
                "visible_from_index": candle_index + 1,
            }
        )

    rows = []
    active = []

    for k in range(length):
        # --- 1. NEW LEVELS ----------------------------------------------
        # Admitted before this candle's sweep test. Safe for the reason in
        # the module docstring: the candle that confirms a level is, by
        # both rules' construction, a candle that did not take it.
        for level in pending.get(k, ()):
            active.append(level)

        # --- 2. SWEEPS AND EXPIRY ---------------------------------------
        survivors = []
        for level in active:
            if level["visible_from_index"] >= k:
                survivors.append(level)
                continue

            if level["side"] == HIGH:
                swept = highs[k] > level["level"]
            else:
                swept = lows[k] < level["level"]

            if swept:
                rows.append(_row(level, k, "swept", dates, swept_index=k))
                continue

            if k - level["visible_from_index"] >= lookback:
                rows.append(_row(level, k, "expired", dates))
                continue

            survivors.append(level)
        active = survivors

    # Whatever is still standing when the data ends is still LIVE.
    for level in active:
        rows.append(_row(level, length - 1, "data_end", dates))

    rows.sort(key=lambda row: (row["visible_from_index"], row["candle_index"]))
    return pd.DataFrame(rows, columns=_MINOR_COLUMNS)
