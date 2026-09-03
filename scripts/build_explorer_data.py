"""Precomputes every trade the settings explorer can ever need to show.

Run:  ./.venv/bin/python scripts/build_explorer_data.py [INSTRUMENT ...]
Out:  data/research/explorer.json

Naming instruments builds only those, and MERGES them into whatever the
output file already holds. That default matters: the page carries every
instrument in one file, so a run that rewrote the file from scratch would
silently delete the twenty-six pairs it was not asked about.

Every instrument is backtested INDEPENDENTLY. Nothing about one pair's
book, scale or settings informs another's, and the explorer keeps their
tuning separate for the same reason.

The explorer is a published Artifact: one self-contained HTML file with no
network access. So every number it can display has to be in the file
before it is opened. This script decides what that is.

WHICH SETTINGS COST A RE-RUN, AND WHICH DO NOT
----------------------------------------------
Two of the four settings can be re-derived in the browser exactly, and two
cannot. That split is the whole design.

Free in the browser:

  take profit    The forward walk carries no take-profit at all (see
                 backtest/simulate.py), so apply_tp's rule reduces to "if
                 max_r_reached >= target, the trade filled there, else the
                 walk's own ending stands". Two stored numbers and a
                 comparison.
  HTF threshold  find_signals just skips a candidate scoring too low, and a
                 skipped candidate never consumes its order block. Filtering
                 the stored list BEFORE re-running the one-trade-per-order
                 -block rule reproduces that exactly.

Needs a re-run, so it is stored as a discrete grid:

  SL buffer      Moves the stop AND the pending order price, so it changes
                 entry prices, stop distances, which setups clear the
                 minimum stop, and which orders ever fill.
  trade strength Not merely a filter. It feeds the live-probability
                 breakeven rule, which moves the stop to entry the first
                 bar a trade's score drops below the threshold it was
                 admitted under -- so it changes OUTCOMES, not just
                 membership. Measured over 2015-2025: keeping that rule is
                 worth +42.07R against +37.07R without it, which is too
                 much to drop for convenience.

Hence a 7 x 9 grid of real backtest runs per instrument, about three
minutes each after that instrument's one-off pipeline build.

WHY recheck_all
---------------
Each cell is run with no HTF gate, so the browser can apply any. But the
breakeven rule is normally armed only for candidates that survived the
one-trade-per-order-block rule, and the browser re-runs that rule after
its own HTF filter -- which can promote a candidate this script recorded
as untaken. Every stored trade therefore has to be walked as though it
were taken, which is what recheck_all does.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.instruments import PIP_SIZES, pip_size_for
from backtest.pipeline import build_instrument_bundle
from backtest.research import pass_runner, report
from backtest.research.params import ALL_YEARS, SL_BUFFER_GRID
from backtest.settings import DEFAULT_SETTINGS
from backtest.weights import initial_weights

# Every instrument the explorer carries. Each is backtested completely
# independently: its own book of trades, its own scale, its own tuning.
# Nothing about one pair's settings informs another's.
INSTRUMENTS = ("EUR_USD", "GBP_JPY", "AUD_USD", "EUR_JPY", "GBP_USD",
               "NZD_USD", "USD_CAD", "USD_CHF", "USD_JPY",
               "EUR_GBP", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD",
               "GBP_CHF", "GBP_AUD", "GBP_CAD", "GBP_NZD",
               "AUD_JPY", "AUD_CAD", "AUD_CHF", "AUD_NZD",
               "NZD_JPY", "NZD_CAD", "NZD_CHF",
               "CAD_JPY", "CHF_JPY",
               # Point-quoted index CFDs and the metals, added 2026-09-03.
               # Read each on its own scale: a "pip" is one index point on
               # the eleven indices, and a contract-value pip on the metals.
               "SP500", "US30", "UK100", "DAX40", "JPN225", "NAS100",
               "ASXAUD", "HSIHKD", "IBXEUR", "ESXEUR", "F40EUR",
               "XAU_USD", "XAG_USD", "XPT_USD", "XPD_USD", "COPPER_USD")

# Trade-strength presets. Starts at the floor below which a trade is not
# tradeable at all, and stops at 65 because the scale does not reach 100
# in practice: with every weight at 1.0 the highest observed score is
# 66.7 on GBP_JPY and 69.1 on EUR_USD.
TOTAL_THRESHOLD_GRID = (25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0)

# Codes keep the JSON small and the browser's comparisons cheap.
SESSIONS = ("london", "ny")
MODELS = ("LC-1", "LC-2A", "LC-2B", "CE")
REASONS = ("tp", "sl", "be_stop", "cut_19h", "friday_close", "data_end")

OUT_PATH = os.path.join("data", "research", "explorer.json")

# The tiers build_instrument_bundle loads. Checked up front so a missing
# parquet is one clear message rather than a traceback forty minutes into
# a multi-instrument run.
REQUIRED_TIERS = ("D", "H4", "H1", "M15")

SCALPING_NOT_IMPLEMENTED = """\
The scalping chain (4H -> H1 -> M15 -> M1) is not implemented.

The engine is hardwired to the swing chain. backtest/pipeline.py loads D,
H4, H1 and M15 by literal name, H1 IS the base timeline that everything
else is merged onto, and the daily_/h4_/h1_ column and factor names are
spelled out across backtest/, smc/ and live/. On top of that the trade
management in backtest/simulate.py is swing-horizon by design: a 19:00
London breakeven checkpoint, a 19:00 loss cut and a Friday-close deadline,
none of which mean anything on a scalp.

Making it real is an engine project, not a flag. Nothing was run and
nothing was published."""


def validate(instrument):
    """Refuse an instrument we cannot honestly backtest.

    Two failures are possible and they need different answers, so they get
    different messages. A missing pip size is a TRADING decision (what is
    one pip worth on this thing?) and is never guessed here. Missing data
    is just a fetch that has not happened yet.
    """
    problems = []
    if instrument not in PIP_SIZES:
        problems.append(
            "no pip size configured. Add %s to PIP_SIZES in "
            "backtest/instruments.py first. This script will not guess one, "
            "because the SL buffer grid and every R figure on the page are "
            "denominated in it." % instrument
        )
    missing = [
        tier for tier in REQUIRED_TIERS
        if not os.path.exists(
            os.path.join("data", "raw", "%s_%s.parquet" % (instrument, tier))
        )
    ]
    if missing:
        problems.append(
            "missing raw data: %s. Fetch it before backtesting."
            % ", ".join("%s_%s.parquet" % (instrument, t) for t in missing)
        )
    return problems


def _code(value, table):
    """Index into a lookup table, or -1 for an unknown value.

    -1 rather than a raise: a new entry model or exit reason appearing
    should show up as "unclassified" in the explorer, not abort a
    three-minute build.
    """
    try:
        return table.index(value)
    except ValueError:
        return -1


def encode(candidate):
    """One trade as a flat array.

    Field ORDER is the contract with the page's decoder. Anything added
    goes on the end, never in the middle.
    """
    row = candidate["row"]
    walk = candidate["walk"]
    start = row.get("order_placed_time")
    end = row.get("order_completed_time")

    return [
        candidate["ob_row"],                                    # 0
        candidate["year"],                                      # 1
        report.fmt_date(row.get("date")),                       # 2
        (row.get("day_of_week") or "")[:3],                     # 3
        report.fmt_time(start),                                 # 4
        report.fmt_time(end),                                   # 5
        report.fmt_duration(start, end),                        # 6
        report.fmt_date(pd.Timestamp(end).date()) if end is not None else "",  # 7
        _code(row.get("session"), SESSIONS),                    # 8
        _code(candidate.get("entry_model"), MODELS),            # 9
        1 if row.get("direction") == "bullish" else 0,          # 10
        round(candidate["htf_probability"] or 0.0, 2),          # 11
        round(candidate["total_probability"] or 0.0, 2),        # 12
        round(candidate["final_probability"] or 0.0, 2)
        if candidate.get("final_probability") is not None else None,   # 13
        round(candidate["sl_pips"], 1),                         # 14
        round(walk["max_r_reached"], 3),                        # 15
        round(walk["terminal_r"], 3),                           # 16
        _code(walk["terminal_reason"], REASONS),                # 17
        round(candidate["max_r_to_be"], 2)
        if candidate.get("max_r_to_be") is not None else None,  # 18
        # Sort key for the equity curve. The account moves when a trade
        # CLOSES, and two trades opened the same week can close a month
        # apart, so entry order would draw a curve nobody lived through.
        int(pd.Timestamp(end).timestamp() // 60) if end is not None else 0,  # 19
    ]


def data_start_year(instrument):
    """The first year this instrument's WHOLE timeframe chain exists.

    Not every instrument has eleven years of history, but the payload's
    "years" is always ALL_YEARS, so without this the page would offer a
    year filter for years that hold no data and read as years that held
    no trades. Those are very different things to someone deciding
    whether a book is worth trading.

    It is the LATEST of the four tiers' starts, not the earliest. The
    swing chain needs Daily, H4, H1 and M15 all present, so the chain
    begins when the last of them does. Palladium is exactly this case:
    M15 from 2021-07 but daily only from 2022-02, so its real start is
    2022, not 2021.
    """
    starts = []
    for tier in REQUIRED_TIERS:
        path = os.path.join("data", "raw", "%s_%s.parquet" % (instrument, tier))
        dates = pd.read_parquet(path, columns=["date"])["date"]
        starts.append(pd.Timestamp(dates.min()).year)
    return max(starts)


def build_instrument(instrument):
    """Every cell of the grid for one instrument."""
    pip_size = pip_size_for(instrument)
    print("\n=== %s ===" % instrument)
    print("  building pipeline (about two minutes, done once)...")
    bundle = build_instrument_bundle(instrument)
    m15_df = pd.read_parquet(
        os.path.join("data", "raw", "%s_M15.parquet" % instrument)
    )
    weights = initial_weights()

    cells = {}
    # Headline counts for the page's "N candidate trades, M after
    # one-trade-per-order-block" line. Taken from the loosest cell, which
    # is the only one where the phrase means the whole book. Recorded here
    # rather than transcribed by hand from this script's own log, because
    # forty-odd instruments of hand-copied numbers on a page that decides
    # real trades is a wrong number waiting to happen.
    counts_cell = (2.0, 25.0)
    n_candidates = n_after_ob = 0

    for buffer_pips in SL_BUFFER_GRID:
        counts = []
        for threshold in TOTAL_THRESHOLD_GRID:
            settings = dict(
                DEFAULT_SETTINGS,
                tp_multiple=None,
                htf_threshold=None,       # the browser applies any HTF cut
                total_threshold=threshold,
            )
            candidates = pass_runner.run_pass(
                bundle, ALL_YEARS,
                pip_size=pip_size, weights=weights, settings=settings,
                m15_df=m15_df, sl_buffer_pips=buffer_pips,
                recheck=True, recheck_all=True,
                record_final_probability=True,
            )
            if (buffer_pips, threshold) == counts_cell:
                n_candidates = len(candidates)
                # Distinct order blocks, NOT the number actually taken.
                # "After one-trade-per-order-block" is asking how big the
                # book is once duplicates on the same order block collapse
                # to one, which is a property of the book and does not move
                # with the threshold. The taken count does move with it, so
                # it would be the wrong number for a fixed header line.
                n_after_ob = len({c["ob_row"] for c in candidates})
            # Only what clears this cell's threshold. The rest belong to a
            # looser cell and are already stored there.
            kept = [
                c for c in candidates
                if (c["total_probability"] or -999) >= threshold
            ]
            # Entry order, because the one-trade-per-order-block rule is
            # first-come and only means "the first one" if this holds.
            kept.sort(key=lambda c: c["row"]["order_placed_time"])
            cells["%g|%g" % (buffer_pips, threshold)] = [encode(c) for c in kept]
            counts.append(len(kept))
        print("  buffer %.1f pips: %s"
              % (buffer_pips, " ".join("%d" % n for n in counts)))

    print("  %d candidates, %d after one-trade-per-order-block"
          % (n_candidates, n_after_ob))

    return {
        "years": list(ALL_YEARS),
        "buffers": list(SL_BUFFER_GRID),
        "thresholds": list(TOTAL_THRESHOLD_GRID),
        # Carried so the page can say what a "pip" is on this instrument.
        # An index CFD's 1.0 is one index point, which puts the buffer grid
        # on a scale that is not comparable with an FX pair's.
        "pip_size": pip_size,
        # The first year the whole D/H4/H1/M15 chain exists, so the page can
        # say "this book starts later" instead of showing empty early years
        # as if they were traded and lost.
        "data_start": data_start_year(instrument),
        "n_candidates": n_candidates,
        "n_after_ob": n_after_ob,
        "cells": cells,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the settings explorer's precomputed trade data.",
    )
    parser.add_argument(
        "instruments", nargs="*",
        help="Instruments to build. Default: every instrument in INSTRUMENTS.",
    )
    parser.add_argument(
        "--strategy", choices=("swing", "scalping"), default="swing",
        help="Timeframe chain to backtest. Only swing exists today.",
    )
    parser.add_argument(
        "--out", default=OUT_PATH,
        help="Output JSON (default: %s)." % OUT_PATH,
    )
    parser.add_argument(
        "--merge", dest="merge", action="store_true", default=True,
        help="Update the named instruments in an existing output file, "
             "leaving the others alone. This is the default.",
    )
    parser.add_argument(
        "--no-merge", dest="merge", action="store_false",
        help="Rewrite the output file from scratch. Drops every instrument "
             "not named on this run.",
    )
    return parser.parse_args(argv)


def load_existing(path):
    """The payload already on disk, or an empty one.

    A corrupt file raises rather than being quietly discarded: losing
    twenty-six instruments' worth of overnight backtests to a silent
    fallback would be the worst outcome available here.
    """
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def main(argv=None):
    args = parse_args(argv)

    if args.strategy == "scalping":
        raise SystemExit(SCALPING_NOT_IMPLEMENTED)

    instruments = args.instruments or list(INSTRUMENTS)

    failed = [(i, validate(i)) for i in instruments]
    failed = [(i, p) for i, p in failed if p]
    if failed:
        lines = ["cannot backtest %d instrument(s):" % len(failed)]
        for instrument, problems in failed:
            for problem in problems:
                lines.append("  %s: %s" % (instrument, problem))
        raise SystemExit("\n".join(lines))

    print("strategy: %s" % args.strategy)
    print("instruments: %s" % ", ".join(instruments))
    print("thresholds: %s" % ", ".join("%g" % t for t in TOTAL_THRESHOLD_GRID))

    payload = load_existing(args.out) if args.merge else None
    if payload is None:
        payload = {"instruments": {}}
    carried = [i for i in payload["instruments"] if i not in instruments]
    if carried:
        print("carrying forward %d untouched instrument(s)" % len(carried))

    # Refreshed from the module constants every run: these are the index
    # tables the encoded rows point into, so a stale copy would silently
    # relabel every session, entry model and exit reason on the page.
    payload["sessions"] = list(SESSIONS)
    payload["models"] = list(MODELS)
    payload["reasons"] = list(REASONS)

    for instrument in instruments:
        payload["instruments"][instrument] = build_instrument(instrument)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    total = sum(len(v) for inst in payload["instruments"].values()
                for v in inst["cells"].values())
    print("\nwrote %s  (%d instruments, %d trade rows, %.1f MB)"
          % (args.out, len(payload["instruments"]), total,
             os.path.getsize(args.out) / 1048576))


if __name__ == "__main__":
    main()
