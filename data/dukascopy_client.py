"""Thin wrapper around dukascopy_python for the 11 backtest instruments.

Dukascopy's historical feed needs no account, login, or API token: fetch()
pulls directly from Dukascopy's public data feed. See
roadmap/V0.x.x.md and the Step 1 plan for why this replaced the
originally-planned OANDA REST API (OANDA does not accept Indian
residents).
"""

import dukascopy_python as dp
import dukascopy_python.instruments as instruments
import pandas as pd

# Maps this project's own instrument keys to the actual dukascopy_python
# constants, since the FX majors/crosses/commodities/indices live under
# different constant-name categories in the library.
INSTRUMENTS = {
    "EUR_USD": instruments.INSTRUMENT_FX_MAJORS_EUR_USD,
    "GBP_USD": instruments.INSTRUMENT_FX_MAJORS_GBP_USD,
    "USD_JPY": instruments.INSTRUMENT_FX_MAJORS_USD_JPY,
    "USD_CHF": instruments.INSTRUMENT_FX_MAJORS_USD_CHF,
    "AUD_USD": instruments.INSTRUMENT_FX_MAJORS_AUD_USD,
    "USD_CAD": instruments.INSTRUMENT_FX_MAJORS_USD_CAD,
    "NZD_USD": instruments.INSTRUMENT_FX_MAJORS_NZD_USD,
    "GBP_JPY": instruments.INSTRUMENT_FX_CROSSES_GBP_JPY,
    "EUR_JPY": instruments.INSTRUMENT_FX_CROSSES_EUR_JPY,
    "XAU_USD": instruments.INSTRUMENT_FX_METALS_XAU_USD,
    "NAS100": instruments.INSTRUMENT_IDX_AMERICA_E_NQ_100,
}

# Maps this project's own granularity keys to dukascopy_python's interval
# constants. Daily/H4/H1 are fetched directly, never resampled: swing_structure/
# has no resampling helpers and each tier is fed its own pre-built DataFrame.
GRANULARITIES = {
    "D": dp.INTERVAL_DAY_1,
    "H4": dp.INTERVAL_HOUR_4,
    "H1": dp.INTERVAL_HOUR_1,
}


def fetch_candles(instrument_key, granularity_key, start, end):
    """Fetch OHLC candles for one instrument/granularity/date range.

    Returns a DataFrame with columns date, open, high, low, close, in
    ascending chronological order, the exact shape swing_structure/'s
    detectors expect (see e.g. swing_structure/atr.py, fractal_detector.py,
    h1_structure.py, h4_structure.py, daily_structure.py).
    """
    instrument = INSTRUMENTS[instrument_key]
    interval = GRANULARITIES[granularity_key]

    df = dp.fetch(instrument, interval, dp.OFFER_SIDE_BID, start, end)

    df = df.reset_index().rename(columns={"timestamp": "date"})
    df = df[["date", "open", "high", "low", "close"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df
