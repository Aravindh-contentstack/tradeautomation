"""Turning an order-block mitigation into a concrete trade setup.

The trigger question, the killzone question, and the price-level question
were all one function before order blocks drove entries. They are separate
here because each now has its own rule and its own failure modes.

What replaced what
------------------
A candidate used to begin with an H1 fractal break, which fixed the
direction before anything else was known. It now begins with price
mitigating a valid H1 order block, and the DIRECTION COMES FROM THAT ZONE:
a bullish OB mitigated is a long, whatever the surrounding structure says.
Nothing about the trade is assumed before the market touches something.

Only a qualifying touch counts, meaning one that went deeper than the
previous touch of the same zone. A shallower re-tap reaches no orders the
earlier one did not already absorb, so it is not a fresh opportunity, and
order_blocks._apply_touch_lifecycle has already worked out which touches
qualify.

The killzone pre-window
-----------------------
Price often taps the zone before the session and then simply travels
during it, so requiring the touch itself to land inside the killzone would
miss the setup entirely. A touch in the hour immediately before a session
counts, with the entry deferred to that session's first candle.
"""

from backtest import killzone

# The hour before each session opens. Derived from the session bounds
# rather than written as literals so the two cannot drift apart.
PRE_WINDOW_HOURS = (killzone.LONDON_START_HOUR - 1, killzone.NY_START_HOUR - 1)

# The stop sits this many pips beyond the zone's far edge, not on it. Same
# reasoning as the fractal-based stop it replaces: the edge is a level
# price has already traded to, so resting exactly there leaves no room for
# spread or slippage on the most likely level to be revisited.
SL_BUFFER_PIPS = 2.0

# How far away a zone can sit and still be a plausible draw, in R.
#
# The rule as the user states it is "within 2x the take-profit distance",
# and TP is tp_multiple x R. Pinning the multiple here at the spec's 2.5R
# baseline (so 5R) rather than reading the live tp_multiple is deliberate.
# tp_multiple is what the PRIOR year's walk-forward search recommended, so
# feeding it into the probability would make each year's scores a function
# of last year's tuning, which is the ratchet backtest/settings.py exists
# to avoid. It would also break analysis.py's post-hoc TP grid search,
# which is only free because the walk carries no TP dependence at all.
TARGET_SEARCH_R = 5.0

# Zones can be a few pips wide once _shape_zone trims a large candle to
# its body. A stop that tight makes every R multiple enormous and turns
# noise into apparent edge, so setups below this are dropped rather than
# reported.
MIN_R_PIPS = 3.0


def iter_mitigation_candidates(ctx):
    """Yields (bar_index, ob_row, touch_number) for each qualifying touch.

    One array read per bar. The universe already resolved which zone was
    touched and which touch it was, including the freshest-wins tie-break
    when two zones are hit on the same candle.
    """
    obs = getattr(ctx, "obs", None)
    if obs is None:
        return
    trigger_ob = obs.trigger_ob
    for k in range(len(trigger_ob)):
        ob_row = int(trigger_ob[k])
        if ob_row >= 0:
            yield k, ob_row, int(obs.trigger_touch_no[k])


def resolve_entry_bar(ctx, k):
    """The killzone gate. Returns the bar whose close is the entry, or None.

    A touch inside a session enters on that same candle. A touch in the
    hour before a session defers to the next candle, which is the
    session's first. Anything else is outside the hours the strategy
    trades and produces no candidate at all.
    """
    hour = int(ctx.london_hour[k])
    if killzone.LONDON_START_HOUR <= hour < killzone.LONDON_END_HOUR:
        return k
    if killzone.NY_START_HOUR <= hour < killzone.NY_END_HOUR:
        return k
    if hour in PRE_WINDOW_HOURS and k + 1 < len(ctx.london_hour):
        next_hour = int(ctx.london_hour[k + 1])
        in_session = (
            killzone.LONDON_START_HOUR <= next_hour < killzone.LONDON_END_HOUR
            or killzone.NY_START_HOUR <= next_hour < killzone.NY_END_HOUR
        )
        if in_session:
            return k + 1
    return None


def build_setup(ctx, ob_row, entry_index, pip_size):
    """Direction, entry, and stop for one mitigation. None if unusable.

    Two rejections exist only because entry and stop now come from
    different places, which was impossible when both derived from the same
    fractal break:

    - A deferred entry can close BEYOND the edge the stop sits under, so
      the trade would open already past its own stop. Guarded explicitly
      rather than left to produce a negative or absurd R.
    - A zone narrow enough to give a stop of a couple of pips inflates
      every R multiple it produces, so those setups are dropped.
    """
    series = ctx.obs.series["H1"]
    bullish = series.sign[ob_row] > 0
    buffer_price = SL_BUFFER_PIPS * pip_size

    if bullish:
        direction = "bullish"
        sl = float(series.bottom[ob_row]) - buffer_price
    else:
        direction = "bearish"
        sl = float(series.top[ob_row]) + buffer_price

    entry_price = float(ctx.close[entry_index])
    sign = 1 if bullish else -1
    if (entry_price - sl) * sign <= 0:
        return None

    r_distance = abs(entry_price - sl)
    if r_distance < MIN_R_PIPS * pip_size:
        return None

    return {
        "direction": direction,
        "entry_price": entry_price,
        "sl": sl,
        "r_distance": r_distance,
    }
