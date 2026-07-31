# Market Structure (FXR Script)

Nine structure indicators, three per timeframe:

| Script | Timeframe | Mechanism | linewidth |
|---|---|---|---|
| [`daily_swing_structure.py`](daily_swing_structure.py) | Daily | Williams Fractal, n=20 | 9 |
| [`daily_internal_structure.py`](daily_internal_structure.py) | Daily | Williams Fractal, n=8 | 8 |
| [`daily_fractal_structure.py`](daily_fractal_structure.py) | Daily | Williams Fractal, n=2 | 1 |
| [`h4_fractal_structure.py`](h4_fractal_structure.py) | 4H | Williams Fractal, n=2 | 2 |
| [`h4_internal_structure.py`](h4_internal_structure.py) | 4H | Williams Fractal, n=8 | 3 |
| [`h4_swing_structure.py`](h4_swing_structure.py) | 4H | Williams Fractal, n=20 | 4 |
| [`h1_fractal_structure.py`](h1_fractal_structure.py) | H1 | Williams Fractal, n=2 | 5 |
| [`h1_internal_structure.py`](h1_internal_structure.py) | H1 | Williams Fractal, n=8 | 6 |
| [`h1_swing_structure.py`](h1_swing_structure.py) | H1 | Williams Fractal, n=20 | 7 |

**Updated July 29 2026**: Daily used to run three different mechanisms (a lookback window plus timeout, an ATR zigzag, and a Williams Fractal at n=2), one per tier, while 4H ran one mechanism at three scales. Daily has now been ported to the same one-mechanism-three-scales approach as 4H, ahead of the originally planned H1-first order. See [`../roadmap/detection-method-decision.md`](../roadmap/detection-method-decision.md)'s "Daily port" section for the full reasoning, including the caveat that Daily's n=8 (internal tier) has never been checked against real Daily bars.

**Also added July 29 2026**: H1 now has its own trio (`h1_fractal_structure.py`, `h1_internal_structure.py`, `h1_swing_structure.py`), carrying over n=2/8/20 unchanged from the 4H trio as a placeholder. See the decision doc's "For the H1 port" section: none of these three numbers have been checked against real H1 bars, and H1's trend-to-noise ratio is expected to differ from 4H's, which is the one thing Williams Fractal is genuinely sensitive to.

Why the tiers stay fully independent of each other despite sharing one mechanism is also in that same decision doc. Read it before changing any of the nine.

[`timeframe_probe.py`](timeframe_probe.py) is a throwaway spike, not an indicator. See "Timeframe isolation" below.

## Equilibrium (premium/discount)

Two more scripts, [`h4_swing_premium_discount.py`](h4_swing_premium_discount.py) and [`h4_internal_premium_discount.py`](h4_internal_premium_discount.py), each a full duplicate of the matching structure script's fractal-detection engine (`h4_swing_structure.py`'s n=20, `h4_internal_structure.py`'s n=8) with a drawing block appended. They draw a single thin ("EQ"-labeled) `horizontalLine` at that tier's current equilibrium price, `(effectiveHigh + effectiveLow) / 2` (see below), nothing else.

An earlier version of both scripts also drew a shaded, "PREMIUM"/"DISCOUNT"-labeled `rectangle` spanning the range's two corners in time. That box's left corner sat at whichever pivot's own (possibly old) timestamp, so as the chart accumulated history the box visually grew to cover every prior swing leg rather than just the current one, reading as premium/discount for every past structure at once instead of just the latest, which is not what was wanted. Removed entirely rather than fixed, since a `horizontalLine` has no time corners to get wrong in the first place, it is simply the current equilibrium price, full chart width, always current, and it is deleted and redrawn every closed candle (`deleteDrawingByCondition` then redraw) the same way the box was, since the price itself can still change candle to candle. Each script reserves its own `EQ_LINE_COLOR` (`h4_swing_premium_discount.py` orange, `h4_internal_premium_discount.py` cyan, both linewidth 1), matched via `opts['linecolor']`, so neither script's cleanup can touch the other's line, or any trendLine drawn by the structure scripts.

There is no existing TradingView reference indicator for this the way there is for swing/internal/fractal structure, so these two scripts are themselves the visual verification method: load one in fxreplay, and check the line's price against the tier's own swing high/low and current direction by eye.

**Both scripts recalibrate as the trend runs.** Each tier's raw `swing_high`/`swing_low` are Williams Fractal pivots that only update once fully confirmed (`periods` candles beyond it with no reversal), and `h4_swing_structure.py`'s own docstring already documents that this makes a tier "go quiet in a strong one-directional trend": once a side breaks and price keeps running, that pivot freezes at the already-broken level (the same applies to `h4_internal_structure.py`'s pivots, just not documented there in as many words). Using either raw column directly would freeze the equilibrium right along with it instead of recalibrating as the trend extends. The fix (`swing_structure/current_range.py`'s `compute_current_range` in Python, ported into each script as `h4SwingRunningMaxHigh`/`h4SwingRunningMinLow` or the `h4Internal*` equivalents) extends whichever side is stale with the running high (or low) actually made since that side's pivot last changed, while the side that hasn't been broken keeps reading its own pivot live, unaffected. As of this note, the Python side (`swing_structure/premium_discount.py`) applies this to both `h4_swing` and `h4_internal`. The FXR port of the fix into `h4_internal_premium_discount.py` itself is still outstanding, so that script currently draws its equilibrium from the tier's raw, unrecalibrated pivots (`h4_swing_premium_discount.py` already has the FXR-side fix).

**Found while wiring this up**: the docs' quick example shows `horizontalLine(time, price, styles, text)`, but the editor's own type-checker rejects that shape. Three successive errors triangulated the real signature. `horizontalLine(time, price, styles, text)` (4 args) errored "Expected 1-3 arguments" (no leading `time` argument exists at all, since a horizontal line spans the whole chart width rather than starting at a point the way `horizontalRay` does). `horizontalLine(price, wrongPrice, styles)` (putting a second number in position 2) errored "Type 'number' has no properties in common with type 'HorzlineLineToolOverrides'" (position 2 is the styles object, not a second price). `horizontalLine(price, { ...styles, text: 'EQ' })` errored "'text' does not exist in type 'HorzlineLineToolOverrides'" (that type carries no text field). The real shape is `horizontalLine(price, styles?, text?)`, the label as its own 3rd positional argument: `horizontalLine(equilibrium, { linecolor, linewidth }, 'EQ')`. Same class of docs-vs-editor mismatch as the `linecolor`/`band.line` findings above, worth remembering for any future line-tool call in this API.

## Timeframe isolation

The requirement: 4H markings must not appear on a Daily chart, and Daily markings must not appear on a 4H chart.

**FXR Script cannot read the chart's current timeframe.** Confirmed against the live docs MCP server. There is no `timeframe`, `period`, `resolution` or `syminfo` global. `indicator()` accepts only `onMainPanel`, `format` and `precision`. `input.timeframe` reports what the *user picked* in a dropdown, not what the chart is on. And `mtf.*` (BETA) reads *other* timeframes but cannot report the chart's own. So the timeframe has to be inferred.

**The inference rule: the MINIMUM positive `time(i) - time(i+1)` over the last 20 bars.** Not the median, and not simply `time(0) - time(1)`. Session and weekend gaps only ever make a delta *larger*, never smaller, so the minimum is the true bar interval and is gap-proof by construction. This matters most on Daily forex, where the Friday-to-Monday delta is three days, but plenty of midweek deltas are exactly one day, so the minimum still lands on 86400000. Compare against `MY_TIMEFRAME_MS`: 86400000 for Daily, 14400000 for 4H, 3600000 for H1.

Three things the guard does, in order, and the reason for each:

1. **Bail out while fewer than 5 usable deltas exist**, without touching the stored spacing. A warming-up bar must not look like a timeframe change.
2. **On a mismatch, draw nothing and clean up.** This is what enforces the requirement.
3. **On arriving from another timeframe, reset every top-level `let`.** Without this, Daily to 4H to Daily resumes the state machine on top of state accumulated from the other timeframe's bars.

**The guard must sit before the `time(0) === lastSeenTime` new-bar gate, not after.** A wrong-timeframe bar that consumed the new-bar gate would desynchronise it, so the state machine would skip that bar when the user switched back.

### Self-only drawing cleanup, and why every script has a distinct linewidth

`deleteDrawingByCondition` can only inspect `StoredShape`, which is `{ chartPoints, overrideOptions, shapeType }`. Matching on `shapeType` alone would delete *every* `trendLine` on the chart, including a sibling tier's. So each script tags its drawings with a unique `linewidth` (Daily tiers 1, 8 and 9, 4H tiers 2, 3 and 4, H1 tiers 5, 6 and 7) and deletes only its own.

Reading that tag back needs **bracket access**, `drawing.overrideOptions['linewidth']`, not dotted access. Dotted access is a type error, for the same reason `linecolor` was abandoned in Design 3 below: `overrideOptions` is the `DrawingOverrides` union across every drawing tool, so a given field exists on only some members. **If the editor rejects the bracket form too, the fallback is to give a script a different `shapeType` from its siblings** (`rayLine` instead of `trendLine`) and match on `shapeType` after all.

**Retired July 29 2026**: `daily_swing_structure.py` used to be the one exception here, since it drew horizontal rays rather than trendLines and so was matched by `shapeType === 'horizontal_ray'` instead of by linewidth. That exception no longer applies: as part of the Daily port to the fractal-family mechanism (see the table above), `daily_swing_structure.py` now draws trendLines like every other tier, and needs its own linewidth tag (9) the same way. Until that port, this was also a latent hazard worth naming: `daily_internal_structure.py` and `daily_fractal_structure.py` already shared linewidth 1, which meant either one's timeframe-mismatch cleanup could delete the other's lines. Fixed at the same time by moving `daily_internal_structure.py` to linewidth 8.

### Three unknowns the probe answers

`timeframe_probe.py` exists because guessing wrong on any of these means rewriting all nine scripts. Load it alone, switch the chart between Daily and 4H, and read the labels on the drawn segments.

1. **On a timeframe change, are the previous timeframe's script-drawn drawings cleared automatically, or left behind?** The most important one. If fxreplay clears them, the gate alone achieves full isolation and `TF_CLEANUP_ON_MISMATCH` can be set to `false` in all nine scripts. Read it by switching Daily to 4H and checking whether the `TF=1D` segments are still there.
2. **Does top-level `let` state survive a timeframe change?** Read `ticks` in the label: if it keeps climbing across a switch rather than restarting near 0, state survives, and the reset in step 3 above is required. A label reading `TF=4h prevTF=1D` is direct proof.
3. **Does `overrideOptions['linewidth']` read without a type error?** Read `lwProbe` in the label. `ok:<n>` means it worked, `undef` means it read without erroring but the field was absent. A compile error or throw means the fallback above is needed.

**Findings: not yet recorded.** Run the probe in fxreplay and write the three answers here. The nine scripts currently ship with `TF_CLEANUP_ON_MISMATCH = true`, which is the safe assumption (it does harmless extra work if drawings turn out to be auto-cleared).

## Keeping the nine scripts in sync

FXR Script has no import or module mechanism, so the timeframe guard block is **duplicated verbatim** in all nine scripts, bar the two per-script constants. A change to it has to be applied nine times by hand. Each script's copy carries a comment listing the other eight.

The three scripts within each timeframe are otherwise a one-line diff from each other: default `periods`, state-variable prefix, and linewidth. Prefer regenerating them from one another over hand-editing each.

## Original notes: Daily Swing Structure

**Superseded July 29 2026**: everything below in this section, and the Design 1/2/3 history right after it, describes `daily_swing_structure.py`'s OLD mechanism (a lookback window plus timeout, powered by `swing_structure/detector.py`) and its OLD visual design (a pair of horizontal rays, redrawn every tick). Neither is current: the script now runs a Williams Fractal at n=20 with a cross-then-redraw trendLine visual, matching its 4H sibling `h4_swing_structure.py`. Kept here as design history rather than deleted, since the reasoning that ruled out Designs 1 and 2 (and the docs-MCP-server findings and open questions further down this file) remains accurate and relevant to any future FXR Script drawing work, not specific to the retired mechanism.

FXR Script port of [`swing_structure/detector.py`](../swing_structure/detector.py), written for fxreplay.com. fxreplay does not run Pine Script. It has its own language, FXR Script, which is why [`../pinescripts/daily_swing_structure.py`](../pinescripts/daily_swing_structure.py) cannot simply be pasted in there. That Pine file stays as the working TradingView version, untouched. Python (`detector.py`) remains the source of truth for the rules. If this file ever disagrees with the Python for the same candles, fix `detector.py` first, then re-port.

## Status: confirmed working in fxreplay

`daily_swing_structure.py` is a full reconstruction of `detector.py`'s rules, confirmed working end to end in fxreplay: the state machine (persistent state, new-bar gating, the six-step per-side precedence) tracks correctly through replay, and the swing-high/swing-low rays now show exactly one of each at a time, updating in place as levels change. The visuals went through three designs before landing on the current one.

**Design 1 (dropped): `horizontalRay` with id tracking.** Store the id `horizontalRay(...)` returns in a persistent `let`, and call `deleteDrawingById(id)` on it before drawing a new one each redraw, mirroring the original Pine script's `line.delete()`-then-`line.new()` pattern. This looked completely correct when running the script fresh from scratch (a full run left exactly one correct ray per side), which confirmed the state machine and the delete/create logic were both right in a single continuous execution. But stepping through fxreplay's **replay** one candle at a time left old rays behind: the swing low would break, a new ray would appear, but the old one never got removed. This is odd given the numeric state (`swingHigh`/`swingLow`/`seeded`/the clocks) tracks correctly through the exact same replay stepping the whole time, meaning top-level `let` state clearly does survive across replay steps in general. Why `deleteDrawingById(id)` specifically failed to find the old ray during replay, while the identical approach worked in one continuous run, is still not fully understood.

**Design 2 (dropped): `band.line`.** Found via the FXR docs MCP server (see below): `band.line(name, value, color, linestyle?, linewidth?, visible?)` is purpose-built for persistent, continuously-updated levels like support/resistance lines, and its own docs warn that reusing the same `name` reuses the same `id`, i.e. it updates in place. That looked like a clean way to sidestep the id problem in Design 1 entirely. It threw `ReferenceError: band is not defined` when called from `onTick`, though. The only working example of it in the docs calls it inside `init`, with hardcoded static values, matching the same restriction the docs state for `input.*` ("must be declared only inside the init control block"). `band` is apparently init-only, so it cannot express a level that changes every tick.

**Design 3 (current, confirmed working): `horizontalRay` with `deleteDrawingByCondition`.** Same `horizontalRay` call as Design 1, but the delete is now content-based instead of id-based. `deleteDrawingByCondition(condition)` inspects the chart's actual current drawings rather than a remembered id, so it doesn't depend on this script's own memory of what it drew last. Matching on `shapeType === 'horizontalRay'` errored: "types 'MultiplePointShapeTypes' and '\"horizontalRay\"' have no overlap," meaning that isn't the shape's real internal name. The real value is `'horizontal_ray'` (snake_case, matching the snake_case shape names TradingView's real Charting Library API uses elsewhere, e.g. `horizontal_line`/`vertical_line`/`long_position`), confirmed by testing in fxreplay. `overrideOptions.linecolor` was dropped rather than pursued further (it errored too, since `overrideOptions` is a big union type, `DrawingOverrides`, across every drawing tool, so `linecolor` only exists on some of its members): every `horizontal_ray`-shaped drawing is deleted and both rays are recreated every tick (using `swingHigh`/`swingLow`'s persisted pivot time, `swingHighPivotTime`/`swingLowPivotTime`), instead of trying to distinguish red from green by inspecting `overrideOptions`.

## How the FXR docs MCP server was used

The official FXR Script docs at [custom-indicators.gitbook.io](https://custom-indicators.gitbook.io/custom-indicators-docs) expose an MCP server at `https://custom-indicators.gitbook.io/custom-indicators-docs/~gitbook/mcp`, registered in this project via `claude mcp add fxr-script-docs --scope user --transport http https://custom-indicators.gitbook.io/custom-indicators-docs/~gitbook/mcp`. It exposes a `searchDocumentation` tool and a `getPage` tool that return the real, un-summarized documentation content directly, which is how `band.line`, the full `horizontalRay` example, and several other details below were found. Earlier research in this file relied on fetching gitbook pages as plain web pages, which passed the content through an intermediate summarizer and lost detail. Prefer the MCP tools over plain web fetches for anything about this API going forward.

## Open questions, resolved via the editor's own autocomplete and the docs MCP server

Several things about FXR Script were not documented anywhere obvious and had to be confirmed directly:

1. `horizontalRay`'s real signature, confirmed via the editor's hover tooltip: `function horizontalRay(time: number, price: number, styles?: HorzRayLineToolOverrides, text?: string): string`.
2. `horizontalRay` does not redraw in place when called again with the same label. Every call creates a brand new ray.
3. The real delete function for an id-based drawing, confirmed via autocomplete, is `deleteDrawingById(id)`. A first guess, `removeEntity`, based on TradingView's real Charting Library API (which this project's option types otherwise trace back to via `PriceLabelLineToolOverrides`), turned out wrong for this specific function name.
4. A plain top-level `let` variable keeps its value across separate `onTick` calls, confirmed yes, but only between top-level state and `onTick`/`init` themselves. A helper function declared alongside them does NOT share that state: a first attempt factored the delete-then-create logic into `redrawSwingHigh`/`redrawSwingLow` helpers, which produced a runtime error, `ReferenceError: swingHighRayId is not defined`, inside the helper, even though the same variable is read and written correctly from directly inside `onTick`'s own body.
5. `deleteDrawingByCondition(condition: (drawing: Readonly<StoredShape>) => boolean): void` also exists, confirmed via autocomplete, for deleting based on a drawing's own properties rather than a remembered id. `StoredShape` has exactly three fields (confirmed via the editor's autocomplete): `chartPoints`, `overrideOptions`, `shapeType`. This is what Design 3 above uses.
6. `band` (as in `band.line(...)`) only works inside `init`, not `onTick`, per Design 2 above.
7. Whether any table or grid API exists at all for a status readout like the Pine version's table. None turned up, so it is left out for now.

## Loading it into fxreplay

1. Open fxreplay and its custom indicator (FXR Script) editor.
2. Paste in the current contents of `daily_swing_structure.py`.
3. Run it and check the chart: one red swing-high ray with an "HH" label, one green swing-low ray with an "LL" label, each updating in place as the levels change.

## Still to verify

The core swing-high/swing-low tracking and visuals are confirmed working. Not yet specifically re-verified against this final version:
- `Manual Restart Now` and `Hold Timeout Active` behaving correctly (same one-shot/edge-triggered caveats as the Pine version, see Inputs below).
- The exact same-day precedence order (conflict check, manual restart, break, hold released, timeout, ordinary day) producing identical results to `detector.py` on a shared set of test candles.

## Inputs

**Structure Settings**
- `Lookback` (default 45): how many trailing candles define the window a swing point is picked from.
- `Timeout Candles` (default 65): how many candles can pass with a given side never redrawing before that side alone is force-redrawn. The two sides time out independently.

**Manual Controls**
- `Manual Restart Now`: forces an immediate redraw of both sides on the next candle, then should be switched off again.
- `Hold Timeout Active`: while on, suppresses only the automatic timeout for both sides. Releasing it resets both sides' clocks to a fresh timeout window.

Same meaning as the Pine version. See [`../pinescripts/README.md`](../pinescripts/README.md) for the full explanation and its caveat about these manual controls only behaving correctly when flipped while stepping forward through candles rather than scrubbing backward in history. The same limitation is expected to apply here once the manual controls stage is in.
