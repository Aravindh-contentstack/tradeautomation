// FXR Script port of swing_structure/detector.py (compute_daily_swing_structure),
// for fxreplay's FXR Script language. Full reconstruction: same rules, same
// branch order, confirmed in ethereal-coalescing-flute.md. Python
// (swing_structure/detector.py) stays the source of truth. If this ever
// disagrees with it for the same candles, fix detector.py first, then
// re-port. pinescripts/daily_swing_structure.py is the separate, working
// TradingView (Pine Script) version and is untouched by this file.
//
// Swing high and swing low each have their OWN independent age clock and
// timeout, not a shared one (see ethereal-coalescing-flute.md's "Revision"
// section for why: a shared clock let one side's frequent breaks keep
// resetting the timeout for BOTH sides, freezing the quiet side
// indefinitely). manual_restart and hold_timeout remain global controls
// acting on both sides at once.
//
// Visuals history, in order tried:
//   1. horizontalRay + deleteDrawingById(id): looked correct on a single
//      full script run (exactly one ray per side at the end), but stepping
//      through fxreplay's replay one candle at a time left old rays behind.
//      The numeric state (swingHigh/swingLow/seeded/clocks) tracks correctly
//      across replay the whole time, so top-level `let` state clearly does
//      survive across replay steps in general. Why deleteDrawingById(id)
//      specifically failed to find and remove the old ray during replay,
//      while the exact same id-tracking worked in a single full run, is
//      still not fully understood.
//   2. band.line(name, value, color, ...): its own docs say reusing the
//      same `name` reuses the same id, i.e. it updates in place, which
//      looked like a clean way to sidestep the id problem in (1) entirely.
//      But it threw "ReferenceError: band is not defined" when called from
//      onTick. The only working example of it in the docs calls it inside
//      init with hardcoded static values, matching the same restriction the
//      docs state for input.* ("must be declared only inside the init
//      control block"). band is apparently init-only, so it cannot express
//      a level that changes every tick.
//   3. horizontalRay + deleteDrawingByCondition (current, confirmed
//      working): same horizontalRay call as (1), but the delete is now
//      content-based instead of id-based. deleteDrawingByCondition(condition)
//      inspects the chart's actual current drawings (StoredShape:
//      chartPoints, overrideOptions, shapeType, confirmed via the editor's
//      own autocomplete) rather than a remembered id, so it isn't relying
//      on this script's own memory of what it drew last. Matching on
//      shapeType === 'horizontalRay' errored: "types 'MultiplePointShapeTypes'
//      and '"horizontalRay"' have no overlap", i.e. that's not the real
//      internal name for this shape. The real value is 'horizontal_ray'
//      (snake_case, matching the snake_case shape names TradingView's real
//      Charting Library API uses elsewhere, e.g.
//      horizontal_line/vertical_line/long_position), confirmed by testing
//      in fxreplay. overrideOptions.linecolor was dropped rather than
//      pursued further (it errored too, since overrideOptions is a big
//      union type, DrawingOverrides, across every drawing tool, so
//      linecolor only exists on some of its members): every
//      horizontal_ray-shaped drawing is deleted and both rays are
//      recreated every tick, instead of trying to distinguish red from
//      green by inspecting overrideOptions.
//
// No table/grid API was found in the docs for the Pine version's status
// table, so it is dropped here.
//
// The new-bar gate is on the candle's own timestamp (time(0) changing)
// rather than the onTick `length` parameter: `length`'s exact meaning
// (candles replayed so far vs. total loaded history) isn't documented, and
// gating on it produced a silent failure (seeded from a not-yet-available
// window on the very first tick, then never recovered since a NaN swing
// level never compares as "broken"). The cold-start check is likewise a
// direct check that the rolling window actually resolved to real numbers,
// not a `length` threshold guess.

let seeded = false;
let swingHigh = NaN;
let swingLow = NaN;
let swingHighPivotTime = null;
let swingLowPivotTime = null;
let highClock = 0;
let lowClock = 0;
let prevManualInput = false;
let prevHoldInput = false;
let lastSeenTime = null;

// Market structure (bullish/bearish), ported from
// swing_structure/market_structure.py (compute_market_structure). Python
// stays the source of truth for this too, same as the swing high/low
// logic above: fix there first, then re-port.
//
// Rule: structure starts undetermined (null) and flips ONLY on a genuine
// break (a real close beyond the level, the same "else if (closeToday >
// swingHigh)" / "else if (closeToday < swingLow)" branches below already
// use to redraw a level). A manual restart, a hold release, or a timeout
// redraw moves a level without a real price break, so none of those may
// flip marketStructure, only the two real-break branches may.
let marketStructure = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  input.int('Lookback', 45, 'lookback', 2, 500, 1, 'How many trailing candles (including the current one) define the window a swing point is picked from.', 'Structure Settings');
  input.int('Timeout Candles', 65, 'timeoutCandles', 1, 500, 1, 'How many candles can pass with a given side never redrawing before that side alone is force-redrawn. The two sides time out independently of each other.', 'Structure Settings');

  input.bool('Manual Restart Now', false, 'manualRestartInput');
  input.bool('Hold Timeout Active', false, 'holdTimeoutInput');
};

onTick = (length, _moment, _, ta, inputs) => {
  const lookback = inputs.lookback;
  const timeoutCandles = inputs.timeoutCandles;
  const manualRestartInput = inputs.manualRestartInput;
  const holdTimeoutInput = inputs.holdTimeoutInput;

  // One state-machine update per closed candle, gated on that candle's own
  // timestamp rather than `length`.
  const currentTime = time(0);
  if (currentTime === lastSeenTime) {
    return;
  }
  lastSeenTime = currentTime;

  // Rolling `lookback`-candle window, computed by hand since FXR's `ta`
  // library has no highest/lowest/highestbars/lowestbars. Also tracks which
  // offset the extreme occurred at, needed to anchor the ray at the actual
  // pivot candle when this side redraws today.
  let windowHigh = high(0);
  let windowLow = low(0);
  let highOffset = 0;
  let lowOffset = 0;
  for (let i = 1; i < lookback; i++) {
    const h = high(i);
    const l = low(i);
    if (Number.isNaN(h) || Number.isNaN(l)) {
      // Cold start: not enough history yet for a full window. Bail out for
      // this candle rather than seeding from a partial window.
      return;
    }
    if (h > windowHigh) {
      windowHigh = h;
      highOffset = i;
    }
    if (l < windowLow) {
      windowLow = l;
      lowOffset = i;
    }
  }

  const manualRaw = manualRestartInput;
  let manualTriggered = manualRaw && !prevManualInput;

  const holdRaw = holdTimeoutInput;
  const holdReleased = !holdRaw && prevHoldInput;
  let holdEffective = holdRaw;

  const closeToday = closeC(0);

  if (!seeded) {
    // Cold start: seed both sides together from the current window.
    swingHigh = windowHigh;
    swingLow = windowLow;
    swingHighPivotTime = time(highOffset);
    swingLowPivotTime = time(lowOffset);
    highClock = 0;
    lowClock = 0;
    seeded = true;
  } else {
    // Step 1: conflict check, shared by both sides. Bypass both manual
    // controls entirely if manual_restart triggers while hold_timeout is
    // also true today.
    if (manualTriggered && holdEffective) {
      manualTriggered = false;
      holdEffective = false;
    }

    // Market structure only cares about a genuine break, so track that
    // separately from the redraw logic below, reset fresh each tick.
    let highBrokeReal = false;
    let lowBrokeReal = false;

    // ---- High side: its own clock, independent of the low side. ----
    if (manualTriggered) {
      // Step 2: manual restart wins outright.
      swingHigh = windowHigh;
      swingHighPivotTime = time(highOffset);
      highClock = 0;
    } else if (closeToday > swingHigh) {
      // Step 3: real break of the swing high.
      swingHigh = windowHigh;
      swingHighPivotTime = time(highOffset);
      highClock = 0;
      highBrokeReal = true;
    } else if (holdReleased) {
      // Step 4: hold_timeout just switched back off. Fresh grace period.
      highClock = 0;
    } else {
      const tentativeHighClock = highClock + 1;
      if (tentativeHighClock >= timeoutCandles && !holdEffective) {
        // Step 5: automatic timeout, for the high side alone.
        swingHigh = windowHigh;
        swingHighPivotTime = time(highOffset);
        highClock = 0;
      } else {
        // Step 6: an ordinary day for this side.
        highClock = tentativeHighClock;
      }
    }

    // ---- Low side: same six steps, independent of the high side. ----
    if (manualTriggered) {
      swingLow = windowLow;
      swingLowPivotTime = time(lowOffset);
      lowClock = 0;
    } else if (closeToday < swingLow) {
      swingLow = windowLow;
      swingLowPivotTime = time(lowOffset);
      lowClock = 0;
      lowBrokeReal = true;
    } else if (holdReleased) {
      lowClock = 0;
    } else {
      const tentativeLowClock = lowClock + 1;
      if (tentativeLowClock >= timeoutCandles && !holdEffective) {
        swingLow = windowLow;
        swingLowPivotTime = time(lowOffset);
        lowClock = 0;
      } else {
        lowClock = tentativeLowClock;
      }
    }

    // A close can't be both above swingHigh and below swingLow at once
    // (swingHigh always stays above swingLow once seeded), so these are
    // mutually exclusive in practice, same as in market_structure.py.
    if (highBrokeReal) {
      marketStructure = 'bullish';
    } else if (lowBrokeReal) {
      marketStructure = 'bearish';
    }
  }

  // Redrawn every tick, not just on a redraw branch. deleteDrawingByCondition
  // clears any horizontalRay currently on the chart (matched by shapeType
  // alone, not by a remembered id), so this stays correct regardless of
  // whatever it is about fxreplay's replay stepping that broke id-based
  // tracking. Both rays are deleted and both recreated every tick, since
  // matching shapeType alone can't tell the red one from the green one.
  deleteDrawingByCondition((drawing) => drawing.shapeType === 'horizontal_ray');
  const structureLabel = marketStructure ? marketStructure.toUpperCase() : 'UNDETERMINED';
  horizontalRay(swingHighPivotTime, swingHigh, { linecolor: color.red, linewidth: 1, showLabel: true }, `HH (${structureLabel})`);
  horizontalRay(swingLowPivotTime, swingLow, { linecolor: color.green, linewidth: 1, showLabel: true }, `LL (${structureLabel})`);

  prevManualInput = manualRaw;
  prevHoldInput = holdEffective;
};
