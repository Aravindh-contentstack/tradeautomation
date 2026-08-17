"""Shared fixtures for the backtest engine tests.

Everything here is hand-built OHLC. No parquet, no network, no
build_instrument_pipeline. That is deliberate: the properties under test
(the 19:00 checkpoint, the Friday deadline, DST, intrabar attribution)
are all decided by a handful of bars, and a fixture small enough to read
in one screen is the only kind that can be argued about. A test that
loads a year of real data can tell you THAT something broke; a test built
from six bars tells you WHICH rule broke.

The shared trade shape, used by every fixture in the suite:

    bullish, entry 1.1000, stop 1.0980

so r_distance is 0.0020 and 1R is 20 pips. That makes every R level a
round number of pips off the entry, which keeps the fixtures readable:

    1.0R -> 1.1020    2.0R -> 1.1040    3.0R -> 1.1060
    1.5R -> 1.1030    2.5R -> 1.1050    4.0R -> 1.1080

Always assert R values through pytest.approx. 0.0020 and 0.0005 are not
exactly representable in binary floating point, so (1.1010 - 1.1000) /
0.0020 is not exactly 0.5 and an == comparison fails for reasons that
have nothing to do with the engine.
"""

import pandas as pd
import pytest

from backtest.context import build_market_context

ENTRY = 1.1000
SL = 1.0980
R_DISTANCE = 0.0020
PIP_SIZE = 0.0001
DIRECTION = "bullish"


def _frame(bars):
    """[(ts, o, h, l, c), ...] -> a frame in the raw parquet schema.

    date is datetime64[ns, UTC] and the OHLC columns are float64, which is
    exactly what build_market_context expects to read off a stored H1/M15
    parquet. ts may be an ISO string or a Timestamp.
    """
    rows = [
        {
            "date": pd.Timestamp(ts).tz_localize("UTC")
            if pd.Timestamp(ts).tzinfo is None
            else pd.Timestamp(ts).tz_convert("UTC"),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
        }
        for ts, o, h, l, c in bars
    ]
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.astype({"open": "float64", "high": "float64",
                      "low": "float64", "close": "float64"})


def h1(bars):
    """Hand-built H1 frame. See _frame."""
    return _frame(bars)


def m15(bars):
    """Hand-built M15 frame. Same schema as h1 -- the engine does not
    distinguish them structurally, only by the timestamps inside.
    """
    return _frame(bars)


def bar_range(start, count, ohlc, freq="1h"):
    """`count` filler bars of identical OHLC, starting at `start`.

    Several fixtures need a trade to simply survive from one 19:00
    checkpoint to the next, which at H1 is 24 bars of nothing happening.
    Spelling those out would bury the two or three bars that actually
    matter.
    """
    o, hi, lo, c = ohlc
    stamps = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=count, freq=freq)
    return [(ts, o, hi, lo, c) for ts in stamps]


def ctx_for(bars, m15_bars=None, pip_size=PIP_SIZE):
    """Wraps build_market_context so tests never touch its plumbing.

    m15_bars of None means "M15 is unavailable", which is a first-class
    state in the engine (the walk degrades to the pessimistic branch on
    ambiguous bars) rather than an error.
    """
    m15_df = None if m15_bars is None else m15(m15_bars)
    return build_market_context(h1(bars), pip_size, m15_df=m15_df)


@pytest.fixture
def make_ctx():
    """Fixture form of ctx_for, for tests that prefer injection."""
    return ctx_for
