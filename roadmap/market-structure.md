Market Structure for Daily Swing

July 23 2026

- Finalized how am I going to backtest
- Disucssed about daily swing structure and resolved the edge cases

July 24 2026

- ~~Final plan was created based on initial planning~~
- ~~**To do - Check the logic with existing market structure tools and ours and nclude those edge cases and the logic in our code**~~

July 25 2026

- ~~Compare and add the logics from the existing market structure logics in tradingView~~
- ~~Read the plan thoroughly and approve it for creation~~
- ~~Get the pine script version of that code and run it in TradingView or in FxReplay and check the output~~

July 26 2026

- ~~With the market_structure we need to identify whether the swing is bullish or bearish.~~
- ~~Implement the internal daily structure.~~

July 28 2026

- ~~Implement the market structure in 4H~~ (code done, chart calibration still to do, see below)

July 29 2026

- ~~Ported Daily's swing and internal tiers to the same one-mechanism
(Williams Fractal) three-scales approach as 4H, replacing the old
lookback-plus-timeout swing tier and ATR-zigzag internal tier outright.
n=2/8/20, matching 4H's own values (n=20 for Daily swing is in fact the
original value the user observed, applied to 4H first). New~~
`swing_structure/daily_structure.py`~~,~~ `current_structure.py` ~~rewired,~~
`daily_internal_structure.py`~~/~~`daily_swing_structure.py` ~~rewritten, and a
latent linewidth-collision bug between the Daily FXR scripts fixed along
the way. Full detail in~~ `roadmap/detection-method-decision.md`~~'s "Daily
port" section.~~ Done deliberately AHEAD of the order below (H1 was
planned first): the "bring the fractal-family approach back to Daily"
item was pulled forward rather than waiting for 4H's 3-month calibration.
H1 now has two precedents (4H and Daily) to model from instead of one.
- ~~Implemented the market structure in H1:~~ `swing_structure/h1_structure.py`~~,~~
`fxrscripts/h1_fractal_structure.py`~~/~~`h1_internal_structure.py`~~/~~
`h1_swing_structure.py`~~, and the~~ `h1_*` ~~keys in~~ `current_structure.py`~~,
mirroring the 4H work exactly (n=2/8/20,~~ `MY_TIMEFRAME_MS = 3600000`~~,
linewidths 5/6/7). Also generalized~~ `scripts/verify_tier_nesting.py` ~~and
added~~ `scripts/demo_h1_structure.py` ~~so both the 4H and H1 families are
checked from one script.~~ (code done, chart calibration still to do, see
below)

Next Items:

- ~~Find out the internal structure (bullish or bearish) based on the structure mapping~~
- ~~Implement fractal structure mapping~~
- ~~Identify the fractal structure based on the mapping~~
- Test and debug the 4H market structure and verify it for atleast 3 months
- Fill in the H1 three-regime calibration table in
`roadmap/detection-method-decision.md` (tight consolidation, strong trend,
news spike) from real H1 XAU/EU charts, before treating H1's n=2/8/20 as
final. All three were carried over unchanged from 4H, none checked
against a real H1 chart.
- Implement Premium and Discount in daily, 4H and H1
  - Implemented but the logic seems a bit incorrect



### Later Items

- ~~Implement pivot based ATR rule for daily_swing similar to daily internal/~~
- Understand the python code for market_structure thoroughly line by line
- Fill in the Daily three-regime calibration table in  
`roadmap/detection-method-decision.md` (tight consolidation, strong trend,  
news spike) from real Daily XAU/EU charts, before treating Daily's n=8/20  
as final. `daily_internal`'s n=8 in particular has never been checked  
against a real Daily chart at all.

