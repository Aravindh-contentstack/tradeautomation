"""The stop buffer travels from the settings file into trade geometry.

This is the seam that let a per-instrument buffer exist at all. Before it,
SL_BUFFER_PIPS was a module constant, so every instrument traded the same
2 pips whatever its tuning said -- and the failure was silent: orders would
have been placed at prices the tuning never tested, with no error anywhere.

These tests pin the two halves of that: the settings loader always yields a
usable buffer whatever shape the file is in, and an override actually moves
the stop.
"""

import json

from backtest import entry_params
from backtest.entry_ob import SL_BUFFER_PIPS
from backtest.settings import DEFAULT_SETTINGS, load_settings


def test_default_settings_carry_the_shipped_buffer():
    """Absent from a file means the shipped default, never "no buffer".

    Unlike the thresholds, where None legitimately means "no filter", a
    stop has to sit somewhere. Defaulting this to None would put the stop
    exactly on the zone edge.
    """
    assert DEFAULT_SETTINGS["sl_buffer_pips"] == SL_BUFFER_PIPS


def test_settings_file_without_the_key_still_loads(tmp_path):
    """Every settings file written before this key existed keeps working."""
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "applied": {},
        "recommended": {"total_threshold": 12.6, "tp_multiple": 2.0},
    }))
    settings = load_settings(str(path))
    assert settings["sl_buffer_pips"] == SL_BUFFER_PIPS
    assert settings["tp_multiple"] == 2.0


def test_settings_file_can_set_a_per_instrument_buffer(tmp_path):
    path = tmp_path / "new.json"
    path.write_text(json.dumps({
        "applied": {},
        "recommended": {"sl_buffer_pips": 0.5, "total_threshold": 55.0},
    }))
    assert load_settings(str(path))["sl_buffer_pips"] == 0.5


def test_override_moves_the_buffer_and_restores_it():
    """The contract the live runner depends on: in force inside the block,
    back to the default outside it, so one instrument's buffer can never
    leak into the next poll.
    """
    assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS
    with entry_params.override(sl_buffer_pips=0.5):
        assert entry_params.sl_buffer_pips() == 0.5
    assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS


def test_override_restores_even_when_the_block_raises():
    try:
        with entry_params.override(sl_buffer_pips=8.0):
            raise ValueError("boom")
    except ValueError:
        pass
    assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS


def test_stored_live_settings_match_what_was_chosen():
    """The 27 pairs hand-picked for live on 2026-08-29, from a manual
    review of backtest data across the whole portfolio
    (per-pair-trade-setttings.csv), superseding the narrower 2026-08-26
    EUR_USD/GBP_JPY picks.

    Pinned deliberately. These are the numbers real orders are sized and
    stopped against, so a change to them should have to be a change to this
    test as well, not a quiet edit to a JSON file nobody re-reads. The CSV's
    last row, labeled "CAD CHF", does not exist anywhere in this repo and
    was confirmed with the user to mean CHF_JPY (see
    scripts/apply_pair_settings.py's NAME_FIX).
    """
    expected = {
        "EUR_USD": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 7.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "GBP_USD": {"sl_buffer_pips": 1.0, "total_threshold": 50.0,
                    "htf_threshold": 57.0, "tp_multiple": 5.25,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "GBP_JPY": {"sl_buffer_pips": 1.0, "total_threshold": 50.0,
                    "htf_threshold": 56.0, "tp_multiple": 2.75,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "AUD_USD": {"sl_buffer_pips": 2.0, "total_threshold": 50.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.0,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "EUR_JPY": {"sl_buffer_pips": 1.0, "total_threshold": 50.0,
                    "htf_threshold": 53.0, "tp_multiple": 2.25,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "NZD_USD": {"sl_buffer_pips": 1.0, "total_threshold": 55.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.0,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "USD_CAD": {"sl_buffer_pips": 0.5, "total_threshold": 55.0,
                    "htf_threshold": 54.0, "tp_multiple": 3.0,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "USD_CHF": {"sl_buffer_pips": 2.0, "total_threshold": 55.0,
                    "htf_threshold": 50.0, "tp_multiple": 7.5,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "USD_JPY": {"sl_buffer_pips": 2.0, "total_threshold": 55.0,
                    "htf_threshold": 50.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "EUR_GBP": {"sl_buffer_pips": 2.0, "total_threshold": 65.0,
                    "htf_threshold": 50.0, "tp_multiple": 3.0,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "EUR_CHF": {"sl_buffer_pips": 1.0, "total_threshold": 60.0,
                    "htf_threshold": 50.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "EUR_AUD": {"sl_buffer_pips": 1.0, "total_threshold": 55.0,
                    "htf_threshold": 50.0, "tp_multiple": 2.0,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "EUR_CAD": {"sl_buffer_pips": 0.5, "total_threshold": 55.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "EUR_NZD": {"sl_buffer_pips": 0.5, "total_threshold": 50.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": ["LC-1"]},
        "GBP_CHF": {"sl_buffer_pips": 0.5, "total_threshold": 55.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.0,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": ["LC-1"]},
        "GBP_AUD": {"sl_buffer_pips": 1.0, "total_threshold": 50.0,
                    "htf_threshold": 40.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": ["LC-2A"]},
        "GBP_CAD": {"sl_buffer_pips": 1.0, "total_threshold": 50.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.75,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": ["LC-2A"]},
        "GBP_NZD": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "AUD_JPY": {"sl_buffer_pips": 2.0, "total_threshold": 40.0,
                    "htf_threshold": 45.0, "tp_multiple": 3.75,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "AUD_CAD": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.25,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "AUD_CHF": {"sl_buffer_pips": 2.0, "total_threshold": 55.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "AUD_NZD": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 5.0,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "NZD_JPY": {"sl_buffer_pips": 2.0, "total_threshold": 50.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london"],
                    "restricted_entry_models": None},
        "NZD_CAD": {"sl_buffer_pips": 3.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.5,
                    "allowed_sessions": ["ny"],
                    "restricted_entry_models": None},
        "NZD_CHF": {"sl_buffer_pips": 0.5, "total_threshold": 50.0,
                    "htf_threshold": 60.0, "tp_multiple": 2.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
        "CAD_JPY": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 50.0, "tp_multiple": 2.75,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": ["LC-1"]},
        "CHF_JPY": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.5,
                    "allowed_sessions": ["london", "ny"],
                    "restricted_entry_models": None},
    }
    for instrument, wanted in expected.items():
        path = "data/settings/%s/%s_settings_2026.json" % (instrument, instrument)
        settings = load_settings(path)
        for key, value in wanted.items():
            assert settings[key] == value, (instrument, key, settings[key])
