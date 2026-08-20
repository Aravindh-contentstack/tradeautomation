"""The third question about liquidity: what did the H1 approach leg take on
its way back INTO an order block?

Three modules already answer a version of "was liquidity swept", and none of
them can answer this one:

  - smc/liquidity/sweeps.py answers "which candle took which level".
  - smc/order_blocks/order_block_quality.py's apply_candle_sweeps asks that
    over an order block's FORMATION leg, and freezes it at the OB's trigger.
  - backtest/factors.py's evaluate_swept_liquidity_factors asks it of the
    most recently CLOSED Daily or 4H candle.

An H1 order block can form in March and be mitigated in April. The formation
columns describe March. The last-closed-candle gate describes the last hour.
Neither observes the leg that actually delivered price back to the zone, and
"the tap was preceded by a stop run on the previous day's low" is exactly the
kind of confluence a trader reads off the chart.

GEOMETRY, which drives everything below. Eligible liquidity sits ABOVE a
bullish (demand) order block and BELOW a bearish one. A demand zone at
1.0800-1.0820 with the previous day's low at 1.0830: price wicks below 1.0830,
running the sell stops resting under it, then keeps falling into the zone,
where that sell-side liquidity is what fills the buy orders. Had the level sat
BELOW 1.0800, price would have to destroy the order block to reach it, and
there would be no setup left to score.

CREDIT, and how it dies. A sweep is not a permanent fact. Candle S takes the
level, and the credit survives to the entry bar only while both of these hold
on every candle after S:

  (a) No candle CLOSES through the level itself. Wicks below are fine, and a
      re-sweep is welcome, but a close beyond it means the level did not hold
      and the sweep was a breakdown rather than a stop run.
  (b) No candle CLOSES more than CREDIT_SPENT_ATR_MULTIPLE x ATR(14) beyond
      S's FAR extreme (S's high for a bullish setup). This is the reaction
      test. If the sweep already produced a big push, those resting orders
      have been spent somewhere else and are not available to fuel this entry.
      Price coming back down afterwards is bearish order flow, not a second
      helping of the same liquidity.

Whichever fires first, permanently. A later re-sweep of the same level does
not open a new chain: once the pool has paid out, it has paid out.

There is no bar count anywhere in this. The push in (b) can land on S+1 or on
S+7, and S+1 is very often a doji with the real move several candles later, so
every rule here is "any candle after S".

The two conditions bound the close from opposite directions, so they collapse
to a single contiguous band and one forward scan answers both. That is why
"whichever fires first" is structural here rather than bookkeeping.

Credit is additionally capped by the level's own natural lifespan, carried on
the events as expires_index. Nothing new ages here: old points, equals and
LRLQ already expire on the shared 100-candle lookback, and session and
previous-day/week levels already carry the clock-based windows time_levels.py
built them with.

OUTPUT SHAPE. The scoring question is "does a surviving swept level of kind K
exist beyond the zone edge", and for a bullish OB that is exactly
`max(surviving prices) > ob_top`. So the per-level chain state collapses,
without approximation, to one float array per (kind, side) holding the running
extreme. Note the direction, which is the easiest thing in this module to get
backwards: the LOW side keeps a MAX. A previous day low is a low-type level
and lives on the low side, but it has to sit ABOVE a demand zone, so the
useful extreme is the highest surviving one.

Carrying the answer rather than the derivation is also what makes live work.
backtest/pipeline.py's build_live_context keeps only the last 200 bars, and a
per-bar array still reports a chain that opened months earlier.
"""

import heapq

import numpy as np
import pandas as pd

from smc.liquidity import sweeps
from smc.liquidity.levels import EQUALS, OLD_POINT
from smc.liquidity.low_resistance import LRLQ
from smc.market_structure.atr import compute_atr_series

HIGH = "high"
LOW = "low"

# External and time-based kinds only.
#
# The structural kinds (swing, internal, fractal) are absent because they are
# a formation-tier concept: a tier's own strong point is expected to HOLD, and
# order_block_quality.py already asks about them over the leg that built the
# zone.
#
# FVG is absent for a stronger reason. An order block's displacement leg
# creates an imbalance sitting directly in front of the zone, so price cannot
# mitigate the block without passing through it. Crediting that would hand
# every OB with caused_imbalance a free sweep on every mitigation, which is
# caused_imbalance scored twice under a second name.
ELIGIBLE_KINDS = (
    OLD_POINT,
    EQUALS,
    LRLQ,
    "asian",
    "london",
    "ny",
    "previous_day",
    "previous_week",
)

# How far beyond S's far extreme a close has to land before the swept
# liquidity counts as spent. Confirmed with the user as 3x, measured against
# the same ATR(14) every other detector in the repo uses.
CREDIT_SPENT_ATR_MULTIPLE = 3.0

# Matches order_blocks.ATR_PERIOD and levels.py's tolerance ATR, so zones,
# levels and credits all read one volatility number.
ATR_PERIOD = 14

# Growth schedule for the forward scan. Chains usually die within a handful of
# candles because rule (a) is tight, so starting small keeps the common case
# to a single small numpy call, while the doubling keeps the rare long-lived
# chain from degenerating into a per-candle Python loop.
_SCAN_CHUNK = 64


def _first_outside(closes, start, lower, upper):
    """The first index after `start` whose close leaves [lower, upper].

    Returns len(closes) when the close never leaves the band, which the
    caller reads as "nothing killed this chain".

    Scanned in growing chunks rather than by building a mask over the whole
    tail: H1 carries tens of thousands of candles and there is one of these
    per sweep event. The scan must not stop early at a doji or any other
    inside bar, since it is looking for the first close OUTSIDE the band and
    a quiet candle is simply inside it.
    """
    length = len(closes)
    position = start + 1
    span = _SCAN_CHUNK

    while position < length:
        stop = min(position + span, length)
        window = closes[position:stop]
        breached = (window < lower) | (window > upper)
        hits = np.flatnonzero(breached)
        if len(hits) > 0:
            return position + int(hits[0])
        position = stop
        span *= 2

    return length


def compute_credit_windows(
    events,
    h1_df,
    atr_multiple=CREDIT_SPENT_ATR_MULTIPLE,
    atr_period=ATR_PERIOD,
):
    """Adds credit_through to a sweep-event frame.

    events: as produced by smc/liquidity/sweeps.py's *_sweep_events
        functions, carrying kind, side, level, swept_index, expires_index.
    h1_df: the H1 frame those swept_index values address.

    credit_through is the INCLUSIVE last H1 bar on which the credit still
    counts. Credit is alive on [swept_index, credit_through], so the sweeping
    candle can itself be the entry candle and can never kill its own credit.

    Events whose sweep lands during the ATR warm-up are dropped rather than
    given a fabricated ATR, matching how levels.py skips a pivot with no
    tolerance yet. A fallback would let the spend rule fire off a number that
    does not mean anything.
    """
    frame = h1_df.reset_index(drop=True)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)

    atr_series = compute_atr_series(frame, atr_period=atr_period)

    if len(events) == 0:
        return events.assign(credit_through=pd.Series(dtype=int))

    kept = []
    for row in events.itertuples(index=False):
        sweep_index = int(row.swept_index)
        atr = atr_series[sweep_index]
        if atr is None:
            continue

        reach = atr_multiple * atr
        if row.side == LOW:
            # Bullish setup. The level sits above the zone, so a close below
            # it is the failure, and the spend is measured up from S's high.
            lower = row.level
            upper = highs[sweep_index] + reach
        else:
            lower = lows[sweep_index] - reach
            upper = row.level

        killed = _first_outside(closes, sweep_index, lower, upper)
        credit_through = min(killed - 1, int(row.expires_index), len(closes) - 1)
        if credit_through < sweep_index:
            # The level's own window had already closed by the time it was
            # taken. Nothing to carry.
            continue

        kept.append({**row._asdict(), "credit_through": credit_through})

    columns = list(events.columns) + ["credit_through"]
    return pd.DataFrame(kept, columns=columns)


def credit_extremes(events, n, kinds=ELIGIBLE_KINDS):
    """Per-bar surviving extreme per (kind, side), as float arrays of length n.

    The low side reports a MAX and the high side a MIN, per the direction note
    in the module docstring. NaN means no credit of that kind survives on that
    bar, which the scoring gate reads as "omit this factor" rather than "no".

    One lazy-heap sweep per key. Events are pushed as their sweep candle
    arrives and popped once expired, so the running extreme is available in
    O(log m) per bar rather than by rescanning live events. Lazy deletion is
    safe because an expired entry that is not at the top is by definition
    worse than the top and gets popped when it surfaces.
    """
    out = {}
    for kind in kinds:
        for side in (HIGH, LOW):
            out[(kind, side)] = np.full(n, np.nan, dtype=float)

    if len(events) == 0:
        return out

    for kind in kinds:
        for side in (HIGH, LOW):
            subset = events[(events["kind"] == kind) & (events["side"] == side)]
            if len(subset) == 0:
                continue
            _fill_extremes(out[(kind, side)], subset, side, n)

    return out


def _fill_extremes(target, subset, side, n):
    """One (kind, side)'s running extreme, written into `target` in place."""
    # A max-heap is a min-heap over negated levels. The low side wants the
    # HIGHEST surviving level, so it negates; the high side wants the lowest
    # and does not.
    sign = -1.0 if side == LOW else 1.0

    ordered = subset.sort_values("swept_index")
    starts = ordered["swept_index"].to_numpy(dtype=int)
    levels = ordered["level"].to_numpy(dtype=float)
    ends = ordered["credit_through"].to_numpy(dtype=int)

    heap = []
    next_event = 0
    for bar in range(n):
        while next_event < len(starts) and starts[next_event] == bar:
            heapq.heappush(
                heap, (sign * levels[next_event], int(ends[next_event]))
            )
            next_event += 1

        while heap and heap[0][1] < bar:
            heapq.heappop(heap)

        if heap:
            target[bar] = sign * heap[0][0]


def build_mitigation_leg_credit(
    h1_structured_df,
    levels_table,
    lrlq_table,
    time_levels,
    swing_prefix="h1_swing",
    time_kinds=("asian", "london", "ny", "previous_day", "previous_week"),
):
    """The one call backtest/pipeline.py makes. {(kind, side): float array}.

    Everything is read off the H1 tables, because a sweep event's own
    swept_index has to BE an H1 candle for the chain rule to mean anything:
    rule (b) measures from that candle's high, and a Daily candle's high is a
    whole day of range. Extending to the higher timeframes later is additive
    through smc/timeline.to_h1_index, but it is a separate decision.
    """
    frame = h1_structured_df.reset_index(drop=True)
    n = len(frame)

    groups = [
        sweeps.pooled_level_sweep_events(levels_table, frame, swing_prefix),
        sweeps.lrlq_sweep_events(lrlq_table),
    ]
    for kind in time_kinds:
        groups.append(sweeps.time_level_sweep_events(time_levels, kind, frame))

    events = pd.concat(groups, ignore_index=True)
    if len(events) == 0:
        # Every key still has to exist, holding all-NaN, so the scoring gate
        # reads "no credit" rather than KeyError-ing on an absent kind.
        return credit_extremes(_no_events(), n)

    return credit_extremes(compute_credit_windows(events, frame), n)


def _no_events():
    return pd.DataFrame(
        columns=list(sweeps._EVENT_COLUMNS) + ["credit_through"]
    )
