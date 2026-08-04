## Liquidity Sweep

Shares its core logic with `roadmap/supply-and-demand.md`'s Swept Liquidity
sub-factors (Stage 3 there): structural liquidity types (`swing`, `internal`,
`fractal`, `old_points`) plus `fvg`, `equals`, and `previousCandle H/L`. Build
this once against the OB tables from `swing_structure/order_blocks.py` and
reuse it for both the Mitigation OB and OB Target gates, rather than
duplicating the sweep-detection logic per gate.

H1 also has liquidity types that are not OB-derived at all, and need their own
detection separate from the OB work:

- `Asian Liquidity` (H1 only)
- `PDH/PDL` (previous day high/low, H1 only)
- `NWOG` (new week opening gap, H1 only, `Liquidity Target` gate)
- `LRLQ` (Entry-tier `M15 Target Liquidity` sub-path)

These are flagged as separate future stages since they're timeframe-specific
levels, not properties of an identified order block.

## Target Liquidity

Same sub-factors as Liquidity Sweep (`Equals`, `Low Resistance Liquidity`,
`FVG Liquidity`, `Structural Liquidity` swing/internal/fractal/old_points),
plus the H1-specific additions above (`Asian Liquidity`, `PDH/PDL`, `NWOG`).
Selection of which liquidity level becomes "the" target for a given signal is
an Entry-model concern, same as OB Target's directional selection, and is
deferred to when the Entry-model factors (`roadmap/entry-models.md`) are
designed.

### Next Items

- Land OB identification (`swing_structure/order_blocks.py`) first, since the
  structural-liquidity sub-factors here read directly off the OB tables it
  produces.
- Design the H1-only levels (Asian Liquidity, PDH/PDL, NWOG, LRLQ) as their own
  small detectors once OB-derived sweep detection is validated.
