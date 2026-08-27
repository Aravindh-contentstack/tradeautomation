"""Scoring a set of trades: strike rate, ROI, expectancy, drawdown.

The strike rate here is NOT the one in runner.summarise, and the
difference is deliberate rather than an oversight.

  runner.summarise  win = realised_r > 0, loss = realised_r < 0,
                    breakevens excluded. A Friday close at +0.3R is a win.

  strike_rate here  win = the full take-profit was hit. Loss = the stop
                    was hit. EVERYTHING else, including breakeven stops,
                    19:00 cuts and Friday closes at any R, is excluded
                    from both the numerator and the denominator.

The second is the user's definition, given directly: "anything that hits
the full tp is a winner, anything that hits the SL is a loser, exclude
others apart from these entries". It answers "when the market gave a
verdict, how often was it in our favour", and time-based exits are not a
verdict, they are the clock running out.

ROI and expectancy deliberately do NOT use that filter. A breakeven stop
and a Friday close at +0.7R are real outcomes with real effects on the
account, so every taken trade counts at its actual R. Reporting a strike
rate over one population and a return over another is intentional: they
are answering different questions and averaging them together would hide
exactly the trade-off this study exists to expose.
"""

from backtest.simulate import EXIT_SL, EXIT_TP


def strike_rate(scored):
    """(rate, wins, losses) under the full-TP-or-stop definition.

    Returns a rate of None rather than 0.0 when nothing resolved, so an
    empty cell is distinguishable from a cell that resolved and lost
    everything. Ranking treats None as "no answer" and skips it.
    """
    wins = sum(1 for s in scored if s["exit_reason"] == EXIT_TP)
    losses = sum(1 for s in scored if s["exit_reason"] == EXIT_SL)
    decided = wins + losses
    if not decided:
        return None, 0, 0
    return wins / decided, wins, losses


def max_drawdown_r(scored):
    """Deepest peak-to-trough fall of the cumulative R curve.

    Sorted by exit time, not entry time: the equity curve moves when a
    trade CLOSES, and two trades opened in the same week can close a month
    apart. Ordering by entry would report a drawdown the account never
    actually sat through.
    """
    ordered = sorted(
        scored,
        key=lambda s: (s["exit_time"] is None, s["exit_time"]),
    )
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for s in ordered:
        equity += s["realised_r"]
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def summarise(scored):
    """Every headline number for one set of scored trades.

    `scored` is the output of tp_models.score_all: one dict per TAKEN
    candidate carrying realised_r, exit_reason and exit_time.
    """
    n = len(scored)
    total_r = sum(s["realised_r"] for s in scored)
    rate, wins, losses = strike_rate(scored)

    return {
        "trade_count": n,
        # Every taken trade at its real R, breakevens and time-cuts
        # included. This is the account's actual result.
        "roi_r": total_r,
        "expectancy_r": total_r / n if n else 0.0,
        # Only the trades the market gave a verdict on.
        "strike_rate": rate,
        "tp_hits": wins,
        "sl_hits": losses,
        "resolved": wins + losses,
        # The excluded middle, reported so the gap between `resolved` and
        # `trade_count` is never a mystery.
        "unresolved": n - (wins + losses),
        "max_drawdown_r": max_drawdown_r(scored),
    }


def rank_key(objective):
    """Sort key for one objective, best first.

    A cell whose strike rate is None sorts last under the strike-rate
    objective rather than raising, so a grid containing empty cells can
    still be ranked in one pass.
    """
    if objective == "strike_rate":
        return lambda s: (
            s["strike_rate"] is not None,
            s["strike_rate"] or 0.0,
            # Ties on strike rate are broken by return, so a 60% cell
            # earning 40R outranks a 60% cell earning 4R.
            s["roi_r"],
        )
    if objective == "roi_r":
        return lambda s: (s["roi_r"], s["strike_rate"] or 0.0)
    if objective == "expectancy_r":
        return lambda s: (s["expectancy_r"], s["strike_rate"] or 0.0)
    raise ValueError("unknown objective: %s" % objective)


def best(cells, objective, min_trades=0, tp_floor=None):
    """The highest-ranked cell under one objective.

    min_trades and tp_floor are the guards against fitting a
    recommendation to a handful of trades or to a degenerate sub-1R
    target. Both default to off so the same function can also report the
    unguarded winner, which the study prints alongside as the honest
    answer to what was literally asked.
    """
    pool = [c for c in cells if c["trade_count"] >= min_trades]
    if tp_floor is not None:
        pool = [c for c in pool if c.get("tp_floor_ok", True)]
    if not pool:
        return None
    return max(pool, key=rank_key(objective))
