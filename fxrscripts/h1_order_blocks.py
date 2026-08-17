// FXR Script port of the H1 Order Block / FVG / OB-quality-factor pipeline.
//
// Ports, in one self-contained file (FXR Script has no import mechanism):
//   - swing_structure/atr.py               -> via the built-in ta.atr()
//   - swing_structure/fractal_detector.py   -> h1ObUpdateTier + the
//     frontier helpers, run twice: once natively for H1 (the timeframe
//     this script lives on) and once via mtf.* for 4H (the parent
//     timeframe within_h4_ob needs). Same three tiers as
//     h1_swing_structure.py / h1_internal_structure.py /
//     h1_fractal_structure.py (n=20/8/2), copied for the swing/internal/
//     fractal engine ITSELF, but re-expressed over accumulated OHLC
//     arrays instead of those scripts' relative time()/high()/low()
//     accessor style -- see "Deviation 1" below for why.
//   - swing_structure/market_structure.py   -> the structure flip inside
//     h1ObUpdateTier (break of swing high -> bullish, break of swing low
//     -> bearish, first genuine break decides, later opposite breaks
//     flip it).
//   - swing_structure/order_blocks.py       -> h1ObFindDisplacementAnchor
//     (anchor selection via ATR-displacement leg scan),
//     h1ObShapeZone (medium/high/low ATR-band zone shaping with the
//     up-to-3-candle merge), h1ObUpdateDisplacementImbalance
//     (caused_displacement / caused_imbalance against zone_end, not
//     always the raw anchor), h1ObUpdateMitigationInvalidation
//     (wick-touch mitigation / close-through invalidation, scanned from
//     earliest_trigger_index).
//   - swing_structure/fair_value_gaps.py    -> the FVG push-on-new-triple
//     block plus the per-tick 50%-fill / 100-candle-expiry update loop.
//   - swing_structure/order_block_quality.py -> h1ObComputeSweptStructural,
//     h1ObComputeSweptFvg, h1ObComputeSweptPrevCandle, h1ObComputeInducement,
//     h1ObComputeFlipZone, h1ObComputeContainment (all six factor
//     functions, read side by side with their Python originals before
//     shipping this file -- see the deviation/verification notes at the
//     very end of this header).
//
// Two factors are OUT OF SCOPE, per the shared spec: Old Points liquidity
// and Equals (both need detectors that don't exist yet anywhere in this
// project, Python included).
//
// ---------------------------------------------------------------------
// DEVIATION 1: why the tier engine is array-based here, not a literal
// copy of h1_swing_structure.py's relative-accessor style.
// ---------------------------------------------------------------------
// h1_swing_structure.py (and its two siblings) track one pivot at a time
// using time(i)/high(i)/low(i)/closeC(i), where i is an offset from
// "now". That works for a script whose only job is drawing today's
// pivot. Order block identification is different in kind: for a break
// happening TODAY, order_blocks.py needs to scan every candle from that
// tier's OWN last pivot confirmation (which could be dozens or hundreds
// of candles ago) up to today, hunting for an ATR-displacement candle,
// and later needs to re-examine that exact same historical window for
// the swept-liquidity-structural factor. A relative offset from "now"
// cannot address an arbitrary past window like that; a growing array
// indexed by absolute candle number can, trivially, the same way
// order_blocks.py itself just indexes into Python lists. So this file
// accumulates its own nativeHigh/nativeLow/nativeOpen/nativeClose/
// nativeTime arrays (one push per new closed H1 candle) and reimplements
// the IDENTICAL Williams Fractal algorithm (same frontier tests, same
// tie tolerance, same n=20/8/2) over array indices instead of over
// time()/high()/low() calls. The algorithm is unchanged from the sibling
// scripts; only the addressing scheme is, and it is what
// order_blocks.py's leg-scan and order_block_quality.py's structural-sweep
// scan both need to exist at all. Manual restart and the optional ATR
// leg-size separation filter (both present in the sibling structure
// scripts) are dropped here: neither is referenced anywhere in
// order_blocks.py or order_block_quality.py, and carrying them through
// six tier instances (three native, three mtf-bound) for no algorithmic
// benefit was not worth the size/risk. If a manual restart is later
// wanted for the OB tiers too, port it the same way the sibling scripts
// do it (reset swingHigh/swingLow/crossed to today's own high/low, one
// time, on the input's false->true edge).
//
// DEVIATION 2: ta.atr() is used instead of a hand-rolled Wilder
// accumulator (swing_structure/atr.py), per the shared spec's own
// preference. ta.atr(highs, lows, closes, window) is called fresh every
// tick over the whole accumulated array (the same idiom
// fx-replay-docs/examples/trend-follower-indicator.md uses for ta.sma/
// ta.rsi), returning the full historical series aligned index-for-index
// with the OHLC arrays -- exactly what the leg-scan and zone-shaping
// bands need at arbitrary past indices. UNVERIFIED: whether the ta-math
// library's own ATR seeding matches swing_structure/atr.py's specific
// convention (simple mean of the first `window` True Ranges, Wilder
// smoothing only from candle `window` onward, None/NaN before that) has
// not been checked against this codebase's own Python output. If a
// side-by-side comparison ever shows drift, the fallback is porting
// swing_structure/atr.py's exact accumulator (the commented-out shape is
// in h1_swing_structure.py's own optional ATR-separation-filter code,
// lines computing trToday/atr via the simple-mean-seed-then-Wilder
// recursion) in place of the ta.atr() calls below.
//
// DEVIATION 3: the drawing/deletion tag is matched on `backgroundColor`
// (as the shared spec asks), but the actual bullish/bearish/OB-vs-FVG
// hue lives on the `color` (border) field instead, which is left free to
// vary. This is because dimming a mitigated OB (required by the spec)
// needs a lower-alpha fill, and an exact-string delete-match against
// backgroundColor cannot vary per-drawing without breaking the match.
// The fix: backgroundColor is one of exactly three fixed constants this
// script owns (OB_BG_ACTIVE, OB_BG_DIM, FVG_BG), and the delete condition
// matches against that fixed set; color/border communicates direction
// and OB-vs-FVG instead. See "Drawing" below.
//
// UNVERIFIED, exactly as the shared spec flags going in:
//   - rectangle() + deleteDrawingByCondition() for OB/FVG zones has never
//     been run in this codebase. fxrscripts/rectangle_lifecycle_probe.py
//     exists to answer this and is meant to be run separately; this
//     script assumes its answer will be yes (bracket access on
//     backgroundColor works, no stragglers survive the delete).
//   - Attaching a text label to a rectangle via its trailing positional
//     text argument (as used in rectangle_lifecycle_probe.py's own
//     `showLabel: true, textColor, bold` + trailing string) is inferred
//     from that probe script's own usage, not from a documented
//     rectangle+text example (fx-replay-docs/multi-timeframe/mft-example.md's
//     rectangle() call has no text argument at all, and
//     fx-replay-docs/chart-drawing-tools/chart-drawing-tools.md's only
//     worked example is horizontalLine's `(price, styles, text)` shape).
//     If this turns out wrong, the documented "Text / Annotations" family
//     (`note`, `callout`, `anchoredNote`, ...) is the fallback, anchored
//     at the same rectangle corner.
//   - mtf.* reproducing a native chart's own Williams Fractal tier
//     structure exactly (timing AND price) is the exact open question
//     fxrscripts/mtf_structure_probe.py exists to answer, also meant to
//     be run separately. within_h4_ob is only as trustworthy as that
//     probe's own findings.
//
// Python is the source of truth throughout. If this file ever disagrees
// with swing_structure/order_blocks.py or order_block_quality.py for the
// same candles, fix the Python first, then re-port.

const FRACTAL_TIE_TOLERANCE = 4;
const DISPLACEMENT_ATR_MULTIPLE = 1.0;
const LOW_BAND_ATR_MULTIPLE = 0.5;
const HIGH_BAND_ATR_MULTIPLE = 1.0;
const CONTAINMENT_MIN_OVERLAP_FRACTION = 1.0 / 3.0;

// ---------------------------------------------------------------------
// TIMEFRAME ISOLATION. Copied verbatim (bar the two constants) from
// h1_swing_structure.py / h1_internal_structure.py / h1_fractal_structure.py,
// which is where MY_TIMEFRAME_MS's value (3600000, H1) comes from. See
// fxrscripts/README.md's "Timeframe isolation" section for the full
// rationale; the short version is FXR Script cannot read the chart's own
// timeframe, so it is inferred from the minimum positive bar-to-bar delta
// over the last 20 bars, and this script hides (and cleans up its own
// drawings) when that delta isn't 3600000.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 3600000; // H1.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;
const TF_CLEANUP_ON_MISMATCH = true;

// This script's own drawing tags (see Deviation 3 above). Three fixed
// backgroundColor values, all sharing one hue (a rose/magenta not used by
// any sibling script: structure scripts use white/red/green line colors,
// the six premium/discount scripts use orange/aqua/gold/turquoise/azure/
// coral, rectangle_lifecycle_probe.py's throwaway tag is
// rgba(255,0,221,0.13) at a different alpha again). deleteDrawingByCondition
// matches any rectangle whose backgroundColor equals one of these three.
const OB_BG_ACTIVE = color.rgba(255, 0, 140, 0.30);
const OB_BG_DIM = color.rgba(255, 0, 140, 0.10);
const FVG_BG = color.rgba(255, 0, 140, 0.18);

// Border colors carry direction/kind instead (free to vary since they are
// not part of the delete-match): green/red for bullish/bearish OBs
// (matching the existing red=bearish/green=bullish convention the
// internal/fractal structure scripts already use for their crossed
// lines), blue for FVGs of either direction (distinguished from OBs by
// shape label and lighter fill alpha alone).
const OB_BORDER_BULLISH = color.green;
const OB_BORDER_BEARISH = color.red;
const FVG_BORDER = color.blue;

let obLastSeenTime = null;
let obLastSpacing = null;

// Absolute-index OHLC/time history for the NATIVE (H1) timeframe. Index k
// into every one of these arrays refers to the same candle throughout
// this file, mirroring a Python DataFrame row number.
let nativeTime = [];
let nativeOpen = [];
let nativeHigh = [];
let nativeLow = [];
let nativeClose = [];

// One tier-state object per native tier. Bundled into objects (rather
// than loose scalars) so the shared h1ObUpdateTier helper can mutate a
// tier's fields via the object reference handed to it -- a helper cannot
// see top-level `let`s by name (fxrscripts/README.md, resolved question
// 4), but it CAN mutate fields on an object it was given, and the
// top-level `let` still holds the very same object next tick.
let nativeSwingState = null;
let nativeInternalState = null;
let nativeFractalState = null;

let orderBlocks = [];
let fvgs = [];

// mtf-bound (4H) mirror of the above, driven by mtf.* instead of native
// accessors, needed only for within_h4_ob containment.
let parentLastSeenMtfTime = null;
let parentTime = [];
let parentOpen = [];
let parentHigh = [];
let parentLow = [];
let parentClose = [];
let parentSwingState = null;
let parentInternalState = null;
let parentFractalState = null;
let parentOrderBlocks = [];

const h1ObFreshTierState = () => ({
  swingHigh: NaN,
  swingHighIndex: 0,
  highCrossed: false,
  swingLow: NaN,
  swingLowIndex: 0,
  lowCrossed: false,
  structure: null,
  highHist: [],
  lowHist: [],
  structureHist: [],
});

// NOTE: reset logic below is inlined at BOTH call sites (init() and the
// onTick timeframe-arrival branch) rather than factored into a shared
// top-level helper. A helper declared alongside onTick cannot see (read OR
// write) top-level `let`s by name (fxrscripts/README.md, resolved question
// 4 -- the same constraint h1_swing_structure.py's own comments document
// and route around, e.g. its FRACTAL_TIE_TOLERANCE-parameter anecdote). A
// standalone `h1ObResetAll` that assigned `nativeTime = []` etc. would not
// actually reach the real top-level arrays, silently leaving init()
// unfinished. Two copies is the same tradeoff the nine structure scripts
// already made for their own reset blocks.

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  input.int('H1 OB Swing Periods', 20, 'swingPeriods', 2, 100, 1, 'Williams Fractal periods for the swing tier feeding order-block leg selection. Matches h1_swing_structure.py\'s default.', 'H1 Order Block Tiers');
  input.int('H1 OB Internal Periods', 8, 'internalPeriods', 2, 100, 1, 'Williams Fractal periods for the internal tier. Matches h1_internal_structure.py\'s default.', 'H1 Order Block Tiers');
  input.int('H1 OB Fractal Periods', 2, 'fractalPeriods', 2, 100, 1, 'Williams Fractal periods for the fractal tier. Matches h1_fractal_structure.py\'s default.', 'H1 Order Block Tiers');
  input.int('H1 OB ATR Period', 14, 'atrPeriod', 2, 200, 1, 'Wilder ATR period shared by the displacement-candle scan and the zone-shaping bands, and by the 4H parent engine.', 'H1 Order Block Settings');
  input.int('H1 OB FVG Lookback', 100, 'fvgLookback', 10, 500, 1, 'Candles an FVG stays valid without a 50% fill before it expires. Matches fair_value_gaps.py\'s DEFAULT_LOOKBACK.', 'H1 Order Block Settings');
  input.bool('H1 OB Show FVGs', true, 'showFvgs');
  input.bool('H1 OB Show Mitigated (dimmed)', true, 'showMitigated');

  // Parent (4H) timeframe for within_h4_ob containment. Called once here,
  // never inside onTick, per fx-replay-docs/multi-timeframe/multi-timerame.md's
  // explicit warning. Its own tier periods (20/8/2) are hardcoded rather
  // than exposed as inputs, matching mtf_structure_probe.py's own choice
  // to hardcode periods=20 for its single probed tier, since the parent
  // engine only exists to feed containment, not to be independently tuned.
  mtf.timeframe('4h');

  nativeTime = [];
  nativeOpen = [];
  nativeHigh = [];
  nativeLow = [];
  nativeClose = [];
  nativeSwingState = h1ObFreshTierState();
  nativeInternalState = h1ObFreshTierState();
  nativeFractalState = h1ObFreshTierState();
  orderBlocks = [];
  fvgs = [];
  obLastSeenTime = null;

  parentLastSeenMtfTime = null;
  parentTime = [];
  parentOpen = [];
  parentHigh = [];
  parentLow = [];
  parentClose = [];
  parentSwingState = h1ObFreshTierState();
  parentInternalState = h1ObFreshTierState();
  parentFractalState = h1ObFreshTierState();
  parentOrderBlocks = [];
};

// ---------------------------------------------------------------------
// Williams Fractal frontier tests, ported from
// swing_structure/fractal_detector.py's _future_side_strict /
// _past_side_tolerant / _is_up_fractal / _is_down_fractal, operating on a
// plain array + absolute index instead of a DataFrame column, so they
// serve both the native and the mtf-bound engine identically.
// ---------------------------------------------------------------------

const h1ObNearFrontierStrict = (values, i, n, higher) => {
  const pivotValue = values[i];
  for (let t = 1; t <= n; t++) {
    const idx = i + t;
    if (idx >= values.length) return false;
    const v = values[idx];
    if (higher && !(v < pivotValue)) return false;
    if (!higher && !(v > pivotValue)) return false;
  }
  return true;
};

const h1ObFarFrontierTolerant = (values, i, n, higher, tieTolerance) => {
  const pivotValue = values[i];
  for (let k = 0; k <= tieTolerance; k++) {
    let tieRunOk = true;
    for (let t = 1; t <= k; t++) {
      const idx = i - t;
      if (idx < 0) { tieRunOk = false; break; }
      const v = values[idx];
      if (higher && !(v <= pivotValue)) { tieRunOk = false; break; }
      if (!higher && !(v >= pivotValue)) { tieRunOk = false; break; }
    }
    if (!tieRunOk) continue;

    let strictRunOk = true;
    for (let t = 1; t <= n; t++) {
      const idx = i - k - t;
      if (idx < 0) { strictRunOk = false; break; }
      const v = values[idx];
      if (higher && !(v < pivotValue)) { strictRunOk = false; break; }
      if (!higher && !(v > pivotValue)) { strictRunOk = false; break; }
    }
    if (strictRunOk) return true;
  }
  return false;
};

const h1ObIsUpFractal = (highs, i, n, tieTolerance) =>
  h1ObNearFrontierStrict(highs, i, n, true) && h1ObFarFrontierTolerant(highs, i, n, true, tieTolerance);

const h1ObIsDownFractal = (lows, i, n, tieTolerance) =>
  h1ObNearFrontierStrict(lows, i, n, false) && h1ObFarFrontierTolerant(lows, i, n, false, tieTolerance);

// Faithful array-index port of compute_fractal_swing_structure's per-row
// body (manual_restart and the ATR leg-size filter dropped, see
// Deviation 1). Called once per newly closed candle, idx = that candle's
// absolute index. Mutates `state` in place (see the comment on the
// tier-state objects above for why that is safe) and returns which side,
// if either, broke for real this candle -- callers use that to decide
// whether to run OB candidate detection.
const h1ObUpdateTier = (highs, lows, closes, idx, periods, tieTolerance, state, trackHist) => {
  const closeToday = closes[idx];

  let highBrokeReal = false;
  let lowBrokeReal = false;

  // A genuine cross is checked against whatever level was known BEFORE
  // today's own confirmation is applied below, same ordering
  // fractal_detector.py uses.
  if (!Number.isNaN(state.swingHigh) && !state.highCrossed && closeToday > state.swingHigh) {
    state.highCrossed = true;
    highBrokeReal = true;
  }
  if (!Number.isNaN(state.swingLow) && !state.lowCrossed && closeToday < state.swingLow) {
    state.lowCrossed = true;
    lowBrokeReal = true;
  }

  const pivotIndex = idx - periods;
  if (pivotIndex >= 0) {
    if (h1ObIsUpFractal(highs, pivotIndex, periods, tieTolerance)) {
      state.swingHigh = highs[pivotIndex];
      // Confirmation index (this tick, idx), NOT the pivot candle's own
      // index -- matches order_blocks.py's _pivot_confirmation_indices,
      // which records the row a value CHANGED on, since that confirmation
      // row is what leg_start reads.
      state.swingHighIndex = idx;
      state.highCrossed = false;
    }
    if (h1ObIsDownFractal(lows, pivotIndex, periods, tieTolerance)) {
      state.swingLow = lows[pivotIndex];
      state.swingLowIndex = idx;
      state.lowCrossed = false;
    }
  }

  // A close cannot be both above swingHigh and below swingLow at once, so
  // these stay mutually exclusive, same as market_structure.py.
  if (highBrokeReal) {
    state.structure = 'bullish';
  } else if (lowBrokeReal) {
    state.structure = 'bearish';
  }

  if (trackHist) {
    state.highHist.push(state.swingHigh);
    state.lowHist.push(state.swingLow);
    state.structureHist.push(state.structure);
  }

  return { highBrokeReal, lowBrokeReal };
};

// ---------------------------------------------------------------------
// Order block identification, ported from swing_structure/order_blocks.py.
// ---------------------------------------------------------------------

// _find_displacement_anchor.
const h1ObFindDisplacementAnchor = (highs, lows, atrSeries, legStart, breakIndex, atrMultiple) => {
  let displacementIndex = null;
  for (let k = legStart; k <= breakIndex; k++) {
    const atrK = atrSeries[k];
    if (atrK === undefined || Number.isNaN(atrK)) continue;
    if ((highs[k] - lows[k]) > atrMultiple * atrK) {
      displacementIndex = k;
      break;
    }
  }
  if (displacementIndex === null) displacementIndex = breakIndex;
  const anchor = displacementIndex - 1;
  if (anchor < 0) return null;
  return anchor;
};

// _band. lowMultiple/highMultiple passed explicitly rather than read off
// LOW_BAND_ATR_MULTIPLE/HIGH_BAND_ATR_MULTIPLE directly: this is a helper
// declared alongside onTick, which cannot see top-level `let`/`const`
// names (fxrscripts/README.md, resolved question 4 -- the same reason
// h1_swing_structure.py's frontier helpers take `tieTolerance` as a
// parameter instead of reading FRACTAL_TIE_TOLERANCE).
const h1ObBand = (rangeValue, atrValue, lowMultiple, highMultiple) => {
  if (atrValue === undefined || Number.isNaN(atrValue)) return 'medium';
  if (rangeValue < lowMultiple * atrValue) return 'low';
  if (rangeValue > highMultiple * atrValue) return 'high';
  return 'medium';
};

// _combined_range.
const h1ObCombinedRange = (indices, highs, lows) => {
  let top = -Infinity;
  let bottom = Infinity;
  for (let n = 0; n < indices.length; n++) {
    const j = indices[n];
    if (highs[j] > top) top = highs[j];
    if (lows[j] < bottom) bottom = lows[j];
  }
  return [top, bottom];
};

// _shape_zone. Returns { top, bottom, zoneEnd }. lowMultiple/highMultiple
// passed through to every h1ObBand call for the same reason h1ObBand
// itself takes them as parameters (see its own comment above).
const h1ObShapeZone = (anchor, direction, opens, highs, lows, closes, atrSeries, lowMultiple, highMultiple) => {
  const atrAtAnchor = atrSeries[anchor];
  const anchorRange = highs[anchor] - lows[anchor];
  const band = h1ObBand(anchorRange, atrAtAnchor, lowMultiple, highMultiple);

  if (band === 'medium') {
    return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
  }

  if (band === 'high') {
    if (direction === 'bullish') {
      return { top: Math.max(opens[anchor], closes[anchor]), bottom: lows[anchor], zoneEnd: anchor };
    }
    return { top: highs[anchor], bottom: Math.min(opens[anchor], closes[anchor]), zoneEnd: anchor };
  }

  // band === 'low': too small to use alone, try merging with a neighbor.
  const length = highs.length;

  let forwardResult = null;
  if (anchor + 1 < length) {
    const combined = h1ObCombinedRange([anchor, anchor + 1], highs, lows);
    const fTop = combined[0];
    const fBottom = combined[1];
    const fBand = h1ObBand(fTop - fBottom, atrAtAnchor, lowMultiple, highMultiple);
    if (fBand === 'medium') return { top: fTop, bottom: fBottom, zoneEnd: anchor + 1 };
    forwardResult = { top: fTop, bottom: fBottom, band: fBand };
  }

  let backwardResult = null;
  if (anchor - 1 >= 0) {
    const combined = h1ObCombinedRange([anchor - 1, anchor], highs, lows);
    const bTop = combined[0];
    const bBottom = combined[1];
    const bBand = h1ObBand(bTop - bBottom, atrAtAnchor, lowMultiple, highMultiple);
    if (bBand === 'medium') return { top: bTop, bottom: bBottom, zoneEnd: anchor };
    backwardResult = { top: bTop, bottom: bBottom, band: bBand };
  }

  const bothHigh = forwardResult !== null && forwardResult.band === 'high' &&
    backwardResult !== null && backwardResult.band === 'high';
  if (bothHigh) {
    // Adding a third candle can only keep the range the same size or grow
    // it, never shrink it back down, so it can't help here.
    return { top: forwardResult.top, bottom: forwardResult.bottom, zoneEnd: anchor + 1 };
  }

  if (anchor - 1 >= 0 && anchor + 1 < length) {
    const combined = h1ObCombinedRange([anchor - 1, anchor, anchor + 1], highs, lows);
    return { top: combined[0], bottom: combined[1], zoneEnd: anchor + 1 };
  }

  if (forwardResult !== null) return { top: forwardResult.top, bottom: forwardResult.bottom, zoneEnd: anchor + 1 };
  if (backwardResult !== null) return { top: backwardResult.top, bottom: backwardResult.bottom, zoneEnd: anchor };
  return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
};

// Per-(tier, break event) candidate handling: find the anchor, then either
// merge into an existing (direction, anchor) OB or shape and push a new
// one. Different anchors are NEVER merged even if their zones overlap in
// price, per order_blocks.py's own docstring -- the equality key here is
// exactly (direction, anchor), same as its groups dict.
// displacementAtrMultiple/lowBandMultiple/highBandMultiple are explicit
// parameters, not direct reads of DISPLACEMENT_ATR_MULTIPLE/
// LOW_BAND_ATR_MULTIPLE/HIGH_BAND_ATR_MULTIPLE: this is a helper declared
// alongside onTick, which cannot see top-level `let`/`const` names (see
// h1ObBand's comment above for the full rationale).
const h1ObTryCreateCandidate = (highs, lows, opens, closes, atrSeries, direction, legStart, breakIndex, obsArr, displacementAtrMultiple, lowBandMultiple, highBandMultiple) => {
  const anchor = h1ObFindDisplacementAnchor(highs, lows, atrSeries, legStart, breakIndex, displacementAtrMultiple);
  if (anchor === null) return;

  for (let n = 0; n < obsArr.length; n++) {
    const existing = obsArr[n];
    if (existing.direction === direction && existing.formedIndex === anchor) {
      if (breakIndex < existing.earliestTriggerIndex) existing.earliestTriggerIndex = breakIndex;
      if (legStart < existing.legStartIndex) existing.legStartIndex = legStart;
      return;
    }
  }

  const shaped = h1ObShapeZone(anchor, direction, opens, highs, lows, closes, atrSeries, lowBandMultiple, highBandMultiple);
  obsArr.push({
    direction,
    formedIndex: anchor,
    top: shaped.top,
    bottom: shaped.bottom,
    zoneEndIndex: shaped.zoneEnd,
    legStartIndex: legStart,
    earliestTriggerIndex: breakIndex,
    // null = "not yet knowable" (needs zoneEnd+1 / zoneEnd+2 to exist),
    // matching order_blocks.py computing these once full future data is
    // available; here they resolve the first tick that data exists.
    causedDisplacement: null,
    causedImbalance: null,
    mitigated: false,
    mitigatedIndex: -1,
    invalidated: false,
    invalidatedIndex: -1,
    sweptSwing: false,
    sweptInternal: false,
    sweptFractal: false,
    sweptFvg: false,
    sweptPrevCandle: false,
    hasInducement: false,
    isFlipZone: false,
    flippedObIndex: -1,
    withinH4Ob: false,
  });
};

// Resolves causedDisplacement/causedImbalance once the candles they need
// (zoneEnd+1, zoneEnd+2) exist. Ported from the tail end of
// compute_order_blocks, checked against zone_end (the rightmost candle
// _shape_zone actually folded in), not always the raw anchor.
const h1ObUpdateDisplacementImbalance = (ob, highs, lows, closes) => {
  const length = highs.length;
  if (ob.causedDisplacement === null && ob.zoneEndIndex + 1 < length) {
    if (ob.direction === 'bullish') {
      ob.causedDisplacement = closes[ob.zoneEndIndex + 1] > highs[ob.zoneEndIndex];
    } else {
      ob.causedDisplacement = closes[ob.zoneEndIndex + 1] < lows[ob.zoneEndIndex];
    }
  }
  if (ob.causedImbalance === null && ob.zoneEndIndex + 2 < length) {
    if (ob.direction === 'bullish') {
      ob.causedImbalance = lows[ob.zoneEndIndex + 2] > highs[ob.zoneEndIndex];
    } else {
      ob.causedImbalance = highs[ob.zoneEndIndex + 2] < lows[ob.zoneEndIndex];
    }
  }
};

// _apply_mitigation + _apply_invalidation, run incrementally: called once
// per new candle (idx) per OB, checking only that one new candle against
// the OB's zone, first-time-only, guarded by earliest_trigger_index so
// the impulse leg that formed the OB is never mistaken for mitigating it.
const h1ObUpdateMitigationInvalidation = (ob, highs, lows, closes, idx) => {
  if (idx <= ob.earliestTriggerIndex) return;
  if (!ob.mitigated) {
    if (lows[idx] <= ob.top && highs[idx] >= ob.bottom) {
      ob.mitigated = true;
      ob.mitigatedIndex = idx;
    }
  }
  if (!ob.invalidated) {
    const brokeThrough = ob.direction === 'bullish' ? closes[idx] < ob.bottom : closes[idx] > ob.top;
    if (brokeThrough) {
      ob.invalidated = true;
      ob.invalidatedIndex = idx;
    }
  }
};

// ---------------------------------------------------------------------
// FVGs, ported from swing_structure/fair_value_gaps.py.
// ---------------------------------------------------------------------

// Only the newest triple (idx-2, idx-1, idx) can newly form an FVG each
// tick; every older triple was already checked on its own tick.
const h1ObDetectNewFvg = (highs, lows, idx, lookback, fvgsArr) => {
  if (idx < 2) return;
  const i = idx - 2;
  if (lows[idx] > highs[i]) {
    fvgsArr.push({
      direction: 'bullish',
      formedIndex: idx,
      top: lows[idx],
      bottom: highs[i],
      filled: false,
      filledIndex: -1,
      expiryIndex: idx + lookback,
    });
  }
  if (highs[idx] < lows[i]) {
    fvgsArr.push({
      direction: 'bearish',
      formedIndex: idx,
      top: lows[i],
      bottom: highs[idx],
      filled: false,
      filledIndex: -1,
      expiryIndex: idx + lookback,
    });
  }
};

// 50%-of-range wick fill, checked every tick against every still-open
// FVG. Expiry is implicit: once idx passes expiryIndex without filling,
// h1ObFvgActiveUntil (below) freezes at expiryIndex and the FVG is simply
// never matched again by any factor or drawing check.
const h1ObUpdateFvgFill = (fvg, highs, lows, idx) => {
  if (fvg.filled) return;
  if (idx <= fvg.formedIndex || idx > fvg.expiryIndex) return;
  const midpoint = (fvg.top + fvg.bottom) / 2.0;
  const reachedMidpoint = fvg.direction === 'bullish' ? lows[idx] <= midpoint : highs[idx] >= midpoint;
  if (reachedMidpoint) {
    fvg.filled = true;
    fvg.filledIndex = idx;
  }
};

// active_until_index: filled_index if filled, else expiry_index.
const h1ObFvgActiveUntil = (fvg) => (fvg.filled ? fvg.filledIndex : fvg.expiryIndex);

// ---------------------------------------------------------------------
// OB quality factors, ported from swing_structure/order_block_quality.py.
// Each takes the order_blocks array (mutated in place) plus whatever else
// its Python original needed.
// ---------------------------------------------------------------------

// compute_swept_liquidity_structural (swing/internal/fractal in one pass,
// since all three read the same per-OB leg window).
const h1ObComputeSweptStructural = (obs, highs, lows, tierHists) => {
  const tierNames = ['swing', 'internal', 'fractal'];
  for (let n = 0; n < obs.length; n++) {
    const ob = obs[n];
    for (let t = 0; t < tierNames.length; t++) {
      const tierName = tierNames[t];
      const hist = tierHists[tierName];
      let swept = false;
      for (let k = ob.legStartIndex; k <= ob.earliestTriggerIndex; k++) {
        if (ob.direction === 'bullish') {
          if (hist.structureHist[k] === 'bullish' && lows[k] < hist.lowHist[k]) { swept = true; break; }
        } else {
          if (hist.structureHist[k] === 'bearish' && highs[k] > hist.highHist[k]) { swept = true; break; }
        }
      }
      if (tierName === 'swing') ob.sweptSwing = swept;
      else if (tierName === 'internal') ob.sweptInternal = swept;
      else ob.sweptFractal = swept;
    }
  }
};

// compute_fvg_confluence.
const h1ObComputeSweptFvg = (obs, fvgsArr, closes) => {
  for (let n = 0; n < obs.length; n++) {
    const ob = obs[n];
    let matched = false;
    for (let f = 0; f < fvgsArr.length; f++) {
      const fvg = fvgsArr[f];
      if (fvg.direction !== ob.direction) continue;
      const activeUntil = h1ObFvgActiveUntil(fvg);
      if (!(fvg.formedIndex < ob.formedIndex && ob.formedIndex <= activeUntil)) continue;

      const overlaps = ob.bottom <= fvg.top && ob.top >= fvg.bottom;
      if (!overlaps) continue;

      let farEdgeBreached = false;
      for (let j = ob.formedIndex; j <= ob.zoneEndIndex; j++) {
        farEdgeBreached = ob.direction === 'bullish' ? closes[j] < fvg.bottom : closes[j] > fvg.top;
        if (farEdgeBreached) break;
      }
      if (!farEdgeBreached) { matched = true; break; }
    }
    ob.sweptFvg = matched;
  }
};

// compute_previous_candle_sweep.
const h1ObComputeSweptPrevCandle = (obs, highs, lows, closes) => {
  for (let n = 0; n < obs.length; n++) {
    const ob = obs[n];
    const isSingleCandle = ob.zoneEndIndex === ob.formedIndex;
    if (!isSingleCandle || ob.formedIndex === 0) {
      ob.sweptPrevCandle = false;
      continue;
    }
    const a = ob.formedIndex;
    if (ob.direction === 'bullish') {
      ob.sweptPrevCandle = lows[a] < lows[a - 1] && closes[a] >= lows[a - 1];
    } else {
      ob.sweptPrevCandle = highs[a] > highs[a - 1] && closes[a] <= highs[a - 1];
    }
  }
};

// compute_inducement.
const h1ObComputeInducement = (obs) => {
  const n = obs.length;
  for (let x = 0; x < n; x++) {
    const ox = obs[x];
    const heightX = ox.top - ox.bottom;
    let has = false;
    for (let y = 0; y < n; y++) {
      if (x === y) continue;
      const oy = obs[y];
      if (oy.invalidated) continue;
      if (oy.direction !== ox.direction) continue;
      if (oy.formedIndex >= ox.formedIndex) continue;

      const heightY = oy.top - oy.bottom;
      let nearer;
      let gap;
      if (ox.direction === 'bullish') {
        nearer = oy.bottom > ox.top;
        gap = oy.bottom - ox.top;
      } else {
        nearer = oy.top < ox.bottom;
        gap = ox.bottom - oy.top;
      }
      if (nearer && gap < Math.max(heightX, heightY)) { has = true; break; }
    }
    ox.hasInducement = has;
  }
};

// compute_flip_zone.
const h1ObComputeFlipZone = (obs) => {
  const n = obs.length;
  for (let z = 0; z < n; z++) {
    const oz = obs[z];
    oz.isFlipZone = false;
    oz.flippedObIndex = -1;
    for (let w = 0; w < n; w++) {
      if (z === w) continue;
      const ow = obs[w];
      if (ow.direction === oz.direction) continue;
      if (ow.formedIndex >= oz.formedIndex) continue;
      if (!ow.mitigated) continue;

      const reactedOnW = oz.formedIndex <= ow.mitigatedIndex && ow.mitigatedIndex <= oz.zoneEndIndex;
      if (!reactedOnW) continue;
      if (!ow.invalidated) continue;
      if (ow.invalidatedIndex < oz.earliestTriggerIndex) continue;

      oz.isFlipZone = true;
      oz.flippedObIndex = w;
      break;
    }
  }
};

// compute_containment. Cross-timeframe: child (native H1) vs parent
// (mtf 4H), compared by price only. minOverlapFraction is an explicit
// parameter rather than a direct read of CONTAINMENT_MIN_OVERLAP_FRACTION:
// this is a helper declared alongside onTick, which cannot see top-level
// `let`/`const` names (see h1ObBand's comment above for the full
// rationale).
const h1ObComputeContainment = (children, parents, minOverlapFraction) => {
  for (let c = 0; c < children.length; c++) {
    const child = children[c];
    let matched = false;
    for (let p = 0; p < parents.length; p++) {
      const parent = parents[p];
      if (parent.invalidated || parent.direction !== child.direction) continue;

      const overlap = Math.min(child.top, parent.top) - Math.max(child.bottom, parent.bottom);
      if (overlap <= 0) continue;

      const fullyEngulfed = child.top <= parent.top && child.bottom >= parent.bottom;
      const parentHeight = parent.top - parent.bottom;
      if (fullyEngulfed || (overlap / parentHeight) >= minOverlapFraction) {
        matched = true;
        break;
      }
    }
    child.withinH4Ob = matched;
  }
};

// ---------------------------------------------------------------------
// Drawing. UNVERIFIED overall (see header) -- best-effort translation of
// the confirmed-working trendLine delete-then-redraw pattern onto
// rectangle().
// ---------------------------------------------------------------------

const h1ObAbbreviations = (ob) => {
  const tags = [];
  if (ob.sweptSwing) tags.push('SWG');
  if (ob.sweptInternal) tags.push('ITL');
  if (ob.sweptFractal) tags.push('FRC');
  if (ob.sweptFvg) tags.push('FVG');
  if (ob.sweptPrevCandle) tags.push('PRV');
  if (ob.hasInducement) tags.push('IND');
  if (ob.isFlipZone) tags.push('FLP');
  if (ob.causedDisplacement) tags.push('DIS');
  if (ob.causedImbalance) tags.push('IMB');
  if (ob.withinH4Ob) tags.push('H4');
  return tags.join(' ');
};

// Colors passed explicitly rather than read off OB_BG_ACTIVE/OB_BG_DIM/
// FVG_BG/OB_BORDER_BULLISH/OB_BORDER_BEARISH/FVG_BORDER directly: this is
// a helper declared alongside onTick, which cannot see top-level
// `let`/`const` names (see h1ObBand's comment above for the full
// rationale).
const h1ObRedraw = (obs, fvgsArr, times, currentTime, showFvgs, showMitigated, bgActive, bgDim, fvgBg, borderBullish, borderBearish, fvgBorder) => {
  // Delete only this script's own rectangles, matched on the fixed
  // backgroundColor tag set (Deviation 3). Bracket access, per the
  // established gotcha (fxrscripts/README.md's "Self-only drawing
  // cleanup", rectangle_lifecycle_probe.py): overrideOptions is a union
  // type across every drawing tool, dotted access is a type error.
  deleteDrawingByCondition((drawing) => {
    const opts = drawing.overrideOptions;
    if (!opts) return false;
    const bg = opts['backgroundColor'];
    return bg === bgActive || bg === bgDim || bg === fvgBg;
  });

  for (let n = 0; n < obs.length; n++) {
    const ob = obs[n];
    // Skip invalidated OBs entirely, per the shared spec.
    if (ob.invalidated) continue;
    if (ob.mitigated && !showMitigated) continue;

    const border = ob.direction === 'bullish' ? borderBullish : borderBearish;
    const bg = ob.mitigated ? bgDim : bgActive;
    const formedTime = times[ob.formedIndex];
    const label = 'OB ' + (ob.direction === 'bullish' ? 'BUY' : 'SELL') + ' ' + h1ObAbbreviations(ob);

    rectangle(
      formedTime, ob.top,
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
    for (let n = 0; n < fvgsArr.length; n++) {
      const fvg = fvgsArr[n];
      if (fvg.filled) continue;
      const formedTime = times[fvg.formedIndex];
      rectangle(
        formedTime, fvg.top,
        currentTime, fvg.bottom,
        {
          backgroundColor: fvgBg,
          color: fvgBorder,
          fillBackground: true,
          showLabel: true,
          textColor: fvgBorder,
          bold: false,
        },
        'FVG'
      );
    }
  }
};

onTick = (length, _moment, _, ta, inputs) => {
  const swingPeriods = inputs.swingPeriods;
  const internalPeriods = inputs.internalPeriods;
  const fractalPeriods = inputs.fractalPeriods;
  const atrPeriod = inputs.atrPeriod;
  const fvgLookback = inputs.fvgLookback;
  const showFvgs = inputs.showFvgs;
  const showMitigated = inputs.showMitigated;

  // ---- Timeframe gate. MUST come before the new-bar gate below. Inlined
  // rather than factored into a helper, same reason as every sibling
  // script (fxrscripts/README.md, resolved question 4). ----
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
        return bg === OB_BG_ACTIVE || bg === OB_BG_DIM || bg === FVG_BG;
      });
    }
    obLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== obLastSpacing) {
    // Arrived on H1 from somewhere else. Reset everything so the state
    // machine does not resume on top of another timeframe's candles.
    // Inlined (not a shared helper call) for the same reason as init()'s
    // copy of this block -- see the note above h1ObFreshTierState.
    nativeTime = [];
    nativeOpen = [];
    nativeHigh = [];
    nativeLow = [];
    nativeClose = [];
    nativeSwingState = h1ObFreshTierState();
    nativeInternalState = h1ObFreshTierState();
    nativeFractalState = h1ObFreshTierState();
    orderBlocks = [];
    fvgs = [];
    obLastSeenTime = null;

    parentLastSeenMtfTime = null;
    parentTime = [];
    parentOpen = [];
    parentHigh = [];
    parentLow = [];
    parentClose = [];
    parentSwingState = h1ObFreshTierState();
    parentInternalState = h1ObFreshTierState();
    parentFractalState = h1ObFreshTierState();
    parentOrderBlocks = [];

    obLastSpacing = inferredSpacing;
  }

  // ---- Native (H1) new-bar gate. ----
  const currentTime = time(0);
  if (currentTime === obLastSeenTime) {
    return;
  }
  obLastSeenTime = currentTime;

  nativeTime.push(currentTime);
  nativeOpen.push(openC(0));
  nativeHigh.push(high(0));
  nativeLow.push(low(0));
  nativeClose.push(closeC(0));
  const idx = nativeTime.length - 1;

  // Full ATR series over the accumulated native history, recomputed every
  // tick (see Deviation 2). Aligned index-for-index with nativeHigh/etc.
  const atrSeries = ta.atr(nativeHigh, nativeLow, nativeClose, atrPeriod);

  const prevSwingHighIdx = nativeSwingState.swingHighIndex;
  const prevSwingLowIdx = nativeSwingState.swingLowIndex;
  const prevInternalHighIdx = nativeInternalState.swingHighIndex;
  const prevInternalLowIdx = nativeInternalState.swingLowIndex;
  const prevFractalHighIdx = nativeFractalState.swingHighIndex;
  const prevFractalLowIdx = nativeFractalState.swingLowIndex;

  const swingResult = h1ObUpdateTier(nativeHigh, nativeLow, nativeClose, idx, swingPeriods, FRACTAL_TIE_TOLERANCE, nativeSwingState, true);
  const internalResult = h1ObUpdateTier(nativeHigh, nativeLow, nativeClose, idx, internalPeriods, FRACTAL_TIE_TOLERANCE, nativeInternalState, true);
  const fractalResult = h1ObUpdateTier(nativeHigh, nativeLow, nativeClose, idx, fractalPeriods, FRACTAL_TIE_TOLERANCE, nativeFractalState, true);

  if (swingResult.highBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bullish', prevSwingHighIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
  if (swingResult.lowBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bearish', prevSwingLowIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
  if (internalResult.highBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bullish', prevInternalHighIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
  if (internalResult.lowBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bearish', prevInternalLowIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
  if (fractalResult.highBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bullish', prevFractalHighIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
  if (fractalResult.lowBrokeReal) h1ObTryCreateCandidate(nativeHigh, nativeLow, nativeOpen, nativeClose, atrSeries, 'bearish', prevFractalLowIdx, idx, orderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);

  for (let n = 0; n < orderBlocks.length; n++) {
    const ob = orderBlocks[n];
    h1ObUpdateDisplacementImbalance(ob, nativeHigh, nativeLow, nativeClose);
    h1ObUpdateMitigationInvalidation(ob, nativeHigh, nativeLow, nativeClose, idx);
  }

  h1ObDetectNewFvg(nativeHigh, nativeLow, idx, fvgLookback, fvgs);
  for (let n = 0; n < fvgs.length; n++) {
    h1ObUpdateFvgFill(fvgs[n], nativeHigh, nativeLow, idx);
  }

  // ---- mtf-bound (4H) parent engine, advanced only on a genuine new 4H
  // close (mtf.time(0,false) is STEPPED, per
  // fx-replay-docs/multi-timeframe/multi-timerame.md, so it holds steady
  // across every H1 tick within a still-forming 4H candle). Mirrors
  // mtf_structure_probe.py's own new-mtf-bar gate. ----
  const currentMtfTime = mtf.time(0, false);
  if (currentMtfTime !== parentLastSeenMtfTime) {
    parentLastSeenMtfTime = currentMtfTime;

    parentTime.push(currentMtfTime);
    parentOpen.push(mtf.openC(0, false));
    parentHigh.push(mtf.high(0, false));
    parentLow.push(mtf.low(0, false));
    parentClose.push(mtf.closeC(0, false));
    const pIdx = parentTime.length - 1;

    const parentAtrSeries = ta.atr(parentHigh, parentLow, parentClose, atrPeriod);

    const prevParentSwingHighIdx = parentSwingState.swingHighIndex;
    const prevParentSwingLowIdx = parentSwingState.swingLowIndex;
    const prevParentInternalHighIdx = parentInternalState.swingHighIndex;
    const prevParentInternalLowIdx = parentInternalState.swingLowIndex;
    const prevParentFractalHighIdx = parentFractalState.swingHighIndex;
    const prevParentFractalLowIdx = parentFractalState.swingLowIndex;

    // Same three periods (20/8/2) as the native engine, hardcoded per the
    // init() comment above.
    const pSwingResult = h1ObUpdateTier(parentHigh, parentLow, parentClose, pIdx, 20, FRACTAL_TIE_TOLERANCE, parentSwingState, false);
    const pInternalResult = h1ObUpdateTier(parentHigh, parentLow, parentClose, pIdx, 8, FRACTAL_TIE_TOLERANCE, parentInternalState, false);
    const pFractalResult = h1ObUpdateTier(parentHigh, parentLow, parentClose, pIdx, 2, FRACTAL_TIE_TOLERANCE, parentFractalState, false);

    if (pSwingResult.highBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bullish', prevParentSwingHighIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
    if (pSwingResult.lowBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bearish', prevParentSwingLowIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
    if (pInternalResult.highBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bullish', prevParentInternalHighIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
    if (pInternalResult.lowBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bearish', prevParentInternalLowIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
    if (pFractalResult.highBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bullish', prevParentFractalHighIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
    if (pFractalResult.lowBrokeReal) h1ObTryCreateCandidate(parentHigh, parentLow, parentOpen, parentClose, parentAtrSeries, 'bearish', prevParentFractalLowIdx, pIdx, parentOrderBlocks, DISPLACEMENT_ATR_MULTIPLE, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);

    for (let n = 0; n < parentOrderBlocks.length; n++) {
      const ob = parentOrderBlocks[n];
      h1ObUpdateDisplacementImbalance(ob, parentHigh, parentLow, parentClose);
      h1ObUpdateMitigationInvalidation(ob, parentHigh, parentLow, parentClose, pIdx);
    }
  }

  // ---- Quality factor recompute, every native tick, over the current
  // orderBlocks/fvgs/parentOrderBlocks snapshot. ----
  h1ObComputeSweptStructural(orderBlocks, nativeHigh, nativeLow, {
    swing: nativeSwingState,
    internal: nativeInternalState,
    fractal: nativeFractalState,
  });
  h1ObComputeSweptFvg(orderBlocks, fvgs, nativeClose);
  h1ObComputeSweptPrevCandle(orderBlocks, nativeHigh, nativeLow, nativeClose);
  h1ObComputeInducement(orderBlocks);
  h1ObComputeFlipZone(orderBlocks);
  h1ObComputeContainment(orderBlocks, parentOrderBlocks, CONTAINMENT_MIN_OVERLAP_FRACTION);

  h1ObRedraw(orderBlocks, fvgs, nativeTime, currentTime, showFvgs, showMitigated, OB_BG_ACTIVE, OB_BG_DIM, FVG_BG, OB_BORDER_BULLISH, OB_BORDER_BEARISH, FVG_BORDER);
};
