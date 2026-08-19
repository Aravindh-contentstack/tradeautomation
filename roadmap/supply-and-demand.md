## Mitigation OB

Order blocks (OBs) are identified per timeframe (Daily, 4H, H1), not per structure
tier. This matters because the factor sheet has cross-timeframe containment
checks (`h1_..._present-in-4H-ob`, `4H_..._present-in-daily-ob`) that only make
sense if there is one OB series per timeframe. A break of any tier (swing,
internal, or fractal) at that timeframe can trigger an OB. Which tier caused it
becomes metadata on the OB, feeding the `swept_liquidity_structural-*`
sub-factors below.

### OB lifecycle: candle selection, mitigation, invalidation (confirmed 2026-08-04)

Worked out across several reference scripts in `temp-reference/order-blocks/`
plus direct discussion with the user about how they trade this manually. Full
walkthrough of the external reference scripts themselves is in
`temp-reference/order-blocks/fluxCharts-logic-notes.md`,
`temp-reference/order-blocks/sonarLab-logic-notes.md`, and
`temp-reference/order-blocks/tflab-logic-notes.md`. This section is
specifically the confirmed rules for OUR implementation.

**Candle selection: CONFIRMED, IMPLEMENTED 2026-08-05** (`swing_structure/
order_blocks.py`). An ATR-based leg scan, not a color scan and not the
single-most-extreme-candle idea:
- For the leg relevant to a given tier's break (the span from that tier's own
  pivot to its break candle), scan for the "displacement candle": the first
  candle whose range exceeds **1.0x ATR** (resolved, same multiple as the
  zone-shaping "high" tier below, one consistent meaning of "unusually
  large" everywhere). If no candle in the leg qualifies, the **break candle
  itself becomes the fallback displacement point**, so a break never fails
  to produce an OB for want of a standout candle.
- The OB anchor is the candle **immediately before** that displacement
  candle. Its color is irrelevant, unlike the old last-opposite-color
  approach.
- This single rule, applied at whatever scope a given tier's leg happens to
  span, correctly produces the right anchor for both a small fractal-tier
  pullback and a larger internal-tier leg without needing tier-specific
  branches, confirmed against a worked 8-candle example.
- This supersedes the three previously-open candidates (last-opposite-color,
  most-extreme-candle-in-leg, volume-based). Volume was blocked anyway (no
  volume column in `data/raw/*.parquet`), this ATR/displacement approach is
  effectively the computable substitute for that blocked idea, using price
  velocity instead of trade size as the signal for "real institutional
  footprint."

**`caused_displacement` (confirmed, IMPLEMENTED 2026-08-05).**
A separate, subsequent boolean check on an already-identified OB, not part of
finding the anchor itself. Strict version for now (relax later if needed):
the candle immediately after the anchor must have its body **close** past
the anchor candle's **high** (for a bullish/demand OB) or **low** (for a
bearish/supply OB), not merely past its close. If the next candle only wicks
past that high/low without closing beyond it, `caused_displacement = False`
for that OB, the OB itself is still valid, just flagged as not having
displacement. An OB can win the ATR-based anchor scan without its next
candle satisfying this, the two checks are independent.

**Mitigation (confirmed, simple).** The first candle, of the SAME timeframe
as the OB (an H1 OB is only ever mitigated by an H1 candle, never by looking
at 4H/Daily/M15 data), whose wick overlaps ANY part of the OB zone
`[bottom, top]`. No need to reach the EQ (midpoint), any overlap at all
counts. This is a one-time lifetime flag: once set, it never gets
re-evaluated, and further re-touches of the same zone don't create new
mitigation events. This matches what `order_blocks.py`'s `_apply_mitigation`
already does, no change needed there. The same-timeframe constraint is
already true by construction, since `compute_order_blocks` only ever reads
one timeframe's OHLC rows at a time.

**Invalidation (CONFIRMED and IMPLEMENTED 2026-08-13, `smc/order_blocks/
order_blocks.py`'s `_apply_touch_lifecycle`).** An OB can be "mitigated" and
remain tradeable, it only stops being tradeable once invalidated. The whole
model rests on one idea: an OB is a block of resting orders, and it can only
produce a reaction while enough of them are left. Every rule below is a way
of saying "they are used up now".

Three rules, whichever fires first:

1. **EQ reached.** A wick touches the OB's own midpoint, `(top + bottom) /
   2`. No close required. Reaching the EQ means the majority of the resting
   orders have been absorbed in one go.
   - This SUBSUMES the previously-listed "full break-through" rule, which is
     no longer a separate case: a wick that pushes past the far edge has
     necessarily crossed the midpoint on the way, so the EQ rule always
     fires first. Confirmed with the user 2026-08-13.
2. **Third qualifying touch.** Touch N+1 only qualifies if it penetrates
   DEEPER than touch N (a lower low for a demand OB, a higher high for a
   supply OB). A shallower re-tap reaches no orders the earlier one did not
   already absorb, so it does not advance the count. After three qualifying
   touches the block is treated as spent: with few orders left there is no
   reason to expect a strong reaction, and price can pass through at any
   time. A multi-bar stay inside the zone counts as ONE touch.
   - This replaces the old rules 3a/3b, which are now confirmed to be one
     shared countdown rather than two mechanisms, with N = 3.
3. **Structure break away from a touched zone.** The OB was touched at least
   once and price then broke structure (any of that timeframe's three tiers)
   in the direction AWAY from the zone. Direction matters: a down-break is
   price coming into a demand zone, which is the setup rather than its
   failure.
   - Still only applies if the OB was actually touched first. If price never
     touches an OB and instead breaks structure some other way (for example
     by sweeping a different liquidity pool elsewhere in the range, a
     concept for `roadmap/liquidity.md`), the untouched OB is NOT
     invalidated. It stays live and could be mitigated at any arbitrary
     point in the future. See "Lookback period" below for why this needs a
     bound.
   - Implemented as the weaker TEMPORAL version of the original rule: the
     break must come after a touch, but there is no test that the bounce
     CAUSED the break, since causation is not cleanly testable.

**The zone is still tradeable ON the candle that kills it (confirmed
2026-08-13).** Both the EQ rule and the third-touch rule fire on a candle
that also produces a valid signal, and the OB only dies from the NEXT candle
onward. This is not a rounding decision, it is the point: reaching the EQ is
exactly the evidence that the orders were absorbed and a reaction is now
due, so that candle is the highest-conviction entry the zone will ever
offer. The code keeps the two apart as `invalidated_index` (the candle the
event happened on) and `invalidated_from_index` (the first dead candle), and
`ob_state.py`'s `valid_through` carries the rule so no caller re-derives it.

**Lookback period (CONFIRMED and IMPLEMENTED 2026-08-19).** Because rule 2
explicitly does NOT invalidate an untouched OB just because the market moved
on, untouched OBs would otherwise pile up forever and all remain "technically
still valid." A fourth invalidation rule, `"expired"`, bounds that:

- **100 candles**, as `order_blocks.OB_LOOKBACK`. The same number
  `smc/liquidity/fair_value_gaps.py` already uses, and the same one the
  liquidity levels in `roadmap/liquidity.md` use, so zones, gaps and levels all
  age on one rule. The same 100 on every timeframe, which is deliberately not
  the same amount of calendar time (about five months on Daily, about four days
  on H1).
- **Counted from `earliest_trigger_index`**, the candle the zone becomes known,
  NOT from the anchor candle it is drawn on. The anchor sits in the past
  relative to the break that reveals it, sometimes by a whole leg, so dating
  the clock from formation would hand a zone confirmed 120 candles late a
  lifetime that had already run out, making it untradeable the moment it became
  visible.
- **Applied through the same `_kill`** as the other three rules, so the zone is
  live for exactly 100 candles after its trigger and dead from the next one,
  and `ob_state.py`'s `valid_through` needs no special case.
- **Data running out early is not expiry.** When fewer than 100 candles exist
  after the trigger, the OB stays alive rather than being clamped dead, since
  "not knowable yet" and "dead" are different facts and only the first is true.
  Without this the live bot would lose its newest zones on every run.

`_apply_mitigation` carries the identical bound, so the table never records a
mitigation on a candle the zone was already dead for.

**Measured impact**, entry candidates over 2020 to 2025, before against
after, on flat 1.0 weights:

| Pair | Before | After | Change |
|---|---|---|---|
| EUR_USD | 886 | 791 | -10.7% |
| AUD_USD | 881 | 761 | -13.6% |
| EUR_JPY | 807 | 700 | -13.3% |
| GBP_JPY | 889 | 784 | -11.8% |
| GBP_USD | 953 | 836 | -12.3% |
| NZD_USD | 911 | 799 | -12.3% |
| USD_CAD | 960 | 854 | -11.0% |
| USD_CHF | 834 | 737 | -11.6% |
| USD_JPY | 769 | 683 | -11.2% |
| XAU_USD | 866 | 784 | -9.5% |
| **All** | **8756** | **7739** | **-11.6%** |

Consistent across every pair and every year, which is what a bound on age
should look like: it removes a steady tail of stale zones rather than
reshaping the strategy. This is the baseline every later comparison is
measured against, since it is the only change in the liquidity work that
moves candidate counts at all.

**`caused_imbalance` (confirmed and IMPLEMENTED 2026-08-05).** A plain 3-candle FVG check
anchored directly on the OB, no distance search like `newphewSam.py`'s
`fvgDistance` window. For anchor candle `i`, check for a gap between `i` and
`i+2` (skipping `i+1`): for a bullish/demand OB, `caused_imbalance = True` if
row `i+2`'s low sits above row `i`'s high (a genuine gap, nothing traded in
between). For a bearish/supply OB, the mirror: row `i+2`'s high sits below
row `i`'s low. Deliberately NOT requiring `i+1`'s close to also confirm the
gap (unlike `newphewSam.py`'s `bullishImb`/`bearishImb`, which do), the plain
gap is enough. Same 3-candle window (`i`, `i+1`, `i+2`) as
`caused_displacement` above, the two checks just test different things:
displacement asks whether `i+1` alone closed past the anchor, imbalance asks
whether the gap reaches all the way out to `i+2`.

### Candle sizing and zone shaping (confirmed and IMPLEMENTED 2026-08-05)

Once the ATR-displacement method (above) has picked the anchor candle, a
separate step decides the anchor's actual `top`/`bottom` values, since the
raw anchor candle's own wick-to-wick range isn't always the right zone.
Worked out against `tflab.py`'s "Order Block Refine" feature (see
`temp-reference/order-blocks/tflab-logic-notes.md`), but replacing its
bespoke large-candle neighbor-blending logic with the user's own manual
rules. Three ATR-relative bands, using the anchor candle's own range
(`high - low`) against the SAME ATR series computed for candle selection,
but a DIFFERENT threshold comparison, a separate step from finding the
anchor, not part of it:

- **Low** (anchor's range < 0.5x ATR, "too small to use as-is"): don't use
  the candle alone. Try combining it with a neighbor, checked in this exact
  order, stop at the first success:
  1. `[i, i+1]` (forward: the anchor plus the next candle). If this combined
     range (highest high to lowest low across both candles) lands in
     **medium**, use it, full wick to wick, done. Never check backward or
     the 3-candle combo.
  2. Otherwise, `[i-1, i]` (backward: the anchor plus the previous candle).
     If medium, use it, full wick to wick, done. Never check the 3-candle
     combo.
  3. If NEITHER 2-candle attempt was medium: if BOTH landed in **high**,
     skip the 3-candle combo entirely (adding a third candle can only keep
     the range the same size or grow it, never shrink it back down, so
     trying it when already-too-big can't help) and just take the forward
     `[i, i+1]` result. Otherwise (at least one of the two was still low),
     try `[i-1, i, i+1]` and accept whatever band it lands in, medium, high,
     or even still low, no further fallback, and never extend past 3
     candles.
  - Forward is preferred over backward for two reasons: (1) `caused_imbalance`
    checks the gap between `i` and `i+2`, extending forward to include `i+1`
    pushes the zone's far edge closer to that gap instead of leaving it
    sitting apart from the marked zone. (2) A small, un-extended or
    backward-only zone often just gets its gap filled and price pushes away
    without ever coming back to trade it, extending forward produces a
    bigger, more realistically tradeable zone.
  - Whenever a merged/combined range is used (whether it lands in medium or
    high), take the full wick-to-wick range, never trim it. Trimming (below)
    only ever applies to a single, un-merged candle.
- **Medium** (0.5x to 1x ATR): use the candle's full range, wick to wick, no
  trim.
- **High** (> 1x ATR): trim. Keep the far/important wick, strip only the
  near/unimportant wick down to the candle's body: for a supply (bearish) OB,
  keep the full high-side wick, trim the low side to the body. For a demand
  (bullish) OB, keep the full low-side wick, trim the high side to the body.

This is separate from (but likely shares its 0.5x/1x ATR band boundaries
with) invalidation rule 3's still-open "large OB" criterion above, that one
is about whether the OVERALL zone is big enough for the multi-touch
attrition rule to apply, not about shaping a single anchor candle. Decide
whether they actually share one threshold or need their own when rule 3 gets
built.

**Cluster avoidance / merging nearby OBs (confirmed 2026-08-04).** Two
different situations, handled differently, do NOT treat them the same:
- **Same anchor candle, multiple tiers** (e.g. a long same-colored run means
  the fractal, internal, and swing tiers all trace back to the identical
  anchor candle): merge into ONE OB row, and classify/name it by the
  **largest** tier involved (e.g. call it an Internal OB even though it also
  technically holds the Fractal structure). Safe to merge, since it's
  literally the same candle.
- **Different anchor candles whose zones happen to overlap in price, even in
  the same direction**: do NOT merge, ever. A smaller, nearer-tier OB sitting
  in front of a larger, farther-tier OB (price reaches the smaller one
  first) can be a genuine inducement trap: early entries at the fractal-tier
  OB get invalidated, price pushes through to the internal-tier OB behind
  it, mitigates that instead, then reverses. Merging the two zones would
  destroy the ability to ever detect that pattern, which is exactly what
  `ob_has_inducement` (Stage 4 below) needs to see. This walks back
  `fluxCharts.py`'s overlap-merge idea (see its logic notes file section 6),
  we are NOT adopting it.

Stages, in build order:

1. **OB identification. IMPLEMENTED 2026-08-05.** Detect OB anchor candles
   off existing tier break events (`{tf}_{swing,internal,fractal}_high_event`
   /`low_event` == "break of swing high/low"), apply the ATR-based candle-
   selection rule to find the anchor, then the "Candle sizing and zone
   shaping" rules to turn that anchor into the actual `top`/`bottom`.
   Classify direction, record formation index/date, zone top/bottom,
   triggering tier(s), merging same-anchor multi-tier triggers per the
   cluster-avoidance rule above (`primary_tier`, `trigger_tier`). See
   `swing_structure/order_blocks.py`.
2. **Mitigation and invalidation state.** Implement the confirmed rules
   above: `mitigated`/`mitigated_index`/`mitigated_date` (simple, already
   built), plus a new `invalidated`/`invalidated_index`/`invalidated_date`
   (or similar) capturing whichever of the three invalidation rules fired.
3. **Swept Liquidity sub-factors** (future): `structural-swing`,
   `structural-internal`, `structural-fractal`, `structural-old_points`, `fvg`,
   `equals`, `previousCandle`. What kind of liquidity the price swept on its
   way to forming/mitigating the OB. Shared logic with `roadmap/liquidity.md`'s
   Liquidity Sweep section. Build once, reuse for both Mitigation OB and OB
   Target gates.
4. **`has-inducements`** (future, concept confirmed). A nearer, smaller-tier
   OB in the same direction as a farther, larger-tier OB can act as bait:
   traders enter at the near one, it fails, price pushes through to mitigate
   the farther one instead. This is exactly why the two are never merged
   (see cluster avoidance above), the detection depends on keeping them as
   separate rows.
5. **`caused-imbalance` / `caused-displacement`. IMPLEMENTED 2026-08-05.**
   Both rules confirmed and implemented, see "OB lifecycle" above.
6. **`flipzone`** (future). Definition already confirmed, see
   `temp-reference/order-blocks/fluxCharts-logic-notes.md` section 5: a
   failed OB (invalidated per the rules above) produces a NEW, separate OB in
   the OPPOSITE direction, anchored on the small reaction bounce that formed
   right before the original OB's invalidation.
7. **Cross-timeframe containment** (future): `present-in-{htf}-ob` (H1-in-4H,
   4H-in-Daily). Needs identification done for both timeframes first.

## Target OB

Reuses the same OB objects as Mitigation OB, just selected as the
opposite-direction draw target instead of the reaction zone. Same
sub-factors as Mitigation OB above (Swept Liquidity, Has Inducements,
Caused Imbalance, Caused Displacement, Flip Zone, cross-timeframe
containment).

**Selection (CONFIRMED and IMPLEMENTED 2026-08-13, `backtest/factors.py`'s
`evaluate_ob_target_factors`).** One target per timeframe, matching the
factor sheet's three separate `OB Target` gates: the NEAREST valid,
unmitigated, opposite-direction OB on Daily, on 4H, and on H1, each scored
independently. Nearest-first, so when the near one is invalidated the next
one out takes over and the same process repeats. This turned out NOT to
depend on the Entry models, contrary to the earlier note here, so it is no
longer deferred to `roadmap/entry-models.md`.

**Supporting versus opposing (confirmed 2026-08-13).** A target's qualities
mean opposite things depending on whether price has arrived yet, and this is
the core of what distinguishes the two OB roles:

- **Unmitigated (supporting).** The zone is pulling price toward it, which
  is the direction the trade wants to go. Every quality it has scores YES.
- **Reached (opposing).** That same strength is now what stops price going
  further, so every answer is NEGATED. A target with displacement scored YES
  while unreached and scores NO once reached. The underlying facts about the
  zone never change, only what they mean for this trade.
- The negation is symmetric, so a WEAK opposing zone reads as a positive: a
  target with no displacement, once reached, is unlikely to hold price and
  scores YES.
- Mitigation OB polarity is the simple case by contrast: every quality
  present scores YES, absent scores NO, and those answers FREEZE at entry
  for the life of the trade.

**Distance filter (CONFIRMED 2026-08-13, closing the TBD noted
2026-08-04).** An OB can be completely valid per the lifecycle rules while
still being a bad target if it sits too far away for price to realistically
reach it. The rule as the user states it is "within 2x the take-profit
distance", and TP is `tp_multiple x R`, so the radius is **5R**, using the
spec's 2.5R baseline. Beyond it the whole gate is excluded rather than
scored.

The multiple is PINNED at 5R rather than read from the live `tp_multiple`,
deliberately. `tp_multiple` is what the prior year's walk-forward search
recommended, so feeding it into the probability would make each year's
scores a function of last year's tuning, which is the ratchet
`backtest/settings.py` exists to prevent. It would also break
`analysis.py`'s post-hoc TP grid search, which is only free because the walk
carries no TP dependence at all. See `backtest/entry_ob.py`'s
`TARGET_SEARCH_R`.

**Mid-trade tracking is record-only for now (confirmed 2026-08-13).** The
supporting-to-opposing flip is recomputed on every bar of an open trade and
written to `data/target_log/`, but it changes nothing about entries, exits,
or sizing. Acting on a decaying score (breakeven, partials, or cancelling)
is a separate design, recorded in `roadmap/enhancements.md`.

## The entry trigger (CONFIRMED and IMPLEMENTED 2026-08-13)

Order blocks now DRIVE entries rather than merely scoring them. A trade
candidate used to begin with an H1 fractal break, which fixed the direction
before any zone was involved. It now begins with price taking a qualifying
touch of a valid H1 order block, and the direction IS that zone's direction.
Nothing is assumed about which way the market is going until it touches
something.

- The direction can legitimately disagree with the surrounding structure.
  Price mitigating a bearish H1 internal OB is a sell setup even while the
  H1 swing structure is bullish, and that is the point rather than an edge
  case.
- Consequently the three old mandatory gates (`h1_internal_structure`,
  `h1_internal_zone`, `h1_fractal_structure`) are now SCORED FACTORS, not
  rejections. As hard gates they would have rejected exactly the setups
  described above.
- **Which OB:** the most recently mitigated valid H1 OB, any tier. When two
  zones are touched on the same candle the freshest wins, since the two can
  imply opposite trades and an arbitrary tie-break would silently pick a
  direction.
- **Only qualifying touches trigger**, meaning progressively deeper ones,
  the same test that advances the invalidation counter. A shallower re-tap
  reaches nothing new.
- **Killzone gate:** the touch must land inside a killzone or in the hour
  immediately before one (06:00 to 10:00 and 11:00 to 15:00 London). Price
  often taps the zone before the session and simply travels during it, so
  requiring the touch itself to be in-session would miss the setup. A
  pre-window touch defers its entry to the session's first candle.
- **Entry** is the close of the mitigating candle, or of the deferred
  killzone candle. A placeholder until the M15 entry models land.
- **Stop** is 2 pips beyond the zone's FAR edge (below `bottom` for a demand
  OB, above `top` for a supply OB), replacing the old opposite-fractal stop.
  The zone is the thesis now, so the stop belongs to it.
- Higher timeframes are NOT required to have been mitigated. Only H1 is
  mandatory. A Daily or 4H gate with no valid zone is excluded from scoring
  rather than answered "no".

See `backtest/entry_ob.py` and `backtest/simulate.py`'s `find_signals`.

### Next Items

Candle selection, zone shaping, same-anchor merging, `caused_displacement`,
and `caused_imbalance` were implemented 2026-08-05. The full invalidation
lifecycle, the OB-mitigation trigger, both OB gates, and the supporting/
opposing flip were implemented 2026-08-13. Note the module paths in the
older sections above predate the `smc/` rename: order blocks now live in
`smc/order_blocks/` and structure in `smc/market_structure/`.

What's left:

- Validate identified OBs against a real chart (TradingView/FX Replay).
  Still not done. `scripts/demo_order_blocks.py` only smoke-tests that the
  logic runs sanely.
- Run the full walk-forward on the new trigger and compare against the
  archived pre-change results. First single-year check on EUR_USD 2024 gives
  144 candidates against roughly 76 under the old trigger.
- `Old Points`, `Equals` and `LRLQ` swept-liquidity sub-factors now have
  detectors (`smc/liquidity/`), and H1 additionally carries the five
  time-based kinds. See `roadmap/liquidity.md`.
- Decide whether to bootstrap initial weights from `factors/*.csv` instead
  of the current flat 1.0. Those numbers were tuned against different factor
  definitions on the old trigger, so seeding them now would make year-one
  results un-attributable. Worth an A/B once a clean baseline exists.
