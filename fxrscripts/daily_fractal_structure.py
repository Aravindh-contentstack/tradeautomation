// FXR Script port of swing_structure/fractal_structure.py, powered by
// swing_structure/fractal_detector.py's compute_fractal_swing_structure
// (Williams Fractal, n=2 by default, a bar-for-bar port of
// temp-reference/fractal/williams_fractal.py's default-values behavior).
// Python stays the source of truth. If this ever disagrees with it for
// the same candles, fix fractal_detector.py first, then re-port.
//
// Unlike the ATR-based zigzag daily_internal_structure.py ports, a
// Williams Fractal confirms purely from candle shape: the candle n bars
// back from "now" is a pivot if it stands out from n candles on each
// side of it. No ATR, no bootstrap, no running-extreme tracking, and no
// break-first gate: every newly confirmed fractal on a side immediately
// replaces the previous one, whether or not the level in between ever
// broke. This makes fractal pivots far more frequent than either the
// swing or internal tier, by design.
//
// Crucially, testing the candle n bars back does NOT require unseen
// future data: every bar that check needs (both the n candles further
// back than it, and the n candles closer to "now" than it) is already on
// the chart by the time the current tick runs, exactly mirroring
// williams_fractal.py's own offset=-n plotshape. That is why this port
// needs no buffering of upcoming candles the way a naive "wait for the
// future" reading of the Python version's docstring might suggest.
//
// Tie handling is ported bar-for-bar from williams_fractal.py, not
// simplified to a textbook symmetric fractal: the frontier closer to
// "now" (upflagDownFrontier / downflagDownFrontier in the Pine reference)
// must be strictly beyond the pivot with no ties tolerated, but the
// frontier further back (upflagUpFrontier0..4 / downflagUpFrontier0..4)
// tolerates a run of up to 4 candles tied with the pivot's own high/low
// before requiring a strict move away from it.
//
// Visual (same convention as daily_internal_structure.py): nothing is
// shown for the current, not-yet-crossed pivot. The moment it's
// genuinely crossed (a real close beyond it, never a manual restart or a
// silent reversal confirmation), one fixed dotted trendLine segment is
// drawn from the pivot's own time/price to the crossing candle's time,
// and it is never touched again afterward.

const FRACTAL_TIE_TOLERANCE = 4;

// ---------------------------------------------------------------------
// TIMEFRAME ISOLATION. This block is duplicated VERBATIM (bar the two
// constants) in all nine structure scripts: daily_swing_structure.py,
// daily_internal_structure.py, daily_fractal_structure.py,
// h4_fractal_structure.py, h4_internal_structure.py, h4_swing_structure.py,
// h1_fractal_structure.py, h1_internal_structure.py, h1_swing_structure.py.
// FXR Script has no import mechanism, so a change here has to be applied
// to all nine by hand.
//
// FXR Script CANNOT read the chart's current timeframe. There is no
// `timeframe`, `period`, `resolution` or `syminfo` global, indicator()
// takes only onMainPanel/format/precision, input.timeframe reports what
// the user picked rather than what the chart is on, and mtf.* reads OTHER
// timeframes but cannot report the chart's own. So the timeframe is
// INFERRED from bar spacing, and the script hides itself when it is not
// on its own timeframe. Without this, switching the chart to 4H would
// leave the Daily levels drawn on top of the 4H ones.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 86400000;   // Daily. 14400000 in the 4H scripts.
const MY_LINEWIDTH = 1;             // Also this script's deletion tag.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;
const TF_CLEANUP_ON_MISMATCH = true;

let fractalLastSpacing = null;

let fractalSwingHigh = NaN;
let fractalSwingHighTime = null;
let fractalHighCrossed = false;
let fractalSwingLow = NaN;
let fractalSwingLowTime = null;
let fractalLowCrossed = false;

let fractalPrevManualInput = false;
let fractalLastRestartTime = null;
let fractalLastSeenTime = null;

// Wilder's ATR, only accumulated when the significance filter is switched
// on. Mirrors swing_structure/atr.py. Untouched on the default path.
let fractalAtr = NaN;
let fractalTrSum = 0;
let fractalTrCount = 0;

// Bullish/bearish structure for this tier, same rule as
// swing_structure/market_structure.py: starts undetermined (null), and
// flips ONLY on a genuine cross, never on a manual restart or a silent
// reversal confirmation.
let fractalStructure = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  // Range widened from 10 to 100 to match the three 4H sibling scripts,
  // which need n=20 for their swing tier. The Daily default stays 2, so
  // nothing changes unless it is deliberately raised.
  input.int('Fractal Periods', 2, 'periods', 2, 100, 1, 'Candles required strictly beyond the pivot, on the frontier closer to now, to confirm a fractal.', 'Fractal Structure Settings');

  // OFF by default (0), and expected to stay off. Rejects a newly
  // confirmed pivot unless its distance from the last confirmed pivot on
  // the OPPOSITE side is at least this many ATRs, which makes it a
  // leg-size test rather than a displacement test. Present here only so
  // this script stays a one-line diff from its 4H siblings, and so that
  // enabling it later is a settings change rather than a re-port.
  //
  // WARNING, confirmed in scripts/verify_tier_nesting.py: this is NOT a
  // monotonic dial. The opposite-side pivot it measures against is itself
  // filtered, so a larger threshold can re-admit pivots that a smaller one
  // rejected. Expect to search values rather than turn it one way.
  input.float('Fractal ATR Separation', 0, 'minAtrSeparation', 0, 5, 0.25, 'Minimum leg size against the last opposite pivot, in ATRs. 0 disables the filter entirely.', 'Fractal Structure Settings');
  input.int('Fractal ATR Period', 14, 'atrPeriod', 2, 200, 1, 'How many True Range values feed Wilder\'s ATR. Ignored while ATR Separation is 0.', 'Fractal Structure Settings');

  input.bool('Fractal Manual Restart Now', false, 'manualRestartInput');
};

// True if all `periods` candles between the candidate pivot (periods bars
// back) and now are strictly beyond pivotValue. higher=true checks highs
// for an up-fractal (down frontier), higher=false checks lows for a
// down-fractal (up frontier's mirror on the near side).
const fractalNearFrontierStrict = (periods, pivotValue, higher, getValue) => {
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
// before it tied with pivotValue itself.
//
// tieTolerance is passed in rather than read from the top-level
// FRACTAL_TIE_TOLERANCE constant: a helper declared alongside onTick
// cannot see ANY top-level declaration, let or const
// (fxrscripts/README.md, resolved question 4, confirmed the hard way when
// this exact pattern threw "FRACTAL_TIE_TOLERANCE is not defined" at
// runtime despite the constant being declared earlier in the same file).
const fractalFarFrontierTolerant = (periods, pivotValue, higher, getValue, tieTolerance) => {
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
  // the true bar interval and is gap-proof by construction. That matters
  // most on exactly this timeframe: the Friday-to-Monday delta on a Daily
  // forex chart is three days, but plenty of midweek deltas are exactly
  // one day, so the minimum still lands on 86400000. ----
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
    // touch fractalLastSpacing, so a warming-up candle never looks like a
    // timeframe change.
    return;
  }

  if (inferredSpacing !== MY_TIMEFRAME_MS) {
    // Not our chart. This is what keeps Daily markings off the 4H chart.
    if (TF_CLEANUP_ON_MISMATCH) {
      // Deletes only THIS timeframe's trendLines, matched on linewidth.
      // The Daily tiers all draw at width 1 and the 4H tiers at 2, 3 and 4,
      // so this cannot remove a 4H sibling's lines. Matching on shapeType
      // alone would delete every trendLine on the chart.
      //
      // overrideOptions is the DrawingOverrides union across every drawing
      // tool, so dotted access (.linewidth) is a type error, per README
      // Design 3. Bracket access is used to sidestep that. If the editor
      // still rejects it, the fallback is to give the 4H scripts a
      // different shapeType (rayLine rather than trendLine) and match on
      // shapeType instead.
      deleteDrawingByCondition((drawing) => {
        const opts = drawing.overrideOptions;
        if (!opts) return false;
        return opts['linewidth'] === MY_LINEWIDTH;
      });
    }
    fractalLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== fractalLastSpacing) {
    // Arrived on our timeframe from somewhere else. Reset everything, so
    // the state machine does not resume on top of state accumulated from
    // the other timeframe's candles. Inlined rather than factored into a
    // helper because a helper cannot see top-level state.
    fractalSwingHigh = NaN;
    fractalSwingHighTime = null;
    fractalHighCrossed = false;
    fractalSwingLow = NaN;
    fractalSwingLowTime = null;
    fractalLowCrossed = false;
    fractalPrevManualInput = false;
    fractalLastRestartTime = null;
    fractalLastSeenTime = null;
    fractalStructure = null;
    fractalAtr = NaN;
    fractalTrSum = 0;
    fractalTrCount = 0;
    fractalLastSpacing = inferredSpacing;
  }

  // One state-machine update per closed candle, gated on that candle's own
  // timestamp rather than `length` (see daily_swing_structure.py for why).
  const currentTime = time(0);
  if (currentTime === fractalLastSeenTime) {
    return;
  }
  fractalLastSeenTime = currentTime;

  const closeToday = closeC(0);

  // ---- Wilder's ATR, only when the significance filter is switched on, so
  // the default path does exactly the work it did before the filter
  // existed. Mirrors swing_structure/atr.py, including the
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
    if (Number.isNaN(fractalAtr)) {
      fractalTrSum += trToday;
      fractalTrCount += 1;
      if (fractalTrCount === atrPeriod) {
        fractalAtr = fractalTrSum / atrPeriod;
      }
    } else {
      fractalAtr = (fractalAtr * (atrPeriod - 1) + trToday) / atrPeriod;
    }
  }

  const manualRaw = manualRestartInput;
  const manualTriggered = manualRaw && !fractalPrevManualInput;

  let highBrokeReal = false;
  let lowBrokeReal = false;

  if (manualTriggered) {
    // Deliberate override: forget whatever was in progress and restart
    // fractal tracking from today, both sides reset to today's own
    // high/low, immediately visible. No line drawn, no structure flip,
    // same as manual restart everywhere else in this project. Pivot
    // candidates from before this candle are ignored from here on, even
    // once they'd otherwise become confirmable.
    fractalSwingHigh = high(0);
    fractalSwingHighTime = currentTime;
    fractalHighCrossed = false;
    fractalSwingLow = low(0);
    fractalSwingLowTime = currentTime;
    fractalLowCrossed = false;
    fractalLastRestartTime = currentTime;
  } else {
    // A genuine cross is checked against whatever level was known BEFORE
    // today's own confirmation is applied, same ordering
    // fractal_detector.py uses.
    if (!Number.isNaN(fractalSwingHigh) && !fractalHighCrossed && closeToday > fractalSwingHigh) {
      trendLine(
        newPoint(fractalSwingHighTime, fractalSwingHigh),
        newPoint(currentTime, fractalSwingHigh),
        { linecolor: color.red, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      fractalHighCrossed = true;
      highBrokeReal = true;
    }
    if (!Number.isNaN(fractalSwingLow) && !fractalLowCrossed && closeToday < fractalSwingLow) {
      trendLine(
        newPoint(fractalSwingLowTime, fractalSwingLow),
        newPoint(currentTime, fractalSwingLow),
        { linecolor: color.green, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      fractalLowCrossed = true;
      lowBrokeReal = true;
    }

    // Test the candle `periods` bars back as a fractal candidate. Every
    // bar this needs already exists on the chart (it's between that
    // candle and now, or further back than it), so this is a pure
    // lookback, not a lookahead.
    const pivotTime = time(periods);
    const pivotAllowed = fractalLastRestartTime === null || pivotTime >= fractalLastRestartTime;

    if (pivotAllowed && !Number.isNaN(pivotTime)) {
      // The significance filter is inert while ATR has not seeded, and
      // inert while no pivot exists on the opposite side to measure a leg
      // against, so the first pivot on each side can never be filtered out
      // and the detector always seeds.
      const atrKnown = filterOn && !Number.isNaN(fractalAtr);

      const pivotHigh = high(periods);
      const isUpFractal =
        fractalNearFrontierStrict(periods, pivotHigh, true, (k) => high(k)) &&
        fractalFarFrontierTolerant(periods, pivotHigh, true, (k) => high(k), FRACTAL_TIE_TOLERANCE);
      if (isUpFractal) {
        // Leg measured down to the last confirmed LOW: asks "was the swing
        // big enough", not "did the high move far enough".
        let keepHigh = true;
        if (atrKnown && !Number.isNaN(fractalSwingLow)) {
          keepHigh = Math.abs(pivotHigh - fractalSwingLow) >= minAtrSeparation * fractalAtr;
        }
        if (keepHigh) {
          fractalSwingHigh = pivotHigh;
          fractalSwingHighTime = pivotTime;
          fractalHighCrossed = false;
        }
      }

      const pivotLow = low(periods);
      const isDownFractal =
        fractalNearFrontierStrict(periods, pivotLow, false, (k) => low(k)) &&
        fractalFarFrontierTolerant(periods, pivotLow, false, (k) => low(k), FRACTAL_TIE_TOLERANCE);
      if (isDownFractal) {
        let keepLow = true;
        if (atrKnown && !Number.isNaN(fractalSwingHigh)) {
          keepLow = Math.abs(pivotLow - fractalSwingHigh) >= minAtrSeparation * fractalAtr;
        }
        if (keepLow) {
          fractalSwingLow = pivotLow;
          fractalSwingLowTime = pivotTime;
          fractalLowCrossed = false;
        }
      }
    }
  }

  // A close can't be both above fractalSwingHigh and below
  // fractalSwingLow at once, so these are mutually exclusive.
  if (highBrokeReal) {
    fractalStructure = 'bullish';
  } else if (lowBrokeReal) {
    fractalStructure = 'bearish';
  }

  fractalPrevManualInput = manualRaw;
};
