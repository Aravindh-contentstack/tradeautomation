"""Adaptive per-factor weight table.

Starts all 15 factors at weight 1.0. After each trade, a factor's weight
moves +/-2% depending on whether it said yes/no and whether the trade
won or lost, per the user's exact 4-branch rule.

Learning runs over ALL candidates, including the ones the prior year's
probability threshold rejected: the factors were evaluated on those bars
too, and the market answered them either way. Restricting learning to the
taken subset would also feed the ratchet described in backtest/settings.py.
"""

import pandas as pd

from backtest.factors import ALL_FACTORS


def initial_weights():
    return {f: 1.0 for f in ALL_FACTORS}


def update_weights(weights, factor_results, realised_r):
    """Mutates and returns weights in place.

    yes + win  -> *1.02
    yes + loss -> *0.98
    no + win   -> *0.98
    no + loss  -> *1.02

    A realised_r of exactly 0 RETURNS THE WEIGHTS COMPLETELY UNCHANGED --
    no factor is touched at all. This is the user's decision, and the
    reason is that a breakeven trade neither confirmed nor refuted
    anything: the stop was moved to entry and price came back to it, so
    the market never delivered a verdict on whether the factors were
    right. Nudging every factor on that non-answer would be pure noise,
    and with the 19:00 breakeven rule in place these are common enough
    that the noise would accumulate.

    Takes realised_r rather than a "win"/"loss" string precisely so this
    third case is representable.
    """
    if realised_r == 0:
        return weights

    won = realised_r > 0
    for factor, is_yes in factor_results.items():
        if is_yes == won:
            weights[factor] *= 1.02
        else:
            weights[factor] *= 0.98
    return weights


def save_weights(weights, path):
    pd.DataFrame([weights]).to_csv(path, index=False)


def load_weights(path):
    df = pd.read_csv(path)
    return df.iloc[0].to_dict()
