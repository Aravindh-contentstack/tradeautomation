# Daily Swing Structure (FXR Script)

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
