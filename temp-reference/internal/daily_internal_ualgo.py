// This Pine Script™ code is subject to the terms of the Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) https://creativecommons.org/licenses/by-nc-sa/4.0/
// © UAlgo
//@version=5
indicator("Internal/External Market Structure [UAlgo]", shorttitle="Internal/External Market Structure [UAlgo]", overlay=true, max_lines_count=500, max_labels_count=500)

internalLength = input.int(8, title="Internal Swing Length", maxval=20, minval=2, group="Internal/External Market Structure [UAlgo]")
externalLength = input.int(30, title="External Swing Length", minval=30, maxval=200, group="Internal/External Market Structure [UAlgo]")
showMarketStructure = input.string("Both",title = "Show Internal/External Market Structure",options = ["Both","Internal","External"],group="Internal/External Market Structure [UAlgo]")
upColor = input.color(color.new(color.teal,0),title = "Bullish/Bearish Market Structure Color",group="Internal/External Market Structure [UAlgo]",inline = "vs")
dnColor = input.color(color.new(color.red,0),title = " ",group="Internal/External Market Structure [UAlgo]",inline = "vs")

type swing
    float price = na
    int time = na
    bool crossed = false

type market
    string msbOrBos
    int direction
    line marketLine
    int lastLineTime
    label marketLabel

var market internalStructure = market.new(na, na, na, 0)
var market externalStructure = market.new(na, na, na, 0)

detectSwings(len) =>
    var int swingDirection = 0
    var swing highPoint = swing.new(na, na)
    var swing lowPoint = swing.new(na, na)
    upperLevel = ta.highest(len)
    lowerLevel = ta.lowest(len)

    swingDirection := high[len] > upperLevel ? 0 : low[len] < lowerLevel ? 1 : swingDirection

    if swingDirection == 0 and swingDirection[1] != 0
        highPoint := swing.new(high[len], time[len])
    if swingDirection == 1 and swingDirection[1] != 1
        lowPoint := swing.new(low[len], time[len])

    [highPoint, lowPoint]

checkMarketStructure(crossed, direction, swingPrice, swingTime, lineColor, isExternal) =>
    var market structure = isExternal ? externalStructure : internalStructure
    structure.msbOrBos := na(structure.msbOrBos) ? "msb" : structure.direction == direction ? "bos" : "msb"
    structure.direction := direction
    structure.marketLine := line.new(x1=swingTime, y1=swingPrice, x2=time, y2=swingPrice, xloc=xloc.bar_time, color=lineColor, style=isExternal ? line.style_dashed : line.style_solid)
    labelText = structure.msbOrBos == "msb" ? (isExternal ? "CHoCH+" : "CHoCH") : (isExternal ? "BoS+" : "BoS")
    labelX = math.round(math.avg(swingTime, time))
    structure.marketLabel := label.new(x=labelX, y=swingPrice, text=labelText, color=color.new(color.white, 100), textcolor=lineColor, style=direction == 1 ? label.style_label_down : label.style_label_up, xloc=xloc.bar_time, size=size.tiny)


if showMarketStructure == "Internal" or showMarketStructure == "Both"
    bool isInternalLowCrossed = false
    bool isInternalHighCrossed = false
    
    [internalHigh, internalLow] = detectSwings(internalLength)
   
    if close < internalLow.price and not internalLow.crossed
        internalLow.crossed := true
        isInternalLowCrossed := true
        checkMarketStructure(true, -1, internalLow.price, internalLow.time, dnColor, false)

    else if close > internalHigh.price and not internalHigh.crossed
        internalHigh.crossed := true
        isInternalHighCrossed := true
        checkMarketStructure(true, 1, internalHigh.price, internalHigh.time, upColor, false)

if showMarketStructure == "External" or showMarketStructure == "Both"
    bool isExternalLowCrossed = false
    bool isExternalHighCrossed = false

    [externalHigh, externalLow] = detectSwings(externalLength)
    
    if close < externalLow.price and not externalLow.crossed
        externalLow.crossed := true
        isExternalLowCrossed := true
        checkMarketStructure(true, -1, externalLow.price, externalLow.time, dnColor, true)

    else if close > externalHigh.price and not externalHigh.crossed
        externalHigh.crossed := true
        isExternalHighCrossed := true
        checkMarketStructure(true, 1, externalHigh.price, externalHigh.time, upColor, true)
