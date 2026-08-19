"""Runs the year-by-year walk-forward backtest across every instrument in
INSTRUMENTS, each with its own frozen/learning weight tables, its own
walk-forward settings, and its own pip size (backtest/instruments.py).

All of the actual simulation lives in backtest/runner.py, which is pure. This
script owns only the things that genuinely differ between entry points: where
files live, what gets read at the start of a year and written at the end of it,
and what gets printed.

Layout: data/{journal,weights,settings}/{instrument}/{instrument}_..._{year}.*
one subfolder per instrument, so results across pairs don't collide.

TWO things are carried forward from year N-1 to year N, both resolved here:

  frozen_weights_for  -- last year's learned weight table (or all-1.0 to start)
  frozen_settings_for -- last year's recommended threshold, max SL size and TP

The settings half is new. `recommend_global_settings` had been writing a JSON
file every year since the engine was built and an exhaustive grep confirmed
nothing ever read it back, so every year silently ran unfiltered at a pinned
2.5R. That is what frozen_settings_for closes.

YEARS controls which calendar year(s) each instrument processes.
"""

import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.analysis import recommend_global_settings
from backtest.instruments import pip_size_for
from backtest.journal import save_journal
from backtest.pipeline import build_instrument_bundle
from backtest.runner import run_year, summarise
from backtest.settings import load_settings, save_settings
from backtest.simulate import FIXED_TP_MULTIPLE
from backtest.weights import initial_weights, load_weights, save_weights

INSTRUMENTS = [
    "AUD_USD",
    "EUR_JPY",
    "EUR_USD",
    "GBP_JPY",
    "GBP_USD",
    "NZD_USD",
    "USD_CAD",
    "USD_CHF",
    "USD_JPY",
    "XAU_USD",
]

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

JOURNAL_DIR = "data/journal"
WEIGHTS_DIR = "data/weights"
SETTINGS_DIR = "data/settings"
RAW_DIR = "data/raw"


def weights_path(instrument, year):
    return os.path.join(WEIGHTS_DIR, instrument, "%s_weights_%d.csv" % (instrument, year))


def journal_path(instrument, year):
    return os.path.join(JOURNAL_DIR, instrument, "%s_trades_%d.csv" % (instrument, year))


def settings_path(instrument, year):
    return os.path.join(SETTINGS_DIR, instrument, "%s_settings_%d.json" % (instrument, year))


def frozen_weights_for(instrument, year):
    """The weights every probability in `year` is computed with: the PREVIOUS
    year's learned table if it exists, else all factors at 1.0.
    """
    prior_path = weights_path(instrument, year - 1)
    if os.path.exists(prior_path):
        return load_weights(prior_path)
    return initial_weights()


def frozen_settings_for(instrument, year):
    """The threshold / max SL size / TP multiple `year` trades with: the
    PREVIOUS year's recommendation.

    Mirrors frozen_weights_for deliberately, but needs no os.path.exists guard:
    load_settings returns DEFAULT_SETTINGS for a missing file, and also for the
    literal `null` that the old caller wrote whenever no grid combination
    cleared the minimum trade count (XAU_USD_settings_2025.json is 4 bytes of
    exactly that). So 2020 bootstraps unfiltered with no special-casing.
    """
    return load_settings(settings_path(instrument, year - 1))


def load_m15(instrument):
    """M15 bars for intrabar tie-breaking, or None if they aren't downloaded.

    None is a supported answer, not a failure: the engine runs without M15 and
    simply takes the pessimistic branch on bars that both spike in our favour
    and touch the stop (backtest/intrabar.py).
    """
    path = os.path.join(RAW_DIR, "%s_M15.parquet" % instrument)
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path)


def run_instrument(instrument, years=YEARS):
    print("\n=== %s ===" % instrument)
    pip_size = pip_size_for(instrument)
    bundle = build_instrument_bundle(instrument)
    df = bundle.df

    m15_df = load_m15(instrument)
    if m15_df is None:
        print("  (no M15 data: intrabar ties resolve pessimistically)")

    available_years = sorted(df["date"].dt.year.unique())
    years = [y for y in years if y in available_years]

    os.makedirs(os.path.join(JOURNAL_DIR, instrument), exist_ok=True)
    os.makedirs(os.path.join(WEIGHTS_DIR, instrument), exist_ok=True)
    os.makedirs(os.path.join(SETTINGS_DIR, instrument), exist_ok=True)

    for year in years:
        applied = frozen_settings_for(instrument, year)

        learned_weights, rows = run_year(
            df,
            year,
            pip_size=pip_size,
            frozen_weights=frozen_weights_for(instrument, year),
            settings=applied,
            m15_df=m15_df,
            obs=bundle.obs,
            liq=bundle.liq,
        )

        save_weights(learned_weights, weights_path(instrument, year))
        save_journal(rows, journal_path(instrument, year))

        # The search runs over ALL candidates, taken and untaken alike. Handing
        # it the taken subset is the ratchet trap (backtest/settings.py).
        recommendation = recommend_global_settings(rows, pip_size)
        taken_count = sum(1 for r in rows if r["taken"])
        save_settings(
            settings_path(instrument, year),
            applied,
            recommendation,
            len(rows),
            taken_count,
        )

        print_year(year, rows, applied, recommendation, taken_count)


def print_year(year, rows, applied, recommendation, taken_count):
    """Prints the year's outcome under BOTH the carried-forward TP and a pinned
    2.5R.

    Both, always. The user chose to carry the TP forward against an
    out-of-sample replay that scored it at -0.069 R/trade versus +0.024 for a
    pinned 2.5R, losing in 8 of 8 configurations. Since analysis.py can score
    any TP post-hoc for free, printing both keeps that trade-off visible in the
    results instead of buried in a decision nobody re-reads.
    """
    applied_tp = applied.get("tp_multiple")
    carried = summarise(rows, applied_tp)
    pinned = summarise(rows, FIXED_TP_MULTIPLE)

    print(
        "Year %d: %d candidates, %d taken  [threshold %s, max SL %s pips, TP %s]"
        % (
            year,
            len(rows),
            taken_count,
            applied.get("threshold"),
            applied.get("max_sl_size_pips"),
            applied_tp,
        )
    )
    for label, stats in (("carried TP %s" % applied_tp, carried),
                         ("pinned %.2fR" % FIXED_TP_MULTIPLE, pinned)):
        print(
            "    %-18s total %+7.2fR   strike %5.1f%%   avg %+6.3fR"
            "   (%dW/%dL/%dBE)"
            % (
                label,
                stats["total_r"],
                100.0 * stats["strike_rate"],
                stats["avg_r"],
                stats["wins"],
                stats["losses"],
                stats["breakeven_count"],
            )
        )

    if recommendation is None:
        print("    recommends: nothing (no combination cleared the minimum trade count)")
    else:
        print(
            "    recommends: threshold %.2f, TP %.2fR, max SL %.1f pips"
            " -> %.2fR over %d trades at %.1f%%"
            % (
                recommendation["threshold"],
                recommendation["tp_multiple"],
                recommendation["max_sl_size_pips"],
                recommendation["roi_r"],
                recommendation["trade_count"],
                100.0 * recommendation["strike_rate"],
            )
        )


def main():
    for instrument in INSTRUMENTS:
        run_instrument(instrument)


if __name__ == "__main__":
    main()
