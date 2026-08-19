// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) https://creativecommons.org/licenses/by-nc-sa/4.0/
// © OutofOptions
//@version=6
indicator("Equal Highs and Lows", "Eql H&L", true)

import outofoptions/OutofOptionsHelperLibrary/18 as H


var minCandleDist   = input.int(0, "Minimum Distance (candles/bars)", minval=0, step=1, tooltip="Minimum distance between the candles forming equal highs/lows", display = display.none, group="Settings")
var plotNearest     = input.bool(false, "Show Nearest EQL/EQH Price", tooltip = "If enabled the closest equal high/low price will be highlighted in the price bar", group="Settings")
var realtime        = input.bool(false, "Realtime Mode", tooltip = "If enabled will identify Equal Highs/Lows before candle close", group="Settings")

var lineColorHigh   = input.color(color.green, "Line Color: Highs", group="Appearance", inline="color")
var lineColorLow    = input.color(color.red, "Lows", group="Appearance", inline="color")
var lineWidth       = input.int(1, "Line Width", minval=1, maxval=5, step=1, group="Appearance", display = display.none, inline="line")
var lineStyle       = input.enum(H.ln.sol, "Style", group="Appearance", display = display.none, inline="line")
var extendRight     = input.bool(false, "Extend Line Right", group="Appearance", tooltip="Whether or not to extend the Equal high/low lines right")

var H.liquidity[] hi = array.new<H.liquidity>()
var H.liquidity[] lo = array.new<H.liquidity>()

var UPlines = map.new<float, line>()
var DWlines = map.new<float, line>()
var simple int minDistance = H.oneBar() * minCandleDist
var string lineRenderStyle = H.lineStyle(lineStyle)

var float lastEQH = 0
var float lastEQL = 0
var float Hprice = 0
var float Lprice = 0

while hi.size() > 0
    Hprice := hi.last().price
    if Hprice < high
        line ln = UPlines.get(Hprice)
        if not na(ln)
            lastEQH := 0
            ln.delete()
            UPlines.remove(Hprice)
        hi.pop()
        Hprice := 0
    else
        if lastEQH == 0 and plotNearest
            keys = UPlines.keys()
            if keys.size() > 0
                lastEQH := keys.last()
        break

while lo.size() > 0
    Lprice := lo.last().price
    if Lprice > low
        line ln = DWlines.get(Lprice)
        if not na(ln)
            lastEQL := 0
            ln.delete()
            DWlines.remove(Lprice)
        Lprice := 0
        lo.pop()
    else
        if lastEQL == 0 and plotNearest
            keys = DWlines.keys()
            if keys.size() > 0
                lastEQL := keys.last()
        break

if realtime or barstate.isconfirmed
    int otime = time
    if Hprice == high
        H.liquidity last = hi.last()
        int tm = math.min(last.otime, last.time)
        if (time - tm) > minDistance
            if plotNearest
                lastEQH := Hprice
            line ln = UPlines.get(high)
            if na(ln)
                UPlines.put(Hprice, line.new(x1 = tm, y1=Hprice, x2 = time, y2=Hprice, width=lineWidth, color=lineColorHigh, style=lineRenderStyle, xloc = xloc.bar_time, extend=extendRight ? extend.right : extend.none))
            else
                ln.set_x2(time)
        else
            otime := tm
            
    hi.push(H.liquidity.new(high, time, otime=otime))

    otime := time
    if Lprice == low
        H.liquidity last = lo.last()
        int tm = math.min(last.otime, last.time)
        if (time - tm) > minDistance
            line ln = DWlines.get(Lprice)
            if plotNearest
                lastEQL := Lprice
            if na(ln)
                DWlines.put(Lprice, line.new(x1 = tm, y1=Lprice, x2 = time, y2=Lprice, width=lineWidth, color=lineColorLow, style=lineRenderStyle, xloc = xloc.bar_time, extend=extendRight ? extend.right : extend.none))
            else
                ln.set_x2(time)
        else
            otime := last.otime

    lo.push(H.liquidity.new(low, time, otime=otime))

plot(lastEQH, "EQH", color=lineColorHigh, display = display.price_scale)
plot(lastEQL, "EQL", color=lineColorLow, display = display.price_scale)