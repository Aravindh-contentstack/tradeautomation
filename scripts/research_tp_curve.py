"""The take-profit trade-off curve, on training AND held-out years.

Run:  ./.venv/bin/python scripts/research_tp_curve.py

Why this exists. The main study reports the single best cell per
objective, and on this data those winners disagree violently: maximising
strike rate says "1R target, 8-pip buffer" and maximising return says
"about 10R, 1-pip buffer". A single winning row cannot show WHY, and the
choice between them is the user's to make, not the search's.

So this prints the whole curve instead: for one fixed set of thresholds
and buffer, what every take-profit level from 0.25R to 12R would have
returned, with the training years and the untouched holdout years side by
side.

READ THE TWO COLUMNS TOGETHER. A TP whose training and holdout rows agree
has found something about the market. A TP that looks wonderful on the
training years and collapses on the holdout has found something about
2015 to 2022, and the study's whole train/holdout split exists to tell
those apart.

A WARNING ABOUT USING THIS TO CHOOSE. The holdout column is reported so
the SHAPE of the curve can be checked for agreement. Picking the take
profit that maximises the holdout column would spend the one clean sample
there is and leave nothing to check the choice against. Choose on the
training column and on drawdown tolerance; read the holdout column only
to confirm the choice was not luck.
"""

import json
import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.instruments import pip_size_for
from backtest.pipeline import build_instrument_bundle
from backtest.research import metrics, pass_runner, tp_models
from backtest.research.params import (
    DENSE_TP_LEVELS,
    HOLDOUT_YEARS,
    INSTRUMENT,
    TRAIN_YEARS,
)
from backtest.settings import DEFAULT_SETTINGS
from backtest.weights import load_weights

OUT_DIR = os.path.join("data", "research", INSTRUMENT)

# The card being examined: the one configuration in the study whose
# training and holdout numbers agreed (expectancy seed, max-ROI
# thresholds). Its thresholds are the loosest in the grid, which is
# almost certainly why it survived: it has the fewest fitted degrees of
# freedom of any card tested.
CARD = "expectancy_r / max roi_r"


def curve_for(candidates, levels):
    rows = {}
    for tp in levels:
        scored = tp_models.score_all(candidates, lambda c, tp=tp: tp)
        if scored:
            rows[tp] = metrics.summarise(scored)
    return rows


def main():
    with open(os.path.join(OUT_DIR, "settings.json")) as f:
        card = json.load(f)[CARD]

    pip_size = pip_size_for(INSTRUMENT)
    weights = load_weights(
        os.path.join(OUT_DIR, "stageC", "weights_%s.csv" % card["seed"])
    )
    settings = dict(
        DEFAULT_SETTINGS,
        tp_multiple=None,
        total_threshold=float(card["total_threshold"]),
        htf_threshold=float(card["htf_threshold"]),
    )
    buffer_pips = float(card["sl_buffer_pips"])

    print("Building %s pipeline (about 100 seconds)..." % INSTRUMENT)
    bundle = build_instrument_bundle(INSTRUMENT)
    m15_df = pd.read_parquet(
        os.path.join("data", "raw", "%s_M15.parquet" % INSTRUMENT)
    )

    print("\nCard: %s" % CARD)
    print("  buffer %.1f pips | htf >= %.1f | total >= %.1f"
          % (buffer_pips, settings["htf_threshold"], settings["total_threshold"]))

    curves = {}
    for label, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        candidates = pass_runner.run_pass(
            bundle, years,
            pip_size=pip_size, weights=weights, settings=settings,
            m15_df=m15_df, sl_buffer_pips=buffer_pips, recheck=True,
            record_final_probability=False,
        )
        curves[label] = curve_for(pass_runner.taken(candidates), DENSE_TP_LEVELS)

    print("\n%-7s | %-34s | %-34s" % ("", "TRAIN 2015-2022", "HOLDOUT 2023-2025"))
    print("%-7s | %-8s %-8s %-7s %-7s | %-8s %-8s %-7s %-7s"
          % ("TP", "strike", "exp R", "total", "maxDD", "strike", "exp R", "total", "maxDD"))
    print("-" * 82)

    export = []
    for tp in DENSE_TP_LEVELS:
        tr = curves["train"].get(tp)
        ho = curves["holdout"].get(tp)
        if tr is None or ho is None:
            continue

        def fmt(s):
            rate = "n/a" if s["strike_rate"] is None else "%.1f%%" % (100 * s["strike_rate"])
            return "%-8s %+8.3f %+7.1f %7.1f" % (
                rate, s["expectancy_r"], s["roi_r"], s["max_drawdown_r"]
            )

        print("%-7s | %s | %s" % ("%.2fR" % tp, fmt(tr), fmt(ho)))
        export.append({
            "tp": tp,
            "train_strike_rate": tr["strike_rate"],
            "train_expectancy_r": tr["expectancy_r"],
            "train_roi_r": tr["roi_r"],
            "train_max_drawdown_r": tr["max_drawdown_r"],
            "train_trades": tr["trade_count"],
            "holdout_strike_rate": ho["strike_rate"],
            "holdout_expectancy_r": ho["expectancy_r"],
            "holdout_roi_r": ho["roi_r"],
            "holdout_max_drawdown_r": ho["max_drawdown_r"],
            "holdout_trades": ho["trade_count"],
        })

    path = os.path.join(OUT_DIR, "tp_curve.csv")
    pd.DataFrame(export).to_csv(path, index=False)
    print("\nwrote %s" % path)


if __name__ == "__main__":
    main()
