"""compute_fvg_confluence's own output: which gap matches, and the new
swept_liquidity_fvg_stale_from_index this fix adds alongside it.

Hand-built OB and FVG tables in the shape order_blocks.py and
fair_value_gaps.py actually emit, rather than a full pipeline run: the
property under test is a column-in, column-out contract, and a two-row
fixture states it more plainly than the real detectors' output would.
"""

import pandas as pd

from smc.order_blocks.order_block_quality import compute_fvg_confluence

CLOSE = 15.0


def ohlc(length):
    return pd.DataFrame({"close": [CLOSE] * length})


def ob_row(direction="bullish", top=20.0, bottom=10.0, formed_index=5, zone_end_index=None):
    return {
        "direction": direction,
        "top": top,
        "bottom": bottom,
        "formed_index": formed_index,
        "zone_end_index": zone_end_index if zone_end_index is not None else formed_index,
    }


def fvg_row(direction="bullish", top=18.0, bottom=12.0, formed_index=0,
            active_until_index=50, expiry_index=100):
    return {
        "direction": direction,
        "top": top,
        "bottom": bottom,
        "formed_index": formed_index,
        "active_until_index": active_until_index,
        "expiry_index": expiry_index,
    }


class TestMatching:
    def test_a_touched_gap_gets_the_gaps_own_expiry_as_stale_from(self):
        obs = pd.DataFrame([ob_row()])
        gaps = pd.DataFrame([fvg_row(expiry_index=77)])
        result = compute_fvg_confluence(obs, gaps, ohlc(10))

        assert result.iloc[0]["swept_liquidity_fvg"]
        assert result.iloc[0]["swept_liquidity_fvg_stale_from_index"] == 77

    def test_no_matching_gap_leaves_stale_from_as_none(self):
        obs = pd.DataFrame([ob_row(formed_index=5)])
        # Direction mismatch: the OB is bullish, this gap is bearish.
        gaps = pd.DataFrame([fvg_row(direction="bearish")])
        result = compute_fvg_confluence(obs, gaps, ohlc(10))

        assert not result.iloc[0]["swept_liquidity_fvg"]
        assert result.iloc[0]["swept_liquidity_fvg_stale_from_index"] is None

    def test_an_empty_fvg_table_leaves_stale_from_as_none(self):
        obs = pd.DataFrame([ob_row()])
        gaps = pd.DataFrame(
            columns=["direction", "top", "bottom", "formed_index",
                     "active_until_index", "expiry_index"]
        )
        result = compute_fvg_confluence(obs, gaps, ohlc(10))

        assert not result.iloc[0]["swept_liquidity_fvg"]
        assert result.iloc[0]["swept_liquidity_fvg_stale_from_index"] is None

    def test_stale_from_is_independent_of_whether_the_gap_was_filled(self):
        """The core of this fix. active_until_index (which collapses to
        the fill date once anyone fills the gap) must be ignored entirely;
        only expiry_index (unconditional, fixed at formation + lookback)
        may set stale_from, whether the gap is filled or not.
        """
        obs = pd.DataFrame([ob_row(formed_index=5)])

        filled_early = pd.DataFrame([
            fvg_row(active_until_index=6, expiry_index=105)
        ])
        never_filled = pd.DataFrame([
            fvg_row(active_until_index=105, expiry_index=105)
        ])

        filled_result = compute_fvg_confluence(obs, filled_early, ohlc(10))
        unfilled_result = compute_fvg_confluence(obs, never_filled, ohlc(10))

        assert filled_result.iloc[0]["swept_liquidity_fvg_stale_from_index"] == 105
        assert unfilled_result.iloc[0]["swept_liquidity_fvg_stale_from_index"] == 105

    def test_matching_logic_itself_is_unchanged(self):
        """A far-edge breach still disqualifies a candidate exactly as
        before; this fix adds a column, it does not touch which OBs
        qualify.
        """
        obs = pd.DataFrame([ob_row(formed_index=1, zone_end_index=1)])
        gaps = pd.DataFrame([fvg_row(top=18.0, bottom=12.0)])

        breaching_close = pd.DataFrame({"close": [30.0, 5.0]})  # closes below bottom
        result = compute_fvg_confluence(obs, gaps, breaching_close)

        assert not result.iloc[0]["swept_liquidity_fvg"]
        assert result.iloc[0]["swept_liquidity_fvg_stale_from_index"] is None


class TestEmptyOrderBlockTable:
    def test_an_empty_ob_table_gets_both_columns(self):
        obs = pd.DataFrame(columns=["direction", "top", "bottom", "formed_index",
                                     "zone_end_index"])
        gaps = pd.DataFrame([fvg_row()])
        result = compute_fvg_confluence(obs, gaps, ohlc(10))

        assert list(result.columns).count("swept_liquidity_fvg") == 1
        assert list(result.columns).count("swept_liquidity_fvg_stale_from_index") == 1
        assert len(result) == 0
