# GBP/JPY Backtest and Tuning Study (2015 to 2025)

## Context

The M15 entry algorithm is feature-complete enough to measure. The goal of this
work is **not** to change the strategy. It is to run the strategy unfiltered over
11 years of GBP/JPY, collect every trade, and then work out four settings from
the data:

1. Take-profit level (fixed, or variable by some rule)
2. Stop-loss buffer
3. Threshold on HTF strength
4. Threshold on Trade strength

Everything here is a **research harness sitting beside the existing engine**. The
live walk-forward path (`scripts/backtest_multi.py`, `data/journal/`,
`data/weights/`, `data/settings/`) is not touched and its files are not
overwritten.

---

## What already exists (most of this is wiring, not new logic)

Verified by reading the code. This is why the plan is small.

| Requirement | Status |
|---|---|
| Open-ended walk with no TP | **Exists.** `simulate.simulate_trade` is deliberately TP-free, and `simulate.apply_tp` projects any TP onto it afterwards with no re-simulation. |
| Max R (your "Max RR") | **Exists** as `max_r_reached`. |
| 19:00 London BE and cut rule | **Exists**, `simulate.py:494-520`. |
| Probability-recheck BE rule | **Exists**, `simulate.py:522-563`. |
| Friday EOD close | **Exists**, `simulate.py:483-492` via `killzone.friday_cutoff_for`. |
| Date, day, session in London time | **Exists** in `journal.py`, already London civil time and DST-aware. |
| Start and end time, duration | **Exists** as `order_completed_time`, exit time, `trade_duration`. |
| SL size including the 2 pip buffer | **Exists** as `sl_size` (equal to `r_distance`, buffer already inside it). |
| HTF strength and Trade strength | **Exists** as `htf_probability` and `total_probability`. |
| Weight learning at 2 percent | **Exists**, `weights.update_weights`. |
| Threshold and TP grid search | **Exists in part**, `analysis.recommend_global_settings`. |

**Genuinely new:** `max_r_to_be`, `final_probability`, a runtime-tunable SL
buffer, the variable-TP families, and the three-way reporting (strike rate, ROI,
expectancy).

---

## Measured facts that shape the plan

Run on this machine before writing this plan.

- **Pipeline build for GBP/JPY takes about 100 seconds.** It depends only on price
  data, not on any setting, so build it **once** and reuse it for every tuning
  pass.
- **A full 2015 to 2025 pass takes about 4 seconds.** Tuning is therefore
  effectively free. A 7-value buffer sweep is under a minute.
- **505 candidate trades total**, 460 after the one-trade-per-order-block rule.
  - Train (2015 to 2022): **356** candidates
  - Holdout (2023 to 2025): 149 candidates
- Outcome mix on the TP-free walk: 306 stopped out, 95 stopped at breakeven,
  89 closed Friday, 15 cut at 19:00.
- Entry model mix: LC-2A 227, LC-1 150, CE 103, LC-2B 25.

### The one thing to be aware of before we start

**356 training trades is a thin sample for fitting four dials.** The codebase
already warns about this in its own words (`backtest/analysis.py` says "there is
not enough data for two dials", and we are about to fit four).

Concretely: if we split 356 trades by TP, buffer, and two thresholds, some cells
will contain a handful of trades, and the "best" setting will often just be the
one that happened to dodge two losers in 2017. It is the statistical equivalent
of picking a restaurant from three reviews.

Two mitigations are built into the plan:

- **2023 to 2025 is held back entirely** and scored once at the end. If the
  holdout result is far worse than the training result, we overfitted and we
  know it.
- **A minimum trade count per grid cell** of 8, matching the existing engine.
  Any setting fitted on fewer trades is reported but flagged, not recommended.

If the holdout comes out badly, the honest next step is to pool several JPY pairs
rather than to keep tuning GBP/JPY harder. Worth deciding once we see numbers.

---

## Decisions already agreed

- **Tuning mode:** single global pass over 2015 to 2022, then a one-shot 2023 to
  2025 holdout check.
- **Strike rate:** win means the full TP was hit, loss means the stop was hit.
  Breakeven and time-cut exits are excluded from both sides of the fraction.
- **ROI and expectancy:** include *all* trades at their real R. A breakeven counts
  as 0, and a Friday close at +0.7R counts as +0.7R.
- **Weight step:** keep the existing 2 percent.
- **SL buffer** moves the stop *and* the pending-order price together.
- **Report three winners:** best strike rate, best ROI, best expectancy.
- **Variable TP** may raise *or* lower the R target.

## Decisions I am making, flagged for your override

1. **The probability-recheck BE rule is switched off in Stage A and Stage B.**
   That rule moves the stop to breakeven when live probability drops below the
   threshold, and in Stage A there is no threshold to drop below. Leaving it on
   with the entry probability as the bar would send nearly every trade to
   breakeven on the first bar. It is switched back on in Stage D once real
   thresholds exist, and Stage B-prime re-confirms the TP choice with it active.
2. **`min_live_probability` gets recorded anyway** in Stage A, so we can still see
   what that rule *would* have done.
3. **One-trade-per-order-block stays on.** It is a real trading rule, not a
   filter. Suppressed repeats are journalled separately so they stay visible.
4. **Best-expectancy settings feed Stage C** (the weight learning), with the other
   two carried as sensitivity checks.
5. **`MAX_R_CEILING = 10.0`** (`simulate.py:96`) becomes tunable, otherwise
   liquidity-based targets beyond 10R get silently clipped.

---

## Stage A: collect every trade, unfiltered

Run GBP/JPY from 2015 to 2025 with weights all at 1.0, no thresholds, no TP, the
engine's own stop, and the Friday and 19:00 rules active.

Produces one row per candidate with your requested columns.

| Column | Source |
|---|---|
| Date, as "Jan 1 2020" | format existing `date` |
| Day, as "Monday" | existing `day_of_week` |
| Start time HH:MM London | format `order_completed_time` |
| End time HH:MM London | format exit time |
| Duration HH:MM | format existing `trade_duration` |
| Session, London or New York | map existing `session` |
| Stop loss size | existing `sl_size`, 2 pip buffer already included |
| Max RR | existing `max_r_reached` |
| Max RR to BE | **new** |
| HTF Strength as "33.33%" | format `htf_probability` |
| Trade Strength as "33.33%" | format `total_probability` |
| Final Trade Probability | **new** |

**Two new fields, both cheap:**

- `max_r_to_be` captures `max_r` at the two places the stop moves to breakeven
  (`simulate.py:500` and `simulate.py:559`). Both sit before the excursion
  tracking block, so it naturally means "highest R reached *before* going
  breakeven". Blank if the trade never went breakeven.
- `final_probability` scores the live factors **once at the terminal bar**, after
  the loop ends, rather than every bar. This is the important detail. The existing
  per-bar recheck stops once a trade goes breakeven, and re-enabling it for all
  bars would multiply the work per trade. Scoring once at the exit bar gives the
  same number for a fraction of the cost. It requires splitting the current
  `live_recheck_enabled` flag into "can we score" and "should we act on it".

---

## Stage B: tune TP and SL buffer together

Your concern was correct. These two interact, because changing the buffer changes
the stop *and* the entry price, which changes the R denominator and which trades
qualify at all. So they cannot be tuned one after the other.

**The search is a nested loop, and it is cheap because of an asymmetry.**

- **Outer loop, SL buffer:** `[0.5, 1, 2, 3, 4, 6, 8]` pips. Each value needs a
  full re-run of about 4 seconds, because it changes trade geometry.
- **Inner loop, TP:** free. The TP-free walk already traces the one path that all
  TP variants share, so every TP is scored by projection with no re-simulation.

Total is about 30 seconds of compute after the one-time 100 second build.

**Making the buffer tunable at runtime.** `SL_BUFFER_PIPS = 2.0`
(`backtest/entry_ob.py:51`) is a module constant imported by `entry_models.py` and
used at eight sites (lines 655, 691, 737, 778, 979, 999, 1033, 1048). A new
`backtest/entry_params.py` holds it in a context variable with an `override()`
helper, those eight lines read from it, and the default is unchanged, so `live/`
and every existing test behave exactly as they do today.

Note: the two LC models apply the buffer to both the order and the stop
(`_order_prices`, line 1104), so their R distance grows by **2x** the buffer,
while CE grows by **1x**. Expect the model mix to shift as the buffer widens.
Worth watching, not a bug.

**TP families searched**, all snapped to a 0.25R grid from 0.25 to 8.0:

1. **Fixed:** one R multiple for every trade.
2. **By trade strength:** split candidates into probability bands, each band gets
   its own R target, which may be higher *or* lower.
3. **By stop size:** split by `sl_size` in pips, each band gets its own R target.
4. **Next liquidity level:** target the nearest live unswept liquidity level ahead
   of price. Already computed by the engine (`smc/liquidity/liq_state.py`, fields
   `target_above` and `target_below`), converted to R at the entry bar. R differs
   per trade by construction.

**Output:** for each buffer and TP family, the strike rate, ROI, expectancy, trade
count and max drawdown, plus the three winners.

**Expect the max-strike-rate answer to look silly.** With your definition, a 0.5R
target gets tapped before the stop on most trades, giving roughly 90 percent
strike rate and poor returns. That is arithmetic, not a bug. I will report it
as-is (you asked for max strike rate) with the ROI and expectancy winners next to
it so the trade-off is visible in one table.

**Also expect ROI and expectancy to name the same TP** within a given buffer. They
only diverge once the trade count changes, which happens across buffers and in
Stage D.

---

## Stage C: learn the factor weights

One pass of the existing `weights.update_weights` over every 2015 to 2022
candidate in **global date order**, using the Stage B outcomes. The rule is
unchanged: yes and win multiplies by 1.02, yes and loss by 0.98, no and win by
0.98, no and loss by 1.02, and breakeven leaves the weight alone.

It runs over all candidates, not just taken ones. Same reasoning the engine
already documents: the factors were evaluated on the skipped bars too, and the
market answered them either way.

Output is one weight table, frozen at end of 2022.

---

## Stage D: find the two thresholds

Re-score every trade's HTF strength and Trade strength using the Stage C weights,
then search both thresholds.

**These two cost different amounts, which is worth knowing.**

- **The HTF threshold is free.** It only decides whether the M15 scan runs
  (`simulate.py:200`), so running Stage A with it off means it can be applied
  afterwards as a plain filter on the recorded `htf_probability`. No re-runs.
- **The Trade-strength threshold needs re-walks**, because it feeds the
  probability-recheck BE rule and so changes outcomes. But it does not change
  trade geometry, so these passes reuse the Stage B signal list and skip signal
  detection entirely. About 10 walk-only passes, seconds each.

Both grids are taken as quantiles of the observed distribution, not fixed absolute
numbers. The engine's probability scale is normalised per trade, so a fixed 40 to
80 grid can land entirely above the population.

**Stage B-prime:** with real thresholds now active, re-confirm the TP choice, which
is free within that pass. If it moves, that is a genuine finding, not a wobble.

---

## Stage E: the holdout, and the final answer

Score 2023 to 2025 **once**, with weights frozen at end of 2022 and all four
settings frozen from Stage D. No tuning against it.

**Final deliverables:**

1. **The refilled trade table** you specified: date, day, start, end, duration,
   session, HTF strength, Trade strength, Final probability, and trade outcome in
   R. The TP value if the target was hit, minus 1R if stopped, or the real R if it
   closed early.
2. **Three settings cards**, one each for best strike rate, best ROI, and best
   expectancy. Each gives the HTF strength threshold, the Trade strength
   threshold, the take-profit rule, and the SL buffer.
3. **Train versus holdout comparison** for each card. This is the number that
   tells us whether any of this is real.

---

## Files

**New:**

- `backtest/entry_params.py`, runtime-overridable buffer and min-R.
- `backtest/research/params.py`, grids and run specs.
- `backtest/research/pass_runner.py`, one full pass at a given buffer and settings.
- `backtest/research/tp_models.py`, the four TP families.
- `backtest/research/metrics.py`, strike rate, ROI, expectancy, drawdown.
- `backtest/research/report.py`, the display-formatted table.
- `scripts/research_gbpjpy.py`, orchestrates Stages A through E in one process,
  building the pipeline once.

**Minimally edited:**

- `backtest/entry_models.py`, eight lines read the buffer from `entry_params`.
- `backtest/simulate.py`, add `max_r_to_be`, `final_probability`, a tunable
  `max_r_ceiling`, and split the recheck flag in two.
- `backtest/runner.py`, extract the year-context builder (lines 130 to 165) so the
  research runner can reuse it. `run_year` itself is not reused, because it
  hardcodes learning and a single TP.

**Untouched:** `backtest/journal.py`, all of `live/`, `data/journal/`,
`data/weights/`, `data/settings/`.

**Outputs:** `data/research/GBP_JPY/<run_id>/{stageA,stageB,stageC,stageD,holdout}/`

---

## Verification

1. **Unit tests** for the new pieces: `max_r_to_be` on a hand-built trade that
   goes to +2R then breakeven, `final_probability` matching a manual score at the
   exit bar, and a buffer override changing `sl_size` by the expected amount on
   each of the four entry models (2x for LC, 1x for CE).
2. **No-regression check:** run the existing suite in `tests/`. With the buffer
   defaulting to 2.0, every current test must pass unchanged.
3. **Byte-identical baseline:** re-run GBP/JPY 2020 to 2025 through the *existing*
   `scripts/backtest_multi.py` and diff against the committed journals. Any
   difference means the `simulate.py` edits leaked into the live path.
4. **Stage A sanity:** 505 candidates over 2015 to 2025 (the number measured
   above), all entry times inside London or NY killzone hours, no trade running
   past its Friday deadline, and `max_r_to_be` never exceeding `max_r_reached`.
5. **Stage B sanity:** at buffer 2.0 and fixed TP 2.5R, the results must match
   Stage A projected through `apply_tp`. This proves the buffer plumbing is inert
   at its default.
6. **Holdout discipline:** assert in code that no 2023 to 2025 row is ever read
   during Stages B, C, and D. This is the one bug that would invalidate the whole
   study, and it is easy to introduce by accident.
