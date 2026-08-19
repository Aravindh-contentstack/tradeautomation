## Liquidity (CONFIRMED and IMPLEMENTED 2026-08-19)

Five detectors under `smc/liquidity/`, reconciled onto the H1 timeline by
`smc/liquidity/liq_state.py`, and scored through two new gates in
`backtest/factors.py`.

### The detectors

| Concept | Module | Rule |
|---|---|---|
| Equals, Old Points | `levels.py` | Fractal pivots at n=2, pooled when within 0.25x ATR(14). Touched once is an old point, twice or more is an equals. |
| LRLQ | `low_resistance.py` | 3+ consecutive stepping pivots at n=1, all within 30 candles. The level is the FIRST pivot of the run. |
| FVG | `fair_value_gaps.py` | Unchanged. Already carried the 50% fill rule and the 100-candle expiry. |
| Previous day/week | `time_levels.py` | From the stored Daily candles (00:00 UTC), weeks grouped Monday to Friday. |
| Asian/London/NY | `time_levels.py` | London civil time, from `backtest/killzone.py`'s `SESSION_HOURS`. Live through the end of the FOLLOWING London day, so "previous day London high" is readable. |

Everything ages on the same 100-candle lookback that `order_blocks.py` and
`fair_value_gaps.py` use, on every timeframe.

`levels.py` emits one row per level VERSION rather than per level, because a
pool's price, band, touch count and classification all change when another
pivot joins it. One row per pool would mean a row whose `kind` was decided by
touches that had not happened yet.

### The two gates

**Swept Liquidity** (`{daily,h4}_swept_liquidity_*`). Daily and 4H only.
What the most recently CLOSED candle of that timeframe took: a sweep two
candles back scores nothing. Only a sweep of the OPPOSITE side supports the
trade, since a long wants the sell stops under the market run. Same roll-up
convention as the OB gates: the kinds actually swept are emitted as yes and
the parent is dropped, or the parent alone answers no. Frozen at entry.

**There is no H1 Swept Liquidity gate.** Confirmed with the user, and it
corrects both `factors/*.csv` and this file's earlier draft: an H1 sweep that
does not break structure produces no order block, so there is nothing to
trade from. Every H1 sweep therefore lives on `h1_mitigation_ob_swept_
liquidity` and `h1_ob_target_swept_liquidity` as an extra child, including
the five time-based kinds.

**Liquidity Target** (`{daily,h4,h1}_liquidity_target_*`). All three
timeframes, dynamic, re-evaluated on every bar of an open trade. Emits yes
for every kind with an untaken level in the direction of travel within 5R
(7.5R for previous week, which is a bigger draw). Structural strong points
are deliberately absent: only Old Points can be a target, since a swing,
internal or fractal point is expected to HOLD rather than to be run.

A target price has covered is OMITTED, not negated. That is the user's
explicit rule and it is where this gate differs from OB Target, which flips
from supporting to opposing on arrival.

**Consequence worth knowing: Liquidity Target never answers no.** With no
parent roll-up and omission for both "covered" and "never there", the gate
can only raise a setup's score. Measured on EUR_USD 2025, both new gates
together add a mean of 7.7 factors per candidate (range 2 to 16) against a
base of about 27, and lift mean probability by about 9 points. The variation
is the signal, not the level, and the yearly threshold search re-centres on
the new distribution. If that turns out to be too blunt, the fix is a parent
that answers no when no liquidity of any kind is in range, mirroring the
swept side.

### Base rates worth remembering

Structural sweeps are RARE, because they need a tier's own strong point
wicked while that tier's structure stays put. On EUR_USD Daily: fractal
2.1% of candles, internal 0.5%, swing 0.2%. After the last-closed-candle
rule and the side match, `daily_swept_liquidity_swing` fires perhaps once
every few years. That is not a bug, it is a high-conviction rare event.

### Next items

- Validate the detectors against a real chart. `scripts/demo_liquidity_
  levels.py`, `demo_time_levels.py` and `demo_low_resistance.py` print real
  levels with the dates that formed them, for exactly this.
- LRLQ's rule admits runs whose steps are hundreds of pips apart, which are
  trending legs rather than the compressed grind the concept describes. The
  user chose the simple rule knowingly, so revisit only after eyeballing the
  demo output on a chart.
- NWOG (new week opening gap) still has no detector. It stays in
  `factors/*.csv` as a row that never scores.
- `LRLQ` and `Equals` on M15 for the Entry tier's `M15 Target Liquidity`
  sub-path. Both detectors are timeframe-agnostic and will serve it, but
  wiring the Entry models is separate work (`roadmap/entry-models.md`).
