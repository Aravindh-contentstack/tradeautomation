Detection method decision: why 4H uses one mechanism at three scales

Written July 28 2026, when 4H structure was implemented. Read this before
porting to H1, or before "fixing" anything that looks inconsistent below.

## The decision

All three 4H tiers run the SAME mechanism, a Williams Fractal, at three
different values of n:

| Tier | n | FXR linewidth | What it marks |
|---|---|---|---|
| `h4_fractal` | 2 | 2 | minor pulls |
| `h4_internal` | 8 | 3 | intermediate legs |
| `h4_swing` | 20 | 4 | major 4H legs |

Daily is different, and was deliberately left alone: it runs three
different mechanisms (a trailing lookback window with a timeout clock, an
ATR zigzag, and a Williams Fractal at n=2).

## The tiers are independent, and disagreement is the signal

This is the most important thing on this page, because "one mechanism,
three tiers" reads as though the tiers were coupled. They are not.

Each tier is its own detector call on its own OHLC slice, with its own
break state machine, its own manual restart, and its own
`compute_market_structure` pass. No tier reads another's state, no tier is
bounded or clipped by another's range, and no tier constrains another's
direction. All of the following are expected and correct:

- Swing bullish while internal is bearish. That is the swing pullback
  phase.
- Internal bearish while fractal is bullish. That is the pullback making
  its own pullback.
- After price breaks a swing high, internal and fractal pivots forming
  freely OUTSIDE the old swing range, until a later swing pivot engulfs
  them.

`scripts/verify_tier_nesting.py` asserts the independence, and also
asserts that the demo fixture actually produces the full
swing-up / internal-down / fractal-up cascade. If a future change makes
the tiers agree more, that is a regression, not an improvement.

## Why not copy Daily's mechanism mix onto 4H

Two problems, both of which get worse with every timeframe added.

**1. The timeout makes cross-tier reading unreliable.** Daily's swing tier
has `timeout_candles=65`: after 65 candles with no redraw, it force-redraws
the level. Nothing in the market caused that. Since the whole point of
three tiers is reading them against each other, a swing level that moves
for bookkeeping reasons means a genuine pullback cascade and an artefact
look identical. That is the single strongest argument against carrying the
mechanism forward.

**2. The scale relationship is neither legible nor tunable.** With three
mechanisms, "internal" has no defined scale relative to "swing". It is
whatever `reversal_multiplier * ATR` happens to produce, which drifts with
volatility. There is no single knob that answers "make the internal tier a
bit coarser". `window_extremes` compounds it by picking the highest high of
a TRAILING window ending at "now", which can be far in the past and
unrelated to current structure. And neither 45 nor 65 has any principled 4H
equivalent.

## Why the fractal mechanism specifically

Ranked on what matters for automation, it is the most robust of the three:

- **Williams fractal.** Purely local and deterministic. No accumulated
  state, no ATR warm-up, no arbitrary timeout, no path dependence. Exactly
  known confirmation lag of n candles. The same rule works identically on
  any timeframe and instrument with no recalibration. It is also the
  textbook definition of a swing point, so it matches what you would draw
  by hand.
- **ATR zigzag.** Volatility-normalised, which is genuinely good, but
  path-dependent: the bootstrap phase determines everything after it. And
  `reversal_multiplier = 1.5` is a magic number.
- **Lookback plus timeout.** Two ungrounded constants, a trailing rather
  than centred window, and the timeout problem above.

A fractal at large n IS a swing detector, and a better-behaved one than a
trailing window: the pivot must be the extreme of a window CENTRED on it,
and no timeout is needed because a stale level is replaced the moment the
next fractal confirms. n=20 came from the user observing that a Williams
period near 20 mapped Daily swing structure cleanly in TradingView. n=2 is
the Pine reference's own default.

**n=8 is the one number not taken from an observation.** It is roughly
geometric between 2 and 20. Treat it as provisional.

## Containment: a consequence, not a coupling

Running one detector at different n gives pivot-set containment:

```
pivots(n=20)  is a subset of  pivots(n=8)  is a subset of  pivots(n=2)
```

In `_future_side_strict`, requiring 20 candles strictly beyond implies the
first 2 are. In `_past_side_tolerant`, `_TIE_TOLERANCE` is fixed at 4
regardless of n and the strict run starts at the same index, so any k
satisfying n=20 also satisfies n=2.

**Note the direction.** The SMALL-n set is the superset. The fast tier sees
strictly more pivots, including every pivot outside the slow tier's range.
Nothing is clipped to anything.

This is a fact about three independent runs of one function. It is not a
dependency, and it says nothing about the tiers' directions agreeing. All
it buys is confidence that the tiers measure the same kind of object at
different scales, so a coarser tier never marks a level the finer tier
would not even recognise as a pivot. `verify_tier_nesting.py` reports it
rather than asserting it: a failure there would be informative, not fatal.

Measured on the demo fixture: n=20 finds 2 up and 2 down pivots, n=8 finds
17 and 17, n=2 finds 63 and 66. Containment held on both the main fixture
and a purpose-built plateau fixture that exercises the tie-tolerance path.

## The ATR significance filter: built, shipped off

A Williams Fractal is a purely ORDINAL test: is this candle's high the
highest of the 2n+1 candles centred on it? It never looks at magnitude. So
it is invariant to price level, to instrument, and even to volatility LEVEL
(scaling every price preserves the ordering, so the identical candles stay
pivots). What it is not invariant to is the TREND-TO-NOISE RATIO of the
path.

Three regimes behave differently, and only the first is an ATR problem:

**1. Tight consolidation. The only ATR case.** A small n confirms pivots
separated by a fraction of one candle's range. Ordinally valid, but not a
swing anyone can trade.

**2. Strong one-directional trend. ATR does not help.** Large-n fractals
become rare, because each candidate high is exceeded before n candles pass
on its future side, so the level sits well below price with no new
confirmation. That is the honest answer (no swing high has confirmed yet)
rather than a fault. It is also exactly the situation Daily's timeout was
invented to paper over.

**3. Gaps and news spikes. ATR does not help.** A single spike candle is
the extreme of its whole window, so it confirms at ANY n, with the wick as
the level. This needs a wick or body filter, which is queued in
market-structure.md rather than built.

Need is concentrated in ONE tier. The n=2 tier positively wants its noisy
pivots, since that is what makes it the fast tier. At n=20 the market would
have to stay range-bound for 41 straight candles for a pivot to be trivial.
`h4_internal` at n=8 is the plausible case, most likely on 4H XAU.

So the filter ships at 0.0, fully disabled, with no ATR even computed. It
exists as a parameter purely because adding it later would mean a second
porting round across six FXR scripts, which is the expensive part.

It measures a new pivot's distance from the last confirmed pivot on the
OPPOSITE side. That makes it a leg-size test ("was the swing big enough"),
which is the question worth asking. Measuring against the same side would
ask "did the level move far enough", which says nothing about whether a
swing occurred.

### Three costs if it is ever switched on

1. It reintroduces a warm-up period.
2. It reintroduces path dependence.
3. **It is not a monotonic dial.** This one is not obvious and was found by
   testing. The opposite-side pivot it measures against is ITSELF filtered,
   so rejecting more pivots moves the reference point and can re-admit
   pivots that a lower threshold rejected. Measured on the demo fixture at
   n=2, high levels kept by threshold: 0.00 to 63, 0.50 to 57, 1.00 to 56,
   2.00 to 38, 4.00 to 56. Anyone tuning this should expect to search
   values rather than turn a knob one way.
   `verify_tier_nesting.py` prints this curve for exactly that reason.

Costs 1 and 2 are two of the properties whose absence made the fractal
mechanism attractive in the first place, which is a further reason to leave
it off.

## Still to do: the three-regime table

The n values came from eyeballing a single TradingView range, which samples
one trend-to-noise regime. Before locking defaults, run this on real XAU
and EU 4H charts:

| Regime | What to pick | What to expect |
|---|---|---|
| Tight consolidation | a multi-day Asian-session range | `h4_fractal` marks minor pulls (fine). `h4_internal` and `h4_swing` should NOT mark levels a few pips apart. |
| Strong one-directional trend | a sustained multi-week leg | `h4_swing` holds its last confirmed pivot well below price with no new confirmation. Confirm this reads as acceptable rather than broken. |
| News spike or gap | an NFP or CPI day, or a Sunday gap | Check whether the pivot lands on a wick. Note severity for the wick-filter item. |

Record per tier per regime: pivot count, the smallest leg between
consecutive opposite pivots in multiples of ATR, and any visibly degenerate
level. Then decide the final `periods`, and whether `h4_internal` needs
`minAtrSeparation > 0` (trigger: smallest internal legs under about 0.5
ATR). If the smallest legs are comfortably above 1 ATR in every regime,
leave the filter at 0 permanently and record here that the hook is kept
only against a future regime change.

## Deliberately not done

- **No break-first gate on the swing and internal tiers.** Daily's internal
  tier only replaces its level once the old one has been broken. The
  fractal mechanism does not. Keeping all three 4H tiers ungated keeps n as
  the only thing that differs between them. The trade-off is that the 4H
  swing level can move to a new n=20 fractal without the old one ever
  breaking. Accepted, because it is the documented Williams behaviour.
- **No coupling between tiers.** No tier is clipped to another's range,
  seeded from another's pivots, or reset when another flips.
- **No change to Daily behaviour.** `fractal_detector.py` did gain two
  default-off arguments, so "unchanged" means byte-identical output, proven
  by diffing the detector output before and after the edit rather than by
  not touching the file.

## For the H1 port

**Implemented July 29 2026.** Steps 1 through 4 below are done: see
`swing_structure/h1_structure.py`, `fxrscripts/h1_fractal_structure.py`,
`fxrscripts/h1_internal_structure.py`, `fxrscripts/h1_swing_structure.py`,
and the three `h1_*` keys in `current_structure.py`. The last bullet, the
three-regime table, is explicitly NOT done by this port: n=2/8/20 were
carried over unchanged from 4H as a placeholder, and none of the three
have been checked against real H1 bars. Treat every H1 number as
unverified until that table is filled in.

Steps 1 through 4 of the 4H work should repeat near-mechanically:

- `swing_structure/h1_structure.py` mirroring `h4_structure.py`, reusing
  `compute_tier_structure` unchanged.
- Three FXR scripts with `MY_TIMEFRAME_MS = 3600000` and their own
  linewidths (5, 6, 7, keeping every script's tag distinct so the
  self-only drawing cleanup keeps working).
- Three more keys in `current_structure.py`'s `_STRUCTURE_COLUMNS`.
- Its own three-regime table. Do not assume 4H's numbers transfer: H1 has a
  different trend-to-noise ratio, which is the one thing the fractal
  mechanism is genuinely sensitive to.

### H1 three-regime table (to fill in)

Not yet run. Same method as the 4H and Daily tables: pick a real XAU/EU H1
chart for each regime and record pivot count, smallest leg between
consecutive opposite pivots in multiples of ATR, and any visibly
degenerate level, per tier.

| Regime | What to pick | What to expect |
|---|---|---|
| Tight consolidation | a multi-hour Asian-session range | `h1_fractal` marks minor pulls (fine). `h1_internal` and `h1_swing` should NOT mark levels a few pips apart. |
| Strong one-directional trend | a sustained multi-day leg | `h1_swing` holds its last confirmed pivot well below price with no new confirmation. Confirm this reads as acceptable rather than broken. |
| News spike or gap | an NFP or CPI hour, or a Sunday gap | Check whether the pivot lands on a wick. Note severity for the wick-filter item in `roadmap/market-structure.md`. |

## Daily port (July 29 2026)

Daily was ported to this same one-mechanism-three-scales approach today,
ahead of the order this document originally assumed: the "Still to do"
section above expected Daily's mechanism mix to be revisited only after
4H had been calibrated for a few months, with H1 planned as the next
timeframe port instead. That was reordered by explicit decision: Daily
went first, replacing its old lookback-plus-timeout swing tier and
ATR-zigzag internal tier outright rather than running old and new side by
side. `roadmap/market-structure.md`'s "Later Items" entry on this is
marked done as of this date.

The three Daily tiers now mirror 4H's own n values exactly:

| Tier | n | FXR linewidth | What it marks |
|---|---|---|---|
| `daily_fractal` | 2 | 1 | minor pulls |
| `daily_internal` | 8 | 8 | intermediate legs |
| `daily_swing` | 20 | 9 | major Daily legs |

`daily_fractal` (n=2) is unchanged, it already ran a Williams Fractal
before this port. `daily_swing`'s n=20 is, in fact, the ORIGINAL value:
the user found it by observing Daily charts on TradingView, and it was
used on 4H first (`h4_swing`) before finally landing on the timeframe it
was actually observed on. This is the one Daily number with real,
timeframe-specific backing.

**`daily_internal`'s n=8 has weaker backing than even 4H's own n=8.** On
4H, n=8 was already flagged as the one value not taken from an
observation, picked as roughly geometric between 4H's own n=2 and n=20.
Here it has been carried over a second time, onto a different timeframe,
with no Daily-specific check at all. Treat it as more provisional than
4H's n=8 until the regime table below is filled in.

The linewidth scheme also fixes a latent bug: `daily_internal_structure.py`
and `daily_fractal_structure.py` already shared linewidth 1 before this
port (both drew trendLines), so either script's timeframe-mismatch
cleanup could delete the other's lines. `daily_swing_structure.py` was
accidentally safe, since its old horizontal-ray visual was matched by
shape type instead. Switching `daily_swing` to a trendLine as part of this
port removed that accidental safety, so all three Daily scripts now carry
distinct linewidths (1, 8, 9), clear of 4H's 2/3/4 and H1's 5/6/7.

### Daily three-regime table (to fill in)

Not yet run. Same method as the 4H table above: pick a real XAU/EU Daily
chart for each regime and record pivot count, smallest leg between
consecutive opposite pivots in multiples of ATR, and any visibly
degenerate level, per tier.

| Regime | What to pick | What to expect |
|---|---|---|
| Tight consolidation | a multi-week Daily range | `daily_fractal` marks minor pulls (fine). `daily_internal` and `daily_swing` should NOT mark levels a few pips apart. |
| Strong one-directional trend | a sustained multi-month leg | `daily_swing` holds its last confirmed pivot well below price with no new confirmation. Confirm this reads as acceptable rather than broken. |
| News spike or gap | an NFP or CPI day, or a weekend gap | Check whether the pivot lands on a wick. Note severity for the wick-filter item in `roadmap/market-structure.md`. |

### Not done as part of this port

- The old mechanisms (`swing_structure/detector.py`,
  `swing_structure/pivot_detector.py`, `swing_structure/internal_structure.py`)
  were not deleted, only marked superseded. Deletion is filed as a Later
  Item in `roadmap/market-structure.md`, once nobody needs them for
  comparison.
- `pinescripts/daily_swing_structure.py`, the frozen TradingView version,
  was left untouched and is now further diverged from the Python source
  of truth than before. Pre-existing, acceptable divergence, not a new
  problem.
- This document's "Still to do: the three-regime table" section above,
  written for 4H, is unaffected and still open on its own terms.
