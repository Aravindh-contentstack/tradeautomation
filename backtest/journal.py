"""Per-trade CSV journal.

Every gate-passing candidate gets a row, including the ones the prior
year's settings rejected; those are flagged `taken = False`. Journalling
the rejects is not bookkeeping pedantry, it is what keeps the walk-forward
search off the ratchet (see backtest/settings.py). It also matches how the
user journals manually: write down every setup seen, then decide from the
whole book what filter should have been used.

`date` and `day_of_week` are LONDON civil time, not UTC. The strategy's
trading day, the 19:00 checkpoint and the Friday deadline are all defined
in British civil time, so a UTC weekday would disagree with the engine
about which day a trade belongs to on exactly the bars that matter most.
"""

import pandas as pd

from backtest.killzone import to_london
from backtest.simulate import tp_price_for

JOURNAL_COLUMNS = [
    "date",
    "day_of_week",
    "session",
    "order_placed_time",
    "order_completed_time",
    "direction",
    "entry_price",
    "sl_price",
    "tp_price",
    "exit_price",
    "sl_size",
    "probability",
    "result",
    "realised_r",
    "exit_reason",
    "max_r_reached",
    "terminal_r",
    "terminal_reason",
    "be_moved",
    "be_trigger",
    "be_probability",
    "checkpoints",
    "bars_held",
    "trade_duration",
    "sl_excursion_pips",
    "taken",
    "applied_threshold",
    "applied_max_sl_pips",
    "applied_tp_multiple",
    # What triggered the trade. The zone's own geometry is journalled
    # because a stop derived from it is only auditable alongside it, and
    # ob_touch_no distinguishes a first tap from a third (a zone's last
    # chance before it dies).
    "mitigation_time",
    "entry_deferred",
    "ob_top",
    "ob_bottom",
    "ob_touch_no",
    # Which gates had nothing to say. Without this the probability cannot
    # be read after the fact: 100% of ten factors and 100% of seventy look
    # identical in the CSV, and they are not the same trade.
    "excluded_gates",
]


def _result_for(realised_r):
    """Derived from realised_r rather than passed in, so the two columns
    can never disagree. A 0R exit is its own category: a breakeven stop
    resolved nothing, and calling it a loss would distort both the strike
    rate and the weight learning.
    """
    if realised_r > 0:
        return "win"
    if realised_r < 0:
        return "loss"
    return "breakeven"


def build_row(signal, walk, tp_result, settings):
    """One journal row from a TP-free walk plus the TP projection.

    Reads from `walk` (see simulate.simulate_trade): terminal_r,
    terminal_reason, terminal_time, terminal_idx, terminal_price,
    max_r_reached, be_moved, be_trigger, be_probability, checkpoints,
    sl_excursion_pips.

    be_trigger records which rule moved the stop to breakeven -- the
    19:00 checkpoint or the mid-trade probability recheck -- and
    be_probability the live score at that moment for the latter. Both are
    None when the trade never moved to breakeven, or when the caller ran
    simulate_trade without the live-recheck parameters.

    Reads from `tp_result` (see simulate.apply_tp): realised_r, plus
    exit_reason / exit_price / exit_time / exit_idx, which differ from the
    walk's terminal values only when the TP was reached first. Each falls
    back to the corresponding terminal_* value, so a walk that never
    reached the TP journals correctly even from a minimal apply_tp dict.

    `terminal_r` and `terminal_reason` are persisted alongside the
    TP-projected fields because they are TP-free: analysis.py can re-score
    the whole journal at any other TP multiple from them, with no
    re-simulation.
    """
    entry_time = signal["entry_time"]
    london_entry = to_london(entry_time)

    realised_r = tp_result["realised_r"]
    exit_time = tp_result.get("exit_time", walk["terminal_time"])
    exit_idx = tp_result.get("exit_idx", walk["terminal_idx"])

    bars_held = None
    if exit_idx is not None:
        bars_held = exit_idx - signal["idx"]

    return {
        "date": london_entry.date(),
        "day_of_week": london_entry.day_name(),
        "session": signal["session"],
        "order_placed_time": entry_time,
        "order_completed_time": exit_time,
        "direction": signal["direction"],
        "entry_price": signal["entry_price"],
        "sl_price": signal["sl"],
        "tp_price": tp_price_for(
            signal["entry_price"],
            signal["direction"],
            signal["r_distance"],
            settings["tp_multiple"],
        ),
        "exit_price": tp_result.get("exit_price", walk["terminal_price"]),
        "sl_size": signal["r_distance"],
        "probability": signal["probability"],
        "result": _result_for(realised_r),
        "realised_r": realised_r,
        "exit_reason": tp_result.get("exit_reason", walk["terminal_reason"]),
        "max_r_reached": walk["max_r_reached"],
        "terminal_r": walk["terminal_r"],
        "terminal_reason": walk["terminal_reason"],
        "be_moved": walk["be_moved"],
        "be_trigger": walk.get("be_trigger"),
        "be_probability": walk.get("be_probability"),
        "checkpoints": walk["checkpoints"],
        "bars_held": bars_held,
        "trade_duration": exit_time - entry_time if exit_time is not None else None,
        "sl_excursion_pips": walk["sl_excursion_pips"],
        "taken": signal.get("taken", True),
        "applied_threshold": settings.get("threshold"),
        "applied_max_sl_pips": settings.get("max_sl_size_pips"),
        "applied_tp_multiple": settings.get("tp_multiple"),
        "mitigation_time": signal.get("mitigation_time"),
        "entry_deferred": signal.get("entry_deferred"),
        "ob_top": signal.get("ob_top"),
        "ob_bottom": signal.get("ob_bottom"),
        "ob_touch_no": signal.get("ob_touch_no"),
        # Semicolon-joined rather than a list, so the CSV round-trips as a
        # plain string instead of a repr that has to be eval'ed back.
        "excluded_gates": ";".join(signal.get("excluded_gates", ())),
    }


def save_journal(rows, path):
    """The columns= argument DELIBERATELY DROPS unlisted keys, and the key
    it is dropping is `factor_results`.

    The scripts attach `factor_results` to each row on purpose, because
    weights.py learns from it after the row is built. It is a dict of 15
    booleans and it has no business in a CSV. Do not "fix" this by
    widening the frame or by removing columns= -- the silent strip is the
    intended behaviour here. The flip side is that any genuinely new field
    must be added to JOURNAL_COLUMNS or it vanishes without a warning.
    """
    df = pd.DataFrame(rows, columns=JOURNAL_COLUMNS)
    df.to_csv(path, index=False)


def load_journal(path):
    return pd.read_csv(
        path,
        parse_dates=["date", "order_placed_time", "order_completed_time"],
    )
