// This source code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
//@malk1903

//@version=5
indicator(title='ICT Liquidty H/L [MK]', shorttitle = 'ICT Liquidty H/L [MK]',overlay=true, max_boxes_count = 500, max_labels_count = 500)

plotLiq = input.bool(defval=true, title='Liquidity Highs/Lows', group='Enable/Disable Section---------------------------------------------', inline = '01')

/////////////////////////////////////////////////////////////////////////Liquidity Equal Highs
pvtTopColor = input.color(defval=color.new(color.green,85), title='Top Color', group='Liquidity-------------------------------------------------', inline='1')
pvtBtmColor = input.color(defval=color.new(color.red,85), title='Bottom Color', group='Liquidity-------------------------------------------------',inline = '1')
pvtStyle = line.style_solid
pvtMax = input.int(defval=30, title='Maximum Liquidity Displayed', minval=1, maxval=500, group='Liquidity-------------------------------------------------', tooltip='Minimum = 1, Maximum = 500')
delline = input.bool(defval=true, title='Delete After X Bars Through Line', group='Liquidity-------------------------------------------------')
mitdel = input.int(defval=15, title='Mitigated+Line Delete (X Bars)', minval=1, maxval=100, group='Liquidity-------------------------------------------------', tooltip='Line will remain on chart until this many bars after first broken through. Minimum = 1, Maximum = 500')
linebrk = input.bool(defval=true, title='Color After Close Through Line', group='Liquidity-------------------------------------------------')
mitdelcol = input.color(defval=(color.new(#34eb40,0)), title='Mitigated Color', group='Liquidity-------------------------------------------------', tooltip = 'Once broken, line will change to this colour until deleted. Line deletion is controlled by (Mitigated+Line Delete) input control')
//del_pc = input.bool(defval=false, title='', group='Liquidity-------------------------------------------------', inline="m")
//mitdel_pc = input.int(defval=300, title='Only show lines within x% of current price', minval=1, maxval=500, step=50, group='Liquidity-------------------------------------------------', inline="m")
//
brkstyle = line.style_dashed
var line[] _lowLiqLines  = array.new_line()
var line[] _highLiqLines  = array.new_line()

//Functions
isPvtHigh(_index, __high) =>
    __high[_index+2] < __high[_index+1] and __high[_index+1] > __high[_index]

    //  |   <-- pivot high
    // |||  <-- candles
    // 210  <-- candle index

isPvtLow(_index, __low) =>
    __low[_index+2] > __low[_index+1] and __low[_index+1] < __low[_index]

    // |||  <-- candles
    //  |   <-- pivot low
    // 210  <-- candle index

//Function to Calculte Line Length
_controlLine(_lines, __high, __low) =>
    if array.size(_lines) > 0
        for i = array.size(_lines) - 1 to 0 by 1
            _line = array.get(_lines, i)
            _lineLow = line.get_y1(_line)
            _lineHigh = line.get_y2(_line)
            _lineRight = line.get_x2(_line)
            if na or (bar_index == _lineRight and not((__high > _lineLow and __low < _lineLow) or (__high > _lineHigh and __low < _lineHigh)))
                line.set_x2(_line, bar_index + 1)
            //deletes line if not within X% of current last price
            //pch = (mitdel_pc/100) + 1
            //pcl = 1 - (mitdel_pc/100)
            //cprice = close
            //highpc = (cprice * pch)
            //lowpc = (cprice * pcl)
            //if del_pc
                //if  _lineLow < close + mitdel_pc  //) and > (close*0.98)) //if Y2 < close*1.05 
                    //line.set_color(_line, color.new(color.white,50))
                    //line.delete(_line)
            ///deletes line if more than X bars pass through
            if _lineRight > bar_index[mitdel] and _lineRight < bar_index[0] and linebrk
                line.set_color(_line,mitdelcol)
                line.set_style(_line, brkstyle)
            if _lineRight < bar_index[mitdel] and delline
                line.delete(_line)

//Pivot Low Line Plotting
if isPvtLow(0, low) and plotLiq
    _lowPVT = line.new(x1=bar_index - 1, y1=low[1], x2=bar_index, y2=low[1], extend=extend.none, color=pvtBtmColor, style=pvtStyle)
    if array.size(_lowLiqLines) >= pvtMax
        line.delete(array.shift(_lowLiqLines))
    array.push(_lowLiqLines, _lowPVT)

//Pivot High Line Plotting
if isPvtHigh(0, high) and plotLiq
    _highPVT = line.new(x1=bar_index - 1, y1=high[1], x2=bar_index, y2=high[1], extend=extend.none, color=pvtTopColor, style=pvtStyle)
    if array.size(_highLiqLines) >= pvtMax
        line.delete(array.shift(_highLiqLines))
    array.push(_highLiqLines, _highPVT)

if plotLiq
    _controlLine(_lowLiqLines, high, low)
    _controlLine(_highLiqLines, high, low)