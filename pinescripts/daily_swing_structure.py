// Pine port of swing_structure/detector.py (compute_daily_swing_structure).
// Rules confirmed in ethereal-coalescing-flute.md. Branch order and variable
// names intentionally mirror the Python so the two stay line-for-line
// comparable. See pinescripts/README.md for how to load and use this.
//
// Swing high and swing low each have their OWN independent age clock and
// timeout (originally a single shared clock, revised because a shared
// clock let one side's frequent breaks keep resetting the timeout for
// BOTH sides, freezing the quiet side indefinitely, see
// ethereal-coalescing-flute.md for the concrete example that surfaced
// this). manual_restart and hold_timeout remain global controls acting on
// both sides at once.

//@version=6
indicator("Daily Swing Structure", overlay=true, max_lines_count=500, max_labels_count=500)

// ---- Inputs -----------------------------------------------------------

lookback = input.int(45, "Lookback", minval=2, group="Structure Settings", tooltip="How many trailing candles (including the current one) define the window a swing point is picked from.")
timeoutCandles = input.int(65, "Timeout Candles", minval=1, group="Structure Settings", tooltip="How many candles can pass with a given side never redrawing before that side alone is force-redrawn. The two sides time out independently of each other.")

manualRestartInput = input.bool(false, "Manual Restart Now", group="Manual Controls", tooltip="Check this box for exactly one candle to force an immediate redraw of both sides, then uncheck it. Only behaves correctly going forward while replaying candle by candle.")
holdTimeoutInput = input.bool(false, "Hold Timeout Active", group="Manual Controls", tooltip="While checked, suppresses only the automatic timeout, for both sides. Real breaks still apply. Uncheck to release and start a fresh timeout countdown on both sides.")

// ---- Persistent state (carried bar to bar, mirrors the Python loop state) ----

var swingHigh = float(na)
var swingLow = float(na)
var highClock = int(na)
var lowClock = int(na)
var seeded = false
var prevManualInput = false
var prevHoldInput = false

var swingHighLine = line(na)
var swingLowLine = line(na)
var swingHighLabel = label(na)
var swingLowLabel = label(na)

var lastHighEvent = string(na)
var lastLowEvent = string(na)

// ---- Helpers ------------------------------------------------------------
// Pine functions cannot reassign global `var` variables, so each helper only
// deletes the previous drawing and returns the new one. The caller assigns
// the result back to the persistent var. The window level and pivot bar are
// passed in rather than recomputed here: ta.highest/ta.lowest/ta.highestbars/
// ta.lowestbars must run unconditionally on every bar to track a correct
// trailing window, so they're computed once per bar below, not inside a
// conditionally-called function.

newHighLine(level, pivotBar) =>
    if not na(swingHighLine)
        line.delete(swingHighLine)
    line.new(pivotBar, level, bar_index, level, color=color.red, width=1, extend=extend.right)

newHighLabel(level, pivotBar) =>
    if not na(swingHighLabel)
        label.delete(swingHighLabel)
    label.new(pivotBar, level, "HH", style=label.style_label_down, color=color.new(color.red, 85), textcolor=color.red, size=size.small)

newLowLine(level, pivotBar) =>
    if not na(swingLowLine)
        line.delete(swingLowLine)
    line.new(pivotBar, level, bar_index, level, color=color.green, width=1, extend=extend.right)

newLowLabel(level, pivotBar) =>
    if not na(swingLowLabel)
        label.delete(swingLowLabel)
    label.new(pivotBar, level, "LL", style=label.style_label_up, color=color.new(color.green, 85), textcolor=color.green, size=size.small)

// ---- Per-bar state machine (only commits on a fully closed Daily candle) ----

if barstate.isconfirmed
    // Computed unconditionally every bar, this is what fixes the window from
    // drifting into an all-time high/low: ta.* trailing-window functions
    // must be evaluated on every bar to stay correct.
    windowHigh = ta.highest(high, lookback)
    windowLow = ta.lowest(low, lookback)
    highPivotBar = bar_index + ta.highestbars(high, lookback)
    lowPivotBar = bar_index + ta.lowestbars(low, lookback)

    manualRaw = manualRestartInput
    manualTriggered = manualRaw and not prevManualInput

    holdRaw = holdTimeoutInput
    holdReleased = (not holdRaw) and prevHoldInput
    holdEffective = holdRaw

    closeToday = close
    highEventLabel = string(na)
    lowEventLabel = string(na)

    if not seeded
        // Cold start: wait for a full lookback window before seeding both sides.
        if bar_index >= lookback - 1
            swingHigh := windowHigh
            swingLow := windowLow
            highClock := 0
            lowClock := 0
            seeded := true
            highEventLabel := "initial seed"
            lowEventLabel := "initial seed"
            swingHighLine := newHighLine(windowHigh, highPivotBar)
            swingHighLabel := newHighLabel(windowHigh, highPivotBar)
            swingLowLine := newLowLine(windowLow, lowPivotBar)
            swingLowLabel := newLowLabel(windowLow, lowPivotBar)
        else
            highEventLabel := "warming up"
            lowEventLabel := "warming up"
    else
        // Step 1: conflict check, shared by both sides. Bypass both manual
        // controls entirely if manual_restart triggers while hold_timeout
        // is also true today.
        if manualTriggered and holdEffective
            manualTriggered := false
            holdEffective := false

        // ---- High side: its own clock, its own event, independent of
        // whatever the low side does today. ----
        if manualTriggered
            // Step 2: manual restart wins outright, same as before.
            swingHigh := windowHigh
            highClock := 0
            highEventLabel := "manual restart"
            swingHighLine := newHighLine(windowHigh, highPivotBar)
            swingHighLabel := newHighLabel(windowHigh, highPivotBar)
        else if closeToday > swingHigh
            // Step 3: real break of the swing high.
            swingHigh := windowHigh
            highClock := 0
            highEventLabel := "break of swing high"
            swingHighLine := newHighLine(windowHigh, highPivotBar)
            swingHighLabel := newHighLabel(windowHigh, highPivotBar)
        else if holdReleased
            // Step 4: hold_timeout just switched back off. Fresh grace
            // period for this side's own clock.
            highClock := 0
            highEventLabel := "hold released"
        else
            tentativeHighClock = highClock + 1
            if tentativeHighClock >= timeoutCandles and not holdEffective
                // Step 5: automatic timeout, for the high side alone.
                swingHigh := windowHigh
                highClock := 0
                highEventLabel := "timeout"
                swingHighLine := newHighLine(windowHigh, highPivotBar)
                swingHighLabel := newHighLabel(windowHigh, highPivotBar)
            else
                // Step 6: an ordinary day for this side.
                highClock := tentativeHighClock
                highEventLabel := na

        // ---- Low side: same six steps, its own clock and event,
        // independent of whatever the high side did above. ----
        if manualTriggered
            swingLow := windowLow
            lowClock := 0
            lowEventLabel := "manual restart"
            swingLowLine := newLowLine(windowLow, lowPivotBar)
            swingLowLabel := newLowLabel(windowLow, lowPivotBar)
        else if closeToday < swingLow
            swingLow := windowLow
            lowClock := 0
            lowEventLabel := "break of swing low"
            swingLowLine := newLowLine(windowLow, lowPivotBar)
            swingLowLabel := newLowLabel(windowLow, lowPivotBar)
        else if holdReleased
            lowClock := 0
            lowEventLabel := "hold released"
        else
            tentativeLowClock = lowClock + 1
            if tentativeLowClock >= timeoutCandles and not holdEffective
                swingLow := windowLow
                lowClock := 0
                lowEventLabel := "timeout"
                swingLowLine := newLowLine(windowLow, lowPivotBar)
                swingLowLabel := newLowLabel(windowLow, lowPivotBar)
            else
                lowClock := tentativeLowClock
                lowEventLabel := na

    prevManualInput := manualRaw
    prevHoldInput := holdEffective
    lastHighEvent := na(highEventLabel) ? lastHighEvent : highEventLabel
    lastLowEvent := na(lowEventLabel) ? lastLowEvent : lowEventLabel

// ---- Status table (Pine equivalent of the printed Python table) ----

var infoTable = table.new(position.top_right, 2, 6, bgcolor=color.new(color.black, 70), border_width=1, border_color=color.gray)

if barstate.islast
    table.cell(infoTable, 0, 0, "Swing High", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 0, na(swingHigh) ? "-" : str.tostring(swingHigh), text_color=color.white, text_size=size.small)
    table.cell(infoTable, 0, 1, "Swing Low", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 1, na(swingLow) ? "-" : str.tostring(swingLow), text_color=color.white, text_size=size.small)
    table.cell(infoTable, 0, 2, "High Clock", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 2, na(highClock) ? "-" : str.tostring(highClock), text_color=color.white, text_size=size.small)
    table.cell(infoTable, 0, 3, "Low Clock", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 3, na(lowClock) ? "-" : str.tostring(lowClock), text_color=color.white, text_size=size.small)
    table.cell(infoTable, 0, 4, "Last High Event", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 4, na(lastHighEvent) ? "-" : lastHighEvent, text_color=color.white, text_size=size.small)
    table.cell(infoTable, 0, 5, "Last Low Event", text_color=color.white, text_size=size.small)
    table.cell(infoTable, 1, 5, na(lastLowEvent) ? "-" : lastLowEvent, text_color=color.white, text_size=size.small)
