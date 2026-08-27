"""One full sweep of the strategy over a set of years, scored at every TP.

This is the study's unit of work. It does what runner.run_year does, with
three deliberate differences:

  1. It scores EVERY take-profit on the dense grid in one go, not one.
     The walk carries no TP at all (see backtest/simulate.py), so a single
     walk is the exact common path of every TP variant and apply_tp
     projects onto each for free. That asymmetry is the whole reason the
     study is affordable: the SL buffer needs a genuine re-run per value
     because it moves trade geometry, while TP costs nothing.
  2. It does not learn weights as it goes. Stage C does that in one pass
     afterwards, over the outcomes the tuned settings actually produced.
  3. It records final_probability and min_live_probability, which the
     walk-forward journal has no use for.

Runtime, measured on GBP_JPY: the pipeline build is about 100 seconds and
is done ONCE by the caller, then a full 2015-to-2025 pass is about four
seconds. Every sweep in the study is built on that ratio.
"""

import numpy as np

from backtest import entry_params
from backtest.journal import build_row
from backtest.research.params import (
    DENSE_TP_LEVELS,
    HOLDOUT_YEARS,
    RESEARCH_MAX_R_CEILING,
)
from backtest.research.tp_models import LIQUIDITY_KEY
from backtest.runner import build_year_context
from backtest.settings import apply_settings
from backtest.simulate import apply_tp, find_signals, simulate_trade


def liquidity_target_r(ctx, idx, direction, entry_price, r_distance):
    """The nearest live unswept liquidity level ahead of price, in R.

    Reads the same target arrays the Liquidity Target factor gate scores
    off (smc/liquidity/liq_state._build_targets), so "the next draw on
    liquidity" means one thing in this study and in the probability that
    admitted the trade. Every timeframe and kind is considered and the
    nearest qualifying level wins.

    Returns None when there is nothing ahead. That is a real answer, not a
    failure: a trade with no liquidity above it cannot be traded by this
    TP family, and tp_models reports how often that happened rather than
    substituting a fixed target and quietly changing the family.
    """
    liq = getattr(ctx, "liq", None)
    if liq is None:
        return None

    table = liq.target_above if direction == "bullish" else liq.target_below
    best = None
    for values in table.values():
        if values is None or idx >= len(values):
            continue
        price = values[idx]
        if price is None or not np.isfinite(price):
            continue
        # Measured from the ENTRY price, not the bar close the target
        # arrays were built against. A level already behind the entry is
        # not a target, it is history.
        distance = (price - entry_price) if direction == "bullish" else (entry_price - price)
        if distance <= 0:
            continue
        if best is None or distance < best:
            best = distance

    if best is None:
        return None
    return best / r_distance


def _score_every_tp(walk, signal, levels, liq_r, cand_idx):
    """Project one walk onto every TP on the grid, plus its own liquidity
    target if it has one.

    The liquidity level is scored under its own key rather than snapped to
    the nearest grid point. Snapping would round a 12.5R target down to
    8R, which is not the same trade, and the grid stops at 8R precisely
    because fixed targets beyond that are not sensible while a liquidity
    draw beyond it genuinely can be.
    """
    entry_price = signal["entry_price"]
    direction = signal["direction"]
    r_distance = signal["r_distance"]

    by_tp = {}
    for tp in levels:
        result = apply_tp(walk, tp, entry_price, direction, r_distance)
        result["cand_idx"] = cand_idx
        by_tp[tp] = result

    if liq_r is not None:
        result = apply_tp(walk, liq_r, entry_price, direction, r_distance)
        result["cand_idx"] = cand_idx
        result["tp_multiple"] = liq_r
        by_tp[LIQUIDITY_KEY] = result

    # "No take profit at all", i.e. managed purely to the terminal event.
    # Kept because it is the honest baseline every TP family has to beat.
    result = apply_tp(walk, None, entry_price, direction, r_distance)
    result["cand_idx"] = cand_idx
    by_tp[None] = result

    return by_tp


def run_pass(bundle, years, *, pip_size, weights, settings, m15_df=None,
             sl_buffer_pips=None, levels=DENSE_TP_LEVELS,
             record_final_probability=True, record_min_probability=False,
             recheck=True, recheck_all=False):
    """Walk `years` once and score every candidate at every TP.

    `recheck` controls the live-probability breakeven rule. It is turned
    OFF for the unfiltered stages, and the reason is not performance.
    That rule moves the stop to entry when the live score drops below the
    threshold that admitted the trade, and an unfiltered run has no
    threshold, so there is nothing for it to fail against. Feeding it the
    entry probability instead would send nearly every trade to breakeven
    on its first bar, which is the exact failure runner.run_year already
    documents for untaken candidates.

    `record_min_probability` is how the rule stays measurable while turned
    off: it tracks the lowest live score each trade reached while still at
    risk, without acting on it. It is off by default because it costs a
    factor evaluation per bar of every trade, which is worth paying once
    in Stage A and not seven times over in the buffer sweep.
    """
    override = {} if sl_buffer_pips is None else {"sl_buffer_pips": sl_buffer_pips}

    candidates = []
    with entry_params.override(**override):
        for year in years:
            ctx, year_end = build_year_context(
                bundle.df, year, pip_size=pip_size, m15_df=m15_df,
                m15_bundle=bundle.m15, obs=bundle.obs, liq=bundle.liq,
            )
            if ctx is None:
                continue

            signals = [
                s for s in find_signals(
                    ctx, weights, pip_size,
                    htf_threshold=settings.get("htf_threshold"),
                )
                if s["entry_time"] < year_end
            ]
            apply_settings(signals, settings, pip_size)

            for signal in signals:
                liq_r = liquidity_target_r(
                    ctx, signal["idx"], signal["direction"],
                    signal["entry_price"], signal["r_distance"],
                )

                # Levels handed to the walk so apply_tp can report the
                # real fill bar for each, rather than falling back to the
                # terminal bar as an upper bound.
                walk_levels = list(levels)
                if liq_r is not None:
                    walk_levels.append(liq_r)

                threshold = settings.get("total_threshold")
                recheck_kwargs = {}
                if recheck:
                    # `recheck_all` arms the rule for every candidate that
                    # CLEARS the threshold, rather than only those the
                    # one-trade-per-order-block rule left standing.
                    #
                    # The explorer needs this. It re-derives which trades
                    # are taken in the browser, AFTER an HTF filter the
                    # precompute did not apply, and dropping a candidate on
                    # HTF frees its order block for a later one. A
                    # candidate stored as untaken here can therefore become
                    # taken there, and if it was walked without the recheck
                    # its outcome would be wrong.
                    #
                    # It stays off by default because for the study the
                    # narrower rule is the correct one: rescoring a
                    # candidate the threshold just rejected sends it to
                    # breakeven on its first bar (see runner.run_year).
                    if recheck_all:
                        armed = (
                            threshold is None
                            or (signal.get("total_probability") or 0.0) >= threshold
                        )
                    else:
                        armed = signal["taken"]
                    if armed:
                        recheck_kwargs = dict(threshold=threshold)

                walk = simulate_trade(
                    ctx,
                    signal["idx"],
                    signal["direction"],
                    signal["entry_price"],
                    signal["sl"],
                    signal["r_distance"],
                    tp_levels=walk_levels,
                    weights=weights,
                    mitigation_factor_results=signal["mitigation_factor_results"],
                    entry_zone=signal.get("entry_zone"),
                    entry_setup=signal.get("entry_setup"),
                    max_r_ceiling=RESEARCH_MAX_R_CEILING,
                    record_final_probability=record_final_probability,
                    record_min_probability=record_min_probability,
                    **recheck_kwargs,
                )

                cand_idx = len(candidates)
                # TP None: the base row describes the trade as MANAGED,
                # with no target. Every TP-dependent field is filled in
                # per-TP by _score_every_tp instead.
                base = build_row(signal, walk, apply_tp(
                    walk, None, signal["entry_price"], signal["direction"],
                    signal["r_distance"],
                ), settings)

                candidates.append({
                    "year": year,
                    "taken": bool(signal["taken"]),
                    # Which order block this candidate came from. Carried
                    # so the one-trade-per-order-block rule can be re-run
                    # downstream against a differently filtered pool --
                    # the explorer does exactly that in the browser.
                    "ob_row": signal.get("ob_row"),
                    "row": base,
                    "walk": walk,
                    "factor_results": signal["factor_results"],
                    "htf_probability": signal.get("htf_probability"),
                    "total_probability": signal.get("total_probability"),
                    "sl_pips": signal["r_distance"] / pip_size,
                    "liq_target_r": liq_r,
                    "entry_model": signal.get("entry_model"),
                    "max_r_to_be": walk.get("max_r_to_be"),
                    "final_probability": walk.get("final_probability"),
                    "min_live_probability": walk.get("min_live_probability"),
                    "by_tp": _score_every_tp(
                        walk, signal, levels, liq_r, cand_idx
                    ),
                })

    return candidates


def assert_no_holdout(candidates, stage):
    """Guard that a tuning stage never saw the held-out years.

    This is the one bug that would invalidate the entire study while
    leaving every number looking plausible, and it is a single wrong
    constant away at all times. Cheap to check, so it is checked on every
    stage rather than trusted to the call sites being right.
    """
    leaked = sorted({c["year"] for c in candidates} & set(HOLDOUT_YEARS))
    if leaked:
        raise AssertionError(
            "%s saw held-out years %s. Every setting it produces is fitted "
            "to data the holdout was supposed to keep clean."
            % (stage, leaked)
        )


def taken(candidates):
    """The subset whose P&L counts.

    Untaken candidates are kept by run_pass because Stage C learns from
    them: the factors were evaluated on those bars too and the market
    answered them either way. Only reporting and the settings search
    restrict to this subset.
    """
    return [c for c in candidates if c["taken"]]
