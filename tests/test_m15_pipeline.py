"""The M15 bundle and the two index-space crossings.

Most of what build_m15_bundle does is call detectors that have their own
tests, so the assertions here are about the wiring: that every field is
populated, that None survives, and above all that the two timestamp
helpers refuse to guess.

The helpers are the interesting part. They exist because this bundle is
indexed on FULL M15 history while backtest/intrabar.py's M15Index is
indexed on a year window, and the two integer spaces are
indistinguishable at a glance. Every test below that asserts -1 is
asserting that a caller cannot be handed a plausible wrong bar.
"""

import numpy as np
import pandas as pd

from backtest.m15_pipeline import (
    build_m15_bundle,
    h1_bar_containing,
    m15_index_at_or_after,
)
from smc.market_structure.m15_structure import m15_column_names

START = pd.Timestamp("2024-01-01 00:00", tz="UTC")


def m15_frame(count, start=START, step_minutes=15):
    """`count` M15 candles walking gently upward, at 15-minute spacing."""
    rows = []
    for i in range(count):
        base = 100.0 + (i % 7) * 0.5
        rows.append({
            "date": start + pd.Timedelta(minutes=step_minutes * i),
            "open": base,
            "high": base + 0.4,
            "low": base - 0.4,
            "close": base + 0.2,
        })
    return pd.DataFrame(rows)


def h1_stamps(hours, start="2024-01-01 00:00"):
    """A naive-UTC H1 timestamp array, the shape ctx.ts has."""
    base = pd.Timestamp(start)
    return pd.DatetimeIndex(
        [base + pd.Timedelta(hours=i) for i in range(hours)]
    ).to_numpy(dtype="datetime64[ns]")


class TestBundleShape:
    def test_every_field_is_populated(self):
        bundle = build_m15_bundle(m15_frame(200))
        assert len(bundle.ts) == 200
        assert len(bundle.high) == 200
        assert len(bundle.atr) == 200
        assert len(bundle.london_hour) == 200
        assert bundle.minor is not None
        assert bundle.levels is not None
        assert bundle.lrlq is not None
        assert bundle.fvgs is not None

    def test_every_structure_column_is_present(self):
        bundle = build_m15_bundle(m15_frame(200))
        for name in m15_column_names():
            assert name in bundle.structure
            assert len(bundle.structure[name]) == 200

    def test_atr_warm_up_is_nan_not_zero(self):
        # NaN compares False against everything, so a warm-up bar cannot
        # satisfy a threshold test by accident. 0.0 would satisfy "second
        # top within 0.10 x ATR" for every pair of tops in existence.
        bundle = build_m15_bundle(m15_frame(200))
        assert np.isnan(bundle.atr[0])
        assert not np.isnan(bundle.atr[-1])

    def test_timestamps_are_naive_utc(self):
        # Matched to MarketContext.ts so h1_bar_containing can compare the
        # two arrays without a per-call conversion.
        bundle = build_m15_bundle(m15_frame(50))
        assert bundle.ts.dtype == np.dtype("datetime64[ns]")

    def test_no_data_gives_none(self):
        # First-class answer, not an error: NAS100 has no M15 at all.
        assert build_m15_bundle(None) is None
        assert build_m15_bundle(m15_frame(0)) is None


class TestM15IndexAtOrAfter:
    def test_an_exact_hour_lands_on_that_hour_first_sub_bar(self):
        bundle = build_m15_bundle(m15_frame(20))
        assert m15_index_at_or_after(bundle, START) == 0
        assert m15_index_at_or_after(bundle, START + pd.Timedelta(hours=1)) == 4
        assert m15_index_at_or_after(bundle, START + pd.Timedelta(hours=2)) == 8

    def test_a_hole_yields_the_next_available_sub_bar(self):
        # The 01:00 hour is missing entirely. Starting the scan at 01:00
        # must give the first 02:00 sub-bar, not -1: refusing over a hole
        # in the mitigating hour would drop the whole setup.
        frame = pd.concat(
            [m15_frame(4), m15_frame(4, start=START + pd.Timedelta(hours=2))],
            ignore_index=True,
        )
        bundle = build_m15_bundle(frame)
        pos = m15_index_at_or_after(bundle, START + pd.Timedelta(hours=1))
        assert pos == 4
        assert bundle.ts[pos] == np.datetime64("2024-01-01 02:00")

    def test_past_the_end_gives_minus_one(self):
        bundle = build_m15_bundle(m15_frame(4))
        assert m15_index_at_or_after(bundle, START + pd.Timedelta(days=5)) == -1

    def test_a_naive_timestamp_is_accepted(self):
        # ctx.ts holds naive UTC, so the scan will pass naive values.
        bundle = build_m15_bundle(m15_frame(20))
        assert m15_index_at_or_after(bundle, pd.Timestamp("2024-01-01 01:00")) == 4

    def test_no_bundle_gives_minus_one(self):
        assert m15_index_at_or_after(None, START) == -1


class TestH1BarContaining:
    def test_every_sub_bar_of_an_hour_maps_to_that_hour(self):
        h1_ts = h1_stamps(3)
        bundle = build_m15_bundle(m15_frame(12))
        answers = [h1_bar_containing(h1_ts, bundle.ts[j]) for j in range(12)]
        assert answers == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]

    def test_it_round_trips_with_m15_index_at_or_after(self):
        # The property that matters: the two helpers are inverses across
        # the H1/M15 boundary, which is what lets the scan start from an
        # H1 mitigation and then ask about H1 validity again.
        h1_ts = h1_stamps(4)
        bundle = build_m15_bundle(m15_frame(16))
        for k in range(4):
            pos = m15_index_at_or_after(bundle, h1_ts[k])
            assert h1_bar_containing(h1_ts, bundle.ts[pos]) == k

    def test_a_sub_bar_before_the_frame_gives_minus_one(self):
        h1_ts = h1_stamps(2, start="2024-01-01 05:00")
        bundle = build_m15_bundle(m15_frame(4))
        assert h1_bar_containing(h1_ts, bundle.ts[0]) == -1

    def test_a_sub_bar_past_the_last_hour_gives_minus_one(self):
        # The half-open check. A searchsorted alone would attribute a
        # 03:00 sub-bar to the 02:00 bar, which is the last one present.
        h1_ts = h1_stamps(3)
        bundle = build_m15_bundle(m15_frame(4, start=START + pd.Timedelta(hours=3)))
        assert h1_bar_containing(h1_ts, bundle.ts[0]) == -1

    def test_a_sub_bar_in_a_missing_h1_hour_gives_minus_one(self):
        # h1_ts jumps 00:00 -> 02:00, so the 01:00 hour does not exist in
        # the walk frame. An M15 bar there has no zone validity to read
        # and must not borrow the 00:00 bar's.
        h1_ts = pd.DatetimeIndex(
            [pd.Timestamp("2024-01-01 00:00"), pd.Timestamp("2024-01-01 02:00")]
        ).to_numpy(dtype="datetime64[ns]")
        bundle = build_m15_bundle(m15_frame(8))
        assert h1_bar_containing(h1_ts, bundle.ts[0]) == 0
        assert h1_bar_containing(h1_ts, bundle.ts[4]) == -1

    def test_an_empty_frame_gives_minus_one(self):
        bundle = build_m15_bundle(m15_frame(4))
        assert h1_bar_containing(np.array([], dtype="datetime64[ns]"),
                                 bundle.ts[0]) == -1
