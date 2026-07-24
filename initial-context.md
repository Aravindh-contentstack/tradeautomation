# Trading Automation Project — Context Summary

## Background
User is a forex/gold trader (EUR/USD, GBP/USD, XAU/USD, aiming to expand to all
major forex + metal pairs). Currently trading a prop firm challenge account
(not personally owned — relevant only if/when execution is automated later).

## The 5-Stage Trading Process
1. **Show up** — start of session routine (not yet automated)
2. **Analyze** — top-down multi-timeframe chart analysis to find confluence
   factors (this is the current focus — see below)
3. **Judgment** — ALREADY AUTOMATED. Each confluence factor has a weight.
   Formula used:

   ```
   Probability = [Σ(positive factor weights) − 0.5 × Σ(negative factor weights)]
                 / Σ(all factor weights involved)
   ```

   - Each factor starts at weight = 1
   - After each trade: weight +5% if the factor supported a winning trade,
     −5% if it supported a losing trade
   - Weights were initially calibrated using 1 year of backtested data
   - If probability > threshold → take the trade; else skip
4. **Journal** — record trade + reasoning + emotions (not yet automated)
5. **Journal analysis** — analyze journal to find which factors matter most,
   feed insights back into Stage 3's weights (not yet automated)

## Current Focus: Automating Stage 2 (Analysis)
User's chart analysis uses **top-down multi-timeframe structure**:
- Daily → H4 → H1 → entry on M15
- At each of Daily / H4 / H1, three structure types are checked:
  - Swing structure
  - Internal structure
  - Fractal structure
- Also analyzes: **Liquidity**, **Supply & Demand**, and an **Entry model**
- Full confluence factor list is large (~40 factors) — only market structure
  discussed in detail so far; precise yes/no rules for swing/internal/fractal
  structure have NOT yet been defined and still need to be extracted from the
  user.

## Data Source Decision
- **TradingView has no official data-pull API** — only one-way webhook alerts.
  Unofficial scrapers (e.g. `tvdatafeed`) exist but are against ToS / fragile.
- **Decision: use OANDA's REST API** (`oandapyV20` Python library) for
  historical + live OHLCV data — free practice account works for this since
  only price data is needed, not execution.
- OANDA API caps each request at 5000 candles, but `InstrumentsCandlesFactory`
  paginates automatically to pull full history.
- Major FX pairs and XAU_USD on OANDA typically have M15 data back to
  ~2004–2005, comfortably covering the user's 20–25 year backtest goal
  (current backtesting tool, FX Replay, only goes back to 2020).
- Account ownership (prop firm, not personal) does NOT block using OANDA for
  data — execution/broker rules only matter later if auto-execution is added.

## Journaling Decision (Stage 4)
- User wants to keep using **Notion** (already loves it for journaling).
- Notion has a solid free official API — can programmatically create a new
  database row (page) per trade with structured properties.
- Notion is good for human-readable review but weak for real analysis
  (no real aggregation/statistics engine).
- **Chosen pattern:**
  1. Notion = source of truth + human-readable journal (auto-filled per trade)
  2. Python + pandas = actual analysis engine (Stage 5), pulling data out via
     Notion API
  3. Optionally push summary insights (e.g. weekly factor performance) back
     into Notion for viewing

## Proposed Journal Entry Schema (per trade)
- Trade identity: pair, direction, timestamp, entry price, timeframe context
- Confluence snapshot at entry: every factor's yes/no result **and its exact
  weight at that moment** (weights drift over time, must snapshot per trade)
- Computed probability score + whether it crossed threshold
- Execution details: stop loss, take profit, position size, risk %
- Outcome (filled after close): win/loss, R:R, pips/points, exit reason
- "System confidence" (margin above/below threshold) and anomaly notes
  (factor conflicts, low liquidity, news nearby) — replaces subjective
  emotion logging since decisions are now rule-based

## Tooling Decisions Made
- **No n8n / Zapier / Make required** — pure Python is sufficient and
  preferred, since the judgment logic already lives in code/structured logic
  and the user wants full control without adding a hosting/orchestration
  dependency.
- Language: Python throughout (data fetch, factor detection, scoring,
  journaling, analysis) — not Pine Script (Pine Script is sandboxed inside
  TradingView, can't call external APIs or run custom analysis logic).

## Open / Next Steps
1. Extract precise yes/no technical rules for each market structure type
   (swing / internal / fractal) at Daily, H4, H1 — needed before any
   detection code can be written.
2. Then do the same for liquidity, supply & demand, and entry model factors.
3. Build Python module: OANDA auth + paginated historical data fetch script
   (EUR_USD, GBP_USD, XAU_USD, M15, as far back as available) for backtesting.
4. Build confluence factor detection logic once rules are defined, output as
   structured JSON matching Stage 3's expected input format.
5. Design and implement Notion database schema per the journal entry fields
   above; build Python → Notion API write-on-trade-close logic.
6. Build Stage 5 analysis scripts (pandas) pulling from Notion API, computing
   per-factor win rates, and proposing weight adjustments back into Stage 3.

## User Preferences / Working Style
- Prefers **learn-by-doing** — build real, working pieces rather than
  extended theory explanations.
- Already comfortable with n8n basics but this project intentionally uses
  plain Python instead.
- Cost/context discussions in INR where relevant (India-based).
- Ashok's (cousin's) existing agency workflow is a longer-term reference
  point for a separate, related project (a client intake/proposal agent) —
  not part of this trading automation thread, but same person, worth noting
  if it comes up.