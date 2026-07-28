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

Next Items:

- Find out the internal structure (bullish or bearish) based on the structure mapping
- Implement fractal structure mapping
- Identify the fractal structure based on the mapping
- Implement the market structure in 4H
- Implement the market structure in h1
- Enhancements:
  - Find out whether we can incorporate pivot based market structure for swing and internal for better accuracy
  - Some of the intenral structure are not very small and when the price moves from bearish internal to bullish or vice versa the script is not able to identify it.
  - ~~Improve adaptive look_back period using ATR~~



### Later Items

- Implement pivot based ATR rule for daily_swing similar to daily internal/
- Understand the python code for market_structure thoroughly line by line

