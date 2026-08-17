// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) https://creativecommons.org/licenses/by-nc-sa/4.0/
// © LuxAlgo
//@version=6
indicator("Liquidity Delta Profiler [LuxAlgo]", "LuxAlgo - Liquidity Delta Profiler", overlay = true, max_boxes_count = 500, max_labels_count = 500)

//---------------------------------------------------------------------------------------------------------------------}
// Constants
//---------------------------------------------------------------------------------------------------------------------{
color DATA              = #DBDBDB
color HEADERS           = #808080
color BACKGROUND        = #161616
color BORDERS           = #2E2E2E

string TOP_RIGHT        = 'Top Right'
string BOTTOM_RIGHT     = 'Bottom Right'
string BOTTOM_LEFT      = 'Bottom Left'

string TINY             = 'Tiny'
string SMALL            = 'Small'
string NORMAL           = 'Normal'
string LARGE            = 'Large'
string HUGE             = 'Huge'

string DASHBOARD_GROUP  = 'Dashboard'

//---------------------------------------------------------------------------------------------------------------------}
// Settings
//---------------------------------------------------------------------------------------------------------------------{
length = input.int(15, "Pivot Length", minval = 2, tooltip = "Lookback and lookforward length for detecting major swing highs/lows.")
maxZones = input.int(10, "Max Zones per Type", minval = 1, maxval = 40, tooltip = "Maximum number of active/historical buy and sell zones to keep on the chart.")
showSwept = input.bool(true, "Show Swept Zones", tooltip = "Keep zones visible (with dashed outlines and reduced opacity) after price sweeps them.")
filterOverlaps = input.bool(true, "Filter Overlapping Zones", tooltip = "When enabled, prevents creating new zones that overlap with existing active zones. Only the most significant (highest for BSL, lowest for SSL) level is kept.")

showDecay = input.bool(true, "Show Zone Decay", tooltip = "Displays the remaining 'health' of active zones based on the volume traded inside them. Health drops from 100% to 0%.")
zoneCapacity = input.float(5.0, "Zone Volume Capacity", minval = 1.0, tooltip = "Multiplier for average volume to determine how much volume a zone can absorb before reaching 0% health.")

enableReversals = input.bool(true, "Enable Reversal Detection", group = "Reversals", tooltip = "Detects unusual volume delta patterns during liquidity sweeps to signal potential reversals. Plots a bubble with signal type (ABS, EXH, DIV, REJ) and hover tooltip.")

dashboardInput          = input.bool(   true,       'Show Dashboard',    group = DASHBOARD_GROUP, tooltip = 'Enable or disable the dashboard.')
dashboardPositionInput  = input.string( TOP_RIGHT,  'Position',          group = DASHBOARD_GROUP, tooltip = 'Select the dashboard location.', options = [TOP_RIGHT, BOTTOM_RIGHT, BOTTOM_LEFT])
dashboardSizeInput      = input.string( TINY,       'Size',              group = DASHBOARD_GROUP, tooltip = 'Select the dashboard size.', options = [TINY, SMALL, NORMAL, LARGE, HUGE])
dashboardWindowInput    = input.int(    10,         'Eval Window (Bars)', group = DASHBOARD_GROUP, minval = 1, tooltip = 'Maximum number of bars to wait for the reversal to occur.')
dashboardHoldInput      = input.int(    3,          'Hold Time (Bars)',   group = DASHBOARD_GROUP, minval = 1, tooltip = 'Number of consecutive bars price must stay in profit (opposite direction) to be considered a win.')
dashboardHighlightInput = input.bool(   false,      'Highlight Eval Bars',group = DASHBOARD_GROUP, tooltip = 'Highlights bars actively evaluated. Yellow = Evaluating, Aqua = Holding in profit.')

bslColor = input.color(color.new(#f23645, 0), "BSL Outline Color", group = "Style", tooltip = "Color for Buy-Side Liquidity zones (above price).")
sslColor = input.color(color.new(#089981, 0), "SSL Outline Color", group = "Style", tooltip = "Color for Sell-Side Liquidity zones (below price).")

buyDeltaColor = input.color(color.new(#089981, 0), "Buy Delta Fill", group = "Style", tooltip = "Fill color when buy volume dominates a zone quadrant.")
sellDeltaColor = input.color(color.new(#f23645, 0), "Sell Delta Fill", group = "Style", tooltip = "Fill color when sell volume dominates a zone quadrant.")

//---------------------------------------------------------------------------------------------------------------------}
// Types
//---------------------------------------------------------------------------------------------------------------------{
type Zone
    float top
    float bottom
    int left
    int right
    bool swept
    bool signaled
    box[] quads
    float[] deltas
    box outline
    float volumeTraded
    float capacity
    bool wasHit
    label decayLabel

type Trade
    string type
    int dir
    float entry
    int entryBar
    bool active
    bool won
    int consecBars

//---------------------------------------------------------------------------------------------------------------------}
// Variables
//---------------------------------------------------------------------------------------------------------------------{
var Zone[] bslZones = array.new<Zone>()
var Zone[] sslZones = array.new<Zone>()

var activeTrades = array.new<Trade>()

var int absTotal = 0
var int absWins = 0
var int exhTotal = 0
var int exhWins = 0
var int divTotal = 0
var int divWins = 0
var int rejTotal = 0
var int rejWins = 0

var string parsedDashboardPosition = switch dashboardPositionInput
    TOP_RIGHT       => position.top_right
    BOTTOM_RIGHT    => position.bottom_right
    BOTTOM_LEFT     => position.bottom_left
    => position.top_right

var string parsedDashboardSize     = switch dashboardSizeInput
    TINY            => size.tiny
    SMALL           => size.small
    NORMAL          => size.normal
    LARGE           => size.large
    HUGE            => size.huge
    => size.normal

var table t_able = table.new(parsedDashboardPosition, 4, 11, bgcolor = dashboardInput ? BACKGROUND : na, border_width = 0, frame_color = dashboardInput ? BORDERS : na, frame_width = 1, force_overlay = true)

//------------------------------------------------------------------------------