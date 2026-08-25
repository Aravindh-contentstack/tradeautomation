"""The 32 Entry-tier factors, one set of eight per M15 entry model.

Where these names come from
---------------------------
factors/eu_probability_factors.csv and its XAU twin have carried an
"Entry" tier since before any of this existed: four models (LC-1, LC-2A,
LC-2B, CE) with candidate weights and an "M15 Target Liquidity" sub-path.
No code read those rows. This module is what finally does, with the names
kept in step with the sheet so the two can be compared by eye.

Eight per model, and only the FIRING model's eight are evaluated. The
other 24 are omitted, which compute_probability's dynamic exclusion
already handles. Consequence worth stating plainly, because it is
sharper here than anywhere else in the engine: scores are NOT comparable
across models. An LC-1 candidate at 55% and a CE candidate at 55% do not
mean the same thing, because the denominators are different factor sets.
That property is already documented in backtest/factors.py, but the entry
tier is where it becomes visible in the journal.

Three directional traps
-----------------------
The FVG direction is the subtlest thing in here, because two factors ask
about gaps and they want OPPOSITE halves of the table. Bearish setup, so
price rallying up into a zone above:

  lid_with_fvg      SAME direction as the trade, so BEARISH gaps. A gap
                    above price left by a prior sell-off is unfinished
                    selling, and the sweep eating it on the way up is
                    collecting the last offers before the real move down.
                    User-confirmed.

  no_imbalance_...  OPPOSITE, so BULLISH gaps. This asks about the
                    approach LEG, and a leg rallying up leaves bullish
                    gaps behind it. YES means the rally was efficient and
                    has no unfilled gap that price must come back for,
                    which would run our stop before the move starts.

  has_imbalance     SAME direction again, so BEARISH gaps. CE's leg moves
                    in the trade direction, so the gaps it leaves are
                    bearish.

The third trap is the TARGET side. Every other factor here cares about
liquidity being TAKEN, which for a short is buy-side (highs). The target
gate is the one that cares about liquidity being HUNTED, which for a
short is sell-side (lows) below the entry. The two wanted sides are
opposites and both appear in this file.

The M15 Target Liquidity gate never answers no
----------------------------------------------
Same contract as factors.evaluate_liquidity_target_factors: a child is
YES or OMITTED, never NO, so the gate can only raise a score. Only the
parent can answer no, and only when neither child is present. A target
that price has already consumed drops out of the denominator rather than
flipping to NO, which is what the user asked for explicitly.
"""

import numpy as np

from backtest.entry_ob import TARGET_SEARCH_R
from backtest.factors import (
    ENTRY_FACTORS,  # noqa: F401  re-exported: callers read it from here
    ENTRY_MODEL_FACTORS,
    ENTRY_TARGET_CHILDREN,
    ENTRY_TARGET_PARENT,
    entry_factor_name,
    entry_model_factor_names,
)
from backtest.m15_pipeline import m15_index_at_or_after

# How equal a double top has to be for LC-2B's angle factor to answer yes,
# in multiples of M15 ATR(14). Between this and levels.py's own 0.25x
# pooling tolerance the pool still forms but the angle factor says no;
# beyond 0.25x the pool never forms and LC-2B cannot fire at all.
EQUALS_ANGLE_ATR = 0.10

# How close to CE's fib50 limit an inducement has to sit to count, in
# multiples of M15 ATR(14). The user's figure ("about 1 or 1.5"), taken at
# the looser end because it is easier to tighten after seeing a fire rate
# than to discover the factor answered no to everything. Explicitly a
# dry-run target.
CE_INDUCEMENT_ATR = 1.5

# How recently an M15 target level must have formed to count, in candles.
TARGET_LOOKBACK_BARS = 30

# The name tables live in backtest/factors.py, which owns ALL_FACTORS and
# has zero imports. Importing them from there rather than defining them
# here keeps the dependency pointing one way: this module reaches for
# pandas, numpy and the smc detectors, and factors.py must not.
MODEL_FACTORS = ENTRY_MODEL_FACTORS
TARGET_CHILDREN = ENTRY_TARGET_CHILDREN
TARGET_PARENT = ENTRY_TARGET_PARENT

factor_name = entry_factor_name
model_factor_names = entry_model_factor_names


# --- small geometric helpers ------------------------------------------

def _wick_beyond_close_inside(bundle, bar, level, bullish):
    """Did the wick take `level` while the close stayed on our side?

    The shape of "wicked the LID". A close beyond the level is price
    ACCEPTING it rather than rejecting it, which is what the factor
    penalises: the sweep is supposed to be a raid, not a breakout.
    """
    if bar is None or bar < 0 or level is None:
        return False
    if bullish:
        return bundle.low[bar] < level and bundle.close[bar] >= level
    return bundle.high[bar] > level and bundle.close[bar] <= level


def _body_closed(evidence):
    """Did a candle CLOSE through the broken level, or only wick through it?

    Read straight off the evidence rather than recomputed. entry_models
    decides it while finding the break, because only there is it known
    whether the detector reported the break (which implies a close) or
    whether the wick fallback found it.

    Recomputing `close < level` here is what made this factor tautological
    at 100% yes: the detector's break condition IS a close through the
    level, so any break it reported already satisfied the test.
    """
    return bool(evidence.get("body_closed"))


def _fvg_mitigated_at(bundle, bar, want_bullish):
    """Did bar `bar` reach the midpoint of an active same-side gap?

    The 50%-of-wick rule from fair_value_gaps.py, restated as a mask so
    it agrees with the detector rather than approximating it.
    """
    idx = bundle.fvg_idx
    if len(idx) == 0 or bar is None or bar < 0:
        return False
    active = (idx.bullish == want_bullish) & (idx.formed_index < bar) & (
        idx.active_until >= bar
    )
    if not active.any():
        return False
    mid = idx.midpoint[active]
    if want_bullish:
        return bool((bundle.low[bar] <= mid).any())
    return bool((bundle.high[bar] >= mid).any())


def _fvg_formed_between(bundle, lo, hi, want_bullish):
    """Did any same-side gap FORM in [lo, hi]?"""
    idx = bundle.fvg_idx
    if len(idx) == 0 or lo is None or hi is None or hi < lo:
        return False
    formed = (idx.bullish == want_bullish) & (idx.formed_index >= lo) & (
        idx.formed_index <= hi
    )
    return bool(formed.any())


def _live_levels(idx, bar, want_high):
    """Mask of levels of the wanted side that are unswept as of `bar`."""
    if len(idx.level) == 0:
        return np.zeros(0, dtype=bool)
    return (
        (idx.side_high == want_high)
        & (idx.visible_from <= bar)
        & (idx.live_through >= bar)
    )


# --- the individual factors -------------------------------------------

def _no_other_lids(bundle, zone, setup, trigger):
    """Nothing standing between our stop and the zone's far edge.

    The user's definition, and it replaced a proximity-window version that
    measured differently per model. His reasoning: liquidity sitting
    BEYOND the stop is a magnet, so price will eventually go and take it,
    and our stop is on the way. So the setup is penalised for it.

    Bearish: the region runs from the stop up to the zone's top. Bullish:
    from the zone's bottom down to the stop.

    Counts levels on EITHER side, because "any M15 minor liquidity levels"
    is what he said and both sides are resting orders. Also note the
    region can be empty, when a wide setup puts the stop beyond the far
    edge already, and an empty region is a clean YES.

    This is the one factor definition shared identically by all four
    models, which is what makes their learned weights comparable.
    """
    sl = setup["sl"]
    if zone.bullish:
        low, high = zone.bottom, sl
    else:
        low, high = sl, zone.top
    if high <= low:
        return True
    idx = bundle.minor_idx
    if len(idx.level) == 0:
        return True
    live = (idx.visible_from <= trigger) & (idx.live_through >= trigger)
    inside = live & (idx.level >= low) & (idx.level <= high)
    return not bool(inside.any())


def _approach_leg(bundle, h1_ts, zone, mitigation_bar, trigger):
    """(leg_start, trigger) for the running-minimum rule, or None.

    Decision 13. Anchor on the PREVIOUS QUALIFYING TOUCH of this zone, not
    on any wick overlap, then take the deepest point in that window as the
    leg start. The anchor is what stops a second tap measuring a leg that
    includes time price already spent in the zone, and the qualifying-touch
    cut is what stops an exact-edge kiss collapsing the window to nothing.
    """
    prior = [t for t in zone.touch_at if t < mitigation_bar]
    if prior:
        # The last M15 bar of that touch's hour.
        nxt = prior[-1] + 1
        if nxt < len(h1_ts):
            window_start = m15_index_at_or_after(bundle, h1_ts[nxt]) - 1
        else:
            window_start = trigger
    else:
        anchor = min(max(zone.visible_from, 0), len(h1_ts) - 1)
        window_start = m15_index_at_or_after(bundle, h1_ts[anchor])

    if window_start is None or window_start < 0:
        window_start = 0
    if window_start > trigger:
        window_start = trigger

    series = bundle.high if zone.bullish else bundle.low
    span = series[window_start:trigger + 1]
    if len(span) == 0:
        return None
    offset = int(np.argmax(span) if zone.bullish else np.argmin(span))
    return window_start + offset, trigger


def _no_imbalance_while_mitigation(bundle, h1_ts, zone, mitigation_bar, trigger):
    """No gap left behind by the leg that approached the zone.

    The gap direction is OPPOSITE to the trade: a bearish setup's approach
    rallies UP, so the gaps it leaves are bullish. See the module
    docstring, this is the one factor whose FVG side inverts.
    """
    leg = _approach_leg(bundle, h1_ts, zone, mitigation_bar, trigger)
    if leg is None:
        return False
    lo, hi = leg
    if hi <= lo:
        # A one-candle leg cannot contain a three-candle gap.
        return True
    return not _fvg_formed_between(bundle, lo, hi, want_bullish=not zone.bullish)


def _equals_angle(bundle, zone, level_row, trigger):
    """Are the two tops of the pool close enough to read as equal?

    Decision 14. The pool's own two pivots are compared, not the pooled
    mean against either: the factor is about the SHAPE the S and R traders
    saw, and a mean hides a sloping double top completely.
    """
    if level_row is None:
        return False
    idx = bundle.equals_idx
    if level_row >= len(idx.level):
        return False
    atr = bundle.atr[trigger]
    if not np.isfinite(atr):
        return False
    first = int(idx.first_candle[level_row])
    last = int(idx.candle_index[level_row])
    series = bundle.low if zone.bullish else bundle.high
    if first < 0 or last < 0 or first >= len(series) or last >= len(series):
        return False
    return abs(float(series[first]) - float(series[last])) <= (
        EQUALS_ANGLE_ATR * float(atr)
    )


def _ce_leg(evidence):
    """(lo, hi) bars of the leg that broke internal structure, or None."""
    lo = evidence.get("extreme_m15")
    hi = evidence.get("break_m15")
    if lo is None or hi is None or lo < 0 or hi < 0 or hi < lo:
        return None
    return lo, hi


def _has_inducements(bundle, zone, setup, trigger):
    """Is there minor liquidity near the fib50 limit CE will rest at?

    Restricted to a band around the limit price rather than the whole
    breaking leg. Measured over the whole leg this answered 100% yes on
    real data, because a leg of any length contains one of the five minor
    levels a side carries at any moment, so it said nothing.

    The band is what makes it mean something: liquidity sitting where the
    pullback actually travels is liquidity the pullback can collect on its
    way to our entry, which is the user's reasoning for wanting the factor
    at all.

    Wanted side is the same as the sweep side, HIGHS for a bearish setup,
    because those are what a pullback upward into the limit takes out.
    """
    atr = bundle.atr[trigger]
    if not np.isfinite(atr):
        return False
    band = CE_INDUCEMENT_ATR * float(atr)
    limit = setup["order_price"]
    idx = bundle.minor_idx
    live = _live_levels(idx, trigger, want_high=not zone.bullish)
    if not live.any():
        return False
    near = live & (np.abs(idx.level - limit) <= band)
    return bool(near.any())


def _target_children(bundle, zone, setup, trigger):
    """{child: True} for each M15 target present. Absent means OMITTED.

    Never False. This is a liquidity-target gate, so it can only raise a
    score, exactly as factors.evaluate_liquidity_target_factors does.

    Three filters, all of which have to pass:
      side      OPPOSITE to the sweep side. A short hunts sell-side lows.
      recency   formed within TARGET_LOOKBACK_BARS of the trigger.
      distance  within TARGET_SEARCH_R of the entry (decision 23). R is
                now M15-sized, so this reach is far shorter in pips than
                the H1 gates', which is intended.
    """
    entry = setup["order_price"]
    max_distance = TARGET_SEARCH_R * setup["r_distance"]
    want_high = bool(zone.bullish)

    found = {}
    for child, idx in (("lrlq", bundle.lrlq_idx), ("equals", bundle.equals_idx)):
        live = _live_levels(idx, trigger, want_high=want_high)
        if not live.any():
            continue
        fresh = live & (idx.candle_index >= trigger - TARGET_LOOKBACK_BARS)
        if not fresh.any():
            continue
        levels = idx.level[fresh]
        if zone.bullish:
            ahead = levels > entry
        else:
            ahead = levels < entry
        if not ahead.any():
            continue
        near = np.abs(levels[ahead] - entry) <= max_distance
        if bool(near.any()):
            found[child] = True
    return found


# --- the public entry point -------------------------------------------

def evaluate_entry_factors(bundle, h1_ts, zone, setup, mitigation_bar):
    """The firing model's FROZEN factors. Target gate NOT included.

    bundle: the M15Bundle the setup was found on.
    h1_ts: the walk frame's timestamp array (ctx.ts).
    zone: the Zone the setup came from.
    setup: scan_for_entry's return value.
    mitigation_bar: the H1 bar of the qualifying touch.

    Returns only the firing model's factors. The other three models'
    24 names are absent, which is what makes compute_probability's
    denominator model-specific.
    """
    if bundle is None or setup is None:
        return {}

    model = setup["model"]
    if model not in MODEL_FACTORS:
        return {}

    evidence = setup.get("evidence") or {}
    trigger = setup["trigger_m15"]
    level = evidence.get("level")
    # The candle that actually took the liquidity. LC-1 and LC-2B sweep a
    # standing level; LC-2A raids the PBID on a possibly later candle than
    # the break that created it.
    taker = evidence.get("raid_m15")
    if taker is None:
        taker = evidence.get("sweep_m15")

    answers = {}

    def put(suffix, value):
        answers[factor_name(model, suffix)] = bool(value)

    for suffix in MODEL_FACTORS[model]:
        if suffix == "h1_ob_is_fractal":
            put(suffix, zone.primary_tier == "h1_fractal")
        elif suffix == "h1_ob_is_internal":
            put(suffix, zone.primary_tier == "h1_internal")
        elif suffix in ("wicked_the_lid", "wicked_the_pbid"):
            put(suffix, _wick_beyond_close_inside(
                bundle, taker, level, zone.bullish))
        elif suffix == "lid_with_fvg":
            # SAME direction as the trade, decision 22.
            put(suffix, _fvg_mitigated_at(
                bundle, taker, want_bullish=zone.bullish))
        elif suffix == "no_imbalance_while_mitigation":
            put(suffix, _no_imbalance_while_mitigation(
                bundle, h1_ts, zone, mitigation_bar, trigger))
        elif suffix == "no_other_lids":
            put(suffix, _no_other_lids(bundle, zone, setup, trigger))
        elif suffix == "fake_break_with_body_close":
            put(suffix, _body_closed(evidence))
        elif suffix == "equals_formed_with_less_angle":
            put(suffix, _equals_angle(
                bundle, zone, evidence.get("level_row"), trigger))
        elif suffix == "has_imbalance":
            leg = _ce_leg(evidence)
            put(suffix, leg is not None and _fvg_formed_between(
                bundle, leg[0], leg[1], want_bullish=zone.bullish))
        elif suffix == "has_inducements":
            put(suffix, _has_inducements(bundle, zone, setup, trigger))
        else:
            raise KeyError("unhandled entry factor: %s" % suffix)

    # The M15 Target Liquidity gate is DELIBERATELY not included. It is the
    # only entry factor that is not frozen at entry, so it is asked
    # separately by evaluate_entry_target_factors and re-asked every bar by
    # simulate_trade. Folding it in here would put a stale answer into the
    # frozen dict the mid-trade recheck builds on.
    return answers


def evaluate_entry_target_factors(bundle, zone, setup, m15_bar):
    """Just the three M15 Target Liquidity names, for `m15_bar`.

    Split out from the rest because it is the only part of the entry tier
    that is NOT frozen at entry. The other factors describe the setup, which
    cannot change once the order fills. This one describes what is still
    AHEAD of the trade, and the user was explicit about the consequence: an
    LRLQ target that price reaches and takes out drops OUT of the
    calculation, so the score falls back toward what the remaining untaken
    liquidity supports. simulate_trade re-asks it every bar for exactly the
    same reason it re-asks the H1 liquidity target gate.

    Dropping out, note, not flipping to NO. This is a liquidity-target gate
    and it can only ever raise a score.
    """
    if bundle is None or setup is None:
        return {}
    model = setup.get("model")
    if model not in MODEL_FACTORS:
        return {}

    answers = {}
    children = _target_children(bundle, zone, setup, m15_bar)
    for child in TARGET_CHILDREN:
        if child in children:
            answers[
                entry_factor_name(model, "%s_%s" % (TARGET_PARENT, child))
            ] = True
    # The parent is the only part of this gate that can answer no, and only
    # when neither child is present at all.
    answers[entry_factor_name(model, TARGET_PARENT)] = bool(children)
    return answers
