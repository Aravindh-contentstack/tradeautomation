// THROWAWAY SPIKE. Not a structure indicator. Delete once its answer is
// recorded in fxrscripts/README.md under "Timeframe isolation" (or
// wherever the mtf findings end up living).
//
// THE EXACT QUESTION THIS ANSWERS:
//
//   Does an mtf-bound recomputation of a PARENT timeframe's tier
//   structure match that timeframe's OWN NATIVE script?
//
// Concretely: the upcoming order-block port needs the 4H script to
// recompute Daily's swing_high/swing_low purely from mtf.* accessors
// (mtf.high/mtf.low/mtf.closeC/mtf.time bound to '1D' via
// mtf.timeframe('1D')), because the within_daily_ob containment factor
// has to know Daily's tier structure while running on a 4H chart, and
// FXR Script has no import mechanism to just reuse daily_swing_structure.py
// directly. That only works if mtf-fed Williams Fractal detection produces
// the SAME swing_high/swing_low values daily_swing_structure.py's own
// native time()/high()/low()/closeC() calls produce, running on an actual
// Daily chart. That equivalence has never been tested in this codebase.
// mtf.* is documented as BETA (fx-replay-docs/multi-timeframe/), and the
// only worked example there (mft-example.md) uses it for nothing more
// than a two-candle FVG rectangle — never for a stateful multi-bar
// lookback like a Williams Fractal, so there is no precedent to lean on.
//
// Three unknowns, because guessing wrong on any of them means the
// within_daily_ob factor is built on a foundation that silently drifts
// from Daily's own chart:
//
//   Q1. Do mtf.high(k)/mtf.low(k) at large k (this probe needs indices up
//       to periods*2 + tieTolerance = 44) actually return the same OHLC
//       history a native Daily chart shows, or does mtf's lookback run out
//       / return NaN / return something desynced sooner than a real
//       Daily chart's own bars would?
//   Q2. Does mtf.time(0, false) (stepped, not interpolated) advance
//       EXACTLY once per real Daily candle close, in lockstep with a
//       genuine Daily chart's time(0) — or does it skip, repeat, or lag
//       by a bar because the 4H chart's own tick cadence doesn't line up
//       with Daily closes?
//   Q3. Given Q1 and Q2 hold well enough to run the algorithm at all, do
//       the resulting swing_high/swing_low PRICES match the native
//       script's, or does smoothing/interpolation in the underlying mtf
//       candle feed shift a pivot's value even when its timing lines up?
//
// HOW TO READ THE RESULT
//
// Load this alone on a 4H chart. Load fxrscripts/daily_swing_structure.py
// alone on a Daily chart of the SAME symbol, same replay position. Then
// compare:
//
//   - The two plotted lines below ("MTF Daily SwingHigh (via 4H)" and
//     "MTF Daily SwingLow (via 4H)") against the price levels
//     daily_swing_structure.py's own white trendLine segments sit at.
//   - The on-chart label's mtfHigh=/mtfLow=/mtfTicks= readout: mtfTicks
//     should climb by exactly 1 each time the Daily chart advances one
//     candle (read this by counting Daily candles that close while
//     watching the label update) — a mismatch is a Q2 failure.
//   - If mtfHigh/mtfLow never move at all despite plenty of Daily
//     history on the 4H chart, mtf's lookback likely ran out (Q1) or
//     mtf.time(0,false) never advanced (Q2) — check `mtfTicks` first to
//     tell which.
//   - If mtfTicks climbs correctly but the price values still disagree
//     with daily_swing_structure.py's own segments, that's a Q3 failure:
//     the algorithm and its timing are fine, the underlying mtf price
//     feed itself is off.
//
// A clean match on all three across a real back-and-forth (scrub the
// replay, don't just eyeball one static frame) is what would justify
// building within_daily_ob on mtf.* rather than on some other mechanism.

const FRACTAL_TIE_TOLERANCE = 4;

// ---------------------------------------------------------------------
// CHART TIMEFRAME GATE. This probe is only meaningful while the CHART
// itself is on 4H (mtf.timeframe('1D') is requesting Daily data to be
// read FROM a 4H chart — the whole point of the test). Copied from the
// same inference block used in h4_swing_structure.py, unchanged, because
// FXR Script still cannot read the chart's own timeframe directly.
// ---------------------------------------------------------------------

const H4_MS = 14400000;
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;

let mtfProbeLastChartSpacing = null;

// The recomputed Daily tier state, built ENTIRELY from mtf.* accessors.
// Mirrors h4_swing_structure.py's dailySwing*/h4Swing* state one-for-one,
// renamed to make clear this is the mtf-fed copy, not a native tier.
let mtfSwingHigh = NaN;
let mtfSwingHighTime = null;
let mtfHighCrossed = false;
let mtfSwingLow = NaN;
let mtfSwingLowTime = null;
let mtfLowCrossed = false;

// Q2 instrumentation: last Daily mtf timestamp seen, and how many DISTINCT
// Daily mtf candles have been processed. If mtfTicks stops climbing while
// the Daily chart keeps producing candles, mtf.time(0,false) is not
// advancing in step with real Daily closes.
let mtfLastSeenTime = null;
let mtfTicks = 0;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  // Call mtf.timeframe ONCE here, never inside onTick, per
  // fx-replay-docs/multi-timeframe/multi-timerame.md's explicit warning.
  mtf.timeframe('1D');

  // n=20, matching daily_swing_structure.py's own default 'periods' input
  // exactly (this probe hardcodes it rather than exposing an input, since
  // the only thing under test is whether mtf reproduces THAT specific
  // tier, not whether the tier is reconfigurable).
};

// Same two frontier checks as h4_swing_structure.py / daily_swing_structure.py,
// copied VERBATIM in shape. The only change anywhere in this file relative
// to those two scripts is which accessor getValue closes over at the call
// site below (mtf.high/mtf.low instead of high/low) — the algorithm itself
// is untouched, which is the whole point of the test.
//
// Kept accessor-agnostic (getValue passed in) rather than hardcoding mtf.*
// inside these helpers, for the same reason the source scripts pass
// getValue in: a helper declared alongside onTick cannot see top-level
// state, but CAN see whatever function value is handed to it as an
// argument.
const mtfProbeNearFrontierStrict = (periods, pivotValue, higher, getValue) => {
  for (let t = 1; t <= periods; t++) {
    const v = getValue(periods - t);
    if (Number.isNaN(v)) return false;
    if (higher && !(v < pivotValue)) return false;
    if (!higher && !(v > pivotValue)) return false;
  }
  return true;
};

const mtfProbeFarFrontierTolerant = (periods, pivotValue, higher, getValue, tieTolerance) => {
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
  const periods = 20; // matches daily_swing_structure.py's default exactly

  // ---- Chart timeframe gate. Same MINIMUM-positive-delta inference as
  // every other structure script (fxrscripts/README.md, resolved question
  // 4: this must be inlined, not a helper, because a helper cannot see
  // top-level const/let). This probe only draws while the CHART is 4H;
  // it is not testing what happens on other chart timeframes. ----
  let inferredChartSpacing = null;
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
    inferredChartSpacing = valid < TF_MIN_VALID_BARS ? null : spacing;
  }

  if (inferredChartSpacing === null) {
    return;
  }
  if (inferredChartSpacing !== H4_MS) {
    // Not a 4H chart. This probe has nothing to say here — load it on 4H.
    mtfProbeLastChartSpacing = inferredChartSpacing;
    return;
  }
  mtfProbeLastChartSpacing = inferredChartSpacing;

  // ---- Q2 instrumentation + new-Daily-bar gate. mtf.time(0, false) is
  // STEPPED (per fx-replay-docs/multi-timeframe/multi-timerame.md:
  // smooth=false "updates only when the MTF candle closes"), so it should
  // hold steady across every 4H tick within the same forming Daily
  // candle, then jump exactly once per real Daily close. Gating on it
  // changing is the mtf equivalent of the native scripts' `currentTime
  // === ...LastSeenTime` new-bar gate. ----
  const currentMtfTime = mtf.time(0, false);
  const mtfCloseC0 = mtf.closeC(0, false);

  // Always plot, every 4H tick, even between Daily closes — otherwise the
  // line only has points once every ~6 ticks and is hard to read.
  plot.line('MTF Daily SwingHigh (via 4H)', mtfSwingHigh, color.orange, 0);
  plot.line('MTF Daily SwingLow (via 4H)', mtfSwingLow, color.aqua, 0);

  if (currentMtfTime === mtfLastSeenTime) {
    return;
  }
  mtfLastSeenTime = currentMtfTime;
  mtfTicks = mtfTicks + 1;

  // ---- Crossing check, same ordering as h4_swing_structure.py: tested
  // against whatever level was already known BEFORE this Daily candle's
  // own confirmation is applied below. ----
  if (!Number.isNaN(mtfSwingHigh) && !mtfHighCrossed && mtfCloseC0 > mtfSwingHigh) {
    mtfHighCrossed = true;
  }
  if (!Number.isNaN(mtfSwingLow) && !mtfLowCrossed && mtfCloseC0 < mtfSwingLow) {
    mtfLowCrossed = true;
  }

  // ---- Fractal pivot test, `periods` Daily mtf candles back. Needs mtf
  // lookback as far as periods + tieTolerance + periods = 44 Daily
  // candles, which is Q1: does mtf.high/low actually resolve that far
  // back from a 4H chart? ----
  const pivotTime = mtf.time(periods, false);
  if (!Number.isNaN(pivotTime)) {
    const pivotHigh = mtf.high(periods, false);
    const isUpFractal =
      mtfProbeNearFrontierStrict(periods, pivotHigh, true, (k) => mtf.high(k, false)) &&
      mtfProbeFarFrontierTolerant(periods, pivotHigh, true, (k) => mtf.high(k, false), FRACTAL_TIE_TOLERANCE);
    if (isUpFractal) {
      mtfSwingHigh = pivotHigh;
      mtfSwingHighTime = pivotTime;
      mtfHighCrossed = false;
    }

    const pivotLow = mtf.low(periods, false);
    const isDownFractal =
      mtfProbeNearFrontierStrict(periods, pivotLow, false, (k) => mtf.low(k, false)) &&
      mtfProbeFarFrontierTolerant(periods, pivotLow, false, (k) => mtf.low(k, false), FRACTAL_TIE_TOLERANCE);
    if (isDownFractal) {
      mtfSwingLow = pivotLow;
      mtfSwingLowTime = pivotTime;
      mtfLowCrossed = false;
    }
  }

  // ---- Readable label, one per new Daily mtf candle, so the comparison
  // in the header can be read straight off the chart without hovering
  // over the plotted lines. ----
  const label =
    'mtfTicks=' + String(mtfTicks) +
    ' mtfHigh=' + (Number.isNaN(mtfSwingHigh) ? 'NaN' : String(mtfSwingHigh)) +
    ' mtfLow=' + (Number.isNaN(mtfSwingLow) ? 'NaN' : String(mtfSwingLow)) +
    ' pivotTimeOk=' + String(!Number.isNaN(pivotTime));

  trendLine(
    newPoint(time(1), closeC(1)),
    newPoint(time(0), closeC(0)),
    { linecolor: color.purple, linewidth: 6, linestyle: 1 },
    label
  );
};
