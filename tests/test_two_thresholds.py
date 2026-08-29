"""The two-threshold settings: htf_threshold and total_threshold.

The HTF gate is fixed permissive and the total gate is searched. Both
halves of that are asserted here, along with the thing most likely to
break quietly: five years of settings files on disk were written before
either name existed, and they all still have to load and mean the same
thing they did.
"""

import json

import pytest

from backtest.analysis import (
    HTF_GATE_QUANTILE,
    MIN_TRADES_FOR_CONSIDERATION,
    htf_gate_threshold,
    recommend_global_settings,
)
from backtest.settings import (
    DEFAULT_SETTINGS,
    apply_settings,
    is_taken,
    load_settings,
)

PIP = 0.0001


def candidate(total, htf=None, sl_size=0.0020, max_r=3.0, terminal_r=1.0):
    """One journal-shaped candidate row."""
    return {
        "probability": total if htf is None else htf,
        "total_probability": total,
        "htf_probability": total if htf is None else htf,
        "sl_size": sl_size,
        "max_r_reached": max_r,
        "terminal_r": terminal_r,
    }


class TestLoadingLegacyFiles:
    def test_a_file_with_only_threshold_maps_onto_total(self, tmp_path):
        # The shape every stored settings file has. It gated placing the
        # order, so total_threshold is where it belongs.
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"threshold": 55, "tp_multiple": 2.5,
                                    "max_sl_size_pips": 23.7}))
        s = load_settings(str(path))
        assert s["total_threshold"] == 55
        assert s["threshold"] == 55
        # No pre-gate, which is the permissive reading of a file that
        # never had one.
        assert s["htf_threshold"] is None

    def test_the_nested_shape_carries_both_names(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({
            "recommended": {"htf_threshold": -10.0, "total_threshold": 12.0,
                            "tp_multiple": 3.0, "max_sl_size_pips": 20.0},
            "applied": {}, "candidate_count": 40, "taken_count": 9,
        }))
        s = load_settings(str(path))
        assert s["htf_threshold"] == -10.0
        assert s["total_threshold"] == 12.0

    def test_a_literal_null_file_still_bootstraps(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("null")
        assert load_settings(str(path)) == DEFAULT_SETTINGS

    def test_a_missing_file_bootstraps(self, tmp_path):
        assert load_settings(str(tmp_path / "nope.json")) == DEFAULT_SETTINGS

    def test_an_explicit_total_is_not_overwritten_by_legacy(self, tmp_path):
        # Both present and disagreeing: total_threshold wins, because the
        # legacy key is only ever a fallback.
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"threshold": 55, "total_threshold": 12}))
        assert load_settings(str(path))["total_threshold"] == 12


class TestIsTaken:
    def test_it_reads_total_probability(self):
        settings = {"total_threshold": 20.0}
        assert is_taken(candidate(25.0), settings, PIP)
        assert not is_taken(candidate(15.0), settings, PIP)

    def test_a_signal_without_total_falls_back_to_probability(self):
        # The pre-M15 path. Nothing sets total_probability there, and the
        # comparison must be unchanged rather than crashing or admitting
        # everything.
        settings = {"total_threshold": 20.0}
        assert is_taken({"probability": 25.0, "r_distance": 0.002}, settings, PIP)
        assert not is_taken({"probability": 15.0, "r_distance": 0.002},
                            settings, PIP)

    def test_no_threshold_means_no_filter(self):
        assert is_taken(candidate(-99.0), {"total_threshold": None}, PIP)

    def test_the_legacy_threshold_key_still_gates(self):
        # A settings dict built by hand or by very old code.
        assert not is_taken(candidate(15.0), {"threshold": 20.0}, PIP)

    def test_no_restricted_entry_models_means_no_filter(self):
        signal = dict(candidate(25.0), entry_model="LC-1")
        assert is_taken(signal, {"total_threshold": 20.0}, PIP)

    def test_a_restricted_entry_model_is_excluded(self):
        signal = dict(candidate(25.0), entry_model="LC-1")
        settings = {"total_threshold": 20.0, "restricted_entry_models": ["LC-1"]}
        assert not is_taken(signal, settings, PIP)

    def test_an_unrestricted_entry_model_still_passes(self):
        signal = dict(candidate(25.0), entry_model="LC-2A")
        settings = {"total_threshold": 20.0, "restricted_entry_models": ["LC-1"]}
        assert is_taken(signal, settings, PIP)


class TestHtfGate:
    def test_it_sits_at_the_configured_low_quantile(self):
        cands = [candidate(50.0, htf=float(i)) for i in range(100)]
        gate = htf_gate_threshold(cands)
        assert gate == pytest.approx(HTF_GATE_QUANTILE * 99, abs=1.0)

    def test_it_is_permissive_by_construction(self):
        # The property that matters, stated directly: the gate must admit
        # the large majority, because total_threshold does the real work
        # and there are only about 43 setups a year to fit.
        cands = [candidate(50.0, htf=float(i)) for i in range(100)]
        gate = htf_gate_threshold(cands)
        survivors = [c for c in cands if c["htf_probability"] >= gate]
        assert len(survivors) >= 85

    def test_no_htf_scores_means_no_gate(self):
        # The pre-M15 path. None means no gate, not zero.
        assert htf_gate_threshold([{"probability": 5.0}]) is None
        assert htf_gate_threshold([]) is None


class TestSearch:
    def test_it_searches_total_and_fixes_htf(self):
        cands = [candidate(float(i), htf=float(i)) for i in range(40)]
        best = recommend_global_settings(cands, PIP)
        assert best is not None
        # The searched dial comes off the total distribution.
        assert best["total_threshold"] == best["threshold"]
        # The fixed one comes off htf_gate_threshold, not the grid.
        assert best["htf_threshold"] == htf_gate_threshold(cands)

    def test_too_few_candidates_recommends_nothing(self):
        cands = [candidate(float(i)) for i in range(MIN_TRADES_FOR_CONSIDERATION - 1)]
        assert recommend_global_settings(cands, PIP) is None

    def test_it_grids_on_total_not_htf(self):
        # HTF scores are deliberately uninformative here while the totals
        # separate winners from losers. A search reading the wrong column
        # cannot find the split.
        winners = [candidate(90.0, htf=1.0, max_r=3.0, terminal_r=3.0)
                   for _ in range(12)]
        losers = [candidate(10.0, htf=1.0, max_r=0.0, terminal_r=-1.0)
                  for _ in range(12)]
        best = recommend_global_settings(winners + losers, PIP)
        assert best is not None
        assert best["total_threshold"] > 10.0


class TestOneTradePerOb:
    """The user's rule: at most one TAKEN trade per order block.

    Enforced in apply_settings rather than find_signals because "taken" is
    what the rule counts, and only the settings layer knows it. A candidate
    that never cleared the threshold costs the zone nothing.
    """

    def rows(self, *specs):
        """specs are (ob_row, total_probability) pairs, in order."""
        out = []
        for ob_row, total in specs:
            row = candidate(total)
            row["ob_row"] = ob_row
            row["r_distance"] = 0.0020
            out.append(row)
        return out

    def test_the_first_qualifying_candidate_wins(self):
        signals = self.rows((7, 60.0), (7, 90.0), (7, 80.0))
        apply_settings(signals, {"total_threshold": 50.0}, PIP)
        assert [s["taken"] for s in signals] == [True, False, False]

    def test_a_blocked_candidate_does_not_consume_the_zone(self):
        # The first one fails the threshold, so the zone is still available
        # and the second one takes it. This is the half that would be wrong
        # if the rule lived in find_signals, which cannot see `taken`.
        signals = self.rows((7, 10.0), (7, 90.0))
        apply_settings(signals, {"total_threshold": 50.0}, PIP)
        assert [s["taken"] for s in signals] == [False, True]

    def test_different_zones_are_independent(self):
        signals = self.rows((7, 90.0), (8, 90.0), (7, 90.0))
        apply_settings(signals, {"total_threshold": 50.0}, PIP)
        assert [s["taken"] for s in signals] == [True, True, False]

    def test_it_is_first_come_not_best_of(self):
        # Picking the highest scorer would need the zone's whole life in
        # hand before deciding, which the live bot never has.
        signals = self.rows((7, 60.0), (7, 99.0))
        apply_settings(signals, {"total_threshold": 50.0}, PIP)
        assert signals[0]["taken"] is True
        assert signals[1]["taken"] is False

    def test_a_signal_with_no_ob_row_is_not_pooled(self):
        # The pre-M15 path, or any candidate not built off a zone.
        signals = [candidate(90.0), candidate(90.0)]
        for s in signals:
            s["r_distance"] = 0.0020
        apply_settings(signals, {"total_threshold": 50.0}, PIP)
        assert [s["taken"] for s in signals] == [True, True]
