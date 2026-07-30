"""One tier of structure, at whatever scale the caller asks for.

swing_structure/fractal_structure.py already does exactly this job, but
hardcoded to the Daily fractal tier: it always calls the detector with the
Daily default n and always emits columns prefixed "fractal_". This module
is that same wrapper with the prefix and the scale handed in, so a
timeframe needing three tiers makes three calls instead of needing three
near-identical modules copy-pasted from each other.

Why one detector can serve all three tiers
------------------------------------------
On Daily, the three tiers each use a different mechanism (a trailing
lookback window with a timeout clock, an ATR zigzag, and a Williams
Fractal). That mix has two costs which only get worse as timeframes are
added:

  1. The lookback tier's timeout REDRAWS the swing level purely because a
     counter reached 65, with no market event behind it. Since the whole
     point of having three tiers is to read them against each other
     (swing bullish while internal is bearish is the swing pullback
     phase), a swing level that moves for bookkeeping reasons makes that
     reading unreliable: a genuine pullback and an artefact look the
     same.
  2. "Internal" has no defined scale relative to "swing". It is whatever
     reversal_multiplier x ATR happens to produce, which drifts with
     volatility, so there is no single knob that answers "make the
     internal tier a bit coarser".

A Williams Fractal at a large n solves both. It IS a swing detector, and
a better-behaved one than a trailing window, because the pivot has to be
the extreme of a window CENTRED on it rather than one ending at "now". It
needs no timeout, since a stale level is replaced the moment the next
fractal confirms. And n is one legible knob shared by every tier.

The tiers stay fully independent
--------------------------------
This is worth being explicit about, because "one detector, three calls"
can read as though the tiers were coupled. They are not, and must not
become so. Each call gets its own OHLC slice, its own break state
machine, its own manual_restart, and its own compute_market_structure
pass. No tier reads another's state, no tier is clipped to another's
range, and no tier constrains another's direction. In particular these
are all expected and correct, not bugs to be fixed:

  - Swing bullish while internal is bearish (the swing pullback), and
    internal bearish while fractal is bullish (that pullback's own
    pullback). Disagreement between tiers is the signal, not an error.
  - After price breaks a swing high, internal and fractal pivots forming
    freely OUTSIDE the old swing range, until a later swing pivot
    engulfs them.

Running the same detector at different n does happen to give pivot-set
containment, since a larger n's conditions are a strict superset of a
smaller n's: pivots(n=20) is a subset of pivots(n=8) is a subset of
pivots(n=2). Note the direction. The SMALL-n set is the superset, so the
fast tier sees strictly more pivots, including every pivot outside the
slow tier's range. That is a fact about three independent runs of one
function, not a dependency between tiers, and it says nothing about their
directions agreeing. All it buys is confidence that the tiers measure the
same kind of object at different scales.
"""

from swing_structure.fractal_detector import compute_fractal_swing_structure
from swing_structure.market_structure import compute_market_structure

# Suffixes appended to the caller's prefix. Deliberately the same six
# names fractal_structure.py already emits, so a tier computed here is
# shaped identically to the Daily tiers and get_current_structure can
# read either without special cases.
_COLUMN_SUFFIXES = (
    "swing_high",
    "swing_low",
    "high_event",
    "low_event",
    "structure",
    "structure_event",
)

_DETECTOR_COLUMNS = {
    "swing_high": "swing_high",
    "swing_low": "swing_low",
    "high_event": "high_event",
    "low_event": "low_event",
    "structure": "market_structure",
    "structure_event": "market_structure_event",
}


def tier_column_names(prefix):
    """The six column names compute_tier_structure emits for a prefix.

    Exposed so callers (demos, checks, anything assembling a table) can
    name the columns without rebuilding the "{prefix}_{suffix}" string
    convention by hand and drifting from it.
    """
    return ["%s_%s" % (prefix, suffix) for suffix in _COLUMN_SUFFIXES]


def compute_tier_structure(
    df,
    prefix,
    n,
    manual_restart=None,
    min_atr_separation=0.0,
    atr_period=14,
):
    """Computes one tier's fractal pivots and bullish/bearish structure.

    df: DataFrame of OHLC candles (date, open, high, low, close, in
        ascending order). May already carry other tiers' columns from
        earlier calls, which are left untouched: only date/open/high/low/
        close are read.
    prefix: name this tier's six output columns are prefixed with, e.g.
        "h4_swing" produces h4_swing_swing_high, h4_swing_structure, and
        so on. Must be a legal identifier stem, so "h4_" rather than
        "4h_".
    n: the tier's scale, passed straight to the detector. Small n gives a
        fast, noisy tier, large n a slow, major-swing tier.
    manual_restart: optional boolean Series for THIS tier only,
        independent of any other tier's restart.
    min_atr_separation, atr_period: pass-through to the detector's
        optional significance filter. Off by default (0.0), see
        fractal_detector.py's docstring for what it does and why it ships
        disabled.

    Returns a copy of df with six new columns, named per
    tier_column_names(prefix).
    """
    # The detector always writes to the fixed names swing_high/swing_low/
    # high_event/low_event, overwriting whatever already uses them. Feeding
    # it df directly would silently clobber an earlier tier's columns when
    # tiers are computed in sequence on the same frame, so it is run on a
    # plain OHLC-only slice and only the prefixed columns are attached back
    # onto the caller's df. Same guard, and same reason, as
    # fractal_structure.py.
    ohlc_only = df[["date", "open", "high", "low", "close"]]
    tier = compute_fractal_swing_structure(
        ohlc_only,
        n=n,
        manual_restart=manual_restart,
        min_atr_separation=min_atr_separation,
        atr_period=atr_period,
    )
    tier = compute_market_structure(tier)

    result = df.reset_index(drop=True).copy()
    for suffix in _COLUMN_SUFFIXES:
        result["%s_%s" % (prefix, suffix)] = tier[_DETECTOR_COLUMNS[suffix]]
    return result
