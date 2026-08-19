"""Single source of truth for the multi-pair live rollout: which
instruments run, and how each one's MT5 orders are tagged so 10
processes sharing one account never collide or misattribute trades.
"""

PAIRS = [
    "AUD_USD",
    "EUR_JPY",
    "EUR_USD",
    "GBP_JPY",
    "GBP_USD",
    "NZD_USD",
    "USD_CAD",
    "USD_CHF",
    "USD_JPY",
    "XAU_USD",
]

MAGIC_BASE = 20260808


def magic_for(instrument):
    return MAGIC_BASE + PAIRS.index(instrument)
