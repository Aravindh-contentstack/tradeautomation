"""Walk-forward settings: what year N-1's search recommended, applied to year N.

This module exists because the walk-forward loop was never actually wired up.
`recommend_global_settings` wrote a JSON file every year and an exhaustive grep
confirmed nothing ever read it back, so every year silently ran with hardcoded
defaults (no probability threshold, no max-SL filter, TP pinned at 2.5R). These
four functions are the missing read side.

Carrying all three settings forward (threshold, max SL size, TP multiple) is the
user's explicit decision. An out-of-sample replay on the existing journals scored
the carried-forward TP at -0.069 R/trade against +0.024 for a pinned 2.5R, losing
in 8 of 8 configurations. The user chose to carry it forward anyway, so that is
what this implements; analysis.py can score a pinned 2.5R post-hoc for free, and
the final report prints both so the comparison stays visible rather than assumed.

THE RATCHET TRAP. `is_taken` decides which candidates are *traded*. It must never
be used to decide which candidates are *searched over*. If year N's search only
sees trades that passed year N-1's filters, it is looking at a pre-filtered pool
and can only ever recommend a higher threshold and a tighter SL cap; thresholds
then ratchet monotonically upward until the pool starves. XAU_USD 2025 already has
only 2 candidates at probability 40 or above out of 39 -- one ratchet step kills
it. So: journal every gate-passing candidate with a `taken` flag, search over all
of them, and report P&L over the taken ones only. This is also how the user trades
manually, journalling every setup seen and then deciding from the whole book which
filter should have been used.
"""

import json
import os

# None means "no filter", not "zero". 2020 bootstraps here with nothing to carry
# forward, so it takes every candidate at the spec's 2.5R.
#
# TWO thresholds now, because the M15 entry layer splits the decision the way
# the user actually makes it:
#
#   htf_threshold    gates whether the M15 scan runs at all. FIXED permissive,
#                    never searched -- see analysis.py for why.
#   total_threshold  gates whether the order is placed, scored over HTF plus
#                    entry factors together. This is the one that is searched.
#
# `threshold` is kept for the legacy files on disk and for the pre-M15 baseline
# path. load_settings maps a file carrying only `threshold` onto
# total_threshold, so five years of stored settings keep working.
DEFAULT_SETTINGS = {
    "htf_threshold": None,
    "total_threshold": None,
    "threshold": None,
    "tp_multiple": 2.5,
    "max_sl_size_pips": None,
}


def load_settings(path):
    """Returns the settings to apply, always a complete dict, never raising.

    Four shapes exist on disk and all four have to survive:

    1. No file at all (the first year of an instrument).
    2. A literal ``null``. `recommend_global_settings` returns None when no grid
       combination clears the minimum trade count, and the old caller json.dump'd
       that None straight out. data/settings/XAU_USD/XAU_USD_settings_2025.json is
       4 bytes of exactly this.
    3. The nested {applied, recommended, candidate_count, taken_count} shape this
       module writes. The `recommended` block is what feeds the next year;
       `applied` is only an audit trail of what the prior year handed down.
    4. The legacy flat 6-key shape (threshold, tp_multiple, max_sl_size_pips,
       roi_r, strike_rate, trade_count), which is itself a recommendation.

    The merge is key-by-key over DEFAULT_SETTINGS so a partial or hand-edited dict
    degrades to defaults per-field instead of raising KeyError later, and so the
    scoring fields (roi_r, strike_rate, trade_count) never leak into the applied
    settings.
    """
    raw = None
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                raw = json.load(f)
        except (ValueError, OSError):
            # A truncated or corrupt file is not worth crashing a 10-instrument
            # run over; the year simply bootstraps unfiltered.
            raw = None

    if not isinstance(raw, dict):
        return dict(DEFAULT_SETTINGS)

    source = raw.get("recommended") if "recommended" in raw else raw
    if not isinstance(source, dict):
        source = {}

    merged = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if source.get(key) is not None:
            merged[key] = source[key]

    # Shape 5: a file written before the entry layer existed carries only
    # `threshold`. It was scored over HTF factors alone, but it was the gate on
    # placing the order, so total_threshold is where it belongs. Leaving
    # htf_threshold at None means no pre-gate, which is the permissive end and
    # therefore the safe reading of a file that never had one.
    if merged["total_threshold"] is None and merged["threshold"] is not None:
        merged["total_threshold"] = merged["threshold"]
    return merged


def save_settings(path, applied, recommended, candidate_count, taken_count):
    """Writes the nested shape.

    `applied` and `recommended` are kept separate on purpose: it is the only way
    to verify the loop actually closed. Year N's `applied` must equal year N-1's
    `recommended`, and that check proves the loop rather than merely proving that
    some filter ran.
    """
    payload = {
        "applied": dict(applied) if applied else dict(DEFAULT_SETTINGS),
        "recommended": recommended,
        "candidate_count": candidate_count,
        "taken_count": taken_count,
    }
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return payload


def is_taken(signal, settings, pip_size):
    """Would the prior year's settings have admitted this signal?

    The two filter expressions are lifted verbatim from the pool construction in
    analysis.py (the max-SL filter and the probability threshold), so a signal is
    taken here exactly when the search would have counted it there.

    signal["r_distance"] is what journal.py writes as `sl_size`, so it is already
    in raw price units and lines up with the pips-times-pip_size grid without any
    further conversion.
    """
    max_sl_size_pips = settings.get("max_sl_size_pips")
    if max_sl_size_pips is not None:
        max_sl_size_price = max_sl_size_pips * pip_size
        if not signal["r_distance"] <= max_sl_size_price:
            return False

    # total_probability is HTF plus the firing entry model's factors. A signal
    # from the pre-M15 path has no entry factors, so find_signals sets it equal
    # to `probability` and this comparison is unchanged for that case.
    threshold = settings.get("total_threshold")
    if threshold is None:
        threshold = settings.get("threshold")
    if threshold is not None:
        score = signal.get("total_probability")
        if score is None:
            score = signal["probability"]
        if not score >= threshold:
            return False

    return True


def apply_settings(signals, settings, pip_size):
    """Tags every signal with a boolean "taken" and returns the same list.

    Deliberately tags rather than filters. The caller still simulates and journals
    every candidate; `taken` only controls whose P&L counts. Filtering here is the
    ratchet trap described in the module docstring.

    Also enforces one taken trade per order block. Signals must arrive in
    chronological order for that to mean "the first one", which find_signals
    guarantees by walking bars forward.
    """
    seen_obs = set()
    for signal in signals:
        taken = is_taken(signal, settings, pip_size)
        # ONE TRADE PER ORDER BLOCK, the user's rule. Enforced here rather
        # than in find_signals because "taken" is what the rule counts, and
        # only this function knows it. A candidate that never passed the
        # threshold costs the zone nothing.
        #
        # First-come, not best-of. Picking the highest-scoring candidate on
        # a zone would need the zone's whole life in hand before deciding,
        # which the live bot never has.
        if taken:
            ob_row = signal.get("ob_row")
            if ob_row is not None:
                if ob_row in seen_obs:
                    taken = False
                else:
                    seen_obs.add(ob_row)
        signal["taken"] = taken
    return signals
