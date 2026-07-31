// FXR Script visualization of the H1 INTERNAL tier's equilibrium price
// (n=8). Not an independent detector: this is h1_internal_structure.py's
// full fractal-detection engine, duplicated verbatim (FXR Script has no
// import mechanism, see fxrscripts/README.md), with one drawing block
// appended at the end of onTick. If the detection logic here ever
// disagrees with h1_internal_structure.py for the same candles, that is
// a copy-drift bug, fix h1_internal_structure.py's copy first if the two
// were meant to diverge, otherwise just resync this file from it.
//
// Python is the source of truth: swing_structure/premium_discount.py's
// compute_premium_discount, applied to the h1_internal tier by
// compute_h1_premium_discount. If this ever disagrees with the Python
// for the same candles, fix the Python first, then re-port.
//
// Reads h1InternalSwingHigh/h1InternalSwingLow directly, unlike its
// swing-tier sibling (h1_swing_premium_discount.py), which extends a
// stale, already-broken pivot with the running extreme made since (see
// swing_structure/current_range.py). That same staleness applies here
// too (h1_internal_structure.py's Williams Fractal pivots also freeze
// once a side is broken and price keeps running), it just hasn't been
// fixed for this tier yet, deferred deliberately rather than forgotten.
//
// There is no existing TradingView reference indicator for this to
// compare against (unlike swing/internal/fractal structure, which all
// ported from a known-good Pine reference), so this script IS the
// verification method, checked by eye against the rule itself:
// equilibrium = (swingHigh + swingLow) / 2.
//
// Visual: a single thin ("EQ"-labeled) horizontalLine at the equilibrium
// price, nothing else. An earlier version also drew a shaded box spanning
// the range's two corners in time, but that box's left corner sat at
// whichever pivot's own (possibly old) timestamp, so as the chart
// accumulated history the box visually grew to cover every prior swing
// leg rather than just the current one. Removed entirely rather than
// fixed, since a horizontalLine has no time corners to get wrong in the
// first place, it is simply the current equilibrium price, full width,
// always current (see h1_swing_premium_discount.py's docstring for the
// fuller account of why the box was dropped).
//
// The line is deleted and redrawn every closed candle (unlike the
// swing-level trendLines above, which are drawn once at a cross and
// never touched again), since its price can flip on any candle as price
// wiggles around equilibrium. This is fxrscripts/README.md's proven
// "Design 3" pattern: content-matched deletion via
// deleteDrawingByCondition, not id tracking, since id tracking was found
// to leave stale drawings behind during fxreplay's replay stepping.
//
// The line is tagged with a reserved color (EQ_LINE_COLOR) rather than
// MY_LINEWIDTH, since horizontalLine doesn't share the trendLine-only
// linewidth tagging convention the rest of this file's engine uses for
// its own swing-level lines. This script and its swing-tier sibling
// (h1_swing_premium_discount.py) each use a different EQ_LINE_COLOR, so
// neither's cleanup can delete the other's line.

const FRACTAL_TIE_TOLERANCE = 4;

// ---------------------------------------------------------------------
// TIMEFRAME ISOLATION. This block is duplicated VERBATIM (bar the two
// constants) in all nine structure scripts: daily_swing_structure.py,
// daily_internal_structure.py, daily_fractal_structure.py,
// h1_fractal_structure.py, h1_internal_structure.py, h1_swing_structure.py,
// h1_fractal_structure.py, h1_internal_structure.py, h1_swing_structure.py.
// FXR Script has no import mechanism, so a change here has to be applied
// to all nine (plus this one and its swing-tier sibling) by hand.
//
// FXR Script CANNOT read the chart's current timeframe. There is no
// `timeframe`, `period`, `resolution` or `syminfo` global, indicator()
// takes only onMainPanel/format/precision, input.timeframe reports what
// the user picked rather than what the chart is on, and mtf.* reads OTHER
// timeframes but cannot report the chart's own. So the timeframe is
// INFERRED from bar spacing, and the script hides itself when it is not
// on its own timeframe.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 3600000;    // H1. 86400000 in the daily scripts, 14400000 in the 4H scripts.
const MY_LINEWIDTH = 6;             // Same tag h1_internal_structure.py uses for its own trendLines.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;

// Whether to remove this script's own drawings when the chart is not on
// its timeframe. Set from the fxrscripts/timeframe_probe.py findings: if
// fxreplay already clears an indicator's drawings when the timeframe
// changes, this is unnecessary work and can be set to false.
const TF_CLEANUP_ON_MISMATCH = true;

// Reserved tag color for this script's equilibrium line, read back via
// bracket access on overrideOptions (dotted access is a type error on
// the DrawingOverrides union, see fxrscripts/README.md Design 3). Cyan,
// distinct from every trendLine color already in use (white, red, green)
// and from the swing tier's own tag (orange, in
// h1_swing_premium_discount.py), so deleteDrawingByCondition below can
// never touch a sibling script's drawing.
const EQ_LINE_COLOR = color.rgba(0, 200, 255, 1);
const EQ_LINE_WIDTH = 1;

let h1InternalSwingHigh = NaN;
let h1InternalSwingHighTime = null;
let h1InternalHighCrossed = false;
let h1InternalSwingLow = NaN;
let h1InternalSwingLowTime = null;
let h1InternalLowCrossed = false;

let h1InternalPrevManualInput = false;
let h1InternalLastRestartTime = null;
let h1InternalLastSeenTime = null;

// Wilder's ATR, only accumulated when the significance filter is switched
// on. Mirrors swing_structure/atr.py.
let h1InternalAtr = NaN;
let h1InternalTrSum = 0;
let h1InternalTrCount = 0;

// Last inferred bar spacing, so a timeframe change can be detected and
// state reset rather than resumed on top of the other timeframe's bars.
let h1InternalLastSpacing = null;

// Bullish/bearish structure for this tier, same rule as
// swing_structure/market_structure.py: starts undetermined (null), and
// flips ONLY on a genuine cross, never on a manual restart or a silent
// reversal confirmation.
let h1InternalStructure = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  // Range goes to 100, not the Daily script's 10: the sibling swing tier
  // needs n=20, and keeping one shared range across all three scripts
  // means they stay a one-line diff from each other.
  input.int('H1 Internal Periods', 8, 'periods', 2, 100, 1, 'Candles required strictly beyond the pivot, on the frontier closer to now, to confirm a fractal. This is the tier\'s scale: 2 is minor pulls, 20 is major legs.', 'H1 Internal Premium/Discount Settings');

  // OFF by default (0). Rejects a newly confirmed pivot unless its
  // distance from the last confirmed pivot on the OPPOSITE side is at
  // least this many ATRs, which makes it a leg-size test rather than a
  // displacement test. It exists switched off so that enabling it later is
  // a settings change rather than a re-port across nine scripts.
  //
  // WARNING, confirmed in scripts/verify_tier_nesting.py: this is NOT a
  // monotonic dial. The opposite-side pivot it measures against is itself
  // filtered, so a larger threshold can re-admit pivots that a smaller one
  // rejected. Expect to search values rather than turn it one way.
  input.float('H1 Internal ATR Separation', 0, 'minAtrSeparation', 0, 5, 0.25, 'Minimum leg size against the last opposite pivot, in ATRs. 0 disables the filter entirely.', 'H1 Internal Premium/Discount Settings');
  input.int('H1 Internal ATR Period', 14, 'atrPeriod', 2, 200, 1, 'How many True Range values feed Wilder\'s ATR. Ignored while ATR Separation is 0.', 'H1 Internal Premium/Discount Settings');

  input.bool('H1 Internal Manual Restart Now', false, 'manualRestartInput');
};

// True if all `periods` candles between the candidate pivot (periods bars
// back) and now are strictly beyond pivotValue. higher=true checks highs
// for an up-fractal, higher=false checks lows for a down-fractal.
//
// Note these helpers read only their arguments, never top-level state.
// That is deliberate: a helper declared alongside onTick cannot see
// top-level `let` variables (fxrscripts/README.md, resolved question 4).
const h1InternalNearFrontierStrict = (periods, pivotValue, higher, getValue) => {
  for (let t = 1; t <= periods; t++) {
    const v = getValue(periods - t);
    if (Number.isNaN(v)) return false;
    if (higher && !(v < pivotValue)) return false;
    if (!higher && !(v > pivotValue)) return false;
  }
  return true;
};

// True if the `periods` candles further back than the pivot are beyond
// pivotValue, tolerating a run of up to tieTolerance candles immediately
// before it tied with pivotValue itself. Ties are tolerated ONLY on this
// far side, never on the near side above.
//
// tieTolerance is passed in rather than read from the top-level
// FRACTAL_TIE_TOLERANCE constant: a helper declared alongside onTick
// cannot see ANY top-level declaration, let or const
// (fxrscripts/README.md, resolved question 4, confirmed the hard way when
// this exact pattern threw "FRACTAL_TIE_TOLERANCE is not defined" at
// runtime despite the constant being declared earlier in the same file).
const h1InternalFarFrontierTolerant = (periods, pivotValue, higher, getValue, tieTolerance) => {
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

onTick = (length, _moment, _, ta, inputs) => {
  const periods = inputs.periods;
  const minAtrSeparation = inputs.minAtrSeparation;
  const atrPeriod = inputs.atrPeriod;
  const manualRestartInput = inputs.manualRestartInput;

  // ---- Timeframe gate. MUST come before the new-bar gate below, not
  // after: a wrong-timeframe candle that consumed the new-bar gate would
  // desynchronise it, so the state machine would skip that candle when the
  // user switched back.
  //
  // Inlined directly here rather than factored into a helper: a helper
  // declared alongside onTick cannot see ANY top-level declaration, let or
  // const (fxrscripts/README.md, resolved question 4). MINIMUM positive
  // delta, not the median and not time(0)-time(1): session and weekend
  // gaps only ever make a delta LARGER, never smaller, so the minimum is
  // the true bar interval and is gap-proof by construction. ----
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
    // touch h1InternalLastSpacing, so a warming-up candle never looks like
    // a timeframe change.
    return;
  }

  if (inferredSpacing !== MY_TIMEFRAME_MS) {
    // Not our chart. This is what keeps H1 markings off the Daily chart.
    if (TF_CLEANUP_ON_MISMATCH) {
      // Deletes only THIS script's own trendLines (matched on linewidth,
      // shared with h1_internal_structure.py's tag) and this script's own
      // equilibrium line (matched on EQ_LINE_COLOR). overrideOptions is
      // the DrawingOverrides union across every drawing tool, so dotted
      // access (.linewidth) is a type error, per fxrscripts/README.md
      // Design 3. Bracket access is used to sidestep that.
      deleteDrawingByCondition((drawing) => {
        const opts = drawing.overrideOptions;
        if (!opts) return false;
        if (opts['linewidth'] === MY_LINEWIDTH) return true;
        if (drawing.shapeType === 'horizontal_line' && opts['linecolor'] === EQ_LINE_COLOR) return true;
        return false;
      });
    }
    h1InternalLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== h1InternalLastSpacing) {
    // Arrived on our timeframe from somewhere else. Reset everything, so
    // the state machine does not resume on top of state accumulated from
    // the other timeframe's candles. Inlined rather than factored into a
    // helper because a helper cannot see top-level state.
    h1InternalSwingHigh = NaN;
    h1InternalSwingHighTime = null;
    h1InternalHighCrossed = false;
    h1InternalSwingLow = NaN;
    h1InternalSwingLowTime = null;
    h1InternalLowCrossed = false;
    h1InternalPrevManualInput = false;
    h1InternalLastRestartTime = null;
    h1InternalLastSeenTime = null;
    h1InternalAtr = NaN;
    h1InternalTrSum = 0;
    h1InternalTrCount = 0;
    h1InternalStructure = null;
    h1InternalLastSpacing = inferredSpacing;
  }

  // ---- One state-machine update per closed candle, gated on that
  // candle's own timestamp rather than `length` (see
  // daily_swing_structure.py for why). ----
  const currentTime = time(0);
  if (currentTime === h1InternalLastSeenTime) {
    return;
  }
  h1InternalLastSeenTime = currentTime;

  const closeToday = closeC(0);

  // ---- Wilder's ATR, only when the filter is actually on, so the default
  // path does no extra work. Mirrors swing_structure/atr.py, including the
  // no-previous-close special case and the simple-mean seed. ----
  const filterOn = minAtrSeparation > 0;
  if (filterOn) {
    const highToday = high(0);
    const lowToday = low(0);
    const prevClose = closeC(1);
    let trToday;
    if (Number.isNaN(prevClose)) {
      trToday = highToday - lowToday;
    } else {
      trToday = Math.max(
        highToday - lowToday,
        Math.abs(highToday - prevClose),
        Math.abs(lowToday - prevClose)
      );
    }
    if (Number.isNaN(h1InternalAtr)) {
      h1InternalTrSum += trToday;
      h1InternalTrCount += 1;
      if (h1InternalTrCount === atrPeriod) {
        h1InternalAtr = h1InternalTrSum / atrPeriod;
      }
    } else {
      h1InternalAtr = (h1InternalAtr * (atrPeriod - 1) + trToday) / atrPeriod;
    }
  }

  const manualRaw = manualRestartInput;
  const manualTriggered = manualRaw && !h1InternalPrevManualInput;

  let highBrokeReal = false;
  let lowBrokeReal = false;

  if (manualTriggered) {
    // Deliberate override: forget whatever was in progress and restart
    // from today, both sides reset to today's own high/low, immediately
    // visible. No line drawn, no structure flip, same as manual restart
    // everywhere else in this project. Pivot candidates from before this
    // candle are ignored from here on, even once they would otherwise
    // become confirmable.
    h1InternalSwingHigh = high(0);
    h1InternalSwingHighTime = currentTime;
    h1InternalHighCrossed = false;
    h1InternalSwingLow = low(0);
    h1InternalSwingLowTime = currentTime;
    h1InternalLowCrossed = false;
    h1InternalLastRestartTime = currentTime;
  } else {
    // A genuine cross is checked against whatever level was known BEFORE
    // today's own confirmation is applied, same ordering
    // fractal_detector.py uses.
    if (!Number.isNaN(h1InternalSwingHigh) && !h1InternalHighCrossed && closeToday > h1InternalSwingHigh) {
      trendLine(
        newPoint(h1InternalSwingHighTime, h1InternalSwingHigh),
        newPoint(currentTime, h1InternalSwingHigh),
        { linecolor: color.red, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      h1InternalHighCrossed = true;
      highBrokeReal = true;
    }
    if (!Number.isNaN(h1InternalSwingLow) && !h1InternalLowCrossed && closeToday < h1InternalSwingLow) {
      trendLine(
        newPoint(h1InternalSwingLowTime, h1InternalSwingLow),
        newPoint(currentTime, h1InternalSwingLow),
        { linecolor: color.green, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      h1InternalLowCrossed = true;
      lowBrokeReal = true;
    }

    // Test the candle `periods` bars back as a fractal candidate. Every
    // bar this needs already exists on the chart (it is either between
    // that candle and now, or further back than it), so this is a pure
    // lookback, not a lookahead.
    const pivotTime = time(periods);
    const pivotAllowed = h1InternalLastRestartTime === null || pivotTime >= h1InternalLastRestartTime;

    if (pivotAllowed && !Number.isNaN(pivotTime)) {
      // The significance filter is inert while ATR has not seeded, and
      // inert while no pivot exists on the opposite side to measure a leg
      // against, so the first pivot on each side can never be filtered
      // out and the detector always seeds.
      const atrKnown = filterOn && !Number.isNaN(h1InternalAtr);

      const pivotHigh = high(periods);
      const isUpFractal =
        h1InternalNearFrontierStrict(periods, pivotHigh, true, (k) => high(k)) &&
        h1InternalFarFrontierTolerant(periods, pivotHigh, true, (k) => high(k), FRACTAL_TIE_TOLERANCE);
      if (isUpFractal) {
        // Leg measured down to the last confirmed LOW: asks "was the swing
        // big enough", not "did the high move far enough".
        let keep = true;
        if (atrKnown && !Number.isNaN(h1InternalSwingLow)) {
          keep = Math.abs(pivotHigh - h1InternalSwingLow) >= minAtrSeparation * h1InternalAtr;
        }
        if (keep) {
          h1InternalSwingHigh = pivotHigh;
          h1InternalSwingHighTime = pivotTime;
          h1InternalHighCrossed = false;
        }
      }

      const pivotLow = low(periods);
      const isDownFractal =
        h1InternalNearFrontierStrict(periods, pivotLow, false, (k) => low(k)) &&
        h1InternalFarFrontierTolerant(periods, pivotLow, false, (k) => low(k), FRACTAL_TIE_TOLERANCE);
      if (isDownFractal) {
        let keep = true;
        if (atrKnown && !Number.isNaN(h1InternalSwingHigh)) {
          keep = Math.abs(pivotLow - h1InternalSwingHigh) >= minAtrSeparation * h1InternalAtr;
        }
        if (keep) {
          h1InternalSwingLow = pivotLow;
          h1InternalSwingLowTime = pivotTime;
          h1InternalLowCrossed = false;
        }
      }
    }
  }

  // A close cannot be both above h1InternalSwingHigh and below
  // h1InternalSwingLow at once, so these are mutually exclusive.
  if (highBrokeReal) {
    h1InternalStructure = 'bullish';
  } else if (lowBrokeReal) {
    h1InternalStructure = 'bearish';
  }

  h1InternalPrevManualInput = manualRaw;

  // ---- Equilibrium, appended to h1_internal_structure.py's engine.
  // equilibrium = (swingHigh + swingLow) / 2. Undetermined structure or a
  // still-warming-up range (either swing level still NaN) draws nothing.
  // ----
  const equilibrium = (h1InternalSwingHigh + h1InternalSwingLow) / 2;
  const haveEquilibrium = !Number.isNaN(equilibrium) && h1InternalStructure !== null;

  // Delete this script's own equilibrium line every closed candle before
  // redrawing, since its price can flip on any candle as price wiggles
  // around equilibrium. This is fxrscripts/README.md's proven "Design 3"
  // pattern: content-matched deletion, not id tracking, since id
  // tracking was found to leave stale drawings behind during fxreplay's
  // replay stepping. Matched on EQ_LINE_COLOR, never on shapeType alone,
  // so this cannot delete another script's horizontal line.
  deleteDrawingByCondition((drawing) => {
    const opts = drawing.overrideOptions;
    if (!opts) return false;
    return drawing.shapeType === 'horizontal_line' && opts['linecolor'] === EQ_LINE_COLOR;
  });

  if (haveEquilibrium) {
    // Real signature, triangulated from three successive editor errors
    // (not the docs' claimed horizontalLine(time, price, styles, text),
    // same class of docs-vs-editor mismatch fxrscripts/README.md's Design
    // 3 history records for linecolor/band.line): 4 args errored with
    // "Expected 1-3 arguments" (no leading time arg exists, a horizontal
    // line spans the whole chart width rather than starting at a point
    // like horizontalRay does); putting price in position 2 then errored
    // with "number has no properties in common with HorzlineLineToolOverrides"
    // (position 2 is the styles object, not a second number); adding
    // 'text' inside that styles object then errored with "'text' does not
    // exist in type HorzlineLineToolOverrides" (that type has no text
    // field at all). So the real shape is (price, styles?, text?), the
    // label as its own 3rd positional argument.
    horizontalLine(equilibrium, { linecolor: EQ_LINE_COLOR, linewidth: EQ_LINE_WIDTH }, 'EQ');
  }
};
