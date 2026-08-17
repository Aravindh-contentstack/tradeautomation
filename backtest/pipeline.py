"""Loads one instrument's Daily/H4/H1 history, runs the existing
smc detectors (structure, premium/discount, order blocks and their quality
factors) on each, and merges Daily and H4 state onto the H1 timeline for
the backtest engine.

Reuses smc/market_structure/*.py, smc/order_blocks/*.py and
smc/liquidity/*.py as-is: this module only loads data, sequences those
detectors, and reconciles their output onto one timeline. It does not
reimplement any structure, zone, or OB logic.

Two products come out, and they are different shapes on purpose. The
merged DataFrame is one row per H1 candle, which suits scalar
structure/zone state. Order blocks are not scalar per candle (several are
live and nested at once), so they stay long-format and are reconciled onto
the H1 index by smc/order_blocks/ob_state.py instead of being flattened
into columns. PipelineBundle carries both.

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
from smc.liquidity.fair_value_gaps import compute_fair_value_gaps
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
    compute_containment,
    compute_flip_zone,
    compute_fvg_confluence,
    compute_inducement,
    compute_previous_candle_sweep,
    compute_swept_liquidity_structural,
)

DAILY_DURATION = pd.Timedelta(days=1)
H4_DURATION = pd.Timedelta(hours=4)

# How many H1 bars of the merged frame the live context keeps. Only the
# newest closed candle can produce an order, so this is generous on
# purpose: it costs nothing and leaves room for a deferred entry, whose
# trigger bar sits before the bar it enters on.
LIVE_CONTEXT_BARS = 200


@dataclass(frozen=True, eq=False)
class PipelineBundle:
    """The merged per-candle frame plus the OB universe on its index."""

    df: object
    obs: object


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
    )


def _order_blocks_for(structured_df, timeframe, tier_prefixes):
    """One timeframe's OB table with the full quality stack applied.

    Order matters: identification first, then the per-OB confluence
    columns, and containment last (in build_pipeline_bundle) because it
    needs both timeframes' finished tables. Old Points and Equals
    liquidity are absent throughout, having no detectors yet.
    """
    compute = {
        "Daily": compute_daily_order_blocks,
        "4H": compute_h4_order_blocks,
        "H1": compute_h1_order_blocks,
    }[timeframe]

    obs = compute(structured_df)
    obs = compute_swept_liquidity_structural(obs, structured_df, tier_prefixes)
    obs = compute_fvg_confluence(obs, compute_fair_value_gaps(structured_df), structured_df)
    obs = compute_previous_candle_sweep(obs, structured_df)
    obs = compute_inducement(obs)
    obs = compute_flip_zone(obs)
    return obs


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

    daily_obs = _order_blocks_for(daily_df, "Daily", DAILY_TIER_PREFIXES)
    h4_obs = _order_blocks_for(h4_df, "4H", H4_TIER_PREFIXES)
    h1_obs = _order_blocks_for(h1_df, "H1", H1_TIER_PREFIXES)

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

    return PipelineBundle(df=merged, obs=universe)
