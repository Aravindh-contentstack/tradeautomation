"""Guards the single most important structural claim in the engine: that
apply_tp's post-hoc projection

    realised_r = tp_multiple  if max_r_reached >= tp_multiple  else terminal_r

exactly matches what you would get from re-simulating with the TP baked
into the walk. That equivalence is what lets backtest/analysis.py grid-
search six TP multiples per candidate without re-walking a single bar. If
a future change makes any management rule (stop, breakeven trigger, 19:00
cut, Friday close) depend on the TP, this file is what would catch it --
nothing else in the suite checks this property.

`_brute_force_realised_r` below is a SEPARATE walk, not a call into
simulate_trade. It re-derives the checkpoint/deadline schedule itself
(using the killzone helpers, which are shared infrastructure, not the
property under test) and checks the take-profit price on every bar as it
goes, stopping the instant either the TP or a management rule fires. Its
only borrowed convention is "a bar that touches both the stop and the TP
resolves toward the stop", which is the same pessimistic tie-break
simulate_trade itself uses when M15 cannot resolve the bar -- see
test_max_r_attribution.py's AMBIGUOUS_BARS. None of the eight paths below
rely on that tie-break to get their expected answer (only PATH_8 hits it
at all, and it hits it deliberately, as the one path that checks the
brute force agrees with the pessimistic convention rather than assuming
it away).
"""

import pytest

from backtest.analysis import TP_MULTIPLE_GRID
from backtest.killzone import friday_cutoff_for, london_cutoff_for, next_london_cutoff
from backtest.simulate import apply_tp, simulate_trade
from conftest import DIRECTION, ENTRY, R_DISTANCE, SL, ctx_for

FLAT = (1.1000, 1.1002, 1.0998, 1.1000)


def _naive(ts):
    import numpy as np

    return np.datetime64(ts.tz_convert("UTC").tz_localize(None))


def _brute_force_realised_r(bars, tp_multiple):
    """An independent re-simulation with the TP checked on every bar,
    rather than projected afterwards. Bullish-only (matches every fixture
    in this suite): sign is always +1.
    """
    import pandas as pd

    ctx = ctx_for(bars)
    entry_price, stop = ENTRY, SL
    tp_price = ENTRY + tp_multiple * R_DISTANCE
    be_moved = False

    entry_ts = pd.Timestamp(ctx.ts[0], tz="UTC")
    friday_cutoff = _naive(friday_cutoff_for(entry_ts))
    checkpoint = london_cutoff_for(entry_ts)
    next_checkpoint = _naive(checkpoint)
    while next_checkpoint <= ctx.ts[0]:
        checkpoint = next_london_cutoff(checkpoint)
        next_checkpoint = _naive(checkpoint)

    n = len(ctx.ts)
    for k in range(1, n):
        t = ctx.ts[k]
        o, hi, lo = ctx.open_[k], ctx.high[k], ctx.low[k]

        if t >= friday_cutoff or ctx.cutoff_ts[k] > friday_cutoff:
            return (o - entry_price) / R_DISTANCE

        if t >= next_checkpoint:
            r_open = (o - entry_price) / R_DISTANCE
            if r_open > 0.0:
                if not be_moved:
                    stop = entry_price
                    be_moved = True
            else:
                return r_open
            checkpoint = next_london_cutoff(checkpoint)
            next_checkpoint = _naive(checkpoint)
            while next_checkpoint <= t:
                checkpoint = next_london_cutoff(checkpoint)
                next_checkpoint = _naive(checkpoint)

        # Stop checked before TP on the same bar: the shared pessimistic
        # tie-break (see module docstring).
        if lo <= stop:
            return 0.0 if be_moved else -1.0
        if hi >= tp_price:
            return tp_multiple

    return (ctx.close[n - 1] - entry_price) / R_DISTANCE


def _via_apply_tp(bars, tp_multiple):
    ctx = ctx_for(bars)
    walk = simulate_trade(ctx, 0, DIRECTION, ENTRY, SL, R_DISTANCE, tp_levels=[tp_multiple])
    return apply_tp(walk, tp_multiple, ENTRY, DIRECTION, R_DISTANCE)["realised_r"]


PATHS = {
    # Clean win, single day, well clear of any checkpoint: the running max
    # simply climbs through several grid levels.
    "clean_win": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [("2024-01-08T09:00:00Z", 1.1000, 1.1030, 1.0998, 1.1025)]  # +1.5R
        + [("2024-01-08T10:00:00Z", 1.1025, 1.1100, 1.1020, 1.1090)]  # +5.0R
    ),
    # Clean loss, stop touched immediately, no favourable move worth naming.
    "clean_loss": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [("2024-01-08T09:00:00Z", 1.1000, 1.1001, 1.0975, 1.0980)]
    ),
    # Breakeven stop: profit to BE at the 19:00 checkpoint, then a reversal
    # back through entry. Peak favourable excursion (~0.4R) stays below
    # every grid value, so every TP falls back to the 0R terminal.
    "breakeven_stop": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [(f"2024-01-08T{h:02d}:00:00Z", *FLAT) for h in range(9, 19)]
        + [("2024-01-08T19:00:00Z", 1.1006, 1.1008, 1.1004, 1.1006)]  # +0.3R -> BE
        + [("2024-01-08T20:00:00Z", 1.1005, 1.1006, 1.0995, 1.1000)]  # back through entry
    ),
    # 19:00 loss cut, no favourable excursion worth naming: every TP falls
    # back to the same fractional negative terminal.
    "cut_19h": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [(f"2024-01-08T{h:02d}:00:00Z", *FLAT) for h in range(9, 19)]
        + [("2024-01-08T19:00:00Z", 1.0992, 1.0995, 1.0990, 1.0992)]  # -0.4R
    ),
    # Friday deadline win. The deadline bar closes at the bar's OPEN
    # (favourable/high is never credited for a deadline bar), so max_r
    # never reaches the grid and every TP falls back to the +1.5R terminal.
    "friday_win": (
        [("2024-01-12T08:00:00Z", *FLAT)]
        + [(f"2024-01-12T{h:02d}:00:00Z", *FLAT) for h in range(9, 19)]
        + [("2024-01-12T19:00:00Z", 1.1030, 1.1032, 1.1028, 1.1030)]  # +1.5R
    ),
    # Friday deadline loss, same reasoning, negative side.
    "friday_loss": (
        [("2024-01-12T08:00:00Z", *FLAT)]
        + [(f"2024-01-12T{h:02d}:00:00Z", *FLAT) for h in range(9, 19)]
        + [("2024-01-12T19:00:00Z", 1.0990, 1.0992, 1.0988, 1.0990)]  # -0.5R
    ),
    # Multi-day win: BE at Monday's checkpoint, then a Tuesday spike to
    # +3.2R that the walk never closes on (data simply ends). Some grid
    # values are covered by max_r (3.2), the top of the grid is not, and
    # falls back to the +3.0R data-end terminal.
    "multi_day_win_data_end": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [(f"2024-01-08T{h:02d}:00:00Z", *FLAT) for h in range(9, 19)]
        + [("2024-01-08T19:00:00Z", 1.1006, 1.1008, 1.1004, 1.1006)]  # +0.3R -> BE
        + [("2024-01-08T20:00:00Z", 1.1006, 1.1010, 1.1005, 1.1008)]
        + [("2024-01-09T10:00:00Z", 1.1010, 1.1030, 1.1005, 1.1025)]  # +1.5R, no stop risk
        + [("2024-01-09T11:00:00Z", 1.1025, 1.1064, 1.1020, 1.1060)]  # +3.2R, data ends here
    ),
    # Ambiguous terminal bar: a same-bar spike AND stop touch, no M15. The
    # pessimistic tie-break means the spike is never credited, so every TP
    # falls back to the clean -1R the SL always gives on this bar.
    "ambiguous_terminal_bar": (
        [("2024-01-08T08:00:00Z", *FLAT)]
        + [("2024-01-08T09:00:00Z", 1.1000, 1.1090, 1.0975, 1.0985)]  # +4.5R and the stop
    ),
}


@pytest.mark.parametrize("path_name", sorted(PATHS))
@pytest.mark.parametrize("tp_multiple", TP_MULTIPLE_GRID)
def test_apply_tp_matches_an_independent_resimulation(path_name, tp_multiple):
    bars = PATHS[path_name]
    expected = _brute_force_realised_r(bars, tp_multiple)
    actual = _via_apply_tp(bars, tp_multiple)
    assert actual == pytest.approx(expected), (
        f"{path_name} at TP={tp_multiple}: apply_tp said {actual}, "
        f"independent resimulation said {expected}"
    )
