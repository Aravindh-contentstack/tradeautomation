# Daily Swing Structure (Pine Script)

Pine Script v6 port of [`swing_structure/detector.py`](../swing_structure/detector.py). Same rules, same branch order, confirmed in [`ethereal-coalescing-flute.md`](../ethereal-coalescing-flute.md). Python stays the source of truth. If this Pine version ever disagrees with the Python for the same candles, fix `detector.py` first, then re-port.

Swing high and swing low each run on their own independent age clock and timeout, not a shared one. See the "Revision" section near the top of `ethereal-coalescing-flute.md` for why: a shared clock let one side's frequent breaks keep resetting the timeout for both sides, freezing the quiet side indefinitely.

## Loading it into TradingView

1. Open TradingView, open the Pine Editor (bottom panel of the chart).
2. Create a new blank script, delete the placeholder content, and paste in the full contents of `daily_swing_structure.pine`.
3. Click "Add to Chart."
4. Set the chart timeframe to Daily. This detector is Daily-only for now, matching the Python.

## Inputs

**Structure Settings**
- `Lookback` (default 45): how many trailing candles define the window a swing point is picked from.
- `Timeout Candles` (default 65): how many candles can pass with a given side never redrawing before that side alone is force-redrawn. The two sides time out independently.

**Manual Controls**
- `Manual Restart Now`: check this box for exactly one candle to force an immediate redraw of both sides, then uncheck it again. See the caveat below, this only works correctly going forward while you're stepping through candles live or in replay.
- `Hold Timeout Active`: while checked, suppresses only the automatic timeout, for both sides. Real breaks still fire normally on either side. Unchecking it ("releasing" it) resets both sides' countdowns to a fresh 65 candles instead of either firing an overdue timeout.

## Important Pine-specific caveat on the manual controls

Pine Script indicator inputs are not the same as a pandas boolean Series. They're a single constant value that applies to the whole script run, not a per-candle value you can set differently in the past. The checkboxes above only produce correct, edge-triggered behavior if you flip them **while stepping forward** through candles (live, or in FX Replay one candle at a time):

- To trigger a manual restart: pause on the candle you want, check "Manual Restart Now," let the next candle close, then uncheck it.
- To hold the timeout: check "Hold Timeout Active" whenever you want the hold to start, leave it checked as long as you want the hold to last, then uncheck it when you want to release.

Scrubbing backward in history and changing these checkboxes, or reloading the chart with a checkbox already checked, will not retroactively apply it to a specific past candle. This is a hard limitation of Pine Script, not a bug in this script.

## What's on the chart

- An "HH" line and label at the actual swing high pivot candle (the real peak within the lookback window), extending forward while that level stays active. Redrawn in place on every high-side redraw, so only the current one is ever visible.
- An "LL" line and label at the actual swing low pivot candle, same behavior as above for the low side.
- A small table in the top-right corner showing the current swing high, swing low, each side's own clock (candles since that side's own last redraw), and each side's own last event, mirroring the printed table from the Python demo script.

## Verifying it

1. Apply it to a real Daily chart (e.g. XAUUSD or EURUSD). It should seed a swing high/low after the first ~45 candles ("initial seed" on both sides).
2. Step through FX Replay candle by candle and confirm each rule fires where expected: an ordinary break redraws only that side and only resets that side's own clock, a manual restart redraws both sides together, and each side times out on its own schedule regardless of how often the other side is breaking.
3. If anything looks off compared to the confirmed rules in `ethereal-coalescing-flute.md`, flag it so `detector.py` and this file can both be corrected together.
