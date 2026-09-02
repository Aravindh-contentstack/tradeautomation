"""Live-trade journal: tracks every order the bot places plus the extra
state needed to run the SAME daily-checkpoint trade management the
backtest simulates (see backtest/simulate.py) - a breakeven-moved flag,
the next 19:00 London checkpoint due, and the trade's Friday deadline -
so management survives a bot restart instead of forgetting where each
open trade stood.

A row is written the moment an order is SENT, not when it fills, so
`status` has three values, not two:

    open       resting at the broker, or filled and being managed
    closed     the position is gone and its result is recorded
    cancelled  the order was removed before it ever filled

"cancelled" matters because live/pending_plan.py is cancel-and-replace by
design: an order that re-hosts to a new candle is cancelled and a fresh
row written, so MOST rows here never become trades. Without a way to say
so, those rows would stay "open" forever - polluting any read of what was
actually traded, and leaving run_live.py's management loops re-checking
orders that stopped existing weeks ago. See mark_cancelled() for the one
narrow condition under which it is safe to write.
"""

import os

import pandas as pd

from backtest.journal import JOURNAL_COLUMNS
from backtest.killzone import friday_cutoff_for, london_cutoff_for, next_london_cutoff

# "be_moved" is deliberately NOT repeated here - it already exists in
# JOURNAL_COLUMNS (the backtest tracks the same flag), so it's reused
# directly rather than duplicated under a second name.
#
# pending_exit_reason: when OUR OWN management step (a 19:00 cut or the
# Friday deadline) sends the closing order, the real win/loss/breakeven
# outcome isn't known until MT5 confirms the fill - so the row stays
# "open" and just records WHY a close was requested. reconcile_closed_trades
# picks this up once the position is actually gone, and finalizes the row
# with the real result while keeping this reason instead of overwriting it
# with a generic "closed natively" guess.
LIVE_ONLY_COLUMNS = ["ticket", "lot_size", "status", "next_checkpoint", "friday_cutoff", "pending_exit_reason"]
LIVE_COLUMNS = JOURNAL_COLUMNS + LIVE_ONLY_COLUMNS


def journal_path(instrument, year):
    dir_path = os.path.join("data", "journal", instrument)
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, "%s_live_trades_%d.csv" % (instrument, year))


# Columns that start out entirely blank (None) on every open trade, then
# get a real value written into them later on close. An all-blank column
# round-trips through CSV as float64 NaN, and pandas 3.0 raises rather
# than silently upcasting when a string/Timedelta is later assigned into
# that - so these are forced back to plain object dtype right after
# loading.
_TEXT_COLUMNS_WRITTEN_LATER = ["result", "exit_reason", "trade_duration", "pending_exit_reason"]


_UTC_DATETIME_COLUMNS = ["order_placed_time", "order_completed_time", "next_checkpoint", "friday_cutoff"]


def _load(path):
    if os.path.exists(path):
        df = pd.read_csv(path)
        for col in _UTC_DATETIME_COLUMNS:
            if col in df.columns:
                # A column that's entirely blank on every open trade (like
                # order_completed_time) round-trips through CSV with no
                # timezone info to infer, landing as tz-naive - explicit
                # utc=True forces it back to tz-aware even when all-NaT,
                # so a later tz-aware assignment doesn't get rejected.
                df[col] = pd.to_datetime(df[col], utc=True)
        for col in _TEXT_COLUMNS_WRITTEN_LATER:
            if col in df.columns:
                df[col] = df[col].astype(object)
        return df
    return pd.DataFrame(columns=LIVE_COLUMNS)


def _initial_checkpoint(entry_time):
    """Same rule simulate_trade uses: 19:00 London on the entry's own
    London date, advanced to the next day if the entry itself landed at
    or after that instant.
    """
    checkpoint = london_cutoff_for(entry_time)
    while checkpoint <= entry_time:
        checkpoint = next_london_cutoff(checkpoint)
    return checkpoint


def append_open_trade(instrument, signal, tp, ticket, lot_size):
    """Writes a row the moment an order is sent, pre-computing the
    trade's own checkpoint schedule so management can act on it
    immediately, even before the next reconciliation pass.
    """
    path = journal_path(instrument, signal["entry_time"].year)
    df = _load(path)
    row = {col: None for col in LIVE_COLUMNS}
    row.update({
        "date": signal["entry_time"].date(),
        "day_of_week": signal["entry_time"].day_name(),
        "session": signal["session"],
        "order_placed_time": signal["entry_time"],
        "direction": signal["direction"],
        "entry_price": signal["entry_price"],
        "sl_price": signal["sl"],
        "tp_price": tp,
        "sl_size": signal["r_distance"],
        "probability": signal["probability"],
        "taken": True,
        "ticket": ticket,
        "lot_size": lot_size,
        "status": "open",
        "be_moved": False,
        "next_checkpoint": _initial_checkpoint(signal["entry_time"]),
        "friday_cutoff": friday_cutoff_for(signal["entry_time"]),
    })
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(path, index=False)
    return path


def open_trades(instrument, year):
    """Every currently-open row, as a list of dicts, for the management
    step to iterate - richer than open_tickets() since management needs
    direction/checkpoint state, not just the ticket number.
    """
    path = journal_path(instrument, year)
    df = _load(path)
    if df.empty:
        return []
    return df.loc[df["status"] == "open"].to_dict("records")


def update_checkpoint_state(instrument, year, ticket, *, be_moved=None, next_checkpoint=None):
    path = journal_path(instrument, year)
    df = _load(path)
    mask = df["ticket"] == ticket
    if not mask.any():
        return False
    if be_moved is not None:
        df.loc[mask, "be_moved"] = be_moved
    if next_checkpoint is not None:
        df.loc[mask, "next_checkpoint"] = next_checkpoint
    df.to_csv(path, index=False)
    return True


def mark_pending_exit(instrument, year, ticket, reason):
    """Records that OUR management step just requested a close (and
    why), without marking the row closed - the real result is only known
    once reconcile_closed_trades sees MT5 confirm the position is gone.
    """
    path = journal_path(instrument, year)
    df = _load(path)
    mask = df["ticket"] == ticket
    if not mask.any():
        return False
    df.loc[mask, "pending_exit_reason"] = reason
    df.to_csv(path, index=False)
    return True


def mark_cancelled(instrument, year, ticket):
    """Records that a pending order was removed WITHOUT ever filling.

    Only ever call this having directly observed both halves of that
    claim - that the order was resting, and that removing it succeeded.
    Do not infer it from a ticket's absence.

    The reason is that one integer serves as order ticket, position
    ticket and position id throughout run_live.py, so "no longer a
    resting order" is equally true of an order that FILLED. Writing
    "cancelled" onto a filled order would drop it out of open_trades()
    and strand a real position with no 19:00 checkpoint and no Friday
    close - the worst outcome available in this file. run_live.py's
    reconcile_pending_orders is the only caller for exactly that reason:
    it holds the pending_order_exists() and cancel_pending_order()
    results together, which no other code path does.

    Returns False when the ticket isn't in this year's journal, which is
    the normal answer for the year that didn't hold the order - callers
    sweep both years rather than tracking which one applies.
    """
    path = journal_path(instrument, year)
    df = _load(path)
    mask = df["ticket"] == ticket
    if not mask.any():
        return False
    df.loc[mask, "status"] = "cancelled"
    df.loc[mask, "exit_reason"] = "cancelled"
    df.to_csv(path, index=False)
    return True


def close_trade(instrument, year, ticket, result, exit_time, exit_reason):
    """Fills in the outcome once a position is gone, whether it closed
    natively (broker-side SL/TP fill) or was closed by our own
    management step (breakeven cut / Friday deadline).
    """
    path = journal_path(instrument, year)
    df = _load(path)
    mask = df["ticket"] == ticket
    if not mask.any():
        return False

    order_placed = df.loc[mask, "order_placed_time"].iloc[0]
    df.loc[mask, "result"] = result
    df.loc[mask, "order_completed_time"] = exit_time
    df.loc[mask, "exit_reason"] = exit_reason
    df.loc[mask, "status"] = "closed"
    df.loc[mask, "trade_duration"] = pd.to_datetime(exit_time) - pd.to_datetime(order_placed)
    df.to_csv(path, index=False)
    return True
