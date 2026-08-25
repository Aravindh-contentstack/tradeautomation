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

What this module no longer does
------------------------------
It used to own two more things, both superseded by the M15 entry models
and REMOVED rather than left sitting unused:

  resolve_entry_bar   the H1 killzone gate, with its pre-window deferral.
                      The session gate moved to where it now matters,
                      which is the M15 candle that completes the setup
                      (entry_models._in_killzone). Gating the H1 touch as
                      well would reject setups whose entry lands in a
                      session perfectly well.
  build_setup         entry at the close of the mitigating candle with the
                      stop beyond the zone's far edge. That was always
                      described in roadmap/supply-and-demand.md as "a
                      placeholder until the M15 entry models land", and
                      they have. Price and stop now come from the trigger
                      candle (backtest/entry_models.py).

Both were deleted instead of deprecated because leaving them importable
invites wiring them back in, and each encodes a rule the strategy has
explicitly moved off.

What stays here is the trigger question (which touches are candidates) and
the four distance and buffer constants, which the entry models read.
"""

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

# Previous-week high/low reaches further than everything else, confirmed
# with the user: 7.5R against 5R.
#
# The reason it earns the exception is that a weekly level is a bigger draw
# than a session high or a single old point, so price is worth crediting for
# heading toward one from further away. Every other liquidity kind, and
# every order block, stays on TARGET_SEARCH_R.
WEEKLY_TARGET_SEARCH_R = 7.5

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
