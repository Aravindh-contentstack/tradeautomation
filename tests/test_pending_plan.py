"""The live pending-order decision.

This is the only money-placing logic in the project that gets tested,
because it is the only one that does not import MetaTrader5. That is why
live/pending_plan.py exists as its own module: the wrappers in
mt5_connector.py are one dict and one order_send each, but deciding WHICH
orders to place and cancel has real branching in it and cannot be left to
a Windows-only integration run.

The cases below are the ones that cost money rather than opportunity:
an order left resting after its setup died, and an order whose price moved
without its stop moving with it.
"""

from live.pending_plan import PRICE_TOLERANCE_PIPS, one_per_zone, plan_pending

PIP = 0.0001


def signal(ob_row, price=1.1000, sl=1.1020, model="LC-1"):
    return {
        "ob_row": ob_row,
        "order_price": price,
        "sl": sl,
        "entry_model": model,
        "order_kind": "stop",
        "direction": "bearish",
        "r_distance": abs(sl - price),
    }


def record(ticket, price=1.1000, sl=1.1020, ob_row=None):
    r = {"ticket": ticket, "price": price, "sl": sl}
    if ob_row is not None:
        r["ob_row"] = ob_row
    return r


class TestPlacing:
    def test_a_new_signal_with_nothing_resting_is_placed(self):
        keep, cancel, place = plan_pending([signal(7)], {}, PIP)
        assert keep == {}
        assert cancel == []
        assert list(place) == [7]

    def test_nothing_wanted_and_nothing_resting_does_nothing(self):
        assert plan_pending([], {}, PIP) == ({}, [], {})


class TestKeeping:
    def test_an_unchanged_order_is_left_alone(self):
        # The common case, every 15 seconds. Cancelling and re-placing an
        # identical order would churn the broker and risk a gap with no
        # order resting at all.
        resting = {"7": record(111)}
        keep, cancel, place = plan_pending([signal(7)], resting, PIP)
        assert keep == resting
        assert cancel == []
        assert place == {}

    def test_a_sub_tick_difference_still_counts_as_unchanged(self):
        # Brokers round to the symbol's tick size, so a price read back can
        # differ from the one sent without anything having changed.
        drift = PRICE_TOLERANCE_PIPS * PIP / 2
        resting = {"7": record(111, price=1.1000 + drift)}
        keep, cancel, place = plan_pending([signal(7)], resting, PIP)
        assert list(keep) == ["7"]
        assert cancel == []


class TestCancelling:
    def test_an_order_whose_setup_died_is_cancelled(self):
        # The expensive failure if missed: a live order with no thesis
        # behind it, which fills whenever price happens to wander back.
        resting = {"7": record(111)}
        keep, cancel, place = plan_pending([], resting, PIP)
        assert keep == {}
        assert [r["ticket"] for r in cancel] == [111]
        assert place == {}

    def test_a_re_hosted_order_is_cancelled_and_replaced(self):
        # Both price AND stop move when an LC order re-hosts, so this must
        # be a replace. A partial modify would leave a live order with the
        # wrong stop, which is the one failure here that costs money.
        resting = {"7": record(111, price=1.1000, sl=1.1020)}
        moved = signal(7, price=1.0990, sl=1.1010)
        keep, cancel, place = plan_pending([moved], resting, PIP)
        assert keep == {}
        assert [r["ticket"] for r in cancel] == [111]
        assert place[7] is moved

    def test_a_moved_stop_alone_is_enough_to_replace(self):
        # Price unchanged, stop moved. Keeping the order would silently
        # leave the old risk attached.
        resting = {"7": record(111, price=1.1000, sl=1.1020)}
        keep, cancel, place = plan_pending(
            [signal(7, price=1.1000, sl=1.1050)], resting, PIP
        )
        assert [r["ticket"] for r in cancel] == [111]
        assert list(place) == [7]

    def test_a_resting_order_for_an_unknown_key_is_cancelled(self):
        # A corrupt or hand-edited state file must not leave an order
        # stranded at the broker.
        keep, cancel, place = plan_pending([signal(7)], {"junk": record(111)}, PIP)
        assert [r["ticket"] for r in cancel] == [111]
        assert list(place) == [7]


class TestOnePerZone:
    def test_only_the_first_signal_per_zone_is_wanted(self):
        first = signal(7, price=1.1000)
        second = signal(7, price=1.0990)
        assert one_per_zone([first, second]) == {7: first}

    def test_different_zones_are_independent(self):
        a, b = signal(7), signal(8)
        assert one_per_zone([a, b]) == {7: a, 8: b}

    def test_the_second_signal_on_a_zone_never_reaches_place(self):
        keep, cancel, place = plan_pending([signal(7), signal(7, price=1.09)],
                                           {}, PIP)
        assert list(place) == [7]
        assert place[7]["order_price"] == 1.1000

    def test_a_signal_without_an_ob_row_is_ignored(self):
        s = signal(7)
        del s["ob_row"]
        assert one_per_zone([s]) == {}


class TestMultipleZones:
    def test_keep_cancel_and_place_can_all_happen_at_once(self):
        resting = {
            "7": record(111),                       # unchanged -> keep
            "8": record(222, price=1.2000),         # moved     -> cancel
            "9": record(333),                       # died      -> cancel
        }
        signals = [signal(7), signal(8, price=1.2010), signal(10)]
        keep, cancel, place = plan_pending(signals, resting, PIP)
        assert list(keep) == ["7"]
        assert sorted(r["ticket"] for r in cancel) == [222, 333]
        assert sorted(place) == [8, 10]
