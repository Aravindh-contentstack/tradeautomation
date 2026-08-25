"""Intrabar resolution: mapping each H1 bar to its M15 sub-bars.

Why this exists
---------------
An H1 bar that both spikes in our favour and touches the stop is
ambiguous: OHLC alone cannot say which happened first. The engine's
answer used to be "credit the whole favourable move anyway", which
inflated max_r_reached with a move that may never have been available
(defect 2 in the plan). The honest answer is to look inside the bar.

This module used to carry the line "M15 is used for exactly this and
nothing else. Entry stays on H1 -- there is no M15 structure detection,
and no M15 factor (user-confirmed)." That decision has been REVERSED.
The M15 entry models (LC-1, LC-2A, LC-2B, CE) now detect structure and
liquidity on M15 and contribute their own factors, so M15 has two
consumers rather than one.

What did not change is the direction of the mapping. The base timeline
is still H1: order blocks, liquidity levels and every factor array stay
indexed on H1 bars, and the entry layer is a bounded sub-scan that hands
back a price and a fill bar. Anything that needs the H1 universe rebased
onto M15 is a different and much larger change, and is not what
happened.

This class is NOT the entry layer's seam, and must not become it. Its
indices are YEAR-SCOPED: runner.run_year hands build_market_context an
m15_df already windowed to the walk period, so subbars() addresses that
window and nothing else. The entry models need full history instead, or
liquidity formed in December would be invisible to January for the same
reason build_pipeline_bundle refuses to build order blocks per year. So
backtest/m15_pipeline.py builds its own full-history bundle and maps
between the two spaces by TIMESTAMP, never by reusing an index from
here. Mixing the two index spaces is silent and would misaddress every
level.

Why searchsorted and not a mask
-------------------------------
The obvious implementation, "for each H1 bar, boolean-mask the M15
frame", is about 480k * 620k comparisons per instrument-year. That is
3e11 operations and is simply not runnable. Instead the M15 timestamps
are already sorted, so two vectorised np.searchsorted calls -- one for
every bar's window start, one for every window end -- give the whole
start/stop table in a couple of milliseconds. subbars(k) is then a
numpy SLICE: a zero-copy view, no allocation.

subbars(k) is called only on terminal and ambiguous bars, roughly 4k
times per instrument-year, not once per bar-visit.
"""

import numpy as np
import pandas as pd

H1_DURATION = np.timedelta64(1, "h")


def _naive_utc_values(dates):
    """tz-aware UTC datetimes -> datetime64[ns] numpy array (UTC, naive).

    The engine compares timestamps as raw int64 nanoseconds everywhere,
    so tz metadata is stripped once here rather than at every comparison.
    """
    s = pd.Series(pd.DatetimeIndex(dates))
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s.to_numpy(dtype="datetime64[ns]")


class M15Index:
    """Zero-copy lookup from an H1 bar index to that hour's M15 sub-bars.

    Deliberately a class rather than plain functions: the start/stop
    table and the four OHLC arrays are genuine held state that every
    subbars() call reads, which is the one case the house style allows.
    """

    def __init__(self, h1_dates, m15_df):
        h1_ts = _naive_utc_values(h1_dates)
        m15_ts = _naive_utc_values(m15_df["date"])

        # Two searchsorted calls, vectorised over ALL H1 bars at once.
        # "left" on both edges gives the half-open window [bar, bar+1h),
        # so the 15:00 sub-bar belongs to the 15:00 H1 bar and the 16:00
        # sub-bar does not.
        self._starts = np.searchsorted(m15_ts, h1_ts, side="left")
        self._stops = np.searchsorted(m15_ts, h1_ts + H1_DURATION, side="left")

        self._open = m15_df["open"].to_numpy(dtype=np.float64)
        self._high = m15_df["high"].to_numpy(dtype=np.float64)
        self._low = m15_df["low"].to_numpy(dtype=np.float64)
        self._close = m15_df["close"].to_numpy(dtype=np.float64)
        self._ts = m15_ts

    def __len__(self):
        return len(self._ts)

    def subbars(self, k):
        """Returns (ts, open, high, low, close) numpy VIEWS of H1 bar k's
        M15 sub-bars, in chronological order, or None if that hour has no
        M15 coverage (a data hole, or a bar outside the M15 history).

        Callers must treat None as "unresolvable" and degrade, never as
        "no adverse move".
        """
        if k < 0 or k >= len(self._starts):
            return None
        lo = self._starts[k]
        hi = self._stops[k]
        if hi <= lo:
            return None
        return (
            self._ts[lo:hi],
            self._open[lo:hi],
            self._high[lo:hi],
            self._low[lo:hi],
            self._close[lo:hi],
        )


def build_m15_index(h1_dates, m15_df):
    """Returns an M15Index, or None when M15 is unavailable.

    None is a first-class answer, not an error: the whole engine runs
    without M15 (just more pessimistically on ambiguous bars), and WS-A's
    download is allowed to be missing or partial.
    """
    if m15_df is None or len(m15_df) == 0:
        return None
    return M15Index(h1_dates, m15_df)
