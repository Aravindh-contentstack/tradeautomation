"""The two connector reads that guard risk limits must fail LOUDLY.

live/mt5_connector.py is otherwise deliberately untested (see
tests/test_pending_plan.py's docstring): its functions are one dict and
one order_send each, they fail loudly, and MetaTrader5 is Windows-only so
they cannot run here at all.

These two are the exception, because they have real logic AND because
their wrong answer is silent. positions_get()/orders_get() return None on
failure, which is indistinguishable from "nothing is open". Read as zero,
that frees every slot in the concurrency cap and lets a whole second book
of 1%-risk trades on top of the existing one, and it makes the daily-loss
breakers see a flat day during a losing one. An exception costs one
skipped cycle. That asymmetry is the whole point, so it gets a test.

MetaTrader5 is stubbed into sys.modules to make the module importable off
Windows. Nothing else here mocks the broker, and nothing else should.
"""

import sys
import types

import pytest


class FakeMT5:
    """Only what mt5_connector touches at import time, plus the two reads."""

    TIMEFRAME_D1 = 1
    TIMEFRAME_H4 = 2
    TIMEFRAME_H1 = 3
    TIMEFRAME_M15 = 4
    ORDER_TYPE_BUY_STOP = 5
    ORDER_TYPE_SELL_STOP = 6
    ORDER_TYPE_BUY_LIMIT = 7
    ORDER_TYPE_SELL_LIMIT = 8
    DEAL_ENTRY_OUT = 1

    def __init__(self):
        self.positions = []
        self.orders = []
        self.deals = []

    def positions_get(self, **kwargs):
        return self.positions

    def orders_get(self, **kwargs):
        return self.orders

    def history_deals_get(self, *args, **kwargs):
        return self.deals

    def last_error(self):
        return (-1, "stubbed failure")


def trade(magic, profit=0.0):
    return types.SimpleNamespace(magic=magic, profit=profit, entry=FakeMT5.DEAL_ENTRY_OUT)


@pytest.fixture
def connector(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.delitem(sys.modules, "live.mt5_connector", raising=False)
    import live.mt5_connector as module
    monkeypatch.delitem(sys.modules, "live.mt5_connector", raising=False)
    module.mt5 = fake
    return module, fake


class TestConcurrencyCountFailsClosed:
    def test_unreadable_positions_raise_rather_than_counting_zero(self, connector):
        module, fake = connector
        fake.positions = None
        with pytest.raises(RuntimeError, match="positions_get"):
            module.open_and_pending_by_magic([1, 2])

    def test_unreadable_orders_raise_rather_than_counting_zero(self, connector):
        module, fake = connector
        fake.orders = None
        with pytest.raises(RuntimeError, match="orders_get"):
            module.open_and_pending_by_magic([1, 2])

    def test_counts_are_per_magic_and_ignore_other_magics(self, connector):
        module, fake = connector
        fake.positions = [trade(101), trade(101), trade(999)]  # 999 = a manual trade
        fake.orders = [trade(102)]
        assert module.open_and_pending_by_magic([101, 102]) == {101: 2, 102: 1}

    def test_a_resting_order_occupies_a_slot_before_it_fills(self, connector):
        # It becomes real risk the moment price reaches it, with the bot
        # never consulted, so it has to be counted while still pending.
        module, fake = connector
        fake.orders = [trade(101)]
        assert module.open_and_pending_by_magic([101]) == {101: 1}

    def test_an_empty_account_counts_zero_for_every_magic(self, connector):
        module, fake = connector
        assert module.open_and_pending_by_magic([101, 102]) == {101: 0, 102: 0}


class TestPnlFailsClosed:
    def test_unreadable_positions_raise_rather_than_reporting_no_loss(self, connector):
        module, fake = connector
        fake.positions = None
        with pytest.raises(RuntimeError, match="positions_get"):
            module.pnl_by_magic("2026-09-02")

    def test_unreadable_deal_history_raises(self, connector):
        module, fake = connector
        fake.deals = None
        with pytest.raises(RuntimeError, match="history_deals_get"):
            module.pnl_by_magic("2026-09-02")

    def test_floating_and_realised_are_summed_per_magic(self, connector):
        module, fake = connector
        fake.positions = [trade(101, -50.0)]
        fake.deals = [trade(101, -25.0), trade(102, 10.0)]
        pnl = module.pnl_by_magic("2026-09-02")
        assert pnl[101] == -75.0
        assert pnl[102] == 10.0

    def test_every_magic_is_included_so_the_account_total_is_the_sum(self, connector):
        # The prop firm measures the whole account, manual trades included,
        # so the account-wide breaker must see them too.
        module, fake = connector
        fake.positions = [trade(101, -50.0), trade(999, -100.0)]
        assert sum(module.pnl_by_magic("2026-09-02").values()) == -150.0
