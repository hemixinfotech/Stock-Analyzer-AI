# Copilot Instructions for Stock-Analyzer-AI

## Commands

```powershell
# Install runtime dependencies (no requirements.txt is checked in)
python -m pip install pandas numpy requests yfinance pykalman filterpy ta tqdm python-dotenv streamlit

# If Streamlit should use a specific virtualenv/interpreter for subprocesses, set it before starting the app
$env:PROJECT_PYTHON = "C:\path\to\python.exe"

# Start the Streamlit dashboard
python -m streamlit run "streamlit_app.py"

# Run the main analysis script from the repo root
python "scripts\agent_nifty_trend.py" --indices "nifty50,nifty_midcap_100,nifty_smallcap_100" --out "results_nifty_trend.json"

# Focused smoke run for a single index
python "scripts\agent_nifty_trend.py" --indices "nifty50" --out "results_nifty_trend.json"

```

There is no checked-in automated test suite, lint configuration, or build pipeline. Validate changes by running the smallest relevant script flow from the repo root.

## High-level architecture

- `scripts\agent_nifty_trend.py` is the core pipeline. `main()` parses CLI flags, expands each requested index into ticker symbols, evaluates each ticker, and emits a single JSON object with `summary` plus per-ticker `records`.
- Constituent resolution is intentionally layered inside `get_constituents_from_wikipedia()`:
  1. local `configs\{index}.json` / `configs\{normalized-index}.json`
  2. Wikipedia tables
  3. NSE CSV endpoints  
  Local config files are the most reliable path in this repo and should be kept in sync with any new index support.
- Price data is also layered:
  - daily history: Fyers first when both credentials are present, otherwise `yfinance`
  - latest close: Fyers quote can override the last historical close
  - intraday VWAP: always computed from `yfinance` intraday bars when `--intraday` is enabled
- Trend classification in `evaluate_ticker()` is more than a simple EMA crossover. It combines SMA/EMA, Kalman level, SuperTrend, simple price action, and optional VWAP into a boolean signal set, then applies:
  - strong-up / strong-down shortcuts first
  - `vote-mode` (`strict`, `majority`, `any`) only if no strong trend fired
- `streamlit_app.py` is the frontend entrypoint. It runs `scripts\agent_nifty_trend.py` directly for one selected index at a time, writes fresh output into `jobs\latest\`, and renders the latest per-index JSON artifact.
- Streamlit refreshes use `PROJECT_PYTHON` when it is set; otherwise subprocesses use the current interpreter. Keep that aligned with the environment where numpy/pandas/yfinance are installed.

## Key repository conventions

- Run scripts from the repository root. Both Python entrypoints use relative paths like `Path('configs')` and `Path('results_nifty_trend.json')`; running them from another working directory will break file discovery.
- Streamlit refreshes write artifacts into `jobs\latest\`, including a combined `results_nifty_trend.json` and per-index JSON files like `results_trend_nifty50.json` or `results_trend_nifty_midcap_100.json`.
- Index config files are JSON arrays of either ticker strings or objects with `ticker`, `symbol`, or `code`. The loader normalizes everything to uppercase NSE tickers and appends `.NS`.
- Use the repo's existing index names when invoking `--indices`. The locally maintained configs currently match names like `nifty50`, `nifty_midcap_100`, and `nifty_smallcap_100`; the default CLI values `nifty100` and `nifty500` depend on remote lookup because no local configs are checked in for them.
- Fyers support is opt-in and only activates when **both** `FYERS_API_KEY` and `FYERS_ACCESS_TOKEN` are present, either in `.env` or via CLI flags. Internally the script converts NSE symbols from `.NS` to Fyers `-EQ` format.
- Output shape matters across scripts: the report generator expects the analyzer JSON to contain top-level `summary` and `records`, with each record carrying at least `ticker`, `trend`, and `index`.
