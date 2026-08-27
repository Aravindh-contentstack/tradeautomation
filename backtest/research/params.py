"""Grids, split, and shared constants for the tuning study.

Every number a sweep iterates over lives here, so the study's search space
can be read in one place rather than reconstructed from four call sites.
"""

# --- The train / holdout split ------------------------------------------
#
# 2023 to 2025 is NOT looked at until every setting is frozen. The study
# fits four dials (TP, SL buffer, and two thresholds) on roughly 356
# training candidates, which is thin enough that some grid cells will hold
# a handful of trades. The holdout is the only thing that can tell us
# whether a winning combination found real structure or just dodged two
# losers in 2017, so it is spent once, at the end, and never re-entered.
TRAIN_YEARS = tuple(range(2015, 2023))
HOLDOUT_YEARS = (2023, 2024, 2025)
ALL_YEARS = TRAIN_YEARS + HOLDOUT_YEARS

INSTRUMENT = "GBP_JPY"

# The pairs actually traded. NAS100 is absent on purpose: it is index and
# point based rather than pip based, so a pip buffer means nothing on it.
#
# Each pair keeps its OWN learned weight table (the user's decision). The
# alternative, one pooled table, would have roughly ten times the evidence
# behind it, but it assumes every pair rewards the same confluence, and
# that is exactly the assumption the user does not want baked in.
PORTFOLIO = (
    "AUD_USD", "EUR_JPY", "EUR_USD", "GBP_JPY", "GBP_USD",
    "NZD_USD", "USD_CAD", "USD_CHF", "USD_JPY", "XAU_USD",
)


# --- Stop-loss buffer sweep ---------------------------------------------
#
# Pips beyond the zone edge. 2.0 is today's shipped value and is kept in
# the grid so the sweep always contains the incumbent to beat.
#
# Widening this is NOT free, and the sweep exists to price that. The
# buffer moves the pending ORDER as well as the stop on the two LC models
# (entry_models._order_prices), so a wider buffer parks the order further
# from price and it gets filled less often: measured on 2015 to 2022,
# 356 candidates at 2.0 pips falls to 293 at 4.0 and 222 at 8.0. A wider
# stop that survives more noise is being paid for in trades that never
# happen, and only a joint search can say whether that trade is worth it.
SL_BUFFER_GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)


# --- Take-profit grid ----------------------------------------------------
#
# The dense grid every walk timestamps on the way past. Passing all of it
# to simulate_trade costs one comparison per level per bar and buys exact
# fill TIMES for every TP family, instead of apply_tp falling back to the
# terminal bar as an upper bound.
TP_GRID_STEP = 0.25
TP_GRID_MIN = 0.25
# Raised from 8.0 after the first full run put both the best-ROI and the
# best-expectancy targets at 7.50R and 7.75R, i.e. hard against the old
# ceiling. A winner sitting on the edge of its own grid is a truncation
# artifact until proven otherwise, so the grid has to extend past where
# the optimum wants to sit.
TP_GRID_MAX = 12.0

DENSE_TP_LEVELS = tuple(
    round(TP_GRID_MIN + i * TP_GRID_STEP, 2)
    for i in range(int(round((TP_GRID_MAX - TP_GRID_MIN) / TP_GRID_STEP)) + 1)
)

# Reporting clamp for research walks, well above simulate.MAX_R_CEILING.
#
# The default 10.0 is right for the journal, where anything past 10R is
# noise in a column nobody sizes off. It is wrong here: a liquidity-target
# TP can legitimately sit 15 or 20R away, and clipping max_r_reached at 10
# would silently score those as "never reached" and make that whole TP
# family look worse than it is.
RESEARCH_MAX_R_CEILING = 50.0


# --- Threshold search ----------------------------------------------------
#
# Quantiles of the OBSERVED probability distribution, not absolute numbers.
# The probability scale is normalised over the factors actually evaluated
# for each candidate (backtest/factors.py), so a fixed 40-to-80 grid can
# land entirely above the population and report "nothing qualified" rather
# than an honest answer. analysis.py learned this the hard way and its
# THRESHOLD_QUANTILES exist for the same reason.
THRESHOLD_QUANTILES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
HTF_THRESHOLD_QUANTILES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)

# --- The hard probability floor -----------------------------------------
#
# A trade scoring below this is NOT TRADEABLE, full stop. This is the
# user's rule, not something for the search to discover: a negative or
# near-zero probability means the confluence disagrees with the trade, and
# no amount of favourable return distribution makes that a trade worth
# placing. Quality over quantity, explicitly, and the trade count is
# expected to fall.
#
# It replaces the quantile grids above for any run that respects it. Those
# grids derive their cut points from the observed distribution, which on a
# learned weight table runs well into the negatives -- the first full run
# recommended thresholds of -48.4 and -36.6, which are not filters at all.
# An absolute floor is the whole point here.
PROBABILITY_FLOOR = 25.0

# Searched above the floor, on BOTH gates (the user's decision: HTF
# strength and Trade strength must each clear it).
#
# Stops at 60 because the scale does not reach 100 in practice. With every
# factor weight at 1.0, GBP_JPY's observed trade strength tops out at
# 66.7, and clearing 50 on both gates already leaves only 14 trades in
# eleven years. Grid points above that would be reporting noise.
THRESHOLD_GRID = (25.0, 30.0, 35.0, 40.0, 45.0, 50.0)
HTF_THRESHOLD_GRID = (25.0, 30.0, 35.0, 40.0, 45.0)

# Below this many resolved trades a cell is reported but never recommended.
# Matches analysis.MIN_TRADES_FOR_CONSIDERATION, which was raised to 8
# after the search fitted a whole year's settings to five trades.
MIN_TRADES_FOR_CONSIDERATION = 8

# Strike rate is the user's primary goal, and on its own it has a
# degenerate optimum: a 0.25R target is tapped before the stop on almost
# every trade, so it scores ~90% with poor returns. That is arithmetic,
# not a bug, so it is reported honestly rather than suppressed. This floor
# only bounds the "sensible" column reported alongside it.
SENSIBLE_TP_FLOOR = 1.0


def quantiles(values, qs):
    """Sorted distinct quantile cut points of `values`.

    Nearest-rank rather than interpolated: every cut point is then a value
    the population actually took, so a threshold reported as 43.7 is a
    score some real candidate scored, not an average of two that did not.
    """
    pool = sorted(float(v) for v in values if v is not None)
    if not pool:
        return []
    out = []
    for q in qs:
        idx = min(len(pool) - 1, max(0, int(round(q * (len(pool) - 1)))))
        out.append(pool[idx])
    return sorted(set(out))
