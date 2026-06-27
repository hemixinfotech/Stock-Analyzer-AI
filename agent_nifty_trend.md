# Agent: NIFTY Trend Extractor — Up/Down Trend Filter

Purpose
- Fetch constituents of NIFTY 50, NIFTY 100 and NIFTY 500, compute popular technical indicators, label each stock as Uptrend / Downtrend / Neutral, and provide an overall market trend based on market sentiment.

Requirement (user):
- Indices: NIFTY 50, NIFTY 100, NIFTY 500
- Indicators: EMA 20, EMA 50, Kalman Trend Level (BigBeluga), VWAP
- Output: per-index lists of uptrend/downtrend stocks and an overall market trend derived from sentiment

Data sources
- Primary: NSE APIs or nsepython for constituent lists and intraday/ohlcv; alternative: official index constituents CSV, yfinance for historical OHLCV.
- Market sentiment inputs (optional): India VIX, Put‑Call Ratio, news sentiment, volume breadth.

Approach
1. Ingest constituents for each index. Refresh daily.
2. For each ticker, fetch required price series:
   - For EMA & Kalman: daily close series (lookback: configurable, default 100 bars).
   - For VWAP: intraday OHLCV for the current session (or session VWAP aggregated to requested period).
3. Compute indicators:
   - EMA20, EMA50: standard EMA on close.
   - Kalman Trend Level (BigBeluga): implement Kalman smoothing over close prices to produce a dynamic trend level; use pykalman or a simple Kalman filter implementation. The Trend Level is the smoothed value + optional multiplier for band.
   - VWAP: cumulative (price*volume)/cumulative volume across intraday ticks or resampled bars; compare latest price vs VWAP.
4. Signal rules (configurable thresholds):
   - Uptrend if all true:
       • EMA20 > EMA50
       • Close > KalmanTrendLevel (or KalmanTrendLevel indicates rising slope)
       • Close > VWAP (for intraday bias)
   - Downtrend if all true:
       • EMA20 < EMA50
       • Close < KalmanTrendLevel
       • Close < VWAP
   - Otherwise: Neutral
   - Provide per-signal booleans so users can tune strictness (e.g., majority rule, weighted votes).
5. Market-level sentiment & overall trend:
   - Compute percent_up = (number of Uptrend stocks) / (total evaluated)
   - Compute percent_down similarly.
   - Optional signals: VIX high, PCR > 1.1, negative news sentiment reduce market score.
   - Heuristic: Market Trend = Bullish if percent_up >= 0.6 and sentiment positive; Bearish if percent_down >= 0.6 and sentiment negative; Neutral otherwise. Allow user-set thresholds.

Outputs
- JSON/CSV per run containing:
  - timestamp, index_name, ticker, close, ema20, ema50, kalman_level, vwap, uptrend(bool), downtrend(bool), neutral(bool), signal_breakdown
- Market summary:
  - percent_up, percent_down, market_trend_label, sentiment_inputs (vix, pcr, news_score)

Runtime & integration
- CLI: python -m agent_nifty_trend --indices "nifty50,nifty100" --out results.json --mode intraday
- Scheduler: daily run post-market close for daily signals; intraday mode runs during session for VWAP-aware signals.

Dependencies
- Python 3.11+
- pandas, numpy, requests, yfinance (or nsepython), pykalman or filterpy (for Kalman), ta (optional), tqdm

Implementation notes
- Kalman Trend Level: BigBeluga's variant is a smoothed trend level using Kalman filtering; include parameterization (process noise, measurement noise) and an option to compute trend slope over last N bars.
- VWAP: intraday only — if intraday data unavailable, fallback to session-open VWAP or mark VWAP as unavailable and rely on other signals.
- Performance: evaluate indices in parallel with a bounded threadpool; respect API rate limits and implement retries with exponential backoff.

Testing & validation
- Use saved sample payloads (historical OHLCV) to unit-test indicator outputs and signal rules.
- Backtest rules across historical periods to tune thresholds and reduce false signals.

Caveats
- NSE rate-limits and site protections may require cookie/header handling, caching, or paid data feeds for resilience.
- VWAP requires tick/1m data for accuracy; approximate VWAP from resampled bars if needed.

Extensions & recommendations
- Add weighting by market-cap to compute weighted percent_up for a market-level trend.
- Persist daily outputs to a database for trend history and alerts.
- Provide a dashboard and alerting on trend flips and divergence with market sentiment.

Contact
- Add maintainers and test data links to repo when implementing.
