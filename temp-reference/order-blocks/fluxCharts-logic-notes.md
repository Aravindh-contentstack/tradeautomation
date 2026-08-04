# fluxCharts.py: what we're keeping, what we're skipping

Notes distilled from `temp-reference/order-blocks/fluxCharts.py`, after walking
through its logic line by line. Purpose: capture the decisions made in that
discussion so a future session can update `swing_structure/order_blocks.py`
without re-deriving them. Nothing here has been implemented yet.

## 1. Swing/pivot detection: NOT reused

`findOBSwings` (lines 224-240) is its own pivot detector, not the same
mechanism as our Williams Fractal. It only checks ONE side: whether the high
from `swingLength` candles ago has been exceeded by anything since. It never
checks whether that candle was also a high point relative to what came
before it. That makes it closer to the project's OLD, now-superseded
trailing-lookback swing detector (see `roadmap/market-structure.md`) than to
the two-sided Williams Fractal test we use now.

Decision: keep using our own tiered swing/internal/fractal break events
(`{tier}_high_event`/`{tier}_low_event`) to trigger OB formation, as
`swing_structure/order_blocks.py` already does. Do not port this pivot
method.

## 2. OB candle selection: REUSE (this is the important logic to port)

Once a break is confirmed, fluxCharts does NOT scan backward for the last
opposite-colored candle (that's the `newphewSam.py`/current-`order_blocks.py`
approach). Instead it scans the ENTIRE leg from the swing pivot to the
breakout candle and picks the SINGLE candle with the most extreme value in
that whole stretch:

- Bullish OB: the candle with the **lowest low** anywhere between the swing
  top pivot and the breakout candle. That candle's high/low become the box.
- Bearish OB: the candle with the **highest high** in that same span.

Candle color (red/green) is irrelevant here, unlike the current
`_find_anchor_candle` in `order_blocks.py`, which requires a color flip.

Decision: replace the color-scan anchor logic in `order_blocks.py` with this
extreme-candle-in-the-leg logic. The "leg" boundary to use for our port: from
the most recently confirmed pivot on the triggering tier (at the time of the
break) to the break candle itself, since we don't have fluxCharts' own
`findOBSwings` pivot to anchor from.

## 3. ATR-based size filter: DEFER, but keep the formula

```
obSize = abs(top - bottom)
keep only if obSize <= atr * maxATRMult
```
fluxCharts defaults: `atr = ta.atr(10)` (10-period ATR), `maxATRMult = 3.5`.
Filters out oversized zones formed by abnormal single-candle volatility
spikes (e.g. news events). Good candidate for a later stage, not part of
initial identification.

## 4. Breaker blocks: NOT USED in our strategy

fluxCharts models a two-stage state machine:
- **Becomes a breaker**: once price's low (or close, depending on setting)
  drops below the OB's bottom (a full break-through of the far side, not
  just a touch), the OB flips to "breaker" state.
- **Gets discarded**: once already a breaker, if price later closes back
  above the OB's top (fully reclaiming the zone), it's removed entirely.

We do not use breaker blocks. Our strategy only uses flip zones. Keeping
this section only for context, since flip zones appear to build on a related
"OB got invalidated" idea. Do not port the breaker state machine itself.

## 5. Flip zones: definition confirmed with the user

fluxCharts computes a flag (`bullishBreaked`/`bearishBreaked`) when a new
small swing point forms INSIDE an already-broken OB's range, which is the
same general idea as our flip zone. However, that flag is set and never
read again anywhere else in the script, so fluxCharts does not actually
construct a flip-zone OB from it. Treat this as inspiration for where to
look, not logic to copy.

**Confirmed definition** (2026-08-04): an existing OB gets mitigated, price
reacts with a small bounce (a mini swing point), then fully invalidates the
original OB by closing below it (for a bullish OB, above it for a bearish
one) and pushes on past it in the original breakout direction. That bounce
becomes the anchor of a **new, separate** OB, in the **opposite** direction
from the original:

- Failed bullish OB -> produces a **bearish** flip zone.
- Failed bearish OB -> produces a **bullish** flip zone.
- It is a genuinely new zone (the bounce candle), not the original OB
  relabeled.
- Invalidation trigger is a **close** below/above the original OB (a close
  breach, not merely a wick touching through it).

Still open before implementing: exactly how to detect "a small swing point"
for the bounce (our own tiered break events, or a smaller/local version of
the anchor-candle logic in section 2?), and how far past the original OB
price must close to count as "fully invalidated" (any close beyond it, or a
minimum distance/ATR-relative move?).

## 7. Volume: display only, not a filter

`obVolume`/`obLowVolume`/`obHighVolume` (the sum of the breakout candle's
volume plus the two candles before it, split into a high/low portion) are
used ONLY inside `renderOrderBlock` to draw the two-colored volume bar and
the percentage shown in the box's label. They never appear in
`findOrderBlocks()`'s actual decision logic: not in whether an OB is
created, not in the ATR size filter, not in mitigation/breaker checks, not
in the combine-overlap logic. Volume is informational chart decoration in
this script, not something that filters or validates OBs. No need to plan
for volume-based filtering when porting this logic, unless we deliberately
choose to add it ourselves later.

## 6. Combining overlapping zones: REUSE

When two OBs of the SAME direction have overlapping boxes (overlap
percentage above a threshold), merge them into a single wider box (union of
their extents) instead of keeping both. Purely a decluttering step, applied
after identification, not a detection-logic change. Relevant to us because
our current identification already produces overlapping/duplicate-looking
OBs when multiple tiers break off the same anchor candle at different times
(see the demo output discussed on 2026-08-04), so this merge step is a
natural fix for that once we're ready to apply it.
