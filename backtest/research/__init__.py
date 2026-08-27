"""Offline tuning study, kept apart from the walk-forward engine.

The engine in backtest/runner.py answers "what would this have returned
live, knowing only what was knowable at the time". This package answers a
different question: "over the whole history at once, which settings should
we have been using". The two must not be confused, so they do not share a
module.

Nothing here writes to data/journal, data/weights or data/settings. Study
output goes to data/research/ and the live path is untouched.
"""
