# sonarLab.py: what we're keeping, what we're skipping

Notes distilled from `temp-reference/order-blocks/sonarLab.py`, after walking
through its logic line by line. Purpose: capture the decisions made in that
discussion so a future session can update `swing_structure/order_blocks.py`
without re-deriving them. Nothing here has been implemented yet. Companion to
`fluxCharts-logic-notes.md`, same purpose, different script. See
`roadmap/supply-and-demand.md`'s "OB lifecycle" section for how these ideas
actually landed in OUR rules.

## 1. Trigger: raw price momentum (ROC), not structure, not volume

```
pc = (open - open[4]) / open[4] * 100
ta.crossunder(pc, -sens)  → bearish OB trigger
ta.crossover(pc, sens)    → bullish OB trigger
```

ROC (Rate of Change): how much price moved, in percent, over a fixed 4-candle
window. No structure break, no volume, purely a velocity/momentum measure.
The `sens` scaling looks like it has a units quirk (divided by 100 while
compared against a value already expressed as a percentage), don't copy the
numbers, the CONCEPT (use price velocity as a trigger) is what mattered.
This became the seed for our own ATR-based displacement-candle idea, see the
roadmap doc, since it's a computable substitute for the volume idea that got
blocked by our data not having a volume column.

## 2. Anchor search: same "last opposite color" idea, but window-bounded

```
for i = 4 to 15 by 1
    if close[i] > open[i] ...  // first color-flip candle in that window
```

Starts the search 4 bars back (matching the ROC's own window) and gives up
at 15 bars back if nothing's found, rather than scanning unbounded. We do
NOT use the color-scan approach any more (superseded by the ATR-based
displacement method), but the "bound how far back the search goes" idea is
worth keeping in mind as a robustness point regardless of which
candle-selection method is used: an unbounded backward scan can walk
arbitrarily far into stale history if a leg has a very long uninterrupted
run of one candle color.

## 3. Cooldown between triggers, repurposed for us

```
if ob_created and cross_index - cross_index[1] > 5
```

Requires 5+ bars between trigger events (of either direction) before a new
OB is created, to stop a noisy ROC from spawning near-duplicate OBs in
rapid succession. Important correction made during discussion: this is a
TIME-spacing rule between trigger events, not a PRICE-overlap check between
existing OB zones (unlike `fluxCharts.py`'s combine logic, see its own notes
file section 6). Two OBs spaced more than 5 bars apart in time can still end
up with overlapping price zones under this rule, sonarLab does nothing to
prevent that.

We are not using this for cluster avoidance (see `fluxCharts-logic-notes.md`
and the roadmap doc's own cluster-avoidance rule, which ended up rejecting
overlap-merging entirely for different-anchor OBs). Instead, this idea got
repurposed in the roadmap as a distance/recency filter for TARGET OB
selection (an OB can be perfectly valid while still being unrealistically
far away to serve as a target), a different problem than the one it solves
in this script.

## 4. Mitigation and invalidation, collapsed into one removal step

```
OBBullMitigation = "Close" ? close[1] : low
if OBBearMitigation > top: remove the OB entirely   // invalidation-equivalent
if high > bot: alert('Price inside Bearish OB')      // mitigation-equivalent
```

The removal condition maps to our confirmed invalidation rule 1 (full
break-through) only, nothing like our rules 2/3a/3b exists here. The
separate alert condition (fires whenever price is simply touching the zone)
is the closest thing to our "mitigated" concept, but it's a live, repeating
notification, not a stored one-time flag the way we built it.

**Not applicable to us**: the default "Close" mode uses the PREVIOUS
candle's close (`close[1]`), not the current one, to avoid reacting to a
still-forming candle in live trading. Our `order_blocks.py` only ever
processes fully-closed historical rows in a backtest, there's no
"in-progress" candle to wait out, so this specific quirk doesn't transfer.
