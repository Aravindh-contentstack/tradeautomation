"""Per-instrument pip/point size, for the SLB pip cap and the max-SL-size
grid in backtest/analysis.py. Standard forex majors (quoted to 4 decimal
places) use 0.0001; JPY crosses (quoted to 2 decimal places) and XAU_USD
use 0.01 (2 decimals). NAS100 is a point-based index CFD, not a
pip-quoted currency pair, and is deliberately excluded here (not
supported by this backtest yet).
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
}


def pip_size_for(instrument):
    return PIP_SIZES[instrument]
