"""Deciding what to place, keep and cancel. No MetaTrader5, on purpose.

Why this is its own module
--------------------------
MetaTrader5 is Windows-only, so nothing that imports it can be run or
tested on the machine this is developed on. That is tolerable for the thin
wrappers in mt5_connector.py, which are one dict and one order_send each
and fail loudly. It is not tolerable for the logic that decides WHICH
orders to place and cancel with real money, which is the part with actual
branching in it.

So the decision lives here as a pure function over plain dicts, and
run_live.py does nothing but execute the plan it returns.

Why the plan is recomputed from scratch every candle
---------------------------------------------------
The alternative is remembering the intended order and diffing forward.
That drifts out of step with the broker the first time a send fails or a
fill is missed, and it never recovers, because the remembered state and
the real state have no way to reconcile. Recomputing means the bot is
self-correcting: whatever happened last poll, this poll compares what
SHOULD rest against what DOES and fixes the difference.

Why replace rather than modify
------------------------------
An LC order re-hosting moves its price AND its stop together (both come
off the new host candle). A partial modify would leave a live order with
the wrong stop attached, which is the one failure here that costs money
rather than an opportunity. Cancel-and-replace is slower and cannot land
half-applied.
"""

# How far an order's price or stop may drift before it counts as a
# different order, in pips. Small but not zero: the broker rounds prices to
# the symbol's tick size, so a resting order read back can differ from the
# value sent by a fraction of a pip without anything having changed.
PRICE_TOLERANCE_PIPS = 0.1


def _same_order(record, signal, tolerance):
    """Is the resting order already the one we want?"""
    return (
        abs(float(record.get("price", 0.0)) - signal["order_price"]) <= tolerance
        and abs(float(record.get("sl", 0.0)) - signal["sl"]) <= tolerance
    )


def one_per_zone(signals):
    """The first taken signal per order block, in arrival order.

    The user's rule, and the same one backtest/settings.py's apply_settings
    enforces for the backtest. First come, not best of: picking the highest
    scorer would need the zone's whole life in hand, which the live bot
    never has.
    """
    wanted = {}
    for signal in signals:
        ob_row = signal.get("ob_row")
        if ob_row is None or ob_row in wanted:
            continue
        wanted[ob_row] = signal
    return wanted


def plan_pending(signals, resting, pip_size,
                 tolerance_pips=PRICE_TOLERANCE_PIPS):
    """(keep, cancel, place) for this candle.

    signals: taken candidates, chronological, each carrying ob_row,
        order_price and sl.
    resting: {str(ob_row): record}, the orders believed to be at the broker.
        Records carry ticket, price and sl.
    pip_size: the instrument's pip, for the drift tolerance.

    keep    {str(ob_row): record}   already correct, leave alone
    cancel  [record]                remove from the broker
    place   {ob_row: signal}        send

    An entry in `resting` whose key is not a zone we still want is
    cancelled, which covers the two cases that matter equally: the setup
    died, and the order re-hosted to a new price.
    """
    wanted = one_per_zone(signals)
    tolerance = tolerance_pips * pip_size

    keep = {}
    cancel = []
    for key, record in (resting or {}).items():
        signal = wanted.get(_as_ob_row(key))
        if signal is not None and _same_order(record, signal, tolerance):
            keep[key] = record
            wanted.pop(_as_ob_row(key), None)
            continue
        cancel.append(record)

    return keep, cancel, wanted


def rank_candidates(candidates, occupied, cap, max_per_instrument):
    """Decides which of this sweep's candidates may actually be placed,
    against the account's shared concurrency caps.

    candidates: this sweep's would-be orders, each a dict with "signal"
        (carrying "total_probability"), "instrument", and "is_rehost" -
        the shape run_live.py's run_once() returns.
    occupied: {instrument: trades already open or resting}, read from the
        broker AFTER this sweep's cancellations have gone through.
    cap: the account-wide ceiling (safety.MAX_CONCURRENT_TRADES).
    max_per_instrument: the per-instrument ceiling
        (safety.MAX_TRADES_PER_INSTRUMENT).

    Two ceilings, not one. The account-wide cap exists because N trades
    at 1% risk is N% of the account at once; the per-instrument cap
    exists because filling every slot from ONE symbol is a single
    directional bet wearing the costume of a diversified book, which is
    exactly what the account-wide cap was meant to prevent.

    Re-hosts go first, ahead of higher-scoring newcomers. A re-hosting
    order already HELD a slot at the start of the sweep and only vacated
    it because plan_pending replaces rather than modifies (see this
    module's docstring). Making it re-win that slot in open competition
    would let a newcomer evict a live setup, leaving the pair with
    nothing resting - strictly worse than before the sweep began, and
    repeatable every candle while the book is full. The backtest assumes
    a re-host always succeeds, so this keeps live behaviour matched to
    it. Within each group the order is by total_probability, highest
    first, ties keeping input order (Python's sort is stable), which is
    the caller's instrument order rather than anything randomized.

    Returns (winners, losers), each a list in the same shape as
    `candidates` - the caller places winners and alerts on losers.
    """
    by_probability = lambda c: c["signal"]["total_probability"]
    rehosts = sorted([c for c in candidates if c.get("is_rehost")],
                     key=by_probability, reverse=True)
    newcomers = sorted([c for c in candidates if not c.get("is_rehost")],
                       key=by_probability, reverse=True)

    per_instrument = dict(occupied)
    total = sum(per_instrument.values())

    winners, losers = [], []
    for candidate in rehosts + newcomers:
        instrument = candidate["instrument"]
        if total >= cap or per_instrument.get(instrument, 0) >= max_per_instrument:
            losers.append(candidate)
            continue
        winners.append(candidate)
        per_instrument[instrument] = per_instrument.get(instrument, 0) + 1
        total += 1
    return winners, losers


def _as_ob_row(key):
    """State keys come back from JSON as strings; ob_row is an int."""
    try:
        return int(key)
    except (TypeError, ValueError):
        return None
