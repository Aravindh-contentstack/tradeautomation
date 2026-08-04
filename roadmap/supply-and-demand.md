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
`temp-reference/order-blocks/fluxCharts-logic-notes.md` and
`temp-reference/order-blocks/sonarLab-logic-notes.md`. This section is
specifically the confirmed rules for OUR implementation.

**Candle selection: CONFIRMED.** An ATR-based leg scan, not a color scan and
not the single-most-extreme-candle idea:
- For the leg relevant to a given tier's break (the span from that tier's own
  pivot to its break candle), scan for the "displacement candle": the first
  candle whose range is unusually large relative to ATR (exact multiple TBD,
  same size measure used for the large-candle/large-OB ideas below).
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

**`caused_displacement` (confirmed, and decoupled from candle selection).**
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

**Invalidation (confirmed, separate concept from mitigation).** An OB can
still be "mitigated" and remain tradeable, it only stops being tradeable once
invalidated. Three distinct ways this happens:

1. **Full break-through.** Price's wick (no close required) pushes all the
   way past the OB's far side (below the bottom for a demand/bullish OB,
   above the top for a supply/bearish OB).
2. **Superseded by a fresh structure born from mitigating THIS OB.** Price
   wicks the OB (mitigates it, even shallowly), bounces, and that bounce is
   strong enough to cause a genuinely NEW structural break (a fresh
   fractal/internal/swing pivot break) on the way away from the OB. That new
   break both forms a brand new OB from the reaction candle AND invalidates
   this old one. Important: this ONLY applies if the OB was actually touched
   first. If price never touches an OB and instead breaks structure some
   other way (e.g. by sweeping a different liquidity pool sitting elsewhere
   in the range, a concept for `roadmap/liquidity.md`), the untouched OB is
   NOT invalidated. It stays live and could be mitigated at any arbitrary
   point in the future. See "Lookback period" below for why this needs a
   bound.
3. **Death by attrition, large OBs only (Daily-scale, primarily).** For
   zones big enough that price can chop around inside them for a while
   without cleanly triggering rule 1 or rule 2. Two flavors described by the
   user, possibly (UNCONFIRMED) two faces of one shared mechanism: track the
   deepest touch reached so far, and the OB dies the moment EITHER (a) a
   touch finally crosses past the EQ, OR (b) the touch count hits some limit
   N without ever crossing the EQ.
   - **3a**: touches get progressively deeper each time (first touch stays
     above the EQ, reacts, second touch gets closer to the EQ without
     crossing it, reacts again, a later touch finally crosses past the EQ).
     That crossing is what makes it "completely mitigated" and dead. A wick
     touch to the EQ is enough, no close required.
   - **3b**: price touches the same OB repeatedly without ever getting near
     the EQ. After enough repeats (count TBD), also dead.
   - Open, TBD later: whether 3a/3b really are one shared countdown
     mechanism or two independent rules, the exact touch count N for 3b, and
     whether this rule matters for 4H/H1 at all or whether rules 1/2 always
     resolve things first there in practice (also unconfirmed).

**Lookback period (new concept, TBD, not yet defined).** Because rule 2
explicitly does NOT invalidate an untouched OB just because the market moved
on, untouched OBs could in principle pile up forever and all remain
"technically still valid." A lookback period is needed: some bound on how far
back an unmitigated OB can be and still be considered live, versus being
treated as stale/obsolete purely due to age. Value and exact definition
deferred to a later session.

**Large candle / large OB sizing (two related ideas, both TBD, likely share
one size measure, same measure as the ATR-based candle selection above).**
- When forming a zone from an anchor candle that's unusually large, trim the
  wick on the side that ISN'T the important edge, down to the candle's body:
  for a supply (bearish) OB, keep the full high-side wick, trim the low
  side to the body. For a demand (bullish) OB, keep the full low-side wick,
  trim the high side to the body. Only applies when the candle counts as
  "large."
- Rule 3's "large OB" criterion (which zones are big enough for the
  attrition rule to apply) needs its own threshold too.
- Decide the exact ATR multiple(s) together once we get here, but all of
  these (candle selection, displacement, wick trim, large-OB attrition)
  should share one consistent ATR-relative size measure rather than several
  inconsistent ones.

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

1. **OB identification.** Detect OB anchor candles off existing tier break
   events (`{tf}_{swing,internal,fractal}_high_event`/`low_event` == "break of
   swing high/low"), then apply the confirmed ATR-based candle-selection rule
   above (not the color-scan currently in `order_blocks.py`, that needs
   updating). Classify direction, record formation index/date, zone
   top/bottom, triggering tier(s), merging same-anchor multi-tier triggers
   per the cluster-avoidance rule above.
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
5. **`caused-imbalance` / `caused-displacement`.** `caused_displacement`'s
   exact rule is now confirmed, see "OB lifecycle" above. `caused-imbalance`
   (FVG) still open, likely a similar-shaped check, worked out when we get
   here.
6. **`flipzone`** (future). Definition already confirmed, see
   `temp-reference/order-blocks/fluxCharts-logic-notes.md` section 5: a
   failed OB (invalidated per the rules above) produces a NEW, separate OB in
   the OPPOSITE direction, anchored on the small reaction bounce that formed
   right before the original OB's invalidation.
7. **Cross-timeframe containment** (future): `present-in-{htf}-ob` (H1-in-4H,
   4H-in-Daily). Needs identification done for both timeframes first.

## Target OB

Reuses the same OB objects as Mitigation OB, just selected as the
opposite-direction draw target instead of the reaction zone. Same stages as
Mitigation OB above (Swept Liquidity, Has Inducements, Caused Imbalance,
Caused Displacement, Flip Zone, cross-timeframe containment), applied with
Target OB's directional selection logic. That selection logic (which specific
unmitigated OB becomes "the" target for a given signal) is an Entry-model
concern (`LC-1`/`LC-2A`/`LC-2B`/`CE` in `roadmap/entry-models.md`), not an
identification concern, so it's deferred until the Entry-model factors are
designed.

**Distance/recency filter (new, TBD, noted 2026-08-04).** An OB can be
completely valid as an OB (per the lifecycle rules above) while still being a
bad Target OB candidate, if it sits too far away (e.g. the anchor from deep
inside a long, uninterrupted 40-candle leg) for price to realistically be
expected to travel back to it after a reversal. Inspired by
`sonarLab.py`'s cooldown mechanism (see its logic notes file), repurposed
here as a target-selection filter rather than an OB-creation-frequency
control, which is what it does in that script. Exact measure (bars, ATR-
relative price distance, or relative to the size of the leg/range) TBD,
decide when we design Target OB selection properly.

### Next Items

- Update `order_blocks.py`'s candle selection from the color-scan to the
  confirmed ATR-based displacement-candle method.
- Implement the same-anchor-multi-tier merge (with largest-tier naming), and
  make sure different-anchor overlapping OBs are explicitly NOT merged.
- Add `invalidated`/`invalidated_index`/`invalidated_date` alongside the
  existing `mitigated` fields, covering rule 1 (full break-through) first,
  rules 2/3 once their open TBDs (touch count N, large-OB threshold, 3a/3b
  unification) are settled.
- Implement `caused_displacement` per the confirmed rule above.
- Validate identified OBs against a real chart (TradingView/FX Replay) before
  moving on to the Swept Liquidity sub-factors.
