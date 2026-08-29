"""Applies the 2026-08-29 hand-picked per-pair settings rollout.

Run:  ./.venv/bin/python scripts/apply_pair_settings.py

Reads per-pair-trade-setttings.csv (TP, SL buffer, HTF/total thresholds,
sessions, restricted entry model per pair, hand-picked by the user from
manual review of backtest data) and, for each of the 27 pairs:

  1. Re-runs the walk (2015-2025) with the settings applied, including the
     new allowed_sessions/restricted_entry_models enforcement, to get real
     strike rate / total R / max drawdown numbers behind the file, matching
     the shape data/settings/EUR_USD/EUR_USD_settings_2026.json already has.
  2. Writes data/settings/{PAIR}/{PAIR}_settings_2026.json.
  3. Writes data/weights/{PAIR}/{PAIR}_weights_2026.csv (all factors at
     1.0) for any pair that does not already have a weights file, since
     live/run_live.py raises rather than defaulting if one is missing.

The CSV's last row is labeled "CAD CHF", which does not exist anywhere in
this repo (no data, no pip size, absent from the settings-explorer tool
the user reviewed the backtest data in). The user confirmed it was meant
to be CHF_JPY, which the explorer does have full backtest data for, so
that row is applied to CHF_JPY.

The four already-live pairs whose thresholds this changes (AUD_USD,
EUR_JPY, GBP_USD, NZD_USD, USD_CAD, USD_CHF, USD_JPY, plus EUR_USD and
GBP_JPY which already had a hand-picked 2026-08-26 file) take effect
immediately: live/run_live.py reads whichever settings file has the
highest year suffix, and there is no separate activation step.
"""
import csv
import glob
import json
import os
import sys

sys.path.insert(0, ".")

import pandas as pd

from backtest.instruments import pip_size_for
from backtest.pipeline import build_instrument_bundle
from backtest.research import metrics, pass_runner, tp_models
from backtest.settings import DEFAULT_SETTINGS, save_settings
from backtest.weights import initial_weights, save_weights

YEARS = tuple(range(2015, 2026))
CSV_PATH = "per-pair-trade-setttings.csv"
SETTINGS_DIR = "data/settings"
WEIGHTS_DIR = "data/weights"

# The CSV's last row is labeled "CAD CHF", which has no infrastructure or
# backtest data anywhere in this repo. Confirmed with the user: it was
# meant to be CHF_JPY (present in the settings-explorer tool's own
# instrument list, unlike CAD_CHF).
NAME_FIX = {"CAD CHF": "CHF_JPY"}

NOTE = (
    "Chosen by hand by the user in a review of backtest data across all "
    "27 pairs on 2026-08-29 (per-pair-trade-setttings.csv), aiming for a "
    "combined strike rate above 60% and max drawdown of 4.1%. Factor "
    "weights are all 1.0 (no learning). Stats are over 2015-2025, "
    "in-sample by construction since the settings were picked while "
    "looking at that period. Written as a 2026 file so the live runner, "
    "which reads the newest year, picks these up. Produced by "
    "scripts/apply_pair_settings.py."
)


def csv_to_instrument(name):
    return NAME_FIX.get(name, name.replace(" ", "_"))


def parse_sessions(raw):
    return [s.strip().lower() for s in raw.split(",")]


def parse_restricted(raw):
    raw = raw.strip()
    if not raw or raw == "None":
        return None
    return [raw]


def load_rows():
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def ensure_weights(instrument):
    pattern = os.path.join(WEIGHTS_DIR, instrument, "%s_weights_*.csv" % instrument)
    if glob.glob(pattern):
        return
    path = os.path.join(WEIGHTS_DIR, instrument, "%s_weights_2026.csv" % instrument)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_weights(initial_weights(), path)
    print("  wrote %s (no prior weights file for this pair)" % path)


def main():
    for row in load_rows():
        instrument = csv_to_instrument(row["Pair"])
        tp = float(row["TP"])
        slb = float(row["SLB"])
        htf = float(row["HTF Thresh"])
        total = float(row["Total Thresh"])
        sessions = parse_sessions(row["Sessions"])
        restricted = parse_restricted(row["Restricted entry models"])

        print("=== %s (csv row %r) ===" % (instrument, row["Pair"]))

        pip_size = pip_size_for(instrument)
        bundle = build_instrument_bundle(instrument)
        m15_path = os.path.join("data", "raw", "%s_M15.parquet" % instrument)
        m15_df = pd.read_parquet(m15_path) if os.path.exists(m15_path) else None

        applied = dict(
            DEFAULT_SETTINGS,
            sl_buffer_pips=slb,
            total_threshold=total,
            threshold=total,
            htf_threshold=htf,
            tp_multiple=tp,
            allowed_sessions=sessions,
            restricted_entry_models=restricted,
        )

        walk_settings = dict(applied, tp_multiple=None)
        candidates = pass_runner.run_pass(
            bundle, YEARS,
            pip_size=pip_size, weights=initial_weights(), settings=walk_settings,
            m15_df=m15_df, sl_buffer_pips=slb,
            recheck=True, record_final_probability=True,
        )
        took = pass_runner.taken(candidates)
        scored = tp_models.score_all(took, lambda c: tp)
        s = metrics.summarise(scored)

        payload = save_settings(
            os.path.join(SETTINGS_DIR, instrument, "%s_settings_2026.json" % instrument),
            applied,
            applied,
            len(candidates),
            len(took),
        )
        # save_settings does not know about source/note/backtest stats;
        # add them the same way the earlier hand-picked files carry them.
        path = os.path.join(SETTINGS_DIR, instrument, "%s_settings_2026.json" % instrument)
        payload["source"] = "manual"
        payload["note"] = NOTE
        payload["backtest_2015_2025"] = {
            "trades": s["trade_count"],
            "tp_hits": s["tp_hits"],
            "sl_hits": s["sl_hits"],
            "other": s["unresolved"],
            "strike_rate": s["strike_rate"],
            "total_r": round(s["roi_r"], 2),
            "r_per_trade": round(s["expectancy_r"], 3),
            "max_drawdown_r": round(s["max_drawdown_r"], 2),
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)

        strike = "n/a" if s["strike_rate"] is None else "%.1f%%" % (100 * s["strike_rate"])
        print("  %d trades, strike %s, total %+.2fR, max DD %.2fR -> %s"
              % (s["trade_count"], strike, s["roi_r"], s["max_drawdown_r"], path))

        ensure_weights(instrument)


if __name__ == "__main__":
    main()
