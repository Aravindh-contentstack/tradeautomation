"""Per-instrument pip/point size, for the SLB pip cap and the max-SL-size
grid in backtest/analysis.py. Standard forex majors (quoted to 4 decimal
places) use 0.0001; JPY crosses (quoted to 2 decimal places) and XAU_USD
use 0.01 (2 decimals).

The 10 world equity indices are point-quoted index CFDs rather than
pip-quoted currency pairs, so their "pip size" is simply 1.0: one point.
That single value is safe across instruments whose prices differ by two
orders of magnitude (SP500 near 6000, JPN225 near 42000) because nothing
downstream assumes a fixed pip scale. In particular
_max_sl_size_pips_grid() in backtest/analysis.py derives the stop-loss
grid from quantiles of the OBSERVED data "rather than of an arbitrary
fixed pip scale", so a wide index and a narrow one each get a grid fitted
to their own distribution. A point size of 1.0 therefore just makes the
unit "1 index point" and lets the data set the scale.

NAS100 stays excluded: no data was pulled for it under this plan, and
adding a key here without a matching parquet would only make the fetch
and validation scripts iterate over a hole.
"""

PIP_SIZES = {
    "AUD_USD": 0.0001,
    "EUR_JPY": 0.01,
    "EUR_USD": 0.0001,
    "GBP_JPY": 0.01,
    "GBP_USD": 0.0001,
    "NZD_USD": 0.0001,
    "USD_CAD": 0.0001,
    "USD_CHF": 0.0001,
    "USD_JPY": 0.01,
    "XAU_USD": 0.01,
    "EUR_GBP": 0.0001,
    "EUR_CHF": 0.0001,
    "EUR_AUD": 0.0001,
    "EUR_CAD": 0.0001,
    "EUR_NZD": 0.0001,
    "GBP_CHF": 0.0001,
    "GBP_AUD": 0.0001,
    "GBP_CAD": 0.0001,
    "GBP_NZD": 0.0001,
    "AUD_JPY": 0.01,
    "AUD_CAD": 0.0001,
    "AUD_CHF": 0.0001,
    "AUD_NZD": 0.0001,
    "NZD_JPY": 0.01,
    "NZD_CAD": 0.0001,
    "NZD_CHF": 0.0001,
    "CAD_JPY": 0.01,
    "CHF_JPY": 0.01,
    # World equity index CFDs: point-quoted, so one "pip" is one point.
    # See the module docstring for why a single 1.0 works across every
    # price scale here.
    "SP500": 1.0,
    "UK100": 1.0,
    "JPN225": 1.0,
    "US30": 1.0,
    "DAX40": 1.0,
    "IBXEUR": 1.0,
    "ESXEUR": 1.0,
    "ASXAUD": 1.0,
    "HSIHKD": 1.0,
    "F40EUR": 1.0,
}


def pip_size_for(instrument):
    return PIP_SIZES[instrument]
