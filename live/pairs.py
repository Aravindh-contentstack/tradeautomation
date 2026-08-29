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
    # Appended 2026-08-29 with the portfolio-wide settings rollout
    # (per-pair-trade-setttings.csv). Always append here, never insert
    # above: magic_for derives each pair's MT5 magic number from its
    # index in this list, and reordering would reassign a currently-live
    # pair's magic number and orphan its open MT5 positions.
    "EUR_GBP",
    "EUR_CHF",
    "EUR_AUD",
    "EUR_CAD",
    "EUR_NZD",
    "GBP_CHF",
    "GBP_AUD",
    "GBP_CAD",
    "GBP_NZD",
    "AUD_JPY",
    "AUD_CAD",
    "AUD_CHF",
    "AUD_NZD",
    "NZD_JPY",
    "NZD_CAD",
    "NZD_CHF",
    "CAD_JPY",
    "CHF_JPY",
]

MAGIC_BASE = 20260808


def magic_for(instrument):
    return MAGIC_BASE + PAIRS.index(instrument)
