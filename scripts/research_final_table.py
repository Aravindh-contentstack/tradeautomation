"""The per-trade table for one set of frozen settings.

Run:  ./.venv/bin/python scripts/research_final_table.py [YEAR ...]

Defaults to 2024 and 2025. This is the Step 4 table from the original
brief: one row per trade actually taken, in London civil time, with the
two probabilities at entry, the probability at the moment it closed, and
the outcome in R.

The settings are FROZEN here, not searched. They are the ones the study
landed on, and the point of this script is to show the trades they
produce rather than to look for better ones. Changing a number here does
not re-tune anything, it just re-runs the book under a different rule.
"""

import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.instruments import pip_size_for
from backtest.pipeline import build_instrument_bundle
from backtest.research import metrics, pass_runner, report, tp_models
from backtest.research.params import INSTRUMENT, PROBABILITY_FLOOR
from backtest.settings import DEFAULT_SETTINGS
from backtest.weights import initial_weights

DEFAULT_YEARS = (2024, 2025)

# The recommended configuration.
TP_MULTIPLE = 2.5
SL_BUFFER_PIPS = 2.0
HTF_THRESHOLD = PROBABILITY_FLOOR
TOTAL_THRESHOLD = PROBABILITY_FLOOR

# Weights stay at 1.0 deliberately. Learning them moved the tuning-year
# result not at all (+24.9R either way) and cost more than half the
# held-back return (+6.2R against +17.2R), which on roughly a hundred
# training trades is the signature of fitting noise rather than signal.
WEIGHTS = initial_weights()

OUT_DIR = os.path.join("data", "research", INSTRUMENT)


def main():
    years = tuple(int(a) for a in sys.argv[1:]) or DEFAULT_YEARS
    pip_size = pip_size_for(INSTRUMENT)

    print("Building %s pipeline (about 100 seconds)..." % INSTRUMENT)
    bundle = build_instrument_bundle(INSTRUMENT)
    m15_df = pd.read_parquet(
        os.path.join("data", "raw", "%s_M15.parquet" % INSTRUMENT)
    )

    settings = dict(
        DEFAULT_SETTINGS,
        tp_multiple=None,          # the walk stays TP-free; apply_tp projects
        htf_threshold=HTF_THRESHOLD,
        total_threshold=TOTAL_THRESHOLD,
    )

    candidates = pass_runner.run_pass(
        bundle, years,
        pip_size=pip_size, weights=WEIGHTS, settings=settings,
        m15_df=m15_df, sl_buffer_pips=SL_BUFFER_PIPS,
        recheck=True, record_final_probability=True,
    )
    took = pass_runner.taken(candidates)
    scored = tp_models.score_all(took, lambda c: TP_MULTIPLE)
    rows = report.final_rows(candidates, scored, SL_BUFFER_PIPS)

    tag = "_".join(str(y) for y in years)
    path = os.path.join(OUT_DIR, "trades_%s.csv" % tag)
    report.save_table(rows, path, report.FINAL_COLUMNS)

    print("\n%s  |  TP %.2fR  |  SL buffer %.1f pips  |  HTF >= %.0f%%  |  total >= %.0f%%"
          % (INSTRUMENT, TP_MULTIPLE, SL_BUFFER_PIPS, HTF_THRESHOLD, TOTAL_THRESHOLD))
    print("years %s  |  %d candidates seen, %d taken\n"
          % (", ".join(str(y) for y in years), len(candidates), len(took)))

    head = ("%-12s %-10s %-6s %-6s %-9s %-10s %-9s %-9s %-9s %8s"
            % ("Date", "Day", "Start", "End", "Duration", "Session",
               "HTF", "Trade", "Final", "Outcome"))
    print(head)
    print("-" * len(head))
    for r in rows:
        print("%-12s %-10s %-6s %-6s %-9s %-10s %-9s %-9s %-9s %8s"
              % (r["date"], r["day"], r["start_time"], r["end_time"],
                 r["duration"], r["session"], r["htf_strength"],
                 r["trade_strength"], r["final_probability"],
                 "%+.2fR" % r["trade_outcome_r"]))

    s = metrics.summarise(scored)
    print("-" * len(head))
    print("%d trades  |  %d hit TP, %d hit SL, %d closed some other way"
          % (s["trade_count"], s["tp_hits"], s["sl_hits"], s["unresolved"]))
    print("strike rate %s (TP hits over TP+SL only)  |  total %+.2fR  |  %+.3fR per trade"
          % ("n/a" if s["strike_rate"] is None else "%.1f%%" % (100 * s["strike_rate"]),
             s["roi_r"], s["expectancy_r"]))
    print("max drawdown %.2fR" % s["max_drawdown_r"])
    print("\nwrote %s" % path)


if __name__ == "__main__":
    main()
