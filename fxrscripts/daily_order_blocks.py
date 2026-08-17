// FXR Script port of the Daily order-block (OB) + fair-value-gap (FVG)
// detection and quality-factor system, for the Daily timeframe.
//
// Ground truth, all in Python, read in full before writing this file:
//   swing_structure/atr.py               (Wilder's ATR -- NOT reimplemented
//                                          here, see "ta.atr" note below)
//   swing_structure/fractal_detector.py  (Williams Fractal, the mechanism
//                                          behind all three Daily tiers)
//   swing_structure/market_structure.py  (bullish/bearish structure from
//                                          genuine breaks only)
//   swing_structure/order_blocks.py      (OB identification: anchor
//                                          selection via ATR-displacement
//                                          leg scan, zone shaping, caused_
//                                          displacement/imbalance, wick
//                                          mitigation, close-through
//                                          invalidation)
//   swing_structure/fair_value_gaps.py   (FVG: 3-candle gap test, 50% wick
//                                          fill or lookback expiry)
//   swing_structure/order_block_quality.py (the 6 factor functions)
//
// Tier engines: this script's own three Williams Fractal engines (swing
// n=20, internal n=8, fractal n=2) are copied from, and must be kept in
// sync with, fxrscripts/daily_swing_structure.py, daily_internal_
// structure.py, and daily_fractal_structure.py -- same frontier tests,
// same FRACTAL_TIE_TOLERANCE=4, same MY_TIMEFRAME_MS=86400000. DELIBERATE
// SIMPLIFICATIONS vs those three sibling files, since order_blocks.py's
// algorithm needs none of it: no manual-restart input, no ATR-leg-
// separation filter, and no trendLine drawing for the tiers themselves
// (this script draws OB/FVG rectangles only; load the three sibling
// scripts alongside this one if the tier levels themselves need to be
// seen). If this ever needs the omitted features, port them back in from
// the three sibling files the same way the core frontier tests were.
//
// ARCHITECTURE, since order_blocks.py's algorithm is written as a batch
// function over a whole DataFrame, not a streaming state machine: this
// script keeps a full chronological history of every closed Daily candle
// (dailyObHist*) plus, for each tier, a full per-candle history of its
// structure/swing_high/swing_low (mirroring fractal_detector.py's own
// per-row output columns), and recomputes the 6 quality factors fully
// over the in-memory OB/FVG lists every closed candle (an in-place
// equivalent of calling order_block_quality.py's compute_* functions
// fresh each time). OB creation, caused_displacement/imbalance, wick
// mitigation, close-through invalidation, and FVG fill/expiry are each
// done incrementally (one new closed candle at a time) rather than
// rescanning all of history every tick, since each of those is a strictly
// forward-scanning test in the Python source; this is mathematically
// equivalent to that forward scan, just spread one candle per tick.
//
// Helper functions cannot see top-level let/const (fxrscripts/README.md,
// resolved question 4), and -- to stay safe against the untested question
// of whether that restriction also blocks one top-level const function
// from calling ANOTHER top-level const function by name -- every helper
// below is fully self-contained: any sub-helper it needs (near/far
// frontier tests, band classification, combined-range) is nested INSIDE
// it as a local closure, and any constant it needs (ATR multiples, tier
// order) is passed in as an explicit parameter, never read by name from
// outside. Only onTick itself (which CAN see top-level state) references
// the top-level consts directly.
//
// UNVERIFIED ASSUMPTIONS, flagged here and again at point of use:
//   1. ta.atr(high[], low[], close[], window)'s exact smoothing formula
//      is undocumented beyond "average true range" (fx-replay-docs/
//      external-libraries/ta-math-library.md just lists the signature).
//      The task instructions say to prefer it over reimplementing Wilder's
//      recursion by hand, so it is used as-is; if it turns out to use a
//      plain rolling mean instead of Wilder's smoothing, the displacement-
//      candle scan and zone-shaping bands will diverge slightly from the
//      Python reference (swing_structure/atr.py).
//   2. rectangle() + deleteDrawingByCondition, at OB/FVG volumes, has
//      NEVER been confirmed working in this codebase --
//      fxrscripts/rectangle_lifecycle_probe.py exists specifically to
//      answer this and its findings were never recorded ("not yet
//      recorded" in fxrscripts/README.md as of this port). This script
//      follows the same content-matched delete-then-redraw pattern
//      confirmed for trendLine/horizontal_ray (Design 3), tagged on
//      backgroundColor via bracket access, as its best-effort translation.
//   3. rectangle()'s text/label argument: chart-drawing-tools.md's own
//      quick example never shows rectangle with a text argument (only
//      horizontalLine's 4-arg form, itself later found wrong for that
//      function). This script mirrors rectangle_lifecycle_probe.py's own
//      guess -- showLabel:true in the options object, plus a 6th
//      positional text string -- which that probe's own findings were
//      also never confirmed.
//   4. FVG expiry_index is NOT capped to "length-1" the way fair_value_
//      gaps.py caps it against a fixed, already-complete DataFrame: in a
//      live/streaming chart there is no fixed final candle, so
//      expiryIndex is simply formedIndex+lookback, uncapped. This only
//      differs from the Python behavior at the very end of a finite
//      backtest replay, where it has no practical effect (those future
//      candles simply do not exist yet, and the checks resume normally
//      once they do).

const FRACTAL_TIE_TOLERANCE = 4;
const DISPLACEMENT_ATR_MULTIPLE = 1.0;
const LOW_BAND_ATR_MULTIPLE = 0.5;
const HIGH_BAND_ATR_MULTIPLE = 1.0;

// ---------------------------------------------------------------------
// TIMEFRAME ISOLATION. Same pattern as the nine structure scripts and six
// premium/discount scripts (fxrscripts/README.md's "Timeframe isolation"
// section) -- FXR Script cannot read the chart's own timeframe, so it is
// inferred from bar spacing and the script hides itself (and cleans up
// its own drawings) when the chart is not on Daily.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 86400000; // Daily, same value daily_swing_structure.py uses.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;
const TF_CLEANUP_ON_MISMATCH = true;

// Reserved OB/FVG rectangle tag colors, read back via bracket access on
// overrideOptions (dotted access is a type error on the DrawingOverrides
// union, see fxrscripts/README.md Design 3). Checked against every color
// already reserved elsewhere in fxrscripts/ (structure lines: white/red/
// green; EQ lines: orange, cyan, coral rgba(255,140,105), azure
// rgba(0,128,255), gold rgba(255,215,0), turquoise rgba(0,255,200); the
// rectangle probe's magenta rgba(255,0,221,0.13)) -- all six of these are
// distinct rgba tuples from every one of those.
const OB_BULLISH_COLOR = color.rgba(50, 205, 50, 0.35); // lime green
const OB_BULLISH_DIM_COLOR = color.rgba(50, 205, 50, 0.12); // mitigated: dimmed
const OB_BEARISH_COLOR = color.rgba(220, 20, 60, 0.35); // crimson
const OB_BEARISH_DIM_COLOR = color.rgba(220, 20, 60, 0.12); // mitigated: dimmed
const FVG_BULLISH_COLOR = color.rgba(30, 144, 255, 0.20); // dodger blue
const FVG_BEARISH_COLOR = color.rgba(255, 140, 0, 0.20); // dark orange
const OB_TAG_COLORS = [
  OB_BULLISH_COLOR, OB_BULLISH_DIM_COLOR,
  OB_BEARISH_COLOR, OB_BEARISH_DIM_COLOR,
  FVG_BULLISH_COLOR, FVG_BEARISH_COLOR,
];

// ---- Full chronological history of every closed Daily candle this
// script has seen (index 0 = oldest). Everything below indexes into these
// by position rather than by the built-in high(i)/low(i)/... offset
// convention, since the algorithm needs arbitrary PAST indices, not just
// recent lookback. ----
const dailyObHistTime = [];
const dailyObHistOpen = [];
const dailyObHistHigh = [];
const dailyObHistLow = [];
const dailyObHistClose = [];

// ---- Per-tier engine state (mutated in place by updateFractalTier).
// highConfirmedIndex/lowConfirmedIndex mirror order_blocks.py's
// _pivot_confirmation_indices: the history-array index where the
// CURRENTLY active swing_high/swing_low was last confirmed, tracked
// online instead of via a separate forward pass. ----
let dailyObSwingTier = { swingHigh: NaN, highCrossed: false, swingLow: NaN, lowCrossed: false, structure: null, highConfirmedIndex: 0, lowConfirmedIndex: 0 };
let dailyObInternalTier = { swingHigh: NaN, highCrossed: false, swingLow: NaN, lowCrossed: false, structure: null, highConfirmedIndex: 0, lowConfirmedIndex: 0 };
let dailyObFractalTier = { swingHigh: NaN, highCrossed: false, swingLow: NaN, lowCrossed: false, structure: null, highConfirmedIndex: 0, lowConfirmedIndex: 0 };

// ---- Per-tier per-candle history, mirroring fractal_detector.py's own
// swing_high_col/swing_low_col and market_structure.py's structure_col.
// Needed by compute_swept_liquidity_structural, which scans arbitrary
// past windows [leg_start_index, earliest_trigger_index]. ----
const dailyObSwingStructureArr = [];
const dailyObSwingHighArr = [];
const dailyObSwingLowArr = [];
const dailyObInternalStructureArr = [];
const dailyObInternalHighArr = [];
const dailyObInternalLowArr = [];
const dailyObFractalStructureArr = [];
const dailyObFractalHighArr = [];
const dailyObFractalLowArr = [];

// ---- OB and FVG tables. Plain arrays of plain objects, one row per OB /
// per FVG, the same "long format, not per-candle columns" shape
// order_blocks.py's and fair_value_gaps.py's own DataFrames use, since
// several OBs/FVGs are simultaneously live. ----
const dailyOrderBlocks = [];
const dailyFvgs = [];

let dailyObLastSpacing = null;
let dailyObLastSeenTime = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  input.int('Daily OB Swing Periods', 20, 'swingPeriods', 2, 100, 1, 'Williams Fractal periods for the swing tier. Copied from daily_swing_structure.py; keep them equal unless a deliberate divergence is wanted.', 'Daily Order Block Settings');
  input.int('Daily OB Internal Periods', 8, 'internalPeriods', 2, 100, 1, 'Williams Fractal periods for the internal tier. Copied from daily_internal_structure.py.', 'Daily Order Block Settings');
  input.int('Daily OB Fractal Periods', 2, 'fractalPeriods', 2, 100, 1, 'Williams Fractal periods for the fractal tier. Copied from daily_fractal_structure.py.', 'Daily Order Block Settings');
  input.int('Daily OB ATR Period', 14, 'atrPeriod', 2, 200, 1, 'True Range period feeding both the displacement-candle scan and the zone-shaping bands. Mirrors swing_structure/order_blocks.py ATR_PERIOD.', 'Daily Order Block Settings');
  input.int('Daily OB FVG Lookback', 100, 'fvgLookback', 5, 500, 5, 'Candles an FVG stays valid without a 50% fill before it expires. Mirrors swing_structure/fair_value_gaps.py DEFAULT_LOOKBACK.', 'Daily Order Block Settings');
  input.bool('Daily OB Show Mitigated', true, 'showMitigated');
  input.bool('Daily OB Show FVGs', true, 'showFvgs');
};

// ---- Williams Fractal tier engine, self-contained (near/far frontier
// tests nested as local closures rather than separate top-level helpers,
// so this never has to call another top-level const by name). Copied from
// the three Daily structure scripts' own frontier tests, stripped of
// manual restart / ATR-separation / trendLine drawing (order_blocks.py's
// algorithm needs none of those). Mutates `state` in place; returns which
// side broke this candle plus the OLD (pre-update) confirmed-index for
// each side, i.e. order_blocks.py's leg_start for a break on this candle.
const updateFractalTier = (state, periods, tieTolerance, curIdx, closeToday) => {
  const nearFrontierStrict = (pivotValue, higher, getValue) => {
    for (let t = 1; t <= periods; t++) {
      const v = getValue(periods - t);
      if (Number.isNaN(v)) return false;
      if (higher && !(v < pivotValue)) return false;
      if (!higher && !(v > pivotValue)) return false;
    }
    return true;
  };

  const farFrontierTolerant = (pivotValue, higher, getValue) => {
    for (let k = 0; k <= tieTolerance; k++) {
      let tieRunOk = true;
      for (let t = 1; t <= k; t++) {
        const v = getValue(periods + t);
        if (Number.isNaN(v)) { tieRunOk = false; break; }
        if (higher && !(v <= pivotValue)) { tieRunOk = false; break; }
        if (!higher && !(v >= pivotValue)) { tieRunOk = false; break; }
      }
      if (!tieRunOk) continue;

      let strictRunOk = true;
      for (let t = 1; t <= periods; t++) {
        const v = getValue(periods + k + t);
        if (Number.isNaN(v)) { strictRunOk = false; break; }
        if (higher && !(v < pivotValue)) { strictRunOk = false; break; }
        if (!higher && !(v > pivotValue)) { strictRunOk = false; break; }
      }
      if (strictRunOk) return true;
    }
    return false;
  };

  // Captured BEFORE this candle's own possible reconfirmation is applied,
  // same ordering as order_blocks.py's leg_start = confirmed[i-1].
  const highLegStart = state.highConfirmedIndex;
  const lowLegStart = state.lowConfirmedIndex;

  let highBroke = false;
  let lowBroke = false;

  // A genuine break is checked against whatever level was known BEFORE
  // today's own confirmation is applied, same ordering fractal_detector.py
  // uses.
  if (!Number.isNaN(state.swingHigh) && !state.highCrossed && closeToday > state.swingHigh) {
    state.highCrossed = true;
    highBroke = true;
  }
  if (!Number.isNaN(state.swingLow) && !state.lowCrossed && closeToday < state.swingLow) {
    state.lowCrossed = true;
    lowBroke = true;
  }

  // Test the candle `periods` bars back as a fractal candidate. Every bar
  // this needs already exists on the chart, so this is a pure lookback.
  const pivotTime = time(periods);
  if (!Number.isNaN(pivotTime)) {
    const pivotHigh = high(periods);
    if (nearFrontierStrict(pivotHigh, true, (k) => high(k)) && farFrontierTolerant(pivotHigh, true, (k) => high(k))) {
      state.swingHigh = pivotHigh;
      state.highCrossed = false;
      state.highConfirmedIndex = curIdx;
    }

    const pivotLow = low(periods);
    if (nearFrontierStrict(pivotLow, false, (k) => low(k)) && farFrontierTolerant(pivotLow, false, (k) => low(k))) {
      state.swingLow = pivotLow;
      state.lowCrossed = false;
      state.lowConfirmedIndex = curIdx;
    }
  }

  // A close cannot be both above swingHigh and below swingLow at once, so
  // these are mutually exclusive, same as market_structure.py.
  if (highBroke) {
    state.structure = 'bullish';
  } else if (lowBroke) {
    state.structure = 'bearish';
  }

  return { highBroke, lowBroke, highLegStart, lowLegStart };
};

// ---- Anchor selection: order_blocks.py's _find_displacement_anchor,
// ported 1:1. Scans [legStart, breakIndex] for the first candle whose
// range exceeds atrMultiple times its own ATR (the displacement candle),
// falling back to breakIndex itself if none qualifies. Returns the candle
// immediately before it (the anchor), or null if that falls before index
// 0. ----
const findDisplacementAnchor = (highs, lows, atrArr, legStart, breakIndex, atrMultiple) => {
  let displacementIndex = null;
  for (let k = legStart; k <= breakIndex; k++) {
    const atrK = atrArr[k];
    if (atrK === undefined || atrK === null || Number.isNaN(atrK)) continue;
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

// ---- Zone shaping: order_blocks.py's _shape_zone, ported 1:1, with
// _band and _combined_range nested as local closures (self-contained, per
// the header note on why no top-level helper calls another by name).
// Returns {top, bottom, zoneEnd}, where zoneEnd is the rightmost candle
// actually folded into the zone (anchor itself unless a forward merge
// pulled anchor+1 in too). ----
const shapeZone = (anchor, direction, opens, highs, lows, closes, atrArr, lowMultiple, highMultiple) => {
  const bandOf = (rangeValue, atrValue) => {
    if (atrValue === undefined || atrValue === null || Number.isNaN(atrValue)) return 'medium';
    if (rangeValue < lowMultiple * atrValue) return 'low';
    if (rangeValue > highMultiple * atrValue) return 'high';
    return 'medium';
  };

  const combinedRange = (indices) => {
    let top = -Infinity;
    let bottom = Infinity;
    for (let n = 0; n < indices.length; n++) {
      const j = indices[n];
      if (highs[j] > top) top = highs[j];
      if (lows[j] < bottom) bottom = lows[j];
    }
    return [top, bottom];
  };

  const atrAtAnchor = atrArr[anchor];
  const anchorRange = highs[anchor] - lows[anchor];
  const band = bandOf(anchorRange, atrAtAnchor);

  if (band === 'medium') {
    return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
  }

  if (band === 'high') {
    if (direction === 'bullish') {
      // Keep the far (low-side) wick, trim the near edge to the body.
      return { top: Math.max(opens[anchor], closes[anchor]), bottom: lows[anchor], zoneEnd: anchor };
    }
    return { top: highs[anchor], bottom: Math.min(opens[anchor], closes[anchor]), zoneEnd: anchor };
  }

  // band === 'low': too small to use alone, try merging with a neighbor.
  const length = highs.length;

  let forwardResult = null;
  if (anchor + 1 < length) {
    const fRange = combinedRange([anchor, anchor + 1]);
    const fBand = bandOf(fRange[0] - fRange[1], atrAtAnchor);
    if (fBand === 'medium') return { top: fRange[0], bottom: fRange[1], zoneEnd: anchor + 1 };
    forwardResult = [fRange[0], fRange[1], fBand];
  }

  let backwardResult = null;
  if (anchor - 1 >= 0) {
    const bRange = combinedRange([anchor - 1, anchor]);
    const bBand = bandOf(bRange[0] - bRange[1], atrAtAnchor);
    if (bBand === 'medium') return { top: bRange[0], bottom: bRange[1], zoneEnd: anchor };
    backwardResult = [bRange[0], bRange[1], bBand];
  }

  const bothHigh = forwardResult !== null && forwardResult[2] === 'high' && backwardResult !== null && backwardResult[2] === 'high';
  if (bothHigh) {
    // Adding a third candle can only keep the range the same size or grow
    // it, never shrink it back down, so it can't help here.
    return { top: forwardResult[0], bottom: forwardResult[1], zoneEnd: anchor + 1 };
  }

  if (anchor - 1 >= 0 && anchor + 1 < length) {
    const range3 = combinedRange([anchor - 1, anchor, anchor + 1]);
    return { top: range3[0], bottom: range3[1], zoneEnd: anchor + 1 };
  }

  // Too close to the edge of the data for a 3-candle combo.
  if (forwardResult !== null) return { top: forwardResult[0], bottom: forwardResult[1], zoneEnd: anchor + 1 };
  if (backwardResult !== null) return { top: backwardResult[0], bottom: backwardResult[1], zoneEnd: anchor };
  return { top: highs[anchor], bottom: lows[anchor], zoneEnd: anchor };
};

// ---- Candidate grouping: order_blocks.py groups ALL (direction, anchor)
// candidates across every tier into one OB row, classified by the LARGEST
// tier involved (primary_tier). Since candles are processed strictly in
// chronological order here, the first tier to trigger a given anchor
// always sets earliestTriggerIndex correctly; a later tier joining the
// same anchor just appends to triggerTiers and recomputes primaryTier.
// zone (top/bottom/zoneEnd) is a pure function of anchor+direction+market
// data, so it is computed once at creation and never touched again on a
// merge. ----
const upsertOrderBlock = (orderBlocks, direction, anchor, tierName, triggerIdx, legStart, zoneTop, zoneBottom, zoneEnd, tierOrder) => {
  let existing = null;
  for (let i = 0; i < orderBlocks.length; i++) {
    if (orderBlocks[i].direction === direction && orderBlocks[i].formedIndex === anchor) {
      existing = orderBlocks[i];
      break;
    }
  }

  if (existing) {
    if (existing.triggerTiers.indexOf(tierName) === -1) existing.triggerTiers.push(tierName);
    const prevForTier = existing.triggerIndexByTier[tierName];
    existing.triggerIndexByTier[tierName] = prevForTier === undefined ? triggerIdx : Math.min(prevForTier, triggerIdx);
    existing.earliestTriggerIndex = Math.min(existing.earliestTriggerIndex, triggerIdx);
    existing.legStartIndex = Math.min(existing.legStartIndex, legStart);

    let primary = existing.primaryTier;
    for (let t = 0; t < tierOrder.length; t++) {
      if (existing.triggerTiers.indexOf(tierOrder[t]) !== -1) {
        primary = tierOrder[t];
        break;
      }
    }
    existing.primaryTier = primary;
    existing.triggerIndex = existing.triggerIndexByTier[primary];
    return existing;
  }

  const created = {
    direction,
    formedIndex: anchor,
    top: zoneTop,
    bottom: zoneBottom,
    zoneEndIndex: zoneEnd,
    triggerTiers: [tierName],
    primaryTier: tierName,
    triggerIndexByTier: {},
    triggerIndex: triggerIdx,
    legStartIndex: legStart,
    earliestTriggerIndex: triggerIdx,
    causedDisplacement: null, // null = not yet knowable, see onTick
    causedImbalance: null,
    mitigated: false,
    mitigatedIndex: null,
    invalidated: false,
    invalidatedIndex: null,
    sweptLiquiditySwing: false,
    sweptLiquidityInternal: false,
    sweptLiquidityFractal: false,
    sweptLiquidityFvg: false,
    sweptLiquidityPreviousCandle: false,
    hasInducement: false,
    isFlipZone: false,
    flippedObIndex: null,
  };
  created.triggerIndexByTier[tierName] = triggerIdx;
  orderBlocks.push(created);
  return created;
};

// ---- Factor 1-3: swept_liquidity_swing/internal/fractal. Ported from
// order_block_quality.py's compute_swept_liquidity_structural: for each OB
// and each tier T, scans [leg_start_index, earliest_trigger_index] (the
// leg that produced the OB, regardless of which tier actually triggered
// it) for a candle that wicks beyond T's own currently-active strong
// point while T's structure stays on that side. ----
const computeSweptLiquidityStructural = (orderBlocks, obHistHigh, obHistLow, tierHistories, tierOrder) => {
  for (let x = 0; x < orderBlocks.length; x++) {
    const ob = orderBlocks[x];
    for (let t = 0; t < tierOrder.length; t++) {
      const tierName = tierOrder[t];
      const hist = tierHistories[tierName];
      let swept = false;
      for (let k = ob.legStartIndex; k <= ob.earliestTriggerIndex; k++) {
        if (ob.direction === 'bullish') {
          if (hist.structureArr[k] === 'bullish' && obHistLow[k] < hist.swingLowArr[k]) {
            swept = true;
            break;
          }
        } else {
          if (hist.structureArr[k] === 'bearish' && obHistHigh[k] > hist.swingHighArr[k]) {
            swept = true;
            break;
          }
        }
      }
      if (tierName === 'swing') ob.sweptLiquiditySwing = swept;
      else if (tierName === 'internal') ob.sweptLiquidityInternal = swept;
      else ob.sweptLiquidityFractal = swept;
    }
  }
};

// ---- Factor 4: swept_liquidity_fvg. Ported from compute_fvg_confluence:
// true if the OB's own formation candles wick into a same-direction FVG
// that was already active before the OB formed, without closing past the
// FVG's far edge. ----
const computeFvgConfluence = (orderBlocks, fvgs, obHistClose) => {
  for (let x = 0; x < orderBlocks.length; x++) {
    const ob = orderBlocks[x];
    const anchor = ob.formedIndex;
    const zoneEnd = ob.zoneEndIndex;
    const direction = ob.direction;
    let matched = false;

    for (let f = 0; f < fvgs.length; f++) {
      const fvg = fvgs[f];
      if (fvg.direction !== direction) continue;

      const activeUntil = fvg.filled ? fvg.filledIndex : fvg.expiryIndex;
      if (!(fvg.formedIndex < anchor && anchor <= activeUntil)) continue;

      const overlaps = (ob.bottom <= fvg.top) && (ob.top >= fvg.bottom);
      if (!overlaps) continue;

      let farEdgeBreached = false;
      for (let j = anchor; j <= zoneEnd; j++) {
        farEdgeBreached = direction === 'bullish' ? (obHistClose[j] < fvg.bottom) : (obHistClose[j] > fvg.top);
        if (farEdgeBreached) break;
      }

      if (!farEdgeBreached) {
        matched = true;
        break;
      }
    }

    ob.sweptLiquidityFvg = matched;
  }
};

// ---- Factor 5: swept_liquidity_previous_candle. Ported from
// compute_previous_candle_sweep: only meaningful for single-candle OBs
// (zone_end_index == formed_index). ----
const computePreviousCandleSweep = (orderBlocks, obHistHigh, obHistLow, obHistClose) => {
  for (let x = 0; x < orderBlocks.length; x++) {
    const ob = orderBlocks[x];
    const anchor = ob.formedIndex;
    const zoneEnd = ob.zoneEndIndex;
    const isSingleCandle = zoneEnd === anchor;

    if (!isSingleCandle || anchor === 0) {
      ob.sweptLiquidityPreviousCandle = false;
      continue;
    }

    if (ob.direction === 'bullish') {
      ob.sweptLiquidityPreviousCandle = obHistLow[anchor] < obHistLow[anchor - 1] && obHistClose[anchor] >= obHistLow[anchor - 1];
    } else {
      ob.sweptLiquidityPreviousCandle = obHistHigh[anchor] > obHistHigh[anchor - 1] && obHistClose[anchor] <= obHistHigh[anchor - 1];
    }
  }
};

// ---- Factor 6: has_inducement. Ported from compute_inducement: true for
// OB X if some earlier-formed, same-direction, not-yet-invalidated OB Y
// sits nearer to price with gap < max(height(X), height(Y)). ----
const computeInducement = (orderBlocks) => {
  const n = orderBlocks.length;
  for (let x = 0; x < n; x++) {
    const obX = orderBlocks[x];
    const heightX = obX.top - obX.bottom;
    obX.hasInducement = false;

    for (let y = 0; y < n; y++) {
      if (x === y) continue;
      const obY = orderBlocks[y];
      if (obY.invalidated) continue;
      if (obY.direction !== obX.direction) continue;
      if (obY.formedIndex >= obX.formedIndex) continue;

      const heightY = obY.top - obY.bottom;
      let nearer;
      let gap;
      if (obX.direction === 'bullish') {
        nearer = obY.bottom > obX.top;
        gap = obY.bottom - obX.top;
      } else {
        nearer = obY.top < obX.bottom;
        gap = obX.bottom - obY.top;
      }

      if (nearer && gap < Math.max(heightX, heightY)) {
        obX.hasInducement = true;
        break;
      }
    }
  }
};

// ---- Factor 7: is_flip_zone. Ported from compute_flip_zone: Z is a flip
// zone if some earlier, opposite-direction OB W was mitigated by one of
// Z's own formation candles, and W was later invalidated at or after Z
// went live. ----
const computeFlipZone = (orderBlocks) => {
  const n = orderBlocks.length;
  for (let z = 0; z < n; z++) {
    const obZ = orderBlocks[z];
    obZ.isFlipZone = false;
    obZ.flippedObIndex = null;

    for (let w = 0; w < n; w++) {
      if (z === w) continue;
      const obW = orderBlocks[w];
      if (obW.direction === obZ.direction) continue;
      if (obW.formedIndex >= obZ.formedIndex) continue;
      if (!obW.mitigated) continue;

      const reactedOnW = obZ.formedIndex <= obW.mitigatedIndex && obW.mitigatedIndex <= obZ.zoneEndIndex;
      if (!reactedOnW) continue;
      if (!obW.invalidated) continue;
      if (obW.invalidatedIndex < obZ.earliestTriggerIndex) continue;

      obZ.isFlipZone = true;
      obZ.flippedObIndex = w;
      break;
    }
  }
};

// Short abbreviations for the label attached to each OB rectangle. See
// header note 3 (rectangle text argument, UNVERIFIED).
const buildFactorLabel = (ob) => {
  const parts = [];
  if (ob.sweptLiquiditySwing) parts.push('SWG');
  if (ob.sweptLiquidityInternal) parts.push('INT');
  if (ob.sweptLiquidityFractal) parts.push('FRC');
  if (ob.sweptLiquidityFvg) parts.push('FVG');
  if (ob.sweptLiquidityPreviousCandle) parts.push('PC');
  if (ob.hasInducement) parts.push('IND');
  if (ob.isFlipZone) parts.push('FLIP');
  if (ob.causedDisplacement) parts.push('DISP');
  if (ob.causedImbalance) parts.push('IMB');
  return parts.join(' ');
};

onTick = (length, _moment, _, ta, inputs) => {
  const swingPeriods = inputs.swingPeriods;
  const internalPeriods = inputs.internalPeriods;
  const fractalPeriods = inputs.fractalPeriods;
  const atrPeriod = inputs.atrPeriod;
  const fvgLookback = inputs.fvgLookback;
  const showMitigated = inputs.showMitigated;
  const showFvgs = inputs.showFvgs;

  // ---- Timeframe gate. MUST come before the new-bar gate below, not
  // after (fxrscripts/README.md's "Timeframe isolation" section). ----
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
    // Not enough history to decide. Draw nothing, and deliberately do not
    // touch dailyObLastSpacing, so a warming-up candle never looks like a
    // timeframe change.
    return;
  }

  if (inferredSpacing !== MY_TIMEFRAME_MS) {
    // Not our chart. This is what keeps Daily OB/FVG markings off other
    // timeframes.
    if (TF_CLEANUP_ON_MISMATCH) {
      deleteDrawingByCondition((drawing) => {
        if (drawing.shapeType !== 'rectangle') return false;
        const opts = drawing.overrideOptions;
        if (!opts) return false;
        return OB_TAG_COLORS.indexOf(opts['backgroundColor']) !== -1;
      });
    }
    dailyObLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== dailyObLastSpacing) {
    // Arrived on our timeframe from somewhere else. Reset everything, so
    // the state machine does not resume on top of state accumulated from
    // the other timeframe's candles.
    dailyObHistTime.length = 0;
    dailyObHistOpen.length = 0;
    dailyObHistHigh.length = 0;
    dailyObHistLow.length = 0;
    dailyObHistClose.length = 0;

    dailyObSwingStructureArr.length = 0;
    dailyObSwingHighArr.length = 0;
    dailyObSwingLowArr.length = 0;
    dailyObInternalStructureArr.length = 0;
    dailyObInternalHighArr.length = 0;
    dailyObInternalLowArr.length = 0;
    dailyObFractalStructureArr.length = 0;
    dailyObFractalHighArr.length = 0;
    dailyObFractalLowArr.length = 0;

    dailyOrderBlocks.length = 0;
    dailyFvgs.length = 0;

    dailyObSwingTier.swingHigh = NaN;
    dailyObSwingTier.highCrossed = false;
    dailyObSwingTier.swingLow = NaN;
    dailyObSwingTier.lowCrossed = false;
    dailyObSwingTier.structure = null;
    dailyObSwingTier.highConfirmedIndex = 0;
    dailyObSwingTier.lowConfirmedIndex = 0;

    dailyObInternalTier.swingHigh = NaN;
    dailyObInternalTier.highCrossed = false;
    dailyObInternalTier.swingLow = NaN;
    dailyObInternalTier.lowCrossed = false;
    dailyObInternalTier.structure = null;
    dailyObInternalTier.highConfirmedIndex = 0;
    dailyObInternalTier.lowConfirmedIndex = 0;

    dailyObFractalTier.swingHigh = NaN;
    dailyObFractalTier.highCrossed = false;
    dailyObFractalTier.swingLow = NaN;
    dailyObFractalTier.lowCrossed = false;
    dailyObFractalTier.structure = null;
    dailyObFractalTier.highConfirmedIndex = 0;
    dailyObFractalTier.lowConfirmedIndex = 0;

    dailyObLastSeenTime = null;
    dailyObLastSpacing = inferredSpacing;
  }

  // ---- One update per closed candle, gated on that candle's own
  // timestamp rather than `length`. ----
  const currentTime = time(0);
  if (currentTime === dailyObLastSeenTime) {
    return;
  }
  dailyObLastSeenTime = currentTime;

  // ---- Append this closed candle to history ----
  dailyObHistTime.push(currentTime);
  dailyObHistOpen.push(openC(0));
  dailyObHistHigh.push(high(0));
  dailyObHistLow.push(low(0));
  dailyObHistClose.push(closeC(0));
  const curIdx = dailyObHistHigh.length - 1;
  const closeToday = dailyObHistClose[curIdx];

  // ---- ATR series. See header note 1 (UNVERIFIED: ta.atr's exact
  // smoothing formula). Recomputed over the whole growing history every
  // tick, since past indices need their OWN ATR value for the
  // displacement scan and zone shaping, not just today's. ----
  const atrArr = ta.atr(dailyObHistHigh, dailyObHistLow, dailyObHistClose, atrPeriod);

  // ---- Update the three tier engines ----
  const swingResult = updateFractalTier(dailyObSwingTier, swingPeriods, FRACTAL_TIE_TOLERANCE, curIdx, closeToday);
  const internalResult = updateFractalTier(dailyObInternalTier, internalPeriods, FRACTAL_TIE_TOLERANCE, curIdx, closeToday);
  const fractalResult = updateFractalTier(dailyObFractalTier, fractalPeriods, FRACTAL_TIE_TOLERANCE, curIdx, closeToday);

  dailyObSwingStructureArr.push(dailyObSwingTier.structure);
  dailyObSwingHighArr.push(dailyObSwingTier.swingHigh);
  dailyObSwingLowArr.push(dailyObSwingTier.swingLow);
  dailyObInternalStructureArr.push(dailyObInternalTier.structure);
  dailyObInternalHighArr.push(dailyObInternalTier.swingHigh);
  dailyObInternalLowArr.push(dailyObInternalTier.swingLow);
  dailyObFractalStructureArr.push(dailyObFractalTier.structure);
  dailyObFractalHighArr.push(dailyObFractalTier.swingHigh);
  dailyObFractalLowArr.push(dailyObFractalTier.swingLow);

  // Largest to smallest, matching order_blocks.py's DAILY_TIER_PREFIXES
  // ordering, used to pick primary_tier on a multi-tier merge.
  const tierOrder = ['swing', 'internal', 'fractal'];
  const tierResults = [
    { name: 'swing', result: swingResult },
    { name: 'internal', result: internalResult },
    { name: 'fractal', result: fractalResult },
  ];

  // ---- OB candidate creation: one leg-scan + anchor selection per
  // (tier, break event) this candle, merged by (direction, anchor) into
  // dailyOrderBlocks. ----
  for (let t = 0; t < tierResults.length; t++) {
    const tierName = tierResults[t].name;
    const r = tierResults[t].result;

    if (r.highBroke) {
      const anchor = findDisplacementAnchor(dailyObHistHigh, dailyObHistLow, atrArr, r.highLegStart, curIdx, DISPLACEMENT_ATR_MULTIPLE);
      if (anchor !== null) {
        const zone = shapeZone(anchor, 'bullish', dailyObHistOpen, dailyObHistHigh, dailyObHistLow, dailyObHistClose, atrArr, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
        upsertOrderBlock(dailyOrderBlocks, 'bullish', anchor, tierName, curIdx, r.highLegStart, zone.top, zone.bottom, zone.zoneEnd, tierOrder);
      }
    }
    if (r.lowBroke) {
      const anchor = findDisplacementAnchor(dailyObHistHigh, dailyObHistLow, atrArr, r.lowLegStart, curIdx, DISPLACEMENT_ATR_MULTIPLE);
      if (anchor !== null) {
        const zone = shapeZone(anchor, 'bearish', dailyObHistOpen, dailyObHistHigh, dailyObHistLow, dailyObHistClose, atrArr, LOW_BAND_ATR_MULTIPLE, HIGH_BAND_ATR_MULTIPLE);
        upsertOrderBlock(dailyOrderBlocks, 'bearish', anchor, tierName, curIdx, r.lowLegStart, zone.top, zone.bottom, zone.zoneEnd, tierOrder);
      }
    }
  }

  // ---- caused_displacement / caused_imbalance: deferred until
  // zone_end+1 / zone_end+2 actually exist (they may be in the future
  // relative to the candle the OB formed on), checked against zone_end,
  // not the raw anchor, matching order_blocks.py's compute_order_blocks.
  for (let i = 0; i < dailyOrderBlocks.length; i++) {
    const ob = dailyOrderBlocks[i];
    if (ob.causedDisplacement === null && ob.zoneEndIndex + 1 <= curIdx) {
      if (ob.direction === 'bullish') {
        ob.causedDisplacement = dailyObHistClose[ob.zoneEndIndex + 1] > dailyObHistHigh[ob.zoneEndIndex];
      } else {
        ob.causedDisplacement = dailyObHistClose[ob.zoneEndIndex + 1] < dailyObHistLow[ob.zoneEndIndex];
      }
    }
    if (ob.causedImbalance === null && ob.zoneEndIndex + 2 <= curIdx) {
      if (ob.direction === 'bullish') {
        ob.causedImbalance = dailyObHistLow[ob.zoneEndIndex + 2] > dailyObHistHigh[ob.zoneEndIndex];
      } else {
        ob.causedImbalance = dailyObHistHigh[ob.zoneEndIndex + 2] < dailyObHistLow[ob.zoneEndIndex];
      }
    }
  }

  // ---- Mitigation / invalidation: one incremental step per closed
  // candle, equivalent to _apply_mitigation's / _apply_invalidation's
  // forward scan from earliest_trigger_index+1, since this runs every bar
  // in chronological order and stops updating once true (a one-time
  // lifetime flag). ----
  for (let i = 0; i < dailyOrderBlocks.length; i++) {
    const ob = dailyOrderBlocks[i];
    if (!ob.mitigated && curIdx > ob.earliestTriggerIndex) {
      if (dailyObHistLow[curIdx] <= ob.top && dailyObHistHigh[curIdx] >= ob.bottom) {
        ob.mitigated = true;
        ob.mitigatedIndex = curIdx;
      }
    }
    if (!ob.invalidated && curIdx > ob.earliestTriggerIndex) {
      const brokeThrough = ob.direction === 'bullish' ? dailyObHistClose[curIdx] < ob.bottom : dailyObHistClose[curIdx] > ob.top;
      if (brokeThrough) {
        ob.invalidated = true;
        ob.invalidatedIndex = curIdx;
      }
    }
  }

  // ---- FVG detection: the same 3-candle gap test order_blocks.py uses
  // for caused_imbalance, run over every consecutive triple as soon as
  // the 3rd candle closes. No width filter, matching fair_value_gaps.py's
  // "very aggressive" detection. ----
  if (curIdx >= 2) {
    const gapStart = curIdx - 2;
    if (dailyObHistLow[curIdx] > dailyObHistHigh[gapStart]) {
      dailyFvgs.push({
        direction: 'bullish',
        formedIndex: curIdx,
        gapStartIndex: gapStart,
        top: dailyObHistLow[curIdx],
        bottom: dailyObHistHigh[gapStart],
        midpoint: (dailyObHistLow[curIdx] + dailyObHistHigh[gapStart]) / 2,
        filled: false,
        filledIndex: null,
        expiryIndex: curIdx + fvgLookback, // see header note 4: uncapped
      });
    }
    if (dailyObHistHigh[curIdx] < dailyObHistLow[gapStart]) {
      dailyFvgs.push({
        direction: 'bearish',
        formedIndex: curIdx,
        gapStartIndex: gapStart,
        top: dailyObHistLow[gapStart],
        bottom: dailyObHistHigh[curIdx],
        midpoint: (dailyObHistLow[gapStart] + dailyObHistHigh[curIdx]) / 2,
        filled: false,
        filledIndex: null,
        expiryIndex: curIdx + fvgLookback,
      });
    }
  }

  // ---- FVG fill / expiry: 50%-of-range wick fill or lookback expiry,
  // whichever comes first, mirroring fair_value_gaps.py. ----
  for (let i = 0; i < dailyFvgs.length; i++) {
    const fvg = dailyFvgs[i];
    if (fvg.filled) continue;
    if (curIdx <= fvg.formedIndex) continue; // never test an FVG on its own formation candle
    if (curIdx > fvg.expiryIndex) continue; // expired, stays unfilled permanently
    const reached = fvg.direction === 'bullish' ? dailyObHistLow[curIdx] <= fvg.midpoint : dailyObHistHigh[curIdx] >= fvg.midpoint;
    if (reached) {
      fvg.filled = true;
      fvg.filledIndex = curIdx;
    }
  }

  // ---- Quality factors: recomputed fully over the in-memory OB/FVG
  // lists every closed candle, an in-place equivalent of calling
  // order_block_quality.py's compute_* functions fresh each time. ----
  const tierHistories = {
    swing: { structureArr: dailyObSwingStructureArr, swingHighArr: dailyObSwingHighArr, swingLowArr: dailyObSwingLowArr },
    internal: { structureArr: dailyObInternalStructureArr, swingHighArr: dailyObInternalHighArr, swingLowArr: dailyObInternalLowArr },
    fractal: { structureArr: dailyObFractalStructureArr, swingHighArr: dailyObFractalHighArr, swingLowArr: dailyObFractalLowArr },
  };
  computeSweptLiquidityStructural(dailyOrderBlocks, dailyObHistHigh, dailyObHistLow, tierHistories, tierOrder);
  computeFvgConfluence(dailyOrderBlocks, dailyFvgs, dailyObHistClose);
  computePreviousCandleSweep(dailyOrderBlocks, dailyObHistHigh, dailyObHistLow, dailyObHistClose);
  computeInducement(dailyOrderBlocks);
  computeFlipZone(dailyOrderBlocks);

  // ---- Redraw. See header notes 2 and 3 (UNVERIFIED: rectangle +
  // deleteDrawingByCondition at this volume, and rectangle's text
  // argument). Delete every one of this script's own OB/FVG rectangles,
  // matched on backgroundColor via bracket access (dotted access is a
  // type error on the DrawingOverrides union, per fxrscripts/README.md
  // Design 3), then redraw fresh from current state. ----
  deleteDrawingByCondition((drawing) => {
    if (drawing.shapeType !== 'rectangle') return false;
    const opts = drawing.overrideOptions;
    if (!opts) return false;
    return OB_TAG_COLORS.indexOf(opts['backgroundColor']) !== -1;
  });

  const rightTime = dailyObHistTime[curIdx];

  for (let i = 0; i < dailyOrderBlocks.length; i++) {
    const ob = dailyOrderBlocks[i];
    if (ob.invalidated) continue; // skip invalidated OBs entirely, per spec
    if (ob.mitigated && !showMitigated) continue;

    const boxColor = ob.direction === 'bullish'
      ? (ob.mitigated ? OB_BULLISH_DIM_COLOR : OB_BULLISH_COLOR)
      : (ob.mitigated ? OB_BEARISH_DIM_COLOR : OB_BEARISH_COLOR);

    const leftTime = dailyObHistTime[ob.formedIndex];
    const label = buildFactorLabel(ob);

    // NOTE: no extendRight override here -- unlike the text-label/showLabel
    // guess (header note 3), `extendRight` was never part of
    // rectangle_lifecycle_probe.py's tested option set, and h4_order_
    // blocks.py's own rectangle calls don't use it either. Since this loop
    // already redraws every OB fresh each tick with its right corner
    // pinned to rightTime (the current candle), that alone is what
    // extends the box; adding an unconfirmed `extendRight` key on top
    // risks it meaning "extend to the edge of the viewport" instead, which
    // would silently produce a wider box than intended with no compile
    // error to catch it. Dropped to match the sibling script's approach.
    rectangle(
      leftTime, ob.top,
      rightTime, ob.bottom,
      { backgroundColor: boxColor, color: boxColor, fillBackground: true, showLabel: true, textColor: color.white, bold: true },
      label
    );
  }

  if (showFvgs) {
    for (let i = 0; i < dailyFvgs.length; i++) {
      const fvg = dailyFvgs[i];
      if (fvg.filled) continue;
      if (curIdx > fvg.expiryIndex) continue; // expired, no longer active

      const boxColor = fvg.direction === 'bullish' ? FVG_BULLISH_COLOR : FVG_BEARISH_COLOR;
      const leftTime = dailyObHistTime[fvg.gapStartIndex];

      // See the OB loop's note above: extendRight is not a confirmed
      // option and isn't needed since the box's right corner is already
      // pinned to rightTime (the current candle) every redraw.
      rectangle(
        leftTime, fvg.top,
        rightTime, fvg.bottom,
        { backgroundColor: boxColor, color: boxColor, fillBackground: true, showLabel: true, textColor: color.white, bold: false },
        'FVG'
      );
    }
  }
};
