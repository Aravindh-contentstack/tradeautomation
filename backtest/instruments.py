"""Per-instrument pip/point size, for the SLB pip cap and the max-SL-size
grid in backtest/analysis.py. Standard forex majors (quoted to 4 decimal
places) use 0.0001; JPY crosses (quoted to 2 decimal places) and XAU_USD
use 0.01 (2 decimals).

The 11 world equity indices are point-quoted index CFDs rather than
pip-quoted currency pairs, so their "pip size" is simply 1.0: one point.
That single value is safe across instruments whose prices differ by two
orders of magnitude (SP500 near 6000, JPN225 near 42000) because nothing
downstream assumes a fixed pip scale. In particular
_max_sl_size_pips_grid() in backtest/analysis.py derives the stop-loss
grid from quantiles of the OBSERVED data "rather than of an arbitrary
fixed pip scale", so a wide index and a narrow one each get a grid fitted
to their own distribution. A point size of 1.0 therefore just makes the
unit "1 index point" and lets the data set the scale.

NAS100 was excluded while its M15 series was a partial pull ending
2022-05-24. It is included now that the series is filled forward, and it
takes 1.0 for the same reason as the other ten.

Silver, platinum and palladium take a CONTRACT-VALUE pip, not the feed's
tick size. What one pip is worth has to mean something on the account,
and they are quoted far finer than they are traded: the standard retail
pip is one cent on a 5,000oz silver contract ($50) and ten cents on a
100oz platinum or palladium contract ($10). Using the raw tick instead
(0.001 on silver, 0.05 on platinum) would denominate every R figure in a
unit no position is ever sized in. Gold's existing 0.01 is the same
convention on a 100oz contract.

COPPER IS THE ONE EXCEPTION, and it is measured rather than assumed. Its
retail pip would be 0.01 on a 10,000lb contract, but copper trades near
$6.59 and its order blocks sit close to price: the 2015-2025 backtest put
the natural structure distance, stop to entry before any buffer, at about
1.2-1.5 pips on that scale. The fixed 0.5-8 pip SL buffer grid would then
be the stop rather than padding on it (at buffer 8, ~86% of the median
stop was buffer), and it would double as a trade filter, admitting 18
trades at buffer 0.5 against 116 at buffer 3 because a sub-3-pip stop is
inside normal 15-minute noise. 0.001 puts copper's natural stop near 35
pips, where the same buffer grid spans a few percent to about 40% of the
stop, which is the range it means on every other instrument. The unit is
still a real one: 0.001 on a 10,000lb contract is $10.
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
    "NAS100": 1.0,
    # Metals: the contract-value pip, not the feed's tick. See the module
    # docstring for the contract math and for why copper is the coarse one.
    "XAG_USD": 0.01,
    "XPT_USD": 0.1,
    "XPD_USD": 0.1,
    "COPPER_USD": 0.001,
}


def pip_size_for(instrument):
    return PIP_SIZES[instrument]
