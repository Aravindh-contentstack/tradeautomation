// This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © The_Forex_Steward

//@version=6
indicator("Internal Market Structure + Order Blocks", overlay=true)

// === User Inputs ===
showBullishOrderBlocks = input.bool(true, title="Show Bullish Order Blocks (Green)")
showBearishOrderBlocks = input.bool(true, title="Show Bearish Order Blocks (Red)")
orderBlockDuration = 20  // Number of bars the box extends
deleteMitigatedBoxes = input.bool(true, title="Delete Mitigated Boxes")  // New input to delete mitigated boxes

// === Variables to Track State ===
var float lastBearishHigh = na
var float lastBearishLow = na
var int lastBearishBarIndex = na
var bool bearishBroken = true

var float lastBullishHigh = na
var float lastBullishLow = na
var int lastBullishBarIndex = na
var bool bullishBroken = true

isBearish = close < open
isBullish = close > open

// === Track latest box objects ===
// Arrays to store multiple boxes with their unique identifiers (bar_index or timestamp)
var box[] bullishBoxes = array.new<box>()  // Initialize as empty array
var box[] bearishBoxes = array.new<box>()  // Initialize as empty array

// === Capture Candle State ===
if isBearish
    lastBearishHigh := high
    lastBearishLow := low
    lastBearishBarIndex := bar_index
    bearishBroken := false

if isBullish
    lastBullishLow := low
    lastBullishHigh := high
    lastBullishBarIndex := bar_index
    bullishBroken := false

// === Break Conditions ===
bullishBreak = not bearishBroken and close > lastBearishHigh
bearishBreak = not bullishBroken and close < lastBullishLow

if bullishBreak
    bearishBroken := true
if bearishBreak
    bullishBroken := true

// Plot triangle shapes with size.tiny
plotshape(bullishBreak, title="Bullish Engulfment", location=location.belowbar, color=color.green, style=shape.triangleup, size=size.tiny)
plotshape(bearishBreak, title="Bearish Engulfment", location=location.abovebar, color=color.red, style=shape.triangledown, size=size.tiny)

// === Automatically delete invalidated boxes ===
if array.size(bullishBoxes) > 0
    for i = 0 to array.size(bullishBoxes) - 1
        boxItem = array.get(bullishBoxes, i)
        if low < box.get_bottom(boxItem)  // Delete bullish box if price exceeds its bottom 
            box.delete(boxItem)
            array.remove(bullishBoxes, i)

if array.size(bearishBoxes) > 0
    for i = 0 to array.size(bearishBoxes) - 1
        boxItem = array.get(bearishBoxes, i)
        if high > box.get_top(boxItem)  // Delete bearish box if price exceeds its top 
            box.delete(boxItem)
            array.remove(bearishBoxes, i)

// === Delete mitigated boxes optional ===
if deleteMitigatedBoxes
    if array.size(bullishBoxes) > 0
        for i = 0 to array.size(bullishBoxes) - 1
            boxItem = array.get(bullishBoxes, i)
            if low < box.get_top(boxItem)  
                box.delete(boxItem)
                array.remove(bullishBoxes, i)

    if array.size(bearishBoxes) > 0
        for i = 0 to array.size(bearishBoxes) - 1
            boxItem = array.get(bearishBoxes, i)
            if high > box.get_bottom(boxItem)
                box.delete(boxItem)
                array.remove(bearishBoxes, i)

// === Draw New Order Blocks ===
if showBullishOrderBlocks and bullishBreak and not na(lastBearishBarIndex)
    bullishBox = box.new(left=lastBearishBarIndex, right=bar_index + orderBlockDuration, top=lastBearishHigh, bottom=lastBearishLow, border_color=color.green, bgcolor=color.new(color.green, 85))
    array.push(bullishBoxes, bullishBox)  // Add new box to array

if showBearishOrderBlocks and bearishBreak and not na(lastBullishBarIndex)
    bearishBox = box.new(left=lastBullishBarIndex, right=bar_index + orderBlockDuration, top=lastBullishHigh, bottom=lastBullishLow, border_color=color.red, bgcolor=color.new(color.red, 85))
    array.push(bearishBoxes, bearishBox)  // Add new box to array

// === Internal Structure Logic ===
var int bullishCount = 0
var int bearishCount = 0
var float lowestBullishPrice = na
var float highestBearishPrice = na

var float firstBullishOpen = na
var float firstBearishOpen = na

var int lastInternalShift = 0

if isBullish
    bullishCount += 1
    bearishCount := 0
    if bullishCount == 1 or low < lowestBullishPrice
        lowestBullishPrice := low
    if bullishCount == 1
        firstBullishOpen := open
else if isBearish
    bearishCount += 1
    bullishCount := 0
    if bearishCount == 1 or high > highestBearishPrice
        highestBearishPrice := high
    if bearishCount == 1
        firstBearishOpen := open
else
    bullishCount := 0
    bearishCount := 0
    lowestBullishPrice := na
    highestBearishPrice := na

internalShiftBearish = close < firstBullishOpen
internalShiftBullish = close > firstBearishOpen

allowInternalShiftBearish = internalShiftBearish and lastInternalShift != -1
allowInternalShiftBullish = internalShiftBullish and lastInternalShift != 1

var bool plotBearishInternalShift = false
var bool plotBullishInternalShift = false

plotBearishInternalShift := false
plotBullishInternalShift := false

if allowInternalShiftBearish
    plotBearishInternalShift := true
    lastInternalShift := -1

if allowInternalShiftBullish
    plotBullishInternalShift := true
    lastInternalShift := 1

// Plot internal shift triangles with size.tiny
plotshape(plotBullishInternalShift, title="Bullish Internal Shift", location=location.belowbar, color=color.black, style=shape.triangleup, size=size.tiny)
plotshape(plotBearishInternalShift, title="Bearish Internal Shift", location=location.abovebar, color=color.black, style=shape.triangledown, size=size.tiny)

// === Alerts ===
alertcondition(bullishBreak, title="Bullish Engulfment", message="Bullish engulfment! New order block available.")
alertcondition(bearishBreak, title="Bearish Engulfment", message="Bearish engulfment! New order block available.")
alertcondition(plotBullishInternalShift, title="Bullish Internal Shift", message="Bullish Internal Shift detected! Update your swing low.")
alertcondition(plotBearishInternalShift, title="Bearish Internal Shift", message="Bearish Internal Shift detected! Update your swing high.")
