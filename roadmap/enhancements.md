Deferred enhancements. Ideas confirmed as wanted, scoped out of the work
that raised them, and parked here so the reasoning is not lost between
sessions. Each says what it is, why it was deferred, and what has to be
decided before it can be built.

## Re-entry inside the same H1 OB (noted 2026-08-13)

When the H1 Mitigation OB fails, price has by definition already run past
the stop, so that trade is a loss. But the zone itself may not be finished:
a large OB can be tapped shallowly, fail its first entry, and still hold
enough resting orders to react from a deeper level inside the same zone.

A second entry model may be sought within the same H1 OB when ALL of:

- the OB is large,
- price has not mitigated it deeply, with at least 33% of the zone
  unmitigated,
- an M15 OB sits inside the H1 OB and has not itself been mitigated.

Deferred because the third condition needs M15 order blocks, which do not
exist yet, and because the whole thing belongs with the entry-model design
rather than with OB identification.

**Open before building:** what counts as "large" (the zone-shaping bands in
`roadmap/supply-and-demand.md` use 0.5x and 1x ATR, and this may or may
not share them), whether the 33% is measured from the near edge or against
the EQ, and how a re-entry interacts with the three-touch invalidation
counter (a re-entry is by construction a second touch).

## Acting on a decaying trade score (noted 2026-08-13)

The Target OB flip from supporting to opposing is computed on every bar of
an open trade, but currently only recorded (`data/target_log/`, see
`backtest/target_log.py`). Nothing acts on it.

The intended responses, in rough order of severity as the number falls:
move the stop to breakeven, take partials, cancel the trade outright.

Deferred deliberately so the mechanic could be landed without moving any
existing P&L. Which response fires at which drop is a separate design
question, and it cannot be answered from first principles: it needs the
recorded data to show how often and how sharply the score actually moves
over a trade's life.

**Open before building:** the thresholds themselves, whether they are
absolute probability levels or drops relative to the entry score, and
whether partials need position-sizing support that
`live/risk.py` does not currently have.

## Tier-level OBs for M15 sniper entries (noted 2026-08-13)

One of the entry factors is whether price has mitigated the FRACTAL-tier OB
specifically, which needs OBs resolved per tier rather than per timeframe.
Everything today is per timeframe, with the triggering tier kept only as
metadata (`trigger_tier`, `primary_tier`).

Deferred to the entry-model work, where the M15 zoom-in lives. Outside of
M15 entries the tier does not matter, so building it now would add a
dimension nothing reads.

## Reject oversized OBs at entry, not just post-hoc (noted 2026-08-16)

Confirmed from the EUR_USD 2020 backtest: the OB mitigated on 24 March 2020
was 344 pips wide (1.08858 to 1.09202), against a normal H1 order block of
maybe 20-50 pips. The stop, 2 pips beyond the zone's far edge, inherited
that width and became a roughly 1000-pip risk. Checking the raw H1 data
confirmed why: that week's average H1 candle moved 42 pips against a 18-pip
yearly average, with one candle moving 119 pips in a single hour (the peak
week of the March 2020 COVID/dash-for-cash spike). An order block is built
from an anchor candle, so an abnormally large candle produces an
abnormally large zone, and the fixed "2 pips beyond the edge" stop rule has
no ceiling on how far that edge is allowed to sit.

`entry_ob.py` already rejects zones that are too NARROW (`MIN_R_PIPS`,
guarding against noise inflating the R multiple). No equivalent ceiling
exists for zones that are too WIDE. Today the only lever against this is
`max_sl_size_pips` in the walk-forward settings search, but that is a
post-hoc filter applied to a whole year's pool after the fact, not a
rejection at the point the candidate is generated, so a trade like the 24
March one is journalled, simulated, and can still be taken if that year's
settings happen not to cap SL size (as they did not in the runs so far,
where `max_sl_size_pips` has been left at `None`).

Deferred rather than fixed immediately because the right bound is a
judgment call: an ATR-relative cap would correctly scale down for calmer
instruments/years, while a flat pip cap is simpler to reason about and
matches how `max_sl_size_pips` already works everywhere else.

**Open before building:** whether the cap is a flat pip ceiling (mirroring
`max_sl_size_pips`) or ATR-relative (mirroring the zone-shaping bands in
`roadmap/supply-and-demand.md`), what the actual number should be, and
whether it belongs in `build_setup` (reject the candidate outright,
alongside `MIN_R_PIPS`) or as a new scored factor instead of a hard
rejection.

## Assumptions inside the H1 mitigation-leg swept gate (noted 2026-08-20)

Full design and rationale:
`~/.claude/plans/assume-there-is-a-logical-ripple.md`. The gate itself is
built (`smc/liquidity/sweep_credit.py`, and see `roadmap/liquidity.md`). What
follows are the five places its rules were CHOSEN rather than derived, each
recorded so a later change is a decision rather than a rediscovery.

**ATR is frozen at the sweep candle S.** The `3 x ATR(14)` spend test reads
the ATR at S and holds it for the life of the chain. Reading each bar's own
ATR instead would make the threshold breathe, so a credit could die purely
because volatility rose. Freezing it is also what lets "whichever kill
condition fires first, permanently" be a structural guarantee: the two
conditions become one contiguous band on the close, answered by a single
forward scan, rather than two pieces of bookkeeping that have to agree.

**The 3x ATR anchor never resets.** For a bullish OB the reference stays at
`high[S]` even if a later candle inside the band prints a higher high. This
follows the rule as stated and only makes the spend condition harder to reach,
so it errs conservative, but a resetting anchor is defensible and untested.

**The old-point external-to-swing-range filter is kept.** This gate reuses
`smc/liquidity/sweeps.py`'s `external` predicate, so "old point" means the
same thing here as on the Mitigation OB and Daily/4H gates. Dropping it would
admit more credits but make the three gates' `old_points` answers
incomparable with each other.

**A previous-day low can score twice, deliberately.** One swept during
formation AND still credited at mitigation emits both
`h1_mitigation_ob_swept_liquidity_previous_day` and
`h1_mitigation_leg_swept_liquidity_previous_day`. They are different facts
about different legs, weeks apart, and the weights learn them separately, but
it is a real correlation between two factors rather than two independent
signals. Worth knowing before reading either weight.

**The close-through rule is the tightest constraint in the design.** Credit
survives only if no candle from S through the entry bar closes beyond the
swept level, and the entry candle is included. That forces the zone to sit
within roughly one H1 wick's reach of the level, so the gate is a rare
high-conviction signal rather than a common one.

**Measured fire rate, EUR_USD 2021 to 2025:** 71 of 660 candidates carry at
least one child yes, or 10.8%, ranging from 6.6% in 2023 to 16.5% in 2025.
That is the number the close-through worry above was about, and it settles it:
the gate discriminates rather than firing on everything or nothing, so no
loosening is called for. Per child, as a share of all candidates: london 5.3%,
asian 3.9%, previous day 2.0%, LRLQ 1.8%, equals 1.1%, NY 0.6%, old points
0.6%.

**Two children have no evidence yet.** `previous_week` never fired once in
five years, and `old_points` fired four times. Both are real rules rather than
dead code, but their learned weights will stay near their 1.0 seed for a long
while, so treat either weight as unproven rather than as a finding. If
`previous_week` is still at zero after another instrument, the question worth
asking is whether a weekly level is simply never within one H1 wick of an
order block, which would make it structurally unreachable for this gate
rather than merely rare.

## Lookback period for untouched OBs (still open, noted 2026-08-04)

Carried over from `roadmap/supply-and-demand.md`. Because the structure-break
invalidation rule explicitly does NOT kill an untouched OB, zones that price
never came back to remain technically valid forever. Under the OB-target
sweep this now has a visible cost: an ancient untouched zone can be selected
as the nearest valid target.

**Open before building:** the bound itself, and whether it is measured in
bars, in ATR-relative distance, or against the size of the leg that
produced the zone.
