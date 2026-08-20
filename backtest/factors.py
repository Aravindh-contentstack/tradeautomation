"""The yes/no factor evaluation and the weighted probability formula.

Five families, all scored the same way once answered:

- **Always** (`ALWAYS_FACTORS`): the structure and zone columns, read
  straight off the merged frame's row. Yes when the tier's bullish/bearish
  value equals the trade's own direction, or when the zone matches the
  direction (premium for a short, discount for a long).
- **Mitigation OB** (`MITIGATION_OB_FACTORS`): the qualities of the zone
  price reacted FROM. Answered once, at entry, and frozen for the life of
  the trade, with one exception: the `swept_liquidity_fvg` child goes
  silent once the specific gap it swept has aged past its own 100-candle
  lookback unfilled, since months can pass between an OB forming and it
  actually being mitigated and scored. See `_ob_factor_answers`.
- **OB Target** (`OB_TARGET_FACTORS`): the qualities of the nearest
  opposite-direction zone price is being drawn TOWARD. Carries the same
  `swept_liquidity_fvg` exception as Mitigation OB above.
- **Swept Liquidity** (`SWEPT_LIQUIDITY_GATE_FACTORS`): what the most
  recently closed Daily or 4H candle took out. Not anchored to any zone,
  and frozen at entry like the Mitigation OB gate.
- **Liquidity Target** (`LIQUIDITY_TARGET_FACTORS`): what untaken
  liquidity is still sitting within reach in the direction of travel.
  Dynamic, and the one gate that never answers no (see below).

Dynamic exclusion
-----------------
A factor that has nothing to say is OMITTED from the result dict, not
answered "no". A timeframe with no valid order block, an already-dead one,
or one too far away to be a realistic draw is genuinely silent, and
scoring it "no" would punish the setup for the market's shape rather than
for anything about the setup. `compute_probability` normalises over the
factors actually present, so omission costs nothing rather than costing
half a weight.

The trade-off, accepted deliberately: a setup evidenced only on H1 can
reach the same 100% as one where Daily, 4H and H1 all agree. Breadth of
confluence is not rewarded, only agreement among whatever spoke.

The supporting/opposing flip
----------------------------
A Target OB's qualities mean opposite things depending on whether price
has reached it yet. Unmitigated, a strong zone pulls price toward the
target, which helps the trade. Once price arrives, that same strength is
what stops it going further, which hurts. So every answer is negated once
the target is reached: strong-and-unreached is a yes, strong-and-reached
is a no, and a weak zone reads the other way round on both counts.

Liquidity Target never answers no
--------------------------------
Worth stating plainly, because it makes that one gate different from every
other. Its factors are emitted as yes when a level is in range and omitted
otherwise, with no parent roll-up, so the gate can only ever RAISE a
setup's score. That follows directly from the user's rule that a target
price has covered becomes NA rather than turning against the trade: if
"covered" is silence, then "never there" has to be silence too, or the two
would be scored differently for no reason a trader would recognise.

The discrimination therefore comes from HOW MANY kinds of liquidity are in
range, not from any of them saying no. Measured on EUR_USD 2025, both gates
together add a mean of 7.7 factors per candidate against a base of about 27,
ranging from 2 to 16. If that proves too blunt, the fix is a parent that
answers no when nothing at all is in range, exactly mirroring the swept
side, and roadmap/liquidity.md records it as the open option.
"""

STRUCTURE_FACTORS = [
    "daily_swing_structure",
    "daily_internal_structure",
    "daily_fractal_structure",
    "h4_swing_structure",
    "h4_internal_structure",
    "h4_fractal_structure",
    "h1_swing_structure",
    # Demoted from mandatory entry gates to scored factors when the trigger
    # moved to OB mitigation. They used to reject a candidate outright, but
    # the direction now comes from the mitigated OB, and that direction can
    # legitimately disagree with them: price mitigating a bearish internal
    # OB is a sell setup even while the swing structure is bullish. As hard
    # gates they would have rejected exactly those setups.
    "h1_internal_structure",
    "h1_fractal_structure",
]

ZONE_FACTORS = [
    "daily_swing_zone",
    "daily_internal_zone",
    "h4_swing_zone",
    "h4_internal_zone",
    "h1_swing_zone",
    "h1_internal_zone",
]

ALWAYS_FACTORS = STRUCTURE_FACTORS + ZONE_FACTORS

TIMEFRAME_KEYS = {"Daily": "daily", "4H": "h4", "H1": "h1"}

# The per-OB qualities scored under both gates, as (factor suffix, the
# column ob_state carries it in). Containment is per-timeframe, so it is
# added separately below: Daily has no higher timeframe to sit inside.
OB_QUALITY_FACTORS = [
    ("caused_displacement", "caused_displacement"),
    ("caused_imbalance", "caused_imbalance"),
    ("has_inducement", "has_inducement"),
    ("flip_zone", "is_flip_zone"),
]

# The swept-liquidity children an order block can carry, as
# (factor suffix, the column ob_state carries it in).
#
# Every timeframe gets the structural kinds plus the pivot-derived ones. H1
# additionally gets the time-based kinds, which is the user's confirmed rule
# that an H1 sweep only counts when it is attached to an order block: a sweep
# that does not break structure leaves nothing to trade from, so there is no
# standalone H1 Swept Liquidity gate for them to live in.
#
# Kept per timeframe rather than as one shared list so the factor namespace
# does not fill up with names that can never be emitted, such as
# daily_mitigation_ob_swept_liquidity_asian.
# APPEND ONLY. backtest/target_log.py derives its bitmask positions from
# this order, and reordering silently reinterprets every stored row. The
# first five are the ones that existed before the liquidity work and keep
# their original positions for that reason, even though grouping the
# structural three with old points and equals would read better.
_SHARED_SWEPT_FACTORS = [
    ("swept_liquidity_swing", "swept_liquidity_swing"),
    ("swept_liquidity_internal", "swept_liquidity_internal"),
    ("swept_liquidity_fractal", "swept_liquidity_fractal"),
    ("swept_liquidity_fvg", "swept_liquidity_fvg"),
    ("swept_liquidity_previous_candle", "swept_liquidity_previous_candle"),
    ("swept_liquidity_old_points", "swept_liquidity_old_point"),
    ("swept_liquidity_equals", "swept_liquidity_equals"),
    ("swept_liquidity_lrlq", "swept_liquidity_lrlq"),
]

_H1_TIME_SWEPT_FACTORS = [
    ("swept_liquidity_asian", "swept_liquidity_asian"),
    ("swept_liquidity_london", "swept_liquidity_london"),
    ("swept_liquidity_ny", "swept_liquidity_ny"),
    ("swept_liquidity_previous_day", "swept_liquidity_previous_day"),
    ("swept_liquidity_previous_week", "swept_liquidity_previous_week"),
]

SWEPT_LIQUIDITY_FACTORS_BY_TF = {
    "Daily": _SHARED_SWEPT_FACTORS,
    "4H": _SHARED_SWEPT_FACTORS,
    "H1": _SHARED_SWEPT_FACTORS + _H1_TIME_SWEPT_FACTORS,
}

# Kept for callers that want the union without caring which timeframe can
# emit what. backtest/target_log.py's bit table is one.
SWEPT_LIQUIDITY_FACTORS = _SHARED_SWEPT_FACTORS + _H1_TIME_SWEPT_FACTORS

CONTAINMENT_FACTOR = {"4H": "within_daily_ob", "H1": "within_h4_ob"}


def _gate_factor_names(gate):
    """Every key one gate contributes, across all three timeframes."""
    names = []
    for timeframe, prefix in TIMEFRAME_KEYS.items():
        children = OB_QUALITY_FACTORS + SWEPT_LIQUIDITY_FACTORS_BY_TF[timeframe]
        for suffix, _ in children:
            names.append("%s_%s_%s" % (prefix, gate, suffix))
        # The parent roll-up, scored only when nothing at all was swept.
        names.append("%s_%s_swept_liquidity" % (prefix, gate))
        if timeframe in CONTAINMENT_FACTOR:
            names.append("%s_%s_%s" % (prefix, gate, CONTAINMENT_FACTOR[timeframe]))
    return names


MITIGATION_OB_FACTORS = _gate_factor_names("mitigation_ob")
OB_TARGET_FACTORS = _gate_factor_names("ob_target")

# --- The two standalone liquidity gates ---------------------------------
#
# Neither is anchored to an order block. Swept Liquidity asks what the last
# closed candle took; Liquidity Target asks what is still sitting there to
# be taken.

# Swept Liquidity is Daily and 4H only. H1's sweeps live on the OB gates
# above, per the user's confirmed rule.
SWEPT_GATE_TIMEFRAMES = ["Daily", "4H"]

# One time-based child each, matching which timeframe owns that level. The
# previous week is a Daily-candle question, the previous day a 4H one.
SWEPT_GATE_TIME_CHILD = {"Daily": "previous_week", "4H": "previous_day"}

SWEPT_GATE_CHILDREN = ["swing", "internal", "fractal", "old_points", "equals",
                       "fvg", "lrlq"]

# Liquidity Target runs on all three timeframes. Structural strong points
# are deliberately absent: the user confirmed only Old Points can be a
# target, since a swing/internal/fractal point is expected to HOLD rather
# than to be run.
TARGET_GATE_CHILDREN = ["old_points", "equals", "fvg", "lrlq"]

# Session and daily/weekly levels are targets on H1 only. Unlike the sweep
# side, where each timeframe owns its own clock level, every time-based
# target is hunted inside an H1 trade.
TARGET_GATE_H1_CHILDREN = ["asian", "london", "ny", "previous_day", "previous_week"]


def _swept_gate_names():
    names = []
    for timeframe in SWEPT_GATE_TIMEFRAMES:
        prefix = TIMEFRAME_KEYS[timeframe]
        for child in SWEPT_GATE_CHILDREN + [SWEPT_GATE_TIME_CHILD[timeframe]]:
            names.append("%s_swept_liquidity_%s" % (prefix, child))
        names.append("%s_swept_liquidity" % prefix)
    return names


def _target_gate_names():
    names = []
    for timeframe, prefix in TIMEFRAME_KEYS.items():
        children = list(TARGET_GATE_CHILDREN)
        if timeframe == "H1":
            children += TARGET_GATE_H1_CHILDREN
        for child in children:
            names.append("%s_liquidity_target_%s" % (prefix, child))
    return names


SWEPT_LIQUIDITY_GATE_FACTORS = _swept_gate_names()
LIQUIDITY_TARGET_FACTORS = _target_gate_names()

# --- The mitigation-leg gate, H1 only -----------------------------------
#
# The third swept-liquidity question, and the only one that looks at the leg
# which actually delivered price back to the zone. The Mitigation OB gate
# above describes the FORMATION leg and is frozen at the OB's trigger, which
# may be weeks earlier; the standalone gate describes the last closed Daily or
# 4H candle. Neither can say "the tap was preceded by a stop run on the
# previous day's low".
#
# This does not contradict the rule that there is no standalone H1 Swept
# Liquidity gate. That rule is about formation: an H1 sweep breaking no
# structure produces no order block, so there is nothing to trade from. This
# gate is anchored to a mitigation that has already happened.
#
# The children are the external and time-based kinds only. Structural kinds
# are a formation-tier concept, and FVG is excluded because an order block's
# own displacement leg leaves an imbalance directly in front of the zone,
# so price cannot mitigate the block without sweeping it. Crediting that
# would score caused_imbalance a second time under another name. Names that
# can never be emitted are deliberately not created, per the note above.
MITIGATION_LEG_CHILDREN = ["old_points", "equals", "lrlq", "asian", "london",
                           "ny", "previous_day", "previous_week"]

MITIGATION_LEG_FACTORS = (
    ["h1_mitigation_leg_swept_liquidity_%s" % child
     for child in MITIGATION_LEG_CHILDREN]
    + ["h1_mitigation_leg_swept_liquidity"]
)

# Factor-name child to the `kind` string the detectors and liq_state use.
# They differ in exactly one place, "old_points" against "old_point", which
# is not worth renaming either side over: the factor sheet says Old Points
# and the detector emits one row per level.
KIND_FOR = {
    "swing": "swing",
    "internal": "internal",
    "fractal": "fractal",
    "old_points": "old_point",
    "equals": "equals",
    "lrlq": "lrlq",
    "fvg": "fvg",
    "asian": "asian",
    "london": "london",
    "ny": "ny",
    "previous_day": "previous_day",
    "previous_week": "previous_week",
}

ALL_FACTORS = (
    ALWAYS_FACTORS
    + MITIGATION_OB_FACTORS
    + OB_TARGET_FACTORS
    + SWEPT_LIQUIDITY_GATE_FACTORS
    + LIQUIDITY_TARGET_FACTORS
    + MITIGATION_LEG_FACTORS
)


def evaluate_always_factors(row, trade_direction):
    """The structure and zone factors, read off one merged-frame row."""
    results = {}
    for column in STRUCTURE_FACTORS:
        results[column] = row[column] == trade_direction
    for column in ZONE_FACTORS:
        zone = row[column]
        if trade_direction == "bearish":
            results[column] = zone == "premium"
        else:
            results[column] = zone == "discount"
    return results


def _ob_factor_answers(series, ob_row, bar_index, prefix, gate, negate):
    """One order block's qualities as factor keys.

    `negate` flips every answer, which is how an opposing Target OB is
    scored: the underlying facts about the zone do not change, only what
    they mean for this trade.

    Swept liquidity follows the confirmed roll-up: when the zone swept
    something, only the types it actually swept are emitted (as yes), and
    the parent is dropped, so sweeping two kinds of liquidity scores
    higher than sweeping one. When it swept nothing at all, the parent
    alone is emitted as a no. Either way the untouched sub-types stay
    silent rather than each contributing a no.

    The "fvg" child carries one further gate, the inverse of flip_zone's:
    where flip_zone waits for a fact to become knowable, fvg's credit
    expires once the specific gap it swept has aged past its own
    100-candle lookback unfilled (series.fvg_stale_from, in H1-bar space).
    Confirmed with the user this must be pure age, not fill status: a fill
    of that gap, by this OB's own candles, by this OB's later mitigation
    candle, or by anyone else at any time, never shortens the credit, only
    the gap's own unconditional lookback may. Suppressing it here (before
    `emit`, hence before `negate`) means a stale fvg reads as silent on
    both the Mitigation OB and the OB Target gate, never as a flipped
    "yes" on a reached target. This one check must never read the OB's
    own invalidated state, and nothing about the OB's invalidation may
    ever read fvg_stale_from: the two lifecycles are independent.
    """
    quality = series.quality
    results = {}

    def emit(suffix, value):
        results["%s_%s_%s" % (prefix, gate, suffix)] = (not value) if negate else value

    for suffix, column in OB_QUALITY_FACTORS:
        if column not in quality:
            continue
        if suffix == "flip_zone":
            # Unlike the others this is not knowable at the OB's own
            # trigger, since it depends on the flipped zone dying. Silent
            # until that has actually happened.
            if bar_index < series.flip_known_from[ob_row]:
                continue
        emit(suffix, bool(quality[column][ob_row]))

    containment = CONTAINMENT_FACTOR.get(series.timeframe)
    if containment and containment in quality:
        emit(containment, bool(quality[containment][ob_row]))

    swept_any = False
    for suffix, column in SWEPT_LIQUIDITY_FACTORS_BY_TF[series.timeframe]:
        if column not in quality or not bool(quality[column][ob_row]):
            continue
        # The one end-boundary gate in this whole function; every other
        # child here is a frozen fact. Kept as `>=` deliberately, the
        # mirror of flip_zone's `<` above: this is an EXPIRY, not a
        # not-yet-knowable start.
        if suffix == "swept_liquidity_fvg" and bar_index >= series.fvg_stale_from[ob_row]:
            continue
        emit(suffix, True)
        swept_any = True
    if not swept_any:
        emit("swept_liquidity", False)

    return results


def evaluate_mitigation_ob_factors(obs, bar_index, direction, mitigation_ob):
    """The Mitigation OB gate, for every timeframe that has one.

    H1 comes from the OB that triggered the trade. The 4H and Daily
    blocks are scored only when price is actually inside a valid zone on
    that timeframe, and are omitted entirely otherwise: unlike H1, higher
    timeframes are not required to have been mitigated for a trade to
    exist.

    Only same-direction zones count. A higher-timeframe zone facing the
    other way is not a Mitigation OB at all, it is a target, and it gets
    picked up by the other gate.
    """
    if obs is None:
        return {}

    results = {}
    sign = 1 if direction == "bullish" else -1

    for timeframe, prefix in TIMEFRAME_KEYS.items():
        series = obs.series.get(timeframe)
        if series is None:
            continue
        if timeframe == "H1":
            ob_row = mitigation_ob
        else:
            ob_row = int(obs.mitigated_htf[timeframe][bar_index])
        if ob_row < 0 or series.sign[ob_row] != sign:
            continue
        results.update(
            _ob_factor_answers(series, ob_row, bar_index, prefix, "mitigation_ob", False)
        )

    return results


def evaluate_ob_target_factors(obs, bar_index, direction, max_distance, high, low):
    """The OB Target gate, one target per timeframe.

    The target is the nearest valid, unmitigated zone facing the opposite
    way to the trade: for a long, the nearest bearish zone above. Beyond
    `max_distance` it is not a realistic draw and the whole gate is
    omitted, same as if no zone existed.

    `high`/`low` are the current bar's range, used to decide whether price
    has REACHED the target, which is what flips every answer from
    supporting to opposing.
    """
    if obs is None:
        return {}

    results = {}
    bullish = direction == "bullish"

    for timeframe, prefix in TIMEFRAME_KEYS.items():
        series = obs.series.get(timeframe)
        if series is None:
            continue
        lookup = obs.target_above if bullish else obs.target_below
        ob_row = int(lookup[timeframe][bar_index])
        if ob_row < 0:
            continue
        # A target faces the other way by definition. The nearest zone in
        # the direction of travel can be same-direction (price running
        # back into its own kind), and that is not a target.
        if series.sign[ob_row] == (1 if bullish else -1):
            continue

        edge = series.bottom[ob_row] if bullish else series.top[ob_row]
        distance = abs(edge - (high if bullish else low))
        if max_distance is not None and distance > max_distance:
            continue

        reached = low <= series.top[ob_row] and high >= series.bottom[ob_row]
        results.update(
            _ob_factor_answers(series, ob_row, bar_index, prefix, "ob_target", reached)
        )

    return results


def _wanted_side(direction):
    """Which side of the book a sweep has to have taken to help this trade.

    A long wants the SELL stops under the market run: the lows have been
    taken, the sellers who were going to sell have sold, and what is left
    above is buyers. Sweeping the highs before a long is the opposite
    story, so it is omitted rather than scored.
    """
    return "low" if direction == "bullish" else "high"


def evaluate_swept_liquidity_factors(liq, bar_index, direction):
    """The standalone Swept Liquidity gate, Daily and 4H only.

    What the most recently CLOSED candle of that timeframe took, which is
    the user's confirmed recency rule: a sweep two candles back scores
    nothing. liq_state precomputed that carry, so this is array reads.

    Roll-up follows the OB gates exactly: only the kinds actually swept are
    emitted, as yes, and the parent is dropped, so taking two kinds of
    liquidity scores higher than taking one. When nothing was swept the
    parent alone is emitted, as a no.

    Frozen at entry by the caller, same as the Mitigation OB gate. What
    price took on its way into the zone does not change afterwards.

    The "external to the swing range" condition on old points is NOT
    applied here. It needs the level's own price, which this gate never
    sees, and it is a point-in-time question best answered on the candle
    that did the sweeping. smc/liquidity/sweeps.pooled_level_sweeps applies
    it there instead.
    """
    if liq is None:
        return {}

    side = _wanted_side(direction)
    results = {}

    for timeframe in SWEPT_GATE_TIMEFRAMES:
        prefix = TIMEFRAME_KEYS[timeframe]
        children = SWEPT_GATE_CHILDREN + [SWEPT_GATE_TIME_CHILD[timeframe]]

        swept_any = False
        for child in children:
            swept = liq.swept_last_candle.get((timeframe, KIND_FOR[child], side))
            if swept is None or not bool(swept[bar_index]):
                continue
            results["%s_swept_liquidity_%s" % (prefix, child)] = True
            swept_any = True

        if not swept_any:
            results["%s_swept_liquidity" % prefix] = False

    return results


def evaluate_mitigation_leg_swept_factors(liq, bar_index, direction,
                                          ob_top, ob_bottom):
    """What the H1 approach leg took on its way INTO the zone. H1 only.

    Answers, per kind: is there a swept level whose credit is still alive on
    this bar AND which sits on the far side of the zone from price. Both parts
    matter.

    The geometry is the part worth stating, because it reads backwards.
    Eligible liquidity sits ABOVE a bullish order block. A demand zone at
    1.0800-1.0820 with the previous day's low at 1.0830: price wicks below
    1.0830, running the sell stops resting under it, then keeps falling into
    the zone, where that sell-side liquidity is what fills the buy orders. Had
    the level sat BELOW the zone, price would have to destroy the order block
    to reach it and there would be no setup left. So a bullish OB wants a
    LOW-side sweep (the stops under a low were run) whose level is ABOVE
    ob_top, and liq_state's low-side array holds a MAX for exactly that
    reason.

    A level inside [ob_bottom, ob_top] is excluded too: taking it IS the
    mitigation, not a separate stop run preceding it.

    Credit expiry, the chain rule and the 3x ATR spend test all live in
    smc/liquidity/sweep_credit.py, so this is array reads. Roll-up follows
    every other swept gate: the kinds that survived are emitted as yes and
    the parent is dropped, or the parent alone answers no.

    Called with entry_index rather than the mitigation bar, which is what
    makes the killzone deferral fall out for free: resolve_entry_bar returns
    the bar whose CLOSE is the entry, and that is the bar whose close the
    chain rule has to have survived. Frozen at entry by the caller.
    """
    if liq is None or getattr(liq, "mitigation_credit", None) is None:
        return {}

    side = _wanted_side(direction)
    bullish = direction == "bullish"
    results = {}

    swept_any = False
    for child in MITIGATION_LEG_CHILDREN:
        prices = liq.mitigation_credit.get((KIND_FOR[child], side))
        if prices is None:
            continue
        price = prices[bar_index]
        # NaN means no credit of this kind survives on this bar. Tested by
        # self-comparison to keep this module import-free, same as
        # evaluate_liquidity_target_factors below.
        if price != price:
            continue
        # The surviving extreme is the best candidate of its kind, so if it
        # fails the zone test no level of that kind can pass it.
        beyond = price > ob_top if bullish else price < ob_bottom
        if not beyond:
            continue
        results["h1_mitigation_leg_swept_liquidity_%s" % child] = True
        swept_any = True

    if not swept_any:
        results["h1_mitigation_leg_swept_liquidity"] = False

    return results


def evaluate_liquidity_target_factors(liq, bar_index, direction, high, low,
                                      max_distance, week_max_distance):
    """The standalone Liquidity Target gate, all three timeframes.

    Emits YES for every kind that still has an untaken level sitting within
    reach in the direction of travel, and omits the rest. There is no NO
    case and no parent roll-up: the user's rule is that a target which
    price has covered becomes NA rather than turning against the trade, and
    a kind that never had a level in range never had anything to say
    either.

    That makes this gate monotonically helpful, unlike OB Target which
    negates once price arrives. It is the deliberate consequence of "these
    factors become NA as and when the price covers these liquidities".

    Re-evaluated on every bar of an open trade, which is what lets targets
    fall away one at a time as price eats through them.

    week_max_distance is separate because previous-week levels are allowed
    out to 7.5R where everything else is capped at 5R: a weekly level is a
    bigger draw and worth reaching further for.
    """
    if liq is None:
        return {}

    bullish = direction == "bullish"
    lookup = liq.target_above if bullish else liq.target_below
    edge = high if bullish else low

    results = {}
    for timeframe, prefix in TIMEFRAME_KEYS.items():
        children = list(TARGET_GATE_CHILDREN)
        if timeframe == "H1":
            children += TARGET_GATE_H1_CHILDREN

        for child in children:
            price = lookup.get((timeframe, KIND_FOR[child]))
            if price is None:
                continue
            target = price[bar_index]
            if target != target:
                continue

            limit = week_max_distance if child == "previous_week" else max_distance
            if limit is not None and abs(target - edge) > limit:
                continue
            results["%s_liquidity_target_%s" % (prefix, child)] = True

    return results


def evaluate_factors(row, trade_direction):
    """The Always gate alone, for callers holding only a frame row.

    Kept at its original name and signature so anything that scores a row
    without OB state keeps working unchanged.
    """
    return evaluate_always_factors(row, trade_direction)


def compute_probability(factor_results, weights):
    """(sum of weights of yes factors - 0.5 * sum of weights of no
    factors) / (sum of the weights of the factors ACTUALLY EVALUATED) * 100.

    Normalized against the evaluated factors rather than a fixed count,
    for two reasons that happen to want the same denominator.

    The first is weight drift. The +/-2% multiplicative update always
    drags weights below 1.0 whenever the strategy's win rate sits under
    50% (1.02*0.98=0.9996<1), which would otherwise shrink the achievable
    probability ceiling over time and could permanently lock the system
    out of ever taking a trade again once that ceiling fell under
    whatever threshold was in force. Dividing by the live weight sum keeps
    probability a measure of RELATIVE confidence regardless of how much
    the absolute scale has drifted.

    The second is dynamic exclusion: an omitted factor drops out of the
    denominator too, so a timeframe with no order block to speak of
    neither helps nor hurts. Before exclusion existed, factor_results
    always spanned every factor and this expression equalled
    sum(weights.values()), so historical probabilities reproduce exactly.
    """
    yes_weight = sum(weights[f] for f, is_yes in factor_results.items() if is_yes)
    no_weight = sum(weights[f] for f, is_yes in factor_results.items() if not is_yes)
    total_weight = sum(weights[f] for f in factor_results)
    if total_weight <= 0:
        return 0.0
    return (yes_weight - 0.5 * no_weight) / total_weight * 100
