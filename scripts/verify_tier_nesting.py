"""Checks the two properties the Daily, 4H and H1 fractal-family designs rest on.

INDEPENDENCE (must pass). The three tiers have to be genuinely
independent, because the strategy reads them against each other: swing
bullish with internal bearish is a pullback within the swing, and internal
bearish with fractal bullish is that pullback making its own pullback. If
running one tier changed another's output, or if the result depended on
the order the tiers ran in, that reading would be meaningless. This is the
property that matters, so a failure here is fatal.

CONTAINMENT (informational). Running one detector at different n happens
to give pivots(n=20) as a subset of pivots(n=8) as a subset of
pivots(n=2), because a larger n's conditions are a strict superset of a
smaller n's. Note the direction: the SMALL-n set is the superset, so the
fast tier sees strictly more pivots, including every pivot outside the
slow tier's range. This is a consequence of three independent runs of one
function, NOT a dependency between tiers, and it says nothing about their
directions agreeing.

A containment failure would mean the tie-tolerance clause interacts with n
in a way the reasoning missed. That is worth knowing and worth writing
down, but it does not invalidate the design, which rests on n being a
single legible scale knob and on there being no timeout. So containment is
reported rather than asserted.

Run from the repo root:  python scripts/verify_tier_nesting.py
Exit code 0 if the independence checks pass, 1 if any fails.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from demo_daily_structure import build_synthetic_daily_data  # noqa: E402
from demo_h1_structure import build_synthetic_h1_data  # noqa: E402
from demo_h4_structure import build_synthetic_h4_data  # noqa: E402
from smc.market_structure.daily_structure import (  # noqa: E402
    DAILY_TIER_PERIODS,
    compute_daily_structures,
)
from smc.market_structure.fractal_detector import (  # noqa: E402
    _is_down_fractal,
    _is_up_fractal,
)
from smc.market_structure.h1_structure import (  # noqa: E402
    H1_TIER_PERIODS,
    compute_h1_structures,
)
from smc.market_structure.h4_structure import (  # noqa: E402
    H4_TIER_PERIODS,
    compute_h4_structures,
)
from smc.market_structure.tiered_fractal_structure import (  # noqa: E402
    compute_tier_structure,
    tier_column_names,
)

OHLC = ["date", "open", "high", "low", "close"]

failures = []
notes = []


def check(label, condition, detail=""):
    """Records a pass or a hard failure."""
    if condition:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s%s" % (label, ("  ->  " + detail) if detail else ""))
        failures.append(label)


def _fractal_indices(highs, lows, n):
    """The set of candle indices confirming as fractals at this n."""
    length = len(highs)
    up = set()
    down = set()
    for i in range(length):
        if i + n < length:
            if _is_up_fractal(highs, i, n):
                up.add(i)
            if _is_down_fractal(lows, i, n):
                down.add(i)
    return up, down


def _plateau_frame():
    """A tiny frame containing runs of exactly equal highs and lows.

    _TIE_TOLERANCE in fractal_detector.py is fixed at 4 regardless of n,
    and tolerates ties only on the PAST side. A plateau is the only shape
    that reaches that branch, and therefore the only place containment
    between different n could plausibly break, so it gets its own fixture
    rather than relying on the main one happening to contain one.
    """
    highs = []
    lows = []

    # Two plateaus, deliberately straddling the tolerance boundary:
    #   - a 4-candle top plateau, WITHIN _TIE_TOLERANCE, which should
    #     confirm an up pivot on its last candle via the tie branch.
    #   - a 6-candle top plateau, BEYOND the tolerance, which should not
    #     confirm at all.
    # Having both means the fixture proves the tie branch is reachable
    # rather than merely not crashing.
    #
    # Two construction rules matter, and getting either wrong makes the
    # fixture silently prove nothing (it reports zero up pivots at every n):
    #   1. The run leaving a plateau must START STRICTLY BEYOND it. Tie
    #      tolerance applies only to the PAST side, so the last plateau
    #      candle confirms only if what follows is strictly lower. A
    #      descent that begins at the plateau's own value ties with it and
    #      fails the strict future-side test.
    #   2. The run arriving at a plateau must likewise stop strictly short
    #      of it, or those candles join the plateau and push the tie run
    #      past the tolerance.

    # Rise to 112, then a 4-candle plateau at 113 (within tolerance).
    for value in range(0, 13):
        highs.append(100.0 + value)
        lows.append(99.0 + value)
    for _ in range(4):
        highs.append(113.0)
        lows.append(112.0)
    # Fall to 100, starting strictly below the plateau.
    for value in range(12, -1, -1):
        highs.append(100.0 + value)
        lows.append(99.0 + value)
    # A 3-candle bottom plateau at 99/100, then rise to 112 again.
    for _ in range(3):
        highs.append(100.0)
        lows.append(99.0)
    for value in range(1, 14):
        highs.append(100.0 + value)
        lows.append(99.0 + value)
    # A 6-candle plateau at 114 (beyond tolerance), then fall away.
    for _ in range(6):
        highs.append(114.0)
        lows.append(113.0)
    for value in range(12, -1, -1):
        highs.append(100.0 + value)
        lows.append(99.0 + value)

    dates = pd.date_range("2024-01-01", periods=len(highs), freq="4h")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [(h + l) / 2 for h, l in zip(highs, lows)],
            "high": highs,
            "low": lows,
            "close": [(h + l) / 2 for h, l in zip(highs, lows)],
        }
    )


def verify_independence(df, tier_periods, compute_fn):
    """Each tier's output must not depend on the others running.

    tier_periods/compute_fn parameterize this over a tier family (H4 or
    H1) rather than hardcoding H4's names, since the two families run the
    identical checks. fast/mid/slow are derived from tier_periods'
    declared order (fast to slow, per H4_TIER_PERIODS/H1_TIER_PERIODS'
    own ordering) rather than hardcoded tier-name strings.
    """
    print("Independence")

    fast, mid, slow = list(tier_periods)
    combined = compute_fn(df)

    # 1. A tier computed alone must match the same tier inside the
    #    combined run, column for column.
    for tier, n in tier_periods.items():
        alone = compute_tier_structure(df[OHLC], prefix=tier, n=n)
        cols = tier_column_names(tier)
        same = all(
            alone[col].equals(combined[col].reset_index(drop=True)) for col in cols
        )
        check("%s alone matches %s inside the combined run" % (tier, tier), same)

    # 2. Order must not matter. Running the tiers back to front has to
    #    produce the same values as front to back.
    reversed_order = df
    for tier in reversed(list(tier_periods)):
        reversed_order = compute_tier_structure(
            reversed_order, prefix=tier, n=tier_periods[tier]
        )
    all_cols = [c for tier in tier_periods for c in tier_column_names(tier)]
    order_free = all(
        combined[col].equals(reversed_order[col]) for col in all_cols
    )
    check("tier order does not change any tier's output", order_free)

    # 3. A manual restart on ONE tier must leave the others untouched.
    #    This is the concrete way coupling would show up in practice.
    restart = pd.Series([False] * len(df))
    restart.iloc[len(df) // 2] = True
    restarted = compute_fn(df, manual_restarts={fast: restart})

    fractal_moved = not restarted["%s_swing_high" % fast].equals(
        combined["%s_swing_high" % fast]
    )
    check("a restart on %s actually changes %s" % (fast, fast), fractal_moved)

    for tier in (mid, slow):
        untouched = all(
            restarted[col].equals(combined[col]) for col in tier_column_names(tier)
        )
        check("a restart on %s leaves %s untouched" % (fast, tier), untouched)


def verify_disagreement(df, tier_periods, compute_fn):
    """The fixture must actually exercise tier disagreement."""
    print("Disagreement (the pullback cascade the strategy reads)")

    combined = compute_fn(df)
    tiers = list(tier_periods)
    fast, mid, slow = tiers

    disagreements = 0
    cascade = 0
    for _, row in combined.iterrows():
        states = {}
        for tier in tiers:
            value = row["%s_structure" % tier]
            if not pd.isna(value):
                states[tier] = value
        if len(set(states.values())) > 1:
            disagreements += 1
        # The specific three-scale cascade: the slow tier up, the middle
        # tier down against it, and the fast tier turned back up again.
        if (
            states.get(slow) == "bullish"
            and states.get(mid) == "bearish"
            and states.get(fast) == "bullish"
        ):
            cascade += 1

    check(
        "the tiers disagree on direction somewhere (%d candles)" % disagreements,
        disagreements > 0,
        "a fixture where they never disagree is not exercising the thing "
        "that matters",
    )
    check(
        "the full swing-up / internal-down / fractal-up cascade occurs "
        "(%d candles)" % cascade,
        cascade > 0,
    )


def report_containment(label, df, tier_periods):
    """Reports, rather than asserts, the pivot-set subset property."""
    print("Containment on %s (informational)" % label)

    highs = df["high"].tolist()
    lows = df["low"].tolist()

    # Slowest to fastest, so each is checked against the next one faster.
    ordered = sorted(set(tier_periods.values()), reverse=True)
    found = {n: _fractal_indices(highs, lows, n) for n in ordered}

    for n in ordered:
        up, down = found[n]
        print("    n=%-3d up pivots: %-4d down pivots: %d" % (n, len(up), len(down)))

    for slower, faster in zip(ordered, ordered[1:]):
        for side, index in (("up", 0), ("down", 1)):
            slow_set = found[slower][index]
            fast_set = found[faster][index]
            extra = sorted(slow_set - fast_set)
            if extra:
                note = (
                    "containment BROKEN on %s: %s side, n=%d pivots at %s are "
                    "not pivots at n=%d"
                    % (label, side, slower, extra[:5], faster)
                )
                print("    NOTE  %s" % note)
                notes.append(note)
            else:
                print(
                    "    ok    n=%d %s pivots are a subset of n=%d"
                    % (slower, side, faster)
                )


def verify_wrapper_parity():
    """The generalized wrapper must be a refactor, not a reimplementation.

    compute_tier_structure at n=2 has to reproduce the existing Daily
    fractal tier exactly. If it does not, the generalization changed
    behavior somewhere, and every 4H/H1/Daily number computed with it is
    suspect. Run on the DAILY fixture, since that is what the original
    fractal tier was validated against.
    """
    print("Wrapper parity against the existing Daily fractal tier")

    from demo_swing_structure import build_synthetic_data
    from smc.market_structure.fractal_structure import compute_fractal_structure

    daily, manual_restart, _hold = build_synthetic_data()
    ohlc = daily[OHLC]

    for restart, label in ((None, "no restart"), (manual_restart, "with restart")):
        existing = compute_fractal_structure(ohlc, n=2, manual_restart=restart)
        generalized = compute_tier_structure(
            ohlc, prefix="fractal", n=2, manual_restart=restart
        )
        same = all(
            existing[col].equals(generalized[col])
            for col in tier_column_names("fractal")
        )
        check("compute_tier_structure matches compute_fractal_structure (%s)"
              % label, same)

    # The actually-shipped Daily fractal tier, not just the generic
    # wrapper: compute_daily_structures(df)'s daily_fractal_* columns must
    # match compute_fractal_structure(df, n=2)'s columns value for value
    # (module names differ; a manual_restart is not passed here, since
    # compute_daily_structures keys restarts per tier rather than taking
    # one shared Series). This closes the loop the checks above don't
    # quite prove: that the live daily_structure.py entry point, not just
    # compute_tier_structure in isolation, is still byte-identical to the
    # pre-port oracle.
    daily_shipped = compute_daily_structures(daily, tier_periods={"daily_fractal": 2})
    oracle = compute_fractal_structure(ohlc, n=2)
    rename = {
        "fractal_swing_high": "daily_fractal_swing_high",
        "fractal_swing_low": "daily_fractal_swing_low",
        "fractal_high_event": "daily_fractal_high_event",
        "fractal_low_event": "daily_fractal_low_event",
        "fractal_structure": "daily_fractal_structure",
        "fractal_structure_event": "daily_fractal_structure_event",
    }
    shipped_matches_oracle = all(
        oracle[old].reset_index(drop=True).equals(
            daily_shipped[new].reset_index(drop=True)
        )
        for old, new in rename.items()
    )
    check(
        "compute_daily_structures' daily_fractal tier matches the "
        "pre-port oracle (compute_fractal_structure, n=2)",
        shipped_matches_oracle,
    )


def verify_atr_filter(df, label):
    """Smoke test for the disabled-by-default significance filter.

    The filter ships at 0.0 and is expected to stay there unless the
    regime testing on real charts shows a need. It is still exercised here
    so that it is not dead on arrival the day someone switches it on. Not
    parameterized by tier_periods/compute_fn like the checks above,
    because it always tests a fixed prefix="t", n=2 tier built directly
    from OHLC, independent of which family's fixture supplies the candles.
    """
    print("ATR significance filter on %s (ships disabled, tested anyway)" % label)

    ohlc = df[OHLC]

    # Tested on the FAST tier (n=2), because that is where small legs
    # actually exist. On this fixture the n=8 legs run at roughly 2.5 ATR,
    # so a 0.75 threshold rejects nothing there at all: informative about
    # the fixture, useless as a test of the filter.
    thresholds = [0.0, 0.5, 1.0, 2.0, 4.0]
    levels = []
    for threshold in thresholds:
        frame = compute_tier_structure(
            ohlc, prefix="t", n=2, min_atr_separation=threshold
        )
        levels.append(frame["t_swing_high"].dropna().nunique())

    print(
        "    high levels kept by threshold: %s"
        % ", ".join(
            "%.2f->%d" % (t, n) for t, n in zip(thresholds, levels)
        )
    )
    # Deliberately REPORTED, not asserted. The filter measures each new
    # pivot against the last confirmed pivot on the opposite side, and that
    # reference is itself subject to the filter. So rejecting more pivots
    # moves the reference point, which can re-admit pivots that a lower
    # threshold rejected. Raising the knob therefore does NOT monotonically
    # reduce noise: on this fixture 2.00 ATR keeps 38 levels while 4.00
    # keeps 56.
    #
    # This is the path dependence named as a cost in
    # fractal_detector.py's docstring, showing up concretely. It is a real
    # usability trap for anyone who ever switches the filter on expecting
    # a monotonic dial, which is a further reason it ships at 0.0.
    monotonic = all(later <= earlier for earlier, later in zip(levels, levels[1:]))
    if not monotonic:
        note = (
            "the ATR filter is NOT monotonic in its threshold, because the "
            "opposite-side reference it measures against is itself filtered: "
            "%s" % ", ".join("%.2f->%d" % (t, n) for t, n in zip(thresholds, levels))
        )
        print("    NOTE  %s" % note)
        notes.append(note)
    else:
        print("    ok    raising the threshold did not increase levels kept here")

    check(
        "a large threshold rejects at least some pivots (%d -> %d)"
        % (levels[0], levels[-1]),
        levels[-1] < levels[0],
    )

    off = compute_tier_structure(ohlc, prefix="t", n=2, min_atr_separation=0.0)
    on = compute_tier_structure(ohlc, prefix="t", n=2, min_atr_separation=4.0)

    # A rejected pivot must leave the PREVIOUS level standing, never blank
    # it out. Once a side has seeded, it must never go missing again.
    seeded = on["t_swing_high"].notna()
    first_seed = seeded.idxmax() if seeded.any() else None
    check(
        "a rejected pivot keeps the previous level rather than clearing it",
        first_seed is not None and on["t_swing_high"].iloc[first_seed:].notna().all(),
    )

    # The filter must be inert during ATR warm-up rather than rejecting,
    # so the very first pivot on each side still seeds at the same candle
    # it would have without the filter.
    off_first = off["t_swing_high"].notna().idxmax()
    on_first = on["t_swing_high"].notna().idxmax()
    check(
        "the filter does not delay the first seed (candle %d both ways)"
        % off_first,
        off_first == on_first,
    )


def main():
    families = (
        ("Daily", DAILY_TIER_PERIODS, compute_daily_structures, build_synthetic_daily_data),
        ("4H", H4_TIER_PERIODS, compute_h4_structures, build_synthetic_h4_data),
        ("H1", H1_TIER_PERIODS, compute_h1_structures, build_synthetic_h1_data),
    )

    for label, tier_periods, compute_fn, build_fn in families:
        print("=== %s ===" % label)
        df, _restarts = build_fn()
        verify_independence(df, tier_periods, compute_fn)
        print()
        verify_disagreement(df, tier_periods, compute_fn)
        print()
        verify_atr_filter(df, label)
        print()
        report_containment("the %s demo fixture" % label, df, tier_periods)
        print()

    verify_wrapper_parity()
    print()
    # n values are currently identical across families (2/8/20), so the
    # plateau's tie-tolerance path only needs checking once.
    report_containment(
        "a plateau fixture (tie-tolerance path)", _plateau_frame(), H4_TIER_PERIODS
    )
    print()

    if notes:
        print("Informational notes (not failures):")
        for note in notes:
            print("  - %s" % note)
        print("  Record these in roadmap/detection-method-decision.md.")
        print()

    if failures:
        print("FAILED: %d independence check(s)" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1

    print("All independence checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
