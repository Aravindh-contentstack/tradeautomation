"""Turning scored candidates into the table the user asked for.

The engine's own journal (backtest/journal.py) stores machine-readable
values: raw timestamps, floats, and probabilities on a 0-to-100 scale.
This module is the presentation layer on top of it, producing the exact
column formats the study was specified in ("Jan 1 2020", "Monday",
"HH:MM", "33.33%").

Formatting lives here and NOT in journal.py on purpose. The stored journal
is read back by analysis.py and by the live bot, both of which need the
numbers, and a date rendered as "Jan 1 2020" cannot be parsed back into a
sortable timestamp without guesswork.
"""

import os

import pandas as pd

from backtest.killzone import to_london

# The study's own columns, in the order the user listed them.
STAGE_A_COLUMNS = [
    "date", "day", "start_time", "end_time", "duration", "session",
    "sl_size_pips", "max_rr", "max_rr_to_be",
    "htf_strength", "trade_strength", "final_probability",
]

FINAL_COLUMNS = [
    "date", "day", "start_time", "end_time", "duration", "session",
    "htf_strength", "trade_strength", "final_probability", "trade_outcome_r",
]

# Extra columns carried on every research export. Not part of the two
# tables above, but the first thing anyone asks for the moment a number
# looks wrong ("which model was that? was it even taken?").
DIAGNOSTIC_COLUMNS = [
    "entry_model", "direction", "taken", "exit_reason", "terminal_r",
    "sl_buffer_pips", "tp_multiple", "min_live_probability", "year",
]

SESSION_LABELS = {"london": "London", "ny": "New York"}


def _london(ts):
    """A UTC timestamp in London civil time.

    Only for the raw timestamp columns. `date` and `day_of_week` are
    ALREADY London (journal.build_row converts before storing), so putting
    them through here would shift them a second time.
    """
    if ts is None or pd.isna(ts):
        return None
    return to_london(pd.Timestamp(ts))


def fmt_date(value):
    """"Jan 1 2020". No zero padding on the day, per the spec.

    Takes the journal's `date`, which is a plain date already in London
    civil time, not a UTC timestamp needing conversion.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    d = pd.Timestamp(value)
    return "%s %d %d" % (d.strftime("%b"), d.day, d.year)


def fmt_time(ts):
    """"HH:MM" in London civil time, which is what the strategy's clock
    runs on: the killzones, the 19:00 checkpoint and the Friday deadline
    are all defined there, so a UTC time would disagree with the engine on
    exactly the trades that matter most."""
    t = _london(ts)
    return t.strftime("%H:%M") if t is not None else ""


def fmt_duration(start, end):
    """"HH:MM" of elapsed time, and it may exceed 24 hours.

    Hours are NOT wrapped at 24. A trade held Tuesday to Friday reads
    "68:30", not "20:30". Wrapping would make a three-day hold
    indistinguishable from a twenty-minute one.
    """
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return ""
    delta = pd.Timestamp(end) - pd.Timestamp(start)
    total = int(delta.total_seconds())
    if total < 0:
        return ""
    return "%02d:%02d" % (total // 3600, (total % 3600) // 60)


def fmt_pct(value):
    """"33.33%", two decimals. Blank for a missing score, which is not the
    same as 0.00% and must not be rendered as one."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return "%.2f%%" % round(float(value), 2)


def fmt_session(value):
    return SESSION_LABELS.get(value, value or "")


def stage_a_rows(candidates, pip_size, sl_buffer_pips):
    """The Stage A collection table: one row per candidate, no TP applied.

    Note the journal's column names, which read backwards from what the
    study calls them: `order_placed_time` is when the trade STARTED (the
    fill) and `order_completed_time` is when it ENDED (the exit).
    """
    out = []
    for c in candidates:
        row = c["row"]
        start = row.get("order_placed_time")
        end = row.get("order_completed_time")
        out.append({
            "date": fmt_date(row.get("date")),
            "day": row.get("day_of_week") or "",
            "start_time": fmt_time(start),
            "end_time": fmt_time(end),
            "duration": fmt_duration(start, end),
            "session": fmt_session(row.get("session")),
            "sl_size_pips": round(c["sl_pips"], 2),
            "max_rr": round(c["walk"]["max_r_reached"], 2),
            "max_rr_to_be": (
                round(c["max_r_to_be"], 2) if c["max_r_to_be"] is not None else ""
            ),
            "htf_strength": fmt_pct(c["htf_probability"]),
            "trade_strength": fmt_pct(c["total_probability"]),
            "final_probability": fmt_pct(c["final_probability"]),
            "entry_model": c.get("entry_model"),
            "direction": row.get("direction"),
            "taken": c["taken"],
            "exit_reason": row.get("exit_reason"),
            "terminal_r": round(c["walk"]["terminal_r"], 4),
            "sl_buffer_pips": sl_buffer_pips,
            "tp_multiple": "",
            "min_live_probability": fmt_pct(c["min_live_probability"]),
            "year": c["year"],
        })
    return out


def final_rows(candidates, scored, sl_buffer_pips):
    """The Stage E table: the same trades with a take-profit applied.

    `scored` is the per-TP result list from tp_models.score_all, which
    carries cand_idx back to the candidate it came from. Joining on that
    rather than on position is what keeps the two in step when a TP family
    skips a candidate (a liquidity target that did not exist).
    """
    out = []
    for s in scored:
        c = candidates[s["cand_idx"]]
        row = c["row"]
        start = row.get("order_placed_time")
        # The TP's own exit, which is earlier than the managed exit
        # whenever the target was reached first.
        end = s.get("exit_time") or row.get("order_completed_time")
        out.append({
            "date": fmt_date(row.get("date")),
            "day": row.get("day_of_week") or "",
            "start_time": fmt_time(start),
            "end_time": fmt_time(end),
            "duration": fmt_duration(start, end),
            "session": fmt_session(row.get("session")),
            "htf_strength": fmt_pct(c["htf_probability"]),
            "trade_strength": fmt_pct(c["total_probability"]),
            "final_probability": fmt_pct(c["final_probability"]),
            "trade_outcome_r": round(s["realised_r"], 4),
            "entry_model": c.get("entry_model"),
            "direction": row.get("direction"),
            "taken": c["taken"],
            "exit_reason": s["exit_reason"],
            "terminal_r": round(c["walk"]["terminal_r"], 4),
            "sl_buffer_pips": sl_buffer_pips,
            "tp_multiple": s.get("tp_multiple", ""),
            "min_live_probability": fmt_pct(c["min_live_probability"]),
            "year": c["year"],
        })
    return out


def save_table(rows, path, columns):
    """Writes one table, creating the directory if needed."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    frame = pd.DataFrame(rows, columns=columns + DIAGNOSTIC_COLUMNS)
    frame.to_csv(path, index=False)
    return path


def save_cells(cells, path):
    """Writes a whole search grid, best first by expectancy.

    The full grid is written, not just the winner. A winner with no
    neighbours is a winner sitting on a spike, and that is only visible
    with the losing cells next to it.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    frame = pd.DataFrame(sorted(
        cells, key=lambda c: c["expectancy_r"], reverse=True
    ))
    frame.to_csv(path, index=False)
    return path
