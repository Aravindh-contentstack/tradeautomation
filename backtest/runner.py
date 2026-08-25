"""One instrument-year of the walk-forward backtest, end to end and in memory.

This is the loop that scripts/backtest_eurusd.py and scripts/backtest_multi.py
used to each carry their own byte-identical copy of (they differed only in
`PIP_SIZE` versus `pip_size`). Three separate changes in this round -- the
walk tail, the settings loop, and learning over all candidates -- would each
have had to be hand-applied twice, and the two copies would have drifted the
first time one of them was edited alone.

Deliberately PURE: no filesystem I/O, no path construction, no os.makedirs, no
save_weights. Path layout is the one thing that genuinely differs between the
two scripts (one hardcodes EUR_USD, the other nests per instrument), so the
scripts keep the paths and this module keeps the logic. It also means the
engine can be tested from hand-built OHLC frames with nothing on disk.

What the walk-forward actually is
---------------------------------
Two things are carried forward from year N-1 into year N, and they are carried
by the CALLER, not looked up here:

* Frozen weights. Every probability in year N is computed with year N-1's
  learned weight table, so the journalled probability is exactly what a live
  system would have traded on that day. No global pooling, no retroactive
  recompute. Alongside it a `learning` accumulator starts from that same frozen
  baseline and drifts +/-2% per outcome through the year; it is returned here
  and becomes year N+1's frozen baseline.

* Settings (threshold, max SL size, TP multiple). All three are carried, which
  is the user's explicit decision -- see backtest/settings.py for the
  out-of-sample evidence that argued against carrying the TP and was overruled.
  Year 2020 bootstraps with no prior file, and load_settings returns
  DEFAULT_SETTINGS in that case, so there is no special-casing anywhere.

Learning runs over ALL candidates, not just the taken ones. The factors were
evaluated on the rejected bars too and the market answered them either way, so
restricting learning to the taken subset would throw away roughly three
quarters of the evidence (~15 learning events a year instead of ~60), at which
point the +/-2% drift is mostly noise. It would also feed the ratchet described
in backtest/settings.py.

The walk tail
-------------
The walk frame spans [Jan 1 of the year, Jan 1 of the next year + walk_tail),
but only signals whose own bar falls INSIDE the year are traded. Without the
tail, a trade entered on 28 December ran out of bars before its Friday deadline
and was dropped: roughly 1 to 3 trades per instrument per year, always the
late-December ones, which is a real and directional end-of-year bias rather
than rounding. The tail is only affordable now that the walk is bounded by the
Friday deadline (~120 bars); against the old unbounded walk it would have been
an open-ended extension.
"""

import pandas as pd

from smc.liquidity.liq_state import slice_universe as slice_liquidity
from smc.order_blocks.ob_state import slice_universe

from backtest.context import build_market_context
from backtest.settings import apply_settings
from backtest.simulate import apply_tp, find_signals, simulate_trade
from backtest.journal import build_row
from backtest.target_log import collect_target_log, trade_id_for
from backtest.weights import update_weights

DEFAULT_WALK_TAIL = pd.Timedelta(days=7)


def _year_bounds(year):
    """[start, end) of the calendar year in UTC.

    UTC, not London. The journal reports London civil dates, but which YEAR a
    bar belongs to has always been decided in UTC here (`df["date"].dt.year`),
    and changing that would shift a handful of trades across every year
    boundary, making the pre-change journals in
    data/_backup_15factor_2020_2025/ undiffable for no gain.
    """
    start = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
    end = pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC")
    return start, end


def _window(frame, start, end):
    """Rows of a date-sorted frame inside [start, end). Returns None for an
    empty or missing frame so callers can pass it straight on as "unavailable".
    """
    if frame is None or len(frame) == 0:
        return None
    dates = pd.DatetimeIndex(frame["date"])
    if dates.tz is None:
        dates = dates.tz_localize("UTC")
    else:
        dates = dates.tz_convert("UTC")
    sliced = frame[(dates >= start) & (dates < end)]
    if len(sliced) == 0:
        return None
    return sliced.reset_index(drop=True)


def run_year(df, year, *, pip_size, frozen_weights, settings,
             m15_df=None, m15_bundle=None, tp_levels=None,
             walk_tail=DEFAULT_WALK_TAIL,
             obs=None, liq=None, log_targets=False, instrument=None):
    """Simulates and journals EVERY gate-passing candidate of `year`.

    df is the full instrument pipeline frame (backtest/pipeline.py). settings
    is what the prior year recommended, already loaded (see
    backtest/settings.py::load_settings); it decides `taken` and the TP
    multiple, and it is stamped onto every row as an audit trail.

    obs is the FULL-HISTORY ObUniverse from the same bundle as df, rebased
    here onto this year's walk window. It is computed once per instrument
    rather than per year, so it is passed in whole and sliced, never
    recomputed (see backtest/pipeline.py's docstring for why per-window
    computation would be wrong). liq is the LiquidityUniverse from that same
    bundle, under the identical contract and sliced by the same offset.

    Returns (learning_weights, rows). rows are journal-ready dicts, each still
    carrying "factor_results", which save_journal strips on the way to CSV.

    log_targets adds a third return value, the per-bar Target OB record
    (backtest/target_log.py). It is off by default: it changes no result
    and costs a factor evaluation per bar of every candidate's life.
    `instrument` only labels those rows and is unused otherwise.

    Untaken candidates are simulated and journalled exactly like taken ones.
    Only their `taken` flag differs, and only P&L reporting reads it. Filtering
    them out here is the ratchet trap (backtest/settings.py).
    """
    learning_weights = dict(frozen_weights)

    start, end = _year_bounds(year)
    tail = walk_tail if walk_tail is not None else pd.Timedelta(0)

    # The walk frame runs past year-end; the SIGNAL frame does not.
    walk_df = _window(df, start, end + tail)
    if walk_df is None:
        return learning_weights, []

    # The window's offset into full history comes from the explicit
    # h1_index column, never from re-deriving a position by timestamp:
    # walk_df has already been reset_index'ed and its own index says
    # nothing about where it sits in the instrument's history.
    window_obs = None
    window_liq = None
    if "h1_index" in walk_df.columns:
        window_start = int(walk_df["h1_index"].iloc[0])
        window_stop = window_start + len(walk_df)
        if obs is not None:
            window_obs = slice_universe(obs, window_start, window_stop)
        if liq is not None:
            window_liq = slice_liquidity(liq, window_start, window_stop)

    ctx = build_market_context(
        walk_df,
        pip_size,
        m15_df=_window(m15_df, start, end + tail),
        obs=window_obs,
        liq=window_liq,
        # Passed through WHOLE, unlike obs/liq/m15_df above, all three of
        # which are cut to the walk window. The bundle is indexed on its own
        # full M15 history and the entry models cross into it by timestamp,
        # so windowing it here would blind LC-1 to every level formed just
        # across a year boundary. See backtest/m15_pipeline.py.
        m15_bundle=m15_bundle,
    )

    # find_signals runs over the whole walk frame and the tail's signals are
    # dropped afterwards, rather than the frame being pre-trimmed, because a
    # signal's `idx` is a positional index into ctx. Trimming the frame first
    # would mis-address every bar. The cost is nil: find_signals only iterates
    # the handful of OB-mitigation bars, of which the tail holds a few.
    signals = [
        s for s in find_signals(
            ctx, frozen_weights, pip_size,
            htf_threshold=settings.get("htf_threshold"),
        )
        if s["entry_time"] < end
    ]
    apply_settings(signals, settings, pip_size)

    tp_multiple = settings.get("tp_multiple")
    if tp_levels is None:
        # Timestamping the applied level lets apply_tp report the real fill
        # bar instead of falling back to the terminal bar as an upper bound.
        levels = [tp_multiple] if tp_multiple is not None else []
    else:
        levels = list(tp_levels)

    rows = []
    target_rows = []
    for signal in signals:
        # The live recheck is trade MANAGEMENT: it only makes sense for a
        # trade actually placed. A rejected candidate is walked purely so
        # its real, un-managed outcome can teach the weight table
        # something (see the ratchet-trap note above); recomputing its
        # probability against this year's threshold would move nearly
        # every one of them to breakeven within its first bar (a rejected
        # candidate's score rarely climbs back above the very bar it just
        # failed), which is exactly the narrow, taken-trades-only sample
        # the "simulate every candidate" design exists to avoid.
        if signal["taken"]:
            recheck_kwargs = dict(
                weights=frozen_weights,
                # The recheck compares a FULL score (HTF plus entry plus
                # both target gates) so it has to use the total threshold,
                # not the legacy one. load_settings maps an old file's
                # `threshold` onto total_threshold, so this reads correctly
                # for every stored year too.
                threshold=settings.get("total_threshold"),
                mitigation_factor_results=signal["mitigation_factor_results"],
                # Needed to re-ask the M15 target gate, the one entry
                # factor that is not frozen at entry.
                entry_zone=signal.get("entry_zone"),
                entry_setup=signal.get("entry_setup"),
            )
        else:
            recheck_kwargs = {}

        walk = simulate_trade(
            ctx,
            signal["idx"],
            signal["direction"],
            signal["entry_price"],
            signal["sl"],
            signal["r_distance"],
            tp_levels=levels,
            **recheck_kwargs,
        )
        tp_result = apply_tp(
            walk,
            tp_multiple,
            signal["entry_price"],
            signal["direction"],
            signal["r_distance"],
        )

        row = build_row(signal, walk, tp_result, settings)
        row["factor_results"] = signal["factor_results"]
        rows.append(row)

        if log_targets:
            target_rows.extend(
                collect_target_log(
                    ctx, signal, walk, trade_id_for(instrument or "", signal)
                )
            )

        # ALL candidates, taken or not. update_weights leaves the table
        # completely untouched on a 0R breakeven (user decision), so no branch
        # is needed here.
        update_weights(
            learning_weights, signal["factor_results"], tp_result["realised_r"]
        )

    if log_targets:
        return learning_weights, rows, target_rows
    return learning_weights, rows


def realised_r_at(row, tp_multiple):
    """What a journal row would have returned at some OTHER TP multiple.

    The same exact projection as backtest/analysis.py::_realised_r, restated
    here so the runner does not import a private name. It is exact rather than
    approximate because the walk carries no TP at all and `max_r_reached`
    excludes the terminal bar -- see backtest/simulate.py's module docstring.
    """
    if tp_multiple is None:
        return row["terminal_r"]
    if row["max_r_reached"] >= tp_multiple:
        return float(tp_multiple)
    return row["terminal_r"]


def summarise(rows, tp_multiple):
    """Total R and strike rate over the TAKEN rows at one TP multiple.

    Breakevens are excluded from the strike-rate denominator, matching
    analysis.py::_score. A 0R exit resolved nothing; counting it as a loss
    would let the 19:00 breakeven rule collapse the reported strike rate on
    flat ROI.
    """
    taken = [r for r in rows if r.get("taken", True)]
    total_r = 0.0
    wins = 0
    losses = 0
    breakevens = 0
    for row in taken:
        r = realised_r_at(row, tp_multiple)
        total_r += r
        if r > 0:
            wins += 1
        elif r < 0:
            losses += 1
        else:
            breakevens += 1

    decided = wins + losses
    return {
        "trade_count": len(taken),
        "total_r": total_r,
        "strike_rate": wins / decided if decided else 0.0,
        "wins": wins,
        "losses": losses,
        "breakeven_count": breakevens,
        "avg_r": total_r / len(taken) if taken else 0.0,
    }
