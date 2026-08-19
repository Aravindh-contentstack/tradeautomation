// This source code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/

//@version=6
indicator("High/Low Resistance Liquidity", shorttitle="HRLR/LRLR", overlay=true, max_lines_count=500, max_labels_count=500)

// --- Settings ---
grp_pivots = "Pivot & Structure Settings"
pivotLeft  = input.int(5, "Pivot Left Bars", minval=1, group=grp_pivots, tooltip="Number of bars to the left to confirm a structural high/low.")
pivotRight = input.int(5, "Pivot Right Bars", minval=1, group=grp_pivots, tooltip="Number of bars to the right to confirm a structural high/low.")

grp_lrlr = "LRLR Settings (Low Resistance)"
lrlrToleranceTicks = input.int(10, "EQH/EQL Tolerance (Ticks)", minval=0, group=grp_lrlr, tooltip="Maximum tick distance between two structural points to be considered Equal Highs or Equal Lows (LRLR).")

grp_visuals = "Display & Visuals"
colLRLR            = input.color(color.new(#2962FF, 0), "LRLR Color (EQH/EQL)", group=grp_visuals)
colHRLR            = input.color(color.new(#FF5252, 0), "HRLR Color (Swept Pivot)", group=grp_visuals)
showStandardPivots = input.bool(false, "Show Standard Unmitigated Pivots", group=grp_visuals)
colStd             = input.color(color.new(#9E9E9E, 50), "Standard Pivot Color", group=grp_visuals)
showLabels         = input.bool(true, "Show Text Labels", group=grp_visuals)


// --- Data Structures ---
type LiquidityPool
    float priceLevel
    int   startBar
    bool  isHigh
    int   poolType // 0 = Standard, 1 = LRLR, 2 = HRLR
    line  ln
    label lbl

var array<LiquidityPool> activePools = array.new<LiquidityPool>()

// Track historical pivots to compare new ones against
var array<float> phHistory = array.new<float>()
var array<float> plHistory = array.new<float>()

// --- Pivot Detection ---
float ph = ta.pivothigh(high, pivotLeft, pivotRight)
float pl = ta.pivotlow(low, pivotLeft, pivotRight)

bool newPH = not na(ph)
bool newPL = not na(pl)

// The bar index where the pivot ACTUALLY occurred
int phBar = bar_index - pivotRight
int plBar = bar_index - pivotRight

float tickSize = syminfo.mintick

// --- Evaluate New Pivot Highs ---
if newPH
    int poolType = 0 // 0 = Standard
    float lastPH = na
    
    if array.size(phHistory) > 0
        lastPH := array.get(phHistory, array.size(phHistory) - 1)
        
    if not na(lastPH)
        // Check LRLR (Equal Highs)
        if math.abs(ph - lastPH) / tickSize <= lrlrToleranceTicks
            poolType := 1 // LRLR
        // Check HRLR (Swept High)
        else if ph > lastPH
            poolType := 2 // HRLR
            
    array.push(phHistory, ph)
    if array.size(phHistory) > 500
        array.shift(phHistory)
        
    if poolType > 0 or showStandardPivots
        color  col   = poolType == 1 ? colLRLR : (poolType == 2 ? colHRLR : colStd)
        string style = poolType == 1 ? line.style_solid : (poolType == 2 ? line.style_dotted : line.style_dashed)
        string txt   = poolType == 1 ? "LRLR (EQH)" : (poolType == 2 ? "HRLR (Sweep)" : "Liq")
        
        line ln = line.new(phBar, ph, bar_index, ph, color=col, style=style, width=poolType > 0 ? 2 : 1)
        label lbl = na
        if showLabels
            lbl := label.new(phBar, ph, text=txt, color=color.new(color.white, 100), textcolor=col, style=label.style_label_left, size=size.tiny)
            
        array.push(activePools, LiquidityPool.new(ph, phBar, true, poolType, ln, lbl))

// --- Evaluate New Pivot Lows ---
if newPL
    int poolType = 0 // 0 = Standard
    float lastPL = na
    
    if array.size(plHistory) > 0
        lastPL := array.get(plHistory, array.size(plHistory) - 1)
        
    if not na(lastPL)
        // Check LRLR (Equal Lows)
        if math.abs(pl - lastPL) / tickSize <= lrlrToleranceTicks
            poolType := 1 // LRLR
        // Check HRLR (Swept Low)
        else if pl < lastPL
            poolType := 2 // HRLR
            
    array.push(plHistory, pl)
    if array.size(plHistory) > 500
        array.shift(plHistory)
        
    if poolType > 0 or showStandardPivots
        color  col   = poolType == 1 ? colLRLR : (poolType == 2 ? colHRLR : colStd)
        string style = poolType == 1 ? line.style_solid : (poolType == 2 ? line.style_dotted : line.style_dashed)
        string txt   = poolType == 1 ? "LRLR (EQL)" : (poolType == 2 ? "HRLR (Sweep)" : "Liq")
        
        line ln = line.new(plBar, pl, bar_index, pl, color=col, style=style, width=poolType > 0 ? 2 : 1)
        label lbl = na
        if showLabels
            lbl := label.new(plBar, pl, text=txt, color=color.new(color.white, 100), textcolor=col, style=label.style_label_left, size=size.tiny)
            
        array.push(activePools, LiquidityPool.new(pl, plBar, false, poolType, ln, lbl))


// --- Real-Time Mitigation Engine ---
if array.size(activePools) > 0
    for i = array.size(activePools) - 1 to 0
        LiquidityPool p = array.get(activePools, i)
        
        bool mitigated = false
        if p.isHigh and high >= p.priceLevel
            mitigated := true
        else if not p.isHigh and low <= p.priceLevel
            mitigated := true
            
        if mitigated
            line.set_x2(p.ln, bar_index)
            if not na(p.lbl)
                label.set_x(p.lbl, bar_index)
                label.set_text(p.lbl, p.poolType == 1 ? "LRLR (Mitigated)" : (p.poolType == 2 ? "HRLR (Mitigated)" : "Liq (Mitigated)"))
            // Remove from active array so it stops extending
            array.remove(activePools, i)
        else
            // Extend the lines and labels forward
            line.set_x2(p.ln, bar_index)
            if not na(p.lbl)
                label.set_x(p.lbl, bar_index)
