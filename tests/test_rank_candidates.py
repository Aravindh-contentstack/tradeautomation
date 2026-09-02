"""The cross-instrument concurrency caps.

Unlike plan_pending's per-instrument decisions, this ranks candidates from
EVERY instrument in the same sweep against each other and against whatever
is already open on the account, which is why live/run_live.py's main() has
to gather every pair's candidates before placing any of them.

The cases below are the ones that cost money rather than opportunity: more
risk on the book than the cap allows, and a whole book concentrated on one
symbol while appearing to respect the cap.
"""

from live.pending_plan import rank_candidates

CAP = 4
PER_INSTRUMENT = 1


def candidate(instrument, probability, is_rehost=False):
    return {
        "instrument": instrument,
        "signal": {"total_probability": probability},
        "is_rehost": is_rehost,
    }


def rank(candidates, occupied=None, cap=CAP, per_instrument=PER_INSTRUMENT):
    return rank_candidates(candidates, occupied or {}, cap, per_instrument)


def names(candidates):
    return [c["instrument"] for c in candidates]


class TestTheAccountWideCap:
    def test_fewer_candidates_than_the_cap_all_win(self):
        winners, losers = rank([candidate("EUR_USD", 60), candidate("GBP_USD", 55)])
        assert names(winners) == ["EUR_USD", "GBP_USD"]
        assert losers == []

    def test_the_highest_probability_candidates_win_the_slots(self):
        winners, losers = rank(
            [candidate("EUR_USD", 60), candidate("GBP_USD", 80), candidate("USD_JPY", 55)],
            cap=2,
        )
        assert names(winners) == ["GBP_USD", "EUR_USD"]
        assert names(losers) == ["USD_JPY"]

    def test_trades_already_on_the_book_take_up_slots(self):
        winners, losers = rank(
            [candidate("EUR_USD", 60), candidate("GBP_USD", 80)],
            occupied={"AUD_JPY": 2, "NZD_CAD": 1},  # 3 of 4 slots gone
        )
        assert names(winners) == ["GBP_USD"]
        assert names(losers) == ["EUR_USD"]

    def test_a_full_book_rejects_everything(self):
        candidates = [candidate("EUR_USD", 90)]
        winners, losers = rank(candidates, occupied={"AUD_JPY": 4})
        assert winners == []
        assert losers == candidates

    def test_an_over_full_book_still_rejects_everything(self):
        # A manual trade can push the account past the cap. Slots must
        # clamp at zero rather than going negative.
        candidates = [candidate("EUR_USD", 90)]
        winners, losers = rank(candidates, occupied={"AUD_JPY": 9})
        assert winners == []
        assert losers == candidates

    def test_ties_keep_input_order(self):
        winners, _ = rank([candidate("EUR_USD", 70), candidate("GBP_USD", 70)], cap=1)
        assert names(winners) == ["EUR_USD"]

    def test_no_candidates_is_a_no_op(self):
        assert rank([]) == ([], [])


class TestThePerInstrumentCap:
    def test_one_instrument_cannot_take_every_slot(self):
        # Four EUR_USD zones triggering at once is not a diversified book,
        # it is one directional bet at four times the size.
        winners, losers = rank([candidate("EUR_USD", p) for p in (90, 80, 70, 60)])
        assert names(winners) == ["EUR_USD"]
        assert len(losers) == 3

    def test_slots_pass_to_the_next_instrument_instead(self):
        winners, _ = rank([
            candidate("EUR_USD", 90),
            candidate("EUR_USD", 85),
            candidate("GBP_USD", 50),
        ])
        assert names(winners) == ["EUR_USD", "GBP_USD"]

    def test_an_instrument_already_holding_its_limit_is_rejected(self):
        winners, losers = rank(
            [candidate("EUR_USD", 90), candidate("GBP_USD", 50)],
            occupied={"EUR_USD": 1},
        )
        assert names(winners) == ["GBP_USD"]
        assert names(losers) == ["EUR_USD"]


class TestRehostsKeepTheirSlot:
    def test_a_rehost_outranks_a_higher_scoring_newcomer(self):
        # The re-host held this slot before the sweep and only vacated it
        # because plan_pending replaces rather than modifies. Letting a
        # newcomer take it would leave the pair with nothing resting -
        # worse than before the sweep started.
        winners, losers = rank(
            [candidate("GBP_USD", 95), candidate("EUR_USD", 30, is_rehost=True)],
            occupied={"AUD_JPY": 3},  # one slot free
        )
        assert names(winners) == ["EUR_USD"]
        assert names(losers) == ["GBP_USD"]

    def test_rehosts_are_still_bounded_by_the_cap(self):
        winners, losers = rank(
            [candidate("EUR_USD", 60, is_rehost=True),
             candidate("GBP_USD", 50, is_rehost=True)],
            occupied={"AUD_JPY": 3},
        )
        assert names(winners) == ["EUR_USD"]
        assert names(losers) == ["GBP_USD"]

    def test_rehosts_rank_among_themselves_by_probability(self):
        winners, _ = rank(
            [candidate("EUR_USD", 40, is_rehost=True),
             candidate("GBP_USD", 80, is_rehost=True)],
            cap=1,
        )
        assert names(winners) == ["GBP_USD"]

    def test_a_full_book_of_rehosts_leaves_nothing_for_newcomers(self):
        # The realistic steady state: the book is full, several orders
        # re-host on the same M15 boundary, and their own cancellations are
        # the only reason slots appear free at all.
        winners, losers = rank(
            [candidate("EUR_USD", 20, is_rehost=True),
             candidate("GBP_USD", 25, is_rehost=True),
             candidate("USD_JPY", 99),
             candidate("AUD_USD", 98)],
            occupied={"NZD_CAD": 2},  # the two re-hosts already cancelled
        )
        assert sorted(names(winners)) == ["EUR_USD", "GBP_USD"]
        assert sorted(names(losers)) == ["AUD_USD", "USD_JPY"]
