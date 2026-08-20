"""Loads one instrument's Daily/H4/H1 history, runs the existing
smc detectors (structure, premium/discount, order blocks and their quality
factors) on each, and merges Daily and H4 state onto the H1 timeline for
the backtest engine.

Reuses smc/market_structure/*.py, smc/order_blocks/*.py and
smc/liquidity/*.py as-is: this module only loads data, sequences those
detectors, and reconciles their output onto one timeline. It does not
reimplement any structure, zone, or OB logic.

Three products come out, and they are different shapes on purpose. The
merged DataFrame is one row per H1 candle, which suits scalar
structure/zone state. Order blocks are not scalar per candle (several are
live and nested at once), so they stay long-format and are reconciled onto
the H1 index by smc/order_blocks/ob_state.py instead of being flattened
into columns. Liquidity levels have the same problem and get the same
treatment from smc/liquidity/liq_state.py. PipelineBundle carries all three.

Liquidity is built BEFORE the order blocks here, not after, because the OB
quality stack reads its per-candle sweep arrays: whether a candle took out
an old point is a fact about that candle, and both the zone's formation leg
and the standalone Swept Liquidity gate ask the same question of it.

Everything here runs ONCE PER INSTRUMENT over full history, never per
backtest year. An OB formed in December and mitigated in January belongs
to both years and to neither year's table if computed per window. Full
history is safe because ob_state stores per-OB index thresholds rather
than lifetime flags, so windowing cannot reveal a zone early.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.context import build_market_context
from smc.liquidity import liq_state, sweep_credit, sweeps
from smc.liquidity.fair_value_gaps import compute_fair_value_gaps
from smc.liquidity.levels import compute_liquidity_levels
from smc.liquidity.low_resistance import compute_low_resistance_liquidity
from smc.liquidity.time_levels import (
    compute_previous_day_levels,
    compute_previous_week_levels,
    compute_session_levels,
)
from smc.market_structure.daily_structure import compute_daily_structures
from smc.market_structure.h4_structure import compute_h4_structures
from smc.market_structure.h1_structure import compute_h1_structures
from smc.market_structure.premium_discount import (
    compute_daily_premium_discount,
    compute_h4_premium_discount,
    compute_h1_premium_discount,
)
from smc.order_blocks import ob_state
from smc.order_blocks.order_blocks import (
    DAILY_TIER_PREFIXES,
    H1_TIER_PREFIXES,
    H4_TIER_PREFIXES,
    compute_daily_order_blocks,
    compute_h1_order_blocks,
    compute_h4_order_blocks,
)
from smc.order_blocks.order_block_quality import (
    apply_candle_sweeps,
    compute_containment,
    compute_flip_zone,
    compute_fvg_confluence,
    compute_inducement,
    compute_previous_candle_sweep,
)

DAILY_DURATION = pd.Timedelta(days=1)
H4_DURATION = pd.Timedelta(hours=4)

# How many H1 bars of the merged frame the live context keeps. Only the
# newest closed candle can produce an order, so this is generous on
# purpose: it costs nothing and leaves room for a deferred entry, whose
# trigger bar sits before the bar it enters on.
LIVE_CONTEXT_BARS = 200


# Which tier's range decides whether an old point counts as "external", per
# timeframe. Always that timeframe's own swing tier.
SWING_PREFIX = {"Daily": "daily_swing", "4H": "h4_swing", "H1": "h1_swing"}

# Which time-based levels each timeframe is entitled to sweep. Daily owns
# the week, 4H owns the day (the user's "sweep of previous day high means it
# should sweep and completely print", checked on whichever 4H candle last
# closed). H1 owns everything, because every H1 sweep is OB-anchored and the
# session levels only mean anything inside an H1 trade.
SWEPT_TIME_KINDS = {
    "Daily": ["previous_week"],
    "4H": ["previous_day"],
    "H1": ["asian", "london", "ny", "previous_day", "previous_week"],
}

# The extra swept-liquidity columns each timeframe's OB table carries, on
# top of the structural three and the two that predate this work
# (swept_liquidity_fvg, swept_liquidity_previous_candle). "fvg" is
# deliberately absent: compute_fvg_confluence already owns that column and
# applies a stricter test than a bare zone touch. Its own staleness
# bookkeeping (swept_liquidity_fvg_stale_from_index) travels with it
# automatically, needing no entry here.
OB_SWEEP_KINDS = {
    "Daily": ["old_point", "equals", "lrlq"],
    "4H": ["old_point", "equals", "lrlq"],
    "H1": ["old_point", "equals", "lrlq"] + SWEPT_TIME_KINDS["H1"],
}

# Time-based levels that can be a TARGET, which is H1 only. Unlike the sweep
# side, where each timeframe owns its own clock level, every time-based
# target is hunted inside an H1 trade.
TARGET_TIME_KINDS = {
    "Daily": [],
    "4H": [],
    "H1": ["asian", "london", "ny", "previous_day", "previous_week"],
}


@dataclass(frozen=True, eq=False)
class PipelineBundle:
    """The merged per-candle frame, the OB universe on its index, and the
    liquidity universe on the same index."""

    df: object
    obs: object
    liq: object = None


def _load_raw(instrument, granularity):
    path = "data/raw/%s_%s.parquet" % (instrument, granularity)
    return pd.read_parquet(path)


def build_live_context(daily_df, h4_df, h1_df, pip_size, tail=LIVE_CONTEXT_BARS):
    """A MarketContext over the last `tail` H1 bars, for the live bot.

    The live loop only cares about the newest closed candle, but it cannot
    simply slice the merged frame and hand that to find_signals: OB
    positions address the FULL history, so a post-hoc slice would
    mis-address every zone. The universe has to be rebased with the same
    offset the frame was cut at, which is what this does.

    `tail` has to comfortably exceed 1. A mitigation in the hour before a
    session defers its entry to the next candle, so the trigger bar and
    the entry bar are different rows and both must be inside the window.
    """
    bundle = build_pipeline_bundle(daily_df, h4_df, h1_df)
    total = len(bundle.df)
    start = max(total - tail, 0)

    window = bundle.df.iloc[start:total].reset_index(drop=True)
    return build_market_context(
        window,
        pip_size,
        obs=ob_state.slice_universe(bundle.obs, start, total),
        liq=liq_state.slice_universe(bundle.liq, start, total),
    )


def _liquidity_tables(structured_df, timeframe, tier_prefixes, time_levels):
    """One timeframe's liquidity level tables, plus its per-candle sweeps.

    The sweep arrays are built here rather than inside each detector
    because two very different consumers need the same answer: the OB
    quality stack asks it over a formation leg, and the standalone Swept
    Liquidity gate asks it about the last closed candle. Computing it once
    per timeframe means they cannot disagree.

    Structural sweeps are in the same dict as the rest. The standalone gate
    needs them here: without this, daily_swept_liquidity_swing and its five
    siblings can never fire, because nothing else ever puts a ("swing",
    side) key into the universe.
    """
    levels = compute_liquidity_levels(structured_df)
    lrlq = compute_low_resistance_liquidity(structured_df)
    fvgs = compute_fair_value_gaps(structured_df)
    length = len(structured_df)

    groups = [
        sweeps.structural_sweeps(structured_df, tier_prefixes),
        sweeps.pooled_level_sweeps(levels, structured_df, SWING_PREFIX[timeframe]),
        sweeps.lrlq_sweeps(lrlq, length),
        sweeps.fvg_sweeps(fvgs, structured_df),
    ]
    for kind in SWEPT_TIME_KINDS[timeframe]:
        groups.append(sweeps.time_level_sweeps(time_levels, kind, structured_df))

    return {
        "levels": levels,
        "lrlq": lrlq,
        "fvgs": fvgs,
        "per_candle": sweeps.merge(*groups),
    }


def _order_blocks_for(structured_df, timeframe, tier_prefixes, liquidity):
    """One timeframe's OB table with the full quality stack applied.

    Order matters: identification first, then the per-OB confluence
    columns, and containment last (in build_pipeline_bundle) because it
    needs both timeframes' finished tables.
    """
    compute = {
        "Daily": compute_daily_order_blocks,
        "4H": compute_h4_order_blocks,
        "H1": compute_h1_order_blocks,
    }[timeframe]

    obs = compute(structured_df)
    # One call, because liquidity["per_candle"] already carries the
    # structural keys alongside the rest. Going through
    # order_block_quality.compute_swept_liquidity_structural here as well
    # would recompute the same arrays a second time to reach the same
    # columns.
    kinds = [prefix.rsplit("_", 1)[-1] for prefix in tier_prefixes]
    obs = apply_candle_sweeps(
        obs, liquidity["per_candle"], kinds + OB_SWEEP_KINDS[timeframe]
    )
    obs = compute_fvg_confluence(obs, liquidity["fvgs"], structured_df)
    obs = compute_previous_candle_sweep(obs, structured_df)
    obs = compute_inducement(obs)
    obs = compute_flip_zone(obs)
    return obs


def _liquidity_universe(by_timeframe, time_levels, frames, merged, h1_ts, closes):
    """Every timeframe's levels reconciled onto the H1 timeline.

    frames maps a timeframe to its own structured frame, which is where the
    date columns the index conversion needs come from. `merged` is the
    H1-indexed frame, read only for each tier's swing range: old-point
    targets have to sit outside it, and the merged frame is the only place
    that range is already aligned to H1 without lookahead.
    """
    series = {}
    swept = {}
    n = len(h1_ts)

    for timeframe, liquidity in by_timeframe.items():
        dates = frames[timeframe]["date"]

        series[(timeframe, "equals")] = liq_state.indexed_series(
            liquidity["levels"][liquidity["levels"]["kind"] == "equals"],
            "equals", timeframe, dates, h1_ts, top="level_top", bottom="level_bot",
        )
        series[(timeframe, "old_point")] = liq_state.indexed_series(
            liquidity["levels"][liquidity["levels"]["kind"] == "old_point"],
            "old_point", timeframe, dates, h1_ts, top="level_top", bottom="level_bot",
        )
        series[(timeframe, "lrlq")] = liq_state.indexed_series(
            liquidity["lrlq"], "lrlq", timeframe, dates, h1_ts,
        )
        series[(timeframe, "fvg")] = liq_state.fvg_series(
            liquidity["fvgs"], timeframe, dates, h1_ts,
        )
        for kind in TARGET_TIME_KINDS[timeframe]:
            # H1 extremes, so a session or daily level stops being a target
            # on the bar that takes it. These levels carry no sweep state of
            # their own, being facts about a clock rather than about any
            # timeframe's candles.
            series[(timeframe, kind)] = liq_state.time_series(
                time_levels, kind, timeframe, h1_ts,
                highs=merged["high"].to_numpy(dtype=float),
                lows=merged["low"].to_numpy(dtype=float),
            )

        # The sweep answers were computed on this timeframe's candles, so
        # they are carried across by "which of its candles had closed by H1
        # bar k", never by a positional join.
        last_closed = liq_state._last_closed_candle(dates, h1_ts, timeframe)
        for (kind, side), per_candle in liquidity["per_candle"].items():
            swept[(timeframe, kind, side)] = liq_state.carry_to_h1(
                per_candle, last_closed, n
            )

    # merge_asof already put these on the H1 timeline without lookahead,
    # so reading them here inherits that guarantee rather than restating it.
    swing_ranges = {
        timeframe: (
            pd.to_numeric(merged["%s_swing_high" % SWING_PREFIX[timeframe]],
                          errors="coerce").to_numpy(dtype=float),
            pd.to_numeric(merged["%s_swing_low" % SWING_PREFIX[timeframe]],
                          errors="coerce").to_numpy(dtype=float),
        )
        for timeframe in by_timeframe
    }

    # H1 only, and H1 IS the base timeline: merge_asof preserves the left
    # frame row for row, so the H1 detectors' own row indices already ARE
    # universe indices and no conversion belongs here. That is also why this
    # is not carried through carry_to_h1 like the arrays above.
    mitigation_credit = sweep_credit.build_mitigation_leg_credit(
        frames["H1"],
        by_timeframe["H1"]["levels"],
        by_timeframe["H1"]["lrlq"],
        time_levels,
        swing_prefix=SWING_PREFIX["H1"],
    )

    return liq_state.build_liquidity_universe(
        series, swept, closes, swing_ranges, mitigation_credit=mitigation_credit
    )


def build_instrument_pipeline(instrument):
    """Returns one H1-indexed DataFrame carrying every Daily/H4/H1 factor
    column the strategy needs, with Daily/H4 state merged in without
    lookahead (only fully-closed higher-timeframe candles are visible to
    each H1 row).
    """
    return build_instrument_bundle(instrument).df


def build_instrument_bundle(instrument):
    """build_instrument_pipeline plus the OB universe. The full product."""
    daily_df = _load_raw(instrument, "D")
    h4_df = _load_raw(instrument, "H4")
    h1_df = _load_raw(instrument, "H1")
    return build_pipeline_bundle(daily_df, h4_df, h1_df)


def build_pipeline_from_frames(daily_df, h4_df, h1_df):
    """The merged frame alone, for callers that need no OB state."""
    return build_pipeline_bundle(daily_df, h4_df, h1_df).df


def build_pipeline_bundle(daily_df, h4_df, h1_df):
    """Same merge/no-lookahead logic as build_instrument_pipeline, but
    takes already-loaded Daily/H4/H1 DataFrames (date, open, high, low,
    close, ascending, only fully-closed candles) instead of reading the
    backtest's Parquet files. Used by live/ so the live signal engine
    runs through the identical code path as the backtest rather than a
    parallel reimplementation.
    """
    daily_df = compute_daily_structures(daily_df)
    daily_df = compute_daily_premium_discount(daily_df)

    h4_df = compute_h4_structures(h4_df)
    h4_df = compute_h4_premium_discount(h4_df)

    h1_df = compute_h1_structures(h1_df)
    h1_df = compute_h1_premium_discount(h1_df)

    # Each higher-timeframe row's CLOSE time, not its open time, is what
    # must be <= an H1 row's own time for that row to be visible without
    # lookahead: a Daily candle opening the same morning as an H1 signal
    # hasn't closed yet and must not be readable by that signal.
    daily_df = daily_df.sort_values("date").reset_index(drop=True)
    daily_df["date"] = daily_df["date"].astype("datetime64[ns, UTC]")
    daily_df["close_time"] = daily_df["date"] + DAILY_DURATION

    h4_df = h4_df.sort_values("date").reset_index(drop=True)
    h4_df["date"] = h4_df["date"].astype("datetime64[ns, UTC]")
    h4_df["close_time"] = h4_df["date"] + H4_DURATION

    h1_df = h1_df.sort_values("date").reset_index(drop=True)
    h1_df["date"] = h1_df["date"].astype("datetime64[ns, UTC]")

    # Excludes raw OHLC and the raw "date" (open time) columns: only the
    # already-prefixed structure/zone columns and "close_time" (the merge
    # key) are carried over, so nothing collides with h1_df's own "date".
    daily_cols = [c for c in daily_df.columns if c not in ("date", "open", "high", "low", "close")]
    h4_cols = [c for c in h4_df.columns if c not in ("date", "open", "high", "low", "close")]

    merged = pd.merge_asof(
        h1_df,
        daily_df[daily_cols],
        left_on="date",
        right_on="close_time",
        direction="backward",
    )
    merged = merged.drop(columns=["close_time"])

    merged = pd.merge_asof(
        merged,
        h4_df[h4_cols],
        left_on="date",
        right_on="close_time",
        direction="backward",
    )
    merged = merged.drop(columns=["close_time"])

    # An explicit position column, not an inferred offset. runner.py slices
    # this frame per year and then reset_index'es it, so the only reliable
    # way to rebase the OB universe onto a window is to carry the original
    # position along with the row.
    merged["h1_index"] = np.arange(len(merged), dtype=np.int64)

    # Time-based levels come from whichever frame owns their clock: day and
    # week from the Daily candles (confirmed with the user, so they line up
    # with the Daily structure and Daily zones), sessions from H1, which is
    # the only frame fine-grained enough to see a four-hour window.
    time_levels = pd.concat(
        [
            compute_previous_day_levels(daily_df),
            compute_previous_week_levels(daily_df),
            compute_session_levels(h1_df),
        ],
        ignore_index=True,
    )

    frames = {"Daily": daily_df, "4H": h4_df, "H1": h1_df}
    tier_prefixes = {
        "Daily": DAILY_TIER_PREFIXES,
        "4H": H4_TIER_PREFIXES,
        "H1": H1_TIER_PREFIXES,
    }
    liquidity = {
        timeframe: _liquidity_tables(
            frame, timeframe, tier_prefixes[timeframe], time_levels
        )
        for timeframe, frame in frames.items()
    }

    daily_obs = _order_blocks_for(
        daily_df, "Daily", DAILY_TIER_PREFIXES, liquidity["Daily"]
    )
    h4_obs = _order_blocks_for(h4_df, "4H", H4_TIER_PREFIXES, liquidity["4H"])
    h1_obs = _order_blocks_for(h1_df, "H1", H1_TIER_PREFIXES, liquidity["H1"])

    h4_obs = compute_containment(h4_obs, daily_obs, "within_daily_ob")
    h1_obs = compute_containment(h1_obs, h4_obs, "within_h4_ob")

    h1_ts = pd.DatetimeIndex(merged["date"])
    series = {
        "Daily": ob_state.to_h1_space(daily_obs, daily_df["date"], h1_ts, "Daily"),
        "4H": ob_state.to_h1_space(h4_obs, h4_df["date"], h1_ts, "4H"),
        "H1": ob_state.to_h1_space(h1_obs, h1_df["date"], h1_ts, "H1"),
    }
    universe = ob_state.build_ob_universe(
        series,
        merged["high"].to_numpy(),
        merged["low"].to_numpy(),
        merged["close"].to_numpy(),
    )

    liquidity_universe = _liquidity_universe(
        liquidity, time_levels, frames, merged, h1_ts, merged["close"].to_numpy()
    )

    return PipelineBundle(df=merged, obs=universe, liq=liquidity_universe)
