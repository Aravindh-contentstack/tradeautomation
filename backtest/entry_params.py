"""Runtime-overridable versions of the entry-geometry constants.

Why this exists
---------------
`backtest/entry_ob.py` holds SL_BUFFER_PIPS and MIN_R_PIPS as module
constants, and `backtest/entry_models.py` reads them at eight places to
build order and stop prices. That is the right shape for the live bot and
the walk-forward backtest, which both want one fixed answer.

The tuning study needs a different thing: the SAME code run several times
over the same years with a different buffer each time, to find which
buffer the market actually rewards. Rebinding a module constant between
runs is not safe (`entry_models` imported the value at import time, so a
rebind would be ignored), and threading a keyword argument down would
touch about a dozen signatures through _walk / _try_* / _resolve_* /
_pending_*.

A ContextVar gives the override without either cost. Nothing that does not
call `override()` sees any change, so `live/` and every existing test keep
today's behaviour exactly.

Contract
--------
Read through the accessor functions, never by importing the value:

    from backtest import entry_params
    buffer_price = entry_params.sl_buffer_pips() * pip_size

`entry_ob.SL_BUFFER_PIPS` and `entry_ob.MIN_R_PIPS` remain the defaults and
the single source of truth for what "unset" means, so there is still only
one number to change if the strategy's baseline moves.
"""

import contextlib
import contextvars
from dataclasses import dataclass, replace

from backtest.entry_ob import MIN_R_PIPS, SL_BUFFER_PIPS


@dataclass(frozen=True)
class EntryParams:
    """The geometry dials the tuning study sweeps.

    Frozen because an override is scoped to a `with` block: mutating the
    active params from inside one would leak past the block's exit, which
    is exactly the bug ContextVar is here to prevent.
    """

    sl_buffer_pips: float = SL_BUFFER_PIPS
    min_r_pips: float = MIN_R_PIPS


DEFAULTS = EntryParams()

_active = contextvars.ContextVar("entry_params", default=DEFAULTS)


def current():
    """The EntryParams in force for the calling context."""
    return _active.get()


def sl_buffer_pips():
    """Pips the stop sits beyond the zone edge.

    Note for anyone reading a swept result: two of the four entry models
    apply this to the pending ORDER price as well as the stop
    (entry_models._order_prices), so their R distance grows by twice the
    buffer while CE's grows by once. Widening the buffer therefore shifts
    the model mix, not just the stop distance.
    """
    return _active.get().sl_buffer_pips


def min_r_pips():
    """Smallest stop distance a setup may have before it is dropped."""
    return _active.get().min_r_pips


@contextlib.contextmanager
def override(**kwargs):
    """Runs the block with some entry params replaced.

    Unspecified fields keep their current value rather than resetting to
    DEFAULTS, so nested overrides compose:

        with override(sl_buffer_pips=4.0):
            with override(min_r_pips=1.0):
                ...  # buffer is still 4.0 here

    The token reset in the finally block is what makes this exception-safe
    and safe under the research runner's loop, where one buffer value
    raising must not silently poison the next.
    """
    unknown = set(kwargs) - {f for f in EntryParams.__dataclass_fields__}
    if unknown:
        raise TypeError("unknown entry param(s): %s" % ", ".join(sorted(unknown)))

    token = _active.set(replace(_active.get(), **kwargs))
    try:
        yield _active.get()
    finally:
        _active.reset(token)
