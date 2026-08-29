"""Live trading loop, one process per instrument, running the exact
same signal logic and trade management as backtest/ against live MT5
data, with real order placement and real position management gated by
the safety guardrails in live/safety.py.

The strategy does not just set a stop-loss/take-profit and walk away:
backtest/simulate.py actively manages every open trade at 19:00 London
each day (move to breakeven if in profit, cut if not) and force-closes
everything by Friday 19:00 London. manage_open_trades() below runs that
exact same schedule against real open MT5 positions, so live behaviour
matches what was actually backtested.

Run on the Windows VPS (see live/README.md for setup), one process per
pair in live/pairs.py, e.g.:

    python -u live/run_live.py EUR_USD

Configure via environment variables - nothing sensitive is hardcoded:

    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER   - your MT5 account credentials
    MT5_SYMBOL_SUFFIX                     - optional; appended to every pair's broker
                                             symbol (e.g. ".a" if your broker quotes
                                             EUR_USD as EURUSD.a, EUR_JPY as EURJPY.a, ...)
    MT5_TERMINAL_PATH                     - optional; the full path to terminal64.exe,
                                             only needed if MT5 can't auto-attach to the
                                             already-running terminal
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  - see live/alerts.py
    DRY_RUN                               - set to "true" to compute and log every
                                             decision WITHOUT sending real orders
                                             (recommended for the first run or two)

Before running for real:
- Verify live/mt5_connector.py's BROKER_TZ against your broker's stated server timezone.
- Verify live/safety.py's MAX_DAILY_LOSS_PCT and DAILY_RESET_HOUR_UTC against
  your prop firm's actual rulebook.
- Confirm your prop firm's challenge rules allow automated/EA trading.
"""

import glob
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, ".")

from backtest.instruments import pip_size_for
from backtest.killzone import next_london_cutoff
from backtest.pipeline import build_live_context
from backtest import entry_params
from backtest.entry_ob import SL_BUFFER_PIPS
from backtest.settings import is_taken, load_settings
from backtest.simulate import find_signals, tp_price_for
from backtest.weights import load_weights

from live import alerts, mt5_connector as connector, journal_live, risk, safety
from live.pairs import PAIRS, magic_for
from live.pending_plan import plan_pending

if len(sys.argv) != 2 or sys.argv[1] not in PAIRS:
    sys.exit("Usage: python live/run_live.py <INSTRUMENT>, one of: %s" % ", ".join(PAIRS))

INSTRUMENT = sys.argv[1]
MT5_SYMBOL = INSTRUMENT.replace("_", "") + os.environ.get("MT5_SYMBOL_SUFFIX", "")
PIP_SIZE = pip_size_for(INSTRUMENT)
MAGIC = magic_for(INSTRUMENT)  # distinct per pair, so 10 processes on one account never collide

DAILY_HISTORY_COUNT = 1000
H4_HISTORY_COUNT = 1500
H1_HISTORY_COUNT = 3000
# The entry models need enough M15 to seed ATR(14), form n=5 internal
# structure, and hold LC-2A's 30-candle approach lookback, all with room to
# spare. 3000 bars is about a month, which is far more than any of that and
# still a cheap fetch at a 15-second poll.
M15_HISTORY_COUNT = 3000

POLL_SECONDS = 15
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

STATE_PATH = os.path.join("live", "state", "%s_state.json" % INSTRUMENT)


def _latest_year_file(pattern):
    matches = glob.glob(pattern)
    if not matches:
        return None
    return max(matches)


def load_latest_settings(instrument):
    """Uses backtest/settings.py's own loader so every settings-file
    shape it supports (nested applied/recommended, legacy flat, null,
    missing) is handled identically here and in the backtest - no
    separate parsing logic to drift out of sync.
    """
    pattern = os.path.join("data", "settings", instrument, "%s_settings_*.json" % instrument)
    path = _latest_year_file(pattern)
    settings = load_settings(path)
    print("Using settings from %s: %s" % (path, settings))
    return settings


def load_latest_weights(instrument):
    pattern = os.path.join("data", "weights", instrument, "%s_weights_*.csv" % instrument)
    path = _latest_year_file(pattern)
    if path is None:
        raise RuntimeError("No weights file found for %s at %s" % (instrument, pattern))
    print("Using weights from %s" % path)
    return load_weights(path)


def manage_open_trades():
    """Runs the SAME daily-checkpoint rule backtest/simulate.py walks
    through historically, but against real time and real open MT5
    positions: at 19:00 London, breakeven if in profit else cut; by
    Friday 19:00 London, force-close no matter what.
    """
    now = datetime.now(timezone.utc)
    current_year = now.year

    for year in (current_year - 1, current_year):
        for trade in journal_live.open_trades(INSTRUMENT, year):
            ticket = trade["ticket"]
            position = connector.get_position(ticket)
            if position is None:
                continue  # closed natively (SL/TP fill) - reconcile_closed_trades handles it

            friday_cutoff = pd.Timestamp(trade["friday_cutoff"])
            next_checkpoint = pd.Timestamp(trade["next_checkpoint"])

            if now >= friday_cutoff:
                if DRY_RUN:
                    alerts.send("[DRY RUN] Would force-close ticket %s (Friday deadline)" % ticket)
                    continue
                result = connector.close_position_at_market(
                    ticket, MT5_SYMBOL, position["direction"], position["volume"], magic=MAGIC
                )
                if result["success"]:
                    journal_live.mark_pending_exit(INSTRUMENT, year, ticket, "friday_close")
                    alerts.send("Force-closed ticket %s at the Friday deadline" % ticket)
                else:
                    alerts.send("FAILED to force-close ticket %s: %s" % (ticket, result["comment"]))
                continue

            if now < next_checkpoint:
                continue

            in_profit = position["profit"] > 0
            if in_profit:
                if not trade["be_moved"] and not DRY_RUN:
                    mod = connector.modify_position_sl(ticket, MT5_SYMBOL, position["price_open"], position["tp"])
                    if mod["success"]:
                        journal_live.update_checkpoint_state(INSTRUMENT, year, ticket, be_moved=True)
                        alerts.send("Moved ticket %s to breakeven at the 19:00 London checkpoint" % ticket)
                    else:
                        alerts.send("FAILED to move ticket %s to breakeven: %s" % (ticket, mod["comment"]))
                elif DRY_RUN and not trade["be_moved"]:
                    alerts.send("[DRY RUN] Would move ticket %s to breakeven" % ticket)

                next_ts = next_checkpoint
                while next_ts <= now:
                    next_ts = next_london_cutoff(next_ts)
                journal_live.update_checkpoint_state(INSTRUMENT, year, ticket, next_checkpoint=next_ts)
            else:
                if DRY_RUN:
                    alerts.send("[DRY RUN] Would cut ticket %s at the 19:00 London checkpoint (not in profit)" % ticket)
                    continue
                result = connector.close_position_at_market(
                    ticket, MT5_SYMBOL, position["direction"], position["volume"], magic=MAGIC
                )
                if result["success"]:
                    journal_live.mark_pending_exit(INSTRUMENT, year, ticket, "cut_19h")
                    alerts.send("Cut ticket %s at the 19:00 London checkpoint (not in profit)" % ticket)
                else:
                    alerts.send("FAILED to cut ticket %s: %s" % (ticket, result["comment"]))


def reconcile_closed_trades():
    """Catches trades MT5 closed on its own (a native SL or TP fill) -
    our own management-driven closes are already journalled the moment
    they happen, in manage_open_trades().
    """
    current_year = datetime.now(timezone.utc).year
    for year in (current_year - 1, current_year):
        for trade in journal_live.open_trades(INSTRUMENT, year):
            ticket = trade["ticket"]
            if connector.is_position_open(ticket):
                continue
            deal = connector.get_closed_deal(ticket)
            if deal is None:
                continue
            if deal["profit"] > 0:
                result = "win"
            elif deal["profit"] < 0:
                result = "loss"
            else:
                result = "breakeven"
            # A never-set pending_exit_reason reads back from CSV as NaN
            # (a float), not None - which `or` would treat as truthy, so
            # pd.isna() is checked explicitly rather than relying on `or`.
            pending_reason = trade.get("pending_exit_reason")
            exit_reason = pending_reason if pending_reason and not pd.isna(pending_reason) else "native_close"
            journal_live.close_trade(INSTRUMENT, year, ticket, result, deal["time"], exit_reason)
            alerts.send(
                "Trade closed: ticket %s result=%s profit=%.2f reason=%s" % (ticket, result, deal["profit"], exit_reason)
            )


def run_once(state, settings, weights):
    if safety.kill_switch_active(INSTRUMENT):
        return state

    balance = connector.account_balance()
    state = safety.roll_daily_state(state, balance)

    day_start_utc = safety.trading_day_start_utc(datetime.now(timezone.utc))
    pair_pnl_today = connector.today_realized_profit(MAGIC, day_start_utc) + connector.floating_profit(MAGIC)

    if safety.daily_loss_breached(state, pair_pnl_today):
        if not state.get("breach_alerted"):
            alerts.send(
                "Daily loss breaker tripped for %s (down %.2f%% of day-start balance). "
                "Pausing new entries until the next trading day." % (INSTRUMENT, safety.MAX_DAILY_LOSS_PCT * 100)
            )
            state["breach_alerted"] = True
        return state

    daily_df = connector.fetch_closed_candles(MT5_SYMBOL, "D", DAILY_HISTORY_COUNT)
    h4_df = connector.fetch_closed_candles(MT5_SYMBOL, "H4", H4_HISTORY_COUNT)
    h1_df = connector.fetch_closed_candles(MT5_SYMBOL, "H1", H1_HISTORY_COUNT)
    m15_df = connector.fetch_closed_candles(MT5_SYMBOL, "M15", M15_HISTORY_COUNT)

    # A full context, not a tail slice of the frame. Order-block positions
    # address the whole history, so slicing the frame afterwards would
    # mis-address every zone; build_live_context cuts the frame and rebases
    # the OB universe together. The M15 bundle is passed through UNSLICED,
    # for the reason in backtest/m15_pipeline.py.
    ctx = build_live_context(daily_df, h4_df, h1_df, PIP_SIZE, m15_df=m15_df)
    if ctx.m15_bundle is None:
        return state

    # The clock is M15 now, not H1. Setups form and orders re-host on M15
    # boundaries, so waking only on a new H1 candle would miss three
    # quarters of every decision and place orders up to 45 minutes stale.
    as_of = len(ctx.m15_bundle.ts) - 1
    latest_candle_time = pd.Timestamp(ctx.m15_bundle.ts[as_of], tz="UTC")

    if not safety.is_new_candle(state, latest_candle_time):
        return state
    safety.mark_candle_processed(state, latest_candle_time)

    # The stop buffer is per instrument and lives in the settings file, so
    # it has to be in force while the setups are BUILT, not applied after.
    # It moves the stop and, on the two LC models, the pending order price
    # too, so it decides the order price, the stop distance, whether the
    # setup clears the minimum stop size, and therefore whether the setup
    # exists at all. Applying it any later would place orders at prices
    # the tuning never tested.
    with entry_params.override(
        sl_buffer_pips=settings.get("sl_buffer_pips", SL_BUFFER_PIPS)
    ):
        signals = find_signals(
            ctx, weights, PIP_SIZE,
            htf_threshold=settings.get("htf_threshold"),
            pending_as_of=as_of,
            allowed_sessions=settings.get("allowed_sessions"),
        )
    state = reconcile_pending_orders(state, signals, settings, balance)
    return state


def reconcile_pending_orders(state, signals, settings, balance):
    """Makes what is RESTING at the broker match what should be resting.

    The decision is live/pending_plan.py's, deliberately: MetaTrader5 is
    Windows-only, so anything importing it cannot be tested where this is
    written, and order placement is the last logic that should go untested.
    This function only EXECUTES the plan.
    """
    taken = [s for s in signals if is_taken(s, settings, PIP_SIZE)]
    keep, cancel, place = plan_pending(
        taken, state.get("pending") or {}, PIP_SIZE
    )

    for record in cancel:
        if DRY_RUN:
            continue
        if not connector.pending_order_exists(record["ticket"]):
            continue
        if not connector.cancel_pending_order(record["ticket"]):
            # Kept so the next poll tries again. Forgetting an order that
            # is still live at the broker is the one outcome here that can
            # leave an unmanaged position.
            keep[str(record.get("ob_row", record["ticket"]))] = record
            alerts.send("Could not cancel pending order %s (%s)"
                        % (record["ticket"], INSTRUMENT))

    for ob_row, signal in place.items():
        placed = place_pending(signal, settings, balance)
        if placed is not None:
            placed["ob_row"] = ob_row
            keep[str(ob_row)] = placed

    state["pending"] = keep
    return state


def place_pending(signal, settings, balance):
    """Rests one order. Returns the record to remember, or None."""
    spec = connector.symbol_trade_spec(MT5_SYMBOL)
    lots = risk.position_size(balance, signal["r_distance"], spec)
    if lots is None:
        alerts.send(
            "Signal skipped (%s): broker's minimum lot would risk more than %.0fx the "
            "intended 0.1%% given current balance" % (INSTRUMENT, risk.MAX_RISK_MULTIPLE_ON_MIN_LOT)
        )
        return None

    tp = tp_price_for(
        signal["order_price"], signal["direction"], signal["r_distance"],
        settings["tp_multiple"],
    )

    if DRY_RUN:
        alerts.send(
            "[DRY RUN] Would rest %s %s %s %.2f lots @ %.5f, SL %.5f, TP %.5f, "
            "total %.1f%% (HTF %.1f%%)"
            % (signal["entry_model"], signal["order_kind"], signal["direction"],
               lots, signal["order_price"], signal["sl"], tp,
               signal["total_probability"], signal["htf_probability"])
        )
        return None

    result = connector.send_pending_order(
        MT5_SYMBOL, signal["order_kind"], signal["direction"], lots,
        signal["order_price"], signal["sl"], tp,
        magic=MAGIC, comment="algo-m15-%s" % signal["entry_model"],
    )
    if not result["success"]:
        alerts.send(
            "PENDING ORDER FAILED (%s): retcode=%s comment=%s"
            % (INSTRUMENT, result["retcode"], result["comment"])
        )
        return None

    journal_live.append_open_trade(INSTRUMENT, signal, tp, result["ticket"], lots)
    alerts.send(
        "Order resting: %s %s %s %.2f lots @ %.5f, SL %.5f, TP %.5f, total %.1f%%"
        % (signal["entry_model"], signal["order_kind"], signal["direction"],
           lots, signal["order_price"], signal["sl"], tp,
           signal["total_probability"])
    )
    return {
        "ticket": result["ticket"],
        "price": signal["order_price"],
        "sl": signal["sl"],
        "model": signal["entry_model"],
    }


def main():
    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]
    server = os.environ["MT5_SERVER"]
    terminal_path = os.environ.get("MT5_TERMINAL_PATH")  # optional; set this if auto-detect can't find the running terminal

    connector.connect(login, password, server, terminal_path=terminal_path)
    alerts.send(
        "Live bot started for %s (symbol=%s, DRY_RUN=%s)" % (INSTRUMENT, MT5_SYMBOL, DRY_RUN)
    )

    settings = load_latest_settings(INSTRUMENT)
    weights = load_latest_weights(INSTRUMENT)
    state = safety.load_state(STATE_PATH)

    while True:
        try:
            state = run_once(state, settings, weights)
            safety.save_state(state, STATE_PATH)
            manage_open_trades()
            reconcile_closed_trades()
        except Exception as e:
            alerts.send("ERROR in live loop for %s: %s" % (INSTRUMENT, e))
            try:
                connector.disconnect()
            except Exception:
                pass
            time.sleep(POLL_SECONDS)
            try:
                connector.connect(login, password, server, terminal_path=terminal_path)
            except Exception as reconnect_error:
                alerts.send("Reconnect failed: %s" % reconnect_error)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
