"""The GBP/JPY tuning study, Stages A through E, in one process.

Run:  ./.venv/bin/python scripts/research_gbpjpy.py

Why one process. Building the instrument's pipeline takes about 100
seconds and depends only on price data, never on any setting being tuned.
A full 2015-to-2025 pass over that built bundle takes about four seconds.
So the study is structured to pay the 100 seconds ONCE and then sweep
freely, rather than to shell out per stage.

What it writes, all under data/research/GBP_JPY/, never touching
data/journal, data/weights or data/settings:

  stageA/trades.csv        every candidate, unfiltered, no TP
  stageB/grid.csv          every TP family x SL buffer cell
  stageC/weights.csv       the learned weight table, frozen at end 2022
  stageD/grid.csv          the threshold search
  holdout/trades.csv       2023 to 2025 scored once with frozen settings
  settings.json            the three winning setting cards

The holdout years are not read by any stage before E. There is an
assertion enforcing that, because it is the one bug that would invalidate
the whole study and it is easy to introduce by accident.
"""

import json
import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.instruments import pip_size_for
from backtest.pipeline import build_instrument_bundle
from backtest.research import metrics, pass_runner, report, tp_models
from backtest.research.params import (
    ALL_YEARS,
    HOLDOUT_YEARS,
    HTF_THRESHOLD_GRID,
    INSTRUMENT,
    MIN_TRADES_FOR_CONSIDERATION,
    PROBABILITY_FLOOR,
    SENSIBLE_TP_FLOOR,
    SL_BUFFER_GRID,
    THRESHOLD_GRID,
    TRAIN_YEARS,
)
from backtest.settings import DEFAULT_SETTINGS
from backtest.weights import initial_weights, save_weights, update_weights

OUT_DIR = os.path.join("data", "research", INSTRUMENT)

# Stage A collects with no probability gate at all, so the give-back
# diagnostic below can see every trade the algorithm would ever have
# looked at. The one-trade-per-order-block rule still applies, because
# that is a real trading rule rather than a filter.
UNFILTERED = dict(DEFAULT_SETTINGS, tp_multiple=None)

# Everything from Stage B onward runs behind the probability floor.
#
# This is the user's hard rule and it is applied through `settings`, where
# the engine enforces it for real, rather than as a filter afterwards.
# That matters for more than tidiness: a candidate rejected on probability
# never consumes its order block, so the next candidate on that same zone
# stays eligible. Filtering after the fact would silently lose those.
QUALITY = dict(
    DEFAULT_SETTINGS,
    tp_multiple=None,
    htf_threshold=PROBABILITY_FLOOR,
    total_threshold=PROBABILITY_FLOOR,
)

OBJECTIVES = ("strike_rate", "roi_r", "expectancy_r")


def _out(*parts):
    return os.path.join(OUT_DIR, *parts)


def _pct(value):
    return "n/a" if value is None else "%.1f%%" % (100.0 * value)


def _describe(cell):
    return (
        "%-52s  SR %-7s (%d/%d)  ROI %+8.2fR  exp %+6.3fR  n=%-4d  DD %.1fR"
        % (
            cell["tp_desc"][:52],
            _pct(cell["strike_rate"]), cell["tp_hits"], cell["resolved"],
            cell["roi_r"], cell["expectancy_r"], cell["trade_count"],
            cell["max_drawdown_r"],
        )
    )


# --------------------------------------------------------------------------
# Stage A
# --------------------------------------------------------------------------

def stage_a(bundle, m15_df, pip_size):
    """Every trade 2015 to 2025, unfiltered, no take-profit.

    Run over ALL years including the holdout, because this stage only
    COLLECTS. Nothing here chooses a setting, so seeing the holdout costs
    nothing. Every stage that does choose is handed TRAIN_YEARS only.
    """
    print("\n=== Stage A: collecting every trade, unfiltered ===")
    candidates = pass_runner.run_pass(
        bundle, ALL_YEARS,
        pip_size=pip_size, weights=initial_weights(), settings=UNFILTERED,
        m15_df=m15_df, recheck=False,
        record_final_probability=True, record_min_probability=True,
    )
    rows = report.stage_a_rows(candidates, pip_size, sl_buffer_pips=2.0)
    report.save_table(rows, _out("stageA", "trades.csv"), report.STAGE_A_COLUMNS)

    took = pass_runner.taken(candidates)
    print("  %d candidates, %d taken (%d suppressed by one-trade-per-OB)"
          % (len(candidates), len(took), len(candidates) - len(took)))
    print("  wrote %s" % _out("stageA", "trades.csv"))

    quality = [
        c for c in took
        if (c["total_probability"] or -99) >= PROBABILITY_FLOOR
        and (c["htf_probability"] or -99) >= PROBABILITY_FLOOR
    ]
    print("  %d clear the %.0f%% floor on BOTH gates (%.0f%% of taken)"
          % (len(quality), PROBABILITY_FLOOR,
             100.0 * len(quality) / max(1, len(took))))
    report_giveback(quality)
    return candidates


def report_giveback(candidates):
    """How much profit the breakeven rules hand back, and where.

    This answers a specific question: if trades routinely run to some R and
    then return to breakeven, a take-profit placed just below that level
    converts them from 0R scratches into wins, lifting strike rate and
    return at the same time.

    The answer is a distribution, not a single number, and the useful part
    is its SHAPE. What matters is not the median give-back but where the
    cluster sits, because a take-profit only banks a trade whose high-water
    mark got PAST it. Placing the target above the cluster catches nothing.
    """
    gave_back = [c for c in candidates if c["max_r_to_be"] is not None]
    if not gave_back:
        print("  no trades reached breakeven, nothing to give back")
        return

    peaks = sorted(c["max_r_to_be"] for c in gave_back)
    zeroed = [c for c in gave_back if c["walk"]["terminal_reason"] == "be_stop"]
    print("\n  --- breakeven give-back ---")
    print("  %d of %d trades moved to breakeven; %d then closed at 0R"
          % (len(gave_back), len(candidates), len(zeroed)))
    print("  peak R before the stop moved: median %.2f, p25 %.2f, p75 %.2f"
          % (peaks[len(peaks) // 2], peaks[len(peaks) // 4],
             peaks[3 * len(peaks) // 4]))

    # How many of the trades that ended at 0R had already cleared each
    # level. This is the number a take-profit at that level would rescue.
    zero_peaks = sorted(c["max_r_to_be"] for c in zeroed)
    print("  scratched trades a take-profit would have banked:")
    for level in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        n = sum(1 for p in zero_peaks if p >= level)
        print("    TP %.1fR would have banked %2d of the %d scratches (%+.1fR)"
              % (level, n, len(zeroed), n * level))


# --------------------------------------------------------------------------
# Stage B
# --------------------------------------------------------------------------

def stage_b(bundle, m15_df, pip_size, label="Stage B", settings=QUALITY,
            weights=None, recheck=False):
    """Joint search over SL buffer and every take-profit family.

    The buffer is the outer loop because it is the expensive dial: it
    moves both the stop AND the pending order price, so it changes trade
    geometry and needs a genuine re-run. TP is the inner loop and costs
    nothing, because the walk carries no TP and apply_tp projects onto any
    of them exactly.

    Returns (cells, passes) where passes maps a buffer to its candidate
    list, so Stage C can learn from the winning buffer's outcomes without
    re-running it.
    """
    print("\n=== %s: SL buffer x take-profit ===" % label)
    weights = weights or initial_weights()
    cells = []
    passes = {}

    for buffer_pips in SL_BUFFER_GRID:
        candidates = pass_runner.run_pass(
            bundle, TRAIN_YEARS,
            pip_size=pip_size, weights=weights, settings=settings,
            m15_df=m15_df, sl_buffer_pips=buffer_pips, recheck=recheck,
            record_final_probability=False,
        )
        pass_runner.assert_no_holdout(candidates, "Stage B")
        passes[buffer_pips] = candidates
        took = pass_runner.taken(candidates)

        # Built once and reused for both the grid and the count. Calling
        # all_cells twice here doubled the search, which at roughly six
        # thousand cells a buffer is not a rounding error.
        buffer_cells = tp_models.all_cells(took)
        for cell in buffer_cells:
            cell["sl_buffer_pips"] = buffer_pips
        cells.extend(buffer_cells)

        print("  buffer %.1f pips: %d taken trades, %d cells"
              % (buffer_pips, len(took), len(buffer_cells)))

    report.save_cells(cells, _out("stageB", "grid.csv"))
    print("  wrote %s (%d cells)" % (_out("stageB", "grid.csv"), len(cells)))
    return cells, passes


def report_winners(cells, header):
    """The three winning cards, plus the unguarded strike-rate answer.

    The unguarded winner is printed on purpose. Maximising strike rate
    alone has a degenerate optimum -- a sub-1R target is tapped before the
    stop on almost every trade -- and hiding that would be answering a
    different question from the one asked. It is shown, then the guarded
    answer is shown next to it.
    """
    print("\n--- %s ---" % header)
    winners = {}

    raw_sr = metrics.best(cells, "strike_rate")
    if raw_sr is not None:
        print("  max SR (unguarded)   %s  [buffer %.1f]"
              % (_describe(raw_sr), raw_sr["sl_buffer_pips"]))

    for objective in OBJECTIVES:
        cell = metrics.best(
            cells, objective,
            min_trades=MIN_TRADES_FOR_CONSIDERATION,
            tp_floor=SENSIBLE_TP_FLOOR,
        )
        winners[objective] = cell
        if cell is None:
            print("  %-20s nothing cleared the minimum trade count" % objective)
            continue
        print("  %-20s %s  [buffer %.1f]"
              % ("max " + objective, _describe(cell), cell["sl_buffer_pips"]))

    # The same three objectives again, but the best cell WITHIN each TP
    # family rather than overall.
    #
    # This is not decoration. The banded families search roughly fourteen
    # thousand combinations each (three cut points x two independent R
    # grids) against the fixed family's forty-eight, so on a few hundred
    # trades a banded winner is close to guaranteed on search freedom
    # alone, whatever the market did. Seeing fixed, strength-banded,
    # sl-banded and liquidity side by side is what makes the size of the
    # banded gain judgeable rather than just its existence: a band that
    # beats the best fixed target by a hair has almost certainly found
    # nothing, and the holdout will say so.
    families = sorted({c["family"] for c in cells})
    for objective in OBJECTIVES:
        print("  by family, max %s:" % objective)
        for family in families:
            pool = [c for c in cells if c["family"] == family]
            cell = metrics.best(
                pool, objective,
                min_trades=MIN_TRADES_FOR_CONSIDERATION,
                tp_floor=SENSIBLE_TP_FLOOR,
            )
            if cell is None:
                print("    %-10s (nothing cleared the guards)" % family)
                continue
            print("    %-10s %s  [buffer %.1f]"
                  % (family, _describe(cell), cell["sl_buffer_pips"]))

    return winners


# --------------------------------------------------------------------------
# Stage C
# --------------------------------------------------------------------------

def stage_c(candidates, scored_by_idx, seed_name):
    """One pass of the existing plus/minus 2 percent rule.

    Learns over ALL candidates, taken or not, in date order. The factors
    were evaluated on the skipped bars too and the market answered them
    either way, so restricting to the taken subset would throw away most
    of the evidence. Same reasoning runner.run_year already documents.

    A candidate the winning TP family could not score (a liquidity target
    that did not exist) contributes its managed outcome instead of being
    dropped, so the weight table still sees every trade the market
    actually resolved.
    """
    print("\n=== Stage C: learning factor weights ===")
    pass_runner.assert_no_holdout(candidates, "Stage C")
    weights = initial_weights()

    ordered = sorted(
        candidates,
        key=lambda c: (c["row"]["order_placed_time"] is None,
                       c["row"]["order_placed_time"]),
    )
    learned = 0
    for c in ordered:
        scored = scored_by_idx.get(id(c))
        realised_r = scored["realised_r"] if scored else c["by_tp"][None]["realised_r"]
        # update_weights leaves the table completely untouched at exactly
        # 0R (the user's decision: a breakeven resolved nothing), so no
        # branch is needed here.
        update_weights(weights, c["factor_results"], realised_r)
        if realised_r != 0:
            learned += 1

    # save_weights does not create its parent, unlike save_table and
    # save_cells. It is called by the walk-forward scripts, which make
    # their directories up front.
    os.makedirs(_out("stageC"), exist_ok=True)
    path = _out("stageC", "weights_%s.csv" % seed_name)
    save_weights(weights, path)
    drift = sorted(weights.items(), key=lambda kv: kv[1])
    print("  learned from %d of %d candidates (%d breakevens taught nothing)"
          % (learned, len(ordered), len(ordered) - learned))
    print("  weakest: %s" % ", ".join("%s=%.3f" % (k, v) for k, v in drift[:4]))
    print("  strongest: %s" % ", ".join("%s=%.3f" % (k, v) for k, v in drift[-4:]))
    print("  wrote %s" % path)
    return weights


# --------------------------------------------------------------------------
# Stage D
# --------------------------------------------------------------------------

def stage_d(bundle, m15_df, pip_size, weights, tp_spec, buffer_pips):
    """The two thresholds, searched with the Stage C weights.

    The two cost very different amounts and are searched accordingly:

      htf_threshold    only decides whether the M15 scan runs, so with the
                       pass made ungated it applies afterwards as a plain
                       filter on the recorded htf_probability. Free, but
                       APPROXIMATE -- see below.
      total_threshold  feeds the live-probability breakeven rule, so it
                       changes outcomes and needs a re-walk. It does not
                       change trade GEOMETRY, so the walk is all that
                       repeats, not the pipeline.

    It also changes which candidate on a given order block gets taken: a
    candidate that fails the threshold does not consume its zone, so the
    next one on that zone becomes eligible. That is why the threshold
    cannot be applied as a post-hoc filter on the taken set.

    BOTH GATES GO INTO `settings` HERE, exactly as the engine applies
    them, rather than the HTF one being filtered afterwards. An earlier
    version filtered it post-hoc to save re-walks, which undercounted: the
    one-trade-per-zone rule means a candidate properly rejected on HTF
    never consumes its order block, leaving a later candidate on that zone
    eligible, and a post-hoc filter cannot recreate that. With the
    probability floor in place a pass costs under two seconds, so the
    exact nested search is affordable and the approximation is gone.

    The grid is ABSOLUTE, starting at the floor, not quantiles of the
    observed distribution. Quantiles were what produced the first run's
    nonsensical recommendation of "htf >= -48.4": on a learned weight
    table the distribution runs deep into the negatives, and its low
    quantiles are not filters at all.
    """
    print("\n=== Stage D: threshold search (floor %.0f%%, both gates) ==="
          % PROBABILITY_FLOOR)
    assign = tp_models.assign_from_spec(tp_spec)
    print("  total grid: %s" % ", ".join("%.0f" % v for v in THRESHOLD_GRID))
    print("  htf grid:   %s" % ", ".join("%.0f" % v for v in HTF_THRESHOLD_GRID))

    cells = []
    for total_t in THRESHOLD_GRID:
        for htf_t in HTF_THRESHOLD_GRID:
            settings = dict(
                QUALITY, total_threshold=total_t, htf_threshold=htf_t
            )
            candidates = pass_runner.run_pass(
                bundle, TRAIN_YEARS,
                pip_size=pip_size, weights=weights, settings=settings,
                m15_df=m15_df, sl_buffer_pips=buffer_pips, recheck=True,
                record_final_probability=False,
            )
            pass_runner.assert_no_holdout(candidates, "Stage D")
            scored = tp_models.score_all(pass_runner.taken(candidates), assign)
            if not scored:
                continue
            cell = metrics.summarise(scored)
            cell.update({
                "total_threshold": total_t,
                "htf_threshold": htf_t,
                "sl_buffer_pips": buffer_pips,
                "tp_desc": "htf>=%.0f, total>=%.0f" % (htf_t, total_t),
                "tp_spec": tp_spec,
                "family": tp_spec["family"],
                "tp_floor_ok": True,
            })
            cells.append(cell)

    # The caller writes this, under a per-seed name. Writing it here too
    # would have the second seed silently overwrite the first's grid.
    print("  %d threshold cells" % len(cells))
    return cells


# --------------------------------------------------------------------------
# Stage E
# --------------------------------------------------------------------------

def stage_e(bundle, m15_df, pip_size, weights, card, tag):
    """2023 to 2025, scored once, with everything frozen.

    This is the only number in the study that is not fitted to its own
    data, so it is the only one worth trusting as an estimate of what the
    settings would do next year.

    Both gates go into `settings` here, where the engine applies them for
    real, rather than the HTF one being filtered afterwards as Stage D's
    grid search does. That difference matters: gated properly, a candidate
    rejected on HTF never consumes its order block and a later candidate
    on the same zone stays eligible. This is the exact run.
    """
    print("\n=== Stage E: holdout %s, card %s ===" % (HOLDOUT_YEARS, tag))
    settings = dict(
        UNFILTERED,
        total_threshold=card["total_threshold"],
        htf_threshold=card["htf_threshold"],
    )
    candidates = pass_runner.run_pass(
        bundle, HOLDOUT_YEARS,
        pip_size=pip_size, weights=weights, settings=settings,
        m15_df=m15_df, sl_buffer_pips=card["sl_buffer_pips"], recheck=True,
        record_final_probability=True,
    )
    pool = pass_runner.taken(candidates)
    scored = tp_models.score_all(pool, tp_models.assign_from_spec(card["tp_spec"]))
    summary = metrics.summarise(scored)

    rows = report.final_rows(candidates, scored, card["sl_buffer_pips"])
    path = _out("holdout", "trades_%s.csv" % tag)
    report.save_table(rows, path, report.FINAL_COLUMNS)
    print("  %s" % _describe(dict(summary, tp_desc="holdout " + tag)))
    print("  wrote %s" % path)
    return summary


# --------------------------------------------------------------------------

def run_seed(bundle, m15_df, pip_size, passes, seed, seed_name):
    """Stages C, D and E for one Stage B winner.

    Stage C onward depends on which TP and buffer it is handed, because
    the weight table learns from THAT setting's win/loss pattern. So the
    whole tail of the study is run once per seed rather than once
    overall, and the seeds are chosen to bracket the user's two stated
    goals: strike rate first, return second.
    """
    buffer_pips = seed["sl_buffer_pips"]
    tp_spec = seed["tp_spec"]
    print("\n" + "=" * 74)
    print("SEED %r: buffer %.1f pips, TP %s"
          % (seed_name, buffer_pips, seed["tp_desc"]))
    print("=" * 74)

    winning_pass = passes[buffer_pips]
    assign = tp_models.assign_from_spec(tp_spec)
    scored_by_idx = {}
    for c in winning_pass:
        key = assign(c)
        if key in c["by_tp"]:
            scored_by_idx[id(c)] = c["by_tp"][key]

    weights = stage_c(winning_pass, scored_by_idx, seed_name)
    d_cells = stage_d(bundle, m15_df, pip_size, weights, tp_spec, buffer_pips)
    report.save_cells(d_cells, _out("stageD", "grid_%s.csv" % seed_name))
    d_winners = report_winners(
        d_cells, "Stage D winners, seed %r (train %d-%d)"
        % (seed_name, TRAIN_YEARS[0], TRAIN_YEARS[-1])
    )

    cards = {}
    for objective in OBJECTIVES:
        cell = d_winners.get(objective)
        if cell is None:
            continue
        card = {
            "seed": seed_name,
            "htf_threshold": cell["htf_threshold"],
            "total_threshold": cell["total_threshold"],
            "tp_spec": tp_spec,
            "tp_desc": seed["tp_desc"],
            "sl_buffer_pips": buffer_pips,
            "train": {k: cell[k] for k in (
                "strike_rate", "roi_r", "expectancy_r", "trade_count",
                "resolved", "max_drawdown_r",
            )},
        }
        # Every card gets its own holdout, not just one. A holdout run is
        # three years and a couple of seconds, and the whole point of the
        # exercise is comparing how each card survives contact with data
        # it was not fitted to.
        holdout = stage_e(
            bundle, m15_df, pip_size, weights, card,
            tag="%s_%s" % (seed_name, objective),
        )
        card["holdout"] = {k: holdout[k] for k in (
            "strike_rate", "roi_r", "expectancy_r", "trade_count",
            "resolved", "max_drawdown_r",
        )}
        cards[objective] = card

    return cards


def print_scorecard(all_cards):
    """Train against holdout for every card, side by side.

    This is the table the whole study exists to produce. A card whose
    holdout expectancy collapses toward zero found nothing; the training
    number was the search picking the luckiest cell out of ninety-five
    thousand.
    """
    print("\n" + "=" * 92)
    print("FINAL SCORECARD: train 2015-2022 vs untouched holdout 2023-2025")
    print("=" * 92)
    print("  %-26s %-28s %-28s" % ("card", "train", "holdout (never tuned on)"))
    for name, card in all_cards.items():
        tr, ho = card["train"], card["holdout"]
        print("  %-26s SR %-6s exp %+6.3fR n=%-4d  SR %-6s exp %+6.3fR n=%-4d"
              % (name,
                 _pct(tr["strike_rate"]), tr["expectancy_r"], tr["trade_count"],
                 _pct(ho["strike_rate"]), ho["expectancy_r"], ho["trade_count"]))
        print("      %s | buffer %.1f pips | htf>=%.1f | total>=%.1f"
              % (card["tp_desc"][:60], card["sl_buffer_pips"],
                 card["htf_threshold"], card["total_threshold"]))


def main():
    pip_size = pip_size_for(INSTRUMENT)
    print("Building %s pipeline (about 100 seconds, done once)..." % INSTRUMENT)
    bundle = build_instrument_bundle(INSTRUMENT)
    m15_df = pd.read_parquet(os.path.join("data", "raw", "%s_M15.parquet" % INSTRUMENT))

    stage_a(bundle, m15_df, pip_size)

    cells, passes = stage_b(bundle, m15_df, pip_size)
    winners = report_winners(cells, "Stage B winners (train %d-%d)"
                             % (TRAIN_YEARS[0], TRAIN_YEARS[-1]))

    # TWO seeds, not one.
    #
    # The plan seeded Stage C from best expectancy alone. The first full
    # run showed why that is not enough on this data: expectancy has no
    # interior maximum here, so it walks to whatever the TP grid's ceiling
    # happens to be (7.75R at a 8R ceiling, 12.00R at a 12R ceiling) and
    # rests its answer on about thirty take-profit hits. That is a search
    # artifact, not a setting.
    #
    # Strike rate is the user's stated PRIMARY goal and lands somewhere
    # completely different (a 1R target, ~57%), so running both and
    # comparing them on the holdout is the honest way to choose between
    # them rather than picking one up front.
    seeds = {}
    for name in ("strike_rate", "expectancy_r"):
        seed = winners.get(name)
        if seed is not None:
            seeds[name] = seed
    if not seeds:
        print("\nNo Stage B cell cleared the guards. Stopping.")
        return

    all_cards = {}
    for seed_name, seed in seeds.items():
        for objective, card in run_seed(
            bundle, m15_df, pip_size, passes, seed, seed_name
        ).items():
            all_cards["%s / max %s" % (seed_name, objective)] = card

    print_scorecard(all_cards)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(_out("settings.json"), "w") as f:
        json.dump(all_cards, f, indent=2, default=str)
    print("\nwrote %s" % _out("settings.json"))


if __name__ == "__main__":
    main()
