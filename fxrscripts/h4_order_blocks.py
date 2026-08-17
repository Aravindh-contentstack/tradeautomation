// FXR Script port of Order Block (OB) + Fair Value Gap (FVG) identification
// and 9 of the 11 OB quality/confluence factors, for the 4H timeframe.
//
// Python ground truth (fix there first, then re-port, per every sibling
// script's own convention):
//   swing_structure/atr.py               Wilder's ATR
//   swing_structure/fractal_detector.py  Williams Fractal (per-tier pivots)
//   swing_structure/market_structure.py  bullish/bearish structure
//   swing_structure/order_blocks.py      OB identification, zone shaping,
//                                        caused_displacement/imbalance,
//                                        mitigation, invalidation
//   swing_structure/fair_value_gaps.py   FVG identification + lifecycle
//   swing_structure/order_block_quality.py  the 6 compute_* factor functions
//
// Sibling FXR scripts this borrows its tier engine and timeframe/mtf idioms
// from (read in full before touching this file): fxrscripts/h4_swing_
// structure.py, h4_internal_structure.py, h4_fractal_structure.py (native
// 4H tiers), fxrscripts/daily_swing_structure.py, daily_internal_
// structure.py, daily_fractal_structure.py (the Daily tiers this script
// re-derives via mtf.* for the within_daily_ob containment factor), and
// fxrscripts/mtf_structure_probe.py + rectangle_lifecycle_probe.py, whose
// open questions this script inherits (see "UNVERIFIED" comments below).
//
// ---------------------------------------------------------------------
// BIG DEVIATION FROM "COPY THE TIER ENGINE VERBATIM", AND WHY
// ---------------------------------------------------------------------
// The nine structure scripts track each tier's swing_high/swing_low purely
// via TIME (swingHighTime = the pivot candle's own time(periods)), because
// all they ever need is "was this level crossed" and "where do I draw the
// line from". Order block identification needs strictly more: an actual
// INTEGER BAR INDEX for leg_start_index, anchor (formed_index), zone_end,
// and earliest_trigger_index, exactly as swing_structure/order_blocks.py
// defines them, so the ATR-displacement leg scan and the zone-shaping
// merge can walk a genuine index range. Reverse-engineering an index from
// a remembered timestamp (dividing by MY_TIMEFRAME_MS) is NOT reliable,
// because real calendar gaps (weekends) make timestamp deltas larger than
// the true bar count, per fxrscripts/README.md's own timeframe-inference
// note ("gaps only ever make a delta LARGER, never smaller").
//
// So instead of literally pasting the sibling scripts' time-based state,
// this script maintains its own growing OHLC arrays (opens/highs/lows/
// closes/times), one value pushed per newly closed candle, with a plain
// array position acting as the same kind of integer row index
// order_blocks.py's DataFrame rows use. The Williams Fractal DECISION
// LOGIC itself (strict near-frontier, tie-tolerant far-frontier, n
// periods, tie tolerance 4) is unchanged from the sibling scripts, just
// reindexed against array positions instead of time-offset accessor
// calls. This is the single biggest structural difference from "copy
// verbatim", and it is deliberate: mirroring the Python's own index-based
// design turned out to be more faithful than reproducing the sibling
// scripts' time-based bookkeeping and then trying to convert back.
//
// One consequence: the per-tier ATR-separation significance filter and
// the manual-restart input the nine structure scripts expose are DROPPED
// here (both default OFF/inert in every sibling script anyway). Keeping
// six more inputs and six more branches across three native tiers, three
// parent tiers, OB merge, FVG, and nine quality factors was judged not
// worth the added surface area for a feature that ships disabled
// everywhere else in this codebase. Flagged here as a scope reduction,
// not an oversight.
//
// ---------------------------------------------------------------------
// SECOND DEVIATION: within_daily_ob's mtf usage is narrower than the
// timeframe instructions literally describe, on purpose
// ---------------------------------------------------------------------
// fxrscripts/mtf_structure_probe.py raised three open questions about
// mtf.*, none yet answered ("Findings: not yet recorded"): Q1 (does
// mtf.high(k, false) resolve correctly at large k, up to ~44 candles
// back), Q2 (does mtf.time(0, false) advance exactly once per real Daily
// close), Q3 (do the resulting prices match a native Daily chart's own).
//
// This script only ever calls mtf.*(0, false) — index 0, never any larger
// k — because, exactly like the native tiers above, it keeps its OWN
// growing Daily OHLC arrays (pushed once per detected Daily close) and
// reads bar history from THOSE arrays, never from mtf's own lookback.
// That sidesteps Q1 and Q3 entirely: this script never asks mtf for a
// price more than one candle old. Q2 remains live and UNVERIFIED: the new-
// Daily-bar gate below (mtf.time(0, false) changing) is exactly the
// mechanism mtf_structure_probe.py exists to test. If mtf.time(0, false)
// skips or repeats a Daily close on a real chart, this script's Daily
// tiers (and therefore within_daily_ob) will desync from a genuine Daily
// chart without any local symptom — there is nothing in this script that
// can detect that failure from the 4H side alone. Treat within_daily_ob
// as unverified until mtf_structure_probe.py's Q2 is answered.
//
// ---------------------------------------------------------------------
// THIRD DEVIATION: caused_displacement / caused_imbalance are DEFERRED
// ---------------------------------------------------------------------
// order_blocks.py computes these once, offline, because its zone_end+1/
// zone_end+2 candles already exist in the full DataFrame. A live script
// cannot know the candle after an OB's own zone until that candle
// actually closes. Both flags are therefore evaluated lazily, exactly
// once each, on the first tick where zoneEndBarIndex+1 / zoneEndBarIndex+2
// is knowable, using the identical close-vs-high/low and low/high-vs-
// high/low tests order_blocks.py uses, read off the FIXED candle at that
// position (not off "the current bar") — see evaluateDeferredFlags's own
// comment for a bug this caught: since an OB's anchor sits at the far end
// of a leg-scan that ends at the break bar, zoneEndBarIndex+1/+2 are
// usually ALREADY in the past by the tick the OB is created, so this
// fires immediately at creation in the common case, not on some later
// tick. Nothing about the RULE changed, only WHEN/WHERE it reads from.
//
// ---------------------------------------------------------------------
// UNVERIFIED drawing mechanics (flagged individually below too)
// ---------------------------------------------------------------------
// - rectangle() + deleteDrawingByCondition matched on backgroundColor is
//   exactly rectangle_lifecycle_probe.py's untested design (that probe's
//   own findings are "not yet recorded"). Used here as the best-effort
//   translation of the CONFIRMED-working trendLine/horizontal_ray
//   pattern, per the task's own instruction to do so and flag it.
// - Attaching a text label via rectangle()'s putative 5th/6th positional
//   argument is modelled on rectangle_lifecycle_probe.py's own untested
//   usage (`rectangle(x1,y1,x2,y2,overrides,text)`); the docs' only
//   confirmed rectangle example (fx-replay-docs/multi-timeframe,
//   fx-replay-docs/indicator-structure/on-tick.md) never passes a text
//   argument at all.
// - ta.atr's exact seeding/smoothing (fx-replay-docs/external-libraries/
//   ta-math-library.md documents only the signature, not the algorithm)
//   is assumed to match swing_structure/atr.py's Wilder-with-simple-mean-
//   seed convention closely enough for the medium/high/low zone bands and
//   the 1.0x displacement threshold to land the same way. Not verified
//   against the Python output on shared fixtures.

const FRACTAL_TIE_TOLERANCE = 4;
const DISPLACEMENT_ATR_MULTIPLE = 1.0;
const ATR_PERIOD_DEFAULT = 14;
const FVG_LOOKBACK_DEFAULT = 100;

// Daily (parent) tier periods, hardcoded rather than exposed as inputs:
// matches daily_swing_structure.py / daily_internal_structure.py /
// daily_fractal_structure.py's own current defaults (20/8/2). Not
// user-configurable here since within_daily_ob is a secondary factor and
// six more period inputs (three native + three parent) was judged not
// worth the clutter.
const DAILY_PARENT_PERIODS = { swing: 20, internal: 8, fractal: 2 };

// Largest-to-smallest tier rank, mirrors H4_TIER_PREFIXES /
// DAILY_TIER_PREFIXES's ordering in swing_structure/order_blocks.py:
// whichever tier in a merged group ranks lowest here becomes primary_tier.
const TIER_RANK = { swing: 0, internal: 1, fractal: 2 };

// ---------------------------------------------------------------------
// TIMEFRAME ISOLATION. Copied from h4_swing_structure.py's own verbatim
// block (fxrscripts/README.md: duplicated across all nine structure
// scripts), adapted only in what gets cleaned up on mismatch: rectangles
// tagged by backgroundColor instead of trendLines tagged by linewidth,
// since this script draws OB/FVG zones, not tier lines.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 14400000; // 4H, matches every h4_*.py sibling.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;
const TF_CLEANUP_ON_MISMATCH = true;

// This script's own drawing tags. backgroundColor is unused by every
// other fxrscripts/*.py script (they all tag trendLine/horizontal_line
// via linewidth/linecolor), so any RGBA set here is already collision-free
// with the rest of this codebase; distinct hues are still chosen per
// direction/state purely for on-chart readability, not for tag safety.
const OB_BULLISH_COLOR = color.rgba(0, 200, 5, 0.28);
const OB_BULLISH_COLOR_DIM = color.rgba(0, 200, 5, 0.10);
const OB_BEARISH_COLOR = color.rgba(220, 30, 30, 0.28);
const OB_BEARISH_COLOR_DIM = color.rgba(220, 30, 30, 0.10);
const FVG_BULLISH_COLOR = color.rgba(0, 140, 255, 0.16);
const FVG_BEARISH_COLOR = color.rgba(255, 140, 0, 0.16);

// NOTE: deliberately no top-level helper here to test backgroundColor
// against the six tag colors above. fxrscripts/README.md's resolved
// question 4 confirms a helper declared alongside onTick cannot see ANY
// top-level declaration, `let` OR `const` (FRACTAL_TIE_TOLERANCE threw
// "not defined" from inside a sibling helper despite being const). The
// OR-chain is therefore inlined directly inside onTick's own two
// deleteDrawingByCondition callbacks below, which — like every sibling
// script's identical pattern — are closures created INSIDE onTick's own
// body, not separate top-level declarations, so they retain visibility
// into onTick's enclosing scope (including these top-level consts) the
// normal JS way.

// ---------------------------------------------------------------------
// MODULE-LEVEL STATE. Two timeframe contexts (native 4H, parent Daily via
// mtf), each: growing OHLC arrays, three tier engines (swing/internal/
// fractal), an fvgs array (native only), an orderBlocks array. Plain `let`
// objects, mutated in place by helpers that receive them as parameters —
// helpers never reference these names directly (fxrscripts/README.md,
// resolved question 4: a helper declared alongside onTick cannot see ANY
// top-level declaration).
// ---------------------------------------------------------------------

const makeTierState = () => ({
  swingHigh: NaN,
  swingHighBarIndex: 0,
  highCrossed: false,
  swingLow: NaN,
  swingLowBarIndex: 0,
  lowCrossed: false,
  structure: null,
  // History snapshots, one push per bar, index-aligned with the ctx's own
  // OHLC arrays. Only populated for the native tiers (needed by
  // compute_swept_liquidity_structural); left empty and unused for the
  // parent Daily tiers, which only need current scalars for OB anchor
  // selection, not historical structure.
  swingHighHist: [],
  swingLowHist: [],
  structureHist: [],
});

const makeCtx = () => ({
  opens: [],
  highs: [],
  lows: [],
  closes: [],
  times: [],
  lastSeenTime: null,
  tiers: { swing: makeTierState(), internal: makeTierState(), fractal: makeTierState() },
  fvgs: [],
  orderBlocks: [],
});

let h4Ctx = makeCtx();
let dailyCtx = makeCtx();
let obLastSpacing = null; // timeframe-guard memory, separate from either ctx's own lastSeenTime.

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  input.int('4H OB Swing Periods', 20, 'swingPeriods', 2, 100, 1, 'Williams Fractal periods for the swing tier (largest scale) feeding OB anchor selection and swept_liquidity_swing.', 'Order Block Settings');
  input.int('4H OB Internal Periods', 8, 'internalPeriods', 2, 100, 1, 'Williams Fractal periods for the internal tier (intermediate scale).', 'Order Block Settings');
  input.int('4H OB Fractal Periods', 2, 'fractalPeriods', 2, 100, 1, 'Williams Fractal periods for the fractal tier (fastest scale).', 'Order Block Settings');
  input.int('OB ATR Period', ATR_PERIOD_DEFAULT, 'atrPeriod', 2, 200, 1, 'Wilder ATR period shared by anchor-selection displacement detection and zone-shaping bands, on both the native 4H and the mtf Daily leg.', 'Order Block Settings');
  input.int('FVG Lookback', FVG_LOOKBACK_DEFAULT, 'fvgLookback', 10, 500, 1, 'Candles since formation an FVG stays valid without a 50% fill before it expires.', 'Order Block Settings');
  input.bool('Show FVGs', true, 'showFvgs');

  // Called exactly once here, never inside onTick, per fx-replay-docs/
  // multi-timeframe's explicit warning. Requests Daily data purely so this
  // script's OWN parent-tier engine (below) can be fed from it — see the
  // header comment's "SECOND DEVIATION" for why this never queries mtf at
  // any index beyond 0.
  mtf.timeframe('1D');
};

// =======================================================================
// SHARED HELPERS. Every one takes everything it needs as an explicit
// parameter; none reference h4Ctx/dailyCtx/any top-level let or const by
// name, so the same functions serve both the native 4H context and the
// mtf-fed Daily (parent) context.
// =======================================================================

// One Williams Fractal engine tick. Mirrors h4_swing_structure.py's
// per-tick logic exactly (strict near-frontier, tie-tolerant far-frontier,
// break-crossing before pivot-update, same ordering), reindexed to plain
// array positions instead of time-offset accessor calls (see header
// comment). `highs`/`lows`/`closes` are the ctx's own growing arrays;
// `barIndex` is the position just pushed for the current closed candle.
// Returns which side broke for real this tick, and the leg_start bar
// index for each (the OLD confirm-bar-index, i.e. swept BEFORE any
// pivot update this same tick — same ordering fractal_detector.py uses).
const updateTierEngine = (tier, periods, tieTolerance, barIndex, highs, lows, closes, pushHistory) => {
  const closeToday = closes[barIndex];

  let highBrokeReal = false;
  let lowBrokeReal = false;
  const legStartForHigh = tier.swingHighBarIndex;
  const legStartForLow = tier.swingLowBarIndex;

  if (!Number.isNaN(tier.swingHigh) && !tier.highCrossed && closeToday > tier.swingHigh) {
    tier.highCrossed = true;
    highBrokeReal = true;
  }
  if (!Number.isNaN(tier.swingLow) && !tier.lowCrossed && closeToday < tier.swingLow) {
    tier.lowCrossed = true;
    lowBrokeReal = true;
  }

  const pivotIndex = barIndex - periods;
  if (pivotIndex >= 0) {
    const pivotHigh = highs[pivotIndex];
    let isUpFractal = true;
    for (let t = 1; t <= periods; t++) {
      const v = highs[pivotIndex + t];
      if (v === undefined || !(v < pivotHigh)) { isUpFractal = false; break; }
    }
    if (isUpFractal) {
      let farOk = false;
      for (let k = 0; k <= tieTolerance && !farOk; k++) {
        let tieRunOk = true;
        for (let t = 1; t <= k; t++) {
          const v = highs[pivotIndex - t];
          if (v === undefined || !(v <= pivotHigh)) { tieRunOk = false; break; }
        }
        if (!tieRunOk) continue;
        let strictOk = true;
        for (let t = 1; t <= periods; t++) {
          const v = highs[pivotIndex - k - t];
          if (v === undefined || !(v < pivotHigh)) { strictOk = false; break; }
        }
        if (strictOk) farOk = true;
      }
      isUpFractal = farOk;
    }
    if (isUpFractal) {
      tier.swingHigh = pivotHigh;
      tier.swingHighBarIndex = barIndex;
      tier.highCrossed = false;
    }

    const pivotLow = lows[pivotIndex];
    let isDownFractal = true;
    for (let t = 1; t <= periods; t++) {
      const v = lows[pivotIndex + t];
      if (v === undefined || !(v > pivotLow)) { isDownFractal = false; break; }
    }
    if (isDownFractal) {
      let farOk = false;
      for (let k = 0; k <= tieTolerance && !farOk; k++) {
        let tieRunOk = true;
        for (let t = 1; t <= k; t++) {
          const v = lows[pivotIndex - t];
          if (v === undefined || !(v >= pivotLow)) { tieRunOk = false; break; }
        }
        if (!tieRunOk) continue;
        let strictOk = true;
        for (let t = 1; t <= periods; t++) {
          const v = lows[pivotIndex - k - t];
          if (v === undefined || !(v > pivotLow)) { strictOk = false; break; }
        }
        if (strictOk) farOk = true;
      }
      isDownFractal = farOk;
    }
    if (isDownFractal) {
      tier.swingLow = pivotLow;
      tier.swingLowBarIndex = barIndex;
      tier.lowCrossed = false;
    }
  }

  if (highBrokeReal) tier.structure = 'bullish';
  else if (lowBrokeReal) tier.structure = 'bearish';

  if (pushHistory) {
    tier.swingHighHist[barIndex] = tier.swingHigh;
    tier.swingLowHist[barIndex] = tier.swingLow;
    tier.structureHist[barIndex] = tier.structure;
  }

  return { highBrokeReal, lowBrokeReal, legStartForHigh, legStartForLow };
};

// swing_structure/order_blocks.py's _find_displacement_anchor, ported
// directly: scans [legStart, breakIndex] ascending (chronological) for
// the first candle whose range exceeds atrMultiple*ATR, falls back to
// breakIndex itself, returns the candle immediately before it. null if
// that would be before index 0.
const findDisplacementAnchor = (highs, lows, atrSeries, legStart, breakIndex, atrMultiple) => {
  let displacementIndex = null;
  for (let k = legStart; k <= breakIndex; k++) {
    const atrK = atrSeries[k];
    if (atrK === undefined || Number.isNaN(atrK)) continue;
    if (highs[k] - lows[k] > atrMultiple * atrK) { displacementIndex = k; break; }
  }
  if (displacementIndex === null) displacementIndex = breakIndex;
  const anchor = displacementIndex - 1;
  if (anchor < 0) return null;
  return anchor;
};

// _band: medium is the no-trim default, ATR-unknown also defaults medium.
const bandOf = (rangeValue, atrValue) => {
  if (atrValue === undefined || Number.isNaN(atrValue)) return 'medium';
  if (rangeValue < 0.5 * atrValue) return 'low';
  if (rangeValue > 1.0 * atrValue) return 'high';
  return 'medium';
};

// _shape_zone, ported directly: medium keeps the full wick, high trims the
// near edge to the body, low tries forward-then-backward-then-3-candle
// merges before falling back to whichever attempt exists. Returns
// {top, bottom, zoneEnd}, zoneEnd being the rightmost candle actually
// folded in (matches order_blocks.py's own zone_end semantics).
const shapeZone = (anchor, direction, opens, highs, lows, closes, atrSeries) => {
  const atrAtAnchor = atrSeries[anchor];
  const anchorRange = highs[anchor] - lows[anchor];
  const bandAtAnchor = bandOf(anchorRange, atrAtAnchor);

  if (bandAtAnchor === 'medium') {
    return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
  }
  if (bandAtAnchor === 'high') {
    if (direction === 'bullish') {
      return { top: Math.max(opens[anchor], closes[anchor]), bottom: lows[anchor], zoneEnd: anchor };
    }
    return { top: highs[anchor], bottom: Math.min(opens[anchor], closes[anchor]), zoneEnd: anchor };
  }

  const combinedRange = (idxs) => {
    let top = -Infinity;
    let bottom = Infinity;
    for (const j of idxs) {
      top = Math.max(top, highs[j]);
      bottom = Math.min(bottom, lows[j]);
    }
    return { top, bottom };
  };

  let forwardResult = null;
  if (highs[anchor + 1] !== undefined) {
    const f = combinedRange([anchor, anchor + 1]);
    const fBand = bandOf(f.top - f.bottom, atrAtAnchor);
    if (fBand === 'medium') return { top: f.top, bottom: f.bottom, zoneEnd: anchor + 1 };
    forwardResult = { top: f.top, bottom: f.bottom, band: fBand };
  }

  let backwardResult = null;
  if (anchor - 1 >= 0) {
    const b = combinedRange([anchor - 1, anchor]);
    const bBand = bandOf(b.top - b.bottom, atrAtAnchor);
    if (bBand === 'medium') return { top: b.top, bottom: b.bottom, zoneEnd: anchor };
    backwardResult = { top: b.top, bottom: b.bottom, band: bBand };
  }

  const bothHigh =
    forwardResult !== null && forwardResult.band === 'high' &&
    backwardResult !== null && backwardResult.band === 'high';
  if (bothHigh) {
    return { top: forwardResult.top, bottom: forwardResult.bottom, zoneEnd: anchor + 1 };
  }

  if (anchor - 1 >= 0 && highs[anchor + 1] !== undefined) {
    const t3 = combinedRange([anchor - 1, anchor, anchor + 1]);
    return { top: t3.top, bottom: t3.bottom, zoneEnd: anchor + 1 };
  }

  if (forwardResult !== null) {
    return { top: forwardResult.top, bottom: forwardResult.bottom, zoneEnd: anchor + 1 };
  }
  if (backwardResult !== null) {
    return { top: backwardResult.top, bottom: backwardResult.bottom, zoneEnd: anchor };
  }
  return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
};

// Finds an existing OB with the same (direction, anchor) and merges this
// tier's contribution into it (widening legStart/earliestTrigger to the
// min, upgrading primaryTier if this tier outranks the current one, per
// order_blocks.py's tiers_in_group[0] convention), or creates a fresh row
// if none exists. anchor/legStart/barIndex are all plain array positions
// into the SAME ctx this tier belongs to.
const upsertOrderBlock = (orderBlocks, direction, anchor, tierName, tierRank, barIndex, legStart, opens, highs, lows, closes, atrSeries, times) => {
  for (const ob of orderBlocks) {
    if (ob.direction === direction && ob.anchorBarIndex === anchor) {
      if (ob.triggerTiers.indexOf(tierName) === -1) ob.triggerTiers.push(tierName);
      if (legStart < ob.legStartBarIndex) ob.legStartBarIndex = legStart;
      if (barIndex < ob.earliestTriggerBarIndex) ob.earliestTriggerBarIndex = barIndex;
      if (tierRank[tierName] < tierRank[ob.primaryTier]) {
        ob.primaryTier = tierName;
        ob.triggerBarIndex = barIndex;
      }
      return ob;
    }
  }

  const zone = shapeZone(anchor, direction, opens, highs, lows, closes, atrSeries);
  const ob = {
    direction,
    anchorBarIndex: anchor,
    anchorTime: times[anchor],
    top: zone.top,
    bottom: zone.bottom,
    zoneEndBarIndex: zone.zoneEnd,
    isSingleCandle: zone.zoneEnd === anchor,
    triggerTiers: [tierName],
    primaryTier: tierName,
    triggerBarIndex: barIndex,
    legStartBarIndex: legStart,
    earliestTriggerBarIndex: barIndex,
    causedDisplacement: false,
    causedImbalance: false,
    displacementChecked: false,
    imbalanceChecked: false,
    mitigated: false,
    mitigatedBarIndex: null,
    invalidated: false,
    invalidatedBarIndex: null,
    sweptSwing: false,
    sweptInternal: false,
    sweptFractal: false,
    sweptFvg: false,
    sweptPreviousCandle: false,
    hasInducement: false,
    isFlipZone: false,
    flippedObAnchorBarIndex: null,
    withinDailyOb: false,
  };
  orderBlocks.push(ob);
  return ob;
};

// caused_displacement/caused_imbalance, deferred (see header comment,
// "THIRD DEVIATION"): evaluated exactly once each, the first tick each
// becomes knowable. Same tests order_blocks.py runs against zone_end.
//
// FIX (was `barIndex === ob.zoneEndBarIndex + 1/+2`, using `closes[barIndex]`/
// `lows[barIndex]`/`highs[barIndex]` as the candle to test): an OB's anchor
// comes from a leg-scan that ends at the break bar, so zoneEndBarIndex is
// usually FAR earlier than barIndex at the tick the OB is created (creation
// happens at earliestTriggerBarIndex, i.e. the break). By that tick,
// zoneEndBarIndex+1/+2 are already-closed historical bars, so `barIndex`
// (the current, most-recent bar) had already moved past them — the `===`
// equality could never become true again, and closes[barIndex] tested the
// wrong candle even on ticks where it coincidentally did. Both flags were
// landing permanently false for any OB whose anchor sat more than 1-2 bars
// before its trigger, which is the common case, not the exception.
// Fixed by reading the FIXED candle position ob.zoneEndBarIndex+1/+2
// directly (available the moment barIndex reaches or passes it, most often
// immediately at OB creation) instead of assuming "now" IS that position.
const evaluateDeferredFlags = (ob, barIndex, highs, lows, closes) => {
  if (!ob.displacementChecked && barIndex >= ob.zoneEndBarIndex + 1) {
    const k = ob.zoneEndBarIndex + 1;
    ob.causedDisplacement = ob.direction === 'bullish'
      ? closes[k] > highs[ob.zoneEndBarIndex]
      : closes[k] < lows[ob.zoneEndBarIndex];
    ob.displacementChecked = true;
  }
  if (!ob.imbalanceChecked && barIndex >= ob.zoneEndBarIndex + 2) {
    const k = ob.zoneEndBarIndex + 2;
    ob.causedImbalance = ob.direction === 'bullish'
      ? lows[k] > highs[ob.zoneEndBarIndex]
      : highs[k] < lows[ob.zoneEndBarIndex];
    ob.imbalanceChecked = true;
  }
};

// _apply_mitigation / _apply_invalidation, ported as a per-tick check
// (equivalent to their forward scan since this runs every tick from
// creation onward): wick-touch mitigation, close-through invalidation,
// both one-time, both starting strictly after earliestTriggerBarIndex.
const applyMitigationInvalidationTick = (ob, barIndex, highs, lows, closes) => {
  if (barIndex <= ob.earliestTriggerBarIndex) return;
  if (!ob.mitigated && lows[barIndex] <= ob.top && highs[barIndex] >= ob.bottom) {
    ob.mitigated = true;
    ob.mitigatedBarIndex = barIndex;
  }
  if (!ob.invalidated) {
    const brokeThrough = ob.direction === 'bullish' ? closes[barIndex] < ob.bottom : closes[barIndex] > ob.top;
    if (brokeThrough) {
      ob.invalidated = true;
      ob.invalidatedBarIndex = barIndex;
    }
  }
};

// fair_value_gaps.py's 3-candle gap test, run once per newly closed
// candle against the single new window it completes (k-2, k-1, k) —
// equivalent to the offline version's full scan since every earlier
// window was already checked on its own tick.
const detectFvgCandidates = (k, highs, lows) => {
  const candidates = [];
  if (k - 2 < 0) return candidates;
  if (lows[k] > highs[k - 2]) {
    candidates.push({ direction: 'bullish', formedBarIndex: k, top: lows[k], bottom: highs[k - 2] });
  }
  if (highs[k] < lows[k - 2]) {
    candidates.push({ direction: 'bearish', formedBarIndex: k, top: lows[k - 2], bottom: highs[k] });
  }
  return candidates;
};

// FVG lifecycle: 50%-of-range wick fill or lookback expiry, whichever
// first, both one-time. No width filter, per fair_value_gaps.py.
const updateFvgTick = (fvg, barIndex, highs, lows, lookback) => {
  if (fvg.filled || fvg.expired) return;
  if (barIndex <= fvg.formedBarIndex) return;
  const midpoint = (fvg.top + fvg.bottom) / 2;
  const reached = fvg.direction === 'bullish' ? lows[barIndex] <= midpoint : highs[barIndex] >= midpoint;
  if (reached) {
    fvg.filled = true;
    fvg.filledBarIndex = barIndex;
    return;
  }
  if (barIndex >= fvg.formedBarIndex + lookback) {
    fvg.expired = true;
    fvg.expiryBarIndex = fvg.formedBarIndex + lookback;
  }
};

// compute_swept_liquidity_structural, ported per-OB: scans
// [legStartBarIndex, earliestTriggerBarIndex] for a wick sweep of the
// tier's own currently-active strong point while that tier's OWN
// structure held that side, using the tier's HISTORICAL (not current)
// structure/pivot snapshots — exactly what structured_df's per-row
// columns give the Python version.
const computeSweptStructural = (ob, tiers, highs, lows) => {
  const result = {};
  for (const name of ['swing', 'internal', 'fractal']) {
    const tier = tiers[name];
    let swept = false;
    for (let k = ob.legStartBarIndex; k <= ob.earliestTriggerBarIndex; k++) {
      if (ob.direction === 'bullish') {
        if (tier.structureHist[k] === 'bullish' && lows[k] < tier.swingLowHist[k]) { swept = true; break; }
      } else {
        if (tier.structureHist[k] === 'bearish' && highs[k] > tier.swingHighHist[k]) { swept = true; break; }
      }
    }
    result[name] = swept;
  }
  return result;
};

// compute_fvg_confluence: an active, same-direction, price-overlapping FVG
// that already existed before the OB's anchor, with none of the OB's own
// formation candles (anchor..zoneEnd) closing past the FVG's far edge.
const computeFvgConfluence = (ob, fvgs, closes, currentBarIndex) => {
  for (const fvg of fvgs) {
    if (fvg.direction !== ob.direction) continue;
    const activeUntil = fvg.filled ? fvg.filledBarIndex : (fvg.expired ? fvg.expiryBarIndex : currentBarIndex);
    if (!(fvg.formedBarIndex < ob.anchorBarIndex && ob.anchorBarIndex <= activeUntil)) continue;
    const overlaps = ob.bottom <= fvg.top && ob.top >= fvg.bottom;
    if (!overlaps) continue;
    let farEdgeBreached = false;
    for (let j = ob.anchorBarIndex; j <= ob.zoneEndBarIndex; j++) {
      const breached = ob.direction === 'bullish' ? closes[j] < fvg.bottom : closes[j] > fvg.top;
      if (breached) { farEdgeBreached = true; break; }
    }
    if (!farEdgeBreached) return true;
  }
  return false;
};

// compute_previous_candle_sweep: single-candle OBs only.
const computePreviousCandleSweep = (ob, highs, lows, closes) => {
  if (!ob.isSingleCandle || ob.anchorBarIndex === 0) return false;
  const a = ob.anchorBarIndex;
  if (ob.direction === 'bullish') {
    return lows[a] < lows[a - 1] && closes[a] >= lows[a - 1];
  }
  return highs[a] > highs[a - 1] && closes[a] <= highs[a - 1];
};

// compute_inducement: an earlier, same-direction, not-yet-invalidated OB
// sitting nearer to price with gap < max(height(x), height(y)).
const computeInducementFlag = (x, allObs) => {
  for (const y of allObs) {
    if (y === x || y.invalidated) continue;
    if (y.direction !== x.direction) continue;
    if (y.anchorBarIndex >= x.anchorBarIndex) continue;
    const heightX = x.top - x.bottom;
    const heightY = y.top - y.bottom;
    let nearer;
    let gap;
    if (x.direction === 'bullish') {
      nearer = y.bottom > x.top;
      gap = y.bottom - x.top;
    } else {
      nearer = y.top < x.bottom;
      gap = x.bottom - y.top;
    }
    if (nearer && gap < Math.max(heightX, heightY)) return true;
  }
  return false;
};

// compute_flip_zone: an earlier, opposite-direction OB W that Z's own
// formation candles mitigated, later invalidated at/after Z went live.
// Returns W (for flippedObAnchorBarIndex) or null.
const computeFlipZoneMatch = (z, allObs) => {
  for (const w of allObs) {
    if (w === z) continue;
    if (w.direction === z.direction) continue;
    if (w.anchorBarIndex >= z.anchorBarIndex) continue;
    if (!w.mitigated) continue;
    const reactedOnW = z.anchorBarIndex <= w.mitigatedBarIndex && w.mitigatedBarIndex <= z.zoneEndBarIndex;
    if (!reactedOnW) continue;
    if (!w.invalidated) continue;
    if (w.invalidatedBarIndex < z.earliestTriggerBarIndex) continue;
    return w;
  }
  return null;
};

// compute_containment: same-direction, not-yet-invalidated parent that either
// fully engulfs the child or overlaps by >= 1/3 of the PARENT's height.
const computeContainmentFlag = (child, parents) => {
  for (const parent of parents) {
    if (parent.invalidated || parent.direction !== child.direction) continue;
    const overlap = Math.min(child.top, parent.top) - Math.max(child.bottom, parent.bottom);
    if (overlap <= 0) continue;
    const fullyEngulfed = child.top <= parent.top && child.bottom >= parent.bottom;
    const parentHeight = parent.top - parent.bottom;
    if (fullyEngulfed || overlap / parentHeight >= 1 / 3) return true;
  }
  return false;
};

// Abbreviated factor label, drawn on each OB rectangle. See header
// comment: rectangle()'s text argument is itself UNVERIFIED.
const buildFactorLabel = (ob) => {
  const parts = [];
  if (ob.sweptSwing) parts.push('SWG');
  if (ob.sweptInternal) parts.push('INT');
  if (ob.sweptFractal) parts.push('FRC');
  if (ob.sweptFvg) parts.push('FVG');
  if (ob.sweptPreviousCandle) parts.push('PC');
  if (ob.hasInducement) parts.push('IND');
  if (ob.isFlipZone) parts.push('FLIP');
  if (ob.causedDisplacement) parts.push('DISP');
  if (ob.causedImbalance) parts.push('IMB');
  if (ob.withinDailyOb) parts.push('DLY');
  return parts.join(' ');
};

// =======================================================================
// onTick
// =======================================================================

onTick = (length, _moment, _, ta, inputs) => {
  const swingPeriods = inputs.swingPeriods;
  const internalPeriods = inputs.internalPeriods;
  const fractalPeriods = inputs.fractalPeriods;
  const atrPeriod = inputs.atrPeriod;
  const fvgLookback = inputs.fvgLookback;
  const showFvgs = inputs.showFvgs;

  // ---- Timeframe gate, MUST run before any new-bar gate below. Copied
  // in shape from h4_swing_structure.py; see fxrscripts/README.md. ----
  let inferredSpacing = null;
  {
    let spacing = null;
    let valid = 0;
    for (let i = 0; i < TF_PROBE_BARS; i++) {
      const newer = time(i);
      const older = time(i + 1);
      if (Number.isNaN(newer) || Number.isNaN(older)) break;
      const delta = newer - older;
      if (delta <= 0) continue;
      valid += 1;
      if (spacing === null || delta < spacing) spacing = delta;
    }
    inferredSpacing = valid < TF_MIN_VALID_BARS ? null : spacing;
  }

  if (inferredSpacing === null) {
    return;
  }

  if (inferredSpacing !== MY_TIMEFRAME_MS) {
    if (TF_CLEANUP_ON_MISMATCH) {
      deleteDrawingByCondition((drawing) => {
        const opts = drawing.overrideOptions;
        if (!opts) return false;
        const bg = opts['backgroundColor'];
        return (
          bg === OB_BULLISH_COLOR || bg === OB_BULLISH_COLOR_DIM ||
          bg === OB_BEARISH_COLOR || bg === OB_BEARISH_COLOR_DIM ||
          bg === FVG_BULLISH_COLOR || bg === FVG_BEARISH_COLOR
        );
      });
    }
    obLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== obLastSpacing) {
    // Arrived on 4H from another timeframe: reset EVERYTHING, both
    // contexts, so nothing resumes on top of another timeframe's bars.
    h4Ctx = makeCtx();
    dailyCtx = makeCtx();
    obLastSpacing = inferredSpacing;
  }

  // =====================================================================
  // PARENT (Daily, via mtf) leg. Gated on its OWN new-bar detection
  // (mtf.time(0, false) changing), independent of the native gate below —
  // see header comment's "SECOND DEVIATION" for why only index 0 is ever
  // read from mtf.
  // =====================================================================
  const currentMtfTime = mtf.time(0, false);
  if (!Number.isNaN(currentMtfTime) && currentMtfTime !== dailyCtx.lastSeenTime) {
    dailyCtx.lastSeenTime = currentMtfTime;

    dailyCtx.opens.push(mtf.openC(0, false));
    dailyCtx.highs.push(mtf.high(0, false));
    dailyCtx.lows.push(mtf.low(0, false));
    dailyCtx.closes.push(mtf.closeC(0, false));
    dailyCtx.times.push(currentMtfTime);
    const dBarIndex = dailyCtx.times.length - 1;

    const dAtrSeries = ta.atr(dailyCtx.highs, dailyCtx.lows, dailyCtx.closes, atrPeriod);

    const dTierDefs = [
      { name: 'swing', periods: DAILY_PARENT_PERIODS.swing },
      { name: 'internal', periods: DAILY_PARENT_PERIODS.internal },
      { name: 'fractal', periods: DAILY_PARENT_PERIODS.fractal },
    ];

    for (const def of dTierDefs) {
      const tier = dailyCtx.tiers[def.name];
      const res = updateTierEngine(
        tier, def.periods, FRACTAL_TIE_TOLERANCE, dBarIndex,
        dailyCtx.highs, dailyCtx.lows, dailyCtx.closes, false
      );
      if (res.highBrokeReal) {
        const anchor = findDisplacementAnchor(dailyCtx.highs, dailyCtx.lows, dAtrSeries, res.legStartForHigh, dBarIndex, DISPLACEMENT_ATR_MULTIPLE);
        if (anchor !== null) {
          upsertOrderBlock(dailyCtx.orderBlocks, 'bullish', anchor, def.name, TIER_RANK, dBarIndex, res.legStartForHigh, dailyCtx.opens, dailyCtx.highs, dailyCtx.lows, dailyCtx.closes, dAtrSeries, dailyCtx.times);
        }
      }
      if (res.lowBrokeReal) {
        const anchor = findDisplacementAnchor(dailyCtx.highs, dailyCtx.lows, dAtrSeries, res.legStartForLow, dBarIndex, DISPLACEMENT_ATR_MULTIPLE);
        if (anchor !== null) {
          upsertOrderBlock(dailyCtx.orderBlocks, 'bearish', anchor, def.name, TIER_RANK, dBarIndex, res.legStartForLow, dailyCtx.opens, dailyCtx.highs, dailyCtx.lows, dailyCtx.closes, dAtrSeries, dailyCtx.times);
        }
      }
    }

    // Both mitigation and invalidation are needed for containment
    // (compute_containment reads parent["invalidated"], not
    // parent["mitigated"], so a daily OB must stop counting as a
    // containment parent once it's genuinely broken, not just touched).
    for (const ob of dailyCtx.orderBlocks) {
      if (dBarIndex <= ob.earliestTriggerBarIndex) continue;
      if (!ob.mitigated && dailyCtx.lows[dBarIndex] <= ob.top && dailyCtx.highs[dBarIndex] >= ob.bottom) {
        ob.mitigated = true;
        ob.mitigatedBarIndex = dBarIndex;
      }
      if (!ob.invalidated) {
        const brokeThrough = ob.direction === 'bullish'
          ? dailyCtx.closes[dBarIndex] < ob.bottom
          : dailyCtx.closes[dBarIndex] > ob.top;
        if (brokeThrough) {
          ob.invalidated = true;
          ob.invalidatedBarIndex = dBarIndex;
        }
      }
    }
  }

  // =====================================================================
  // NATIVE 4H leg. Gated on this script's own new-bar detection, same
  // `time(0) === lastSeenTime` idiom every sibling script uses.
  // =====================================================================
  const currentTime = time(0);
  if (currentTime === h4Ctx.lastSeenTime) {
    return;
  }
  h4Ctx.lastSeenTime = currentTime;

  h4Ctx.opens.push(openC(0));
  h4Ctx.highs.push(high(0));
  h4Ctx.lows.push(low(0));
  h4Ctx.closes.push(closeC(0));
  h4Ctx.times.push(currentTime);
  const barIndex = h4Ctx.times.length - 1;

  const atrSeries = ta.atr(h4Ctx.highs, h4Ctx.lows, h4Ctx.closes, atrPeriod);

  const tierDefs = [
    { name: 'swing', periods: swingPeriods },
    { name: 'internal', periods: internalPeriods },
    { name: 'fractal', periods: fractalPeriods },
  ];

  for (const def of tierDefs) {
    const tier = h4Ctx.tiers[def.name];
    const res = updateTierEngine(
      tier, def.periods, FRACTAL_TIE_TOLERANCE, barIndex,
      h4Ctx.highs, h4Ctx.lows, h4Ctx.closes, true
    );
    if (res.highBrokeReal) {
      const anchor = findDisplacementAnchor(h4Ctx.highs, h4Ctx.lows, atrSeries, res.legStartForHigh, barIndex, DISPLACEMENT_ATR_MULTIPLE);
      if (anchor !== null) {
        upsertOrderBlock(h4Ctx.orderBlocks, 'bullish', anchor, def.name, TIER_RANK, barIndex, res.legStartForHigh, h4Ctx.opens, h4Ctx.highs, h4Ctx.lows, h4Ctx.closes, atrSeries, h4Ctx.times);
      }
    }
    if (res.lowBrokeReal) {
      const anchor = findDisplacementAnchor(h4Ctx.highs, h4Ctx.lows, atrSeries, res.legStartForLow, barIndex, DISPLACEMENT_ATR_MULTIPLE);
      if (anchor !== null) {
        upsertOrderBlock(h4Ctx.orderBlocks, 'bearish', anchor, def.name, TIER_RANK, barIndex, res.legStartForLow, h4Ctx.opens, h4Ctx.highs, h4Ctx.lows, h4Ctx.closes, atrSeries, h4Ctx.times);
      }
    }
  }

  // ---- New FVGs formed by the 3-candle window ending at this bar ----
  const newFvgs = detectFvgCandidates(barIndex, h4Ctx.highs, h4Ctx.lows);
  for (const fvg of newFvgs) {
    fvg.filled = false;
    fvg.filledBarIndex = null;
    fvg.expired = false;
    fvg.expiryBarIndex = null;
    h4Ctx.fvgs.push(fvg);
  }

  // ---- Per-tick lifecycle updates on every existing OB/FVG ----
  for (const ob of h4Ctx.orderBlocks) {
    evaluateDeferredFlags(ob, barIndex, h4Ctx.highs, h4Ctx.lows, h4Ctx.closes);
    applyMitigationInvalidationTick(ob, barIndex, h4Ctx.highs, h4Ctx.lows, h4Ctx.closes);
  }
  for (const fvg of h4Ctx.fvgs) {
    updateFvgTick(fvg, barIndex, h4Ctx.highs, h4Ctx.lows, fvgLookback);
  }

  // ---- Recompute every applicable quality factor over the whole table,
  // fresh, every closed candle (mirrors order_block_quality.py's own
  // "recompute over the whole OB table" convention). ----
  for (const ob of h4Ctx.orderBlocks) {
    const swept = computeSweptStructural(ob, h4Ctx.tiers, h4Ctx.highs, h4Ctx.lows);
    ob.sweptSwing = swept.swing;
    ob.sweptInternal = swept.internal;
    ob.sweptFractal = swept.fractal;

    ob.sweptFvg = computeFvgConfluence(ob, h4Ctx.fvgs, h4Ctx.closes, barIndex);
    ob.sweptPreviousCandle = computePreviousCandleSweep(ob, h4Ctx.highs, h4Ctx.lows, h4Ctx.closes);
    ob.hasInducement = computeInducementFlag(ob, h4Ctx.orderBlocks);

    const flipMatch = computeFlipZoneMatch(ob, h4Ctx.orderBlocks);
    ob.isFlipZone = flipMatch !== null;
    ob.flippedObAnchorBarIndex = flipMatch !== null ? flipMatch.anchorBarIndex : null;

    ob.withinDailyOb = computeContainmentFlag(ob, dailyCtx.orderBlocks);
  }

  // =====================================================================
  // DRAWING. Delete every rectangle this script owns (matched on our
  // reserved backgroundColor set, per rectangle_lifecycle_probe.py's
  // UNVERIFIED design — see header comment), then redraw fresh: one
  // rectangle per still-relevant OB (invalidated ones skipped entirely,
  // mitigated ones dimmed), and, if enabled, one per still-active FVG.
  // =====================================================================
  deleteDrawingByCondition((drawing) => {
    const opts = drawing.overrideOptions;
    if (!opts) return false;
    const bg = opts['backgroundColor'];
    return (
      bg === OB_BULLISH_COLOR || bg === OB_BULLISH_COLOR_DIM ||
      bg === OB_BEARISH_COLOR || bg === OB_BEARISH_COLOR_DIM ||
      bg === FVG_BULLISH_COLOR || bg === FVG_BEARISH_COLOR
    );
  });

  for (const ob of h4Ctx.orderBlocks) {
    if (ob.invalidated) continue;

    const bullish = ob.direction === 'bullish';
    const bg = ob.mitigated
      ? (bullish ? OB_BULLISH_COLOR_DIM : OB_BEARISH_COLOR_DIM)
      : (bullish ? OB_BULLISH_COLOR : OB_BEARISH_COLOR);
    const border = bullish ? color.green : color.red;
    const label = buildFactorLabel(ob);

    // UNVERIFIED: rectangle() + a text argument, modelled on
    // rectangle_lifecycle_probe.py's own untested call shape. If the
    // editor rejects the 6th argument, drop it and fall back to a
    // separate text()/note()/callout() call at the same two corners.
    rectangle(
      ob.anchorTime, ob.top,
      currentTime, ob.bottom,
      {
        backgroundColor: bg,
        color: border,
        fillBackground: true,
        showLabel: true,
        textColor: border,
        bold: true,
      },
      label
    );
  }

  if (showFvgs) {
    for (const fvg of h4Ctx.fvgs) {
      if (fvg.filled || fvg.expired) continue;
      const bullish = fvg.direction === 'bullish';
      const bg = bullish ? FVG_BULLISH_COLOR : FVG_BEARISH_COLOR;
      const border = bullish ? color.aqua : color.orange;
      rectangle(
        h4Ctx.times[fvg.formedBarIndex], fvg.top,
        currentTime, fvg.bottom,
        { backgroundColor: bg, color: border, fillBackground: true },
        ''
      );
    }
  }
};
