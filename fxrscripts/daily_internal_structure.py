// FXR Script port of swing_structure/internal_structure.py, powered by
// swing_structure/pivot_detector.py's compute_pivot_swing_structure
// (ATR-based zigzag version). Python stays the source of truth. If this
// ever disagrees with it for the same candles, fix pivot_detector.py
// first, then re-port.
//
// Mechanism history (see pivot_detector.py's module docstring for the
// full version): this file has now used three different mechanisms.
// Rounds 1-2 used the same persist-until-broken-plus-timeout mechanism
// as daily_swing_structure.py, at a shorter lookback/timeout, which
// fixed a string of bugs but left a structural one, a level could still
// go stale and span an enormous range before finally breaking. Round 3
// switched to a fixed-window fractal pivot (pivotLen candles both
// sides), which fixed staleness but didn't adapt: a fast trend's
// pullbacks are often shorter, in candle-count terms, than a fixed
// window, so a real, meaningful retracement could fail to ever confirm.
//
// Round 4 (current): an ATR-based zigzag. A pivot confirms as soon as
// price reverses from the running extreme by more than
// reversalMultiplier x ATR, rather than after a fixed number of candles.
// A sharp, volatile reversal confirms quickly, a shallow, slow one takes
// longer, adapting automatically since ATR itself moves with
// volatility. ATR uses Wilder's smoothing (the traditional formula most
// platforms default to), computed one candle at a time rather than
// recomputed from scratch each tick, consistent with how this file
// already carries state forward.
//
// Structural note: unlike round 3, the two sides are no longer fully
// independent. A true zigzag alternates, only one side is "live"
// (accumulating toward its next reversal) at a time, the other side just
// holds its last confirmed value until the zigzag swings back to it.
// This is what makes staleness structurally impossible rather than
// something to tune away.
//
// Visual (unchanged in spirit): nothing is shown for the current,
// not-yet-crossed pivot. The moment it's genuinely crossed (a real close
// beyond it, never a manual restart or a silent reversal confirmation),
// one fixed dotted trendLine segment is drawn from the pivot's own
// time/price to the crossing candle's time, and it is never touched
// again afterward.

let internalAtr = NaN;
let internalTrSum = 0;
let internalTrCount = 0;

let internalDirection = null; // null (bootstrap), 'up', or 'down'
let internalRunningExtreme = NaN;
let internalRunningExtremeTime = null;

// During bootstrap (internalDirection is null) both sides are tracked at
// once, since we don't yet know which one will confirm first.
let internalBootstrapHigh = NaN;
let internalBootstrapHighTime = null;
let internalBootstrapLow = NaN;
let internalBootstrapLowTime = null;

let internalSwingHigh = NaN;
let internalSwingHighTime = null;
let internalHighCrossed = false;
let internalSwingLow = NaN;
let internalSwingLowTime = null;
let internalLowCrossed = false;

let internalPrevManualInput = false;
let internalLastSeenTime = null;

// Bullish/bearish structure for this tier, same rule as
// swing_structure/market_structure.py: starts undetermined (null), and
// flips ONLY on a genuine cross, never on a manual restart or a silent
// reversal confirmation.
let internalStructure = null;

//@version=1

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });

  input.int('Internal ATR Period', 14, 'atrPeriod', 2, 200, 1, 'How many True Range values feed Wilder\'s ATR.', 'Internal Structure Settings');
  input.float('Internal Reversal Multiplier', 1.5, 'reversalMultiplier', 0.1, 10, 0.1, 'How many ATRs of pullback from the running extreme count as a genuine reversal, confirming it as a pivot.', 'Internal Structure Settings');

  input.bool('Internal Manual Restart Now', false, 'manualRestartInput');
};

onTick = (length, _moment, _, ta, inputs) => {
  const atrPeriod = inputs.atrPeriod;
  const reversalMultiplier = inputs.reversalMultiplier;
  const manualRestartInput = inputs.manualRestartInput;

  // One state-machine update per closed candle, gated on that candle's own
  // timestamp rather than `length` (see daily_swing_structure.py for why).
  const currentTime = time(0);
  if (currentTime === internalLastSeenTime) {
    return;
  }
  internalLastSeenTime = currentTime;

  const highToday = high(0);
  const lowToday = low(0);
  const closeToday = closeC(0);

  // True Range and Wilder's ATR, updated one candle at a time.
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
  if (Number.isNaN(internalAtr)) {
    internalTrSum += trToday;
    internalTrCount += 1;
    if (internalTrCount === atrPeriod) {
      internalAtr = internalTrSum / atrPeriod;
    }
  } else {
    internalAtr = (internalAtr * (atrPeriod - 1) + trToday) / atrPeriod;
  }

  const manualRaw = manualRestartInput;
  const manualTriggered = manualRaw && !internalPrevManualInput;

  let highBrokeReal = false;
  let lowBrokeReal = false;

  if (manualTriggered) {
    // Deliberate override: forget whatever was in progress and restart
    // the zigzag from today, both sides reset to today's own high/low,
    // immediately visible. No line drawn, no structure flip, same as
    // manual restart everywhere else in this project.
    internalSwingHigh = highToday;
    internalSwingHighTime = currentTime;
    internalHighCrossed = false;
    internalSwingLow = lowToday;
    internalSwingLowTime = currentTime;
    internalLowCrossed = false;
    internalDirection = null;
    internalBootstrapHigh = highToday;
    internalBootstrapHighTime = currentTime;
    internalBootstrapLow = lowToday;
    internalBootstrapLowTime = currentTime;
  } else if (Number.isNaN(internalAtr)) {
    // Not enough candles yet for a first ATR value at all.
  } else if (internalDirection === null) {
    // Bootstrap: ATR is ready, but we haven't confirmed which side goes
    // first. Track both running extremes at once and see which one
    // reverses first.
    if (Number.isNaN(internalBootstrapHigh)) {
      internalBootstrapHigh = highToday;
      internalBootstrapHighTime = currentTime;
      internalBootstrapLow = lowToday;
      internalBootstrapLowTime = currentTime;
    } else {
      if (highToday > internalBootstrapHigh) {
        internalBootstrapHigh = highToday;
        internalBootstrapHighTime = currentTime;
      }
      if (lowToday < internalBootstrapLow) {
        internalBootstrapLow = lowToday;
        internalBootstrapLowTime = currentTime;
      }
      const reversalDown = closeToday <= internalBootstrapHigh - reversalMultiplier * internalAtr;
      const reversalUp = closeToday >= internalBootstrapLow + reversalMultiplier * internalAtr;
      if (reversalDown) {
        internalSwingHigh = internalBootstrapHigh;
        internalSwingHighTime = internalBootstrapHighTime;
        internalHighCrossed = false;
        internalDirection = 'down';
        internalRunningExtreme = lowToday;
        internalRunningExtremeTime = currentTime;
      } else if (reversalUp) {
        internalSwingLow = internalBootstrapLow;
        internalSwingLowTime = internalBootstrapLowTime;
        internalLowCrossed = false;
        internalDirection = 'up';
        internalRunningExtreme = highToday;
        internalRunningExtremeTime = currentTime;
      }
    }
  } else {
    // Direction is established: check both sides for a genuine cross
    // first (independent of direction), then advance whichever side is
    // currently "live" toward its next pivot.
    if (!Number.isNaN(internalSwingHigh) && !internalHighCrossed && closeToday > internalSwingHigh) {
      trendLine(
        newPoint(internalSwingHighTime, internalSwingHigh),
        newPoint(currentTime, internalSwingHigh),
        { linecolor: color.red, linewidth: 1, linestyle: 1 }
      );
      internalHighCrossed = true;
      highBrokeReal = true;
    }
    if (!Number.isNaN(internalSwingLow) && !internalLowCrossed && closeToday < internalSwingLow) {
      trendLine(
        newPoint(internalSwingLowTime, internalSwingLow),
        newPoint(currentTime, internalSwingLow),
        { linecolor: color.green, linewidth: 1, linestyle: 1 }
      );
      internalLowCrossed = true;
      lowBrokeReal = true;
    }

    if (internalDirection === 'up') {
      if (highToday > internalRunningExtreme) {
        internalRunningExtreme = highToday;
        internalRunningExtremeTime = currentTime;
      }
      if (closeToday <= internalRunningExtreme - reversalMultiplier * internalAtr) {
        internalSwingHigh = internalRunningExtreme;
        internalSwingHighTime = internalRunningExtremeTime;
        internalHighCrossed = false;
        internalDirection = 'down';
        internalRunningExtreme = lowToday;
        internalRunningExtremeTime = currentTime;
      }
    } else if (internalDirection === 'down') {
      if (lowToday < internalRunningExtreme) {
        internalRunningExtreme = lowToday;
        internalRunningExtremeTime = currentTime;
      }
      if (closeToday >= internalRunningExtreme + reversalMultiplier * internalAtr) {
        internalSwingLow = internalRunningExtreme;
        internalSwingLowTime = internalRunningExtremeTime;
        internalLowCrossed = false;
        internalDirection = 'up';
        internalRunningExtreme = highToday;
        internalRunningExtremeTime = currentTime;
      }
    }
  }

  // A close can't be both above internalSwingHigh and below
  // internalSwingLow at once, so these are mutually exclusive.
  if (highBrokeReal) {
    internalStructure = 'bullish';
  } else if (lowBrokeReal) {
    internalStructure = 'bearish';
  }

  internalPrevManualInput = manualRaw;
};
