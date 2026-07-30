// FXR Script port of the 4H INTERNAL tier (n=8, the intermediate tier).
//
// n=8 is the one number here NOT taken from an observation on real
// charts: 2 and 20 both came from the user checking TradingView, while 8
// is picked as roughly geometric between them. Treat it as PROVISIONAL
// until the regime testing in roadmap/detection-method-decision.md runs.
// It is also the tier most likely to want the ATR separation filter,
// since a 17-candle window can sit entirely inside a tight range.
//
// Python is the source of truth: swing_structure/fractal_detector.py's
// compute_fractal_swing_structure, driven by
// swing_structure/h4_structure.py's H4_TIER_PERIODS. If this ever
// disagrees with the Python for the same candles, fix the Python first,
// then re-port.
//
// This is one of three sibling scripts that differ ONLY in their default
// `periods`, their state-variable prefix, and their line width:
//
//     h4_fractal_structure.py    n=2    linewidth 2   minor pulls
//     h4_internal_structure.py   n=8    linewidth 3   intermediate legs
//     h4_swing_structure.py      n=20   linewidth 4   major 4H legs
//
// All three run the SAME mechanism (Williams Fractal) at three scales,
// unlike the Daily set where each tier uses a different mechanism. Two
// reasons, both recorded in roadmap/detection-method-decision.md:
//
//   1. The Daily swing tier's timeout redraws its level purely because a
//      counter reached 65, with no market event behind it. Since the whole
//      point of three tiers is reading them against each other (swing
//      bullish while internal is bearish is the swing pullback phase), a
//      level that moves for bookkeeping reasons makes that reading
//      unreliable.
//   2. With one mechanism, n is a single legible scale knob. "Make the
//      middle tier coarser" is one number, not a different algorithm.
//
// THE TIERS ARE STILL FULLY INDEPENDENT. Each script has its own state,
// its own manual restart, and its own structure. None reads another's.
// Swing bullish with internal bearish with fractal bullish is the expected
// three-scale pullback cascade, not a contradiction to be fixed.
//
// Visual (same convention as the Daily internal and fractal scripts):
// nothing is shown for the current, not-yet-crossed pivot. The moment it
// is genuinely crossed (a real close beyond it, never a manual restart and
// never a silent reversal confirmation), one fixed dotted trendLine is
// drawn from the pivot's own time/price to the crossing candle, and never
// touched again. Because nothing is ever deleted during normal operation,
// there is no delete-vs-redraw problem here at all, which is why the
// timeframe isolation below reduces to a gate.

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
// on its own timeframe.
// ---------------------------------------------------------------------

const MY_TIMEFRAME_MS = 14400000;   // 4H. 86400000 in the daily scripts.
const MY_LINEWIDTH = 3;             // Also this script's deletion tag, see below.
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;

// Whether to remove this script's own drawings when the chart is not on
// its timeframe. Set from the fxrscripts/timeframe_probe.py findings: if
// fxreplay already clears an indicator's drawings when the timeframe
// changes, this is unnecessary work and can be set to false.
const TF_CLEANUP_ON_MISMATCH = true;

let h4InternalSwingHigh = NaN;
let h4InternalSwingHighTime = null;
let h4InternalHighCrossed = false;
let h4InternalSwingLow = NaN;
let h4InternalSwingLowTime = null;
let h4InternalLowCrossed = false;

let h4InternalPrevManualInput = false;
let h4InternalLastRestartTime = null;
let h4InternalLastSeenTime = null;

// Wilder's ATR, only accumulated when the significance filter is switched
// on. Mirrors swing_structure/atr.py.
let h4InternalAtr = NaN;
let h4InternalTrSum = 0;
let h4InternalTrCount = 0;

// Last inferred bar spacing, so a timeframe change can be detected and
// state reset rather than resumed on top of the other timeframe's bars.
let h4InternalLastSpacing = null;

// Bullish/bearish structure for this tier, same rule as
// swing_structure/market_structure.py: starts undetermined (null), and
// flips ONLY on a genuine cross, never on a manual restart or a silent
// reversal confirmation.
let h4InternalStructure = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  // Range goes to 100, not the Daily script's 10: the sibling swing tier
  // needs n=20, and keeping one shared range across all three scripts
  // means they stay a one-line diff from each other.
  input.int('4H Internal Periods', 8, 'periods', 2, 100, 1, 'Candles required strictly beyond the pivot, on the frontier closer to now, to confirm a fractal. This is the tier\'s scale: 2 is minor pulls, 20 is major legs.', '4H Internal Structure Settings');

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
  input.float('4H Internal ATR Separation', 0, 'minAtrSeparation', 0, 5, 0.25, 'Minimum leg size against the last opposite pivot, in ATRs. 0 disables the filter entirely.', '4H Internal Structure Settings');
  input.int('4H Internal ATR Period', 14, 'atrPeriod', 2, 200, 1, 'How many True Range values feed Wilder\'s ATR. Ignored while ATR Separation is 0.', '4H Internal Structure Settings');

  input.bool('4H Internal Manual Restart Now', false, 'manualRestartInput');
};

// True if all `periods` candles between the candidate pivot (periods bars
// back) and now are strictly beyond pivotValue. higher=true checks highs
// for an up-fractal, higher=false checks lows for a down-fractal.
//
// Note these helpers read only their arguments, never top-level state.
// That is deliberate: a helper declared alongside onTick cannot see
// top-level `let` variables (fxrscripts/README.md, resolved question 4).
const h4InternalNearFrontierStrict = (periods, pivotValue, higher, getValue) => {
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
const h4InternalFarFrontierTolerant = (periods, pivotValue, higher, getValue, tieTolerance) => {
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
    // touch h4InternalLastSpacing, so a warming-up candle never looks like
    // a timeframe change.
    return;
  }

  if (inferredSpacing !== MY_TIMEFRAME_MS) {
    // Not our chart. This is what keeps 4H markings off the Daily chart.
    if (TF_CLEANUP_ON_MISMATCH) {
      // Deletes only THIS script's drawings, matched on linewidth. Every
      // script uses a distinct width (daily tiers 1, 4H tiers 2/3/4), so
      // this cannot remove a sibling's lines. Matching on shapeType alone
      // would delete every trendLine on the chart, including the Daily
      // tiers' own.
      //
      // overrideOptions is the DrawingOverrides union across every drawing
      // tool, so dotted access (.linewidth) is a type error, per
      // fxrscripts/README.md Design 3. Bracket access is used to sidestep
      // that. If the editor still rejects it, the fallback is to give the
      // 4H scripts a different shapeType from the Daily ones (rayLine
      // rather than trendLine) and match on shapeType instead.
      deleteDrawingByCondition((drawing) => {
        const opts = drawing.overrideOptions;
        if (!opts) return false;
        return opts['linewidth'] === MY_LINEWIDTH;
      });
    }
    h4InternalLastSpacing = inferredSpacing;
    return;
  }

  if (inferredSpacing !== h4InternalLastSpacing) {
    // Arrived on our timeframe from somewhere else. Reset everything, so
    // the state machine does not resume on top of state accumulated from
    // the other timeframe's candles. Inlined rather than factored into a
    // helper because a helper cannot see top-level state.
    h4InternalSwingHigh = NaN;
    h4InternalSwingHighTime = null;
    h4InternalHighCrossed = false;
    h4InternalSwingLow = NaN;
    h4InternalSwingLowTime = null;
    h4InternalLowCrossed = false;
    h4InternalPrevManualInput = false;
    h4InternalLastRestartTime = null;
    h4InternalLastSeenTime = null;
    h4InternalAtr = NaN;
    h4InternalTrSum = 0;
    h4InternalTrCount = 0;
    h4InternalStructure = null;
    h4InternalLastSpacing = inferredSpacing;
  }

  // ---- One state-machine update per closed candle, gated on that
  // candle's own timestamp rather than `length` (see
  // daily_swing_structure.py for why). ----
  const currentTime = time(0);
  if (currentTime === h4InternalLastSeenTime) {
    return;
  }
  h4InternalLastSeenTime = currentTime;

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
    if (Number.isNaN(h4InternalAtr)) {
      h4InternalTrSum += trToday;
      h4InternalTrCount += 1;
      if (h4InternalTrCount === atrPeriod) {
        h4InternalAtr = h4InternalTrSum / atrPeriod;
      }
    } else {
      h4InternalAtr = (h4InternalAtr * (atrPeriod - 1) + trToday) / atrPeriod;
    }
  }

  const manualRaw = manualRestartInput;
  const manualTriggered = manualRaw && !h4InternalPrevManualInput;

  let highBrokeReal = false;
  let lowBrokeReal = false;

  if (manualTriggered) {
    // Deliberate override: forget whatever was in progress and restart
    // from today, both sides reset to today's own high/low, immediately
    // visible. No line drawn, no structure flip, same as manual restart
    // everywhere else in this project. Pivot candidates from before this
    // candle are ignored from here on, even once they would otherwise
    // become confirmable.
    h4InternalSwingHigh = high(0);
    h4InternalSwingHighTime = currentTime;
    h4InternalHighCrossed = false;
    h4InternalSwingLow = low(0);
    h4InternalSwingLowTime = currentTime;
    h4InternalLowCrossed = false;
    h4InternalLastRestartTime = currentTime;
  } else {
    // A genuine cross is checked against whatever level was known BEFORE
    // today's own confirmation is applied, same ordering
    // fractal_detector.py uses.
    if (!Number.isNaN(h4InternalSwingHigh) && !h4InternalHighCrossed && closeToday > h4InternalSwingHigh) {
      trendLine(
        newPoint(h4InternalSwingHighTime, h4InternalSwingHigh),
        newPoint(currentTime, h4InternalSwingHigh),
        { linecolor: color.red, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      h4InternalHighCrossed = true;
      highBrokeReal = true;
    }
    if (!Number.isNaN(h4InternalSwingLow) && !h4InternalLowCrossed && closeToday < h4InternalSwingLow) {
      trendLine(
        newPoint(h4InternalSwingLowTime, h4InternalSwingLow),
        newPoint(currentTime, h4InternalSwingLow),
        { linecolor: color.green, linewidth: MY_LINEWIDTH, linestyle: 1 }
      );
      h4InternalLowCrossed = true;
      lowBrokeReal = true;
    }

    // Test the candle `periods` bars back as a fractal candidate. Every
    // bar this needs already exists on the chart (it is either between
    // that candle and now, or further back than it), so this is a pure
    // lookback, not a lookahead.
    const pivotTime = time(periods);
    const pivotAllowed = h4InternalLastRestartTime === null || pivotTime >= h4InternalLastRestartTime;

    if (pivotAllowed && !Number.isNaN(pivotTime)) {
      // The significance filter is inert while ATR has not seeded, and
      // inert while no pivot exists on the opposite side to measure a leg
      // against, so the first pivot on each side can never be filtered
      // out and the detector always seeds.
      const atrKnown = filterOn && !Number.isNaN(h4InternalAtr);

      const pivotHigh = high(periods);
      const isUpFractal =
        h4InternalNearFrontierStrict(periods, pivotHigh, true, (k) => high(k)) &&
        h4InternalFarFrontierTolerant(periods, pivotHigh, true, (k) => high(k), FRACTAL_TIE_TOLERANCE);
      if (isUpFractal) {
        // Leg measured down to the last confirmed LOW: asks "was the swing
        // big enough", not "did the high move far enough".
        let keep = true;
        if (atrKnown && !Number.isNaN(h4InternalSwingLow)) {
          keep = Math.abs(pivotHigh - h4InternalSwingLow) >= minAtrSeparation * h4InternalAtr;
        }
        if (keep) {
          h4InternalSwingHigh = pivotHigh;
          h4InternalSwingHighTime = pivotTime;
          h4InternalHighCrossed = false;
        }
      }

      const pivotLow = low(periods);
      const isDownFractal =
        h4InternalNearFrontierStrict(periods, pivotLow, false, (k) => low(k)) &&
        h4InternalFarFrontierTolerant(periods, pivotLow, false, (k) => low(k), FRACTAL_TIE_TOLERANCE);
      if (isDownFractal) {
        let keep = true;
        if (atrKnown && !Number.isNaN(h4InternalSwingHigh)) {
          keep = Math.abs(pivotLow - h4InternalSwingHigh) >= minAtrSeparation * h4InternalAtr;
        }
        if (keep) {
          h4InternalSwingLow = pivotLow;
          h4InternalSwingLowTime = pivotTime;
          h4InternalLowCrossed = false;
        }
      }
    }
  }

  // A close cannot be both above h4InternalSwingHigh and below
  // h4InternalSwingLow at once, so these are mutually exclusive.
  if (highBrokeReal) {
    h4InternalStructure = 'bullish';
  } else if (lowBrokeReal) {
    h4InternalStructure = 'bearish';
  }

  h4InternalPrevManualInput = manualRaw;
};
