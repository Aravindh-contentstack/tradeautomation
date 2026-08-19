// This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © AlgoAlpha

//@version=6
indicator("Equal High/Low (EQH/EQL) [AlgoAlpha]", "AlgoAlpha - EQH/EQL", true, max_lines_count = 500)

// Zone Detection Settings
use_val = input.bool(true, "Filter by Overbought/Oversold", 
     tooltip="When enabled, only creates zones when RSI shows overbought/oversold conditions", group="Detection")
state_thresh = input.int(5, "Filter Strength", minval=1, maxval=30, 
     tooltip="Sets the minimum deviation from neutral RSI (50) to consider market overbought/oversold", group="Detection")
tolerance = input.float(0.05, "Equal Level Tolerance", minval=0.001, maxval=0.1, step=0.001, 
     tooltip="Controls how closely highs/lows need to match to create an equal level zone. A lower value will give less signals.", group="Detection")

// Zone Management
expiry_age = input.int(1000, "Max Zone Age", minval=1, 
     tooltip="Number of bars after which a zone will be automatically removed", group="Management")
mitigate = input.string("body", "Sweep Type", options=["wick", "body"], 
     tooltip="Body: zone removed when price body (close) crosses it; Wick: zone removed when price wick crosses it", group="Management")
allow_rejection = input.bool(false, "Allow Rejection", 
     tooltip="When enabled with body sweep type, requires two consecutive closes beyond zone to remove it", group="Management")

// Appearance
colBull = input.color(#808080, "Bull Color", inline="msc", 
     tooltip="Color for bullish (support) zones", group="Appearance")
colBear = input.color(#5b9cf6, "Bear Color", inline="msc", 
     tooltip="Color for bearish (resistance) zones", group="Appearance")

varian = math.avg(math.abs(high-high[1]), math.abs(low-low[1]))
smoothvar = ta.ema(varian, 500)

state = ta.rsi(close, 14)

eqh = math.abs(high-high[1]) < smoothvar * tolerance
eql = math.abs(low-low[1]) < smoothvar * tolerance

rsi_filtered_eqh = use_val ? state > 50 + state_thresh : false
rsi_filtered_eql = use_val ? state < 50 - state_thresh : false
create_eqh = eqh and rsi_filtered_eqh and not eqh[1]
create_eql = eql and rsi_filtered_eql and not eql[1]

var eqlarray = array.new_line()
var eqharray = array.new_line()
var eqlarray1 = array.new_line()
var eqharray1 = array.new_line()

if create_eqh
    eqharray.unshift(line.new(bar_index-1,math.max(high, high[1]),bar_index+1,math.max(high, high[1]), color = colBear, width = 1))
    eqharray1.unshift(line.new(bar_index-1,math.max(open, open[1], close, close[1]),bar_index+1,math.max(open, open[1], close, close[1]), color = color.new(colBear, 80), width = 1))
    linefill.new(eqharray.first(), eqharray1.first(), color.new(colBear, 80))
if create_eql
    eqlarray.unshift(line.new(bar_index-1,math.min(low, low[1]),bar_index+1,math.min(low, low[1]), color = colBull, width = 1))
    eqlarray1.unshift(line.new(bar_index-1,math.min(open, open[1], close, close[1]),bar_index+1,math.min(open, open[1], close, close[1]), color = color.new(colBull, 80), width = 1))
    linefill.new(eqlarray.first(), eqlarray1.first(), color.new(colBull, 80))

plotchar(create_eqh ? 1 : na, "EQH", "▼", location.abovebar, size = size.tiny, color = colBear)
plotchar(create_eql ? 1 : na, "EQL", "▲", location.belowbar, size = size.tiny, color = colBull)

eqh_mitigated = false
eql_mitigated = false

// Process Equal High (EQH) zones
if eqharray.size() > 0
    for ln = eqharray.size() - 1 to 0
        if ln < eqharray.size()
            cL = eqharray.get(ln)
            cL_ = eqharray1.get(ln)
            yL = cL.get_y1()
            x1 = cL.get_x1()
            age = bar_index - x1
            is_body = mitigate == "body"
            cross_body = close > yL
            cross_body_confirmed = close > yL and close[1] > yL
            cross_wick = high > yL
            remove_by_mitigation = is_body ? (allow_rejection ? cross_body_confirmed : cross_body) : cross_wick
            
            if remove_by_mitigation or age > expiry_age
                eqharray.remove(ln)
                eqharray1.remove(ln)
                eqh_mitigated := true
            else
                cL.set_x2(bar_index + 1)
                cL_.set_x2(bar_index + 1)

// Process Equal Low (EQL) zones
if eqlarray.size() > 0
    for ln = eqlarray.size() - 1 to 0
        if ln < eqlarray.size()
            cL = eqlarray.get(ln)
            cL_ = eqlarray1.get(ln)
            yL = cL.get_y1()
            x1 = cL.get_x1()
            age = bar_index - x1
            is_body = mitigate == "body"
            cross_body = close < yL
            cross_body_confirmed = close < yL and close[1] < yL
            cross_wick = low < yL
            remove_by_mitigation = is_body ? (allow_rejection ? cross_body_confirmed : cross_body) : cross_wick
            
            if remove_by_mitigation or age > expiry_age
                eqlarray.remove(ln)
                eqlarray1.remove(ln)
                eql_mitigated := true
            else
                cL.set_x2(bar_index + 1)
                cL_.set_x2(bar_index + 1)

// Alerts
eqh_formed = create_eqh
eql_formed = create_eql

// Define alert conditions
alertcondition(eqh_formed, "New Equal High Formed", "A new Equal High (EQH) resistance zone has formed")
alertcondition(eql_formed, "New Equal Low Formed", "A new Equal Low (EQL) support zone has formed")
alertcondition(eqh_mitigated, "Equal High Mitigated", "Price has mitigated an Equal High (EQH) resistance zone")
alertcondition(eql_mitigated, "Equal Low Mitigated", "Price has mitigated an Equal Low (EQL) support zone")
