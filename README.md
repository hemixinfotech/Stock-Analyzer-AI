# Stock-Analyzer-AI

Stock Analyzer Application for extracting per-ticker trend signals and a market-level summary for Indian indices (NIFTY / BSE).

## Overview

This project fetches index constituents, obtains OHLCV price series (daily or intraday), computes indicators (EMA20, EMA50, Kalman Trend Level, VWAP, SuperTrend), and labels each ticker as Uptrend / Downtrend / Neutral. 

**Data Source**: Real-time prices from **Fyers API** (preferred) or yfinance (fallback)

Outputs are JSON/CSV files with per-ticker signals and a market summary (percent_up/percent_down, market_trend_label).

## Quickstart

### 1. Prerequisites
- Python 3.11+ recommended.
- Install runtime deps (the repository has no requirements.txt):
  ```bash
  python -m pip install pandas numpy requests yfinance pykalman filterpy ta tqdm python-dotenv
  ```

### 2. (Optional) Configure Fyers API for Real-Time Data
For real-time stock prices instead of delayed yfinance data:
1. Create a Fyers account at [https://www.fyers.in/](https://www.fyers.in/)
2. Get API credentials from [https://www.fyers.in/data-api/](https://www.fyers.in/data-api/)
3. Copy `.env.example` to `.env` and add your credentials:
   ```bash
   FYERS_API_KEY=your_api_key
   FYERS_ACCESS_TOKEN=your_access_token
   ```
4. See [FYERS_SETUP.md](FYERS_SETUP.md) for detailed instructions.

### 3. Run the Agent
```bash
# With config files (no Fyers needed, uses yfinance)
python "scripts\agent_nifty_trend.py" --indices "nifty50,nifty_midcap_100,nifty_smallcap_100" --out "results_nifty_trend.json"

# Generate CSV reports
python "scripts\generate_trend_csv.py"
```

## Repository Layout

- **scripts/**: CLI scripts
  - `agent_nifty_trend.py` - Main trend analysis engine (Fyers + yfinance support)
  - `generate_trend_csv.py` - Convert JSON to CSV per-index reports
  - `fetch_bse_constituents.py` - Fetch constituents from NSE/Wikipedia
  
- **configs/**: Static index constituent lists (JSON format)
  - `nifty50.json`, `nifty_midcap_100.json`, `nifty_smallcap_100.json`
  
- **results_***: Example outputs (JSON/CSV format)

## Key Features

✅ **Real-time Data**: Fyers API support for live stock prices
✅ **Fallback Support**: Automatic fallback to yfinance if Fyers not configured
✅ **Multiple Indicators**: EMA20/50, SMA20/50, SuperTrend, Kalman Filter, Price Action
✅ **Voting Logic**: Robust trend determination using multiple signals
✅ **CSV Export**: Generate reports segmented by index
✅ **Config-based**: All constituents loaded from JSON configs (no hardcoding)

## Data Sources

| Source | Type | Freshness | Setup |
|--------|------|-----------|-------|
| Fyers API | Real-time + Historical | Live (seconds) | API credentials required |
| yfinance | Historical | Delayed (minutes) | None |
| NSE/Wikipedia | Constituent lists | Daily | None |

## Configuration Options

Run `agent_nifty_trend.py --help` for all options:

```bash
--indices "nifty50,nifty_midcap_100,nifty_smallcap_100"  # Indices to analyze
--lookback 100                                            # Lookback days
--intraday                                               # Enable intraday VWAP
--vote-mode majority                                      # Voting mode (strict/majority/any)
--supertrend-period 10                                    # ATR period
--supertrend-multiplier 3.0                              # Multiplier for ATR bands
--fyers-api-key "..."                                    # Fyers API Key (or use env var)
--fyers-access-token "..."                               # Fyers Access Token (or use env var)
--out "results.json"                                     # Output file
```

## Output Format

### JSON Output (`results_nifty_trend.json`)
```json
{
  "summary": {
    "timestamp": "2026-06-09T15:27:42.995123Z",
    "total_tickers": 201,
    "percent_up": 38.31,
    "percent_down": 59.70,
    "market_trend": "bearish"
  },
  "records": [
    {
      "ticker": "SBIN.NS",
      "close": 643.45,
      "trend": "up",
      "index": "nifty50"
    }
  ]
}
```

### CSV Output (separate files per index)
- `results_trend_all.csv` - All stocks
- `results_trend_midcap.csv` - Nifty Midcap 100 only
- `results_trend_smallcap.csv` - Nifty Smallcap 100 only

## Technical Details

### Trend Logic
- **Voting System**: Signals (MA crossover, SuperTrend, price action, Kalman level) vote on trend
- **Vote Modes**:
  - `strict`: All signals must agree
  - `majority`: >50% of signals agree (default)
  - `any`: Any signal triggers decision
- **Relaxed Rules**: Special handling for strong momentum stocks

### Constituents Loading
1. First checks `configs/{index_name}.json` files
2. Falls back to Wikipedia table parsing
3. Falls back to NSE CSV endpoints
4. Returns empty if all sources fail

## Notes for Contributors

- All trend rules and thresholds documented in code and `FYERS_SETUP.md`
- API rate limits: Fyers (100 req/min), NSE/Wikipedia (no strict limits)
- Script includes exponential backoff and error handling
- New data sources should follow the priority pattern

## Troubleshooting

### "No module named 'yfinance'"
```bash
pip install yfinance
```

### Fyers API returns 401 errors
- Access token may have expired
- Generate a new token via OAuth flow
- See [FYERS_SETUP.md](FYERS_SETUP.md) for instructions

### Getting only yfinance data (no Fyers)
- Set `FYERS_API_KEY` and `FYERS_ACCESS_TOKEN` env vars
- Or pass `--fyers-api-key` and `--fyers-access-token` args
- If not configured, script automatically uses yfinance

## License

See LICENSE file.

## Contact

Add maintainers or contact info when available.
