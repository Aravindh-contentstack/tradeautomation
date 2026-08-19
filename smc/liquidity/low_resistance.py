"""Low Resistance Liquidity (LRLQ): the stepping pivots left behind by a
complex pullback.

The concept, in the user's words: after price has moved hard in one
direction, it rarely retraces the same way it came. It grinds back instead,
in a series of small pullbacks, and each of those leaves a minor low (coming
up) or a minor high (coming down). A run of them is a stack of stops sitting
in a straight line with nothing between them, which is why price tends to
run through the lot in one move once it turns. Rising and falling wedges are
the same shape drawn differently.

The rule, confirmed with the user
---------------------------------
Three or more consecutive same-side pivots, each beyond the last, all
confirming within MAX_SPAN candles. Pivots come from the Williams Fractal at
n=1, the finest scale it has, because these are minor turns by definition and
n=2 would step straight over most of them.

The LRLQ level is the FIRST pivot of the run, which is the far end of the
stack: price running the liquidity travels through every step to reach it, so
that is the level worth targeting.

    sell side  3+ consecutive HIGHER LOWS. The level is the LOWEST of them,
               and price is expected to come down through the lot.
    buy side   3+ consecutive LOWER HIGHS. The level is the HIGHEST of them.

Deliberately NOT part of the rule
---------------------------------
No impulse or displacement precondition, and no imbalance test. The user was
explicit that the strong-move-then-grind story is how LRLQ usually arises,
not what defines it: "LRLQ will not always be associated with imbalances, can
be formed anywhere."

Reference: temp-reference/liquidity/LRLQ/mozilla.py was read and rejected. It
is Pine, and more importantly it implements a different concept altogether
(classifying each pivot against the previous one as equal-highs versus
higher-high), which is nearer to this project's Equals detector than to
anything here. Nothing was taken from it.
"""

import pandas as pd

from smc.market_structure.fractal_detector import compute_fractal_pivots

# The finest Williams Fractal scale. A complex pullback is made of minor
# turns, and n=2 (what the structure tiers use for their fastest tier) steps
# over most of them.
DEFAULT_PIVOT_N = 1

# Pivots needed before a run counts as low-resistance. Two steps is an
# ordinary pullback; three is a grind.
DEFAULT_MIN_PIVOTS = 3

# Candles from the run's first pivot to its last. "Within a short span of
# candles" in the user's spec, pinned at a tunable constant so it can be
# retuned against a chart without touching call sites. On H1 this is about a
# day and a half of pullback.
DEFAULT_MAX_SPAN = 30

# Candles the level survives after forming, matching order_blocks.OB_LOOKBACK
# and fair_value_gaps.DEFAULT_LOOKBACK.
DEFAULT_LOOKBACK = 100

LRLQ = "lrlq"

HIGH = "high"
LOW = "low"

_LRLQ_COLUMNS = [
    "side",
    "kind",
    "level",
    "pivot_count",
    "first_pivot_index",
    "last_pivot_index",
    "visible_from_index",
    "visible_from_date",
    "valid_through_index",
    "ended_by",
    "swept",
    "swept_index",
    "swept_date",
]


def _runs(pivots, ascending, min_pivots, max_span):
    """Maximal runs of consecutive pivots stepping one way.

    "Consecutive" means consecutive AMONG THAT SIDE'S pivots, not among all
    candles: the highs printed between two higher lows are what makes it a
    pullback rather than a straight line, so they must not break the run.

    Greedy and maximal, so a fourth step extends the existing run rather
    than opening a second one that shares three of its pivots. Overlapping
    runs would report the same stack of stops two or three times over and
    let one pullback outvote everything else in the gate.
    """
    runs = []
    current = []

    for pivot in pivots:
        if current:
            previous = current[-1]
            steps = pivot.pivot_price > previous.pivot_price if ascending \
                else pivot.pivot_price < previous.pivot_price
            if steps and pivot.pivot_index - current[0].pivot_index <= max_span:
                current.append(pivot)
                continue
            if len(current) >= min_pivots:
                runs.append(current)
            # The pivot that broke the run is the natural seed for the next
            # one: a pullback that fails and immediately restarts shares
            # that turn with both.
            current = [pivot]
            continue
        current = [pivot]

    if len(current) >= min_pivots:
        runs.append(current)
    return runs


def compute_low_resistance_liquidity(
    df,
    pivot_n=DEFAULT_PIVOT_N,
    min_pivots=DEFAULT_MIN_PIVOTS,
    max_span=DEFAULT_MAX_SPAN,
    lookback=DEFAULT_LOOKBACK,
):
    """Identifies every LRLQ level in df's candles.

    df: DataFrame of OHLC candles (date, open, high, low, close), ascending.
    pivot_n: Williams Fractal scale the stepping pivots are drawn at.
    min_pivots: pivots needed to call a pullback complex.
    max_span: candles from a run's first pivot to its last.
    lookback: candles the level survives after forming.

    Returns a DataFrame with one row per level, columns per _LRLQ_COLUMNS,
    sorted by visible_from_index. The shape deliberately matches
    smc/liquidity/levels.py's, minus the pooling columns, so
    liq_state.py consumes both through one path.

    `visible_from_index` is the confirmation index of the run's LAST pivot,
    since a run of three is not known to be a run until its third pivot
    confirms. `ended_by` is "swept", "expired", or "data_end", with the same
    meanings as in levels.py.
    """
    df = df.reset_index(drop=True)
    length = len(df)

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    dates = df["date"].tolist()

    pivots = compute_fractal_pivots(df, n=pivot_n)
    low_pivots = [p for p in pivots.itertuples(index=False) if p.side == LOW]
    high_pivots = [p for p in pivots.itertuples(index=False) if p.side == HIGH]

    rows = []
    # A run of higher LOWS is sell-side liquidity: the stops under it get
    # taken on the way DOWN. Hence side="low" for an ascending run.
    for side, candidates, ascending in (
        (LOW, low_pivots, True),
        (HIGH, high_pivots, False),
    ):
        for run in _runs(candidates, ascending, min_pivots, max_span):
            anchor = run[0]
            last = run[-1]
            visible_from = last.confirmed_index
            level = anchor.pivot_price

            expiry = min(visible_from + lookback, length - 1)
            swept_index = None
            for k in range(visible_from + 1, expiry + 1):
                breached = lows[k] < level if side == LOW else highs[k] > level
                if breached:
                    swept_index = k
                    break

            if swept_index is not None:
                valid_through, ended_by = swept_index, "swept"
            elif visible_from + lookback <= length - 1:
                valid_through, ended_by = expiry, "expired"
            else:
                valid_through, ended_by = length - 1, "data_end"

            rows.append({
                "side": side,
                "kind": LRLQ,
                "level": level,
                "pivot_count": len(run),
                "first_pivot_index": anchor.pivot_index,
                "last_pivot_index": last.pivot_index,
                "visible_from_index": visible_from,
                "visible_from_date": dates[visible_from],
                "valid_through_index": valid_through,
                "ended_by": ended_by,
                "swept": swept_index is not None,
                "swept_index": swept_index,
                "swept_date": dates[swept_index] if swept_index is not None else None,
            })

    rows.sort(key=lambda row: (row["visible_from_index"], row["side"]))
    return pd.DataFrame(rows, columns=_LRLQ_COLUMNS)
