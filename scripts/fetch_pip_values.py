"""Snapshot USD pip value for a standard 100,000-unit lot, for every FX pair
in the backtest set plus the 18 liquid crosses added in data/dukascopy_client.py.

Uses live ECB reference rates from the Frankfurter API
(https://api.frankfurter.app -- free, no key) rather than a hardcoded/stale
table, since pip value in USD depends on the day's exchange rate for any
pair not quoted directly in USD.

Formula, uniform for every pair XXX_YYY: pip_value_usd = pip_size * LOT_SIZE
/ rate[YYY], where rate[YYY] is units of YYY per 1 USD (rate[USD] = 1). This
handles both "quote is USD" pairs (EUR_USD) and crosses (EUR_GBP, AUD_JPY,
...) with one formula.

XAU_USD is intentionally excluded: gold's lot size / pip value convention is
broker-specific (unlike a standard FX lot), and live/risk.py already
computes real pip value dynamically from the broker's own tick spec at trade
time. A static guessed number here would be actively misleading rather than
useful, and isn't needed for backtesting math (which only needs PIP_SIZES).
"""

import json
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, ".")
from backtest.instruments import PIP_SIZES as EXISTING_PIP_SIZES

CROSS_PIP_SIZES = {
    "EUR_GBP": 0.0001, "EUR_CHF": 0.0001, "EUR_AUD": 0.0001,
    "EUR_CAD": 0.0001, "EUR_NZD": 0.0001,
    "GBP_CHF": 0.0001, "GBP_AUD": 0.0001, "GBP_CAD": 0.0001, "GBP_NZD": 0.0001,
    "AUD_JPY": 0.01, "AUD_CAD": 0.0001, "AUD_CHF": 0.0001, "AUD_NZD": 0.0001,
    "NZD_JPY": 0.01, "NZD_CAD": 0.0001, "NZD_CHF": 0.0001,
    "CAD_JPY": 0.01, "CHF_JPY": 0.01,
}

LOT_SIZE = 100_000
RATES_URL = "https://api.frankfurter.app/latest?from=USD"
OUTPUT_PATH = "data/pip_values.json"


def load_rates():
    resp = requests.get(RATES_URL, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    rates = payload["rates"]
    rates["USD"] = 1.0
    return rates, payload["date"]


def pip_value_usd(pip_size, quote_ccy, rates):
    return pip_size * LOT_SIZE / rates[quote_ccy]


def main():
    rates, rate_date = load_rates()

    all_pip_sizes = {k: v for k, v in EXISTING_PIP_SIZES.items() if k != "XAU_USD"}
    all_pip_sizes.update(CROSS_PIP_SIZES)

    pairs = {}
    for instrument, pip_size in sorted(all_pip_sizes.items()):
        _, quote_ccy = instrument.split("_")
        pairs[instrument] = {
            "pip_size": pip_size,
            "pip_value_usd": round(pip_value_usd(pip_size, quote_ccy, rates), 4),
        }

    snapshot = {
        "as_of": rate_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://api.frankfurter.app (ECB reference rates)",
        "lot_size": LOT_SIZE,
        "note": (
            "USD value of a 1-pip move for a standard 100,000-unit lot, "
            "computed from the ECB reference rate on 'as_of'. Rates move "
            "daily, so treat this as a reference snapshot: re-run "
            "scripts/fetch_pip_values.py for a fresh one. For mini/micro "
            "lots divide by 10 / 100. XAU_USD is intentionally excluded, "
            "see this script's docstring."
        ),
        "pairs": pairs,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(pairs)} pairs to {OUTPUT_PATH} (rates as of {rate_date})")
    for instrument, data in pairs.items():
        print(f"  {instrument:10s} pip_size={data['pip_size']:<7} pip_value_usd={data['pip_value_usd']}")


if __name__ == "__main__":
    main()
