"""Signal detection and per-trade outcome simulation.

Entry trigger: price taking a qualifying (progressively deeper) touch of
a valid H1 order block, inside a killzone or the hour before one. The
trade's direction is the touched zone's own direction, so nothing is
assumed about which way the market is going until it touches something.
See backtest/entry_ob.py for the mechanics, and the module docstring
there for what this replaced (an H1 fractal break, which fixed the
direction before any zone was involved).

The walk carries NO take-profit
-------------------------------
simulate_trade answers one question: if this trade had no TP at all,
when and at what R would trade MANAGEMENT have closed it? Take-profit is
applied afterwards, by apply_tp.

That split is not a stylistic choice. Every management rule the user
actually trades -- the stop, the 19:00 breakeven trigger, the 19:00 loss
cut, the Friday deadline, the mid-trade probability recheck that can also
move the stop to breakeven -- is a function of price, the clock, and the
OB universe ALONE. None of them consults the TP. So a single TP-free walk
traces the unique path that every TP variant shares, and

    realised_r = tp_multiple  if max_r_reached >= tp_multiple
                 else terminal_r

is EXACT for every TP, not an approximation. That is what lets
backtest/analysis.py grid-search six TP multiples for free instead of
re-walking (~2.9M extra bar-visits).

Two things had to change for the property to hold, and both are in this
module:

* max_r_reached is now the max favourable R reached STRICTLY BEFORE the
  terminal event. The old code updated it before checking the stop, so
  the stop-out candle's favourable spike was credited -- a spike that,
  on that candle, may never have been reachable. Under the old
  definition the biconditional above is simply false.
* The SLB rescue walk is gone (_slb_walk, SLB_MAX_PIPS, and the journal's
  slb column). It took the TP as an argument, which made the terminal
  outcome TP-dependent and broke the independence outright. Per the
  user's decision, a stop touch is now a clean -1.0R. What survives is
  sl_excursion_pips, a pure diagnostic recording how far past the stop
  the terminal bar traded, so the question "would a tolerance band have
  helped?" can still be asked of the data later.

Do not introduce any TP dependence into the walk.
"""

import numpy as np
import pandas as pd

from backtest.context import bar_timestamp
from backtest.entry_ob import (
    TARGET_SEARCH_R,
    WEEKLY_TARGET_SEARCH_R,
    build_setup,
    iter_mitigation_candidates,
    resolve_entry_bar,
)
from backtest.factors import (
    ALL_FACTORS,
    compute_probability,
    evaluate_always_factors,
    evaluate_liquidity_target_factors,
    evaluate_mitigation_ob_factors,
    evaluate_ob_target_factors,
    evaluate_swept_liquidity_factors,
)
from backtest.killzone import (
    friday_cutoff_for,
    london_cutoff_for,
    next_london_cutoff,
    session_for,
)

# Exit reasons. Shared contract: analysis.py, journal.py and the tests
# all key off these exact strings.
EXIT_TP = "tp"                     # take-profit reached
EXIT_SL = "sl"                     # stop hit                    -> -1.0R
EXIT_BE_STOP = "be_stop"           # stop-at-entry hit after BE  ->  0.0R
EXIT_CUT_19H = "cut_19h"           # 19:00 London loss cut       -> fractional, <= 0
EXIT_FRIDAY_CLOSE = "friday_close"  # Friday 19:00 deadline      -> any sign
EXIT_DATA_END = "data_end"         # data exhausted

FIXED_TP_MULTIPLE = 2.5

# Reporting clamp ONLY, applied with a min() at return. It used to break
# the loop, which now would fabricate an exit with no reason attached to
# it. The Friday deadline already bounds the walk at ~120 bars, so the
# runaway guard it once provided is no longer needed.
MAX_R_CEILING = 10.0


def find_signals(ctx, weights, pip_size):
    """Returns a list of signal dicts: every qualifying touch of a valid
    H1 order block that also passes the killzone gate, scored with the
    CURRENT weights table. No probability threshold and no max-SL filter
    is applied here: those are decided later, by the prior year's settings
    (backtest/settings.py).

    That separation is load-bearing, not incidental. If this function
    pre-filtered, each year's journal would contain only trades that
    passed the PREVIOUS year's filters, so the settings search would see
    an already-filtered pool and could only ever recommend a stricter
    one. Thresholds would ratchet upward every year until the pool
    starved. Every candidate is returned; `taken` records whether the
    settings admitted it.

    The trade's DIRECTION is the mitigated zone's direction, decided by
    what price touched rather than assumed beforehand. The three H1 gates
    that used to reject candidates outright (internal structure, internal
    zone, fractal structure) are scored factors now, because the mitigated
    zone can legitimately disagree with them.

    pip_size places the stop SL_BUFFER_PIPS beyond the zone's far edge
    rather than exactly on it.

    Requires a MarketContext carrying OB state (backtest/context.py). A
    context without it yields nothing, which is what a caller that never
    built the universe should get.
    """
    if getattr(ctx, "obs", None) is None:
        return []

    signals = []
    for k, ob_row, touch_no in iter_mitigation_candidates(ctx):
        entry_index = resolve_entry_bar(ctx, k)
        if entry_index is None:
            continue

        setup = build_setup(ctx, ob_row, entry_index, pip_size)
        if setup is None:
            continue

        entry_time = bar_timestamp(ctx, entry_index)
        session = session_for(entry_time)
        if session is None:
            continue

        direction = setup["direction"]
        row = ctx.df.iloc[entry_index]
        # Both frozen gates in one dict: what the zone was, and what price
        # had already taken on its way in. Neither changes once the trade is
        # open, so simulate_trade rebuilds its live score on top of this
        # rather than re-deriving it every bar.
        mitigation_factor_results = evaluate_mitigation_ob_factors(
            ctx.obs, entry_index, direction, ob_row
        )
        mitigation_factor_results.update(
            evaluate_swept_liquidity_factors(
                getattr(ctx, "liq", None), entry_index, direction
            )
        )

        factor_results = dict(evaluate_always_factors(row, direction))
        factor_results.update(mitigation_factor_results)
        factor_results.update(
            evaluate_ob_target_factors(
                ctx.obs,
                entry_index,
                direction,
                TARGET_SEARCH_R * setup["r_distance"],
                float(ctx.high[entry_index]),
                float(ctx.low[entry_index]),
            )
        )
        factor_results.update(
            evaluate_liquidity_target_factors(
                getattr(ctx, "liq", None),
                entry_index,
                direction,
                float(ctx.high[entry_index]),
                float(ctx.low[entry_index]),
                TARGET_SEARCH_R * setup["r_distance"],
                WEEKLY_TARGET_SEARCH_R * setup["r_distance"],
            )
        )
        probability = compute_probability(factor_results, weights)

        signals.append({
            # The ENTRY bar, not the mitigation bar: simulate_trade walks
            # from idx + 1, so a deferred entry that reported its trigger
            # bar here would re-walk the deferral gap as if it were part
            # of the trade.
            "idx": entry_index,
            "direction": direction,
            "entry_time": entry_time,
            "entry_price": setup["entry_price"],
            "sl": setup["sl"],
            "r_distance": setup["r_distance"],
            "session": session,
            "probability": probability,
            "factor_results": factor_results,
            "ob_row": ob_row,
            "ob_touch_no": touch_no,
            "ob_top": float(ctx.obs.series["H1"].top[ob_row]),
            "ob_bottom": float(ctx.obs.series["H1"].bottom[ob_row]),
            "mitigation_idx": k,
            "mitigation_time": bar_timestamp(ctx, k),
            "entry_deferred": entry_index != k,
            "excluded_gates": sorted(set(ALL_FACTORS) - set(factor_results)),
            # Frozen for the life of the trade, handed unchanged to
            # simulate_trade so the mid-trade probability recheck can rebuild
            # a live score without re-deriving the entry's own Mitigation OB.
            "mitigation_factor_results": mitigation_factor_results,
        })

    return signals


def tp_price_for(entry_price, direction, r_distance, tp_multiple):
    """The absolute price of a TP at tp_multiple R. Single definition:
    this formula was previously spelled out in three separate places.
    """
    if direction == "bearish":
        return entry_price - tp_multiple * r_distance
    return entry_price + tp_multiple * r_distance


def _naive_utc(ts):
    """tz-aware pd.Timestamp -> datetime64[ns], so it can be compared
    against MarketContext.ts as a plain integer.
    """
    return np.datetime64(ts.tz_convert("UTC").tz_localize(None))


def _intrabar_favourable_credit(ctx, k, sign, entry_price, stop, r_distance):
    """Re-derives how much favourable excursion H1 bar k really offered
    BEFORE its stop touch, by walking that hour's M15 sub-bars in order.

    Sub-bars are accumulated until the one that touches the stop, and
    that sub-bar contributes NOTHING: once the stop is touched the trade
    is closed, so anything after (or during) that quarter-hour was never
    available to us.

    Returns (credit_in_R, resolved). With no M15 the credit is ZERO --
    deliberately the pessimistic branch. The old engine credited the
    whole bar, which is precisely defect 2.
    """
    if ctx.m15 is None:
        return 0.0, False
    sub = ctx.m15.subbars(k)
    if sub is None:
        return 0.0, False

    _, _, sub_high, sub_low, _ = sub
    credit = 0.0
    for i in range(len(sub_high)):
        favourable_extreme = sub_high[i] if sign > 0 else sub_low[i]
        adverse_extreme = sub_low[i] if sign > 0 else sub_high[i]

        if sign * (adverse_extreme - stop) <= 0.0:
            break

        r = sign * (favourable_extreme - entry_price) / r_distance
        if r > credit:
            credit = r

    return credit, True


def simulate_trade(
    ctx, idx, direction, entry_price, sl, r_distance, tp_levels=None,
    weights=None, threshold=None, mitigation_factor_results=None,
):
    """Walks forward from the bar AFTER idx under the user's real trade
    management, with no take-profit (see the module docstring).

    The state machine is OPEN_INITIAL -> OPEN_BE -> CLOSED. Each bar is
    checked in exactly this precedence, and the order is part of the
    shared contract:

      1. WEEK DEADLINE. Friday 19:00 London is hard. Close at this bar's
         OPEN, EXIT_FRIDAY_CLOSE, at whatever R that is -- winning or
         losing. Nothing is ever carried over a weekend.
      2. DAILY CHECKPOINT. At 19:00 London, read the unrealised R at this
         bar's OPEN. In profit -> move the stop to entry (once). Not in
         profit -> cut, EXIT_CUT_19H, at that fractional R.
      3. LIVE PROBABILITY CHECK. Rebuilds the trade's probability every
         bar: the Mitigation OB answers stay exactly as frozen at entry,
         but the Always (structure/zone) and Target OB answers are
         re-evaluated live off this bar, using the same frozen weights
         and the same threshold that admitted the trade in the first
         place. The first bar this drops below threshold -> move the
         stop to entry (once). Skipped entirely once already at
         breakeven, and skipped whenever `weights`, `threshold`,
         `mitigation_factor_results`, or `ctx.obs` is None, which keeps
         every caller that omits them (including every existing test)
         running exactly as before.
      4. FAVOURABLE. Update the running max R and any TP touches,
         provisionally.
      5. STOP. Active stop touched -> EXIT_SL (-1.0R) before breakeven,
         EXIT_BE_STOP (0.0R) after.
      6. ATTRIBUTION. If step 5 fired, DISCARD step 4's credit for this
         bar and re-derive it from M15 sub-bars.

    Steps 1 and 2 fire at the bar OPEN, so they strictly precede any
    intrabar move. That is not an arbitrary tie-break: verified against
    EUR_USD_H1.parquet, 19:00 London always lands on an exact H1
    boundary, so the checkpoint instant IS a bar open. Using close would
    be lookahead; using the previous bar's close would be stale.

    Deliberate edge-case choices:

    * Exactly FLAT at the 19:00 checkpoint (unrealised R == 0.0) counts
      as NOT in profit, so the trade is cut rather than moved to
      breakeven. A scratch costs nothing and freeing the slot is worth
      more than a coin-flip; the user's rule is "in profit", and flat is
      not in profit.
    * Breakeven is moved ONCE and is then idempotent. Later checkpoints
      never trail it further (explicitly out of scope).
    * The 19:00 bar is genuinely absent on 19 weekdays of the EUR_USD
      history (Christmas, New Year, 2004-05-31), two of them Fridays. So
      the checkpoint fires at the first bar AT OR AFTER the cutoff
      instant, not on an exact timestamp match.
    * Week-rollover guard: a bar whose own London date is already past
      the deadline Friday force-closes as EXIT_FRIDAY_CLOSE even if the
      Friday bars themselves are missing from the feed. Redundant with
      the timestamp test on clean data, kept so the no-weekend
      guarantee holds regardless of feed holes.
    * A checkpoint that finds an already-breakeven trade at or below
      entry cuts it as EXIT_CUT_19H at the true (negative) R rather than
      claiming 0.0R. That only happens on a gap through the BE stop, and
      the fill price is the same open either way, so reporting the real
      R is the honest reading.

    tp_levels is an optional iterable of R multiples to timestamp on the
    way past; it never affects the walk, it only records when each level
    was first touched.
    """
    n = len(ctx.ts)
    sign = -1.0 if direction == "bearish" else 1.0
    levels = sorted(float(x) for x in tp_levels) if tp_levels else []

    entry_ts = pd.Timestamp(ctx.ts[idx], tz="UTC")
    friday_cutoff = _naive_utc(friday_cutoff_for(entry_ts))

    # First daily checkpoint: 19:00 London on the entry's own London
    # date, unless the entry already happened at or after it.
    checkpoint = london_cutoff_for(entry_ts)
    next_checkpoint = _naive_utc(checkpoint)
    while next_checkpoint <= ctx.ts[idx]:
        checkpoint = next_london_cutoff(checkpoint)
        next_checkpoint = _naive_utc(checkpoint)

    stop = sl
    be_moved = False
    be_idx = None
    be_trigger = None
    be_probability = None
    max_r = 0.0
    checkpoints = 0
    tp_touches = {}

    live_recheck_enabled = (
        weights is not None
        and threshold is not None
        and mitigation_factor_results is not None
        and getattr(ctx, "obs", None) is not None
    )
    target_max_distance = TARGET_SEARCH_R * r_distance
    weekly_target_max_distance = WEEKLY_TARGET_SEARCH_R * r_distance

    closed = False
    terminal_r = 0.0
    terminal_reason = EXIT_DATA_END
    terminal_idx = None
    terminal_price = None
    sl_excursion_pips = 0.0
    intrabar_resolved = False

    for k in range(idx + 1, n):
        t = ctx.ts[k]
        open_k = ctx.open_[k]

        # --- 1. WEEK DEADLINE -------------------------------------------
        past_deadline = t >= friday_cutoff
        rolled_into_new_week = ctx.cutoff_ts[k] > friday_cutoff
        if past_deadline or rolled_into_new_week:
            terminal_r = sign * (open_k - entry_price) / r_distance
            terminal_reason = EXIT_FRIDAY_CLOSE
            terminal_idx = k
            terminal_price = open_k
            closed = True
            break

        # --- 2. DAILY CHECKPOINT ----------------------------------------
        if t >= next_checkpoint:
            checkpoints += 1
            r_at_open = sign * (open_k - entry_price) / r_distance

            if r_at_open > 0.0:
                if not be_moved:
                    stop = entry_price
                    be_moved = True
                    be_idx = k
                    be_trigger = "19h_checkpoint"
            else:
                terminal_r = r_at_open
                terminal_reason = EXIT_CUT_19H
                terminal_idx = k
                terminal_price = open_k
                closed = True
                break

            # Advance ONE LONDON DAY by recomputing, never by +24h. The
            # while-loop skips days that produced no bar at all (a
            # holiday), so the next checkpoint is always in the future.
            checkpoint = next_london_cutoff(checkpoint)
            next_checkpoint = _naive_utc(checkpoint)
            while next_checkpoint <= t:
                checkpoint = next_london_cutoff(checkpoint)
                next_checkpoint = _naive_utc(checkpoint)

        # --- 3. LIVE PROBABILITY CHECK -----------------------------------
        if live_recheck_enabled and not be_moved:
            live_factors = dict(mitigation_factor_results)
            live_factors.update(evaluate_always_factors(ctx.df.iloc[k], direction))
            live_factors.update(
                evaluate_ob_target_factors(
                    ctx.obs, k, direction, target_max_distance,
                    float(ctx.high[k]), float(ctx.low[k]),
                )
            )
            # Re-asked every bar for the same reason as the OB target, and
            # with the opposite consequence: a liquidity target price has
            # covered simply drops out, so the score falls back toward what
            # the remaining, still-untaken liquidity supports.
            live_factors.update(
                evaluate_liquidity_target_factors(
                    getattr(ctx, "liq", None), k, direction,
                    float(ctx.high[k]), float(ctx.low[k]),
                    target_max_distance, weekly_target_max_distance,
                )
            )
            live_probability = compute_probability(live_factors, weights)
            if live_probability < threshold:
                stop = entry_price
                be_moved = True
                be_idx = k
                be_trigger = "target_ob_probability"
                be_probability = live_probability

        # --- 4. FAVOURABLE (provisional) --------------------------------
        max_r_before_bar = max_r
        touched_this_bar = []

        favourable_extreme = ctx.high[k] if sign > 0 else ctx.low[k]
        favourable_r = sign * (favourable_extreme - entry_price) / r_distance
        if favourable_r > max_r:
            max_r = favourable_r
        for level in levels:
            if level not in tp_touches and max_r >= level:
                tp_touches[level] = (k, pd.Timestamp(t, tz="UTC"))
                touched_this_bar.append(level)

        # --- 5. STOP ----------------------------------------------------
        adverse_extreme = ctx.low[k] if sign > 0 else ctx.high[k]
        if sign * (adverse_extreme - stop) <= 0.0:
            # --- 6. ATTRIBUTION -----------------------------------------
            # Step 4's credit for THIS bar is discarded outright and
            # re-derived from inside the bar.
            credit, intrabar_resolved = _intrabar_favourable_credit(
                ctx, k, sign, entry_price, stop, r_distance
            )
            max_r = max(max_r_before_bar, credit)
            for level in touched_this_bar:
                del tp_touches[level]
            for level in levels:
                if level not in tp_touches and max_r >= level:
                    tp_touches[level] = (k, pd.Timestamp(t, tz="UTC"))

            terminal_r = 0.0 if be_moved else -1.0
            terminal_reason = EXIT_BE_STOP if be_moved else EXIT_SL
            terminal_idx = k
            terminal_price = stop
            # Diagnostic only: how far the bar traded BEYOND the stop.
            # Replaces the deleted SLB rescue, so the "would a tolerance
            # band have helped?" question stays answerable from the data.
            sl_excursion_pips = max(
                0.0, sign * (stop - adverse_extreme) / ctx.pip_size
            )
            closed = True
            break

    if not closed:
        # Data ran out mid-trade. The runner walks a tail past year-end
        # precisely so this stays rare; when it does happen, mark it to
        # the last available close rather than inventing an exit.
        if n > idx + 1:
            last = n - 1
            terminal_idx = last
            terminal_price = ctx.close[last]
            terminal_r = sign * (ctx.close[last] - entry_price) / r_distance
        terminal_reason = EXIT_DATA_END

    return {
        "terminal_r": float(terminal_r),
        "terminal_reason": terminal_reason,
        "terminal_idx": terminal_idx,
        "terminal_time": (
            pd.Timestamp(ctx.ts[terminal_idx], tz="UTC")
            if terminal_idx is not None
            else None
        ),
        "terminal_price": (
            float(terminal_price) if terminal_price is not None else None
        ),
        # Max favourable R strictly BEFORE the terminal event. Clamped at
        # return only -- never used as a loop break.
        "max_r_reached": float(min(max_r, MAX_R_CEILING)),
        "tp_touches": tp_touches,
        "be_moved": be_moved,
        "be_idx": be_idx,
        "be_trigger": be_trigger,
        "be_probability": be_probability,
        "checkpoints": checkpoints,
        "sl_excursion_pips": float(sl_excursion_pips),
        "intrabar_resolved": bool(intrabar_resolved),
    }


def apply_tp(walk, tp_multiple, entry_price, direction, r_distance):
    """Projects a TP-free walk onto one take-profit multiple.

    Exact, not approximate: see the module docstring. If the walk's
    max_r_reached (which excludes the terminal bar's move) reached the
    multiple, the trade would have been filled at the TP before anything
    else could close it; otherwise the walk's own terminal event stands.

    tp_multiple of None means "no take-profit at all", i.e. the trade is
    managed purely to its terminal event.
    """
    if tp_multiple is not None and walk["max_r_reached"] >= tp_multiple:
        realised_r = float(tp_multiple)
        exit_reason = EXIT_TP
        exit_price = tp_price_for(entry_price, direction, r_distance, tp_multiple)
        touch = walk["tp_touches"].get(float(tp_multiple))
        if touch is not None:
            exit_idx, exit_time = touch
        else:
            # The level was never in tp_levels, so the walk did not
            # timestamp it. The terminal bar is the tightest bound we
            # have on when the fill happened.
            exit_idx = walk["terminal_idx"]
            exit_time = walk["terminal_time"]
    else:
        realised_r = float(walk["terminal_r"])
        exit_reason = walk["terminal_reason"]
        exit_price = walk["terminal_price"]
        exit_idx = walk["terminal_idx"]
        exit_time = walk["terminal_time"]

    if realised_r > 0.0:
        result = "win"
    elif realised_r < 0.0:
        result = "loss"
    else:
        result = "breakeven"

    return {
        "realised_r": realised_r,
        "result": result,
        "exit_reason": exit_reason,
        "exit_idx": exit_idx,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "tp_price": (
            tp_price_for(entry_price, direction, r_distance, tp_multiple)
            if tp_multiple is not None
            else None
        ),
    }
