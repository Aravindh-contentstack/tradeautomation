"""Global settings search: grid-searches probability threshold, TP
multiple, and max stop loss size against the FULL combined journal (not
per-year, evolving forward), picking the combination that maximizes total
R-multiple return (a stand-in for ROI, since position sizing isn't
modeled), using strike rate as the tie-breaker.

A prior version of this search maximized strike rate first. It was
abandoned because the achievable strike rate ceiling looked like only
about 50-53% at TP 1.0R even at maximum selectivity, which a
strike-rate-first objective can never turn into a 60% target, so per the
user's decision this search optimizes ROI and reports whatever strike
rate comes with the best-ROI combination.

*** That 50-53% ceiling is DUE FOR RE-MEASUREMENT. *** It was measured
through the old `_classify`, whose two branches were textually identical,
so it reduced to `max_r_reached >= tp_multiple` and counted stopped-out
trades as wins. It was also measured before `max_r_reached` was redefined
to exclude the terminal bar's favourable move. Both defects inflated the
number. Treat the figure above as history, not as a live finding, until
it has been re-derived from a post-fix journal.

No re-simulation is needed per TP candidate. The forward walk in
backtest/simulate.py carries no TP at all: trade management (stop, BE
trigger, 19:00 cut, Friday close) is a function of price and clock only,
so one TP-free walk traces the unique path every TP variant shares and
`_realised_r` below is an exact projection onto any TP. That is what lets
this module grid-search TP multiples for free.

THE RATCHET TRAP. `candidates` must be EVERY gate-passing candidate of
the year, including the ones the prior year's threshold rejected. If this
search is handed a pre-filtered pool it can only ever recommend a higher
threshold and a tighter SL cap, so thresholds ratchet monotonically until
the pool starves (XAU_USD 2025 has only 2 candidates at probability 40 or
above, out of 39). The `taken` flag is for reporting P&L, never for
building the pool this function searches.
"""

TP_MULTIPLE_GRID = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
SL_SIZE_QUANTILES = [0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]

# Thresholds are derived from the year's own probability distribution,
# the same way the max-SL grid is derived from observed stop sizes.
#
# The fixed 40-to-80 grid this replaces was calibrated against a scale
# that no longer exists. Probability is now normalised over the factors
# actually evaluated (backtest/factors.py), so a trade where only a
# couple of gates had anything to say scores on a different effective
# scale from one where all three timeframes spoke, and the observed range
# runs well below 40 and can go negative. A fixed absolute grid on a
# rescaled metric is not a conservative choice, it is a silent
# mis-search: every grid point can land above the whole population, and
# the search then reports "no combination cleared the minimum trade
# count" rather than an honest recommendation.
THRESHOLD_QUANTILES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

# Raised from 5. Not a performance fix (measured -0.0179 to -0.0171
# R/trade), but it stops the search fitting a whole year's settings to 5
# trades, which is exactly what it did for GBP_USD 2024 and XAU_USD 2024.
MIN_TRADES_FOR_CONSIDERATION = 8


def _threshold_grid(candidates):
    """Probability thresholds drawn from the candidates' own quantiles.

    Deduplicated and rounded, so a year whose probabilities cluster
    tightly produces a short grid rather than ten near-identical cut
    points that all admit the same pool.
    """
    values = sorted(c["probability"] for c in candidates)
    n = len(values)
    if n == 0:
        return []
    thresholds = {round(values[min(int(q * (n - 1)), n - 1)], 2) for q in THRESHOLD_QUANTILES}
    return sorted(thresholds)


def _max_sl_size_pips_grid(candidates, pip_size):
    """Max stop loss size candidates, in pips, derived from quantiles of
    the observed sl_size (raw price units) across every candidate, so the
    grid always matches the instrument's actual H1 fractal range instead
    of an arbitrary fixed pip scale.
    """
    sizes = sorted(c["sl_size"] for c in candidates)
    n = len(sizes)
    pips = set()
    for q in SL_SIZE_QUANTILES:
        i = min(int(q * (n - 1)), n - 1)
        pips.add(round(sizes[i] / pip_size, 1))
    return sorted(pips)


def _realised_r(c, tp_multiple):
    """What this candidate would have returned at this TP multiple.

    Exact, not an approximation. `max_r_reached` is the maximum favourable
    excursion STRICTLY BEFORE the terminal event, so if it reached the TP
    the trade closed at the TP and nothing that happened afterwards
    matters; if it did not, the TP was never in play and the trade closed
    at whatever the TP-free walk terminated on (stop at -1R, breakeven
    stop at 0R, a 19:00 loss cut at some fraction, or the Friday deadline
    at any sign).

    This replaces `_classify` plus `_total_r`. Those assumed every outcome
    was either +tp_multiple or -1.0, which cannot express a 0R breakeven
    stop or a fractional 19:00 cut.
    """
    return tp_multiple if c["max_r_reached"] >= tp_multiple else c["terminal_r"]


def _score(candidates, tp_multiple):
    """Total R, strike rate, and breakeven count for a pool at one TP.

    Breakevens are EXCLUDED FROM THE STRIKE RATE DENOMINATOR. They are
    neither wins nor losses: the trade was scratched at entry price and
    resolved nothing. Counting them as losses would let a strategy that
    moves everything to breakeven report a collapsing strike rate on flat
    ROI, which makes the tie-breaker meaningless precisely when it is
    being used. avg_r is still averaged over ALL trades, breakevens
    included, because a scratch really does dilute return per trade.
    """
    total_r = 0.0
    wins = 0
    losses = 0
    breakevens = 0

    for c in candidates:
        r = _realised_r(c, tp_multiple)
        total_r += r
        if r > 0:
            wins += 1
        elif r < 0:
            losses += 1
        else:
            breakevens += 1

    decided = wins + losses
    strike_rate = wins / decided if decided else 0.0
    avg_r = total_r / len(candidates) if candidates else 0.0
    return total_r, strike_rate, avg_r, breakevens


def recommend_global_settings(candidates, pip_size):
    """Picks the (threshold, tp_multiple, max_sl_size_pips) combination
    maximizing total R first, strike rate as the tie-breaker. Returns a
    settings dict with the resulting roi_r, strike_rate, avg_r and
    breakeven_count, or None if no combination has enough trades to be
    meaningful.

    Pass ALL of the year's candidates here, taken and untaken alike (see
    the ratchet trap in the module docstring).
    """
    if not candidates:
        return None

    best = None
    threshold_grid = _threshold_grid(candidates)
    for max_sl_size_pips in _max_sl_size_pips_grid(candidates, pip_size):
        max_sl_size_price = max_sl_size_pips * pip_size
        pool_by_sl = [c for c in candidates if c["sl_size"] <= max_sl_size_price]

        for threshold in threshold_grid:
            pool_by_threshold = [
                c for c in pool_by_sl if c["probability"] >= threshold
            ]
            if len(pool_by_threshold) < MIN_TRADES_FOR_CONSIDERATION:
                continue

            for tp_multiple in TP_MULTIPLE_GRID:
                total_r, strike_rate, avg_r, breakevens = _score(
                    pool_by_threshold, tp_multiple
                )

                candidate_settings = {
                    "threshold": threshold,
                    "tp_multiple": tp_multiple,
                    "max_sl_size_pips": max_sl_size_pips,
                    "roi_r": total_r,
                    "strike_rate": strike_rate,
                    "avg_r": avg_r,
                    "breakeven_count": breakevens,
                    "trade_count": len(pool_by_threshold),
                }

                if best is None:
                    best = candidate_settings
                    continue

                if candidate_settings["roi_r"] > best["roi_r"]:
                    best = candidate_settings
                elif (
                    candidate_settings["roi_r"] == best["roi_r"]
                    and candidate_settings["strike_rate"] > best["strike_rate"]
                ):
                    best = candidate_settings

    return best
