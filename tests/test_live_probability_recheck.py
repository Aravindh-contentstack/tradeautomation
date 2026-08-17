"""The mid-trade live probability recheck (step 3 of simulate_trade).

Unlike the 19:00 checkpoint, this rule watches the trade's own probability
every bar it is open: Mitigation OB factors stay exactly as frozen at
entry, but Always (structure/zone) and Target OB factors are re-evaluated
live, using the same frozen weights and the same threshold that admitted
the trade. The first bar the recomputed score drops below threshold moves
the stop to breakeven, once.

Every fixture pins the weight of every ALWAYS factor at 0.0 except
h1_internal_structure, which alone is given weight 1.0. That collapses
compute_probability to a single yes/no reading (100% or -50%), so a test
failure points at the recheck mechanism rather than at the twelve-factor
arithmetic already covered by test_probability.py-style tests. Target OB
is kept out of the picture entirely via a fake ObUniverse whose `series`
dict is empty, so evaluate_ob_target_factors finds nothing on every
timeframe and contributes no keys at all.
"""

import pytest

from backtest.factors import ALL_FACTORS, ALWAYS_FACTORS
from backtest.simulate import EXIT_BE_STOP, simulate_trade
from conftest import DIRECTION, ENTRY, PIP_SIZE, R_DISTANCE, SL, h1
from backtest.context import build_market_context


class _FakeObs:
    """Just enough of ObUniverse for evaluate_ob_target_factors to see
    "nothing to score" on every timeframe, without building a real
    pipeline bundle.
    """

    series = {}


def _weights_isolating(factor):
    """Every ALL_FACTORS key at weight 0 except `factor` at 1, so
    compute_probability reduces to that one factor's yes/no answer.
    """
    weights = {f: 0.0 for f in ALL_FACTORS}
    weights[factor] = 1.0
    return weights


def _ctx_with_flip(structure_values, prices, pip_size=PIP_SIZE):
    """A MarketContext carrying the Always-factor columns simulate_trade's
    live recheck needs, plus a _FakeObs so the recheck actually runs.

    structure_values is h1_internal_structure's value per bar (the only
    Always factor this module's tests ever vary); every other Always
    factor is held constant since its weight is zeroed out anyway.
    """
    df = h1(prices)
    for column in ALWAYS_FACTORS:
        if column == "h1_internal_structure":
            df[column] = structure_values
        elif column.endswith("_zone"):
            df[column] = "discount"
        else:
            df[column] = "bullish"
    return build_market_context(df, pip_size, obs=_FakeObs())


# idx0..idx2 never dip near entry (1.1000), so the flip on idx2 is the
# only thing that can move the stop in these fixtures.
BASE_PRICES = [
    ("2024-01-08T08:00:00Z", 1.1010, 1.1012, 1.1008, 1.1010),  # idx0, entry bar
    ("2024-01-08T09:00:00Z", 1.1010, 1.1012, 1.1008, 1.1010),  # idx1, still matches -> 100%
    ("2024-01-08T10:00:00Z", 1.1010, 1.1012, 1.1008, 1.1010),  # idx2, flips -> -50%
]
STRUCTURE_FLIP = ["bullish", "bullish", "bearish"]

# idx3, dips through entry: only in profit at 1.1010, then wicks to
# 1.0995 (below entry, above the original SL of 1.0980). The flip has
# already happened by idx2, so idx3 stays bearish too.
DIP_THROUGH_ENTRY = ("2024-01-08T11:00:00Z", 1.1005, 1.1006, 1.0995, 1.1000)
STRUCTURE_FLIP_WITH_DIP = STRUCTURE_FLIP + ["bearish"]

# idx3', an alternative that stays clear of both entry and the original
# SL, so a trade already at breakeven survives to see a later checkpoint.
STAYS_ABOVE_ENTRY = ("2024-01-08T11:00:00Z", 1.1006, 1.1007, 1.1005, 1.1006)


def test_probability_drop_moves_stop_to_breakeven_once():
    """h1_internal_structure flipping away from the trade's own direction
    on bar idx2 drives the isolated probability from 100% to -50%, which
    crosses the threshold of 60. The stop must move to entry right there,
    and a dip through entry on the very next bar must resolve as
    EXIT_BE_STOP, proving the stop genuinely moved rather than just the
    flag being set.
    """
    ctx = _ctx_with_flip(STRUCTURE_FLIP_WITH_DIP, BASE_PRICES + [DIP_THROUGH_ENTRY])
    result = simulate_trade(
        ctx, 0, DIRECTION, ENTRY, SL, R_DISTANCE,
        weights=_weights_isolating("h1_internal_structure"),
        threshold=60,
        mitigation_factor_results={},
    )

    assert result["be_moved"] is True
    assert result["be_idx"] == 2
    assert result["be_trigger"] == "target_ob_probability"
    assert result["be_probability"] == pytest.approx(-50.0)
    assert result["terminal_reason"] == EXIT_BE_STOP
    assert result["terminal_r"] == pytest.approx(0.0)
    assert result["terminal_idx"] == 3


def test_omitting_the_new_params_disables_the_recheck_entirely():
    """The same ctx and the same structure flip, but simulate_trade called
    exactly as every pre-existing caller/test calls it (no weights, no
    threshold, no mitigation_factor_results). The recheck must not run at
    all: no breakeven move, regardless of what the Always factors do.
    This is the regression guard that the feature is fully opt-in.
    """
    ctx = _ctx_with_flip(STRUCTURE_FLIP_WITH_DIP, BASE_PRICES + [DIP_THROUGH_ENTRY])
    result = simulate_trade(ctx, 0, DIRECTION, ENTRY, SL, R_DISTANCE)

    assert result["be_moved"] is False
    assert result["be_trigger"] is None
    assert result["be_probability"] is None


def test_19h_checkpoint_after_an_already_triggered_probability_be_is_a_noop():
    """Once the probability recheck has already moved the stop to
    breakeven, a later 19:00 checkpoint that would ALSO qualify for a
    breakeven move must not overwrite be_trigger or be_idx: the same
    `be_moved` guard both rules share makes whichever fires first final.
    """
    prices = BASE_PRICES + [
        STAYS_ABOVE_ENTRY,
        # Lands on 19:00Z so the daily checkpoint reads it as "in profit".
        ("2024-01-08T19:00:00Z", 1.1006, 1.1007, 1.1005, 1.1006),
    ]
    structure = STRUCTURE_FLIP + ["bearish", "bearish"]
    ctx = _ctx_with_flip(structure, prices)
    result = simulate_trade(
        ctx, 0, DIRECTION, ENTRY, SL, R_DISTANCE,
        weights=_weights_isolating("h1_internal_structure"),
        threshold=60,
        mitigation_factor_results={},
    )

    assert result["be_trigger"] == "target_ob_probability"
    assert result["be_idx"] == 2
