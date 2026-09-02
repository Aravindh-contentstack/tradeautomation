"""The live journal's three statuses, and which rows management still sees.

A row is written when an order is SENT, so most rows never become trades:
live/pending_plan.py replaces rather than modifies, so every re-host
cancels one order and writes another. Without a "cancelled" status those
rows stay "open" forever, and run_live.py's management loops keep asking
the broker about orders that stopped existing.

journal_live writes to a RELATIVE data/journal path, so every test here
chdirs into tmp_path first. Without that these would write into the repo's
own git-tracked journal tree.
"""

import pandas as pd
import pytest

from live import journal_live

INSTRUMENT = "EUR_USD"
YEAR = 2026


@pytest.fixture(autouse=True)
def _isolate_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def signal(entry_time="2026-09-02 08:15:00"):
    return {
        "entry_time": pd.Timestamp(entry_time, tz="UTC"),
        "session": "london",
        "direction": "bullish",
        "entry_price": 1.1000,
        "sl": 1.0980,
        "r_distance": 0.0020,
        "probability": 0.62,
    }


def place(ticket, entry_time="2026-09-02 08:15:00"):
    journal_live.append_open_trade(INSTRUMENT, signal(entry_time), 1.1060, ticket, 0.5)


def tickets(rows):
    return [row["ticket"] for row in rows]


class TestCancelling:
    def test_a_cancelled_order_drops_out_of_open_trades(self):
        place(111)
        assert tickets(journal_live.open_trades(INSTRUMENT, YEAR)) == [111]

        assert journal_live.mark_cancelled(INSTRUMENT, YEAR, 111) is True
        assert journal_live.open_trades(INSTRUMENT, YEAR) == []

    def test_the_row_is_kept_and_labelled_rather_than_deleted(self):
        # The row is evidence that a setup was found and an order rested,
        # which is exactly what the live-vs-backtest comparison needs. It
        # just must not be counted as a trade.
        place(111)
        journal_live.mark_cancelled(INSTRUMENT, YEAR, 111)

        df = pd.read_csv(journal_live.journal_path(INSTRUMENT, YEAR))
        assert len(df) == 1
        assert df["status"].iloc[0] == "cancelled"
        assert df["exit_reason"].iloc[0] == "cancelled"
        assert df["result"].isna().all()

    def test_only_the_named_ticket_is_touched(self):
        place(111)
        place(222)
        journal_live.mark_cancelled(INSTRUMENT, YEAR, 111)
        assert tickets(journal_live.open_trades(INSTRUMENT, YEAR)) == [222]

    def test_an_unknown_ticket_is_a_no_op(self):
        # run_live.py sweeps both the current and previous year without
        # knowing which one holds the order, so the miss is the normal case.
        place(111)
        assert journal_live.mark_cancelled(INSTRUMENT, YEAR, 999) is False
        assert tickets(journal_live.open_trades(INSTRUMENT, YEAR)) == [111]

    def test_marking_against_a_year_with_no_journal_at_all_is_a_no_op(self):
        assert journal_live.mark_cancelled(INSTRUMENT, 2019, 111) is False


class TestCancelledNeverHidesALiveTrade:
    def test_a_closed_trade_is_not_reopened_or_relabelled_by_a_later_cancel(self):
        # The nightmare this status could cause: a FILLED order labelled
        # cancelled would vanish from open_trades() and strand a real
        # position with no 19:00 checkpoint and no Friday close. The
        # protection lives in run_live.py (mark_cancelled is only reached
        # having seen the order resting AND the cancel succeed), but the
        # ordering here must also not corrupt a finished row.
        place(111)
        journal_live.close_trade(
            INSTRUMENT, YEAR, 111, "win", pd.Timestamp("2026-09-02 19:00:00", tz="UTC"), "tp"
        )
        df = pd.read_csv(journal_live.journal_path(INSTRUMENT, YEAR))
        assert df["status"].iloc[0] == "closed"
        assert df["result"].iloc[0] == "win"

        # Neither status leaves the row visible to management.
        assert journal_live.open_trades(INSTRUMENT, YEAR) == []

    def test_management_keeps_seeing_an_order_that_was_not_cancelled(self):
        place(111)
        journal_live.mark_cancelled(INSTRUMENT, YEAR, 222)  # a different ticket
        assert tickets(journal_live.open_trades(INSTRUMENT, YEAR)) == [111]
