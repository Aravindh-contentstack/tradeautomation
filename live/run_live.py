"""Live trading loop, ONE process for every instrument in live/pairs.py,
sweeping through them in turn each cycle - running the exact same signal
logic and trade management as backtest/ against live MT5 data, with real
order placement and real position management gated by the safety
guardrails in live/safety.py.

This runs as a single process (not one process per pair) because a
small VPS (e.g. AWS t3.micro, 1GB RAM) can't comfortably hold 27+
separate Python interpreters each importing pandas/MetaTrader5 - a
single process pays that loading cost once and loops over the
instrument list instead, which also means adding more instruments later
(see live/pairs.py) is a config change here, not a new process to
manage. Each instrument still gets its own settings/weights/state/
journal/MT5 magic number, so nothing about the actual trading logic
changes versus the old one-process-per-pair design - only how the
polling loop is structured. A crash while handling one instrument is
caught and logged per-instrument, so one pair's bug doesn't stop the
sweep for the rest.

Entries are M15-precision (see roadmap/m15-entry-plan.md): HTF analysis
(Daily/H4/H1) finds an H1 order block mitigation and scores it against
htf_threshold, then a M15 scan looks for one of four entry models
(LC-1, LC-2A, LC-2B, CE) and scores the combined total_probability
against total_threshold before a pending stop/limit order is rested.
live/pending_plan.py decides WHAT should be resting; this file only
executes that plan against MT5.

The strategy does not just set a stop-loss/take-profit and walk away:
backtest/simulate.py actively manages every open trade at 19:00 London
each day (move to breakeven if in profit, cut if not) and force-closes
everything by Friday 19:00 London. manage_open_trades() below runs that
exact same schedule against real open MT5 positions, so live behaviour
matches what was actually backtested - including catching up on a
missed 19:00 check the moment the process is next running, since this
is meant to run 24/7 rather than being started/stopped by hand.

Run on the Windows VPS (see live/README.md for setup):

    python -u live/run_live.py                  # every pair in live/pairs.py
    python -u live/run_live.py EUR_USD GBP_USD   # just these, e.g. for a dry run

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
- Verify live/safety.py's MAX_DAILY_LOSS_PCT, MAX_ACCOUNT_DAILY_LOSS_PCT,
  MAX_CONCURRENT_TRADES, MAX_TRADES_PER_INSTRUMENT and DAILY_RESET_HOUR_UTC
  against your prop firm's actual rulebook. All five are risk ceilings, and
  all five are only as right as the rulebook they were read from.
- Check live/risk.py's RISK_PER_TRADE, which those ceilings are sized around:
  the concurrency cap only bounds the account's exposure at
  MAX_CONCURRENT_TRADES x RISK_PER_TRADE, so changing one means revisiting
  the other.
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
from live.pending_plan import plan_pending, rank_candidates

requested = sys.argv[1:] if len(sys.argv) > 1 else PAIRS
unknown = [i for i in requested if i not in PAIRS]
if unknown:
    sys.exit("Unknown instrument(s) %s - must be one of: %s" % (unknown, ", ".join(PAIRS)))
INSTRUMENTS = requested

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


def state_path(instrument):
    return os.path.join("live", "state", "%s_state.json" % instrument)


ACCOUNT_STATE_PATH = os.path.join("live", "state", "_account_state.json")

# Outstanding alerts, keyed by condition - see alert_once().
_ALERTS_SENT = {}


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
    print("[%s] Using settings from %s: %s" % (instrument, path, settings))
    return settings


def load_latest_weights(instrument):
    pattern = os.path.join("data", "weights", instrument, "%s_weights_*.csv" % instrument)
    path = _latest_year_file(pattern)
    if path is None:
        raise RuntimeError("No weights file found for %s at %s" % (instrument, pattern))
    print("[%s] Using weights from %s" % (instrument, path))
    return load_weights(path)


# Every instrument's broker symbol is its name with the underscore stripped
# (EUR_USD -> EURUSD), except copper: this repo calls it COPPER_USD (the pip
# size and data files use that key) while the broker lists it as CUCUSD, which
# is also how it appears in per-pair-trade-setttings.csv. MT5_SYMBOL_SUFFIX
# still applies on top of whatever this resolves to.
MT5_SYMBOL_OVERRIDES = {"COPPER_USD": "CUCUSD"}


def build_configs(instruments):
    suffix = os.environ.get("MT5_SYMBOL_SUFFIX", "")
    configs = {}
    for instrument in instruments:
        configs[instrument] = {
            "instrument": instrument,
            "mt5_symbol": MT5_SYMBOL_OVERRIDES.get(
                instrument, instrument.replace("_", "")) + suffix,
            "pip_size": pip_size_for(instrument),
            "magic": magic_for(instrument),  # distinct per pair, so many instruments on one account never collide
            "settings": load_latest_settings(instrument),
            "weights": load_latest_weights(instrument),
            "state": safety.load_state(state_path(instrument)),
        }
    return configs


def manage_open_trades(cfg):
    """Runs the SAME daily-checkpoint rule backtest/simulate.py walks
    through historically, but against real time and real open MT5
    positions: at 19:00 London, breakeven if in profit else cut; by
    Friday 19:00 London, force-close no matter what.
    """
    instrument = cfg["instrument"]
    mt5_symbol = cfg["mt5_symbol"]
    magic = cfg["magic"]

    now = datetime.now(timezone.utc)
    current_year = now.year

    for year in (current_year - 1, current_year):
        for trade in journal_live.open_trades(instrument, year):
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
                    ticket, mt5_symbol, position["direction"], position["volume"], magic=magic
                )
                if result["success"]:
                    journal_live.mark_pending_exit(instrument, year, ticket, "friday_close")
                    alerts.send("Force-closed ticket %s at the Friday deadline" % ticket)
                else:
                    alerts.send("FAILED to force-close ticket %s: %s" % (ticket, result["comment"]))
                continue

            if now < next_checkpoint:
                continue

            in_profit = position["profit"] > 0
            if in_profit:
                if not trade["be_moved"] and not DRY_RUN:
                    mod = connector.modify_position_sl(ticket, mt5_symbol, position["price_open"], position["tp"])
                    if mod["success"]:
                        journal_live.update_checkpoint_state(instrument, year, ticket, be_moved=True)
                        alerts.send("Moved ticket %s to breakeven at the 19:00 London checkpoint" % ticket)
                    else:
                        alerts.send("FAILED to move ticket %s to breakeven: %s" % (ticket, mod["comment"]))
                elif DRY_RUN and not trade["be_moved"]:
                    alerts.send("[DRY RUN] Would move ticket %s to breakeven" % ticket)

                next_ts = next_checkpoint
                while next_ts <= now:
                    next_ts = next_london_cutoff(next_ts)
                journal_live.update_checkpoint_state(instrument, year, ticket, next_checkpoint=next_ts)
            else:
                if DRY_RUN:
                    alerts.send("[DRY RUN] Would cut ticket %s at the 19:00 London checkpoint (not in profit)" % ticket)
                    continue
                result = connector.close_position_at_market(
                    ticket, mt5_symbol, position["direction"], position["volume"], magic=magic
                )
                if result["success"]:
                    journal_live.mark_pending_exit(instrument, year, ticket, "cut_19h")
                    alerts.send("Cut ticket %s at the 19:00 London checkpoint (not in profit)" % ticket)
                else:
                    alerts.send("FAILED to cut ticket %s: %s" % (ticket, result["comment"]))


def reconcile_closed_trades(cfg):
    """Catches trades MT5 closed on its own (a native SL or TP fill) -
    our own management-driven closes are already journalled the moment
    they happen, in manage_open_trades().
    """
    instrument = cfg["instrument"]
    current_year = datetime.now(timezone.utc).year
    for year in (current_year - 1, current_year):
        for trade in journal_live.open_trades(instrument, year):
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
            journal_live.close_trade(instrument, year, ticket, result, deal["time"], exit_reason)
            alerts.send(
                "Trade closed: ticket %s result=%s profit=%.2f reason=%s" % (ticket, result, deal["profit"], exit_reason)
            )


def flatten_pending(cfg, state, reason):
    """Cancels every order this instrument has resting at the broker.

    Called when a breaker trips. Returning early without this would make
    "pausing new entries" a false promise: an order already resting at the
    broker becomes a position the moment price reaches it, with the bot
    never consulted. At 0.2% risk per trade that was untidy; at 1% it means
    the account can keep taking on full-size trades after the breaker said
    stop. Reuses reconcile_pending_orders' own cancel path (no signals ->
    plan_pending cancels everything) rather than a second, separate
    cancellation routine that could drift out of step with it.
    """
    before = len(state.get("pending") or {})
    if not before:
        return state
    if DRY_RUN:
        alert_once(
            "flatten:%s" % cfg["instrument"],
            "[DRY RUN] Would cancel %d resting order(s) for %s (%s)"
            % (before, cfg["instrument"], reason),
        )
        return state

    state, _place, _rehosted = reconcile_pending_orders(cfg, state, [])

    # Reported AFTER the fact, and only for orders actually removed. An
    # announcement before the attempt would repeat every poll for as long as
    # the broker kept refusing a cancel, which is precisely the condition
    # under which the alert log must stay readable.
    cancelled = before - len(state.get("pending") or {})
    if cancelled:
        alerts.send(
            "Cancelled %d resting order(s) for %s (%s)"
            % (cancelled, cfg["instrument"], reason)
        )
    return state


def run_once(cfg, balance, pair_pnl_today, account_breached=False):
    """Decides this instrument's orders. Returns (state, candidates).

    Nothing is PLACED here. New orders come back as candidates for main()
    to rank against every other instrument's, because the concurrency cap
    is an account-wide budget and a per-pair decision cannot see it.
    Cancellations do happen here, immediately: removing an order only
    reduces risk, so it never has to wait its turn.

    `balance` and `pair_pnl_today` are handed in already read, once for
    the whole sweep, rather than fetched per instrument - see
    connector.pnl_by_magic.
    """
    instrument = cfg["instrument"]
    mt5_symbol = cfg["mt5_symbol"]
    pip_size = cfg["pip_size"]
    settings = cfg["settings"]
    weights = cfg["weights"]
    state = cfg["state"]

    if safety.kill_switch_active(instrument):
        return state, []

    if account_breached:
        return flatten_pending(cfg, state, "account-wide daily loss breaker"), []

    # The breaker is checked BEFORE the cheap-candle early-out below, and
    # deliberately so. A tripped breaker has to pull its resting orders on
    # the poll it trips, not on the next M15 boundary: a resting order
    # becomes a position the moment price reaches it, so 15 more minutes of
    # full-size orders resting would defeat the whole point of pausing.
    # Checking every poll is affordable now only because the P&L for every
    # magic arrives in one broker read per sweep.
    state = safety.roll_daily_state(state, balance)
    if safety.daily_loss_breached(state, pair_pnl_today):
        if not state.get("breach_alerted"):
            alerts.send(
                "Daily loss breaker tripped for %s (down %.2f%% of day-start balance). "
                "Pausing new entries until the next trading day." % (instrument, safety.MAX_DAILY_LOSS_PCT * 100)
            )
            state["breach_alerted"] = True
        return flatten_pending(cfg, state, "%s daily loss breaker" % instrument), []

    # Cheap pre-check: a 2-candle M15 fetch is enough to tell whether the
    # clock has actually moved. Going straight to the full D/H4/H1/M15
    # fetch and pipeline rebuild below on every 15-second poll - even the
    # 59 out of every 60 where the M15 candle hasn't closed yet - is what
    # pushed a 27-pair sweep past a minute of work per cycle on the VPS.
    # This does not decide anything itself; the real is_new_candle check
    # further down (against the fully built context) stays the source of
    # truth and is what actually marks the candle processed. If the two
    # ever briefly disagree, the worst case is skipping one poll's early
    # exit - the next cycle re-checks and self-corrects.
    tail = connector.fetch_closed_candles(mt5_symbol, "M15", 2)
    if not safety.is_new_candle(state, tail["date"].iloc[-1]):
        return state, []

    daily_df = connector.fetch_closed_candles(mt5_symbol, "D", DAILY_HISTORY_COUNT)
    h4_df = connector.fetch_closed_candles(mt5_symbol, "H4", H4_HISTORY_COUNT)
    h1_df = connector.fetch_closed_candles(mt5_symbol, "H1", H1_HISTORY_COUNT)
    m15_df = connector.fetch_closed_candles(mt5_symbol, "M15", M15_HISTORY_COUNT)

    # A full context, not a tail slice of the frame. Order-block positions
    # address the whole history, so slicing the frame afterwards would
    # mis-address every zone; build_live_context cuts the frame and rebases
    # the OB universe together. The M15 bundle is passed through UNSLICED,
    # for the reason in backtest/m15_pipeline.py.
    ctx = build_live_context(daily_df, h4_df, h1_df, pip_size, m15_df=m15_df)
    if ctx.m15_bundle is None:
        return state, []

    # The clock is M15 now, not H1. Setups form and orders re-host on M15
    # boundaries, so waking only on a new H1 candle would miss three
    # quarters of every decision and place orders up to 45 minutes stale.
    as_of = len(ctx.m15_bundle.ts) - 1
    latest_candle_time = pd.Timestamp(ctx.m15_bundle.ts[as_of], tz="UTC")

    if not safety.is_new_candle(state, latest_candle_time):
        return state, []
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
            ctx, weights, pip_size,
            htf_threshold=settings.get("htf_threshold"),
            pending_as_of=as_of,
            allowed_sessions=settings.get("allowed_sessions"),
        )
    state, place_candidates, rehosted = reconcile_pending_orders(cfg, state, signals)
    candidates = [
        {
            "cfg": cfg,
            "instrument": instrument,
            "ob_row": ob_row,
            "signal": signal,
            "is_rehost": ob_row in rehosted,
        }
        for ob_row, signal in place_candidates.items()
    ]
    return state, candidates


def reconcile_pending_orders(cfg, state, signals):
    """Cancels whatever should no longer be resting, and returns the
    candidates that WOULD be newly placed - execution of those is
    deferred to the caller (main()'s second pass over every instrument),
    which ranks all of this sweep's candidates together against the
    shared safety.MAX_CONCURRENT_TRADES cap before any of them are
    actually sent. Cancelling happens here immediately regardless of
    that cap: removing a stale or re-hosted order only ever reduces
    risk, so there is no reason to make it wait its turn.

    The decision of WHAT should be resting is live/pending_plan.py's,
    deliberately: MetaTrader5 is Windows-only, so anything importing it
    cannot be tested where this is written, and order placement is the
    last logic that should go untested. This function only EXECUTES the
    cancel half of the plan; the caller executes the place half via
    place_pending(), once ranking has decided which candidates win.
    """
    instrument = cfg["instrument"]
    settings = cfg["settings"]
    pip_size = cfg["pip_size"]

    resting = state.get("pending") or {}
    # Which zones already had an order resting BEFORE this sweep. A zone in
    # both this set and `place` is re-hosting rather than arriving new, and
    # rank_candidates gives it back the slot it just vacated.
    previously_resting = {
        record["ob_row"] for record in resting.values() if record.get("ob_row") is not None
    }

    taken = [s for s in signals if is_taken(s, settings, pip_size)]
    keep, cancel, place = plan_pending(taken, resting, pip_size)

    uncancelled_rows = set()
    for record in cancel:
        if DRY_RUN:
            continue
        if not connector.pending_order_exists(record["ticket"]):
            # Already gone, but NOT necessarily cancelled - the same ticket
            # is also the position ticket, so this is equally what a FILLED
            # order looks like. Nothing is journalled here; reconcile_closed_
            # trades owns that case and can tell the difference.
            continue
        if not connector.cancel_pending_order(record["ticket"]):
            # Kept so the next poll tries again. Forgetting an order that
            # is still live at the broker is the one outcome here that can
            # leave an unmanaged position. Keyed by TICKET, not ob_row: a
            # re-host shares its predecessor's ob_row, so keying by that
            # would let the replacement order's record overwrite this one
            # and strand a live order nothing is tracking. The "stuck-"
            # prefix keeps the key un-parseable as an ob_row, so the next
            # sweep sees it as unwanted and retries the cancel.
            keep["stuck-%s" % record["ticket"]] = record
            uncancelled_rows.add(record.get("ob_row"))
            # Deduped: a record that stays stuck is retried on every poll,
            # so an undeduped alert here would repeat for as long as the
            # problem lasts.
            alert_once("stuck:%s" % record["ticket"],
                       "Could not cancel pending order %s (%s) - will keep retrying"
                       % (record["ticket"], instrument))
            continue
        clear_alert("stuck:%s" % record["ticket"])

        # Both halves observed: it WAS resting, and removing it worked. That
        # is the only place this can be said without inferring, so it is the
        # only place the journal learns an order never became a trade.
        current_year = datetime.now(timezone.utc).year
        for year in (current_year - 1, current_year):
            journal_live.mark_cancelled(instrument, year, record["ticket"])

    # Never replace an order that is still resting because its cancel
    # failed: both would sit on the same zone, doubling that zone's risk.
    place = {row: signal for row, signal in place.items() if row not in uncancelled_rows}

    state["pending"] = keep
    rehosted = {row for row in place if row in previously_resting}
    return state, place, rehosted


def place_pending(cfg, signal, balance):
    """Rests one order. Returns the record to remember, or None."""
    instrument = cfg["instrument"]
    mt5_symbol = cfg["mt5_symbol"]
    magic = cfg["magic"]
    settings = cfg["settings"]

    spec = connector.symbol_trade_spec(mt5_symbol)
    lots = risk.position_size(balance, signal["r_distance"], spec)
    if lots is None:
        alerts.send(
            "Signal skipped (%s): broker's minimum lot would risk more than %.0fx the "
            "intended %.2f%% given current balance"
            % (instrument, risk.MAX_RISK_MULTIPLE_ON_MIN_LOT, risk.RISK_PER_TRADE * 100)
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
        mt5_symbol, signal["order_kind"], signal["direction"], lots,
        signal["order_price"], signal["sl"], tp,
        magic=magic, comment="algo-m15-%s" % signal["entry_model"],
    )
    if not result["success"]:
        alerts.send(
            "PENDING ORDER FAILED (%s): retcode=%s comment=%s"
            % (instrument, result["retcode"], result["comment"])
        )
        return None

    journal_live.append_open_trade(instrument, signal, tp, result["ticket"], lots)
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


def alert_once(key, message):
    """Sends `message` only if it isn't the one already outstanding under
    `key`. Call clear_alert(key) when the condition ends, so a recurrence
    is reported again.

    Every repeating alert in this loop needs this. The loop runs every 15
    seconds forever, so an alert on a persistent condition - a symbol that
    keeps failing, a cancel the broker keeps refusing, a candidate the cap
    keeps holding back - is not one message but roughly six thousand a day
    into a log file. That is what filled the VPS disk once already.

    Module-level rather than threaded through every caller, because the
    conditions worth deduplicating are found several layers down (inside
    reconcile_pending_orders) and passing a dict through those signatures
    would only obscure what they do.
    """
    if _ALERTS_SENT.get(key) != message:
        alerts.send(message)
        _ALERTS_SENT[key] = message


def clear_alert(key):
    """Marks the condition behind `key` as over. Returns True if an alert
    was actually outstanding, so callers can announce the recovery.
    """
    return _ALERTS_SENT.pop(key, None) is not None


def reconnect(login, password, server, terminal_path):
    """Drops and reopens the MT5 connection after an error."""
    try:
        connector.disconnect()
    except Exception:
        pass
    try:
        connector.connect(login, password, server, terminal_path=terminal_path)
    except Exception as reconnect_error:
        alerts.send("Reconnect failed: %s" % reconnect_error)


def main():
    login = int(os.environ["MT5_LOGIN"])
    password = os.environ["MT5_PASSWORD"]
    server = os.environ["MT5_SERVER"]
    terminal_path = os.environ.get("MT5_TERMINAL_PATH")  # optional; set this if auto-detect can't find the running terminal

    connector.connect(login, password, server, terminal_path=terminal_path)

    configs = build_configs(INSTRUMENTS)
    account_state = safety.load_state(ACCOUNT_STATE_PATH)
    # Every magic the bot owns, not just this process's instruments: an
    # order resting on a pair this run isn't sweeping is still this bot's
    # order and still consumes a concurrency slot.
    owned_magics = [magic_for(pair) for pair in PAIRS]
    alerts.send(
        "Live bot started for %d pair(s): %s (DRY_RUN=%s)" % (len(configs), ", ".join(configs), DRY_RUN)
    )

    while True:
        # Read once for the whole sweep: the balance every pair sizes off,
        # and every magic's P&L. Both feed the breakers below AND the
        # per-pair breaker inside run_once, which is why they can no longer
        # be fetched per instrument.
        #
        # Guarded, because account_balance() raises on exactly the case
        # that matters - a dropped terminal connection. Unguarded, that
        # exception would escape the loop entirely and take the process
        # with it, above the reconnect that exists to recover from it.
        try:
            balance = connector.account_balance()
            day_start_utc = safety.trading_day_start_utc(datetime.now(timezone.utc))
            pnl = connector.pnl_by_magic(day_start_utc)
            if clear_alert("sweep"):
                alerts.send("Recovered: the account is readable again")
        except Exception as e:
            alert_once("sweep", "ERROR reading the account: %s" % e)
            reconnect(login, password, server, terminal_path)
            time.sleep(POLL_SECONDS)
            continue

        # The account-wide breaker, across every pair combined - the
        # per-pair check inside run_once only ever sees its own magic
        # number, so it cannot catch several correlated pairs stopping out
        # on the same day and adding up to real account damage. See
        # safety.MAX_ACCOUNT_DAILY_LOSS_PCT.
        account_state = safety.roll_daily_state(account_state, balance)
        account_breached = safety.daily_loss_breached(
            account_state, sum(pnl.values()), threshold=safety.MAX_ACCOUNT_DAILY_LOSS_PCT
        )
        if account_breached and not account_state.get("breach_alerted"):
            alerts.send(
                "ACCOUNT-WIDE daily loss breaker tripped (down %.2f%% of day-start balance across every pair). "
                "Pausing new entries for every instrument until the next trading day."
                % (safety.MAX_ACCOUNT_DAILY_LOSS_PCT * 100)
            )
            account_state["breach_alerted"] = True
        safety.save_state(account_state, ACCOUNT_STATE_PATH)

        # Pass 1: every instrument decides what it WANTS resting (cancels
        # for dead/re-hosted setups execute immediately inside run_once -
        # see reconcile_pending_orders), but new orders are only collected
        # as candidates here, not sent yet. Placing them per-pair as each
        # was found is what let a low-probability pair grab a concurrency
        # slot before a higher-probability one elsewhere in the sweep even
        # got evaluated - ranking needs every pair's candidates in hand
        # first.
        sweep_candidates = []
        for instrument, cfg in configs.items():
            try:
                cfg["state"], candidates = run_once(
                    cfg, balance, pnl.get(cfg["magic"], 0.0), account_breached=account_breached
                )
                sweep_candidates.extend(candidates)
                safety.save_state(cfg["state"], state_path(instrument))
                manage_open_trades(cfg)
                reconcile_closed_trades(cfg)
                if clear_alert("pair:%s" % instrument):
                    alerts.send("Recovered: %s live loop is running again" % instrument)
            except Exception as e:
                alert_once("pair:%s" % instrument,
                           "ERROR in live loop for %s: %s" % (instrument, e))
                reconnect(login, password, server, terminal_path)
                time.sleep(POLL_SECONDS)

        # Pass 2: rank this sweep's candidates against every OTHER pair's,
        # and against whatever is already open or resting on the account,
        # so the concurrency caps are enforced as one shared budget rather
        # than per pair.
        if sweep_candidates:
            try:
                counts = connector.open_and_pending_by_magic(owned_magics)
                occupied = {cfg["instrument"]: counts.get(cfg["magic"], 0)
                            for cfg in configs.values()}
                winners, losers = rank_candidates(
                    sweep_candidates, occupied,
                    safety.MAX_CONCURRENT_TRADES, safety.MAX_TRADES_PER_INSTRUMENT,
                )
                if clear_alert("ranking"):
                    alerts.send("Recovered: ranking is running again")
            except Exception as e:
                # Deliberately places nothing. The cap cannot be honoured
                # without knowing what is already open, and placing blind
                # is how the account ends up over its risk budget.
                alert_once("ranking",
                           "ERROR ranking candidates, placing nothing this sweep: %s" % e)
                reconnect(login, password, server, terminal_path)
                winners, losers = [], []

            for candidate in losers:
                # No probability in the text, deliberately: it moves every
                # candle, and a message that changes every candle cannot be
                # deduplicated. At cap with 27 pairs this would otherwise be
                # thousands of alerts a day.
                alert_once(
                    "capped:%s" % candidate["instrument"],
                    "Signal held back (%s): at the %d-trade cap (max %d per instrument)"
                    % (candidate["instrument"], safety.MAX_CONCURRENT_TRADES,
                       safety.MAX_TRADES_PER_INSTRUMENT),
                )
            for candidate in winners:
                clear_alert("capped:%s" % candidate["instrument"])

            for candidate in winners:
                cfg = candidate["cfg"]
                try:
                    # Sized off THIS sweep's balance, not one captured
                    # per-pair earlier in the sweep: at 1% risk a stale
                    # balance sizes every trade off a pre-drawdown number,
                    # which is the wrong direction on a losing day.
                    placed = place_pending(cfg, candidate["signal"], balance)
                except Exception as e:
                    # Per candidate, so one unknown symbol cannot silently
                    # drop the rest of the batch.
                    alert_once("place:%s" % cfg["instrument"],
                               "ERROR placing order for %s: %s" % (cfg["instrument"], e))
                    continue
                clear_alert("place:%s" % cfg["instrument"])
                if placed is not None:
                    placed["ob_row"] = candidate["ob_row"]
                    cfg["state"].setdefault("pending", {})[str(candidate["ob_row"])] = placed
                    safety.save_state(cfg["state"], state_path(cfg["instrument"]))

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
