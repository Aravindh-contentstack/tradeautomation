"""Exposes the current (most recent) daily structure state, per factor,
for a later stage to combine with the weighted factors in
factors/xau_probability_factors.csv / factors/eu_probability_factors.csv.

Nothing new is computed here. swing_structure/detector.py plus
swing_structure/market_structure.py already produce a market_structure
column (the swing tier's bullish/bearish), and
swing_structure/internal_structure.py already produces an
internal_structure column (the internal tier's), both via the same
compute_market_structure rule (only a genuine break flips it). This
module just answers "what is the state right now" instead of every
caller reaching into a DataFrame and pulling the last row by hand.
"""

# Factor name (matching the Factor column in the probability factors
# CSVs, lowercased) to the DataFrame column that holds its current
# bullish/bearish state. Add a line here once a tier exists, e.g.:
#     "fractal": "fractal_structure",
_STRUCTURE_COLUMNS = {
    "swing": "market_structure",
    "internal": "internal_structure",
}


def get_current_structure(df):
    """Returns the most recent bullish/bearish state per factor.

    df: a DataFrame that already has one or more of the columns listed
        in _STRUCTURE_COLUMNS computed on it (the same combined table
        produced by chaining compute_daily_swing_structure ->
        compute_market_structure -> compute_internal_structure), in
        ascending date order. Only the LAST row is read, "today" as far
        as this DataFrame goes. To get the state as of an earlier date
        instead, pass a DataFrame truncated up to that date.

    Returns a dict: {"date": <the last row's date>, "swing": <value>,
    "internal": <value>}. Every key in _STRUCTURE_COLUMNS is always
    present. A value is None if that column isn't in df at all (not yet
    computed) or if the column's own value is None (computed, but still
    undetermined), the caller only ever needs to check for None, not
    care which of those it was.
    """
    last_row = df.iloc[-1]

    result = {"date": last_row["date"]}
    for factor, column in _STRUCTURE_COLUMNS.items():
        result[factor] = last_row[column] if column in df.columns else None
    return result
