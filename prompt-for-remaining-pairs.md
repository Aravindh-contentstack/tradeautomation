# Task: backtest the 7 remaining forex pairs and add them to the settings explorer

## What I want

Backtest these seven pairs over **2015 to 2025** and add every one of them to the
existing settings explorer artifact, so I can tune each pair by hand the way I
already did for EUR/USD and GBP/JPY:

    AUD_USD   EUR_JPY   GBP_USD   NZD_USD   USD_CAD   USD_CHF   USD_JPY

**Deliberately excluded:**

- **XAU_USD** (gold). It has data and works, but metals are a later phase. Do not
  add it.
- **NAS100.** It has no M15 data and no pip size, because it is a point-based
  index rather than a pip-quoted pair. The engine produces zero candidates for it
  by design. Do not try to make it work.

Everything must land in the **same artifact**, at the same URL, alongside the two
pairs already there. Nine instruments total when you are done.

---

## Context: this is an extension, not a new build

The whole pipeline already exists and already works for two pairs. Your job is to
run it for seven more and extend the page's pair switcher. **Do not redesign
anything, do not re-tune anything, and do not change the backtest method.**

The strategy this serves: cherry-pick hard on every instrument (tight probability
thresholds, so few trades per pair), and make up the scarcity by trading many
instruments. A pair producing only a handful of trades over eleven years is the
INTENDED shape here, not a problem to solve. Do not flag low per-pair trade counts
as a reason to loosen anything.

### Already done, do not repeat

- The engine (`backtest/`) is complete and instrument-parameterised.
- **Every pip size you need is already configured** in
  `backtest/instruments.py`. Verified: all seven pairs are present and correct
  (0.0001 for the USD majors, 0.01 for the JPY crosses). There is nothing to
  identify, add, or guess. If you find yourself writing a pip size, stop, because
  you are solving a problem that does not exist.
- **All seven pairs have complete D, H4, H1 and M15 data** in `data/raw/`, all
  starting well before 2015. Verified.
- EUR/USD and GBP/JPY are already in the artifact and already have live settings
  fixed. **Do not touch their settings, and do not re-run their tuning.**

---

## How the pipeline works, and the one idea it rests on

Read `scripts/build_explorer_data.py` first. Its docstring explains this, but the
short version, because it explains why the page is shaped the way it is:

Two of the four settings can be recomputed **exactly** in the browser, and two
cannot.

- **Take profit is free.** The forward walk in `backtest/simulate.py` carries no
  take-profit at all, so `apply_tp` reduces to "if the trade's high-water mark
  reached the target it filled there, otherwise the walk's own ending stands".
  Two stored numbers and a comparison.
- **HTF threshold is free.** `find_signals` simply skips a candidate scoring too
  low, and a skipped candidate never consumes its order block. Filtering the
  stored rows BEFORE re-running the one-trade-per-order-block rule reproduces it
  exactly.
- **SL buffer is not free.** It moves the stop AND the pending order price, so it
  changes entry prices, stop distances, and which setups exist at all.
- **Trade-strength threshold is not free.** It is not merely a filter: it feeds
  the live-probability breakeven rule, so it changes trade OUTCOMES, not just
  membership.

So the two "not free" settings are precomputed as a grid of real backtest runs
(7 buffers x 9 thresholds = 63 runs per instrument) and the two free ones are
live controls in the browser.

---

## Step 1: build the data

`scripts/build_explorer_data.py` already loops instruments and accepts them as
arguments. Add the seven pairs to its `INSTRUMENTS` tuple (keep EUR_USD and
GBP_JPY, and keep XAU_USD out), then run it:

    ./.venv/bin/python scripts/build_explorer_data.py

**Run it in the background and expect it to take 45 to 60 minutes.** Each
instrument costs roughly a 100 to 125 second pipeline build plus about 215
seconds of grid runs. Do not sit polling it. Start it, do the template work in
step 2 while it runs, and check back.

Everything else about the method stays exactly as it is:

- **Weights fixed at 1.0**, no learning. (Measured on GBP/JPY: learning was
  identical on the years it learned from and two-thirds worse on held-back years.
  Do not turn it on.)
- `recheck_all=True`, which is what lets the browser re-derive the
  one-trade-per-order-block rule after its own HTF filter and still be exact.
- Years 2015 to 2025 for every pair, so all instruments stay directly comparable.

Output is `data/research/explorer.json`.

### Watch the payload

Two instruments is 1.56 MB, so nine will be around 7 MB. The artifact limit is
16 MB, so it fits, but the page may feel slow to load.

**Only if it becomes a real problem**, there is an easy 40% saving available: six
of the twenty stored fields are formatted date and time strings (date, day, start
time, end time, duration, end date) that could be derived in the browser from the
entry and exit timestamps instead. If you do this, note the trap: those times are
**London civil time, DST-aware**, so the browser must use
`toLocaleString` with `timeZone: "Europe/London"`, not UTC and not the viewer's
local zone. Measure first, optimise only if needed.

---

## Step 2: extend the page

`scripts/explorer_template.html` already has a working pair switcher, per-pair
state isolation, and per-pair copy. It needs **one thing** from you: a `COPY`
entry for each new pair.

Find the `COPY` object in the page's script block. Each pair needs `meta`, `sub`
and `banner`. Follow the EUR/USD entry, not the GBP/JPY one.

**This matters and is the easiest thing to get wrong.** GBP/JPY's banner says the
page opens on tuned settings, because it has been through a full tuning study.
EUR/USD's says the opposite: that it is a raw book, nothing is optimised, and the
opening controls are a neutral starting point rather than a recommendation.

**Every pair you add is in EUR/USD's situation, not GBP/JPY's.** Their banners
must say so plainly. Claiming a recommendation exists where none does is the most
misleading thing this page could do, and I will be making real trading decisions
from it.

Put the correct candidate count in each `meta` line. The build log prints it.

If the pair switcher becomes cramped with nine buttons, let it wrap (the `.seg`
class already does) or give it a slightly smaller size. Do not restructure the
layout.

Then rebuild:

    ./.venv/bin/python scripts/build_explorer_page.py <output>.html

---

## Step 3: publish to the SAME artifact

**This is the step most likely to go wrong.** You are in a different session from
the one that published the artifact, so publishing by file path alone will create
a **separate, new artifact** instead of updating mine.

You must pass the existing URL explicitly:

    Artifact(file_path="...", url="https://claude.ai/code/artifact/3c9dea62-5bbc-4348-ab7b-f8c364b9c854")

Keep the favicon `🎛️` and keep the `<title>` as **FX Settings Explorer** so it
keeps its identity in my gallery. Update the one-sentence description to reflect
that it now covers nine pairs.

---

## Verification, all of which I expect you to actually run

The numbers on this page decide real trades, so verify rather than assume.

1. **Cross-check each new pair against Python.** For three settings combinations
   per pair spread across the grid, confirm the page's JSON gives the same trade
   count as running `backtest.research.pass_runner.run_pass` directly, and the
   same total R to within the JSON's 3-decimal rounding. There is a working
   example of exactly this cross-check in the session history. The pattern is to
   extract the data blob from the built HTML and compare against a direct
   `run_pass` call with `htf_threshold` and `total_threshold` in the settings dict
   and the matching `sl_buffer_pips` override.

2. **Run the page's own JavaScript, not a reimplementation of it.** Extract the
   `compute` and `metrics` functions from the built HTML and `eval` them under
   node against the embedded data. A reimplementation that agrees with itself
   proves nothing.

3. **Regression: EUR/USD and GBP/JPY must be unchanged.**
   - GBP/JPY, buffer 2, both gates 25, TP 2.5R, 2024 to 2025:
     **28 trades, 50.0%, +18.70R**
   - GBP/JPY, same settings, 2020 to 2024:
     **67 trades, 34.5%, +14.84R**
   - EUR/USD, buffer 2, both gates 25, TP 2.5R, 2015 to 2025:
     **181 trades, +38.52R**
   - EUR/USD at its live settings (buffer 2, trade 60, HTF 55, TP 2.5R):
     **11 trades, 77.8%, +15.50R**
   - GBP/JPY at its live settings (buffer 0.5, trade 55, HTF 55, TP 3.25R):
     **6 trades, 60.0%, +7.87R**

4. **Pair state isolation.** Change settings on one pair, switch away and back,
   confirm they return and that no other pair moved.

5. **Static checks on the built page**: JavaScript parses, every `var(--token)`
   is defined in all three theme states (bare `:root`, the
   `prefers-color-scheme: dark` media block, and `[data-theme="dark"]`), and
   container tags balance.

6. **`./.venv/bin/python -m pytest tests/ -q` must stay at 628 passing.** Nothing
   in the engine should move for this task. If a test breaks, you have changed
   something you should not have.

---

## Hard constraints

- **Do not touch the live trading path.** `live/`, and
  `data/settings/*/*_settings_2026.json` are fixed configuration that places real
  orders. EUR/USD and GBP/JPY have live settings I chose by hand, pinned
  by `tests/test_sl_buffer_setting.py`. The new pairs get **no** settings files
  from you. I will tune them myself in the explorer first.
- **Do not touch** `data/journal/`, `data/weights/`, or the existing
  `data/settings/` files.
- **Do not re-tune, re-recommend, or "improve" EUR/USD or GBP/JPY.**
- **Do not enable weight learning** anywhere.
- **Do not change the backtest method**, the grids, or the metric definitions.
  Strike rate counts only trades that reached the take-profit (win) or hit the
  stop (loss). Breakeven stops, 19:00 London cuts and Friday closes are excluded
  from strike rate but included in every R figure at their real value.

## What to report back

- The per-pair candidate counts, so I can see which pairs are worth my attention.
- A table of each pair's trade count at a few threshold levels, so I can see where
  each one runs out of trades.
- Anything that surprised you, especially any pair whose probability scale looks
  different from the others, since the thresholds are absolute numbers and a pair
  whose scores top out low would never clear a high gate.
- The artifact URL, confirming it is the same one.

If anything in here turns out to be wrong about the codebase, say so rather than
working around it silently.
