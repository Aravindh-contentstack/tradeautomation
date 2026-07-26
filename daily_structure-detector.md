# Daily Swing Structure Detector

## Context

This is the first piece of Stage 2 ("Analyze") of the trading automation pipeline. The user (a non-technical, non-developer forex/gold trader) wants a Python script that detects "swing structure" on the Daily timeframe, matching their own manual chart-reading method. The intended workflow: I write the reference logic in Python (explained line by line, since the user wants to fully own and understand the code), the user manually re-implements it in Pine Script, and tests it visually inside FX Replay (their backtesting tool, which recently added Pine Script based backtesting) against real historical charts. Only Daily swing structure is in scope for this piece. Internal structure and fractal structure are separate, later pieces once their own rules are extracted the same way.

The exact rules below were extracted through many rounds of clarifying questions with the user, specifically to avoid guessing at ambiguous trading logic. Nothing in this spec is assumed. Every branch was explicitly confirmed.

## Revision (2026-07-25): independent per-side age clocks

The original rules (below) had swing high and swing low sharing ONE age clock. This was revised after testing the Pine port on a real XAU/USD Daily chart: during a sustained downtrend, the swing low kept breaking every so often, and each of those breaks reset the SHARED clock back to zero. Since the clock never got a chance to reach 65 uninterrupted, the swing high, untouched since months earlier, could never time out and stay stayed frozen at a stale level, well past what should have been a current, "fresh" swing high for that side. One side being active was silently preventing the other side from ever refreshing, which is not correct for ongoing analysis.

The fix: swing high and swing low now each get their OWN independent age clock and their OWN independent automatic timeout. A quiet side can now refresh on its own 65-candle schedule regardless of how often the other side is breaking. All rules below have been updated to reflect this. `manual_restart` and `hold_timeout` are unaffected in scope: both remain global controls that still act on both sides at once (see their entries below).

One consequence of independent clocks: the two sides can now report different events on the very same candle (e.g. the high breaks while the low independently times out). There are now two event columns, `high_event` and `low_event`, instead of one shared `event` column, so this never has to be squeezed into a single ambiguous label.

## Confirmed Rules (Daily Swing Structure)

**What counts as a swing high/low, initially:** the single candle with the highest `high` (for swing high) or lowest `low` (for swing low), using wick price, not candle body, within a trailing window of candles.

**Window size:** 45 candles (the low end of the user's 45-60 range, can widen later after visual testing).

**Persistence ("stays until broken"):** once a swing high/low is set, it does NOT get recalculated every day from a rolling window. It stays fixed until a real break happens. (Pure "always recompute from trailing window" was rejected. It produces false signals once the true peak/trough ages out of the window while price is still ranging between the original two points.)

**What counts as a "break":** the candle's CLOSE price finishes beyond the level (above swing high, or below swing low). A wick merely touching beyond the level, without closing beyond it, does NOT count as a break.

**When a break happens, only that side redraws:** if the swing high breaks, only the swing high gets recalculated (from the current 45-candle window). The swing low is left untouched. Same in reverse.

**Independent "age" clocks:** swing high and swing low each have their OWN clock (candles since the last redraw of that side specifically), not a shared one. A redraw event on one side (a break, an automatic timeout, or a manual restart) resets only that side's own clock. Manual restart is the one exception: it redraws both sides and resets both clocks at once, since it's a deliberate, explicit full restart (see below).

**Automatic timeout (per side):** if 65 candles pass with a given side's own clock never resetting (that side hasn't broken, hasn't been manually restarted, and hold_timeout hasn't just released), force a redraw of THAT side alone, using the current 45-candle window. The other side is untouched unless it independently also happens to hit its own 65-candle mark.

**Manual "hold" control (`hold_timeout`, true/false):** while true, suppresses ONLY the automatic timeout, for BOTH sides. Real breaks still apply normally regardless of this flag. When it's switched back to false ("released"), BOTH sides' clocks reset to zero at that moment (a fresh 65-candle grace period for each), rather than either side immediately firing an overdue timeout.

**Manual "restart now" control (`manual_restart`, true/false):** a one-time trigger, detected on the exact candle it flips from false to true (not "true forever" acting every candle). When triggered, forces an immediate redraw of BOTH sides from the current window, and resets BOTH sides' clocks. This remains a global, both-sides action. It was not split per side.

**Conflict rule:** if `manual_restart` triggers on the same candle that `hold_timeout` is true, bypass BOTH manual controls entirely for that candle. Behave exactly as if neither existed (only a real break or that side's own 65-candle timeout can act that day).

**Same-day precedence, checked independently for EACH side (first match wins, rest skipped for that side that candle):**
1. Conflict check: `manual_restart` triggers while `hold_timeout` is true, so bypass both manual controls and fall through to step 3.
2. Manual restart: `manual_restart` flips false to true, so redraw both sides, reset both clocks, event equals `manual restart` for both sides. (This takes precedence over a same-day break, on whichever side would have broken.)
3. Real break: this side's close finishes strictly beyond its own level (equal-to does NOT count as a break), so redraw only this side, reset only this side's clock, event equals `break of swing high` or `break of swing low` (only ever in that side's own event column).
4. Hold release: only reached if none of the above fired for this side. `hold_timeout` just switched from true to false, so reset this side's clock to zero for a fresh 65-candle grace period, event equals `hold released`. No redraw, this only resets the clock.
5. Automatic timeout: only reached if none of the above fired for this side. This side's own clock has hit 65 and `hold_timeout` isn't suppressing it, so redraw this side alone, reset this side's clock, event equals `timeout`.
6. Ordinary day for this side: nothing happened, this side's clock just ticks up by one.

Because each side runs this precedence independently, the high and low can land on different steps on the very same candle (e.g. high breaks while low ticks up ordinarily, or high times out while low breaks). Because break is still checked before timeout on each side, a break landing on the same candle that side's clock would have hit 65 always wins outright for that side. Because hold release is also checked before timeout, releasing `hold_timeout` never triggers an immediate overdue timeout on either side, even if a clock had already climbed past 65 while held, it always starts a fresh count from zero instead.

**Cold start:** before any swing point has ever been set (start of the dataset), wait until at least 45 candles of history exist, then seed both swing high and swing low from that first 45-candle window, together, both clocks starting at zero.

**Scope:** track only the current/latest swing high plus swing low pair (not a running history of every past swing point), Daily timeframe only, for this piece. Same logic gets reused later for 4H and 1H once this is validated.

## Existing Repo Context (reviewed, no conflicts)

- `factors/xau_probability_factors.csv` and `factors/eu_probability_factors.csv`: confirm "Swing" (along with "Internal", "Fractal") is an always-checked confluence factor at Daily/4H/H1 feeding Stage 3's gate tree (Mitigation OB, Swept Liquidity, OB Target, Liquidity Target, Entry models). Just confirms the swing detector is foundational. No impact on this piece's design.
- `temp-reference/structure_1` and `structure_2`: two well-known open-source Pine Script market-structure indicators (EmreKb's MSB-OB, Leviathan's Market Structure), saved by the user as reference. Both use fixed pivot-based swing detection (`ta.pivothigh`/`ta.pivotlow`) with a Close-vs-Wick break toggle. Useful confirmation that "close-based break confirmation" is standard practice, but the user's actual design (rolling-window seed plus persist-until-broken plus timeout plus manual override) is intentionally a custom hybrid, not a copy of either reference.

## Implementation Plan

**New files:**
- `swing_structure/__init__.py`: empty package marker
- `swing_structure/detector.py`: the core function, `compute_daily_swing_structure(df, lookback=45, timeout_candles=65, manual_restart=None, hold_timeout=None)`, taking a DataFrame of Daily OHLC candles (`date, open, high, low, close`, ascending order) and two optional boolean Series (defaulting to all-False if not supplied) for the manual controls. Returns a DataFrame with the original data plus `swing_high`, `swing_low`, `high_clock`, `low_clock` (candles since last redraw, per side), and `high_event`, `low_event` (a plain-English reason per row, per side, one of `initial seed`, `break of swing high` (high_event only), `break of swing low` (low_event only), `timeout`, `manual restart`, `hold released`, `warming up`, or `None` for an ordinary day on that side), so every redraw is traceable and nothing is a silent guess. `hold released` marks the moment `hold_timeout` switches back to false and that side's clock resets to zero, per the same-day precedence rule below, so that reset is never a silent, unlabeled change.
- `scripts/demo_swing_structure.py`: builds a small hand-crafted synthetic OHLC dataset (roughly 150 to 200 daily candles) deliberately engineered to exercise every rule at least once (cold start, a break of the high, a break of the low, a long unbroken stretch that hits the 65-candle timeout on each side independently, a `manual_restart` flip, and a `hold_timeout` flip-then-release), runs the detector, and prints a readable table so we can eyeball correctness together this session before any Pine porting starts. No charting library, no persistent visualization tooling, just a printed table, consistent with the decision not to build parallel infrastructure to FX Replay.
- `requirements.txt`: just `pandas` for this piece.

Real OANDA data integration is intentionally NOT part of this piece. That is a separate, already-planned future step. Using hand-built synthetic data here lets us deliberately hit every rule (including rare ones like a 65-candle timeout) in a single short test, which real historical data might not conveniently contain.

The core function will be a straightforward row-by-row loop (not a vectorized pandas operation). This is a deliberate choice, not a simplification shortcut. Since Pine Script itself processes one candle ("bar") at a time, writing the Python version the same way makes the eventual manual translation to Pine much more direct and line-for-line comparable.

**Code style:** per the user's explicit preference, `detector.py` will include plain-English comments explaining what each block does (not just why), and after writing it I'll walk through the whole function line by line in the chat so the user fully owns the logic before touching Pine Script at all.

## Verification

1. Run `python scripts/demo_swing_structure.py` and review the printed table together. Confirm each side's `event` fires on the expected day given the synthetic data's construction (cold start day, both break days, each side's independent timeout day, the manual restart day, the hold/release days).
2. Walk through `detector.py` line by line with the user in chat.
3. User then hand-ports the confirmed logic into Pine Script and visually tests it in FX Replay against real charts. This is the real acceptance test, and the start of the feedback loop the user described. Any mismatch found there feeds back into revising `detector.py` first (keeping Python as the source of truth), then re-porting to Pine.
