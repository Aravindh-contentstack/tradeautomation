// THROWAWAY SPIKE. Not a structure indicator. Delete once its three
// answers are recorded in fxrscripts/README.md under "Timeframe isolation".
//
// FXR Script has no way to read the chart's current timeframe. Verified
// against the live docs: there is no `timeframe`, `period`, `resolution`
// or `syminfo` global, `indicator()` accepts only onMainPanel/format/
// precision, `input.timeframe` is a UI selector that reports what the
// USER picked rather than what the chart is on, and `mtf.*` reads OTHER
// timeframes but cannot report the chart's own.
//
// So the 4H structure scripts have to INFER the chart timeframe from bar
// spacing, and hide themselves when it isn't theirs. Before writing six
// scripts around that idea, three unknowns need answering, because
// guessing wrong on any of them means rewriting all six:
//
//   Q1. On a chart timeframe change, are the previous timeframe's
//       script-drawn drawings cleared automatically, or left behind?
//       THE MOST IMPORTANT ONE. If they are cleared, the timeframe gate
//       alone achieves full isolation and no deletion code is needed
//       anywhere in any script.
//
//   Q2. Does top-level `let` state survive a timeframe change? If it
//       does, every script needs an explicit reset when the inferred
//       spacing changes, or Daily -> 4H -> Daily resumes on top of state
//       accumulated from the other timeframe's bars.
//
//   Q3. Does drawing.overrideOptions['linewidth'] read without a type
//       error inside deleteDrawingByCondition? Bracket access may dodge
//       the DrawingOverrides union error that defeated `linecolor` in
//       README Design 3. If it works, linewidth becomes a per-timeframe
//       tag and gives precise self-only deletion.
//
// HOW TO READ THE RESULT
//
// Load this alone on a chart, then switch the chart between Daily and
// 4H a few times and read the labels on the drawn segments.
//
//   Q1: after switching Daily -> 4H, are the segments labelled "TF=1D"
//       still on the chart? Still there = drawings are LEFT BEHIND.
//       Gone = AUTO-CLEARED.
//   Q2: read `ticks` in the label. If it keeps climbing across a
//       timeframe switch instead of restarting near 0, state SURVIVES.
//       `prevTF` in the label shows the spacing seen before this one, so
//       a label reading "TF=4h prevTF=1D" is direct proof state carried
//       across the switch.
//   Q3: read `lwProbe` in the label. "lwProbe=ok:<n>" means bracket
//       access worked and returned a value. "lwProbe=undef" means it
//       read without erroring but the field wasn't present. If the
//       script fails to compile or throws, Q3 is a NO and the comment
//       below the probe explains the fallback.

const DAILY_MS = 86400000;
const H4_MS = 14400000;
const H1_MS = 3600000;
const TF_PROBE_BARS = 20;
const TF_MIN_VALID_BARS = 5;

// Q2 instrumentation: if these keep their values across a timeframe
// switch, top-level state survives it.
let probeTicks = 0;
let probeLastSpacing = null;
let probePrevSpacing = null;
let probeLastSeenTime = null;

// Q3 instrumentation: what the bracket-access read produced, as text.
let probeLwResult = 'notrun';

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
};

// Names the spacing for the label. Anything unrecognised is reported as
// its raw millisecond value so an unexpected chart (weekly, 15m) is
// still readable rather than silently bucketed as "other".
//
// Inlined directly at each call site inside onTick rather than kept as a
// separate top-level helper: a helper declared alongside onTick cannot
// see ANY top-level declaration, let or const (fxrscripts/README.md,
// resolved question 4, confirmed the hard way when this exact pattern
// threw "DAILY_MS is not defined" at runtime despite the constant being
// declared earlier in the same file).
const probeSpacingName = (ms, dailyMs, h4Ms, h1Ms) => {
  if (ms === dailyMs) return '1D';
  if (ms === h4Ms) return '4h';
  if (ms === h1Ms) return '1h';
  return String(ms);
};

onTick = (length, _moment, _, ta, inputs) => {
  const currentTime = time(0);
  if (currentTime === probeLastSeenTime) {
    return;
  }
  probeLastSeenTime = currentTime;
  probeTicks = probeTicks + 1;

  // ---- The inference rule the real scripts will use ----
  // MINIMUM positive delta over the last TF_PROBE_BARS bars, not the
  // median and not time(0)-time(1). Session and weekend gaps only ever
  // make a delta LARGER, never smaller, so the minimum is the true bar
  // interval and is gap-proof by construction. On a Daily forex chart
  // the Fri->Mon delta is 3 days, but plenty of Mon->Tue deltas are
  // exactly one day, so the minimum still lands on 86400000.
  let inferredSpacing = null;
  let validDeltas = 0;
  for (let i = 0; i < TF_PROBE_BARS; i++) {
    const newer = time(i);
    const older = time(i + 1);
    if (Number.isNaN(newer) || Number.isNaN(older)) break;
    const delta = newer - older;
    if (delta <= 0) continue;
    validDeltas = validDeltas + 1;
    if (inferredSpacing === null || delta < inferredSpacing) {
      inferredSpacing = delta;
    }
  }

  if (inferredSpacing === null || validDeltas < TF_MIN_VALID_BARS) {
    // Not enough history to decide yet. Draw nothing and, importantly,
    // do NOT touch probePrevSpacing: a warming-up bar must not look
    // like a timeframe change to the real scripts.
    return;
  }

  if (inferredSpacing !== probeLastSpacing) {
    probePrevSpacing = probeLastSpacing;
    probeLastSpacing = inferredSpacing;
  }

  // ---- Q3: can overrideOptions be read at all, via bracket access? ----
  // README Design 3 records that `overrideOptions.linecolor` errored,
  // because overrideOptions is the DrawingOverrides union across every
  // drawing tool and linecolor exists on only some members. Bracket
  // access may sidestep that. This deletes nothing (the predicate always
  // returns false), it only reads, so it is safe to run every tick.
  probeLwResult = 'undef';
  deleteDrawingByCondition((drawing) => {
    const opts = drawing.overrideOptions;
    if (opts) {
      const lw = opts['linewidth'];
      if (lw !== undefined && lw !== null) {
        probeLwResult = 'ok:' + String(lw);
      }
    }
    return false;
  });

  // ---- Draw one labelled segment per bar so the answers are visible ----
  // Deliberately NEVER deleted, so Q1 is answered by observation: if
  // "TF=1D" segments are still on the chart after switching to 4H, then
  // drawings are left behind across a timeframe change and the real
  // scripts need self-only cleanup rather than a gate alone.
  const tfName = probeSpacingName(inferredSpacing, DAILY_MS, H4_MS, H1_MS);
  const prevName = probePrevSpacing === null ? 'none' : probeSpacingName(probePrevSpacing, DAILY_MS, H4_MS, H1_MS);
  const label =
    'TF=' + tfName +
    ' prevTF=' + prevName +
    ' ticks=' + String(probeTicks) +
    ' deltas=' + String(validDeltas) +
    ' lwProbe=' + probeLwResult;

  // linewidth is deliberately different per timeframe (Daily 1, 4H 2,
  // anything else 3) so that IF Q3 comes back "ok", the same tag can be
  // reused as the self-only deletion discriminator in the real scripts.
  const probeWidth = inferredSpacing === DAILY_MS ? 1 : (inferredSpacing === H4_MS ? 2 : 3);

  trendLine(
    newPoint(time(1), closeC(1)),
    newPoint(currentTime, closeC(0)),
    { linecolor: color.orange, linewidth: probeWidth, linestyle: 1 },
    label
  );
};
