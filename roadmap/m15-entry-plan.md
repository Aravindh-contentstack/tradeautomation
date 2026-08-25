# M15 Entry Models: LC-1 / LC-2A / LC-2B / CE

## Context

**The problem.** Today a trade opens at the **close of the H1 candle that mitigated the order block**
(`backtest/entry_ob.py` line 134: `entry_price = float(ctx.close[entry_index])`), with the stop 2 pips
beyond the zone's far edge. `roadmap/supply-and-demand.md:380` says so in writing: *"A placeholder until
the M15 entry models land."* It is a placeholder because it throws away everything that actually decides
whether the trade works. It enters blind, at whatever price the hour happened to close at, with a stop as
wide as the whole zone.

**What is being added.** The M15 layer that the manual process has always had:

1. HTF analysis (Daily, H4, H1) produces a **trade_probability** when price mitigates an H1 OB. *(exists)*
2. If `trade_probability >= htf_threshold`, zoom into M15 and look for an entry model. *(new)*
3. One of four models fires (**LC-1**, **LC-2A**, **LC-2B**, **CE**), contributing its own factors. *(new)*
4. If `total_probability (HTF + Entry) >= total_threshold`, place the order. *(new)*
5. After entry, keep rescoring every bar. When the total drops below threshold, move to breakeven.
   *(the mechanism exists in `simulate.py` step 3, it just needs the entry factors folded in)*

**Intended outcome.** M15-precision entries and stops (typically 4 to 12 pips instead of a full zone
width), 32 new probability factors that the existing walk-forward weight learner can tune, and a two-gate
threshold structure that mirrors how the decision is actually made.

**Reused, not rebuilt:** `data/raw/*_M15.parquet` (10 instruments, ~400k bars each),
`backtest/intrabar.py::M15Index` (H1 bar to its M15 sub-bars, zero-copy), and every detector in `smc/`.
All of them are already timeframe-agnostic: `compute_tier_structure`, `compute_liquidity_levels`,
`compute_low_resistance_liquidity`, `compute_fair_value_gaps`, `compute_fractal_pivots`,
`compute_atr_series`, `sweep_credit.compute_credit_windows`.

---

## How to execute this

Build in phase order. Each phase is independently verifiable, and nothing later is safe to start until the
phase under it is green.

| Phase | What | New files | Verify by |
|---|---|---|---|
| **0 DONE** | M15 substrate: structure tiers, minor liquidity, the bundle | `smc/market_structure/m15_structure.py`, `smc/liquidity/minor_liquidity.py`, `backtest/m15_pipeline.py` | `tests/test_m15_structure.py`, `tests/test_minor_liquidity.py`, `tests/test_m15_pipeline.py`. 45 tests, full suite 360 green. |
| **1 DONE** | The four entry models and the scan | `backtest/entry_models.py` | `tests/test_entry_models.py` (80 tests, every rule parametrised by direction), full suite 440 green, plus a real-data run over 2023 to 2024 on EUR_USD and XAU_USD |
| **2 DONE** | 31 entry factors, `ALL_FACTORS` at 182, the two thresholds, the search change | `backtest/entry_factors.py` | `tests/test_entry_factors.py` (57), `tests/test_two_thresholds.py` (15), full suite 516 green, no skips |
| **3 DONE** | Wiring into pipeline, context, simulate, journal, runner | `tests/test_find_signals.py` | 551 green, no skips, and a full EUR_USD 2020 to 2025 walk-forward |
| **4 DONE** | Live runner: M15 feed and pending orders | `live/pending_plan.py`, `tests/test_pending_plan.py` | 578 green. NOT run against a broker: MetaTrader5 is Windows-only, so nothing in `live/` executes here. |

**Read these three sections before writing any code**, because they cut across every phase and are where
the expensive mistakes live: *Direction mirroring*, *Exclude versus negate*, and *The eq rule at M15
resolution*.

---

## Confirmed decisions

These were settled in conversation over several rounds. They are the specification, so **do not re-derive
them**, and where a decision says "chosen, not derived", do not quietly replace it with something that
looks more principled.

Decision 8a exists because it was carved out of 8 after a conflict surfaced. The numbering is kept stable
so the cross-references in the body stay valid.

| # | Decision |
|---|---|
| 1 | **Two thresholds, both walk-forward searched.** `htf_threshold` gates the M15 search, `total_threshold` gates the order. |
| 2 | **H1 stays the base timeline.** The M15 layer is a bounded sub-scan, the `intrabar.M15Index` pattern. No rebase of `ob_state`, `liq_state`, `timeline`. |
| 3 | **OB tier comes from the existing `primary_tier` metadata.** Per-tier OBs get filed in `roadmap/enhancements.md` as a follow-up for better accuracy. |
| 4 | **The M15 trigger candle must close inside a killzone** (London 7 to 10, or NY 12 to 15, London civil time). The H1 mitigation itself is no longer killzone-gated. |
| 5 | **LC-1 liquidity is a "local rejection extreme, unswept."** For a **bearish** H1 OB it is an unswept **high**: a green M15 candle whose *immediately following* candle does not exceed its high. For a **bullish** H1 OB it is an unswept **low**: a red M15 candle whose following candle does not go below its low. Plus every n=1 fractal point on the same side, same unswept test. Full explanation and worked example in Phase 0. |
| 6 | **Entry and stop both come from the trigger candle.** Bearish: sell stop below its low, SL 2 pips above its high. |
| 7 | **No order rolling past N=2.** Candle A (sweep plus mitigation) hosts the order. If candle B makes a new high and does not kill the setup, the order moves to below B's low. Candle C means cancel. |
| 8 | **The M15 scan ports the H1 `eq` rule to M15 resolution, with the same off-by-one.** A wick reaching the zone midpoint (bearish: `high >= eq`, bullish: `low <= eq`) breaches it. The **breaching candle is still tradeable**, later candles are not. This replaces the earlier far-edge threshold, which was too loose, and it removes the hour-alignment problem because M15 now does its own accounting. |
| 8a | **The eq off-by-one gates setup FORMATION, not order MANAGEMENT.** No new trigger candle may form after the breach, but a setup whose trigger candle is already fixed keeps its full N=2 window and may re-host. Derived in Phase 1. |
| 9 | **One trade per OB: score all, take the first.** Later candidates on the same OB are still simulated and journalled but flagged `taken=False`. The "never pre-filter" invariant survives. |
| 10 | **Scoring timing.** HTF factors frozen at the H1 mitigation bar. Entry factors evaluated on the M15 trigger candle. `total_probability` computed there, once, with no re-check before fill. |
| 11 | **After entry, rescore every bar across all timeframes and BE when the total drops below `total_threshold`.** A consumed **liquidity** target (including M15 target liquidity) is **excluded** from the denominator. A reached **OB** target keeps the existing behaviour and has all its answers **negated**. See "Exclude versus negate" below. |
| 12 | **M15 tiers: `m15_fractal` n=2, `m15_internal` n=5, raw n=1 pivots** for LC-1 liquidity and LRLQ. |
| 13 | **"No imbalance while mitigation" measures the approach leg, found by the running-minimum rule.** Anchor the window on the **previous qualifying touch** of the OB (not any wick overlap, so an exact-edge kiss does not reset it), then take the deepest point in that window as the leg start. Replaces the fixed 10-candle lookback, which inverted the sign of the evidence. Full derivation in Phase 2. Pre-pivot imbalance does **not** get its own positive factor. |
| 14 | **Equals angle: second top within 0.10x M15 ATR(14) is YES.** Between 0.10x and 0.25x is NO. Beyond 0.25x is not an equals at all, so LC-2B does not fire. |
| 15 | **CE fib range runs from the broken `m15_internal` swing extreme to the running extreme** since the break. Bearish: broken swing high down to the running low, sell limit at 50%, SL 2 pips above the range high. Bullish: broken swing low up to the running high, buy limit at 50%, SL 2 pips below the range low. Both recomputed each candle. |
| 16 | **LC-1 freshness: the BSLQ candle must be at most 10 M15 candles old** relative to the mitigating candle. Stale liquidity falls through to CE. |
| 17 | **Polarity: a clean approach leg is YES.** No imbalance in the leg scores YES, imbalance present scores NO, matching the factor name `no_imbalance_while_mitigation`. Confirmed after a wording slip in the other direction. |
| 18 | **H1 remains the touch authority.** The 3-touch limit stays an H1 concept: three H1 candles tapping the zone progressively deeper. M15 does not run a parallel touch counter. M15 adds only the intrabar eq check from decision 8. |
| 19 | **"One trade per OB" counts TAKEN trades, not touches.** A qualifying touch that produces no entry model costs nothing, and the zone remains fully available for a later touch. |
| 20 | **LC-1 proximity bound: the level must sit inside the OB, or within 1.0x M15 ATR(14) of the near edge.** Bearish: inside the zone or at most 1.0x ATR below `ob_bottom`. Bullish: inside, or at most 1.0x ATR above `ob_top`. Restores the "just below or inside" and "within nearby distance" wording from the original description, which the earlier "at or below the OB top" rendering had dropped. Measured effect below. |
| 21 | **"No other LIDs" counts only levels inside the same proximity window.** Not every unswept level between price and the zone. Tying the two rules to one region is what keeps the factor informative rather than near-constant. |
| 22 | **"LID with FVG" counts SAME-direction M15 FVGs only.** Bearish setup counts bearish gaps. The logic: a gap above price left by a prior sell-off is unfinished selling, so filling it on the way up is the sweep collecting the last offers before the real move down. Uses the existing 50%-of-wick rule in `fair_value_gaps.py`. |
| 23 | **M15 Target Liquidity is capped at 5R**, reusing `entry_ob.TARGET_SEARCH_R = 5.0`, measured against the M15 entry's own `r_distance`. One notion of "a plausible draw" across the engine. Note this is a much shorter reach than the H1 gates in absolute pips, because R is now M15-sized (about 58 pips on EUR_USD rather than several hundred). |
| 24 | **`htf_threshold` is FIXED permissive, not searched. Only `total_threshold` is searched.** Pinned near the 10th percentile of the year's HTF scores, so it discards only clearly hopeless setups. Reason: about 43 setups a year means searching two gates fits luck rather than signal, and the HTF gate is largely redundant anyway because `total_probability` already contains the HTF factors. Its real value is matching the manual workflow and saving compute, not filtering better. |
| 25 | **HTF-blocked mitigations are journalled but EXCLUDED from weight learning.** They get a row with `entry_model` empty, `htf_probability` filled, and `taken=False`, so the gate's effect stays visible. The learner ignores them: without a realised R there is no outcome to learn from, and `update_weights` already leaves the table untouched at 0R. |
| 26 | **LC-2A accepts a WICK-ONLY fake break, and `fake_break_with_body_close` is what distinguishes it.** The detector only records a break on a CLOSE through the level, so a wick that pokes through and closes back was invisible and the factor was tautological at 100% yes. LC-2A now finds wick breaches directly. A body break is preferred outright over a more recent wick, which is what makes the user's third scenario (wick, wick, then a decisive close) a YES. |
| 27 | **CE requires a BODY break, and `ibos_with_body_close` is DROPPED. 31 factors, not 32.** The wick fallback was tried on CE and measured: it took CE from 3.7% to 9.6% of mitigations and left 94% of CE setups resting on unconfirmed breaks, inverting the conservative post-displacement model CE is meant to be. LC-2A keeps wick breaks because for LC-2A the break's strength is a QUALITY input (a wick induces fewer traders than a close, but it does induce some, so both are valid and the factor scores the difference). For CE it is a PRECONDITION: a wick-only breach means the range was never broken, so the premise fails rather than weakens. Note this is NOT a claim that fake breaks are wicks, which is false. A fake break is one that later reverses and can be a decisive body close that then fails. CE keeps 7 factors, every other model 8. |
| 28 | **CE's `has_inducements` counts only levels within 1.5x M15 ATR of the fib50 limit.** Measured over the whole breaking leg it answered 100% yes, because a leg of any length contains one of the five minor levels a side carries at any moment. The band is the region the pullback actually travels through, which is the user's own reasoning for wanting the factor. 1.5x is the looser end of his "about 1 or 1.5" and an explicit dry-run target. |
| 29 | **`no_other_lids` means: nothing unswept between the setup's STOP and the zone's far edge.** The user's definition, replacing a proximity-window version. His reasoning: liquidity beyond the stop is a magnet, price will eventually take it, and our stop is on the way. Counts BOTH sides ("any M15 minor liquidity levels"). Applied identically to all four models, which is what makes their learned weights comparable. |

---

## Cross-cutting rules, read before any phase

### Direction mirroring

Every rule below is written in **bearish** language, because that is how the models were described. Each
one has an exact bullish mirror, and getting one of them backwards is the single most likely bug in this
whole build. Keep this table next to you.

| Concept | Bearish H1 OB (we sell) | Bullish H1 OB (we buy) |
|---|---|---|
| Price approaches the zone | rallying **up** from below | falling **down** from above |
| Liquidity we want swept | **buy-side**, an unswept **high** | **sell-side**, an unswept **low** |
| Rejection candle that makes it | **green** (`close > open`), level at `high[i]`, qualifies when `high[i+1] <= high[i]` | **red** (`close < open`), level at `low[i]`, qualifies when `low[i+1] >= low[i]` |
| Swept means | a later `high > level` | a later `low < level` |
| OB mitigated means | `high >= ob_bottom` | `low <= ob_top` |
| **eq breached**, so no NEW setup may form (decision 8) | `high >= eq` | `low <= eq` |
| **Far edge**, the re-host constraint only (decision 8a) | `high <= ob_top` to re-host | `low >= ob_bottom` to re-host |
| Structure flip we need | `m15_*_low_event == "break of swing low"` | `m15_*_high_event == "break of swing high"` |
| Order type, LC models | **sell stop** below the host candle's low | **buy stop** above the host candle's high |
| Order type, CE | **sell limit** at fib50 | **buy limit** at fib50 |
| Stop loss | above the host candle's high, or above the CE range high | below the host candle's low, or below the CE range low |
| CE range | broken swing **high** down to the running **low** | broken swing **low** up to the running **high** |

**Implementation rule:** write each detector once with a `sign` variable (`-1` bearish, `+1` bullish) and
a `side` string, the way `sweep_credit.py` already does (`sign = -1.0 if side == LOW else 1.0`). Do not
write two mirrored code paths, and **test both directions for every rule.**

### Exclude versus negate

The repo already has two different behaviours for "the target got taken", and they must not be mixed up.
Both are in `backtest/factors.py`:

| Gate | What happens when price reaches the target | Where |
|---|---|---|
| **OB target** | every answer is **negated** (`negate=reached` in `_ob_factor_answers`) | `evaluate_ob_target_factors` |
| **Liquidity target** | the child is **excluded** from the denominator, and the gate never answers NO | `evaluate_liquidity_target_factors` |

The new **M15 Target Liquidity** gate is a liquidity gate, so it follows the **exclude** rule, exactly as
the user described: the LRLQ child that gets consumed drops out of the calculation rather than flipping to
NO. Nothing about OB target negation changes.

---

## Architecture

```
                    per instrument, full history, once
  ┌──────────────────────────────────────────────────────────────┐
  │  build_instrument_bundle()          [backtest/pipeline.py]   │
  │    df / obs / liq        <- unchanged, H1-indexed             │
  │    m15                   <- NEW: M15Bundle                    │
  └──────────────────────────────────────────────────────────────┘
                                  │
                    per H1 OB mitigation, per year
                                  v
   iter_mitigation_candidates(ctx)              [entry_ob.py, unchanged]
                                  │
                    HTF factors -> htf_probability
                                  │
                     htf_probability >= htf_threshold ?
                                  │ yes
                                  v
   scan_for_entry(ctx, ob_row, mitigation_bar)   [NEW entry_models.py]
     walks M15 bars from the mitigation hour forward until the
     eq is breached (decision 8) or the H1 OB dies (its own H1
     lifecycle), and returns the first model that fires under the
     precedence chain
                                  │
                 entry factors -> total_probability
                                  │
                     total_probability >= total_threshold ?
                                  │ yes
                                  v
              pending order -> fill resolved on M15 sub-bars
                                  │
                                  v
   simulate_trade(...)                          [simulate.py, H1 walk]
     first bar starts at the filling M15 sub-bar, and the BE recheck
     now includes the entry factors
```

The one thing to get right: **the M15 layer never enters the H1 universe.** It is looked up by M15 index,
and only its *result* (an entry price, a stop, a factor dict, a fill bar) crosses back into H1 space. That
is what keeps `ob_state`, `liq_state`, `smc/timeline.py`, `target_log.py` and the live-parity path
untouched.

---

## Phase 0: the M15 substrate

### `smc/market_structure/m15_structure.py` (new)

Copy the shape of `smc/market_structure/h1_structure.py` (128 lines, mostly docstring).

```python
M15_TIER_PERIODS = {"m15_fractal": 2, "m15_internal": 5}
M15_TIER_ATR_SEPARATION = {"m15_fractal": 0.0, "m15_internal": 0.0}
M15_ATR_PERIOD = 14

def compute_m15_structures(df, tier_periods=None, tier_atr_separation=None,
                           manual_restarts=None, atr_period=M15_ATR_PERIOD): ...
def m15_column_names(tier=None): ...
```

It is a loop over `tiered_fractal_structure.compute_tier_structure(df, prefix, n, ...)`, exactly like the
three existing wrappers. No `m15_swing` tier, because nothing in the entry models needs it.

Emits the standard 6 columns per tier: `m15_fractal_swing_high`, `m15_fractal_high_event`,
`m15_fractal_structure`, and the same set for `m15_internal_*`.

**Document in the module docstring**, echoing the warning in `roadmap/detection-method-decision.md`, that
n=5 for `m15_internal` is the user's stated figure rather than a derived one, and that Williams Fractal is
sensitive to trend-to-noise ratio. So n=5 on M15 is *less* verified than n=8 on H4. Flag it as a tuning
candidate.

### `smc/liquidity/minor_liquidity.py` (new)

The one genuinely new detector. Decision 5, made concrete.

```python
DEFAULT_PIVOT_N = 1
DEFAULT_LOOKBACK = 100

def compute_minor_liquidity(df, pivot_n=DEFAULT_PIVOT_N, lookback=DEFAULT_LOOKBACK):
    """Unswept minor highs and lows on a single timeframe."""
```

#### What the rule actually says, and why

Take the bearish case: an H1 bearish OB sitting above price, and price rallying up into it. We want an
unswept **high** below or inside the zone.

**Why a green candle's high is liquidity.** Earlier, price was in that area and then dropped away. The last
green (up) candle before that drop pushed to some high and failed. Two kinds of orders got left behind at
that high: breakout buyers who bought the push, and shorts who later placed protective stops just above it.
Nobody has come back for them, because price left downward. On the return trip up, price has to trade
through that high to reach the OB, and when it does, those orders fill. That is the fuel for the move down
that follows. This is the standard inducement idea, just measured on one candle instead of a structure.

**The two-part test.**

1. `close[i] > open[i]` (the candle is green). It was an attempt to go up.
2. `high[i+1] <= high[i]` (the *next* candle does not exceed it). The attempt **failed**.

Part 2 is the whole point. Without it, every candle in a rally qualifies and the concept is meaningless.
With it, we are only marking highs where the market tried to continue and could not, which is what makes
the high a ceiling rather than one step in a staircase.

**Worked example.** Five M15 candles, then price drops away and later returns:

| i | open | high | low | close | green? | next high | qualifies? |
|---|---|---|---|---|---|---|---|
| 10 | 1.1020 | 1.1032 | 1.1018 | 1.1030 | yes | 1.1041 | **no**, next candle exceeded it |
| 11 | 1.1030 | 1.1041 | 1.1028 | 1.1039 | yes | 1.1050 | **no**, next candle exceeded it |
| 12 | 1.1039 | 1.1050 | 1.1036 | 1.1048 | yes | 1.1045 | **yes**, level at 1.1050 |
| 13 | 1.1048 | 1.1045 | 1.1020 | 1.1022 | no | | not green |
| 14 | 1.1022 | 1.1025 | 1.1005 | 1.1008 | no | | not green |

Candles 10 and 11 were part of the push. Candle 12 is where the push died. **1.1050 is the level.** It stays
live until some later candle prints `high > 1.1050`, and the candle that does is the LC-1 sweep candle.

**Why not just require a proper n=1 fractal?** Because a fractal requires the **left** neighbour to be
lower too, and the rejection rule does not look left at all. That difference is not academic. It is exactly
the most common inducement shape:

| | `high[11]` | `high[12]` | `high[13]` | n=1 fractal at 12? | Rejection level at 12? |
|---|---|---|---|---|---|
| Push dies at the top | 1.1041 | **1.1050** | 1.1045 | yes | yes |
| Green candle inside a decline | 1.1070 | **1.1060** | 1.1055 | **no**, left is higher | **yes** |
| Equal left side | 1.1060 | **1.1060** | 1.1055 | sometimes | **yes** |

Row 2 is the one that matters: a green candle that failed, sitting inside a move down, with a higher candle
before it. That is the textbook "last up candle before continuation" whose high nobody came back for. A
fractal steps straight over it. Row 3 is the tie case, which `compute_fractal_pivots` handles with a
tie-tolerant past side (`_TIE_TOLERANCE = 4`) and so often catches, but not reliably.

Taking the **union** of the two sources means we never have to reason about which one wins.

#### Implementation

Two sources, unioned and deduplicated by `(side, candle_index)`:

- **Rejection candle.** A green candle `i` (`close > open`) produces a high-side level at `high[i]`
  when `high[i+1] <= high[i]`. A red candle produces a low-side level at `low[i]` when
  `low[i+1] >= low[i]`. **Confirmed at `i+1`, never at `i`**, because the rule reads candle `i+1`. Set
  `visible_from_index = i + 1` and `candle_index = i`. Getting this wrong is a lookahead bug that will
  quietly inflate every backtest number.
- **Fractal pivot at n=1.** `fractal_detector.compute_fractal_pivots(df, n=1)`, taken verbatim, which
  already sets `confirmed_index = pivot_index + 1`.

Row schema, deliberately shaped like `_LEVEL_COLUMNS` in `levels.py` so downstream code has one path:

```
side, kind, level, source, candle_index, visible_from_index, visible_from_date,
valid_through_index, ended_by, swept, swept_index, swept_date
```

`kind` is always `"minor"`. `source` is `"rejection"` or `"fractal"`. The sweep test is **wick-based and
strict**, matching every other detector in the repo (see `low_resistance.py:175`): `highs[k] > level` for
a high-side level, `lows[k] < level` for a low-side one. `ended_by` is one of `swept`, `expired`,
`data_end`.

### `backtest/intrabar.py` (extend)

`M15Index` already holds the H1-to-M15 boundary arrays. Add the inverse and a window helper:

```python
def h1_of(self, m15_index): ...     # which H1 bar contains this M15 bar
def range_from(self, k):  ...       # first M15 index at or after the start of H1 bar k
```

Both are `np.searchsorted` on arrays already stored. No new state.

### `backtest/m15_pipeline.py` (new)

Build the whole M15 substrate once per instrument, mirroring how `pipeline.py::_liquidity_tables` fans out
per timeframe.

```python
@dataclass(frozen=True, eq=False)
class M15Bundle:
    ts, open_, high, low, close     # float64 / int64 arrays
    london_hour, london_dow         # precomputed, same trick as MarketContext
    atr                             # np.float64, NaN during warm-up
    structure                       # the m15_fractal_* / m15_internal_* columns as arrays
    minor                           # arrays from compute_minor_liquidity
    levels                          # compute_liquidity_levels(df, pivot_n=2): equals + old_point
    lrlq                            # compute_low_resistance_liquidity(df, pivot_n=1)
    fvgs                            # compute_fair_value_gaps(df)
    credit                          # sweep_credit windows over minor + equals + lrlq

def build_m15_bundle(m15_df): ...
def slice_bundle(bundle, start, stop): ...   # index-threshold rebasing, same rule as slice_universe
```

Everything except `minor` is an existing function called with the M15 frame. `compute_atr_series` returns
a Python list with `None` during warm-up, so convert to `np.float64` with `NaN`, same as elsewhere.

**Store index thresholds, never lifetime booleans.** `slice_bundle` has to survive the same rebasing that
`ob_state.slice_universe` does, and a boolean "was swept" cannot be rebased.

---

## Phase 1: the four entry models

### `backtest/entry_models.py` (new)

The heart of the work. One public entry point:

```python
def scan_for_entry(ctx, ob_row, mitigation_bar, pip_size):
    """The first model to fire, or None.

    Walks M15 bars forward from the H1 mitigation hour. Returns a dict:
      {model, trigger_m15, order_price, sl, r_distance, direction,
       fill_m15, fill_price, evidence}
    `evidence` carries everything the factor evaluators need so they never
    re-derive geometry.
    """
```

Bearish language throughout below. **Every rule is mirrored for bullish, per the mirroring table above.**

**Shared scan state.** Walk M15 bars from `ctx.m15_index.range_from(mitigation_bar)` forward. Track:

```
eq            = (ob_top + ob_bottom) / 2.0
eq_breach     = first M15 index in the window whose WICK reaches eq
                bearish: high >= eq        bullish: low <= eq
                None until it happens
```

Two things end the scan:

- **The zone dies at H1.** The H1 bar containing the current M15 bar is past
  `obs.series["H1"].valid_through[ob_row]`. The H1 lifecycle always outranks the M15 view, and the M15
  layer never gets to keep a zone H1 has retired.
- **The eq breach closes the door on new setups.** See below.

**The far edge is no longer a scan terminator.** It was in an earlier draft. It is redundant now, because
the far edge lies beyond eq in both directions, so any candle reaching it has already breached eq and the
scan has already stopped forming setups. The far edge survives in exactly one place: the candle-B re-host
test (decision 8a). Do not add it back as a second terminator.

#### The eq rule at M15 resolution, and the N=2 conflict

Porting the H1 `eq` rule down to M15 (decision 8) collides head-on with the N=2 order window
(decision 7), and the collision is worth spelling out because the naive reading throws away good trades.

**The collision.** Take a bullish OB with bottom 100 and top 120, so `eq = 110`. An LC-2A forms where the
trigger candle A sweeps down to 109, which is past eq. Under a plain reading of the off-by-one, candle A is
tradeable but the next candle is dead. Yet N=2 says the next candle is **candle B**, the legitimate second
host for the order. Both rules are right and they contradict.

**The resolution: the two rules govern different things.**

| | What it protects | Applies to |
|---|---|---|
| eq off-by-one | do not go hunting for **fresh** setups in a half-consumed zone | setup **formation** |
| N=2 window | re-price the order because price made a new extreme | order **management** |

Candle B is not a new opportunity. It is the same opportunity re-priced. The eq rule has no business
blocking it. So:

```
Setup formation:   a trigger candle A is valid only while  eq_breach is None  or  A <= eq_breach.
                   A == eq_breach is ALLOWED. The breaching candle is the reaction, and the reaction
                   is the trade. This is the same off-by-one the H1 lifecycle already uses, where
                   invalidated_index is tradeable and invalidated_from_index is not.
Order management:  once A is fixed, the N=2 window runs its normal course regardless of eq. Candle B
                   may re-host. The only geometric constraint on B is the FAR EDGE, not eq, because
                   candle A already conceded eq.
Scan termination:  after eq_breach, stop looking for new trigger candles. Keep managing a setup that
                   has already formed.
```

Applied to the example: **candle A at 109 forms the setup, and the following candle may still re-host as
candle B.** What that candle cannot do is start a different setup.

**One consequence to accept knowingly.** If eq is breached by a candle that carries no setup, and a
trigger candle would have formed two candles later, the M15 rule rejects it while the H1 rule might not
have (if both fell inside one H1 bar, H1's own off-by-one would still allow it). **M15 is therefore
strictly stricter than H1 for setup formation.** That is the price of not being sensitive to where the hour
boundary falls, and it errs toward not trading. Say so in the docstring so nobody "fixes" it later.

**CE follows the same split.** The `m15_internal` break that forms CE must occur at or before `eq_breach`.
Once formed, the resting limit order and the second-leg re-anchor are management, not formation, and are
bounded by CE's own three abort conditions rather than by eq.

**Precedence.** A table keyed on `primary_tier` so it stays tunable, defaulting to one uniform chain:

```python
MODEL_PRECEDENCE = {
    "h1_fractal":  ("LC-2A", "LC-2B", "LC-1", "CE"),
    "h1_internal": ("LC-2A", "LC-2B", "LC-1", "CE"),
    "h1_swing":    ("LC-2A", "LC-2B", "CE"),          # NO LC-1 on swing
}
```

**There is no LC-1 on the swing tier.** A swing range is too large to predict a structure break off a
single swept minor high, and the user's rule is explicit. If `primary_tier == "h1_swing"` and the only
thing present is an LC-1 setup, produce **no candidate** and fall through to CE. Assert this in a test,
because the freshness rule alone does not enforce it.

LC-2A is tried before LC-2B when both are present, because a structural break is the stronger inducement.
That ordering is a **chosen default, not derived**, so say so in the docstring.

### LC-1: pre-existing liquidity

Preconditions on the trigger candle `A`:

1. At least one **unswept high-side `minor` level** whose `level` sits **at or below the OB top** and
   whose `candle_index` is within **10 M15 candles** of `A` (decision 16).
2. `A` sweeps it (`high[A] > level`) and `A` also **mitigates the H1 OB** (`high[A] >= ob_bottom`). Both
   conditions on one candle, or the sweep on `A` and the mitigation on `A+1`, which is the N=2 window.
3. `A` closes inside a killzone.

If several levels were swept by `A`, the **deepest one swept** is the reference for `LID with FVG`. That is
the user's stacked-liquidity edge case.

The "swept the first liquidity, pushed away, came back" case is handled by
`sweep_credit.compute_credit_windows` on the M15 bundle at `CREDIT_SPENT_ATR_MULTIPLE = 3.0`. A level
whose credit has expired by the time `A` arrives no longer counts as swept-and-still-relevant, so LC-1 does
not fire and we fall through the chain.

### LC-2A: fake break liquidity

1. On the approach leg, `m15_fractal` structure was **bullish** and then flipped **bearish**, meaning
   `m15_fractal_low_event == "break of swing low"`. The break may land before or after the H1 OB
   mitigation.
2. The **PBID** (post-break inducement) is the `m15_fractal_swing_high` that was live at the moment of the
   break, which is the high the trapped shorts put their stops above.
3. Trigger candle `A` sweeps the PBID and mitigates the OB, within the same N=2 window.
4. `A` closes inside a killzone.

### LC-2B: equals raid liquidity

1. On the approach leg, an **equals** high-side level from `compute_liquidity_levels(m15_df, pivot_n=2)`
   with `kind == "equals"`, sitting at or below the OB top.
2. Trigger candle `A` sweeps it and mitigates the OB, N=2 window, killzone close.
3. The angle test (decision 14) is a **factor, not a gate**, except that a second top more than
   `0.25 x ATR` from the first is never pooled as equals by the detector, so LC-2B simply never sees it.

### CE: confirmation entry

Only reached when nothing above fired.

1. The H1 OB must already be **mitigated**.
2. `m15_internal` (n=5) must break bearish, meaning `m15_internal_low_event == "break of swing low"`. The
   repo's break test is already close-based (`fractal_detector.py:364`), so "iBOS with body close" is
   about whether the *breaking candle's body* cleared the level, evaluated separately as a factor.
3. **Range** (decision 15). **Both directions spelled out, do not infer one from the other:**

   | | Bearish OB (sell) | Bullish OB (buy) |
   |---|---|---|
   | Range extreme | `range_high` = the broken `m15_internal_swing_high` | `range_low` = the broken `m15_internal_swing_low` |
   | Running extreme | `range_low` = lowest `low` since the break | `range_high` = highest `high` since the break |
   | Limit price | `range_high - 0.5 * (range_high - range_low)` | `range_low + 0.5 * (range_high - range_low)` |
   | Stop loss | `range_high + SL_BUFFER_PIPS * pip_size` | `range_low - SL_BUFFER_PIPS * pip_size` |
   | Recomputed when | the running low extends | the running high extends |

   Note that `limit_price` is the same arithmetic midpoint in both cases, so it can be written once. Only
   which end is fixed and which end runs differs.
4. Order type is a **limit**, not a stop. The fill is the first M15 candle whose **high reaches
   `limit_price`** (bearish) or whose **low reaches `limit_price`** (bullish).

**CE aborts**, the user's three conditions in order:

- **Second leg allowed.** If price pulls back shallower than fib50 and then makes a fresh `m15_internal`
  break **in the trade direction**, the range re-anchors to the new one and CE stays live, provided the
  recomputed `total_probability` is still at or above `total_threshold`.
- **Two legs maximum.** If the second leg also fails to pull back to fib50, abort.
- **Runaway guard.** Abort if the leg extends beyond **5x** the range height of the first leg. Condition 1
  usually fires first. This is the forced backstop the user asked for.

### Order handling for LC-1, LC-2A, LC-2B

Candle `A` hosts the order. `buffer = SL_BUFFER_PIPS * pip_size`, reusing the constant already in
`entry_ob.py`.

| | Bearish OB (sell stop) | Bullish OB (buy stop) |
|---|---|---|
| `order_price` | `low[A] - buffer` | `high[A] + buffer` |
| `sl` | `high[A] + buffer` | `low[A] - buffer` |
| Re-host on `B` when | `close[B] > high[A]` and `high[B] <= ob_top` | `close[B] < low[A]` and `low[B] >= ob_bottom` |
| Fill when | a later `low <= order_price` | a later `high >= order_price` |

Candle `C` means cancel (decision 7).

**The re-host constraint is the FAR EDGE, not eq** (decision 8a). Candle `A` may already have breached eq,
so testing `B` against eq would be incoherent. Test it against the zone's far edge only: `high[B] <= ob_top`
for a bearish OB, `low[B] >= ob_bottom` for a bullish one. Price all the way through the zone genuinely has
nothing left to trade against, which is a different statement from "half the zone is consumed".

Reject the setup when `r_distance < MIN_R_PIPS * pip_size`, the existing 3-pip floor, which will now bite
far more often than it does today. Count and print those rejections rather than silently dropping setups.

---

## Phase 2: entry factors and the two-threshold probability

### `backtest/entry_factors.py` (new)

32 factors, named per model to match `factors/eu_probability_factors.csv` rows 150 to 182 (the sheet
already holds candidate weights, though no code reads it). Naming follows the repo convention:

```
entry_lc1_h1_ob_is_fractal                 entry_lc2b_equals_formed_with_less_angle
entry_lc1_wicked_the_lid                   entry_lc2b_h1_ob_is_fractal
entry_lc1_lid_with_fvg                     entry_lc2b_lid_with_fvg
entry_lc1_no_imbalance_while_mitigation     entry_lc2b_wicked_the_pbid
entry_lc1_no_other_lids                    entry_lc2b_no_other_lids
entry_lc1_m15_target_liquidity             entry_lc2b_m15_target_liquidity
entry_lc1_m15_target_liquidity_lrlq        entry_lc2b_m15_target_liquidity_lrlq
entry_lc1_m15_target_liquidity_equals      entry_lc2b_m15_target_liquidity_equals

entry_lc2a_fake_break_with_body_close      entry_ce_ibos_with_body_close
entry_lc2a_wicked_the_pbid                 entry_ce_h1_ob_is_internal
entry_lc2a_h1_ob_is_fractal                entry_ce_has_imbalance
entry_lc2a_lid_with_fvg                    entry_ce_has_inducements
entry_lc2a_no_other_lids                   entry_ce_no_other_lids
entry_lc2a_m15_target_liquidity            entry_ce_m15_target_liquidity
entry_lc2a_m15_target_liquidity_lrlq       entry_ce_m15_target_liquidity_lrlq
entry_lc2a_m15_target_liquidity_equals     entry_ce_m15_target_liquidity_equals
```

`Swept Liquidity` is dropped from CE per the user. Only the **firing model's 8 factors** are evaluated. The
other 24 are omitted, and `compute_probability`'s dynamic exclusion already handles that. **Flag in the
docstring** that this makes scores non-comparable across models. That is already a documented property of
the formula (see the `factors.py` module docstring), but it becomes much more visible now.

Factor definitions:

| Factor | Rule (bearish) |
|---|---|
| `h1_ob_is_fractal` | `primary_tier == "h1_fractal"` (decision 3) |
| `h1_ob_is_internal` | `primary_tier == "h1_internal"`, so NO on swing, which is what discourages swing CE |
| `wicked_the_lid` / `wicked_the_pbid` | YES if `high[A] > level` **and** `close[A] <= level`. A close beyond penalises. |
| `lid_with_fvg` | The **sweep** candle also mitigates an opposite-direction M15 FVG (the 50%-of-wick rule in `fair_value_gaps.py`). If `A` took all the liquidity and `B` did the mitigating, this is NO. |
| `no_imbalance_while_mitigation` | No bullish M15 FVG formed in the **approach leg**, found by the running-minimum rule below. YES means clean. |
| `no_other_lids` | No remaining unswept high-side `minor` level between the trigger candle's high and the OB top. YES means nothing is left to draw price back up. |
| `fake_break_with_body_close` | The candle that broke `m15_fractal` structure **closed** beyond the level rather than only wicking. |
| `equals_formed_with_less_angle` | Second top within `0.10 x ATR(14)` of the first (decision 14). |
| `ibos_with_body_close` | The candle that broke `m15_internal` closed beyond the level. |
| `has_imbalance` | The leg that broke `m15_internal` contains at least one M15 FVG. |
| `has_inducements` | The leg that broke `m15_internal` contains at least one unswept `minor` level (n=1 fractal or rejection candle). |
| `m15_target_liquidity`, `_lrlq`, `_equals` | See below. |

#### The approach leg, and why the fixed lookback was wrong

The original plan used a fixed 10 M15 candles ending at the mitigation candle. **That is a bug**, and the
reasoning matters enough to write down here so nobody reintroduces it.

Suppose an H1 fractal OB, and M15 comes back to mitigate it in **5 candles**. A fixed 10-candle window
covers those 5 approach candles plus **5 candles from before the move even started**. Those earlier candles
belong to the leg that moved *away* from the OB, which is a completely different thing.

Now suppose those 5 earlier candles contain an imbalance. Under the fixed window the factor answers NO and
penalises the setup. But that imbalance is **evidence in favour**: it means the H1 fractal OB, which showed
no imbalance at H1 resolution, *did* leave an imbalance once you zoom into M15. That is a better zone, not
a worse one. The fixed window inverts the sign of the evidence.

#### Finding the leg start: the running-minimum rule

**Two obvious candidates both fail**, and knowing why is what justifies the rule we use:

- **"Most recent `m15_fractal` (n=2) pivot."** n=2 on M15 is noisy, so in any real advance there are
  several minor swing lows. The most recent one is often three or four candles back, sitting right under the
  zone. The leg collapses to a handful of candles that cannot form a gap, and the factor answers YES almost
  always. No information.
- **"The last time price left the OB."** On a first mitigation, price left the zone via the OB's own
  formation displacement, so the window stretches across the entire round trip: the whole move away plus the
  whole move back. Some FVG is nearly certain in a window that long, so the factor answers NO almost always.
  No information either.

**The rule: anchor the window on the previous QUALIFYING touch, then take the deepest point inside it.**

```
# bearish OB, price rallying up to mitigate
prev = the previous QUALIFYING touch of this OB, from ObSeries.touch_at[ob_row],
       taking the last entry strictly before the current touch
window_start = last M15 bar of prev's H1 bar, or the OB's earliest_trigger_index
               mapped to M15 when this is the first qualifying touch
leg_start    = index of the LOWEST low in [window_start, mitigation_index]
leg          = [leg_start, mitigation_index]     inclusive
```

Bullish OB mirrors it: `leg_start` is the index of the **highest high** in the window. The anchor rule
itself is direction-free, because "qualifying" already means "deeper into the zone than last time",
whichever side that is.

**What "inside the OB" means, settled.** It means a **qualifying** touch, not any wick overlap. Worked
through the user's example, a bullish OB with bottom 100 and top 120:

| | Candle 1 | Candle 2 (later) |
|---|---|---|
| Low reached | 110 | 120 |
| Penetration into the zone | 50% | 0%, exactly the top edge |
| Registers under the plain mitigation test (`low <= ob_top`) | yes | yes, because the repo uses `<=` |
| Counts as a **qualifying** touch | yes | **no**, it is shallower than candle 1 |
| **Anchor?** | **yes** | no |

So candle 1 is the anchor and candle 2 is ignored. Two reasons this is the right cut:

- **It avoids the degenerate case.** If a candle that merely kisses the top edge could reset the anchor, the
  window would collapse to one or two candles and the factor would answer YES trivially. That is the exact
  failure mode that rules out the n=2 pivot, so it should not be let back in through the anchor.
- **It keeps the entry layer and the OB lifecycle in agreement.** Qualifying touches are already what decide
  which mitigations become entry candidates at all (`iter_mitigation_candidates`), so reusing the same
  notion means there is one definition of "price came back to this zone", not two.

Reuse `ObSeries.touch_at[ob_row]`, which already holds the qualifying-touch H1 bars per OB, derived from
`qualifying_touch_indices` on the OB row. **No new detection code.** And because each qualifying touch must
be strictly deeper than the last, the previous qualifying touch is also the deepest so far, so "last
qualifying touch" and "deepest penetration so far" are the same bar.

**Why the running minimum is the right answer to "where did the leg start."**

- It *is* the pivot that initiated the move, by construction. The deepest low in a window that ends with
  price up at the zone is necessarily the turning point the advance began from. No pivot detection needed.
- **No structure parameter, so no noise sensitivity.** It cannot be fooled by M15 chop the way an n=2 or
  n=5 pivot can, and there is no new number to tune.
- It matches the user's words exactly: "the pivot candle which initiated the move towards the H1 OB." Note
  this deliberately **includes** intermediate pullbacks. If price rallies, pulls back, then rallies again
  into the zone, the leg starts at the *first* low, not the pullback low. The whole advance is the move
  toward the OB.
- Cost is one `np.argmin` over a bounded slice, so it is free.

**Edge cases to handle explicitly.**

- `leg_start == mitigation_index` (a one-candle leg): answer **YES**. A single candle cannot contain a
  three-candle gap.
- First qualifying touch and the OB's `earliest_trigger_index` maps at or past the mitigation: fall back to
  the first M15 bar of the H1 mitigation hour and answer YES. Never crash, never scan backwards unbounded.

**The factor's polarity, confirmed with the user.** The factor scans `[leg_start, mitigation_index]` and
answers **YES when NO same-direction M15 FVG formed anywhere in that range**. A clean approach leg is the
good case, and imbalance in the leg is the penalty. The reasoning: a gap in the approach means the rally
into the zone was inefficient, so price may have to come back and fill it, which would run our stop before
the real move ever starts. Bearish setup looks for bullish FVGs, bullish setup for bearish ones, reusing
`compute_fair_value_gaps` on the M15 frame with its existing 50%-of-wick fill rule.

**Do not let this polarity drift.** The factor name says `no_imbalance`, so a YES must mean the absence of
imbalance. Assert it in a test with an explicitly named case rather than relying on the name.

**This is now a settled definition**, not an open question. Document it in the function docstring as chosen
rather than derived, and flag it as a candidate to revisit once there are walk-forward results. Per the
user, pre-pivot imbalance does **not** get its own positive factor.

#### The M15 Target Liquidity gate

It mirrors the existing Liquidity Target gate semantics exactly, which is the gate in
`factors.py::evaluate_liquidity_target_factors` that *never answers no*:

- A child answers **YES** if a live level of that kind sits in the trade direction, with
  `visible_from_index` within **30 M15 candles** of the trigger candle. Otherwise the child is
  **excluded**, never NO.
- The **parent** answers NO only when *neither* child is present.
- Per the user's explicit note: once price reaches and takes a target out, the child is **excluded from
  the denominator**, not flipped to NO. This is the liquidity-gate behaviour. **OB targets keep their
  existing negation behaviour and are not changed.** See "Exclude versus negate" near the top.

### `backtest/factors.py` (modify)

- `ALL_FACTORS` becomes `ALWAYS + MITIGATION_OB + OB_TARGET + SWEPT_LIQUIDITY_GATE + LIQUIDITY_TARGET +
  MITIGATION_LEG + ENTRY`, so 151 + 32 = **183**.
- `compute_probability` is **unchanged**. It already does everything needed.
- Add `htf_factor_results` and `entry_factor_results` as separate dicts on the signal so the two gates can
  be scored independently, then `compute_probability(merged, weights)` for the total.
- `weights.ensure_factors` backfills the 32 new names at 1.0, so every stored CSV keeps working.
- **Do not touch the order of `_SHARED_SWEPT_FACTORS`.** `target_log.TARGET_LOG_BITS` derives bit
  positions from it and is append-only.

### `backtest/settings.py` (modify)

```python
DEFAULT_SETTINGS = {
    "htf_threshold": None,      # NEW
    "total_threshold": None,    # NEW
    "threshold": None,          # kept, for legacy files and the pre-M15 baseline path
    "tp_multiple": 2.5,
    "max_sl_size_pips": None,
}
```

`load_settings` already tolerates four on-disk shapes including literal `null`. Add a fifth rule: when a
file has only `threshold`, read it as `total_threshold` and leave `htf_threshold` at `None` (no gate).
`is_taken` compares `signal["total_probability"] >= total_threshold`.

### `backtest/analysis.py` (modify)

A joint 2-D threshold search on top of the existing 3-axis grid is too expensive. Search **sequentially**:

1. `htf_threshold` over `THRESHOLD_QUANTILES` of the year's *HTF* probability distribution, scored on how
   many profitable setups survive the gate.
2. With that winner fixed, run the existing grid (`total_threshold` x `TP_MULTIPLE_GRID` x
   `SL_SIZE_QUANTILES`) over the surviving candidates.

`MIN_TRADES_FOR_CONSIDERATION = 8` still applies. **State in the docstring that sequential is not the same
as jointly optimal.** It is a tractability choice.

---

## Phase 3: wiring

| File | Change |
|---|---|
| `backtest/m15_pipeline.py` | new, Phase 0 |
| `backtest/entry_models.py` | new, Phase 1 |
| `backtest/entry_factors.py` | new, Phase 2 |
| `smc/market_structure/m15_structure.py` | new, Phase 0 |
| `smc/liquidity/minor_liquidity.py` | new, Phase 0 |
| `backtest/intrabar.py` | add `h1_of()` and `range_from()` |
| `backtest/pipeline.py` | `build_instrument_bundle` loads `_M15.parquet` and calls `build_m15_bundle`. `PipelineBundle` gains `m15`. `build_live_context` gains a 4th frame and rebases the M15 bundle with the same offset rule. |
| `backtest/context.py` | `MarketContext` gains `m15_bundle` alongside the existing `m15` (`M15Index`). `None` stays a first-class value, so every M15 code path must degrade to "no candidate" rather than crash. |
| `backtest/entry_ob.py` | `resolve_entry_bar` loses the H1 killzone gate (decision 4). `build_setup` is replaced by a thin call into `entry_models.scan_for_entry`. Keep the two existing rejections (entry beyond its own stop, `MIN_R_PIPS`). |
| `backtest/simulate.py` | `find_signals` runs the HTF gate then the M15 scan, and enforces one-taken-per-OB (decision 9). `simulate_trade`'s **first bar** starts at the filling M15 sub-bar rather than the H1 close. Step 3's BE recheck merges the entry factors and compares against `total_threshold`. |
| `backtest/journal.py` | `JOURNAL_COLUMNS` gains `entry_model`, `htf_probability`, `total_probability`, `m15_trigger_time`, `m15_fill_time`, `order_host_candle`, `entry_excluded_gates`. **Anything not in this list is silently dropped by `save_journal`.** |
| `backtest/runner.py` | thread the M15 bundle through `run_year` into `build_market_context`. `m15_df` is already a first-class optional argument. |
| `roadmap/entry-models.md` | replace the 6-line stub with this spec |
| `roadmap/enhancements.md` | add the **per-tier OB** follow-up (decision 3). The approach-leg definition is settled and no longer belongs here, so record it in the `entry_factors.py` docstring instead |

### Two invariants that must survive

1. **TP independence** (`tests/test_tp_independence.py`, and `simulate.py`'s docstring: *"Do not introduce
   any TP dependence into the walk"*). The M15 fill changes where the walk *starts*, never what it knows
   about TP.
2. **max_r attribution excludes the terminal bar** (`tests/test_max_r_attribution.py`). The new
   first-bar-from-a-sub-bar path is exactly where this is easy to break.

### Behaviour changes to expect and report

- **R distances collapse** from zone width (often 15 to 30 pips) to M15 candle width (4 to 12 pips).
  Absolute TP distances shrink by the same factor. `MIN_R_PIPS = 3.0` will start rejecting real setups, so
  count and print the rejections instead of swallowing them.
- **Candidate count falls**, possibly a lot. Every mitigation now has to clear the HTF gate, produce a
  model, and clear the total gate. The baseline to compare against is in `TRADE_CALENDAR_2020_2025.md`:
  threshold 50 gave 410 trades, +22.58R, 31.2% strike.
- Entries can now land **outside** the H1 bar that mitigated the zone.

---

## Phase 4: live runner (do last, separately)

`live/` needs M15 and, more importantly, **pending orders**. It currently only sends market orders.

- `live/mt5_connector.py::TIMEFRAMES` gains `"M15": mt5.TIMEFRAME_M15`.
- `run_live.py` fetches an M15 frame (~3000 bars) and passes it to `build_live_context`.
- New `send_pending_order(...)` for `ORDER_TYPE_SELL_STOP`, `SELL_LIMIT`, `BUY_STOP`, `BUY_LIMIT`, plus
  cancellation when the N=2 window closes or the setup dies.
- `POLL_SECONDS = 15` is already fast enough for a 15-minute bar.

Do not start this until Phase 3 has a clean walk-forward run.

---

## Verification

Run everything from the repo root with `.venv/bin/python`.

### 1. Unit tests, the primary gate

Follow the house style exactly: hand-built OHLC bars, no parquet, no network, `pytest.approx` for R
values, one test per rule so a failure names the broken rule. Extend `tests/conftest.py`'s `m15()` fixture.

New files:

- `tests/test_m15_structure.py`: the n=2 and n=5 tiers produce the expected pivots and flip events, and
  there is no lookahead (a pivot is applied at `pivot_index + n`, never earlier).
- `tests/test_minor_liquidity.py`: rejection candle detected only when the *next* candle fails to exceed
  it, n=1 fractal picked up, sweep is wick-based and strict, and a level swept then re-approached. Plus:
  a green candle whose next candle **does** exceed it produces **no** level (the staircase case), and
  `visible_from_index == i + 1` rather than `i` (the lookahead guard).
- `tests/test_entry_models.py`, the big one. One test per rule:
  - LC-1 fires on sweep plus mitigation in one candle, and across the N=2 window
  - LC-1 does **not** fire when the BSLQ is 11 candles old (decision 16)
  - LC-1 does **not** fire when the 3x ATR credit on the swept level has expired
  - **LC-1 does not fire at all when `primary_tier == "h1_swing"`**, and the scan falls through to CE
  - LC-2A needs a structural flip, and the PBID is the swing high live at the break
  - LC-2B needs an equals, and a second top 0.30x ATR lower is never pooled so LC-2B cannot fire
  - CE only after mitigation, fib50 re-anchors as the running extreme extends, all three abort conditions
  - the order re-hosts on candle B and cancels on candle C (decision 7)
  - **the eq versus N=2 case, built from the user's own numbers.** Bullish OB 100 to 120, so eq is 110.
    Trigger candle A wicks to 109. Assert the setup **forms**, and assert the next candle may still
    **re-host as candle B**. Then assert that a *different* setup attempting to form on that same next
    candle is **rejected**. This is the single most subtle rule in the build.
  - a trigger candle appearing two candles **after** an eq breach that carried no setup is rejected
  - candle B is tested against the **far edge**, not eq, so a B that goes deeper past eq still re-hosts
  - the H1 lifecycle outranks M15: a zone past `valid_through` yields no candidate even when the M15
    geometry is perfect
  - a trigger candle outside a killzone produces no candidate (decision 4)
  - precedence: with both an LC-1 level and a fake break present, LC-2A wins
  - **every one of the above, mirrored for a bullish OB.** Parametrise the fixtures by direction so the
    mirror cannot be forgotten. This is the highest-value test in the suite.
- `tests/test_entry_factors.py`: each of the 32 factors, YES and NO, plus the exclusion cases, and the M15
  target liquidity gate **never answering NO** at child level. Two specific cases worth naming:
  - `no_imbalance_while_mitigation` answers **YES** when an FVG sits *before* the approach pivot and
    **NO** when one sits inside the leg. This is the exact bug the fixed 10-candle window had.
  - the same factor on a **second** touch of one zone does not reach back past the first touch. Build a
    fixture where an FVG sits between the two touches and assert YES, which fails if the anchor is missing.
  - **a shallower re-tap does not move the anchor.** The user's own example: bullish OB 100 to 120, candle 1
    down to 110, later candle 2 down to only 120. Assert the anchor is candle 1. This fails if the anchor
    uses plain wick overlap instead of qualifying touches.
  - the leg start is the **first** low of a rally that pulled back mid-way, not the pullback low.
  - a one-candle leg answers YES rather than raising.
  - **polarity, named explicitly:** a leg containing an FVG answers **NO**, a clean leg answers **YES**.
  - a consumed M15 target liquidity child is **excluded**, while a reached OB target is still **negated**.
- `tests/test_two_thresholds.py`: the HTF gate blocks the M15 scan entirely, a legacy settings file with
  only `threshold` still loads, and `is_taken` reads `total_probability`.

Must still pass unchanged: `test_tp_independence.py`, `test_max_r_attribution.py`,
`test_trade_management.py`, `test_live_probability_recheck.py`, `test_entry_ob.py`.

```
.venv/bin/python -m pytest -q
```

### 2. Detector smoke test on real data

Add `scripts/demo_entry_models.py`, following the 17 existing `demo_*.py` files (they all do the
`sys.path.insert` plus `# noqa: E402` dance). For one instrument and a short date range, print every H1 OB
mitigation with: HTF probability, whether the M15 scan ran, which model fired or why none did, the order
price, the SL, R in pips, the 8 factor answers, and the total.

**Eyeball 20 to 30 of these against the chart before trusting any backtest number.** This is the step that
catches a mirrored bullish/bearish rule.

### 3. Walk-forward comparison

```
.venv/bin/python scripts/backtest_eurusd.py     # single instrument first
.venv/bin/python scripts/backtest_multi.py      # all 10, 2020 to 2025
```

Compare against `TRADE_CALENDAR_2020_2025.md` (410 trades, +22.58R, 31.2% strike at threshold 50). Expect
fewer trades at a higher strike rate. What to check:

- candidate and taken counts per year in `data/settings/<PAIR>/*.json`
- the `entry_model` distribution in the journals. If one model is 90% of fills, its gate is too loose.
- how often `MIN_R_PIPS` rejected a setup
- whether the learned weights in `data/weights/<PAIR>/*.csv` move the 32 new factors away from 1.0 at all.
  A factor that never leaves 1.0 either never fires or never discriminates.

Back up `data/journal/`, `data/weights/` and `data/settings/` first. The repo already has the convention:
`data/_backup_15factor_2020_2025/`, `data/_backup_pre_ob_trigger/`.

### 4. Live parity

`build_live_context(..., tail=200)` must produce, for the last H1 bar of the backtest frame, the **same**
model, order price, SL and total probability as the full-history run. A tail that clips the M15 bundle
mid-setup is the obvious failure mode. Add this as a test, not a manual check.

---

## Phase 0 as built: three deviations from this plan

**1. `M15Index` is the WRONG seam, and `h1_of`/`range_from` were reverted.**
The plan assumed M15 indices were full-history. They are not:
`runner.run_year` calls `_window(m15_df, start, end + tail)`, so the frame reaching
`build_market_context` is windowed per year and `M15Index`'s indices address that window only.
Detecting M15 liquidity on a year window would blind LC-1 to anything formed across a year boundary,
which is the exact bug `build_pipeline_bundle` avoids for order blocks. So `M15Bundle` is built over
**full history**, and the two spaces are crossed by **timestamp only**, via
`m15_index_at_or_after(bundle, ts)` and `h1_bar_containing(h1_ts, ts)` in `backtest/m15_pipeline.py`.
`intrabar.py`'s docstring now carries the warning, since mixing the two integer spaces is silent.

**2. No `slice_bundle`.** It only made sense under the wrong assumption above. Nothing re-cuts the
bundle, so there is no offset to rebase. The plan's "store index thresholds, never lifetime booleans"
warning is moot here, and adding slicing back would reintroduce the index-space confusion.

**3. `credit` is deferred to Phase 1.** `sweep_credit` takes an events frame
(`kind, side, level, swept_index, expires_index`), and the projection from a minor-liquidity table into
that shape is decided by what LC-1 asks of it. Building it with no consumer would be guessing.

Also worth knowing: `build_m15_bundle` takes about 2.4s per instrument-year of M15, so roughly 40s over
full history. Once per instrument, reused across all six years, so cheaper than the per-year alternative.

## Phase 0 finding: LC-1's gate is far too loose as specified

Measured on real 2024 M15 data, both instruments, before any of Phase 1 exists:

| | EUR_USD | XAU_USD |
|---|---|---|
| Minor levels per 100 bars | 55.7 | similar |
| Bars with **any** unswept high-side level live | 97.8% | 97.1% |
| Bars with an unswept level **at most 10 candles old** | 89.6% | 88.6% |
| High-side levels live **simultaneously** (median) | 5 | 5 |

Two consequences, both about the spec rather than the implementation:

- **Decision 16's freshness rule does nothing.** It exists to stop LC-1 firing on almost everything, and
  the precondition is still satisfied on ~90% of bars. n=1 minor liquidity is simply everywhere.
- **`no_other_lids` will answer NO almost always.** With a median of 5 live levels on a side, sweeping one
  leaves four standing. A factor that is near-constant carries no information and its learned weight will
  drift on noise.

**The cause is a gap in this plan, not a new problem.** The original description said the liquidity sits
*"just below or inside the H1 OB"* and *"within nearby distance from the OB"*. This plan rendered that as
"at or below the OB top", which for a bearish zone is the entire region beneath it. The proximity
requirement was in the spec and got dropped.

### The fix, measured (decisions 20 and 21)

Hit rate of LC-1's liquidity precondition at several bounds, 2024 M15, using each bar's own high as a
stand-in for a zone's near edge:

| Bound | EUR_USD | XAU_USD |
|---|---|---|
| None, as originally planned | 89.6% | 88.6% |
| Within 2.0x ATR of the near edge | 16.6% | 14.8% |
| **Within 1.0x ATR (chosen)** | **15.8%** | **14.0%** |
| Within 0.5x ATR | 13.3% | 11.5% |
| Within 0.25x ATR | 10.0% | 8.0% |

Two things to note beyond the headline drop:

- **1.0x sits on a plateau, not a cliff.** The gap between 1.0x and 2.0x is under one percentage point, so
  the rule is not balanced on the exact constant and a later tuning nudge will not swing behaviour wildly.
  The knee is below 0.5x.
- **The bound reads correctly on a chart.** Median M15 ATR(14) is 0.00047 on EUR_USD, so 1.0x ATR is about
  **4.7 pips** below the zone, and about **$2.73** on XAU_USD. That is "just below" as described, not a
  loose region.

Implement both in Phase 1, in `entry_models.py` (the LC-1 gate) and `entry_factors.py`
(`no_other_lids`). Read the bound from one shared constant so the two cannot drift apart.

The union is doing real work, though, which validates decision 5: the rejection rule contributes 2,866
levels the n=1 fractal source misses, 21% of the total.

## Phase 1 as built

Two rules had to be added that this plan did not specify, both forced by
implementation rather than chosen freely:

**1. Sweep consumption: one sweep produces one trigger.** Without it the N=2 bound silently doubles. A
sweep on bar 23 that also mitigates triggers at 23, and then bar 24 mitigating with the sweep one bar back
triggers again off the SAME liquidity, so the order is effectively live from 23 to 25. Both readings are
individually legitimate, which is why the ambiguity had to be closed rather than left to whichever branch
ran first. A sweep is consumed by the first trigger it produces, whether or not the order fills.

**2. `APPROACH_LOOKBACK_BARS = 30`, chosen not derived.** LC-2A's fake break and LC-2B's equals pool both
form on the approach leg, which is largely BEFORE the mitigation bar the scan starts on. The user was
explicit that the break "can happen after mitigation of H1 OB or before it", so the models must look back
or every break that formed on the way in is invisible. 30 M15 candles is 7.5 hours, about one session, and
matches the M15 target liquidity window so the entry layer has one notion of "recent" rather than two.

Also worth noting: `_resolve_order` had a bug on first write. The fill scan was unbounded, so the order
rested at candle A's level forever and the candle-B re-host could never happen. A resting order is now
live for its host candle and the one after it, which is what "candle C means cancel" has to mean.

### Real-data results, 2023 to 2024

| | EUR_USD | XAU_USD |
|---|---|---|
| H1 OB mitigations | 729 | 657 |
| Produced an entry | **11.9%** | **16.9%** |
| LC-1 | 2.7% | 7.2% |
| LC-2A | 5.3% | 6.7% |
| LC-2B | **0.1%** | **0.8%** |
| CE | 3.7% | 2.3% |
| R median | 11.5 pips | 262 pips (about $2.62, roughly 1x M15 ATR) |
| R p10 / p90 | 7.7 / 19.5 | 155 / 579 |

**No model dominates**, which was the main risk going in and the thing the plan told us to watch. The mix
differs sensibly between instruments (LC-2A leads on EUR_USD, LC-1 on XAU_USD). LC-1 correctly never fires
on `h1_swing`.

**Two corrections to this plan's predictions:**

- **`MIN_R_PIPS = 3.0` does not bite at all.** 0% of setups fall under 5 pips on either instrument, so the
  plan's warning that it "will start rejecting real setups" was wrong. Nothing needs the rejection counter
  it asked for, though leaving it in costs nothing.
- **R collapses less than predicted.** The plan said 4 to 12 pips. The median is 11.5 with a p90 of 19.5.
  Still a large reduction from zone width, just not as extreme.

**Two things to watch in Phase 3:**

- **Candidate count falls roughly 6 to 8x**, because the old engine produced a candidate on every
  mitigation. EUR_USD gives about 43 setups per year before any threshold filter. That is workable but
  tight against `MIN_TRADES_FOR_CONSIDERATION = 8`, and the threshold search will cut further.
- **LC-2B is close to dead**, 1 setup in 729 on EUR_USD. A swept M15 equals pool right at a zone is simply
  rare. Its 8 factors will never accumulate enough evidence to learn a weight, so expect them pinned at
  1.0. Not a bug, but it means the LC-2B factor set is decoration until something loosens.

### One blocker found for Phase 3

**`primary_tier` is not carried onto `ObSeries`.** Decision 3 resolves the OB tier from it, and the entry
models need it for precedence, but `to_h1_space` does not copy it and `PipelineBundle` does not retain the
OB table. The real-data run above had to recompute `compute_h1_order_blocks` purely to reach the column.
Phase 3 must add a `primary_tier` array to `ObSeries` in `smc/order_blocks/ob_state.py`. It is a fixed
per-OB string, so it belongs alongside `src_index` rather than in the all-boolean `quality` dict.

## Phase 2 so far: four factors were dead on arrival

Measuring the factors on real 2023 to 2024 EUR_USD is what caught this. Four of the 32 could never learn
anything, and the fixes are decisions 26 to 29.

| Factor | Before | After | Why it was broken |
|---|---|---|---|
| `lc2a_fake_break_with_body_close` | 100% yes | **88.6%** | Tautological. The detector only records a break on a close through the level, so "did the body close" was asking "was it a break". |
| `ce_ibos_with_body_close` | 96% yes | **dropped** | Same tautology. Fixing it by accepting wick breaks inverted CE, so the factor went instead. |
| `ce_has_inducements` | 100% yes | **33.3%** | Measured over the whole breaking leg, which always contains one of the five minor levels a side carries. |
| `ce_no_other_lids` | 0% yes | **40.7%** | Structurally impossible: CE never sweeps, so liquidity was always still standing in the old proximity window. |

The `no_other_lids` redefinition helped every model, not just CE: LC-1 went 25% to 40%, LC-2A 20.5% to 50%.
It now sits near the middle of its range everywhere, which is where a boolean factor is most informative.

### Where the 31 factors stand

Every factor now varies except two. `entry_lc2b_*` has n=1 so it has no signal at all, which is the LC-2B
scarcity already recorded above rather than anything new. And **`entry_ce_has_imbalance` is still 92.6%
yes**, so it is the one remaining near-constant. The same band treatment that fixed `has_inducements`
would probably fix it, but that was not asked for and it is left as an open item rather than changed
unilaterally.

The target-gate children reading 100% yes is CORRECT and not a defect: a child is YES or omitted, never
NO, so its information lives in whether it was evaluated at all.

### Model mix, unchanged by all of this

EUR_USD 2023 to 2024, 729 mitigations: fires on 12.6%. LC-2A 6.0%, CE 3.7%, LC-1 2.7%, LC-2B 0.1%.

## Phase 2 as built: one deviation

**The entry factor NAMES live in `factors.py`, not `entry_factors.py`.** The plan had it the other way
round. `factors.py` has **zero imports** by design, to the point of testing NaN with `price != price`
rather than importing `math` (see the comment in `evaluate_mitigation_leg_swept_factors`). Importing the
names from `entry_factors.py` would have hung pandas, numpy and every `smc` detector off `ALL_FACTORS` by
transitive import and undone that.

So the dependency points the other way: `factors.py` owns `ENTRY_MODEL_FACTORS` and the name builders,
exactly as it already owns `_gate_factor_names`, `_swept_gate_names` and `_target_gate_names`, and
`entry_factors.py` imports them and supplies only the evaluation logic. `ALL_FACTORS` is now **182**
(151 + 31).

### The threshold work, as specified

- `settings.py` grew `htf_threshold` and `total_threshold`. A fifth on-disk shape is handled: a file
  carrying only `threshold` maps onto `total_threshold` and leaves `htf_threshold` at None, so all six
  stored EUR_USD years still load and keep their old meaning. Verified against the real files.
- `is_taken` reads `total_probability`, falling back to `probability` so the pre-M15 path is unchanged.
- `analysis.py` gained `htf_gate_threshold()` and `HTF_GATE_QUANTILE = 0.1`. The HTF gate is read off the
  year's own distribution but **not gridded**, per decision 24. The searched grid now reads
  `total_probability` and writes both `total_threshold` and `threshold` so a settings file stays readable
  by anything predating the entry layer.

## Phase 3 as built

Everything in the plan's table, plus four things it did not anticipate:

**1. `primary_tier` added to `ObSeries`** (the blocker recorded after Phase 1). A per-OB string, so it
sits next to `src_index` rather than in the all-boolean `quality` dict, and `_rebase_series` carries it
unshifted like `top`/`bottom`/`sign`.

**2. `resolve_entry_bar` and `build_setup` DELETED from `entry_ob.py`**, not deprecated. Both encode rules
the strategy has explicitly moved off (the H1 killzone gate, and entry at the mitigating candle's close),
and leaving them importable invites wiring them back in. `test_entry_ob.py`'s DST coverage was moved onto
the M15 gate rather than deleted with them, because that is the half worth keeping: an M15 bundle derives
`london_hour` through the same `killzone.london_fields`, so the fixed-offset bug is still reachable.

**3. `find_signals` had NO tests, before or after.** `tests/test_find_signals.py` is new ground. It builds
an `ObUniverse` by hand the way `test_ob_factors.py` does, because detecting a real order block needs
enough H1 history to bury the three bars that matter.

**4. The frozen/live split was incomplete on first write.** `evaluate_entry_factors` still folded the M15
target gate in, so a stale target answer landed in the dict the mid-trade recheck builds on. The gate is
now its own function, re-asked every bar at that bar's M15 position, which is what the user described:
an LRLQ target price has taken out drops OUT of the calculation.

`apply_settings` also gained the one-trade-per-OB rule. It lives there rather than in `find_signals`
because "taken" is what the rule counts, and only the settings layer knows it: a candidate that never
cleared the threshold costs the zone nothing.

## Phase 3 results: EUR_USD 2020 to 2025

**Read the per-trade columns, not the totals.** The new layer produces 199 candidates where the old
produced 886, so totals are not comparable and the headline understates it badly.

| Basis | OLD (H1 close) | NEW (M15 entry) |
|---|---|---|
| Taken trades, pinned 2.5R | +30.72R over 276, **+0.111 R/trade** | +15.27R over 101, **+0.151 R/trade** |
| ALL candidates, pinned 2.5R | +21.80R over 886, **+0.025 R/cand** | +17.06R over 199, **+0.086 R/cand** |
| R distance, median | 18.4 pips | 12.3 pips |

The all-candidates row is the cleanest comparison because it removes the threshold and max-SL search
entirely, so neither side benefits from a fit. On it the entry layer is **3.4x better per candidate**.

### LC-1 is badly negative and is the whole problem

All candidates, pinned 2.5R:

| Model | n | Total | Per trade | Strike |
|---|---|---|---|---|
| LC-2A | 96 | +28.00R | +0.292R | 38.1% |
| CE | 51 | +13.60R | +0.267R | 36.2% |
| **LC-1** | **50** | **-22.53R** | **-0.451R** | **14.0%** |
| LC-2B | 2 | -2.00R | -1.000R | 0.0% |

Without LC-1 the layer is +39.6R over 149 candidates, or +0.266 R/candidate.

Not a bug, on the evidence. 37 of 50 LC-1 exits are plain stop-outs, and its `max_r_reached` p90 is
**2.54** against **8.53** for LC-2A and CE combined. So LC-1 enters where there is no follow-through at
all, not merely where direction is wrong. That matches the user's own description of it as the most
aggressive model, predicting a break off an old liquidity sweep.

### An open question: the long/short asymmetry

| | bearish | bullish |
|---|---|---|
| OLD | +0.077 R/cand, 31.4% | -0.026 R/cand, 30.1% |
| NEW | +0.371 R/cand, 40.8% | -0.266 R/cand, 19.2% |

The asymmetry **predates the entry layer**, so it is not a mirroring bug introduced here, and the mirror
is tested exhaustively (every rule parametrised by direction, fixtures reflected through a price axis).
But the entry layer **amplifies** it, from a 0.103 spread to 0.637. Either the tighter stops leverage
whatever edge exists in each direction, or something in the M15 geometry is quietly worse on the long
side. Unresolved, and worth settling before Phase 4.

## Phase 4 as built

**The plan missed that `scan_for_entry` cannot serve the live bot.** It answers a historical question
("where did this setup fill") and needs the fill in hand. The live bot has to place the order BEFORE any
fill exists. So `entry_models.pending_order_for` is new: the same walk, bounded at the last closed M15 bar,
reporting the order that should be RESTING rather than one that filled.

That mattered more than it looks. The alternative, market-ordering once the fill candle closed, would enter
at that candle's CLOSE, which can be most of the way to the stop. It would have quietly discarded the M15
precision the whole entry layer exists for while still appearing to work.

**The clock moved from H1 to M15.** The old loop matched `entry_time == latest_candle_time` against the
newest H1 candle. With M15 entries that never matches, and waking hourly would place orders up to 45
minutes stale.

**`find_signals` gained `pending_as_of`** rather than growing a parallel live function. The HTF gate, the
factor scoring and the signal shape are identical, and the live bot has to score a candidate exactly as
the backtest would or the thresholds it inherits mean nothing.

**The order decision was extracted to `live/pending_plan.py`, which does NOT import MetaTrader5.** That is
the whole reason it is a separate module: MT5 is Windows-only, so anything importing it cannot be tested
where this was written, and order placement is the last logic that should go untested. `run_live.py` now
only executes the plan. Two rules are pinned by tests because they cost money rather than opportunity: an
order whose setup died gets cancelled, and a price change is a REPLACE, never a modify (an LC re-host moves
price and stop together, and a partial modify would leave a live order carrying the old stop).

### What Phase 4 does NOT verify

Nothing in `live/` has been executed. Every `order_send` call has been read, not run. `DRY_RUN=true`
exercises the full path including the plan and alerts what it would rest, and that is the first thing to
run on the VPS.

**Both Phase 3 findings are still open and are now live-facing:** LC-1 loses 0.451 R per trade and is
still enabled, and longs underperform shorts across every model. Recorded in `live/README.md` as well, so
they are visible from the directory that trades.

## Open items deliberately deferred

- **Per-tier order blocks** (decision 3), going into `roadmap/enhancements.md`. Today a zone triggered by
  both the fractal and swing tiers classifies as swing, so some genuine fractal setups will route to CE
  instead of LC-1. This one now has extra bite: since **LC-1 is banned on the swing tier**, a
  fractal-plus-swing zone loses LC-1 entirely rather than merely being scored differently.
- **The approach-leg running-minimum rule** (decision 13) is settled, but it is a chosen definition rather
  than a derived one. Worth revisiting once there are walk-forward results, in particular how often the
  factor answers YES. If it lands above roughly 80% or below roughly 20%, the leg is probably the wrong
  length and the anchor is the thing to adjust.
- **`m15_internal` n=5 is unverified.** Chosen by the user rather than derived, and Williams Fractal is
  trend-to-noise sensitive. A tuning candidate once there are results.
- **LC-2A before LC-2B.** A chosen default.
- **Sequential rather than joint threshold search.** A tractability choice.
- **Oversized OB rejection** (already in `roadmap/enhancements.md`) becomes more relevant now that the stop
  no longer comes from the zone.
- **Live pending orders**, Phase 4, after a clean walk-forward.
