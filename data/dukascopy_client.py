"""Thin wrapper around dukascopy_python for the 29 backtest/research
instruments.

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
    # Extra metals, data-only (not wired into PIP_SIZES/PAIRS). Added
    # 2026-09-03 and pulled by scripts/fetch_metals.py for D/H4/H1/M15/M1.
    # XAG_USD is a spot FX-metal like XAU_USD; the other three are Dukascopy
    # commodity CFDs (XPD.CMD/USD, XPT.CMD/USD, COPPER.CMD/USD) -- palladium,
    # platinum and copper. Copper is what other brokers ticker as CUCUSD.
    # Dukascopy has NO EUR-quoted metals, so XAGEUR / XAUEUR are unavailable.
    "XAG_USD": instruments.INSTRUMENT_FX_METALS_XAG_USD,
    "XPD_USD": instruments.INSTRUMENT_CMD_METALS_XPD_CMD_USD,
    "XPT_USD": instruments.INSTRUMENT_CMD_METALS_XPT_CMD_USD,
    "COPPER_USD": instruments.INSTRUMENT_CMD_METALS_COPPER_CMD_USD,
    # Liquid FX crosses, data-only for now (not wired into PIP_SIZES/PAIRS).
    "EUR_GBP": instruments.INSTRUMENT_FX_CROSSES_EUR_GBP,
    "EUR_CHF": instruments.INSTRUMENT_FX_CROSSES_EUR_CHF,
    "EUR_AUD": instruments.INSTRUMENT_FX_CROSSES_EUR_AUD,
    "EUR_CAD": instruments.INSTRUMENT_FX_CROSSES_EUR_CAD,
    "EUR_NZD": instruments.INSTRUMENT_FX_CROSSES_EUR_NZD,
    "GBP_CHF": instruments.INSTRUMENT_FX_CROSSES_GBP_CHF,
    "GBP_AUD": instruments.INSTRUMENT_FX_CROSSES_GBP_AUD,
    "GBP_CAD": instruments.INSTRUMENT_FX_CROSSES_GBP_CAD,
    "GBP_NZD": instruments.INSTRUMENT_FX_CROSSES_GBP_NZD,
    "AUD_JPY": instruments.INSTRUMENT_FX_CROSSES_AUD_JPY,
    "AUD_CAD": instruments.INSTRUMENT_FX_CROSSES_AUD_CAD,
    "AUD_CHF": instruments.INSTRUMENT_FX_CROSSES_AUD_CHF,
    "AUD_NZD": instruments.INSTRUMENT_FX_CROSSES_AUD_NZD,
    "NZD_JPY": instruments.INSTRUMENT_FX_CROSSES_NZD_JPY,
    "NZD_CAD": instruments.INSTRUMENT_FX_CROSSES_NZD_CAD,
    "NZD_CHF": instruments.INSTRUMENT_FX_CROSSES_NZD_CHF,
    "CAD_JPY": instruments.INSTRUMENT_FX_CROSSES_CAD_JPY,
    "CHF_JPY": instruments.INSTRUMENT_FX_CROSSES_CHF_JPY,
    # World equity indices, added 2026-08-31. These are POINT-quoted CFDs, not
    # pip-quoted pairs, so backtest/instruments.py gives them a PIP_SIZES of
    # 1.0 (one index point). That works because analysis.py's
    # _max_sl_size_pips_grid derives its grid from quantiles of the observed
    # data rather than from a fixed pip scale, so the 20x spread in price
    # scale across these (a median H1 range of ~12 points on ESXEUR vs ~268 on
    # JPN225) sizes itself.
    #
    # Dukascopy only carries these from 2015, which is why
    # scripts/fetch_historical_data.py needs a per-instrument start date
    # rather than the global 2003 EARLIEST_START.
    #
    # TRADING HOURS ARE NOT 24/5 for all of them, and that has one real
    # consequence. IBXEUR, ESXEUR and F40EUR trade roughly 06:00-19:00 UTC and
    # so have NO bars at all during the Asian range (midnight-04:00 London);
    # HSIHKD covers it only partially. smc/liquidity/time_levels.py already
    # skips a session with no bars rather than emitting a null range, so those
    # three simply produce no Asian-range liquidity level. That is the
    # intended behaviour -- an absent level, never an invented one. Both
    # killzones (London 07-10, NY 12-15 London civil) ARE covered by all ten.
    "SP500": instruments.INSTRUMENT_IDX_AMERICA_E_SANDP_500,
    "UK100": instruments.INSTRUMENT_IDX_EUROPE_E_FUTSEE_100,
    "JPN225": instruments.INSTRUMENT_IDX_ASIA_E_N225JAP,
    "US30": instruments.INSTRUMENT_IDX_AMERICA_E_D_J_IND,
    "DAX40": instruments.INSTRUMENT_IDX_EUROPE_E_DAAX,
    "IBXEUR": instruments.INSTRUMENT_IDX_EUROPE_E_IBC_MAC,
    "ESXEUR": instruments.INSTRUMENT_IDX_EUROPE_E_DJE50XX,
    "ASXAUD": instruments.INSTRUMENT_IDX_ASIA_E_XJO_ASX,
    "HSIHKD": instruments.INSTRUMENT_IDX_ASIA_E_H_KONG,
    "F40EUR": instruments.INSTRUMENT_IDX_EUROPE_E_CAAC_40,
}

# The equity-index block above, as its own list. fetch_historical_data.py uses
# it to give the indices a 2015 start date, and scripts/fetch_index_m1.py uses
# it to know which instruments to pull M1 for.
INDEX_KEYS = [
    "SP500",
    "UK100",
    "JPN225",
    "US30",
    "DAX40",
    "IBXEUR",
    "ESXEUR",
    "ASXAUD",
    "HSIHKD",
    "F40EUR",
]

# Maps this project's own granularity keys to dukascopy_python's interval
# constants. Daily/H4/H1 are fetched directly, never resampled: swing_structure/
# has no resampling helpers and each tier is fed its own pre-built DataFrame.
#
# M15 was added for intrabar resolution only, NOT as an entry timeframe. When
# an H1 bar touches both the stop and a favourable extreme, H1 alone cannot say
# which came first; the M15 sub-bars can. Note the library constant is spelled
# INTERVAL_MIN_15 (not INTERVAL_M15) -- getting this wrong silently yields a
# different interval rather than raising, which is why scripts/validate_m15.py
# asserts the minute set is exactly {0, 15, 30, 45}.
#
# M1 was added 2026-08-31 for the equity indices only, and only as the raw
# material for M3: Dukascopy has no 3-minute interval (the minute intervals it
# offers are 1, 5, 10, 15 and 30), so scripts/build_index_m3.py resamples M1
# up to M3. M3 is deliberately NOT a key here, because this dict maps to real
# dukascopy_python interval constants and M3 is not one of them.
#
# M5 was added 2026-09-03, also data-only, pulled by scripts/fetch_m5.py for
# every key in INSTRUMENTS. Unlike M1 it maps to a real Dukascopy interval and
# needs no resampling; it is a genuine intraday tier that just was not needed
# until now. Its history on this feed starts in 2012, same as M1.
GRANULARITIES = {
    "D": dp.INTERVAL_DAY_1,
    "H4": dp.INTERVAL_HOUR_4,
    "H1": dp.INTERVAL_HOUR_1,
    "M15": dp.INTERVAL_MIN_15,
    "M5": dp.INTERVAL_MIN_5,
    "M1": dp.INTERVAL_MIN_1,
}


def fetch_candles(instrument_key, granularity_key, start, end, max_retries=7):
    """Fetch OHLC candles for one instrument/granularity/date range.

    Returns a DataFrame with columns date, open, high, low, close, in
    ascending chronological order, the exact shape swing_structure/'s
    detectors expect (see e.g. swing_structure/atr.py, fractal_detector.py,
    h1_structure.py, h4_structure.py, daily_structure.py).

    max_retries is passed straight through to dp.fetch (library default 7).
    The M15 pull is roughly 47 chunks per instrument across 10 instruments, so
    a single exhausted retry midway is far more likely than on the Daily/H4/H1
    pulls; scripts/fetch_historical_data.py raises this to 10.
    """
    instrument = INSTRUMENTS[instrument_key]
    interval = GRANULARITIES[granularity_key]

    df = dp.fetch(
        instrument, interval, dp.OFFER_SIDE_BID, start, end, max_retries=max_retries
    )

    df = df.reset_index().rename(columns={"timestamp": "date"})
    df = df[["date", "open", "high", "low", "close"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df
