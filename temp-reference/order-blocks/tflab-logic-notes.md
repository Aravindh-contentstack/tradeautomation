# tflab.py: what we're keeping, what we're skipping

Notes distilled from `temp-reference/order-blocks/tflab.py`, the densest of
the five reference scripts. Purpose: capture the decisions made in that
discussion so a future session can update `swing_structure/order_blocks.py`
without re-deriving them. Companion to `fluxCharts-logic-notes.md` and
`sonarLab-logic-notes.md`, same purpose, different script. See
`roadmap/supply-and-demand.md`'s "OB lifecycle" and "Candle sizing and zone
shaping" sections for how these ideas actually landed in OUR rules.

## 1. Its own swing high/low, built from 1-candle breaks

Everything in this script builds up from `Candle_Break`, a single-candle
check (`close > high[1]` or `close < low[1]`), much smaller-scale than a
swing structure break. It layers a "highest high since the last opposite-
side break" calculation on top of that, plus a whipsaw-correction step
(`High_sec`/`Low_sec`), to arrive at its own version of a swing high/low.
NOT used by us, we already have Williams-Fractal-based tiers.

## 2. A swing level only moves once structure genuinely breaks

`High_Correct_f` only accepts a LOWER new high once a genuine bearish break
of structure (`BoS`) has happened. A higher high always replaces it
immediately, no gate needed. Conceptually identical to how our own
`swing_high` in `fractal_detector.py` behaves (a level only moves on a real
event), just implemented differently. Validates our own design, nothing to
port.

## 3. OB anchor: candle right before the level-setting candle

```
bar_index_Supply_OB = ta.valuewhen(High_Correct != High_Correct[1], bar_index[1], 0)
```
The candle immediately before whichever candle just set a new major
high/low. Superseded for us by the ATR-displacement method (see the
roadmap doc), not adopted.

## 4. The Order Block Refiner: REUSE the small/medium tiers, SKIP the large one

The part that actually mattered for us. Computes the OB candle's range
against `ATR(55)` and picks the zone edges differently by size:

- **Small** (range ≤ 0.5x ATR): near-full wick both edges.
- **Medium** (0.5x-1x ATR): trim the near edge (the side price touches
  first) to the candle's body, keep the far edge at the wick extreme. This
  is the user's own asymmetric trim rule, with a concrete ATR trigger
  instead of an undefined "large candle."
- **Large** (> 1x ATR): a bespoke chain of conditionals blending information
  from the OB candle's immediate neighbor (checking range overlap, the
  neighbor's body direction, and further ATR-relative comparisons on a small
  cluster of candles) to shrink the zone. Intent is clear (one oversized
  candle shouldn't dictate an oversized, low-quality zone), but the specific
  mechanism could not be confidently justified branch-by-branch from the
  code alone, reads as empirically tuned rather than derived from a clean
  rule. **NOT adopted.**

We adopted the 0.5x/1x ATR band boundaries, but replaced TFlab's tiering
entirely with the user's own rules once transcribed precisely: their "medium"
band uses the FULL wick (no trim at all, unlike TFlab's own medium tier which
does trim), their "high" band trims (matching TFlab's medium-tier trim
behavior, just at TFlab's "large" boundary instead), and their "low" band
(below 0.5x ATR) adds an entirely new idea TFlab doesn't have: merging the
candle with a neighbor to reach a usable size, rather than shrinking an
oversized one. See the roadmap doc's "Candle sizing and zone shaping" section
for the exact adopted rule, it is NOT a straight port of TFlab's tiers, the
band boundaries carried over, the behavior within each band did not.

There is also a **Defensive vs Aggressive** toggle that further shrinks the
zone when the raw computed range is large (up to 70% shrink at ≥3x ATR),
also not adopted, since the user's own rules already handle the
too-large/too-small cases directly.

## 5. Mitigation and invalidation: both wick-based, one-time, validates our rules

```
if low <= YDp12 and low[1] > YDp12 and LockAlertBull == 0
    LockAlertBull := 1  // fires exactly once per zone
```
A one-time flag (via the `LockAlertBull`/`LockAlertBear` lock), the first
time price's wick dips into the zone from the near side. Matches our own
`mitigated` design, unlike `sonarLab.py`'s repeating alert.

```
if (low < YDd12) and (low[1] > YDd12)
    DemCheck := false  // stop tracking, the zone is done
```
Stops tracking the zone once price's WICK (not close) clears past the
DISTAL (far) edge. A third independent script now agreeing wick-touch is
enough for a full break-through, alongside our own confirmed invalidation
rule 1 and `newphewSam.py`'s deletion logic.
