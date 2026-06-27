# Skill: agent_nifty_trend

Purpose
- Single skill to fetch constituents for NIFTY 50/100/500, compute indicators (EMA20, EMA50, Kalman Trend Level by BigBeluga, VWAP), and label tickers as Uptrend/Downtrend/Neutral. Also emit an overall market trend derived from market sentiment inputs.

Interface
- Invocation: module function or CLI wrapper. Example: python -m skills.agent_nifty_trend --indices "nifty50,nifty100" --mode intraday --out results.json

Input
- indices: list (e.g., ["nifty50","nifty100","nifty500"]) - default all three
- lookback_days: int (default 100)
- intraday: bool (use VWAP) - default False
- vwap_resolution: string ("1m","5m") if intraday
- thresholds: optional object to tune up/down rules

Output
- JSON array of records: {timestamp,index,ticker,close,ema20,ema50,kalman_level,vwap,signals:{ema20_gt_50,close_gt_kalman,close_gt_vwap},trend_label}
- Market summary: {timestamp,total_tickers,percent_up,percent_down,market_trend_label,sentiment_inputs}

Behavior & Signal Rules
- Uptrend: ema20>ema50 AND close>kalman_level AND (if intraday) close>vwap
- Downtrend: ema20<ema50 AND close<kalman_level AND (if intraday) close<vwap
- Neutral: otherwise
- All rules configurable via thresholds and "vote" mode (strict/all vs majority)

Dependencies
- Python 3.11+, pandas, numpy, requests, yfinance or nsepython, pykalman or filterpy, ta (optional)

Error Handling & Retries
- Respect data provider rate limits; use exponential backoff for transient HTTP errors.
- If VWAP unavailable, mark field null and compute trend from remaining indicators.

Idempotency & Permissions
- Read-only by default. Side effects (DB writes, alerts) require explicit permission and sandboxing.

Testing
- Include fixtures of historical OHLCV for unit tests verifying indicator outputs and trend labeling.

Integration Tips
- Register this skill in the orchestrator with input/output schema and cost/time estimates.
- Provide a lightweight CLI and a library-facing function for programmatic use.
