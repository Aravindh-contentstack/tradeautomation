"""The 13-factor yes/no evaluation and the weighted probability formula.

Rules (per the strategy spec, confirmed and corrected with the user):
- 7 structure factors: yes if the tier's bullish/bearish value equals the
  trade's own direction.
- 5 zone factors: yes if (trade_direction == "bearish" and zone ==
  "premium") or (trade_direction == "bullish" and zone == "discount").
  Same formula for all 5.

h1_internal_structure, h1_internal_zone, and h1_fractal_structure are
deliberately excluded from both lists: all three are entry-trigger gates
(backtest/simulate.py's find_signals) rather than scored factors. The
first two are always "yes" for every candidate by construction (a
candidate can't exist unless they align), so scoring them added no
discriminating signal, only noise to the probability's weight scale.
h1_swing_structure and h1_swing_zone remain regular scored factors.
"""

STRUCTURE_FACTORS = [
    "daily_swing_structure",
    "daily_internal_structure",
    "daily_fractal_structure",
    "h4_swing_structure",
    "h4_internal_structure",
    "h4_fractal_structure",
    "h1_swing_structure",
]

ZONE_FACTORS = [
    "daily_swing_zone",
    "daily_internal_zone",
    "h4_swing_zone",
    "h4_internal_zone",
    "h1_swing_zone",
]

ALL_FACTORS = STRUCTURE_FACTORS + ZONE_FACTORS


def evaluate_factors(row, trade_direction):
    """Returns {factor_name: bool} for all 13 factors on one signal row."""
    results = {}
    for col in STRUCTURE_FACTORS:
        results[col] = row[col] == trade_direction
    for col in ZONE_FACTORS:
        zone = row[col]
        if trade_direction == "bearish":
            results[col] = zone == "premium"
        else:
            results[col] = zone == "discount"
    return results


def compute_probability(factor_results, weights):
    """(sum of weights of yes factors - 0.5 * sum of weights of no
    factors) / (current sum of all 13 weights) * 100.

    Normalized against the CURRENT total weight, not the fixed factor
    count, per user decision: the +/-2% multiplicative update always
    drifts weights below 1.0 whenever the strategy's win rate sits under
    50% (1.02*0.98=0.9996<1), which would otherwise shrink the achievable
    probability ceiling over time and could permanently lock the system
    out of ever taking a trade again once that ceiling fell under
    whatever threshold was in force. Dividing by the current weight sum
    keeps probability a measure of RELATIVE confidence (yes-factors vs
    no-factors) regardless of how much the absolute weight scale has
    drifted, so the same set of factor answers always yields the same
    probability even as weights decay or grow over years of trading.
    """
    yes_weight = sum(weights[f] for f, is_yes in factor_results.items() if is_yes)
    no_weight = sum(weights[f] for f, is_yes in factor_results.items() if not is_yes)
    total_weight = sum(weights.values())
    return (yes_weight - 0.5 * no_weight) / total_weight * 100
