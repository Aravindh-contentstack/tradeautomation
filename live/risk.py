"""Position sizing: risk a fixed percentage of current account balance
per trade, sized off the real stop-loss distance and the broker's own
tick value/size (not an assumed pip value), so it's correct for any
instrument/account-currency combination without per-instrument
constants.
"""

import math

RISK_PER_TRADE = 0.001  # 0.1% of current balance, per user decision.

# If the broker's minimum tradeable lot alone would risk more than this
# multiple of the intended RISK_PER_TRADE, the trade is skipped instead
# of silently taking on outsized risk (this only bites on very small
# account balances, where even the minimum lot is "too big").
MAX_RISK_MULTIPLE_ON_MIN_LOT = 3.0


def position_size(balance, sl_distance_price, symbol_spec):
    """Returns the order volume in lots, rounded down to the broker's
    volume_step and clamped to [volume_min, volume_max], or None if
    even the minimum lot would risk too much given the current balance
    and stop distance (caller should skip the trade in that case).
    """
    if sl_distance_price <= 0:
        raise ValueError("sl_distance_price must be positive")

    risk_amount = balance * RISK_PER_TRADE
    money_per_price_unit_per_lot = (
        symbol_spec["trade_tick_value"] / symbol_spec["trade_tick_size"]
    )
    raw_lots = risk_amount / (sl_distance_price * money_per_price_unit_per_lot)

    step = symbol_spec["volume_step"]
    volume_min = symbol_spec["volume_min"]
    volume_max = symbol_spec["volume_max"]

    if raw_lots < volume_min:
        actual_risk = volume_min * sl_distance_price * money_per_price_unit_per_lot
        if actual_risk > risk_amount * MAX_RISK_MULTIPLE_ON_MIN_LOT:
            return None
        return round(volume_min, 2)

    lots = math.floor(raw_lots / step) * step
    lots = min(lots, volume_max)
    return round(lots, 2)
