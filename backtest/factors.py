"""The yes/no factor evaluation and the weighted probability formula.

Three families, all scored the same way once answered:

- **Always** (`ALWAYS_FACTORS`): the structure and zone columns, read
  straight off the merged frame's row. Yes when the tier's bullish/bearish
  value equals the trade's own direction, or when the zone matches the
  direction (premium for a short, discount for a long).
- **Mitigation OB** (`MITIGATION_OB_FACTORS`): the qualities of the zone
  price reacted FROM. Answered once, at entry, and frozen for the life of
  the trade.
- **OB Target** (`OB_TARGET_FACTORS`): the qualities of the nearest
  opposite-direction zone price is being drawn TOWARD.

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

Old Points and Equals liquidity appear in factors/*.csv but have no
detectors yet, so they are absent here rather than permanently "no". A
constant "no" would drag every probability down by a fixed amount and
feed pure noise into the weight learning.
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

# The swept-liquidity children that have detectors. Old Points and Equals
# are deliberately missing (see the module docstring).
SWEPT_LIQUIDITY_FACTORS = [
    ("swept_liquidity_swing", "swept_liquidity_swing"),
    ("swept_liquidity_internal", "swept_liquidity_internal"),
    ("swept_liquidity_fractal", "swept_liquidity_fractal"),
    ("swept_liquidity_fvg", "swept_liquidity_fvg"),
    ("swept_liquidity_previous_candle", "swept_liquidity_previous_candle"),
]

CONTAINMENT_FACTOR = {"4H": "within_daily_ob", "H1": "within_h4_ob"}


def _gate_factor_names(gate):
    """Every key one gate contributes, across all three timeframes."""
    names = []
    for timeframe, prefix in TIMEFRAME_KEYS.items():
        for suffix, _ in OB_QUALITY_FACTORS + SWEPT_LIQUIDITY_FACTORS:
            names.append("%s_%s_%s" % (prefix, gate, suffix))
        # The parent roll-up, scored only when nothing at all was swept.
        names.append("%s_%s_swept_liquidity" % (prefix, gate))
        if timeframe in CONTAINMENT_FACTOR:
            names.append("%s_%s_%s" % (prefix, gate, CONTAINMENT_FACTOR[timeframe]))
    return names


MITIGATION_OB_FACTORS = _gate_factor_names("mitigation_ob")
OB_TARGET_FACTORS = _gate_factor_names("ob_target")

ALL_FACTORS = ALWAYS_FACTORS + MITIGATION_OB_FACTORS + OB_TARGET_FACTORS


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
    for suffix, column in SWEPT_LIQUIDITY_FACTORS:
        if column in quality and bool(quality[column][ob_row]):
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
