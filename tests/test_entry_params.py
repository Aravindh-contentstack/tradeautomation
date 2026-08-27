"""The runtime override for the entry-geometry constants.

The tuning study sweeps the stop-loss buffer, which means running the same
entry models several times over the same years with a different buffer
each time. entry_params is how that happens without a module constant
being rebound underneath code that already imported it.

The property that matters most here is the LAST one: a run that does not
call override() must behave exactly as it did before this module existed,
because live/ and the walk-forward backtest are both such runs.
"""

import pytest

from backtest import entry_params
from backtest.entry_ob import MIN_R_PIPS, SL_BUFFER_PIPS


class TestDefaults:
    def test_unset_reads_the_shipped_constants(self):
        """entry_ob stays the single source of truth for the baseline, so
        changing the strategy's default is still a one-line edit there.
        """
        assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS
        assert entry_params.min_r_pips() == MIN_R_PIPS


class TestOverride:
    def test_override_applies_inside_the_block(self):
        with entry_params.override(sl_buffer_pips=5.0):
            assert entry_params.sl_buffer_pips() == 5.0

    def test_override_is_undone_on_exit(self):
        with entry_params.override(sl_buffer_pips=5.0):
            pass
        assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS

    def test_override_is_undone_after_an_exception(self):
        """The buffer sweep is a loop. One value raising must not leave the
        next one running against a poisoned buffer, which would corrupt a
        result silently rather than failing loudly.
        """
        with pytest.raises(RuntimeError):
            with entry_params.override(sl_buffer_pips=5.0):
                raise RuntimeError("boom")
        assert entry_params.sl_buffer_pips() == SL_BUFFER_PIPS

    def test_unspecified_fields_keep_their_current_value(self):
        """Nested overrides compose rather than resetting each other to the
        shipped defaults.
        """
        with entry_params.override(sl_buffer_pips=5.0):
            with entry_params.override(min_r_pips=1.0):
                assert entry_params.sl_buffer_pips() == 5.0
                assert entry_params.min_r_pips() == 1.0
            assert entry_params.min_r_pips() == MIN_R_PIPS

    def test_an_unknown_param_raises_rather_than_being_ignored(self):
        """A typo'd keyword silently doing nothing would produce a whole
        sweep of identical results that look like a flat response curve.
        """
        with pytest.raises(TypeError):
            with entry_params.override(sl_bufffer_pips=5.0):
                pass
