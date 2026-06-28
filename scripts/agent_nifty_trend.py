#!/usr/bin/env python3
"""
agent_nifty_trend.py

Fetch NIFTY constituents, compute EMA20/EMA50, simple Kalman Trend Level, VWAP (intraday),
and label tickers as Uptrend / Downtrend / Neutral. Outputs JSON.

Data source: Fyers API (real-time, preferred) or yfinance (fallback)
Dependencies: pandas, numpy, requests, yfinance, python-dotenv
"""
import argparse
import hashlib
import json
import time
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests


# Headers used when contacting NSE and Fyers
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': '*/*'
}

# Fyers API Configuration
FYERS_API_KEY = os.getenv('FYERS_API_KEY', '')
FYERS_ACCESS_TOKEN = os.getenv('FYERS_ACCESS_TOKEN', '')
FYERS_BASE_URL = 'https://api.fyers.in/api/v3'
FYERS_QUOTE_URL = 'https://api.fyers.in/api/v3/quotes'
REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = Path(tempfile.gettempdir()) / 'stock-analyzer-ai-cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def ist_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def is_market_hours(now=None) -> bool:
    current = now or ist_now()
    if current.weekday() >= 5:
        return False
    market_open = current.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = current.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= current <= market_close


def build_cache_path(namespace, key, suffix):
    namespace_dir = CACHE_DIR / namespace
    namespace_dir.mkdir(parents=True, exist_ok=True)
    hashed_key = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return namespace_dir / f'{hashed_key}.{suffix}'


def prune_cache_dir(namespace, max_age_seconds=24 * 60 * 60, max_files=200):
    namespace_dir = CACHE_DIR / namespace
    if not namespace_dir.exists():
        return
    now = time.time()
    files = [path for path in namespace_dir.iterdir() if path.is_file()]
    for path in files:
        try:
            if (now - path.stat().st_mtime) > max_age_seconds:
                path.unlink()
        except OSError:
            pass
    files = sorted(
        [path for path in namespace_dir.iterdir() if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale_path in files[max_files:]:
        try:
            stale_path.unlink()
        except OSError:
            pass


def cache_is_fresh(path, ttl_seconds):
    if not path.exists():
        return False
    if ttl_seconds is None:
        return True
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds <= ttl_seconds


def read_json_cache(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def write_json_cache(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)


def read_pickle_cache(path):
    return pd.read_pickle(path)


def write_pickle_cache(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(payload, path)


def history_cache_ttl_seconds():
    return 300 if is_market_hours() else 6 * 60 * 60


def intraday_cache_ttl_seconds():
    return 60 if is_market_hours() else 30 * 60


def quote_cache_ttl_seconds():
    return 0 if is_market_hours() else 10 * 60


def get_constituents_from_wikipedia(index_name):
    """Resolve constituents for an index.

    Priority:
    1. configs/{index_name}.json or configs/{index_key}.json if present
    2. Wikipedia table
    3. NSE CSV endpoint
    4. Built-in fallback for NIFTY 50
    """
    index_key = index_name.lower().replace(' ', '')

    # 1) Check configs folder for a pre-bundled list
    cfg_candidates = [Path('configs') / f"{index_name}.json", Path('configs') / f"{index_key}.json"]
    for cfg in cfg_candidates:
        if cfg.exists():
            try:
                data = json.load(open(cfg, 'r', encoding='utf-8'))
                syms = []
                for item in data:
                    if isinstance(item, str):
                        s = item
                    elif isinstance(item, dict):
                        s = item.get('ticker') or item.get('symbol') or item.get('code') or None
                    else:
                        s = None
                    if s:
                        s = s.strip().upper().replace('.NS', '')
                        syms.append(s + '.NS')
                return list(dict.fromkeys(syms))
            except Exception:
                # if config file is malformed, continue to other methods
                pass

    cache_path = build_cache_path('constituents', index_key, 'json')
    prune_cache_dir('constituents', max_age_seconds=7 * 24 * 60 * 60, max_files=50)
    if cache_is_fresh(cache_path, 12 * 60 * 60):
        try:
            cached_symbols = read_json_cache(cache_path)
            if isinstance(cached_symbols, list) and cached_symbols:
                return cached_symbols
        except Exception:
            pass

    urls = {}
    # Prefer specific mid/smallcap detection before generic '100' rule
    if 'midcap' in index_key and '100' in index_key:
        wiki_url = 'https://en.wikipedia.org/wiki/NIFTY_MIDCAP_100'
        urls['nse_csv'] = 'https://www1.nseindia.com/content/indices/ind_niftymidcap100list.csv'
    elif 'smallcap' in index_key and '100' in index_key:
        wiki_url = 'https://en.wikipedia.org/wiki/NIFTY_SMLCAP_100'
        urls['nse_csv'] = 'https://www1.nseindia.com/content/indices/ind_niftysmallcap100list.csv'
    elif '50' in index_key:
        wiki_url = 'https://en.wikipedia.org/wiki/NIFTY_50'
        urls['nse_csv'] = 'https://www1.nseindia.com/content/indices/ind_nifty50list.csv'
    elif '100' in index_key:
        wiki_url = 'https://en.wikipedia.org/wiki/NIFTY_100'
        urls['nse_csv'] = 'https://www1.nseindia.com/content/indices/ind_nifty100list.csv'
    elif '500' in index_key:
        wiki_url = 'https://en.wikipedia.org/wiki/NIFTY_500'
        urls['nse_csv'] = 'https://www1.nseindia.com/content/indices/ind_nifty500list.csv'
    else:
        raise ValueError(f'Unknown index: {index_name}')

    # Try wikipedia first
    try:
        tables = pd.read_html(wiki_url)
        # Heuristic: find a table that has a column like Symbol or Ticker
        for t in tables:
            cols = [c.lower() for c in t.columns.astype(str)]
            if any('symbol' in c or 'ticker' in c for c in cols):
                for c in t.columns:
                    if 'symbol' in str(c).lower() or 'ticker' in str(c).lower():
                        syms = t[c].astype(str).tolist()
                        syms = [s.strip().upper().replace('.NS', '') for s in syms if s and s != '–']
                        syms = [s + '.NS' for s in syms]
                        resolved = list(dict.fromkeys(syms))
                        write_json_cache(cache_path, resolved)
                        return resolved
    except Exception:
        # ignore and try NSE CSV fallback
        pass

    # Fallback: try NSE CSV list
    csv_url = urls.get('nse_csv')
    if csv_url:
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            # preliminary request to get cookies
            try:
                s.get('https://www.nseindia.com', timeout=10)
            except Exception:
                pass
            r = s.get(csv_url, timeout=15)
            r.raise_for_status()
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            # find symbol-like column
            for col in df.columns:
                if 'symbol' in col.lower() or 'code' in col.lower():
                    syms = df[col].astype(str).tolist()
                    syms = [s.strip().upper().replace('.NS', '') for s in syms if s and s != '–']
                    syms = [s + '.NS' for s in syms]
                    resolved = list(dict.fromkeys(syms))
                    write_json_cache(cache_path, resolved)
                    return resolved
            # if CSV has a 'Security Name' and 'ISIN' etc, attempt common column
            if 'SYMBOL' in (c.upper() for c in df.columns):
                syms = df['SYMBOL'].astype(str).tolist()
                syms = [s.strip().upper().replace('.NS', '') for s in syms if s and s != '–']
                syms = [s + '.NS' for s in syms]
                resolved = list(dict.fromkeys(syms))
                write_json_cache(cache_path, resolved)
                return resolved
        except Exception:
            pass

    if cache_path.exists():
        try:
            cached_symbols = read_json_cache(cache_path)
            if isinstance(cached_symbols, list):
                return cached_symbols
        except Exception:
            pass

    # If no data from any source, return empty list (requires config files)
    return []


def simple_kalman(series, q=1e-5, r=0.001):
    """1D Kalman smoother for a pandas Series. Returns numpy array of filtered values."""
    n = len(series)
    if n == 0:
        return np.array([])
    xhat = np.zeros(n)
    P = np.zeros(n)
    xhatminus = np.zeros(n)
    Pminus = np.zeros(n)
    K = np.zeros(n)

    # initial guesses
    xhat[0] = series.iloc[0]
    P[0] = 1.0

    for k in range(1, n):
        # time update
        xhatminus[k] = xhat[k-1]
        Pminus[k] = P[k-1] + q

        # measurement update
        K[k] = Pminus[k] / (Pminus[k] + r)
        xhat[k] = xhatminus[k] + K[k] * (series.iloc[k] - xhatminus[k])
        P[k] = (1 - K[k]) * Pminus[k]

    return xhat


def compute_vwap(df):
    # df expected to have ['High','Low','Close','Volume'] indexed by datetime
    if df is None or df.empty or 'Volume' not in df.columns:
        return None
    tp = (df['High'] + df['Low'] + df['Close']) / 3.0
    pv = tp * df['Volume']
    cumsum_pv = pv.cumsum()
    cumsum_v = df['Volume'].cumsum()
    vwap = cumsum_pv / cumsum_v.replace({0: np.nan})
    return vwap


def atr(df, period=14):
    """Average True Range (simple rolling average)"""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(window=period, min_periods=1).mean()
    return atr_series


def compute_rsi(series, period=14):
    """Compute RSI using Wilder smoothing."""
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def compute_macd(series, fast=12, slow=26, signal=9):
    """Compute MACD line, signal line, and histogram."""
    if series is None or len(series) == 0:
        empty = pd.Series(dtype=float)
        return empty, empty, empty
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def detect_order_block_rejection(df, direction, lookback=20):
    """Heuristic supply/demand rejection detector using recent pivot zones."""
    if df is None or df.empty or not {'Open', 'High', 'Low', 'Close'}.issubset(df.columns):
        return False, None
    recent = df.tail(max(lookback + 3, 8)).copy()
    if len(recent) < 5:
        return False, None

    body = (recent['Close'] - recent['Open']).abs()
    median_body = float(body.tail(lookback).median()) if not body.tail(lookback).empty else 0.0
    threshold = median_body * 0.6 if median_body > 0 else 0.0

    latest = recent.iloc[-1]
    prior = recent.iloc[-2]
    latest_range = float(latest['High'] - latest['Low']) or 1.0

    if direction == 'bullish':
        candidates = recent[(recent['Close'] < recent['Open']) & (body >= threshold)]
        zone_price = float(candidates['Low'].tail(3).min()) if not candidates.empty else float(recent['Low'].tail(lookback).min())
        touched_zone = float(latest['Low']) <= zone_price * 1.02 or float(prior['Low']) <= zone_price * 1.02
        rejected = (
            float(latest['Close']) > float(latest['Open']) and
            float(latest['Close']) >= (float(latest['Low']) + latest_range * 0.6) and
            float(latest['Close']) > float(prior['Close'])
        )
        return touched_zone and rejected, zone_price

    candidates = recent[(recent['Close'] > recent['Open']) & (body >= threshold)]
    zone_price = float(candidates['High'].tail(3).max()) if not candidates.empty else float(recent['High'].tail(lookback).max())
    touched_zone = float(latest['High']) >= zone_price * 0.98 or float(prior['High']) >= zone_price * 0.98
    rejected = (
        float(latest['Close']) < float(latest['Open']) and
        float(latest['Close']) <= (float(latest['High']) - latest_range * 0.6) and
        float(latest['Close']) < float(prior['Close'])
    )
    return touched_zone and rejected, zone_price


def supertrend(df, period=10, multiplier=3.0):
    """Compute SuperTrend and return (trend_series (True=up), final_upper, final_lower)
    Expects df to contain High, Low, Close columns.
    """
    if df is None or df.empty or not {'High','Low','Close'}.issubset(df.columns):
        return pd.Series(dtype=bool), None, None
    atr_sr = atr(df, period=period)
    hl2 = (df['High'] + df['Low']) / 2.0
    basic_ub = hl2 + multiplier * atr_sr
    basic_lb = hl2 - multiplier * atr_sr

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()
    trend = pd.Series(index=df.index, dtype=bool)

    # initialize
    final_ub.iloc[0] = basic_ub.iloc[0]
    final_lb.iloc[0] = basic_lb.iloc[0]
    trend.iloc[0] = True  # assume up on first bar

    for i in range(1, len(df)):
        if basic_ub.iloc[i] < final_ub.iloc[i-1] or df['Close'].iloc[i-1] > final_ub.iloc[i-1]:
            final_ub.iloc[i] = basic_ub.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i-1]

        if basic_lb.iloc[i] > final_lb.iloc[i-1] or df['Close'].iloc[i-1] < final_lb.iloc[i-1]:
            final_lb.iloc[i] = basic_lb.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i-1]

        # determine trend: if close crosses below final_ub -> downtrend False, if close crosses above final_lb -> uptrend True
        if df['Close'].iloc[i] <= final_ub.iloc[i]:
            trend.iloc[i] = False
        else:
            trend.iloc[i] = True

    return trend, final_ub, final_lb


def fetch_history_fyers(symbol, period_days=120):
    """Fetch historical OHLCV data from Fyers API.
    
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    or None if request fails.
    """
    if not FYERS_ACCESS_TOKEN or not FYERS_API_KEY:
        return None
    
    # Fyers uses different symbol format: SBIN-EQ instead of SBIN.NS
    fyers_symbol = symbol.replace('.NS', '-EQ')
    
    # Fyers API requires recent history (typically supports 1-5 years)
    # Use 'D' for daily candles
    cache_key = f'{symbol}|{period_days}'
    cache_path = build_cache_path('fyers_history', cache_key, 'pkl')
    prune_cache_dir('fyers_history')
    ttl_seconds = history_cache_ttl_seconds()
    if cache_is_fresh(cache_path, ttl_seconds):
        try:
            cached_df = read_pickle_cache(cache_path)
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                return cached_df
        except Exception:
            pass

    try:
        params = {
            'symbol': fyers_symbol,
            'resolution': 'D',  # Daily resolution
            'date_format': 'unix',
            'range_from': int((datetime.now() - timedelta(days=period_days+30)).timestamp()),
            'range_to': int(datetime.now().timestamp())
        }
        headers = {
            'Authorization': f'Bearer {FYERS_ACCESS_TOKEN}',
            **HEADERS
        }
        response = requests.get(f'{FYERS_BASE_URL}/history', params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('s') != 'ok' or not data.get('candles'):
            return None
        
        # Convert Fyers candles to DataFrame
        candles = data['candles']
        df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Date'] = pd.to_datetime(df['Timestamp'], unit='s')
        df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.set_index('Date', inplace=True)
        df = df.sort_index()
        df = df.tail(period_days)
        write_pickle_cache(cache_path, df)
        return df
    except Exception as e:
        if cache_path.exists():
            try:
                cached_df = read_pickle_cache(cache_path)
                if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                    return cached_df
            except Exception:
                pass
        return None


def fetch_quote_fyers(symbol):
    """Fetch real-time quote data from Fyers API.
    
    Returns dict with latest price, high, low, or None if request fails.
    """
    if not FYERS_ACCESS_TOKEN or not FYERS_API_KEY:
        return None
    
    fyers_symbol = symbol.replace('.NS', '-EQ')
    
    cache_key = symbol
    cache_path = build_cache_path('fyers_quote', cache_key, 'json')
    prune_cache_dir('fyers_quote', max_age_seconds=6 * 60 * 60, max_files=200)
    ttl_seconds = quote_cache_ttl_seconds()
    if ttl_seconds > 0 and cache_is_fresh(cache_path, ttl_seconds):
        try:
            cached_quote = read_json_cache(cache_path)
            if isinstance(cached_quote, dict) and cached_quote.get('close') is not None:
                return cached_quote
        except Exception:
            pass

    try:
        params = {
            'symbols': fyers_symbol
        }
        headers = {
            'Authorization': f'Bearer {FYERS_ACCESS_TOKEN}',
            **HEADERS
        }
        response = requests.get(FYERS_QUOTE_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('s') != 'ok' or not data.get('d'):
            return None
        
        quote = data['d'][0]  # First symbol's quote
        quote_payload = {
            'close': quote.get('c'),
            'high': quote.get('h'),
            'low': quote.get('l'),
            'open': quote.get('o'),
            'volume': quote.get('v'),
            'timestamp': quote.get('tm')
        }
        write_json_cache(cache_path, quote_payload)
        return quote_payload
    except Exception as e:
        if cache_is_fresh(cache_path, 5 * 60):
            try:
                cached_quote = read_json_cache(cache_path)
                if isinstance(cached_quote, dict):
                    return cached_quote
            except Exception:
                pass
        return None


def fetch_history_yf(ticker, period_days=120):
    """Fetch historical data. Priority: Fyers API -> yfinance"""
    cache_key = f'{ticker}|{period_days}'
    cache_path = build_cache_path('yf_history', cache_key, 'pkl')
    prune_cache_dir('yf_history')
    ttl_seconds = history_cache_ttl_seconds()
     
    # Try Fyers first if credentials are available
    if FYERS_ACCESS_TOKEN and FYERS_API_KEY:
        try:
            df = fetch_history_fyers(ticker, period_days=period_days)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    if cache_is_fresh(cache_path, ttl_seconds):
        try:
            cached_df = read_pickle_cache(cache_path)
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                return cached_df
        except Exception:
            pass
     
    # Fallback to yfinance
    if yf is None:
        raise RuntimeError('yfinance is required but not installed (Fyers not configured)')
    
    period = f"{max(2, period_days)}d"
    try:
        df = yf.download(ticker, period=period, progress=False, threads=False)
    except Exception:
        df = pd.DataFrame()
    if df is not None and not df.empty:
        write_pickle_cache(cache_path, df)
        return df
    if cache_path.exists():
        try:
            cached_df = read_pickle_cache(cache_path)
            if isinstance(cached_df, pd.DataFrame):
                return cached_df
        except Exception:
            pass
    return df


def fetch_intraday_yf(ticker, interval):
    cache_key = f'{ticker}|{interval}'
    cache_path = build_cache_path('yf_intraday', cache_key, 'pkl')
    prune_cache_dir('yf_intraday', max_age_seconds=6 * 60 * 60, max_files=300)
    ttl_seconds = intraday_cache_ttl_seconds()
    if cache_is_fresh(cache_path, ttl_seconds):
        try:
            cached_df = read_pickle_cache(cache_path)
            if isinstance(cached_df, pd.DataFrame) and not cached_df.empty:
                return cached_df
        except Exception:
            pass

    if yf is None:
        return pd.DataFrame()

    try:
        df = yf.download(ticker, period='1d', interval=interval, progress=False, threads=False)
    except Exception:
        df = pd.DataFrame()
    if df is not None and not df.empty:
        write_pickle_cache(cache_path, df)
        return df
    if cache_path.exists():
        try:
            cached_df = read_pickle_cache(cache_path)
            if isinstance(cached_df, pd.DataFrame):
                return cached_df
        except Exception:
            pass
    return df


def evaluate_ticker(ticker, lookback_days=100, intraday=False, vwap_resolution='5m', vote_mode='majority', supertrend_period=10, supertrend_multiplier=3.0, price_action_lookback=3):
    # fetch daily history
    history_days = max(int(lookback_days) + 10, 60)
    hist = fetch_history_yf(ticker, period_days=history_days)
    if hist is None or hist.empty:
        return None
    volume = pd.Series(dtype=float)
    # normalize columns: support yfinance MultiIndex (Price, Ticker) and plain columns
    if isinstance(hist.columns, pd.MultiIndex):
        # try to find Close column by first level name
        close_cols = [c for c in hist.columns if c[0] == 'Close']
        if not close_cols:
            return None
        # select the first Close column (likely the ticker requested)
        close = hist[close_cols[0]].dropna()
        volume_cols = [c for c in hist.columns if c[0] == 'Volume']
        if volume_cols:
            volume = hist[volume_cols[0]].dropna()
        if close.empty:
            return None
    else:
        if 'Close' not in hist.columns:
            return None
        hist = hist.dropna(subset=['Close'])
        close = hist['Close']
        if 'Volume' in hist.columns:
            volume = hist['Volume'].dropna()

    # Moving averages (EMA and SMA)
    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema50_series = close.ewm(span=50, adjust=False).mean()
    ema20 = ema20_series.iloc[-1]
    ema50 = ema50_series.iloc[-1]
    ema20_slope = float(ema20 - (ema20_series.iloc[-2] if len(ema20_series) > 1 else ema20))

    sma20_series = close.rolling(window=20, min_periods=1).mean()
    sma50_series = close.rolling(window=50, min_periods=1).mean()
    sma20 = sma20_series.iloc[-1]
    sma50 = sma50_series.iloc[-1]

    rsi_series = compute_rsi(close)
    rsi_value = float(rsi_series.iloc[-1]) if not rsi_series.dropna().empty else None
    prev_rsi = float(rsi_series.iloc[-2]) if len(rsi_series.dropna()) >= 2 else None
    latest_volume = float(volume.iloc[-1]) if not volume.empty else None
    avg_volume_20 = float(volume.tail(20).mean()) if not volume.empty else None
    volume_ratio = (latest_volume / avg_volume_20) if latest_volume is not None and avg_volume_20 not in (None, 0) else None

    macd_line, macd_signal_line, macd_histogram = compute_macd(close)
    macd_value = float(macd_line.iloc[-1]) if not macd_line.dropna().empty else None
    macd_signal_value = float(macd_signal_line.iloc[-1]) if not macd_signal_line.dropna().empty else None
    macd_hist_value = float(macd_histogram.iloc[-1]) if not macd_histogram.dropna().empty else None
    prev_macd_value = float(macd_line.iloc[-2]) if len(macd_line.dropna()) >= 2 else None
    prev_macd_signal_value = float(macd_signal_line.iloc[-2]) if len(macd_signal_line.dropna()) >= 2 else None
    prev_macd_hist_value = float(macd_histogram.iloc[-2]) if len(macd_histogram.dropna()) >= 2 else None

    # Kalman smoothing
    kalman_vals = simple_kalman(close.ffill())
    kalman_level = float(kalman_vals[-1]) if kalman_vals.size > 0 else None
    prev_kalman_level = float(kalman_vals[-2]) if kalman_vals.size > 1 else kalman_level

    # Try to get real-time quote from Fyers first
    latest_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else latest_close
    fyers_quote = fetch_quote_fyers(ticker)
    if fyers_quote and fyers_quote.get('close'):
        latest_close = float(fyers_quote['close'])  # Override with real-time price
    lookback_index = max(0, len(close) - min(max(int(lookback_days), 1), len(close)))
    lookback_close = float(close.iloc[lookback_index])
    percent_change = ((latest_close - lookback_close) / lookback_close * 100.0) if lookback_close else None

    # VWAP (intraday)
    vwap_latest = None
    if intraday:
        if yf is not None:
            try:
                intr = fetch_intraday_yf(ticker, vwap_resolution)
                if not intr.empty:
                    vwap_sr = compute_vwap(intr)
                    if vwap_sr is not None and not vwap_sr.empty:
                        vwap_latest = float(vwap_sr.iloc[-1])
            except Exception:
                vwap_latest = None

    bullish_ob_rejection, bullish_order_block = detect_order_block_rejection(hist, 'bullish')
    bearish_ob_rejection, bearish_order_block = detect_order_block_rejection(hist, 'bearish')

    # SuperTrend (daily) — uses lookback portion of history
    st_trend = None
    st_ub = None
    st_lb = None
    try:
        st_series, st_ub_series, st_lb_series = supertrend(hist.tail(max(50, lookback_days)), period=supertrend_period, multiplier=supertrend_multiplier)
        if not st_series.empty:
            st_trend = bool(st_series.iloc[-1])
            st_ub = float(st_ub_series.iloc[-1]) if st_ub_series is not None else None
            st_lb = float(st_lb_series.iloc[-1]) if st_lb_series is not None else None
    except Exception:
        st_trend = None

    # Price action structure: simple higher highs/higher lows over lookback
    pa_bull = None
    pa_bear = None
    try:
        n = max(3, int(price_action_lookback))
        recent = hist.tail(n)
        if len(recent) >= 3:
            highs = recent['High'].values
            lows = recent['Low'].values
            closes = recent['Close'].values
            pa_bull = all(highs[i] > highs[i-1] for i in range(1, len(highs))) and all(lows[i] > lows[i-1] for i in range(1, len(lows)))
            pa_bear = all(highs[i] < highs[i-1] for i in range(1, len(highs))) and all(lows[i] < lows[i-1] for i in range(1, len(lows)))
    except Exception:
        pa_bull = None
        pa_bear = None

    # Signals
    ma_crossover = sma20 > sma50
    price_above_sma20 = latest_close > sma20
    price_above_sma50 = latest_close > sma50
    ema_signal = ema20 > ema50
    kalman_signal = (kalman_level is not None) and (latest_close > kalman_level)
    rsi_signal = None if rsi_value is None else rsi_value >= 50.0
    vwap_signal = None
    if intraday:
        if vwap_latest is None:
            vwap_signal = None
        else:
            vwap_signal = latest_close > vwap_latest
    bearish_vwap_signal = None if vwap_signal is None else not vwap_signal
    volume_bullish = volume_ratio is not None and volume_ratio >= 0.9 and latest_close >= prev_close
    volume_bearish = volume_ratio is not None and volume_ratio >= 0.9 and latest_close <= prev_close

    macd_bullish = (
        macd_value is not None and
        macd_signal_value is not None and
        (
            (prev_macd_value is not None and prev_macd_signal_value is not None and prev_macd_value <= prev_macd_signal_value and macd_value > macd_signal_value) or
            (prev_macd_hist_value is not None and macd_hist_value is not None and macd_hist_value > prev_macd_hist_value)
        )
    )
    macd_bearish = (
        macd_value is not None and
        macd_signal_value is not None and
        (
            (prev_macd_value is not None and prev_macd_signal_value is not None and prev_macd_value >= prev_macd_signal_value and macd_value < macd_signal_value) or
            (prev_macd_hist_value is not None and macd_hist_value is not None and macd_hist_value < prev_macd_hist_value)
        )
    )
    rsi_turning_bullish = rsi_value is not None and (
        (prev_rsi is not None and prev_rsi < 30.0 and rsi_value > prev_rsi) or
        (prev_rsi is not None and prev_rsi <= 50.0 and rsi_value > 50.0)
    )
    rsi_turning_bearish = rsi_value is not None and (
        (prev_rsi is not None and prev_rsi > 70.0 and rsi_value < prev_rsi) or
        (prev_rsi is not None and prev_rsi >= 50.0 and rsi_value < 50.0)
    )
    kalman_turning_bullish = (
        kalman_level is not None and
        prev_kalman_level is not None and
        latest_close > kalman_level and
        prev_close <= prev_kalman_level
    )
    kalman_turning_bearish = (
        kalman_level is not None and
        prev_kalman_level is not None and
        latest_close < kalman_level and
        prev_close >= prev_kalman_level
    )

    turning_bullish_conditions = {
        'order_block_rejection': bullish_ob_rejection,
        'close_holds_above_kalman': kalman_turning_bullish,
        'close_recovers_above_vwap': bool(vwap_signal) if vwap_signal is not None else False,
        'macd_bullish': macd_bullish,
        'rsi_recovering': rsi_turning_bullish,
        'volume_confirming': volume_bullish,
    }
    turning_bearish_conditions = {
        'order_block_rejection': bearish_ob_rejection,
        'close_holds_below_kalman': kalman_turning_bearish,
        'close_capped_below_vwap': bool(bearish_vwap_signal) if bearish_vwap_signal is not None else False,
        'macd_bearish': macd_bearish,
        'rsi_fading': rsi_turning_bearish,
        'volume_confirming': volume_bearish,
    }
    turning_bullish_available = {key: value for key, value in turning_bullish_conditions.items() if key != 'close_recovers_above_vwap' or intraday}
    turning_bearish_available = {key: value for key, value in turning_bearish_conditions.items() if key != 'close_capped_below_vwap' or intraday}
    turning_bullish_count = sum(1 for value in turning_bullish_available.values() if value)
    turning_bearish_count = sum(1 for value in turning_bearish_available.values() if value)
    turning_bullish_threshold = 4 if len(turning_bullish_available) >= 5 else max(3, len(turning_bullish_available))
    turning_bearish_threshold = 4 if len(turning_bearish_available) >= 5 else max(3, len(turning_bearish_available))
    turning_bullish = turning_bullish_count >= turning_bullish_threshold
    turning_bearish = turning_bearish_count >= turning_bearish_threshold

    bearish_context = (
        (prev_rsi is not None and prev_rsi < 50.0) or
        (prev_close <= float(ema20_series.iloc[-2]) if len(ema20_series) > 1 else False) or
        (prev_macd_value is not None and prev_macd_signal_value is not None and prev_macd_value <= prev_macd_signal_value)
    )
    bullish_context = (
        (prev_rsi is not None and prev_rsi > 50.0) or
        (prev_close >= float(ema20_series.iloc[-2]) if len(ema20_series) > 1 else False) or
        (prev_macd_value is not None and prev_macd_signal_value is not None and prev_macd_value >= prev_macd_signal_value)
    )
    turning_bullish = turning_bullish and bearish_context
    turning_bearish = turning_bearish and bullish_context

    # Strong trend detection using prioritized rules
    strong_up = False
    strong_down = False
    try:
        # primary strong up: price above 50SMA and EMA20>EMA50 with confirmation
        if (latest_close > sma50) and (ema20 > ema50) and (st_trend is True or pa_bull is True or ema20_slope > 0) and (rsi_signal is not False):
            strong_up = True
        # secondary relaxed up: price above EMA20 and EMA20 slope positive with at least one supporting signal
        if (latest_close > ema20) and (ema20_slope > 0) and (price_above_sma20 or pa_bull or (st_trend is True) or (rsi_signal is True)):
            strong_up = True

        # primary strong down
        if (latest_close < sma50) and (ema20 < ema50) and (st_trend is False or pa_bear is True or ema20_slope < 0) and (rsi_signal is not True):
            strong_down = True
        # secondary relaxed down
        if (latest_close < ema20) and (ema20_slope < 0) and (not price_above_sma20 or pa_bear or (st_trend is False) or (rsi_signal is False)):
            strong_down = True
    except Exception:
        strong_up = False
        strong_down = False

    # Build signal list — include signals only when available
    signals = []
    # include primary signals: ma crossover and supertrend if available
    signals.append(bool(ma_crossover))
    if st_trend is not None:
        signals.append(bool(st_trend))
    # include price structure if determinable
    if pa_bull is not None:
        signals.append(bool(pa_bull))
    # include momentum signals
    signals.append(bool(price_above_sma20))
    signals.append(bool(price_above_sma50))
    signals.append(bool(ema_signal))
    if rsi_signal is not None:
        signals.append(bool(rsi_signal))
    if kalman_level is not None:
        signals.append(bool(kalman_signal))
    if intraday and vwap_signal is not None:
        signals.append(bool(vwap_signal))

    uptrend = False
    downtrend = False
    trend = 'neutral'
    # prioritize strong flags
    if turning_bullish and not turning_bearish:
        trend = 'turning_bullish'
    elif turning_bearish and not turning_bullish:
        trend = 'turning_bearish'
    elif strong_up:
        uptrend = True
        trend = 'up'
    elif strong_down:
        downtrend = True
        trend = 'down'
    else:
        if signals:
            if vote_mode == 'strict':
                uptrend = all(signals)
                downtrend = not any(signals)
            elif vote_mode == 'any':
                uptrend = any(signals)
                downtrend = not any(signals)
            else:  # majority
                true_count = sum(1 for s in signals if s)
                needed = (len(signals) // 2) + 1
                uptrend = true_count >= needed
                downtrend = (len(signals) - true_count) >= needed
        if uptrend:
            trend = 'up'
        elif downtrend:
            trend = 'down'

    return {
        'ticker': ticker,
        'close': latest_close,
        'lookback_close': lookback_close,
        'percent_change': percent_change,
        'ema20': float(ema20),
        'ema50': float(ema50),
        'sma20': float(sma20),
        'sma50': float(sma50),
        'rsi': rsi_value,
        'rsi_prev': prev_rsi,
        'volume': latest_volume,
        'avg_volume_20': avg_volume_20,
        'volume_ratio': volume_ratio,
        'ema20_slope': float(ema20_slope),
        'ma_crossover': bool(ma_crossover),
        'kalman_level': float(kalman_level) if kalman_level is not None else None,
        'vwap': vwap_latest,
        'macd': macd_value,
        'macd_signal': macd_signal_value,
        'macd_histogram': macd_hist_value,
        'supertrend_up': st_trend,
        'supertrend_ub': st_ub,
        'supertrend_lb': st_lb,
        'price_action_bullish': pa_bull,
        'bullish_order_block': bullish_order_block,
        'bearish_order_block': bearish_order_block,
        'turning_bullish_score': turning_bullish_count,
        'turning_bearish_score': turning_bearish_count,
        'signals': {
            'ma_crossover_sma20_gt_sma50': bool(ma_crossover),
            'price_above_sma20': bool(price_above_sma20),
            'price_above_sma50': bool(price_above_sma50),
            'ema20_gt_ema50': bool(ema_signal),
            'rsi_gte_50': bool(rsi_signal) if rsi_signal is not None else None,
            'ema20_slope_pos': ema20_slope > 0,
            'close_gt_kalman': bool(kalman_signal) if kalman_level is not None else None,
            'close_gt_vwap': bool(vwap_signal) if intraday else None,
            'supertrend_up': bool(st_trend) if st_trend is not None else None,
            'price_action_bullish': bool(pa_bull) if pa_bull is not None else None,
            'turning_bullish': turning_bullish,
            'turning_bearish': turning_bearish,
            'turning_bullish_conditions': turning_bullish_conditions,
            'turning_bearish_conditions': turning_bearish_conditions,
        },
        'trend': trend
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Agentic NIFTY Trend Skill with Fyers Real-time Data')
    parser.add_argument('--indices', type=str, default='nifty50,nifty100,nifty500', help='Comma separated indices')
    parser.add_argument('--lookback', type=int, default=100, help='Lookback days for EMA/Kalman')
    parser.add_argument('--intraday', action='store_true', help='Enable intraday VWAP computation')
    parser.add_argument('--vwap-resolution', type=str, default='5m', help='VWAP resolution for intraday (1m,5m,15m)')
    parser.add_argument('--vote-mode', choices=['strict','majority','any'], default='majority', help='Voting mode for combining signals')
    parser.add_argument('--supertrend-period', type=int, default=10, help='ATR period for SuperTrend')
    parser.add_argument('--supertrend-multiplier', type=float, default=3.0, help='Multiplier for SuperTrend ATR band')
    parser.add_argument('--price-action-lookback', type=int, default=3, help='Lookback bars for simple price-action structure check')
    parser.add_argument('--out', type=str, default=None, help='Output JSON file (default stdout)')
    parser.add_argument('--fyers-api-key', type=str, default=os.getenv('FYERS_API_KEY', ''), help='Fyers API Key (or set FYERS_API_KEY env var)')
    parser.add_argument('--fyers-access-token', type=str, default=os.getenv('FYERS_ACCESS_TOKEN', ''), help='Fyers Access Token (or set FYERS_ACCESS_TOKEN env var)')
    args = parser.parse_args(argv)

    # Update global Fyers credentials if provided via CLI
    global FYERS_API_KEY, FYERS_ACCESS_TOKEN
    if args.fyers_api_key:
        FYERS_API_KEY = args.fyers_api_key
    if args.fyers_access_token:
        FYERS_ACCESS_TOKEN = args.fyers_access_token

    indices = [i.strip() for i in args.indices.split(',') if i.strip()]

    records = []
    for idx in indices:
        try:
            syms = get_constituents_from_wikipedia(idx)
        except Exception as e:
            print(json.dumps({'error': f'failed_loading_constituents_{idx}', 'detail': str(e)}))
            syms = []

        # if no symbols found, skip
        if not syms:
            continue

        for s in syms:
            try:
                item = evaluate_ticker(
                    s,
                    lookback_days=args.lookback,
                    intraday=args.intraday,
                    vwap_resolution=args.vwap_resolution,
                    vote_mode=args.vote_mode,
                    supertrend_period=args.supertrend_period,
                    supertrend_multiplier=args.supertrend_multiplier,
                    price_action_lookback=args.price_action_lookback,
                )
                if item:
                    item.update({'index': idx, 'timestamp': utc_now_iso()})
                    records.append(item)
            except Exception as e:
                # continue on per-ticker errors
                records.append({'ticker': s, 'error': str(e), 'index': idx, 'timestamp': utc_now_iso()})

    # market summary
    total = len([r for r in records if 'trend' in r])
    up = len([r for r in records if r.get('trend') in ('up', 'turning_bullish')])
    down = len([r for r in records if r.get('trend') in ('down', 'turning_bearish')])
    percent_up = (up / total) if total else None
    percent_down = (down / total) if total else None

    market_trend = 'neutral'
    if percent_up is not None:
        if percent_up >= 0.6:
            market_trend = 'bullish'
        elif percent_down >= 0.6:
            market_trend = 'bearish'

    summary = {
        'timestamp': utc_now_iso(),
        'total_tickers': total,
        'percent_up': percent_up,
        'percent_down': percent_down,
        'market_trend': market_trend
    }

    output = {'summary': summary, 'records': records}

    out_text = json.dumps(output, indent=2, default=str)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(out_text)
        print(f'Wrote results to {args.out}')
    else:
        print(out_text)


if __name__ == '__main__':
    main()
