"""Runs the three 4H tiers on synthetic 4H candles and prints them.

Why this does not reuse demo_swing_structure.py's generator
-----------------------------------------------------------
build_synthetic_data() advances one CALENDAR DAY per candle, and its
segments are engineered specifically to trip the lookback tier's rules:
exactly 45 ranging candles to cover the cold start, exactly 65 to make
both sides time out together, a stretch with hold_timeout on, and so on.
The 4H tiers use none of those rules (no lookback window, no timeout, no
hold), so that fixture would be testing machinery that is not there.

What this generator needs instead is scale separation: legs big enough
that an n=20 fractal confirms, each containing smaller pulls that only
n=2 picks up. That is what makes the difference between the tiers visible
in the output rather than merely asserted.

Run from the repo root:  python scripts/demo_h4_structure.py
"""

import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smc.market_structure.h4_structure import (  # noqa: E402
    H4_TIER_PERIODS,
    compute_h4_structures,
)

# One 4H candle is four hours. Six per weekday, none at the weekend, which
# is what real forex 4H data looks like and what the FXR scripts' bar
# spacing inference expects to see (their rule takes the MINIMUM gap
# between candles precisely so a weekend gap cannot fool it).
CANDLE_HOURS = 4
CANDLES_PER_DAY = 6

# Wick padding, so highs and lows are not simply max/min of open and
# close. Keeps the fractal test reading actual extremes.
WICK_PAD = 0.35

# How many candles' worth of progress a minor pull gives back. Must be
# greater than the fast tier's n (2) for the pulls to register there, and
# comfortably less than the middle tier's n (8) so they stay invisible to
# it. See _minor_leg for why this is a multiple of the step rather than an
# absolute price move.
MINOR_PULL_STEPS = 2.6


def _advance(moment):
    """Next 4H candle timestamp, skipping Saturday and Sunday."""
    moment = moment + datetime.timedelta(hours=CANDLE_HOURS)
    while moment.weekday() >= 5:
        moment = moment + datetime.timedelta(days=1)
        moment = moment.replace(hour=0)
    return moment


def _minor_leg(rows, moment, price, target, count, pull_every=0, pull_steps=0.0):
    """The smallest scale: walks price to `target` over `count` candles.

    pull_every injects a single-candle counter-move every N candles, of
    depth pull_steps candles' worth of progress. Those pulls are what
    makes the fast tier fast.

    pull_steps, NOT an absolute size, and that matters. Whether a pull
    registers as an n-fractal depends entirely on how it compares to the
    leg's own slope: inside a rising leg, a dip only becomes a 2-candle
    low if it gives back MORE than 2 candles' worth of progress, because
    otherwise price two candles ago was lower still. So pull_steps has to
    exceed n for the fast tier to see the pull, and stay well under the
    slow tiers' n for them not to. pull_steps of about 2.6 puts the pulls
    squarely visible to n=2 and invisible to n=8, which is precisely the
    scale separation this fixture exists to create.

    Expressing the depth in absolute price instead is what an earlier
    version did, and it silently produced pulls shallower than the slope:
    n=2 and n=8 then found the exact same 17 pivots, because only the
    medium turning points registered at all.
    """
    # Which candles are counter-moves rather than progress.
    pull_at = set()
    if pull_every:
        pull_at = {i for i in range(1, count) if i % pull_every == 0}

    # The step has to be solved for AFTER accounting for the pulls, not
    # simply distance/count. A pull replaces that candle's progress with a
    # move in the opposite direction, so a naive step leaves the leg short
    # of its target and, because the pull sign flips with the leg's
    # direction, it shortchanges falling legs more than rising ones. An
    # earlier version of this generator did exactly that, and the result
    # was a price path that only ever drifted upward: the major down legs
    # never actually declined, so the low side of every tier never broke
    # and no tier ever turned bearish.
    #
    # With pull_move = -pull_steps * step, the distance D over P progress
    # candles and K pulls is D = step * (P - K * pull_steps), so step
    # follows directly. P - K*pull_steps must stay positive, which is why
    # pull_every cannot be small relative to pull_steps.
    n_pulls = len(pull_at)
    progress_candles = count - n_pulls
    denominator = progress_candles - n_pulls * pull_steps
    if denominator <= 0:
        raise ValueError(
            "pulls overwhelm the leg: %d progress candles cannot absorb %d "
            "pulls of %.2f steps each" % (progress_candles, n_pulls, pull_steps)
        )
    step = (target - price) / denominator
    pull_move = -pull_steps * step
    direction = 1.0 if target >= price else -1.0
    pull_size = abs(pull_move)

    for i in range(count):
        is_pull = i in pull_at
        move = pull_move if is_pull else step

        open_price = price
        close_price = price + move
        high = max(open_price, close_price) + WICK_PAD
        low = min(open_price, close_price) - WICK_PAD

        # A pull candle gets an extra wick in the direction it pulled, so
        # that it is a strict local extreme.
        #
        # Without this, a pull produces NO fractal at all, at any n. The
        # next candle opens exactly where the pull closed, so with equal
        # padding on both sides the two candles share an identical low (or
        # high), and _future_side_strict requires the neighbour to be
        # STRICTLY beyond the pivot. The tie tolerance in the detector only
        # applies to the past side, never the future side, by design.
        # Symptom when this is missing: n=2 and n=8 find the exact same
        # pivots, because only the medium turning points register and the
        # minor pulls are invisible to both.
        pull_wick = pull_size * 0.4
        if is_pull:
            if direction > 0:
                low = low - pull_wick
            else:
                high = high + pull_wick

        rows.append(
            {
                "date": moment,
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close_price, 4),
            }
        )
        price = close_price
        moment = _advance(moment)
    return moment, price


def _major_leg(
    rows,
    moment,
    price,
    target,
    cycles=4,
    advance_candles=12,
    retrace_candles=10,
    retrace_fraction=0.45,
):
    """The largest scale: a net move to `target` built from sub-swings.

    Advances `cycles` times toward the target, retracing part of each
    advance in between. That gives the three scales the three tiers need,
    in one shape:

      - the single-candle pulls inside each advance      -> n=2 only
      - the retrace turning points between advances      -> n=8 confirms,
        because a retrace carries price back past where it was 8 candles
        earlier, while 20 candles earlier is still beyond it
      - this leg's own start and end                     -> n=20 confirms

    It is also what produces tier DISAGREEMENT, which is the property the
    strategy actually reads. Once a major up-leg has broken the previous
    n=20 high, the swing tier is bullish. Every retrace inside the leg
    then breaks the n=8 low, turning the internal tier bearish while the
    swing tier stays bullish: the swing pullback phase. A bounce inside
    that retrace flips the n=2 tier bullish again, giving the full
    three-scale cascade on the same candle.

    Sizing: with advance A and retrace R = retrace_fraction * A, net
    travel is cycles*A - (cycles-1)*R, so A is solved for the requested
    distance rather than guessed.
    """
    distance = target - price
    denominator = cycles - (cycles - 1) * retrace_fraction
    advance = distance / denominator
    retrace = retrace_fraction * advance

    for i in range(cycles):
        moment, price = _minor_leg(
            rows,
            moment,
            price,
            price + advance,
            advance_candles,
            pull_every=5,
            pull_steps=MINOR_PULL_STEPS,
        )
        if i < cycles - 1:
            moment, price = _minor_leg(
                rows,
                moment,
                price,
                price - retrace,
                retrace_candles,
                pull_every=6,
                pull_steps=MINOR_PULL_STEPS,
            )
    return moment, price


def _plateau(rows, moment, price, count):
    """A run of candles sharing the same high, to exercise tie handling.

    fractal_detector.py tolerates a run of up to 4 candles tied with the
    pivot's own high or low on the past side (_TIE_TOLERANCE), ported
    deliberately from the Pine reference rather than simplified away. A
    plateau is the only shape that reaches that code, and it is also the
    one place where the pivot-set containment between different n could
    plausibly break, so the fixture has to contain one.
    """
    for _ in range(count):
        rows.append(
            {
                "date": moment,
                "open": round(price - 0.2, 4),
                "high": round(price + WICK_PAD, 4),
                "low": round(price - 0.6, 4),
                "close": round(price, 4),
            }
        )
        moment = _advance(moment)
    return moment, price


def build_synthetic_h4_data():
    """Builds 4H candles with deliberately separated swing scales.

    Returns (df, manual_restarts). The layout, in order:

      1. Rise 100 -> 130. Establishes the first major top.
      2. Fall 130 -> 108. Confirms that top at n=20, and sets a major low
         well below everything that follows, which is what lets later
         pullbacks break the internal low WITHOUT breaking the swing low.
      3. Rise 108 -> 152. Takes out the first major top, so the swing tier
         turns bullish. Its internal retraces then flip the internal tier
         bearish while the swing tier stays bullish: the disagreement the
         strategy reads as a swing pullback.
      4. Fall 152 -> 122. Deep, but deliberately stopping above the 108
         major low, so the swing tier does NOT flip on it.
      5. A tight range around 122, where consecutive pivots sit a fraction
         of one candle's range apart. This is the one regime the optional
         ATR filter exists for, so the fixture has to contain it for the
         filter smoke test to show any difference at all.
      6. A plateau of equal highs, for the tie-tolerance path.
      7. Rise 122 -> 168, taking out the second major top.

    Each major leg is built by _major_leg out of sub-swings, so all three
    scales are present rather than only the fastest one.
    """
    rows = []
    # A Monday, so the weekend skipping in _advance has something to do.
    moment = datetime.datetime(2024, 1, 1, 0, 0)
    price = 100.0

    moment, price = _major_leg(rows, moment, price, 130.0)
    moment, price = _major_leg(rows, moment, price, 108.0)
    moment, price = _major_leg(rows, moment, price, 152.0)
    moment, price = _major_leg(rows, moment, price, 122.0)

    # Tight range: the moves here are small next to the wick padding, so
    # the pivots are ordinally real but not tradeable swings.
    moment, price = _minor_leg(
        rows, moment, price, 122.5, 24, pull_every=6, pull_steps=MINOR_PULL_STEPS
    )

    moment, price = _plateau(rows, moment, price, 6)

    moment, price = _major_leg(rows, moment, price, 168.0)

    df = pd.DataFrame(rows)

    # No restarts fire in this fixture. Built per-tier anyway to show the
    # shape compute_h4_structures expects, and to prove a restart on one
    # tier is expressible without touching the others.
    manual_restarts = {
        tier: pd.Series([False] * len(df)) for tier in H4_TIER_PERIODS
    }
    return df, manual_restarts


def _same(a, b):
    """Equality that treats two missing values as equal.

    Needed because the level columns hold None during warm-up, which
    pandas stores as NaN, and NaN != NaN. A naive `a != b` therefore
    reports a fresh pivot on every single warm-up candle, which silently
    inverts the whole point of this demo: the slow tier warms up for
    longer, so it would appear to update MORE often than the fast tier.
    """
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return a == b


def _state(row, tier):
    """That tier's direction as text, with undetermined shown as a dash.

    Not `value or "-"`: an undetermined structure reads back as NaN, and
    NaN is TRUTHY, so that idiom prints the literal "nan".
    """
    value = row["%s_structure" % tier]
    return "-" if pd.isna(value) else value


def _pivot_update_count(result, tier):
    """How many times that tier's swing high or low actually moved.

    Warm-up candles are skipped entirely rather than counted, so this is
    a count of confirmed pivots, not of column writes.
    """
    updates = 0
    prev_high = None
    prev_low = None
    started = False
    for _, row in result.iterrows():
        high = row["%s_swing_high" % tier]
        low = row["%s_swing_low" % tier]
        if pd.isna(high) and pd.isna(low):
            continue
        if not started:
            started = True
        elif not _same(high, prev_high) or not _same(low, prev_low):
            updates += 1
        prev_high = high
        prev_low = low
    return updates


def main():
    df, manual_restarts = build_synthetic_h4_data()
    result = compute_h4_structures(df, manual_restarts=manual_restarts)

    tiers = list(H4_TIER_PERIODS)

    print("4H candles: %d" % len(result))
    print("First: %s   Last: %s" % (result["date"].iloc[0], result["date"].iloc[-1]))
    print()

    print("Pivot updates per tier (expect fast tier > slow tier):")
    for tier in tiers:
        print(
            "  %-12s n=%-3d pivot updates: %3d   structure flips: %d"
            % (
                tier,
                H4_TIER_PERIODS[tier],
                _pivot_update_count(result, tier),
                result["%s_structure_event" % tier].notna().sum(),
            )
        )
    print()

    print("Rows where any tier's structure changed:")
    header = "%-18s | %-24s | %-24s | %-24s" % (
        "date",
        "h4_fractal",
        "h4_internal",
        "h4_swing",
    )
    print(header)
    print("-" * len(header))
    for _, row in result.iterrows():
        events = [row["%s_structure_event" % tier] for tier in tiers]
        if not any(pd.notna(event) for event in events):
            continue
        print(
            "%-18s | %-24s | %-24s | %-24s"
            % (
                row["date"].strftime("%Y-%m-%d %H:%M"),
                _state(row, "h4_fractal"),
                _state(row, "h4_internal"),
                _state(row, "h4_swing"),
            )
        )
    print()

    # Tier disagreement is the point, not a defect: swing bullish with
    # internal bearish is the swing pullback phase. Counted here so a
    # fixture that never produces it is obvious at a glance.
    disagreements = 0
    for _, row in result.iterrows():
        states = {
            row["%s_structure" % tier]
            for tier in tiers
            if not pd.isna(row["%s_structure" % tier])
        }
        if len(states) > 1:
            disagreements += 1
    print(
        "Candles where the tiers disagree on direction: %d of %d"
        % (disagreements, len(result))
    )
    print("(Disagreement is expected. It is how a pullback within a larger")
    print(" move shows up across the three scales.)")


if __name__ == "__main__":
    main()
