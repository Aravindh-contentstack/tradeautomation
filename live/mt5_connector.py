"""MetaTrader 5 connector: live candle fetch, account/symbol info, and
order placement.

Only runs on Windows - the official `MetaTrader5` pip package wraps the
MT5 terminal's native Windows API. Install and run this module (and
everything in live/) on the Windows VPS, not on the Mac used to write
this code; `import MetaTrader5` fails outside Windows.

Broker server time is NOT UTC: this broker confirmed (in writing, on
their trading-hours page) that their MT5 server runs on EET - UTC+2 in
winter, UTC+3 in summer, switching on the same calendar dates as the
rest of the EU (including Europe/London, which backtest/killzone.py
already relies on). Rather than a fixed offset that would need manual
updating twice a year - a real risk, since forgetting would silently
shift every killzone decision for weeks - BROKER_TZ below is looked up
per-candle via the standard IANA timezone database, so the correct
UTC+2/UTC+3 offset is always applied automatically, DST-transition
dates included.

If you ever switch brokers, re-verify this against the new broker's
own stated server timezone before running live - not every broker
uses EET.
"""

from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd
from zoneinfo import ZoneInfo

BROKER_TZ = ZoneInfo("Europe/Bucharest")  # EET/EEST, per this broker's stated server hours.

TIMEFRAMES = {
    "D": mt5.TIMEFRAME_D1,
    "H4": mt5.TIMEFRAME_H4,
    "H1": mt5.TIMEFRAME_H1,
    # The entry models detect structure and liquidity on M15, so the live
    # bot needs the frame itself now, not just intrabar resolution.
    "M15": mt5.TIMEFRAME_M15,
}

# Which MT5 order type a pending entry becomes, by (kind, direction).
#
# STOP for the LC models (price has to keep going our way to tag us) and
# LIMIT for CE (price has to come BACK to us). Getting these two swapped
# would place an order the broker fills instantly at the wrong side, so the
# mapping is a table rather than a pair of nested conditionals.
PENDING_ORDER_TYPES = {
    ("stop", "bullish"): mt5.ORDER_TYPE_BUY_STOP,
    ("stop", "bearish"): mt5.ORDER_TYPE_SELL_STOP,
    ("limit", "bullish"): mt5.ORDER_TYPE_BUY_LIMIT,
    ("limit", "bearish"): mt5.ORDER_TYPE_SELL_LIMIT,
}


def connect(login, password, server, terminal_path=None):
    # mt5.initialize() rejects an explicit path=None - it must be omitted
    # entirely (not passed as None) to fall back to auto-detecting the
    # already-installed/running terminal.
    kwargs = {"login": login, "password": password, "server": server}
    if terminal_path:
        kwargs["path"] = terminal_path
    ok = mt5.initialize(**kwargs)
    if not ok:
        raise RuntimeError("MT5 initialize failed: %s" % (mt5.last_error(),))
    return True


def disconnect():
    mt5.shutdown()


def _broker_epoch_to_utc(epoch_seconds):
    """MT5's epoch timestamp, read as UTC, actually just reprints the
    broker's own wall-clock reading (mislabeled) - so strip that
    incorrect UTC label, re-attach the REAL broker timezone (which
    resolves the correct UTC+2/UTC+3 offset for that specific date),
    then convert to true UTC.
    """
    broker_wall_clock = pd.Timestamp(epoch_seconds, unit="s", tz="UTC").tz_localize(None)
    return broker_wall_clock.tz_localize(BROKER_TZ, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC")


def fetch_closed_candles(symbol, granularity_key, count):
    """Returns a DataFrame (date, open, high, low, close), ascending,
    UTC-corrected - the same shape data/dukascopy_client.py produces for
    the backtest, so backtest/pipeline.py's merge logic needs no changes
    to accept it.

    start_pos=1 (not 0) always excludes the currently-forming candle:
    MT5's position 0 is the in-progress bar, so every row returned here
    is guaranteed fully closed.
    """
    timeframe = TIMEFRAMES[granularity_key]
    # copy_rates_from_pos silently fails for a symbol Market Watch hasn't
    # activated yet (e.g. a cross pair MT5 hasn't seen this terminal session) -
    # select it first, same guard symbol_trade_spec already applies before
    # placing an order.
    info = mt5.symbol_info(symbol)
    if info is not None and not info.visible:
        mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(
            "No candles returned for %s %s: %s" % (symbol, granularity_key, mt5.last_error())
        )

    df = pd.DataFrame(rates)
    broker_wall_clock = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_localize(None)
    df["date"] = (
        broker_wall_clock.dt.tz_localize(BROKER_TZ, ambiguous="NaT", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )
    df = df.dropna(subset=["date"])
    df = df[["date", "open", "high", "low", "close"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df


def account_balance():
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("account_info() failed: %s" % (mt5.last_error(),))
    return info.balance


def symbol_trade_spec(symbol):
    """Fields needed for accurate position sizing and order placement,
    read live from the broker rather than hardcoded, since tick
    value/size and volume limits vary per broker and per account
    currency.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError("symbol_info(%s) failed: %s" % (symbol, mt5.last_error()))
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return {
        "trade_tick_value": info.trade_tick_value,
        "trade_tick_size": info.trade_tick_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "digits": info.digits,
    }


def current_tick(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError("symbol_info_tick(%s) failed: %s" % (symbol, mt5.last_error()))
    return {"bid": tick.bid, "ask": tick.ask}


def send_market_order(symbol, direction, volume, sl, tp, deviation=20, magic=0, comment=""):
    """Sends an immediate market order. direction is "bullish" (buy) or
    "bearish" (sell), matching backtest/simulate.py's naming.

    Returns a plain dict (not the raw MT5 result object) so callers
    never need to import MetaTrader5 themselves just to read the
    outcome.
    """
    tick = current_tick(symbol)
    order_type = mt5.ORDER_TYPE_BUY if direction == "bullish" else mt5.ORDER_TYPE_SELL
    price = tick["ask"] if direction == "bullish" else tick["bid"]

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"success": False, "retcode": None, "comment": str(mt5.last_error()), "ticket": None}

    success = result.retcode == mt5.TRADE_RETCODE_DONE
    return {"success": success, "retcode": result.retcode, "comment": result.comment, "ticket": result.order}


def is_position_open(ticket):
    positions = mt5.positions_get(ticket=ticket)
    return positions is not None and len(positions) > 0


def get_position(ticket):
    """Live snapshot of an open position, or None once it's closed.

    `profit` is the broker's own floating P&L (money, spread-inclusive) -
    used directly as the "is this trade currently in profit" test for the
    daily breakeven/cut checkpoint, instead of recomputing an R-multiple
    from price: it's simpler and it's literally what the broker considers
    true, unlike a price-only R calculation.
    """
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return None
    p = positions[0]
    return {
        "volume": p.volume,
        "price_open": p.price_open,
        "sl": p.sl,
        "tp": p.tp,
        "profit": p.profit,
        "direction": "bullish" if p.type == mt5.POSITION_TYPE_BUY else "bearish",
    }


def modify_position_sl(ticket, symbol, new_sl, tp):
    """Moves a position's stop-loss (used for the breakeven move) without
    touching its take-profit.
    """
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": symbol,
        "sl": new_sl,
        "tp": tp,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"success": False, "retcode": None, "comment": str(mt5.last_error())}
    return {"success": result.retcode == mt5.TRADE_RETCODE_DONE, "retcode": result.retcode, "comment": result.comment}


def close_position_at_market(ticket, symbol, direction, volume, deviation=20, magic=0, comment=""):
    """Closes an open position immediately at market - used for the
    19:00 London cut and the Friday deadline force-close. The closing
    deal is the OPPOSITE type of the original position, referencing it
    via "position" so MT5 nets it off rather than opening a new one.
    """
    tick = current_tick(symbol)
    close_type = mt5.ORDER_TYPE_SELL if direction == "bullish" else mt5.ORDER_TYPE_BUY
    price = tick["bid"] if direction == "bullish" else tick["ask"]

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"success": False, "retcode": None, "comment": str(mt5.last_error())}
    return {"success": result.retcode == mt5.TRADE_RETCODE_DONE, "retcode": result.retcode, "comment": result.comment}


def floating_profit(magic):
    """Sum of unrealized profit across this magic number's currently open
    positions - used to fold a pair's own open exposure into its
    independent daily-loss check, since two positions with the same
    magic don't have separate 'accounts' to read a balance from.
    """
    positions = mt5.positions_get()
    if not positions:
        return 0.0
    return sum(p.profit for p in positions if p.magic == magic)


def today_realized_profit(magic, since_utc):
    """Sum of profit from this magic number's deals that closed a
    position (DEAL_ENTRY_OUT) since `since_utc` - the realized half of a
    pair's own today P&L, independent of what any other magic number on
    the same account did today.
    """
    deals = mt5.history_deals_get(since_utc, datetime.now(timezone.utc))
    if not deals:
        return 0.0
    return sum(d.profit for d in deals if d.magic == magic and d.entry == mt5.DEAL_ENTRY_OUT)


def get_closed_deal(ticket):
    """Returns the closing deal's price/profit/time for a position
    ticket once MT5 has recorded its close, or None if it's still open
    or MT5 hasn't recorded it yet.
    """
    deals = mt5.history_deals_get(position=ticket)
    if not deals:
        return None
    closing = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    if not closing:
        return None
    d = closing[-1]
    return {
        "price": d.price,
        "profit": d.profit,
        "time": _broker_epoch_to_utc(d.time),
    }


def send_pending_order(symbol, kind, direction, volume, price, sl, tp,
                       magic=0, comment=""):
    """Rests a stop or limit order at `price`. Returns the same dict shape
    as send_market_order.

    kind is "stop" or "limit", direction is "bullish" or "bearish", both
    matching backtest/entry_models.py's naming.

    This exists because the backtest ASSUMES a resting order. Its fill is
    the first M15 candle whose wick reaches the order price, which only a
    real pending order reproduces. Market-ordering once the fill candle
    closed would enter at that candle's close, which can be most of the way
    to the stop, and would quietly discard the precision the whole entry
    layer is for.
    """
    order_type = PENDING_ORDER_TYPES[(kind, direction)]
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "magic": magic,
        "comment": comment,
        # GTC, not a broker-side expiry. The N=2 window is measured in M15
        # candles and this bot cancels on its own clock, so handing the
        # broker a wall-clock expiry would be a second, disagreeing rule.
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"success": False, "retcode": None,
                "comment": str(mt5.last_error()), "ticket": None}
    success = result.retcode == mt5.TRADE_RETCODE_DONE
    return {"success": success, "retcode": result.retcode,
            "comment": result.comment, "ticket": result.order}


def cancel_pending_order(ticket):
    """Removes a resting order. True when it is gone, either way.

    A ticket that no longer exists counts as success: it either filled or
    was already cancelled, and both mean "there is nothing resting", which
    is what the caller was asking for.
    """
    if not pending_order_exists(ticket):
        return True
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    })
    if result is None:
        return False
    return result.retcode == mt5.TRADE_RETCODE_DONE


def pending_order_exists(ticket):
    orders = mt5.orders_get(ticket=ticket)
    return orders is not None and len(orders) > 0


def pending_orders(magic):
    """Every resting order this bot owns, as plain dicts."""
    orders = mt5.orders_get()
    if not orders:
        return []
    out = []
    for order in orders:
        if order.magic != magic:
            continue
        out.append({
            "ticket": order.ticket,
            "symbol": order.symbol,
            "type": order.type,
            "volume": order.volume_current,
            "price": order.price_open,
            "sl": order.sl,
            "tp": order.tp,
        })
    return out
