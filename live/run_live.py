"""Live trading loop for EUR_USD, running the exact same signal logic
and trade management as backtest/ against live MT5 data, with real
order placement and real position management gated by the safety
guardrails in live/safety.py.

The strategy does not just set a stop-loss/take-profit and walk away:
backtest/simulate.py actively manages every open trade at 19:00 London
each day (move to breakeven if in profit, cut if not) and force-closes
everything by Friday 19:00 London. manage_open_trades() below runs that
exact same schedule against real open MT5 positions, so live behaviour
matches what was actually backtested.

Run on the Windows VPS (see live/README.md for setup). Configure via
environment variables - nothing sensitive is hardcoded:

    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER   - your MT5 account credentials
    MT5_SYMBOL                            - broker's exact symbol name (default EURUSD;
                                             some brokers suffix it, e.g. EURUSD.a)
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
from backtest.settings import is_taken, load_settings
from backtest.simulate import find_signals, tp_price_for
from backtest.weights import load_weights

from live import alerts, mt5_connector as connector, journal_live, risk, safety

INSTRUMENT = "EUR_USD"
MT5_SYMBOL = os.environ.get("MT5_SYMBOL", "EURUSD")
PIP_SIZE = pip_size_for(INSTRUMENT)
MAGIC = 20260808  # arbitrary fixed id tagging this bot's orders, distinct from any manual trades

DAILY_HISTORY_COUNT = 1000
H4_HISTORY_COUNT = 1500
H1_HISTORY_COUNT = 3000

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
    if safety.kill_switch_active():
        return state

    balance = connector.account_balance()
    state = safety.roll_daily_state(state, balance)

    if safety.daily_loss_breached(state, balance):
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

    # A full context, not a tail slice of the frame. Order-block positions
    # address the whole history, so slicing the frame afterwards would
    # mis-address every zone; build_live_context cuts the frame and rebases
    # the OB universe together.
    ctx = build_live_context(daily_df, h4_df, h1_df, PIP_SIZE)
    latest_candle_time = ctx.df["date"].iloc[-1]

    if not safety.is_new_candle(state, latest_candle_time):
        return state
    safety.mark_candle_processed(state, latest_candle_time)

    signals = find_signals(ctx, weights, PIP_SIZE)
    new_signals = [s for s in signals if s["entry_time"] == latest_candle_time]
    if not new_signals:
        return state

    signal = new_signals[0]

    if not is_taken(signal, settings, PIP_SIZE):
        alerts.send(
            "Signal skipped (%s): probability %.1f%%, SL %.1f pips did not clear the "
            "current settings (threshold=%s, max_sl_size_pips=%s)"
            % (INSTRUMENT, signal["probability"], signal["r_distance"] / PIP_SIZE,
               settings.get("threshold"), settings.get("max_sl_size_pips"))
        )
        return state

    spec = connector.symbol_trade_spec(MT5_SYMBOL)
    lots = risk.position_size(balance, signal["r_distance"], spec)
    if lots is None:
        alerts.send(
            "Signal skipped (%s): broker's minimum lot would risk more than %.0fx the "
            "intended 0.1%% given current balance" % (INSTRUMENT, risk.MAX_RISK_MULTIPLE_ON_MIN_LOT)
        )
        return state

    tp = tp_price_for(signal["entry_price"], signal["direction"], signal["r_distance"], settings["tp_multiple"])

    if DRY_RUN:
        alerts.send(
            "[DRY RUN] Would place %s %s %.2f lots @ %.5f, SL %.5f, TP %.5f, probability %.1f%%"
            % (signal["direction"], INSTRUMENT, lots, signal["entry_price"], signal["sl"], tp, signal["probability"])
        )
        return state

    result = connector.send_market_order(
        MT5_SYMBOL, signal["direction"], lots, signal["sl"], tp, magic=MAGIC, comment="algo-h1-ob"
    )
    if not result["success"]:
        alerts.send(
            "ORDER FAILED (%s): retcode=%s comment=%s" % (INSTRUMENT, result["retcode"], result["comment"])
        )
        return state

    journal_live.append_open_trade(INSTRUMENT, signal, tp, result["ticket"], lots)
    alerts.send(
        "Order placed: %s %s %.2f lots @ %.5f, SL %.5f, TP %.5f, probability %.1f%%"
        % (signal["direction"], INSTRUMENT, lots, signal["entry_price"], signal["sl"], tp, signal["probability"])
    )
    return state


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
