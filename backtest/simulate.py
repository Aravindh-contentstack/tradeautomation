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
from backtest.entry_factors import (
    evaluate_entry_factors,
    evaluate_entry_target_factors,
)
from backtest.entry_models import Zone, pending_order_for, scan_for_entry
from backtest.entry_ob import (
    TARGET_SEARCH_R,
    WEEKLY_TARGET_SEARCH_R,
    iter_mitigation_candidates,
)
from backtest.m15_pipeline import h1_bar_containing, m15_index_at_or_after
from backtest.factors import (
    ALL_FACTORS,
    compute_probability,
    evaluate_always_factors,
    evaluate_liquidity_target_factors,
    evaluate_mitigation_ob_factors,
    evaluate_ob_target_factors,
    evaluate_mitigation_leg_swept_factors,
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


def zone_for(ctx, ob_row):
    """The H1 order block as backtest/entry_models.py wants it.

    The whole adapter between ObSeries and the entry layer, deliberately
    two dozen lines in one place: entry_models never imports ob_state, and
    its tests never have to build an ObUniverse.
    """
    series = ctx.obs.series["H1"]
    tier = series.primary_tier[ob_row] if series.primary_tier is not None else None
    return Zone(
        top=float(series.top[ob_row]),
        bottom=float(series.bottom[ob_row]),
        bullish=bool(series.sign[ob_row] > 0),
        primary_tier=tier,
        valid_through=int(series.valid_through[ob_row]),
        touch_at=tuple(series.touch_at[ob_row]),
        visible_from=int(series.visible_from[ob_row]),
    )


def _htf_factors(ctx, k, direction, ob_row):
    """Everything scoreable at the MITIGATION bar, before an entry exists.

    The two target gates are absent on purpose: both need a max distance in
    R, and R comes from the M15 setup, which has not been found yet. So
    htf_probability is the higher-timeframe picture alone, which is exactly
    what the HTF gate is meant to judge.
    """
    factors = evaluate_mitigation_ob_factors(ctx.obs, k, direction, ob_row)
    factors.update(
        evaluate_swept_liquidity_factors(getattr(ctx, "liq", None), k, direction)
    )
    factors.update(
        evaluate_mitigation_leg_swept_factors(
            getattr(ctx, "liq", None),
            k,
            direction,
            float(ctx.obs.series["H1"].top[ob_row]),
            float(ctx.obs.series["H1"].bottom[ob_row]),
        )
    )
    factors.update(evaluate_always_factors(ctx.df.iloc[k], direction))
    return factors


def find_signals(ctx, weights, pip_size, htf_threshold=None,
                 pending_as_of=None):
    """Every M15 entry model that fired on a qualifying H1 OB touch.

    pending_as_of switches the question from historical to live. Left None
    (the backtest) each candidate is a setup that FILLED, and the signal
    carries the fill bar and price. Set to the last closed M15 bar (the
    live bot) each candidate is an order that should be RESTING at that
    bar, with `order_kind` and `expires_m15` instead of a fill, because the
    broker has not tagged it yet and may never.

    One code path rather than two on purpose: the HTF gate, all the factor
    scoring, and the signal shape are identical, and the live bot has to
    score a candidate exactly as the backtest would or the thresholds it
    inherits mean nothing.

    No total-probability threshold and no max-SL filter is applied here:
    those are decided later by the prior year's settings
    (backtest/settings.py). That separation is load-bearing. If this
    function pre-filtered on them, each year's journal would contain only
    trades that passed the PREVIOUS year's filters, the settings search
    would see an already-filtered pool, and thresholds would ratchet
    upward every year until the pool starved.

    htf_threshold is the ONE exception, and it is deliberately not part of
    that machinery. It is fixed permissive rather than searched (see
    analysis.HTF_GATE_QUANTILE), and its job is to skip the M15 scan when
    the higher-timeframe picture is hopeless, which is a compute saving
    and a match to how the user trades manually. None means no gate.

    Two probabilities per candidate:

      htf_probability    the Daily/H4/H1 picture at the mitigation bar.
      total_probability  that plus the two target gates and the firing
                         entry model's factors. This is what is_taken
                         reads and what the walk-forward searches.

    The trade's DIRECTION is the mitigated zone's direction, decided by
    what price touched rather than assumed beforehand.

    Requires a MarketContext carrying OB state AND an M15 bundle. Without
    either it yields nothing, which is what a caller that never built them
    should get.
    """
    if getattr(ctx, "obs", None) is None:
        return []
    bundle = getattr(ctx, "m15_bundle", None)
    if bundle is None:
        return []

    signals = []
    for k, ob_row, touch_no in iter_mitigation_candidates(ctx):
        series = ctx.obs.series["H1"]
        direction = "bullish" if series.sign[ob_row] > 0 else "bearish"

        htf_factors = _htf_factors(ctx, k, direction, ob_row)
        htf_probability = compute_probability(htf_factors, weights)
        if htf_threshold is not None and htf_probability < htf_threshold:
            continue

        zone = zone_for(ctx, ob_row)
        if pending_as_of is None:
            setup = scan_for_entry(bundle, ctx.ts, zone, k, pip_size)
        else:
            setup = pending_order_for(
                bundle, ctx.ts, zone, k, pip_size, pending_as_of
            )
        if setup is None:
            continue

        # An M15 bar; the walk and the factor gates run on H1. -1 means it
        # landed where no H1 bar exists, which is unresolvable rather than
        # bar zero.
        anchor_m15 = (
            setup["fill_m15"] if pending_as_of is None else pending_as_of
        )
        entry_index = h1_bar_containing(ctx.ts, bundle.ts[anchor_m15])
        if entry_index < 0:
            continue

        # A resting order has no fill price yet, so the order price is the
        # best available estimate of where it will enter. That is exactly
        # what the backtest assumes too: its fill price IS the order price.
        entry_price = (
            setup["fill_price"] if pending_as_of is None
            else setup["order_price"]
        )
        r_distance = setup["r_distance"]
        target_max = TARGET_SEARCH_R * r_distance
        weekly_max = WEEKLY_TARGET_SEARCH_R * r_distance

        # Frozen for the life of the trade: the HTF picture plus what the
        # setup itself was. simulate_trade's recheck rebuilds a live score
        # on top of this rather than re-deriving any of it every bar.
        frozen = dict(htf_factors)
        frozen.update(
            evaluate_entry_factors(bundle, ctx.ts, zone, setup, k)
        )

        factor_results = dict(frozen)
        factor_results.update(
            evaluate_ob_target_factors(
                ctx.obs, entry_index, direction, target_max,
                float(ctx.high[entry_index]), float(ctx.low[entry_index]),
            )
        )
        factor_results.update(
            evaluate_liquidity_target_factors(
                getattr(ctx, "liq", None), entry_index, direction,
                float(ctx.high[entry_index]), float(ctx.low[entry_index]),
                target_max, weekly_max,
            )
        )
        factor_results.update(
            evaluate_entry_target_factors(
                bundle, zone, setup, setup["trigger_m15"]
            )
        )
        total_probability = compute_probability(factor_results, weights)

        entry_time = pd.Timestamp(bundle.ts[anchor_m15], tz="UTC")
        signals.append({
            # The H1 bar the FILL landed in. simulate_trade walks from
            # idx + 1, so reporting the mitigation bar here would re-walk
            # the wait for the order as if it were part of the trade.
            "idx": entry_index,
            "direction": direction,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "sl": setup["sl"],
            "r_distance": r_distance,
            # Read off the TRIGGER candle, which is the one decision 4
            # gates on, not off the fill, which may land anywhere.
            "session": session_for(
                pd.Timestamp(bundle.ts[setup["trigger_m15"]], tz="UTC")
            ),
            # `probability` stays an alias of the total so anything reading
            # the old key keeps working.
            "probability": total_probability,
            "htf_probability": htf_probability,
            "total_probability": total_probability,
            "factor_results": factor_results,
            "entry_model": setup["model"],
            "m15_trigger_time": pd.Timestamp(
                bundle.ts[setup["trigger_m15"]], tz="UTC"
            ),
            "m15_fill_time": (
                entry_time if pending_as_of is None else None
            ),
            "order_host_candle": setup["host_m15"],
            # Live only. None in the backtest, where a signal only exists
            # once it has already filled.
            "order_kind": setup.get("order_kind"),
            "expires_m15": setup.get("expires_m15"),
            "ob_row": ob_row,
            "ob_touch_no": touch_no,
            "ob_top": float(series.top[ob_row]),
            "ob_bottom": float(series.bottom[ob_row]),
            "mitigation_idx": k,
            "mitigation_time": bar_timestamp(ctx, k),
            "entry_deferred": entry_index != k,
            "excluded_gates": sorted(set(ALL_FACTORS) - set(factor_results)),
            "mitigation_factor_results": frozen,
            # simulate_trade needs these to re-ask the M15 target gate every
            # bar, which is the one entry factor that is not frozen.
            "entry_zone": zone,
            "entry_setup": setup,
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


def _live_probability(ctx, k, direction, mitigation_factor_results, weights,
                      entry_zone, entry_setup,
                      target_max_distance, weekly_target_max_distance):
    """The trade's probability re-asked at bar k.

    Extracted from step 3 of the walk so the same scoring can be reached
    from two places with no risk of them drifting apart: the per-bar
    breakeven recheck, and the single end-of-trade score the research
    harness journals as `final_probability`.

    The Mitigation OB answers stay exactly as frozen at entry. Everything
    that can change while the trade is open is re-evaluated off this bar:
    the Always (structure/zone) factors, the Target OB gate, the liquidity
    target gate, and the M15 target gate. A target price has covered
    simply drops out, so the score falls back toward what the remaining,
    still-untaken liquidity supports.
    """
    live_factors = dict(mitigation_factor_results)
    live_factors.update(evaluate_always_factors(ctx.df.iloc[k], direction))
    live_factors.update(
        evaluate_ob_target_factors(
            ctx.obs, k, direction, target_max_distance,
            float(ctx.high[k]), float(ctx.low[k]),
        )
    )
    live_factors.update(
        evaluate_liquidity_target_factors(
            getattr(ctx, "liq", None), k, direction,
            float(ctx.high[k]), float(ctx.low[k]),
            target_max_distance, weekly_target_max_distance,
        )
    )
    bundle = getattr(ctx, "m15_bundle", None)
    if bundle is not None and entry_setup is not None:
        m15_bar = m15_index_at_or_after(bundle, ctx.ts[k])
        if m15_bar >= 0:
            live_factors.update(
                evaluate_entry_target_factors(
                    bundle, entry_zone, entry_setup, m15_bar
                )
            )
    return compute_probability(live_factors, weights)


def simulate_trade(
    ctx, idx, direction, entry_price, sl, r_distance, tp_levels=None,
    weights=None, threshold=None, mitigation_factor_results=None,
    entry_zone=None, entry_setup=None,
    max_r_ceiling=MAX_R_CEILING, record_final_probability=False,
    record_min_probability=False,
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
         running exactly as before. A run with the inputs but NO
         threshold still scores each bar and reports the lowest score
         seen, it just never acts on it.
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
    way past. It never affects the walk, it only records when each level
    was first touched.

    max_r_ceiling clamps the reported max R (a reporting clamp only, never
    a loop break). It is a parameter rather than a constant because a
    liquidity-target take-profit can legitimately sit beyond the 10R
    default, and clipping it there would silently understate that TP
    family's results.

    record_final_probability adds one extra scoring call at the terminal
    bar. record_min_probability adds one per bar, and is the only one of
    the two that is genuinely expensive. Both are off by default because
    only the research harness wants them, and every caller that omits them
    pays nothing.
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
    max_r_to_be = None
    min_live_probability = None
    final_probability = None
    checkpoints = 0
    tp_touches = {}

    # Two separate questions, deliberately not one flag.
    #
    #   can_score_live  do we have the inputs to compute a live score at
    #                   all? Needs weights, the frozen mitigation answers,
    #                   and the OB universe.
    #   recheck_enabled that, PLUS a threshold to compare against, so the
    #                   breakeven rule has a bar to fail.
    #
    # They came apart because the research harness runs with no threshold
    # (every candidate is taken, so there is nothing for the rule to act
    # on) but still wants the score reported. Fusing them, as the single
    # `live_recheck_enabled` flag used to, made "report the score" and
    # "act on the score" impossible to separate.
    can_score_live = (
        weights is not None
        and mitigation_factor_results is not None
        and getattr(ctx, "obs", None) is not None
    )
    recheck_enabled = can_score_live and threshold is not None
    # Scoring every bar costs a full factor evaluation per bar, so it is
    # done only when someone will read the result: either the rule is
    # armed, or the caller explicitly asked for the diagnostic. The
    # buffer sweep runs with both off and pays nothing.
    score_each_bar = can_score_live and (recheck_enabled or record_min_probability)
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
                    # Highest R reached strictly BEFORE the stop moved.
                    # This site and the recheck below both sit ahead of
                    # step 4, so max_r still excludes this bar -- the same
                    # "strictly before the event" reading max_r_reached
                    # uses for the terminal bar.
                    max_r_to_be = max_r
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
        if score_each_bar and not be_moved:
            live_probability = _live_probability(
                ctx, k, direction, mitigation_factor_results, weights,
                entry_zone, entry_setup,
                target_max_distance, weekly_target_max_distance,
            )
            # Tracked even when the rule is not armed, so an unfiltered run
            # can still answer "how low did this trade's score ever get?",
            # which is what the rule would have acted on had there been a
            # threshold to act against. The `not be_moved` guard makes this
            # the lowest score seen WHILE THE TRADE WAS STILL AT RISK,
            # which is exactly the window the rule operates in.
            if min_live_probability is None:
                min_live_probability = live_probability
            else:
                min_live_probability = min(min_live_probability, live_probability)
            if recheck_enabled and live_probability < threshold:
                stop = entry_price
                be_moved = True
                be_idx = k
                be_trigger = "target_ob_probability"
                be_probability = live_probability
                max_r_to_be = max_r

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

    # The trade's score at the moment it closed, computed ONCE here rather
    # than every bar. The per-bar recheck above stops the instant a trade
    # goes to breakeven, so it cannot answer this on its own, and removing
    # that short-circuit would multiply the factor evaluations per trade by
    # the number of bars held. Scoring the terminal bar alone is O(1) and
    # gives exactly the number the journal wants.
    if record_final_probability and can_score_live and terminal_idx is not None:
        final_probability = _live_probability(
            ctx, terminal_idx, direction, mitigation_factor_results, weights,
            entry_zone, entry_setup,
            target_max_distance, weekly_target_max_distance,
        )

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
        "max_r_reached": float(min(max_r, max_r_ceiling)),
        "tp_touches": tp_touches,
        "be_moved": be_moved,
        "be_idx": be_idx,
        "be_trigger": be_trigger,
        "be_probability": be_probability,
        # Highest R the trade showed before its stop moved to entry.
        # None means it never went to breakeven, which is a different
        # statement from 0.0 (went to breakeven having shown nothing).
        "max_r_to_be": (
            float(min(max_r_to_be, max_r_ceiling))
            if max_r_to_be is not None
            else None
        ),
        "min_live_probability": min_live_probability,
        "final_probability": final_probability,
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
