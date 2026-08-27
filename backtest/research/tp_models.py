"""The take-profit families the study searches, and the search itself.

Four families, per the user's answer on what "variable" is allowed to
mean. Each is a rule that reads one candidate and returns the R multiple
that candidate should have targeted:

  fixed        one multiple for every trade.
  by_strength  bands on total_probability. A stronger read targets
               further, a weaker one nearer -- or the reverse, if that is
               what the data says. The search does not assume a
               direction, it tries both and reports which won.
  by_sl_size   bands on stop size in pips, same freedom.
  liquidity    the nearest live unswept liquidity level ahead of price at
               the entry bar, expressed in R. Differs on every trade by
               construction.

Why this is cheap
-----------------
Nothing here re-simulates. pass_runner has already scored each candidate
at every level of the dense grid and stored the answers in `by_tp`, so a
family is evaluated by table lookup. That is what makes a two-band search
over 32 x 32 R combinations at four split points affordable: the
expensive part (the walk) happened once.
"""

from backtest.research import metrics
from backtest.research.params import DENSE_TP_LEVELS, SENSIBLE_TP_FLOOR

LIQUIDITY_KEY = "liquidity"


def _band_cuts(values, n_cuts=3):
    """Interior split points for a banded family.

    Quantile-based so each band holds a comparable number of trades. A
    split at a round number like "40 pips" sounds tidier but can put 90%
    of the population on one side, and a band holding six trades is
    fitted, not measured.
    """
    pool = sorted(v for v in values if v is not None)
    if len(pool) < 2 * n_cuts:
        return []
    cuts = []
    for i in range(1, n_cuts + 1):
        idx = int(round(i * len(pool) / (n_cuts + 1)))
        idx = min(len(pool) - 1, max(0, idx))
        cuts.append(pool[idx])
    return sorted(set(cuts))


def score_all(candidates, assign):
    """Score every candidate under a TP assignment rule.

    `assign` maps a candidate to a key in its `by_tp` table: either an R
    multiple from the dense grid, or LIQUIDITY_KEY, or None for "no take
    profit, run to the terminal event". A candidate whose assigned key is
    missing is skipped rather than guessed at, which is how a liquidity
    family handles a trade that had no level ahead of it.
    """
    out = []
    for c in candidates:
        key = assign(c)
        scored = c["by_tp"].get(key)
        if scored is None:
            continue
        out.append(scored)
    return out


def fixed_cells(candidates, levels=DENSE_TP_LEVELS):
    """One cell per fixed R multiple."""
    cells = []
    for tp in levels:
        scored = score_all(candidates, lambda c, tp=tp: tp)
        if not scored:
            continue
        cell = metrics.summarise(scored)
        cell.update({
            "family": "fixed",
            "tp_desc": "%.2fR" % tp,
            "tp_spec": {"family": "fixed", "tp": tp},
            "tp_floor_ok": tp >= SENSIBLE_TP_FLOOR,
        })
        cells.append(cell)
    return cells


def no_tp_cell(candidates):
    """The limiting case: no target at all, every trade run to its
    terminal event (breakeven stop, 19:00 cut, Friday close, or stop).

    This exists because the TP search kept walking to the edge of its own
    grid. Raised from 8R to 12R, the best-expectancy target moved from
    7.75R to 12.00R -- it was chasing the ceiling, not finding an optimum.
    A handful of very large winners (max R reaches 41 on this book) pull
    expectancy up faster than the extra unresolved trades pull it down, so
    on this data expectancy has no interior maximum to find.

    Including the TP-free limit makes that visible instead of leaving it
    looking like a grid artifact: if "no target" scores near the top, the
    honest reading is that expectancy alone does not choose a take-profit
    on this sample, and the choice has to come from strike rate or
    drawdown, which is what the user asked to optimise anyway.

    Its strike rate is 0% by construction, and that is not a bug: with no
    target, no trade can ever hit one, so the "full TP or the stop"
    definition has only losses to count.
    """
    scored = score_all(candidates, lambda c: None)
    if not scored:
        return []
    cell = metrics.summarise(scored)
    cell.update({
        "family": "no_tp",
        "tp_desc": "no take profit (run to terminal event)",
        "tp_spec": {"family": "no_tp"},
        # Never a recommendation, always a reference point.
        "tp_floor_ok": False,
    })
    return [cell]


def liquidity_cells(candidates):
    """The single cell for targeting the next liquidity level.

    Reports how many candidates had no level ahead of them and so could
    not be traded by this rule at all. That count matters: a family that
    only fires on half the book is not comparable to one that fires on all
    of it, and burying it inside a strike rate would make it look better
    than it is.
    """
    scored = score_all(candidates, lambda c: LIQUIDITY_KEY)
    if not scored:
        return []
    cell = metrics.summarise(scored)

    # The median of the targets ACTUALLY USED, not of every candidate:
    # a candidate with no level ahead of it is not traded by this family
    # and has no business in its summary statistic.
    rs = sorted(
        c["liq_target_r"] for c in candidates
        if c.get("liq_target_r") is not None
    )
    median = rs[len(rs) // 2] if rs else 0.0

    cell.update({
        "family": "liquidity",
        "tp_desc": "next liquidity level (median %.2fR, %d of %d candidates)"
                   % (median, len(scored), len(candidates)),
        "tp_spec": {"family": "liquidity"},
        "skipped_no_target": len(candidates) - len(scored),
        "median_target_r": median,
        # This family is NOT automatically exempt from the floor, which is
        # what an earlier version of this assumed. Measured on GBP_JPY at
        # an 8-pip buffer the median target is 0.83R, so the family was
        # scoring a 60% strike rate by the same mechanism the floor exists
        # to catch: a near target gets tapped before the stop. Judging it
        # by its median keeps it in the comparison honestly instead of
        # letting it in through the side door.
        "tp_floor_ok": median >= SENSIBLE_TP_FLOOR,
    })
    return [cell]


def banded_cells(candidates, field, label, levels=DENSE_TP_LEVELS):
    """Two-band families over one candidate field.

    Both bands search the full R grid independently, so "weaker reads
    target nearer" and "weaker reads target further" are both in the
    space. The study reports whichever won rather than assuming the
    intuitive direction holds.
    """
    values = [c.get(field) for c in candidates]
    cuts = _band_cuts(values)
    cells = []
    for cut in cuts:
        for lo in levels:
            for hi in levels:
                if lo == hi:
                    # Identical bands are just the fixed family, already
                    # searched. Skipping them keeps the banded winner
                    # honestly banded.
                    continue

                def assign(c, cut=cut, lo=lo, hi=hi):
                    v = c.get(field)
                    if v is None:
                        return None
                    return lo if v < cut else hi

                scored = score_all(candidates, assign)
                if not scored:
                    continue
                cell = metrics.summarise(scored)
                cell.update({
                    "family": label,
                    "tp_desc": "%.2fR below %s %.2f, %.2fR at or above"
                               % (lo, label, cut, hi),
                    "tp_spec": {
                        "family": label, "field": field,
                        "cut": cut, "low": lo, "high": hi,
                    },
                    "tp_floor_ok": min(lo, hi) >= SENSIBLE_TP_FLOOR,
                })
                cells.append(cell)
    return cells


def all_cells(candidates):
    """Every TP family's cells, ready to rank."""
    cells = []
    cells.extend(fixed_cells(candidates))
    cells.extend(no_tp_cell(candidates))
    cells.extend(liquidity_cells(candidates))
    cells.extend(banded_cells(candidates, "total_probability", "strength"))
    cells.extend(banded_cells(candidates, "sl_pips", "sl_size"))
    return cells


def assign_from_spec(spec):
    """Rebuild an assignment callable from a stored tp_spec.

    Specs are plain dicts so a winning setting survives being written to
    JSON and read back by the holdout run, which is the whole point of
    freezing it.
    """
    family = spec["family"]
    if family == "fixed":
        return lambda c: spec["tp"]
    if family == "no_tp":
        return lambda c: None
    if family == "liquidity":
        return lambda c: LIQUIDITY_KEY
    field, cut, lo, hi = spec["field"], spec["cut"], spec["low"], spec["high"]

    def assign(c):
        v = c.get(field)
        if v is None:
            return None
        return lo if v < cut else hi

    return assign
