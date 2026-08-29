"""The M15 entry models: LC-1, LC-2A, LC-2B, CE.

What replaced what
------------------
Entry used to be the CLOSE of the H1 candle that mitigated an order
block, with the stop 2 pips beyond the zone's far edge
(backtest/entry_ob.py's build_setup, and roadmap/supply-and-demand.md
called it "a placeholder until the M15 entry models land"). It was a
placeholder because it committed at whatever price the hour happened to
close at, with a stop as wide as the whole zone.

This module is what price and stop come from instead. Given an H1 zone
and the bar price mitigated it on, it walks M15 forward looking for one
of four setups, and returns the first that fires.

Reading direction
-----------------
Every rule below is written BEARISH: an order block above price, price
rallying up into it, and we are looking to sell. Every rule has an exact
bullish mirror, and getting one backwards is the likeliest bug in here.
So nothing is written twice: `sign` is +1 for a long and -1 for a short,
`_beyond` and `_toward` do all the comparisons, and the tests are
parametrised by direction so a mirror cannot be forgotten. The same
approach sweep_credit.py already takes.

Index spaces
------------
Two integer spaces meet in this module and they are NOT interchangeable:

  H1 bars       index into the walk frame (ctx). Everything about a
                zone's validity lives here.
  M15 bars      index into the FULL M15 history held by M15Bundle.
                Everything about the setup's geometry lives here.

They are crossed only by m15_index_at_or_after and h1_bar_containing,
both timestamp-based. See backtest/m15_pipeline.py's docstring for why
backtest/intrabar.py's M15Index cannot be used for this: its indices are
year-scoped and would silently misaddress every level.

The eq rule, and why it does not kill the N=2 window
----------------------------------------------------
An H1 zone dies when a wick reaches its midpoint (order_blocks.py's "eq"
rule). That test is ported here to M15 resolution so the scan stops
hunting the moment the wick happens, rather than 15 to 45 minutes later
when the H1 bar closes.

Ported naively it contradicts the N=2 order window. Take a bullish zone
with bottom 100 and top 120, so eq is 110. An LC-2A whose trigger candle
sweeps down to 109 has breached eq, so a plain reading says the next
candle is dead. But N=2 says the next candle is candle B, the legitimate
second host for the order. Both rules are right.

They govern different things, and separating them resolves it:

  the eq off-by-one   do not hunt for FRESH setups in a half-consumed
                      zone                        -> setup FORMATION
  the N=2 window      re-price the order because price made a new
                      extreme                     -> order MANAGEMENT

Candle B is not a new opportunity, it is the same opportunity re-priced,
so eq has no business blocking it. Concretely: a trigger candle is valid
while eq is unbreached or ON the breaching candle itself (the breach IS
the reaction, and the reaction is the trade, which is the same off-by-one
order_blocks._kill already uses for invalidated_index). After that, no
new trigger forms, but a setup that already formed keeps its full window
and is bounded by the FAR edge instead.

One consequence, accepted knowingly: if eq is breached by a candle
carrying no setup and a trigger would have formed two candles later, this
rejects it where H1 might not have (both bars could sit inside one H1
candle, and H1's own off-by-one would allow it). M15 is therefore
STRICTLY STRICTER than H1 for formation. That is the price of not being
sensitive to where the hour boundary happens to fall, and it errs toward
not trading. Do not "fix" it.

LC-1's proximity bound is load-bearing
--------------------------------------
Measured on real 2024 M15, an unswept minor level of the wanted side
exists within 10 candles on about 90% of bars, with a median of five live
at once. Without a distance bound LC-1 fires on nearly every mitigation,
the freshness rule accomplishes nothing, and "no other LIDs" answers no
so consistently that it carries no information.

The bound restores the user's original wording, "just below or inside the
H1 OB" and "within nearby distance": the level must sit inside the zone
or within LC1_PROXIMITY_ATR x ATR(14) of its NEAR edge. That takes the
hit rate from 89.6% to 15.8% on EUR_USD and 88.6% to 14.0% on XAU_USD,
and the 1.0x value sits on a plateau (2.0x gives 16.6%) rather than on a
cliff, so a later nudge will not swing behaviour.
"""

from dataclasses import dataclass

import numpy as np

from backtest import entry_params, killzone
from backtest.m15_pipeline import h1_bar_containing, m15_index_at_or_after

# How near the zone LC-1's liquidity has to sit, in multiples of M15
# ATR(14): inside the zone, or this far beyond its near edge. See the
# module docstring for the measurement that set it. An ATR multiple
# rather than pips so one number serves EUR_USD and XAU_USD, the same
# reasoning levels.py states for its own tolerance.
LC1_PROXIMITY_ATR = 1.0

# How stale LC-1's liquidity may be, in M15 candles between the level's
# own candle and the trigger. The user's figure. On its own it restricts
# almost nothing (see the docstring); it earns its place alongside the
# proximity bound, not instead of it.
LC1_FRESHNESS_BARS = 10

# Candles from the sweep within which the zone must be mitigated, the
# user's N=2. The sweep candle counts as the first, so the mitigation is
# on the sweep candle itself or the one after it.
N2_WINDOW = 2

# Tier prefixes this module reads out of M15Bundle.structure.
FRACTAL_TIER = "m15_fractal"
INTERNAL_TIER = "m15_internal"

# CE places its limit at the midpoint of the internal range. The user's
# fib50.
CE_FIB = 0.5

# How many internal legs CE will follow before giving up. The user's rule:
# if the first leg pulls back shallower than fib50 and makes a second leg,
# that second leg is tradeable; if THAT one also fails to pull back, stop.
CE_MAX_LEGS = 2

# Abort when the current leg has grown past this multiple of the leg that
# broke structure. The user's forced backstop for the case where price
# never pulls back at all and just runs, which the leg counter above would
# otherwise never catch.
CE_RUNAWAY_MULTIPLE = 5.0

# The break strings compute_tier_structure emits. There is no BOS/iBOS
# identifier in this codebase: a break of structure IS this string in the
# tier's high_event or low_event column.
BREAK_OF_SWING_HIGH = "break of swing high"
BREAK_OF_SWING_LOW = "break of swing low"

# How far back from the H1 mitigation bar the scan looks for a fake break
# or an equals pool that formed on the approach leg.
#
# It has to look back at all because the user was explicit that LC-2A's
# break "can happen after mitigation of H1 OB or before it", and the scan
# starts AT the mitigation. Without a lookback, every fake break that
# formed on the way in would be invisible.
#
# 30 candles is CHOSEN, NOT DERIVED. It is 7.5 hours of M15, which is
# about one session, and it matches the window the M15 target liquidity
# gate uses so there is one notion of "recent" in the entry layer rather
# than two. Worth revisiting once there are fire rates per model.
APPROACH_LOOKBACK_BARS = 30

# Which models to try, in order, per the H1 zone's primary_tier.
#
# There is NO LC-1 on the swing tier: a swing range is too large to
# predict a structure break off one swept minor high, and the user's rule
# is explicit. LC-1's freshness rule alone does not enforce this, so the
# absence here is the enforcement.
#
# LC-2A before LC-2B because a structural break is the stronger
# inducement. That ordering is a CHOSEN DEFAULT, not derived.
MODEL_PRECEDENCE = {
    "h1_fractal": ("LC-2A", "LC-2B", "LC-1", "CE"),
    "h1_internal": ("LC-2A", "LC-2B", "LC-1", "CE"),
    "h1_swing": ("LC-2A", "LC-2B", "CE"),
}

# Fallback for a primary_tier this table does not know, which should not
# happen but must not crash the walk if the OB layer grows a tier.
DEFAULT_PRECEDENCE = ("LC-2A", "LC-2B", "CE")


@dataclass(frozen=True)
class Zone:
    """The H1 order block, in the only terms this module needs.

    Deliberately not an ObSeries row. Everything here is a plain scalar or
    tuple, so the entry models never import ob_state and the tests never
    have to build an ObUniverse. backtest/simulate.py owns the two-line
    adapter that fills this in from ObSeries plus the OB table's
    primary_tier.

    valid_through and touch_at are H1 bar indices. Everything else is a
    price or a flag.
    """

    top: float
    bottom: float
    bullish: bool
    primary_tier: str
    valid_through: int          # inclusive last H1 bar the zone is live
    touch_at: tuple = ()        # qualifying-touch H1 bars, ascending
    visible_from: int = 0       # first H1 bar the zone exists on

    @property
    def eq(self):
        return (self.top + self.bottom) / 2.0

    @property
    def sign(self):
        """+1 for a long, -1 for a short. The one direction switch."""
        return 1 if self.bullish else -1

    @property
    def near_edge(self):
        """The edge price reaches first when coming back to the zone."""
        return self.top if self.bullish else self.bottom

    @property
    def far_edge(self):
        """The edge price has to pass to leave the zone entirely."""
        return self.bottom if self.bullish else self.top


def _in_killzone(bundle, j, allowed_sessions=None):
    """Does M15 bar j close inside an allowed killzone?

    Decision 4: the H1 mitigation is no longer session-gated, but the M15
    candle that completes the setup must land in a killzone. The gate
    moved to where it now matters, which is entry.

    allowed_sessions restricts which of London/NY may host a trigger, per
    instrument. None (the default, and every caller before this parameter
    existed) allows both, unchanged from the original behavior.
    """
    hour = int(bundle.london_hour[j])
    in_london = killzone.LONDON_START_HOUR <= hour < killzone.LONDON_END_HOUR
    in_ny = killzone.NY_START_HOUR <= hour < killzone.NY_END_HOUR
    if allowed_sessions is None:
        return in_london or in_ny
    return (in_london and "london" in allowed_sessions) or (
        in_ny and "ny" in allowed_sessions
    )


def _reaches(bundle, j, price, bullish):
    """Did bar j's wick reach `price`, coming from the zone's side?

    A bullish zone is approached from above, so reaching a level inside it
    means trading DOWN to it. Bearish is the mirror.
    """
    if bullish:
        return bundle.low[j] <= price
    return bundle.high[j] >= price


def _beyond(bundle, j, price, zone):
    """Did bar j's wick take out `price`, strictly?

    The raid test. Strict rather than inclusive, matching every sweep test
    in the repo (levels.py, low_resistance.py, minor_liquidity.py):
    touching a level is not taking it, because the orders resting there
    only fill once price trades through.

    A bearish setup raids UPWARD into buy-side liquidity, so "beyond"
    means a higher high. That is the opposite direction to _reaches, which
    asks about coming back DOWN to the zone.
    """
    if zone.bullish:
        return bundle.low[j] < price
    return bundle.high[j] > price


def _mitigates(bundle, j, zone):
    """Did bar j trade into the zone at all?"""
    return _reaches(bundle, j, zone.near_edge, zone.bullish)


def _swept_wanted_side(bundle, j, zone):
    """Row positions of the minor levels bar j swept, on the wanted side.

    A bearish setup wants BUY-side liquidity taken, which is a HIGH. The
    sweep itself is not re-tested here: compute_minor_liquidity already
    decided which bar first took each level, and reading its answer is
    what keeps the two from ever disagreeing.
    """
    idx = bundle.minor_idx
    rows = idx.swept_at.get(j)
    if not rows:
        return ()
    want_high = not zone.bullish
    return tuple(pos for pos in rows if bool(idx.side_high[pos]) == want_high)


def _within_proximity(bundle, zone, price, j):
    """Is `price` inside the zone, or within the ATR bound of its near edge?

    Decision 20. Bearish: between `bottom - bound` and `top`. Bullish:
    between `bottom` and `top + bound`. A NaN ATR (the warm-up) answers
    False rather than admitting everything, the same stance levels.py
    takes when it skips pivots that confirm before ATR seeds.
    """
    atr = bundle.atr[j]
    if not np.isfinite(atr):
        return False
    bound = LC1_PROXIMITY_ATR * float(atr)
    if zone.bullish:
        return zone.bottom <= price <= zone.top + bound
    return zone.bottom - bound <= price <= zone.top


def _lc1_liquidity(bundle, zone, j):
    """The reference level for an LC-1 trigger at bar j, or None.

    Returns the DEEPEST level bar j swept that passes both bounds, which
    is the user's stacked-liquidity rule: when one candle takes several
    levels, the last one taken is what `lid_with_fvg` and `wicked_the_lid`
    describe. "Deepest" means furthest into the zone's direction of
    travel, so the highest high for a bearish setup.
    """
    idx = bundle.minor_idx
    best = None
    for pos in _swept_wanted_side(bundle, j, zone):
        price = float(idx.level[pos])
        if not _within_proximity(bundle, zone, price, j):
            continue
        if j - int(idx.candle_index[pos]) > LC1_FRESHNESS_BARS:
            continue
        if best is None:
            best = pos
            continue
        # Deepest wins: the highest high for a short, the lowest low for
        # a long.
        better = price > float(idx.level[best])
        if zone.bullish:
            better = price < float(idx.level[best])
        if better:
            best = pos
    return best


def _try_lc1(bundle, zone, j, sweep_at, consumed):
    """LC-1 on trigger candle j, or None.

    `sweep_at` maps a bar to the LC-1 level it swept, accumulated by the
    caller as it walks. LC-1 needs a sweep and a mitigation within the
    N=2 window, which can be the same candle or two consecutive ones, so
    the trigger is whichever candle COMPLETES the pair.

    A sweep is CONSUMED by the first trigger it produces, which is what
    `consumed` tracks. Without that, one sweep produces two triggers and
    the N=2 bound quietly doubles: a sweep on bar 23 that also mitigates
    triggers at 23, and then bar 24 mitigating with the sweep one bar
    back triggers again, so the order is effectively live from 23 to 25.
    Both readings are legitimate on their own, which is exactly why the
    ambiguity has to be closed rather than left to whichever branch runs
    first.
    """
    if not _mitigates(bundle, j, zone):
        return None
    # The sweep is on this candle, or up to N2_WINDOW - 1 candles back.
    for back in range(N2_WINDOW):
        bar = j - back
        if bar in consumed:
            continue
        pos = sweep_at.get(bar)
        if pos is None:
            continue
        return {
            "model": "LC-1",
            "trigger_m15": j,
            "sweep_m15": bar,
            "level_row": pos,
            "level": float(bundle.minor_idx.level[pos]),
        }
    return None


def _break_event_column(zone, tier):
    """The tier column whose break means "in our direction", and its string.

    A bearish setup wants the structure to flip bearish, which is a break
    of the swing LOW recorded in the tier's low_event column. Bullish is
    the mirror.
    """
    if zone.bullish:
        return "%s_high_event" % tier, BREAK_OF_SWING_HIGH
    return "%s_low_event" % tier, BREAK_OF_SWING_LOW


def _opposite_swing_column(zone, tier):
    """The tier's swing column on the far side of a break in our direction.

    Serves two readers. For LC-2A it is the PBID: a bearish break traps
    shorts whose stops sit ABOVE, at the fractal swing high live at the
    break. For CE it is the top of the internal range the fib is measured
    from, which is the same column for the same reason.
    """
    return "%s_swing_%s" % (tier, "low" if zone.bullish else "high")


def _finite(value):
    """float(value), or None when the structure column has no answer yet."""
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _live_swings(bundle, zone, tier, bar):
    """(near, far) swing prices live at `bar`, either possibly None.

    NEAR is the swing a break in our direction has to clear: a bearish
    setup breaks the swing LOW. FAR is the one on the other side, which is
    the PBID for LC-2A (where trapped traders' stops sit) and the range
    extreme for CE.
    """
    near_col = "%s_swing_%s" % (tier, "high" if zone.bullish else "low")
    near = _finite(bundle.structure[near_col][bar])
    far = _finite(bundle.structure[_opposite_swing_column(zone, tier)][bar])
    return near, far


def _wick_breached(bundle, zone, level, bar):
    """Did bar `bar`'s wick clear `level` in our direction?"""
    if zone.bullish:
        return bundle.high[bar] > level
    return bundle.low[bar] < level


def _find_fake_break(bundle, zone, start, j):
    """The most recent fractal break in our direction, or None.

    Returns (break_bar, pbid, broken_level, body_closed).

    Two kinds of break, and the difference is the whole point of the
    `fake_break_with_body_close` factor:

      BODY   the detector reported it, which it only ever does on a CLOSE
             through the level (fractal_detector.py's `closes[j] <
             swing_low`). body_closed is True.
      WICK   price poked through and closed back. The detector never
             reports this at all, so it is found here by testing the wick
             against the live swing directly. body_closed is False.

    Body breaks are preferred outright rather than "whichever is more
    recent", which is what makes the user's third scenario come out right:
    two wicks followed by a decisive close is a YES, because the close
    happened, not a NO because wicks came first. The factor asks whether a
    close EVER happened by the time we trigger, not whether the first
    breach was one.

    Without the wick branch the factor is tautological, because a recorded
    break already implies a close, and it measured 100% yes on real data.

    The PBID is read at the break bar, not at j: it is the level that was
    live when the trapped traders committed, and a later fractal replacing
    it does not move their stops.
    """
    event_col, wanted = _break_event_column(zone, FRACTAL_TIER)
    events = bundle.structure[event_col]

    for bar in range(j, start - 1, -1):
        if events[bar] == wanted:
            near, far = _live_swings(bundle, zone, FRACTAL_TIER, bar)
            if far is None:
                return None
            return bar, far, near, True

    for bar in range(j, start - 1, -1):
        near, far = _live_swings(bundle, zone, FRACTAL_TIER, bar)
        if near is None or far is None:
            continue
        if _wick_breached(bundle, zone, near, bar):
            return bar, far, near, False
    return None


def _try_lc2a(bundle, zone, j, start, consumed):
    """LC-2A, the fake break, on trigger candle j. Or None.

    The shape: price breaks fractal structure in OUR direction, which
    traps early entries whose stops sit behind the level the break left.
    Price then raids those stops and mitigates the zone, and the real move
    follows. ICT's accumulation, manipulation, distribution, with the fake
    break as the accumulation.
    """
    if not _mitigates(bundle, j, zone):
        return None
    found = _find_fake_break(bundle, zone, start, j)
    if found is None:
        return None
    break_bar, pbid, broken, body_closed = found
    if break_bar in consumed:
        return None
    # The raid has to happen on the trigger candle or the one before it,
    # the same N=2 window LC-1 uses.
    for back in range(N2_WINDOW):
        bar = j - back
        if bar < break_bar:
            continue
        if _beyond(bundle, bar, pbid, zone):
            return {
                "model": "LC-2A",
                "trigger_m15": j,
                "sweep_m15": break_bar,
                "raid_m15": bar,
                "level": pbid,
                "break_m15": break_bar,
                "broken_level": broken,
                "body_closed": body_closed,
            }
    return None


def _try_lc2b(bundle, zone, j, start, consumed):
    """LC-2B, the equals raid, on trigger candle j. Or None.

    Same shape as LC-1 but the liquidity is a double top rather than a
    single rejection high: equal highs trap support-and-resistance traders
    who short the level with stops just above it.

    The sweep is read off compute_liquidity_levels rather than re-tested,
    so LC-2B and the detector cannot disagree about which candle took the
    pool. Note that means the sweep is measured against the pool's BAND
    edge, which is levels.py's rule and deliberately not restated here.
    """
    if not _mitigates(bundle, j, zone):
        return None
    idx = bundle.equals_idx
    want_high = not zone.bullish
    for back in range(N2_WINDOW):
        bar = j - back
        if bar < start or bar in consumed:
            continue
        for pos in idx.swept_at.get(bar, ()):
            if bool(idx.side_high[pos]) != want_high:
                continue
            price = float(idx.level[pos])
            # Same "at or below the top" containment LC-1 has, without
            # LC-1's ATR proximity bound: decision 20 is LC-1 specific,
            # and an equals pool is already a much rarer object.
            if zone.bullish and price < zone.bottom:
                continue
            if not zone.bullish and price > zone.top:
                continue
            return {
                "model": "LC-2B",
                "trigger_m15": j,
                "sweep_m15": bar,
                "level_row": pos,
                "level": price,
            }
    return None


def _extreme_bar(bundle, zone, price, j):
    """The bar whose extreme IS `price`, searching back from j. -1 if none.

    Needed because CE's `has_imbalance` and `has_inducements` describe the
    LEG that broke structure, which runs from the swing extreme to the
    break, and the structure columns carry the extreme's price but not its
    bar.

    Exact float equality is safe here, and only here: the column value was
    copied verbatim out of this same highs/lows array by
    compute_fractal_swing_structure, so it is the identical double rather
    than a recomputation of it.
    """
    series = bundle.high if not zone.bullish else bundle.low
    for bar in range(j, max(-1, j - APPROACH_LOOKBACK_BARS), -1):
        if series[bar] == price:
            return bar
    return -1


def _internal_break_at(bundle, zone, j):
    """(extreme, extreme_bar) if bar j broke structure our way, else None.

    Bearish: `m15_internal_low_event` says the swing low broke, and the
    range's top is the swing HIGH that was live at that moment.
    """
    event_col, wanted = _break_event_column(zone, INTERNAL_TIER)
    if bundle.structure[event_col][j] != wanted:
        return None
    near, far = _live_swings(bundle, zone, INTERNAL_TIER, j)
    if far is None:
        return None
    return far, _extreme_bar(bundle, zone, far, j), near


# CE deliberately does NOT accept a wick-only break, unlike LC-2A.
#
# The asymmetry is NOT that fake breaks are wicks. They are not: a fake
# break is a break that later REVERSES, and it can perfectly well be a
# decisive body close that then fails. Wick-versus-body describes the
# break candle's shape; fake-versus-real describes what happens after.
# The two are independent.
#
# The actual reason the two models treat it differently is what role the
# break plays in each:
#
#   LC-2A  the break's strength is a QUALITY input. Its premise is that
#          traders were induced into positions whose stops we then raid,
#          and a wick through a level does induce some of them, just fewer
#          than a close does. So both kinds are valid setups and the
#          difference is scored by fake_break_with_body_close.
#
#   CE     the break's strength is a PRECONDITION. Its premise is that
#          displacement has already happened and we are entering on a
#          pullback into a confirmed range. A wick-only breach means the
#          range was NOT broken, so the premise fails outright rather
#          than merely weakening. There is nothing to score.
#
# Measured, to check that this is not just a story: allowing wick breaks
# here took CE from 3.7% to 9.6% of mitigations and left 94% of CE setups
# resting on unconfirmed breaks.
#
# The cost is that `ibos_with_body_close` would be tautological here, for
# exactly the reason it was before: a recorded break already implies a
# close. So it is dropped from CE's factor set rather than kept as a
# constant. CE has seven factors, every other model has eight.


def _try_ce(bundle, h1_ts, zone, j, mitigated, pip_size, eq_breach, as_of=None):
    """CE, the confirmation entry, breaking at bar j. Or None.

    Unlike the LC models this does not go through _resolve_order. CE rests
    a LIMIT at the midpoint of the internal range, and that level MOVES as
    the range extends, so the order cannot be priced once at a host candle
    and left there. Resolution walks forward with its own state.

    The conservative model of the four: it only acts after displacement
    has already happened, where LC-1 is predicting a break off a swept
    minor high.
    """
    if not mitigated:
        return None
    found = _internal_break_at(bundle, zone, j)
    if found is None:
        return None
    extreme, extreme_bar, broken = found
    if as_of is not None:
        return _pending_ce(
            bundle, h1_ts, zone, j, extreme, extreme_bar, broken, pip_size,
            eq_breach, as_of,
        )
    return _resolve_ce(
        bundle, h1_ts, zone, j, extreme, extreme_bar, broken, pip_size, eq_breach
    )


def _pending_ce(bundle, h1_ts, zone, break_bar, extreme, extreme_bar, broken,
                pip_size, eq_breach, as_of):
    """The CE limit order that should be resting at `as_of`, or None.

    Walks the same state as _resolve_ce (running extreme, leg count, the
    runaway guard) but stops at as_of and reports the CURRENT limit rather
    than hunting a fill. That is the whole point of CE being a limit: the
    level moves as the range extends, so the resting order has to be
    replaced whenever it does.

    No expiry is reported. Unlike an LC stop order, CE's limit has no N=2
    clock; it lives until one of the three aborts fires, and an abort
    returns None so the caller cancels.
    """
    n = len(bundle.ts)
    buffer_price = entry_params.sl_buffer_pips() * pip_size
    sign = zone.sign

    running = bundle.high[break_bar] if zone.bullish else bundle.low[break_bar]
    first_leg = abs(extreme - running)
    legs = 1

    for j in range(break_bar + 1, min(as_of + 1, n)):
        k = h1_bar_containing(h1_ts, bundle.ts[j])
        if k < 0 or k > zone.valid_through:
            return None
        if _left_zone(bundle, j, zone):
            return None

        if zone.bullish:
            running = max(running, bundle.high[j])
        else:
            running = min(running, bundle.low[j])

        height = abs(extreme - running)
        if first_leg > 0 and height > CE_RUNAWAY_MULTIPLE * first_leg:
            return None

        nxt = _internal_break_at(bundle, zone, j)
        if nxt is not None:
            legs += 1
            if legs > CE_MAX_LEGS:
                return None
            extreme, extreme_bar, broken = nxt
            running = bundle.high[j] if zone.bullish else bundle.low[j]
            first_leg = abs(extreme - running)

    height = abs(extreme - running)
    limit_price = extreme + sign * CE_FIB * height
    sl = extreme - sign * buffer_price
    r_distance = abs(limit_price - sl)
    if r_distance < entry_params.min_r_pips() * pip_size:
        return None

    return {
        "model": "CE",
        "direction": "bullish" if zone.bullish else "bearish",
        "order_kind": "limit",
        "trigger_m15": break_bar,
        "host_m15": break_bar,
        "order_price": limit_price,
        "sl": sl,
        "r_distance": r_distance,
        "expires_m15": None,
        "evidence": dict(
            _evidence({"break_m15": break_bar, "level": extreme,
                       "broken_level": broken}, eq_breach),
            extreme_m15=extreme_bar,
            range_running=running,
            legs=legs,
        ),
    }


def _resolve_ce(bundle, h1_ts, zone, break_bar, extreme, extreme_bar,
                broken, pip_size, eq_breach):
    """Walks CE's limit order forward from its break. None if it never fills.

    The range runs from `extreme` (fixed, the broken swing on the far side)
    to the running extreme since the break (moving). So the limit tracks
    price down on a short, and R stays roughly constant instead of
    widening as the leg extends.

    The three aborts, in the user's order:

    1. A SECOND LEG is allowed. If price pulls back shallower than fib50
       and then breaks internal structure again, the range re-anchors and
       CE stays live. Note there is no separate "did it pull back
       shallower" test: still being here means the limit was never
       reached, which is exactly that condition.
    2. TWO LEGS MAXIMUM. A third break gives up.
    3. RUNAWAY. If the leg grows past CE_RUNAWAY_MULTIPLE times the leg
       that broke structure, give up even if 1 and 2 have not fired. This
       is the case where price never pulls back at all, which the leg
       counter alone would follow forever.
    """
    n = len(bundle.ts)
    buffer_price = entry_params.sl_buffer_pips() * pip_size
    sign = zone.sign

    running = bundle.high[break_bar] if zone.bullish else bundle.low[break_bar]
    # The impulse that broke structure, used as the runaway yardstick.
    first_leg = abs(extreme - running)
    legs = 1

    for j in range(break_bar + 1, n):
        # The H1 lifecycle still outranks CE while its order rests.
        k = h1_bar_containing(h1_ts, bundle.ts[j])
        if k < 0 or k > zone.valid_through:
            return None
        if _left_zone(bundle, j, zone):
            return None

        if zone.bullish:
            running = max(running, bundle.high[j])
        else:
            running = min(running, bundle.low[j])

        height = abs(extreme - running)
        if first_leg > 0 and height > CE_RUNAWAY_MULTIPLE * first_leg:
            return None

        # Recomputed every bar: this is the "dynamic fib50" the user
        # described, not a level fixed at the break.
        #
        # The signs, spelled out because all three are easy to invert and
        # an inverted one still produces plausible numbers:
        #   bearish  extreme is the range HIGH, so the limit sits BELOW it
        #            (extreme - h/2) and the stop ABOVE it (extreme + buf)
        #   bullish  extreme is the range LOW, so the limit sits ABOVE it
        #            (extreme + h/2) and the stop BELOW it (extreme - buf)
        limit_price = extreme + sign * CE_FIB * height
        sl = extreme - sign * buffer_price
        r_distance = abs(limit_price - sl)

        # A limit is reached by price coming BACK to it, which is the same
        # direction as coming back to the zone, so zone.bullish is passed
        # through unchanged here.
        if r_distance >= entry_params.min_r_pips() * pip_size and _reaches(
            bundle, j, limit_price, zone.bullish
        ):
            return {
                "model": "CE",
                "direction": "bullish" if zone.bullish else "bearish",
                "trigger_m15": break_bar,
                "host_m15": break_bar,
                "fill_m15": j,
                "fill_price": limit_price,
                "order_price": limit_price,
                "sl": sl,
                "r_distance": r_distance,
                "evidence": dict(
                    _evidence(
                        {"break_m15": break_bar, "level": extreme,
                         "broken_level": broken}, eq_breach
                    ),
                    extreme_m15=extreme_bar,
                    range_running=running,
                    legs=legs,
                ),
            }

        nxt = _internal_break_at(bundle, zone, j)
        if nxt is not None:
            legs += 1
            if legs > CE_MAX_LEGS:
                return None
            extreme, extreme_bar, broken = nxt
            running = bundle.high[j] if zone.bullish else bundle.low[j]
            first_leg = abs(extreme - running)
    return None


def scan_for_entry(bundle, h1_ts, zone, mitigation_bar, pip_size,
                    allowed_sessions=None):
    """The first entry model to FILL on this zone, or None.

    The backtest entry point. Answers a historical question, so it requires
    the fill to have already happened: an order that was placed and never
    tagged produced no trade and is correctly reported as nothing.

    The live bot needs the opposite question. See pending_order_for.
    """
    return _walk(
        bundle, h1_ts, zone, mitigation_bar, pip_size, as_of=None,
        allowed_sessions=allowed_sessions,
    )


def pending_order_for(bundle, h1_ts, zone, mitigation_bar, pip_size, as_of,
                       allowed_sessions=None):
    """The order that should be RESTING right now, or None.

    The live counterpart to scan_for_entry, and it exists because the two
    ask opposite questions. scan_for_entry asks "where did this setup
    fill", which needs the fill in hand. The live bot has to place the
    order BEFORE any fill, then let the broker tag it, which is the only
    way to reproduce what the backtest actually assumes: a resting stop or
    limit at a price derived from the trigger candle.

    Answering "market-order it once the fill bar closes" instead would
    enter at whatever price the fill bar ended at, which can be most of the
    way to the stop. That would quietly discard the M15 precision the whole
    entry layer exists for.

    as_of is the last CLOSED M15 bar. The answer is recomputed from scratch
    on every poll rather than carried in state, so the bot is
    self-correcting: it compares what should be resting against what is
    resting, and places, replaces or cancels accordingly.

    The returned dict adds `expires_m15`, the last bar the order may rest
    on, so the caller knows when to cancel rather than having to re-derive
    the N=2 window.
    """
    if as_of is None or as_of < 0:
        return None
    return _walk(
        bundle, h1_ts, zone, mitigation_bar, pip_size, as_of=as_of,
        allowed_sessions=allowed_sessions,
    )


def _walk(bundle, h1_ts, zone, mitigation_bar, pip_size, as_of,
          allowed_sessions=None):
    """The one scan. as_of None means the historical walk (find the fill),
    an index means the live walk (find the order to rest at that bar).

    bundle: M15Bundle over FULL M15 history. None is a first-class value
        (NAS100 has no M15) and yields None rather than raising.
    h1_ts: the walk frame's timestamp array, i.e. ctx.ts. Naive UTC.
    zone: a Zone.
    mitigation_bar: the H1 bar the qualifying touch landed on.
    pip_size: the instrument's pip, for the stop buffer and MIN_R floor.
    allowed_sessions: which killzone(s) may host a trigger for this
        instrument. None allows both London and NY, the original behavior.

    Returns a dict carrying the model, the trigger bar, the order and stop
    prices, the fill, and an `evidence` sub-dict holding everything the
    factor evaluators need so they never re-derive geometry. None when no
    model fires before the zone dies.
    """
    if bundle is None or len(h1_ts) == 0:
        return None
    if mitigation_bar < 0 or mitigation_bar >= len(h1_ts):
        return None

    start = m15_index_at_or_after(bundle, h1_ts[mitigation_bar])
    if start < 0:
        return None

    order = MODEL_PRECEDENCE.get(zone.primary_tier, DEFAULT_PRECEDENCE)
    # LC-2A's fake break and LC-2B's equals pool both form on the approach
    # leg, which is largely BEFORE the mitigation bar the scan starts on.
    # So the models may look back this far even though no trigger may fire
    # before `start`.
    lookback_start = max(0, start - APPROACH_LOOKBACK_BARS)
    eq_breach = None
    sweep_at = {}
    consumed = set()
    # CE requires the zone to have been mitigated at M15 resolution, not
    # merely on the H1 candle the scan starts from: the H1 bar may have
    # reached the zone in its last quarter hour.
    mitigated = False

    stop = len(bundle.ts) if as_of is None else min(as_of + 1, len(bundle.ts))
    for j in range(start, stop):
        # The H1 lifecycle outranks everything here. A zone H1 has retired
        # cannot be traded no matter how good the M15 geometry looks, and
        # an unresolvable bar (-1) is not an excuse to keep going.
        k = h1_bar_containing(h1_ts, bundle.ts[j])
        if k < 0 or k > zone.valid_through:
            return None

        if eq_breach is None and _reaches(bundle, j, zone.eq, zone.bullish):
            eq_breach = j

        if not mitigated and _mitigates(bundle, j, zone):
            mitigated = True

        # Record LC-1 sweeps even on bars that cannot host a trigger, so
        # the N=2 window can look back to a sweep that happened outside a
        # killzone or before the zone was reached.
        level_row = _lc1_liquidity(bundle, zone, j)
        if level_row is not None:
            sweep_at[j] = level_row

        # Formation closes one candle after the breach. The breaching
        # candle itself is still allowed: see the module docstring.
        if eq_breach is not None and j > eq_breach:
            return None

        if not _in_killzone(bundle, j, allowed_sessions):
            continue

        for model in order:
            # CE resolves itself. Its limit level moves as the range
            # extends, so it cannot be priced once at a host candle the
            # way _resolve_order prices the LC models' stop orders.
            if model == "CE":
                resolved = _try_ce(
                    bundle, h1_ts, zone, j, mitigated, pip_size, eq_breach,
                    as_of,
                )
                if resolved is not None:
                    return resolved
                continue

            setup = None
            if model == "LC-1":
                setup = _try_lc1(bundle, zone, j, sweep_at, consumed)
            elif model == "LC-2A":
                setup = _try_lc2a(bundle, zone, j, lookback_start, consumed)
            elif model == "LC-2B":
                setup = _try_lc2b(bundle, zone, j, lookback_start, consumed)
            if setup is None:
                continue
            # Consumed whether or not the order resolves. A setup that
            # formed and then failed to fill has spent its liquidity: the
            # stops behind that level are gone either way.
            if setup.get("sweep_m15") is not None:
                consumed.add(setup["sweep_m15"])
            if as_of is None:
                resolved = _resolve_order(
                    bundle, zone, setup, pip_size, eq_breach
                )
            else:
                resolved = _pending_order(
                    bundle, zone, setup, pip_size, eq_breach, as_of
                )
            if resolved is not None:
                return resolved

    return None


def _pending_order(bundle, zone, setup, pip_size, eq_breach, as_of):
    """The LC stop order that should be resting at `as_of`, or None.

    Same prices as _resolve_order, no fill search. The two differences that
    matter:

    1. The host is advanced only as far as bars UP TO as_of allow, so the
       answer reflects what is knowable now rather than what will be
       knowable later.
    2. Liveness is checked instead of a fill. An order rests for its host
       candle and the one after, so once as_of passes host + 1 there is
       nothing to place and the caller should cancel.
    """
    a = setup["trigger_m15"]
    n = len(bundle.ts)
    buffer_price = entry_params.sl_buffer_pips() * pip_size

    # Advance the host first, then judge liveness. The other order would
    # report the stale price on the very bar the order re-hosts.
    host = a
    for _ in range(N2_WINDOW - 1):
        nxt = host + 1
        if nxt > as_of or nxt >= n:
            break
        if not _closes_beyond(bundle, nxt, host, zone):
            break
        if _left_zone(bundle, nxt, zone):
            break
        host = nxt

    if as_of > host + 1:
        return None

    order_price, sl = _order_prices(bundle, host, zone, buffer_price)
    r_distance = abs(order_price - sl)
    if r_distance < entry_params.min_r_pips() * pip_size:
        return None

    return {
        "model": setup["model"],
        "direction": "bullish" if zone.bullish else "bearish",
        "order_kind": "stop",
        "trigger_m15": a,
        "host_m15": host,
        "order_price": order_price,
        "sl": sl,
        "r_distance": r_distance,
        # The last bar this order may rest on, so the caller cancels on a
        # clock rather than re-deriving the N=2 window itself.
        "expires_m15": host + 1,
        "evidence": _evidence(setup, eq_breach),
    }


def _resolve_order(bundle, zone, setup, pip_size, eq_breach):
    """Turns a formed setup into an order, a stop, and a fill. None if unusable.

    Decision 6: both prices come from the trigger candle, not from the
    zone. Bearish, a sell stop below its low with the stop above its high.

    Decision 7: candle A hosts. Candle B may re-host when it closes beyond
    A's extreme and has not left the zone. Candle C cancels.

    Decision 8a: the re-host test is against the FAR edge, not eq. Candle
    A may already have conceded eq, so testing B against it would be
    incoherent. "Price went all the way through the zone" is a different
    and stronger statement than "half the zone is consumed".
    """
    a = setup["trigger_m15"]
    buffer_price = entry_params.sl_buffer_pips() * pip_size
    n = len(bundle.ts)

    # A resting stop order is live for its host candle and the one after
    # it, and no longer. That bound is what "candle C means cancel" means:
    # without it the order sits at candle A's level forever and the
    # re-host below can never happen, because the fill scan would always
    # find some later candle first.
    host = a
    for _ in range(N2_WINDOW):
        order_price, sl = _order_prices(bundle, host, zone, buffer_price)
        r_distance = abs(order_price - sl)
        # A trigger too narrow to give a usable stop does not disqualify a
        # re-host: candle B may be wider. So this skips the fill attempt
        # rather than returning.
        if r_distance >= entry_params.min_r_pips() * pip_size:
            fill = _first_fill(bundle, host, min(host + 1, n - 1),
                               order_price, zone)
            if fill is not None:
                return {
                    "model": setup["model"],
                    "direction": "bullish" if zone.bullish else "bearish",
                    "trigger_m15": a,
                    "host_m15": host,
                    "fill_m15": fill,
                    "fill_price": order_price,
                    "order_price": order_price,
                    "sl": sl,
                    "r_distance": r_distance,
                    "evidence": _evidence(setup, eq_breach),
                }

        nxt = host + 1
        if nxt >= n:
            return None
        # Decision 8a: the re-host test is the FAR edge, never eq. Candle
        # A may already have conceded eq, so testing B against it would be
        # incoherent.
        if not _closes_beyond(bundle, nxt, host, zone):
            return None
        if _left_zone(bundle, nxt, zone):
            return None
        host = nxt
    return None


_EVIDENCE_KEYS = (
    "sweep_m15",     # the bar that took the liquidity (LC-1, LC-2B)
    "raid_m15",      # the bar that raided the PBID (LC-2A)
    "break_m15",     # the structure-break bar (LC-2A, CE)
    "broken_level",  # the swing the break cleared (LC-2A, CE)
    "body_closed",   # did a candle CLOSE through it, or only wick (LC-2A, CE)
    "level_row",     # row position into the relevant SweepIndex
    "level",         # the liquidity price the setup is built on
)


def _evidence(setup, eq_breach):
    """What the factor evaluators need, so they never re-derive geometry.

    A fixed key set with None for whatever a given model does not have,
    rather than each model shipping its own shape. backtest/entry_factors.py
    then reads one dict layout instead of branching per model, and a key
    that stops being populated shows up as a factor going quiet rather than
    as a KeyError somewhere else.
    """
    out = {key: setup.get(key) for key in _EVIDENCE_KEYS}
    out["eq_breach_m15"] = eq_breach
    return out


def _order_prices(bundle, host, zone, buffer_price):
    """(order_price, sl) for a stop order hosted on bar `host`."""
    if zone.bullish:
        return bundle.high[host] + buffer_price, bundle.low[host] - buffer_price
    return bundle.low[host] - buffer_price, bundle.high[host] + buffer_price


def _closes_beyond(bundle, candidate, host, zone):
    """Did `candidate` close past the host's extreme, against the trade?

    The re-host trigger. A bearish setup re-hosts when a later candle
    closes ABOVE the current host's high, i.e. the manipulation leg pushed
    further and the old order level is stale.
    """
    if zone.bullish:
        return bundle.close[candidate] < bundle.low[host]
    return bundle.close[candidate] > bundle.high[host]


def _left_zone(bundle, j, zone):
    """Did bar j's wick pass the zone's far edge?"""
    if zone.bullish:
        return bundle.low[j] < zone.far_edge
    return bundle.high[j] > zone.far_edge


def _first_fill(bundle, lo, hi, order_price, zone):
    """First bar in [lo, hi] whose wick reaches the resting order, or None.

    A bounded window, not an open-ended scan: see _resolve_order on why an
    unbounded fill search silently disables the re-host rule.

    The host candle itself is included. A bearish trigger that spikes up
    and closes back down can trade through its own low after the order
    would have been resting there, and starting at host + 1 would drop
    those fills.
    """
    for j in range(lo, hi + 1):
        if zone.bullish:
            if bundle.high[j] >= order_price:
                return j
        elif bundle.low[j] <= order_price:
            return j
        # A candle that leaves the zone entirely takes the setup with it.
        if _left_zone(bundle, j, zone):
            return None
    return None
