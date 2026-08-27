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
    """The two instruments fixed for live on 2026-08-26.

    Pinned deliberately. These are the numbers real orders are sized and
    stopped against, so a change to them should have to be a change to this
    test as well, not a quiet edit to a JSON file nobody re-reads.
    """
    expected = {
        "EUR_USD": {"sl_buffer_pips": 2.0, "total_threshold": 60.0,
                    "htf_threshold": 55.0, "tp_multiple": 2.5},
        "GBP_JPY": {"sl_buffer_pips": 0.5, "total_threshold": 55.0,
                    "htf_threshold": 55.0, "tp_multiple": 3.25},
    }
    for instrument, wanted in expected.items():
        path = "data/settings/%s/%s_settings_2026.json" % (instrument, instrument)
        settings = load_settings(path)
        for key, value in wanted.items():
            assert settings[key] == value, (instrument, key, settings[key])
