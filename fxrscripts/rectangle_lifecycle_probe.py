// THROWAWAY SPIKE. Not a structure indicator, not an OB/FVG script.
// Delete once its two answers are recorded in fxrscripts/README.md, the
// same way timeframe_probe.py's three answers are recorded there.
//
// The upcoming order-block port (swing_structure/order_blocks.py,
// roadmap/) needs to draw OB/FVG zones as `rectangle()` boxes, many
// simultaneously live, deleted and redrawn every closed candle -- the
// same delete-then-redraw pattern the structure scripts already use for
// `trendLine` and `daily_swing_structure.py`'s old `horizontal_ray`
// design (fxrscripts/README.md, Design 3). But `deleteDrawingByCondition`
// has only ever been confirmed working against those two shapes. It has
// never been exercised against `rectangle()`, and never at the volume an
// OB/FVG script will need (dozens of zones alive at once instead of one
// or two lines). Two unknowns need answering before writing that script,
// because guessing wrong on either means finding out only after the real
// script is already misbehaving on a live chart:
//
//   Q1. Does deleteDrawingByCondition, matched on a tag unique to this
//       script, reliably delete EXACTLY this script's own previously-
//       drawn rectangles each closed candle -- no stragglers left behind,
//       and nothing belonging to another drawing removed?
//
//   Q2. Is there any visible lag or slowdown redrawing ~60-100
//       rectangles every closed candle?
//
// HOW TO READ THE RESULT
//
// Load this alone on a chart (nothing else drawing rectangles) and step
// forward through a replay for a couple dozen candles.
//
//   Q1: read the label on the orange readout box every candle.
//       - `testTagged=<n>` is a live recount of this script's own
//         tagged test rectangles, taken right after this tick's batch of
//         RECT_COUNT boxes was drawn (but before the readout box itself,
//         so the expected value is exactly RECT_COUNT every tick, not
//         RECT_COUNT + 1).
//       - `allRect=<n>` is a recount, at that same moment, of EVERY
//         rectangle on the chart regardless of tag. If this script is
//         the only thing drawing rectangles, allRect should always equal
//         testTagged. If it does not, something outside this script's
//         own tag is on the chart too (and a mismatched drop in either
//         count after a delete would mean the wrong rectangles got
//         removed).
//       - `delMatch=<n>` is how many rectangles this tick's delete call
//         itself matched and removed, i.e. how many of last tick's
//         batch it found. Compare it against `prevTotal` (this tick's
//         copy of what the PREVIOUS tick drew in total, RECT_COUNT + 1
//         readout box included): once warmed up they should be equal
//         every tick. If delMatch keeps coming in lower than prevTotal,
//         stragglers are surviving the delete. If a manual count of
//         boxes on the visible chart keeps climbing tick over tick, that
//         is the same finding read a slower way.
//       - `strayBefore=<n>` is a recount taken immediately after the
//         delete call but before this tick draws anything new. It
//         should always be 0 -- any other value means tagged rectangles
//         survived their own deletion.
//   Q2: watch the chart, not the label, while stepping through the
//       replay. A visible pause, stutter, or the replay falling behind
//       real time on every candle is a NO. Smooth stepping through
//       RECT_COUNT-per-candle redraws is a YES.
//
// `bgProbe=<result>` mirrors timeframe_probe.py's Q3 `lwProbe`: `ok:<v>`
// means `drawing.overrideOptions['backgroundColor']` (bracket access)
// read without a type error; `undef` means it read clean but the field
// was absent on that drawing. If the script fails to compile instead,
// bracket access on backgroundColor is a NO for rectangles, and the
// match/delete logic below has to fall back to `shapeType` instead (see
// the fallback note above PROBE_TAG_COLOR).

const RECT_COUNT = 80; // within the 60-100 range this probe needs to exercise

// Arbitrary RGBA, chosen to be a value nothing else already on a normal
// chart would plausibly be using, so a bracket-access equality match on
// it is unambiguous.
const PROBE_TAG_COLOR = color.rgba(255, 0, 221, 0.13);

// Fallback IF bracket access on backgroundColor errors (mirrors the
// shapeType fallback fxrscripts/README.md's "Self-only drawing cleanup"
// section documents for linewidth, and the exact contingency Design 3
// hit for linecolor): give this script's rectangles a shapeType none of
// the structure/premium-discount scripts use -- draw rotatedRectangle
// instead of rectangle for the test batch, and match on
// `drawing.shapeType === 'rotated_rectangle'` (snake_case unconfirmed,
// check the real string the same way `horizontal_ray` was confirmed for
// horizontalRay in Design 3) instead of on backgroundColor. That answers
// Q1 with a different shape but not for `rectangle()` itself, which the
// OB/FVG port specifically needs, so a bracket-access failure here is
// itself a finding worth recording, not just a workaround to silently
// apply.

let probeTicks = 0;
let probeLastSeenTime = null;
let probePrevTotal = 0; // what the PREVIOUS tick drew in total, for delMatch comparison

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
};

onTick = (length, _moment, _, ta, inputs) => {
  const currentTime = time(0);
  if (currentTime === probeLastSeenTime) {
    return;
  }
  probeLastSeenTime = currentTime;
  probeTicks = probeTicks + 1;

  const t1 = time(1);
  const t0 = currentTime;
  if (Number.isNaN(t1) || Number.isNaN(t0)) {
    // Not enough history yet (e.g. the very first bar). Draw nothing.
    return;
  }
  const price = closeC(0);

  // ---- Delete this script's own previously-drawn rectangles ----
  // Bracket access first, per the established gotcha (README's
  // "Self-only drawing cleanup" section, and Design 3): dotted access on
  // overrideOptions is a type error, since overrideOptions is the
  // DrawingOverrides union across every drawing tool and a given field
  // exists on only some members.
  let delMatch = 0;
  let bgProbeResult = 'undef';
  deleteDrawingByCondition((drawing) => {
    const opts = drawing.overrideOptions;
    if (opts) {
      const bg = opts['backgroundColor'];
      if (bg !== undefined && bg !== null) {
        bgProbeResult = 'ok:' + String(bg);
        if (bg === PROBE_TAG_COLOR) {
          delMatch = delMatch + 1;
          return true;
        }
      }
    }
    return false;
  });

  // ---- Recount immediately after the delete, before drawing anything
  // new this tick. Any tagged survivor here is a straggler the delete
  // call above missed. ----
  let strayBefore = 0;
  deleteDrawingByCondition((drawing) => {
    if (drawing.shapeType === 'rectangle') {
      const opts = drawing.overrideOptions;
      if (opts && opts['backgroundColor'] === PROBE_TAG_COLOR) {
        strayBefore = strayBefore + 1;
      }
    }
    return false;
  });

  // ---- Draw a fresh batch of RECT_COUNT throwaway test rectangles ----
  // Positions/sizes are arbitrary -- small stacked price-offset boxes
  // near the current close -- this only needs to exercise the
  // draw/delete mechanism at volume, not represent anything real.
  for (let i = 0; i < RECT_COUNT; i++) {
    const offset = (i + 1) * 0.0002;
    rectangle(
      t1, price + offset,
      t0, price + offset - 0.0001,
      { backgroundColor: PROBE_TAG_COLOR, color: PROBE_TAG_COLOR, fillBackground: true },
      ''
    );
  }

  // ---- Recount right after the test batch, before the readout box
  // below is added, so the expected values are exactly RECT_COUNT
  // (testTagged) and RECT_COUNT (allRect, if nothing untagged is on the
  // chart too). ----
  let testTagged = 0;
  let allRect = 0;
  deleteDrawingByCondition((drawing) => {
    if (drawing.shapeType === 'rectangle') {
      allRect = allRect + 1;
      const opts = drawing.overrideOptions;
      if (opts && opts['backgroundColor'] === PROBE_TAG_COLOR) {
        testTagged = testTagged + 1;
      }
    }
    return false;
  });

  // ---- One labelled readout rectangle, tagged the same way so next
  // tick's delete cleans it up too. Its own count is deliberately
  // excluded from testTagged/allRect above (see the comment there). ----
  const totalDrawn = RECT_COUNT + 1; // +1 for this readout box itself
  const label =
    'ticks=' + String(probeTicks) +
    ' testTagged=' + String(testTagged) +
    ' allRect=' + String(allRect) +
    ' delMatch=' + String(delMatch) +
    ' prevTotal=' + String(probePrevTotal) +
    ' strayBefore=' + String(strayBefore) +
    ' bgProbe=' + bgProbeResult;

  rectangle(
    t1, price + 0.0011,
    t0, price + 0.0010,
    {
      backgroundColor: PROBE_TAG_COLOR,
      color: color.orange,
      fillBackground: true,
      showLabel: true,
      textColor: color.orange,
      bold: true,
    },
    label
  );

  probePrevTotal = totalDrawn;
};
