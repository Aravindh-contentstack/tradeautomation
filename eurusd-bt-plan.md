# EUR/USD Backtest Engine: Weighted Confluence Strategy

## Context

Step 1 (historical data acquisition) is complete: `data/raw/EUR_USD_D.parquet`,
`EUR_USD_H4.parquet`, and `EUR_USD_H1.parquet` hold ~23 years of clean OHLC
(2003-2026), and the `swing_structure/` package already computes every
structure/zone signal the strategy needs (verified against the real
package source, not assumed):

- `swing_structure/daily_structure.py`, `h4_structure.py`, `h1_structure.py`:
  `compute_{daily,h4,h1}_structures(df)` each add 18 columns (6 per tier x
  swing/internal/fractal), including `{tf}_{tier}_structure` (bullish/
  bearish) and `{tf}_{tier}_swing_high` / `_swing_low` (the raw pivot
  levels, already exactly what's needed for the stop-loss level).
- `swing_structure/premium_discount.py`: `compute_{daily,h4,h1}_premium_discount(df)`
  each add `{tf}_{tier}_zone` ("premium"/"discount") for the swing and
  internal tiers, already built on `current_range.py`'s "effective high/low"
  logic (the same mechanism `daily_internal_premium_discount.py`'s FXR port
  documents), so no new zone-calculation code is needed.
- No "OTE" concept or session/killzone logic exists anywhere yet (confirmed
  by repo-wide grep). Per user clarification, "H1 internal OTE" is not a
  separate Fibonacci calculation, it reuses the same `h1_internal_zone`
  premium/discount output already computed above.

Per user clarification (correcting an earlier misreading of the worked
example on my part), the premium/discount rule is: **"premium" is the
OTE/supportive zone for a SELL (bearish) trade, and "discount" is the
OTE/supportive zone for a BUY (bullish) trade, uniformly across all 6 zone
factors, with no exception.** This is one formula, applied identically to
`daily_swing_pd`, `daily_internal_pd`, `h4_swing_pd`, `h4_internal_pd`,
`h1_swing_pd`, and `h1_internal_pd` alike:

- **9 structure factors** (`daily_swing`, `daily_internal`, `daily_fractal`,
  `h4_swing`, `h4_internal`, `h4_fractal`, `h1_swing`, `h1_internal`*,
  `h1_fractal`*): yes if the column's bullish/bearish value equals the
  trade's direction.
- **6 zone factors** (`daily_swing_pd`, `daily_internal_pd`, `h4_swing_pd`,
  `h4_internal_pd`, `h1_swing_pd`, `h1_internal_pd`*): yes if
  `(trade_direction == "bearish" and zone == "premium")` or
  `(trade_direction == "bullish" and zone == "discount")`. Same formula for
  all 6, `h1_internal_pd` is not a special case in the formula, it is only
  special in also being one of the 3 mandatory gates.

(* marks the 3 mandatory gates. Per user confirmation these still count in
the weighted sum of 15, they just must also independently be "yes" for the
trade to be considered at all.)

Note: the strategy's original worked example marked `H1_internal_pd =
discount -> yes` for a bearish/sell trade, which contradicts this rule
(the rule says a sell trade needs "premium," not "discount," to satisfy
this factor). Per explicit user confirmation, the corrected general rule
above is authoritative and that detail in the original example was a
mistake, not a special case for this factor.

Mandatory gates, evaluated at the H1 candle where a genuine H1 fractal
break just occurred: `h1_fractal_high_event == "break of swing high"`
(candidate buy) or `h1_fractal_low_event == "break of swing low"`
(candidate sell). This is deliberately NOT `h1_fractal_structure_event`,
which (per `swing_structure/market_structure.py`'s own logic, verified
while implementing) only fires when the bullish/bearish label actually
flips, silently staying `None` on a break that continues an
already-established direction. The raw `high_event`/`low_event` columns
fire on every genuine break regardless, which is what "the candle that
broke the H1 fractal point" means:
1. `h1_internal_structure == h1_fractal_structure` (defines `trade_direction`)
2. The `h1_internal_pd` factor (per the zone-factor formula above) evaluates
   to "yes," i.e. `h1_internal_zone == "premium"` for a bearish/sell trade,
   or `h1_internal_zone == "discount"` for a bullish/buy trade.
3. The candle's time falls in the London (07:00-10:00) or NY (12:00-15:00)
   killzone, **British civil time** (`Europe/London`: GMT/UTC+0 in winter,
   BST/UTC+1 in summer), confirmed by the user directly ("some time of the
   year it is UTC+1 and during winter it becomes UTC").

## Approach

New package `backtest/`, instrument-parameterized throughout (this task
runs it for EUR_USD only, but nothing should be EUR_USD-specific in the
code so the other 10 instruments are a one-line change later).

### 1. `backtest/pipeline.py`: load and compute all signals for one instrument
- Reads the 3 Parquet files, runs `compute_{daily,h4,h1}_structures` then
  `compute_{daily,h4,h1}_premium_discount` on each (reusing the existing
  functions verified above, no reimplementation).
- Merges Daily and H4 state onto the H1 timeline via `pd.merge_asof`,
  `direction="backward"`, matched against each higher-timeframe candle's
  **close time** (`date + timeframe_duration`, not its open/start time), so
  only fully-closed Daily/H4 candles are ever visible to an H1 decision,
  avoiding lookahead, per user confirmation.
- Output: one H1-indexed DataFrame carrying all 15 factor columns plus
  `h1_fractal_swing_high`/`_swing_low` (the stop-loss levels) and
  `h1_fractal_high_event`/`h1_fractal_low_event` (the entry trigger
  markers, see the corrected note in Context above).

### 2. `backtest/factors.py`: the 15-factor yes/no and probability
- `FACTOR_SPECS`: the 15 (column, kind) pairs from the Context section.
- `evaluate_factors(row, trade_direction) -> dict[str, bool]` implementing
  the uniform rule above: structure-match for the 9 structure factors, and
  the single premium-for-sell/discount-for-buy formula for all 6 zone
  factors alike (no per-factor special-casing).
- `compute_probability(factor_results, weights) -> float`: `(sum(weights[f]
  for f yes) - 0.5 * sum(weights[f] for f no)) / sum(weights.values()) * 100`.
  Normalizes by the CURRENT sum of all 15 weights, not the fixed count 15,
  a correction made after the first full run: the +/-5% multiplicative
  update always drifts weights below 1.0 whenever the strategy's win rate
  sits under 50% (`1.05*0.95=0.9975<1`), which shrunk the achievable
  probability ceiling over time and permanently locked the system out of
  ever taking a trade again once that ceiling fell under the active
  threshold (confirmed: exactly this happened from 2009 onward in the
  first run). Normalizing by the current weight sum keeps probability a
  measure of relative confidence regardless of how far the absolute
  weight scale has drifted, so a lockup can no longer happen. Per user
  decision.

### 3. `backtest/killzone.py`: session gate
- Converts an H1 candle's UTC timestamp to British civil time
  (`zoneinfo.ZoneInfo("Europe/London")`, DST-aware: GMT in winter, BST in
  summer) and returns "london," "ny," or `None` for 07:00-10:00 or
  12:00-15:00 civil time.

### 4. `backtest/simulate.py`: per-trade outcome simulation
- `find_signals(df)`: rows where `h1_fractal_high_event == "break of swing high"`
  or `h1_fractal_low_event == "break of swing low"` (this determines
  `trade_direction`: bullish for a high break, bearish for a low break),
  the 2 mandatory H1 structure/zone gates pass, and the killzone gate
  returns a session.
- `simulate_trade(df, entry_idx, direction, entry_price, sl, tp)`: walks
  forward candle by candle from the next H1 candle, checking high/low
  against SL/TP.
  - Same-candle SL and TP ambiguity: assume SL hit first (per user
    confirmation), since only OHLC (not tick) data is available.
  - Tracks running max favorable excursion in R-multiples throughout (this
    doubles as the "Maximum R reached" journal field and as the basis for
    later years' "ideal TP" analysis, so no separate open-ended pass is
    needed since this is tracked on every trade regardless of the fixed
    2.5R exit).
  - SLB: once the nominal SL is touched, continues a hypothetical walk
    (ignoring that exit) only through the remainder of that same trading
    day (capped at the last H1 candle before midnight UTC on the day the
    SL was hit, not a multi-day or multi-week window, per user
    correction), watching for a reversal that reaches the original TP. If
    found, SLB equals the worst price excursion beyond the SL level
    reached before that reversal. The user's real-world expectation is
    SLB values in the 3-7 pip range, useful as a sanity check on the
    computed numbers once real trades run through this.
  - Returns: result ("win"/"loss"), exit time, max R reached, SLB (or null
    if not applicable or not reached before end of day).

### 5. `backtest/weights.py`: adaptive weight table
- Starts all 15 weights at 1.0.
- `update_weights(weights, factor_results, result)`: for each factor,
  multiply by 1.05 if (yes and win) or (no and loss), by 0.95 if (yes and
  loss) or (no and win), the exact 4-branch rule specified.
- Persists one CSV per year, e.g. `data/weights/EUR_USD_weights_2003.csv`,
  never overwritten (append-only across years, matching the earlier
  Step-1-era design decision for weight tables).

### 6. `backtest/journal.py`: per-trade CSV record
Columns: date, day_of_week, session, order_placed_time, order_completed_time,
direction, entry_price, sl_price, tp_price, sl_size, probability, result,
max_r_reached, slb. One file per year, e.g.
`data/journal/EUR_USD_trades_2003.csv`.

### 7. `backtest/analysis.py`: end-of-year settings recommendation
After each year, grid-searches probability threshold and TP multiple
against that year's journal to find the combination maximizing ROI, using
max drawdown as a tie-breaker/filter (preferring the lowest-drawdown
combination among those near the top ROI, rather than picking an
otherwise-similar combination with a much deeper drawdown), and reports
strike rate alongside both as secondary metrics, since the user wants ROI,
strike rate, and drawdown all considered. Writes
`data/settings/EUR_USD_settings_{year}.json` (threshold, TP multiple, max
SL size, and the resulting max drawdown for that choice) that the next
year's run reads at startup. First year uses fixed defaults (threshold 50,
TP 2.5R) with no prior settings file to read.

### 8. `scripts/backtest_eurusd.py`: orchestration
Iterates **calendar years** (Jan-Dec, per user confirmation) present in
`EUR_USD_H1.parquet`: 2003 (a partial year, since data starts mid-2003)
through 2026, sequentially: load prior year's weights and settings (or
defaults for year 1), find signals, simulate each, journal, update
weights, analyze, write next year's settings. Produces the full run of
per-year weights, journal, and settings files, plus a final printed
summary (years covered, total trades, overall win rate, final settled
weights).

## Verification
1. Run `backtest/pipeline.py` for EUR_USD alone and confirm the merged H1
   DataFrame has all 15 factor columns populated (non-null once Daily/H4/H1
   histories have warmed up) and no lookahead (spot check: a Daily column's
   value on an H1 row should match the prior day's already-closed value,
   not the in-progress day).
2. Manually replay the corrected factor rules (not the original example's
   literal H1_internal_pd value, which was flagged as a mistake) against
   one real historical H1 signal candle: pull its actual factor values,
   confirm `evaluate_factors` yes/no calls and `compute_probability` match
   hand calculation.
3. Run `scripts/backtest_eurusd.py` for just year 2003 first, inspect
   `data/journal/EUR_USD_trades_2003.csv`, `data/weights/EUR_USD_weights_2003.csv`,
   and `data/settings/EUR_USD_settings_2003.json` for sane values (weights
   near 1.0 with small drift, trades only inside killzone hours, SL sizes
   consistent with EUR_USD's typical H1 fractal range, and a plausible
   drawdown figure rather than 0 or a value larger than the account could
   plausibly survive).
4. Run the full 2003-2026 loop and confirm settings, journal, and weights
   files exist for every year with no gaps, and that weights stay within a
   sane range (no runaway drift toward 0 or extreme values, which would
   flag a bug in the update rule rather than genuine signal).
