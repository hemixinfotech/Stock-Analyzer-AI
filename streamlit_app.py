import json
import os
import shutil
import subprocess
import sys
import hashlib
import html
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd
import requests
import streamlit as st

try:
    import yfinance as yf
except Exception:
    yf = None

REPO_ROOT = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

JOBS_DIR = REPO_ROOT / "jobs"
LATEST_JOB_DIR = JOBS_DIR / "latest"
INDEX_LABELS = {
    "all_indices": "All Supported Indices",
    "nifty50": "Nifty 50",
    "nifty_midcap_100": "Nifty Midcap 100",
    "nifty_smallcap_100": "Nifty Smallcap 100",
}
INDEX_OPTIONS = list(INDEX_LABELS.keys())
DEFAULT_INDEX = "nifty50"
TRADE_MODE_OPTIONS = ("swing", "intraday")
TRADE_MODE_LABELS = {
    "swing": "Swing (5-10 Days)",
    "intraday": "Intraday",
}
TRADE_MODE_SETTINGS = {
    "swing": {
        "lookback_days": 20,
        "intraday": False,
        "horizon": "5-10 trading days",
        "caption": "Reversal setups meant for short swing trades over the next few sessions.",
    },
    "intraday": {
        "lookback_days": 5,
        "intraday": True,
        "horizon": "same day / next session",
        "caption": "Faster reversal setups for market-hours decisions with intraday VWAP confirmation.",
    },
}
GLOBAL_CUE_SYMBOLS = [
    ("S&P 500", "^GSPC", "risk"),
    ("Nasdaq", "^IXIC", "risk"),
    ("Dow Jones", "^DJI", "risk"),
    ("Nikkei 225", "^N225", "risk"),
    ("Hang Seng", "^HSI", "risk"),
    ("Crude Oil", "CL=F", "commodity"),
    ("Gold", "GC=F", "defensive"),
    ("India VIX", "^INDIAVIX", "volatility"),
]
NEWS_POSITIVE_KEYWORDS = {"beat", "wins", "growth", "surge", "approval", "order", "profit", "upgrade", "bullish", "rally", "strong", "gains", "rises", "jumps", "record"}
NEWS_NEGATIVE_KEYWORDS = {"fall", "drops", "downgrade", "loss", "probe", "weak", "bearish", "cuts", "decline", "miss", "slump", "crash", "penalty", "fraud", "warning"}
NEWS_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "📊 Results": ["results", "quarterly", "q1", "q2", "q3", "q4", "profit", "revenue", "earnings", "pat", "ebitda"],
    "🏆 Order Win": ["order", "contract", "wins", "bags", "secures", "awarded", "deal"],
    "⬆️ Upgrade": ["upgrade", "buy", "outperform", "overweight", "target price raised", "target raised"],
    "⬇️ Downgrade": ["downgrade", "sell", "underperform", "underweight", "target cut", "target reduced"],
    "🔔 Guidance": ["guidance", "outlook", "forecast", "expects", "projects", "foresees"],
    "🏛️ Regulatory": ["sebi", "rbi", "nse", "bse", "cci", "probe", "penalty", "notice", "approval", "fda"],
    "💰 Dividend": ["dividend", "bonus", "buyback", "split"],
    "🤝 Merger/Acquisition": ["merger", "acquisition", "acquires", "takeover", "stake", "joint venture"],
}
BULLISH_TRENDS = {"up", "turning_bullish"}
BEARISH_TRENDS = {"down", "turning_bearish"}
TREND_PRIORITY = {
    "Turning Bullish": 0,
    "Up": 1,
    "Turning Bearish": 0,
    "Down": 1,
}
# Broad base list of high-activity NSE stocks always checked for today's news
BASE_NEWS_STOCKS: tuple[str, ...] = (
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "WIPRO",
    "HINDUNILVR", "ITC", "BAJFINANCE", "AXISBANK", "KOTAKBANK", "LT",
    "ADANIENT", "ADANIPORTS", "TATAMOTORS", "TITAN", "SUNPHARMA",
    "DRREDDY", "CIPLA", "MARUTI", "HEROMOTOCO", "ONGC", "NTPC",
    "POWERGRID", "COALINDIA", "JSWSTEEL", "TATASTEEL", "M&MFIN",
    "ULTRACEMCO", "GRASIM", "ASIANPAINT", "NESTLEIND", "BRITANNIA",
    "BAJAJFINSV", "HCLTECH", "TECHM", "DIVISLAB", "EICHERMOT",
    "APOLLOHOSP", "TATACONSUM", "INDUSINDBK", "BPCL", "PIDILITIND",
    "M&M", "SHRIRAMFIN", "DABUR", "BERGEPAINT", "HAVELLS",
)


def expand_index_selection(index_name: str) -> list[str]:
    if index_name == "all_indices":
        return [item for item in INDEX_OPTIONS if item != "all_indices"]
    return [index_name]


def get_trade_mode_settings(trade_mode: str) -> dict:
    return TRADE_MODE_SETTINGS.get(trade_mode, TRADE_MODE_SETTINGS["swing"])


def status_covers_index(status_payload: dict, index_name: str) -> bool:
    selected_indices = status_payload.get("params", {}).get("indices") or expand_index_selection(
        status_payload.get("params", {}).get("index", DEFAULT_INDEX)
    )
    if index_name == "all_indices":
        return set(expand_index_selection(index_name)).issubset(set(selected_indices))
    return index_name in selected_indices


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def normalize_numeric(value: object, default: float | None = None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def build_turning_trade_levels(record: dict) -> dict[str, float | None]:
    trend = str(record.get("trend", ""))
    close = normalize_numeric(record.get("close"))
    atr14 = normalize_numeric(record.get("atr14"))
    mode_settings = get_trade_mode_settings(str(record.get("trade_mode", "swing")))
    if close is None or trend not in {"turning_bullish", "turning_bearish"}:
        return {"buy_price": None, "target_price": None, "stop_loss": None}

    probability_key = "turning_bullish_probability" if trend == "turning_bullish" else "turning_bearish_probability"
    probability = normalize_numeric(record.get(probability_key), 70.0) or 70.0

    # Risk unit: 1× ATR14 (was 0.9× — use full ATR for realistic stop placement)
    # Floor raised: 0.8% intraday, 1.2% swing (was 0.4% / 0.6% — too tight, got stopped out)
    fallback_risk = close * (0.008 if mode_settings["intraday"] else 0.015)
    risk_unit = atr14 * 1.0 if atr14 is not None and atr14 > 0 else fallback_risk
    risk_unit = max(risk_unit, close * (0.008 if mode_settings["intraday"] else 0.012))

    # Reward multiple scales with probability:
    # At 70% → 1.8R (intraday) / 2.0R (swing)
    # At 90% → 2.3R (intraday) / 2.8R (swing)
    # Minimum 1.5R enforced to ensure trades are worth taking
    if mode_settings["intraday"]:
        reward_multiple = clamp(1.8 + ((probability - 70.0) / 40.0), 1.5, 2.5)
    else:
        reward_multiple = clamp(2.0 + ((probability - 70.0) / 35.0), 1.8, 3.0)

    if trend == "turning_bullish":
        target_price = close + (risk_unit * reward_multiple)
        stop_loss = close - risk_unit
    else:
        target_price = close - (risk_unit * reward_multiple)
        stop_loss = close + risk_unit

    return {
        "buy_price": round(close, 2),
        "target_price": round(target_price, 2),
        "stop_loss": round(stop_loss, 2),
    }
def classify_headline_signal(headline: str) -> str:
    text = headline.lower()
    positive_hits = sum(1 for keyword in NEWS_POSITIVE_KEYWORDS if keyword in text)
    negative_hits = sum(1 for keyword in NEWS_NEGATIVE_KEYWORDS if keyword in text)
    if positive_hits > negative_hits:
        return "Bullish"
    if negative_hits > positive_hits:
        return "Bearish"
    return "Neutral"


def detect_news_category(headline: str) -> str:
    text = headline.lower()
    for category, keywords in NEWS_CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return "📰 News"


def extract_close_series(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty:
        return pd.Series(dtype=float)
    if isinstance(history.columns, pd.MultiIndex):
        close_columns = [column for column in history.columns if column[0] == "Close"]
        if close_columns:
            return history[close_columns[0]].dropna()
        return pd.Series(dtype=float)
    if "Close" not in history.columns:
        return pd.Series(dtype=float)
    return history["Close"].dropna()


@st.cache_data(show_spinner=False, ttl=900)
def fetch_stock_news_rows(stocks: tuple[str, ...]) -> list[dict]:
    from email.utils import parsedate_to_datetime
    today = datetime.now(timezone.utc).date()
    yesterday = today - __import__("datetime").timedelta(days=1)
    rows = []
    for stock_name in stocks:
        # when:1d tells Google News to return only last 24 hours
        query = f'"{stock_name}" NSE stock results OR order OR guidance OR upgrade OR downgrade'
        url = (
            f"https://news.google.com/rss/search?q={requests.utils.quote(query)}"
            f"+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            response = requests.get(url, timeout=8)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            items = root.findall("./channel/item")[:5]
            if not items:
                continue
            headlines = []
            for item in items:
                headline = str(item.findtext("title", "")).strip()
                link = str(item.findtext("link", "")).strip()
                published = str(item.findtext("pubDate", "")).strip()
                if not headline:
                    continue
                # Secondary date guard: skip anything older than yesterday
                try:
                    pub_date = parsedate_to_datetime(published).date()
                    if pub_date < yesterday:
                        continue
                except Exception:
                    pass
                headlines.append({
                    "headline": headline,
                    "link": link,
                    "published": published,
                    "signal": classify_headline_signal(headline),
                    "category": detect_news_category(headline),
                })
                if len(headlines) == 3:
                    break
            if not headlines:
                continue
            signals = [h["signal"] for h in headlines]
            bullish_count = signals.count("Bullish")
            bearish_count = signals.count("Bearish")
            overall_signal = "Bullish" if bullish_count > bearish_count else "Bearish" if bearish_count > bullish_count else signals[0]
            rows.append({
                "stock": stock_name,
                "headlines": headlines,
                "signal": overall_signal,
            })
        except Exception:
            continue
    return rows


@st.cache_data(show_spinner=False, ttl=300)
def fetch_stock_price_data(stocks: tuple[str, ...]) -> dict[str, dict]:
    if yf is None:
        return {}
    import logging
    # Suppress yfinance download warnings for delisted/invalid symbols
    yf_logger = logging.getLogger("yfinance")
    prev_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    prices: dict[str, dict] = {}
    for stock_name in stocks:
        ticker_symbol = f"{stock_name}.NS"
        try:
            history = yf.download(ticker_symbol, period="5d", progress=False, threads=False, auto_adjust=False)
            close = extract_close_series(history)
            if len(close) < 2:
                continue
            ltp = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            change_pct = ((ltp - prev_close) / prev_close) * 100.0 if prev_close else 0.0
            volume_series = history.get("Volume") if isinstance(history.get("Volume") if hasattr(history, "get") else None, pd.Series) else None
            vol = None
            if "Volume" in history.columns:
                vol_col = history["Volume"]
                if isinstance(vol_col, pd.DataFrame):
                    vol_col = vol_col.iloc[:, 0]
                vol = int(vol_col.iloc[-1]) if not vol_col.empty else None
            prices[stock_name] = {
                "ltp": round(ltp, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "volume": vol,
            }
        except Exception:
            continue
    yf_logger.setLevel(prev_level)
    return prices


@st.cache_data(show_spinner=False, ttl=900)
def fetch_global_cues_rows() -> tuple[list[dict], str]:
    rows = []
    score = 0
    for label, symbol, cue_type in GLOBAL_CUE_SYMBOLS:
        if yf is None:
            break
        try:
            history = yf.download(symbol, period="5d", progress=False, threads=False, auto_adjust=False)
        except Exception:
            continue
        close = extract_close_series(history)
        if len(close) < 2:
            continue
        latest = float(close.iloc[-1])
        previous = float(close.iloc[-2])
        if previous == 0:
            continue
        percent_change = ((latest - previous) / previous) * 100.0
        if cue_type == "volatility":
            signal = "Bearish" if percent_change > 1.0 else "Bullish" if percent_change < -1.0 else "Neutral"
        elif cue_type == "defensive":
            signal = "Bearish" if percent_change > 0.5 else "Bullish" if percent_change < -0.5 else "Neutral"
        else:
            signal = "Bullish" if percent_change > 0.3 else "Bearish" if percent_change < -0.3 else "Neutral"
        if signal == "Bullish":
            score += 1
        elif signal == "Bearish":
            score -= 1
        rows.append(
            {
                "market": label,
                "last": round(latest, 2),
                "change_pct": round(percent_change, 2),
                "signal": signal,
            }
        )
    overall = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Neutral"
    return rows, overall


def build_market_intelligence_payload(payload: dict | None) -> tuple[list[dict], list[dict], str]:
    # Build analysis stock list (ranked by trend importance)
    analysis_stocks: list[str] = []
    trend_map: dict[str, str] = {}
    if isinstance(payload, dict):
        records = payload.get("records", [])
        valid_records = [r for r in records if isinstance(r, dict) and r.get("ticker")]

        def rank_record(record: dict) -> float:
            trend = record.get("trend", "")
            base = 100.0 if "turning" in str(trend) else 50.0 if trend in ("up", "down") else 0.0
            prob = max(
                normalize_numeric(record.get("turning_bullish_probability"), 0.0) or 0.0,
                normalize_numeric(record.get("turning_bearish_probability"), 0.0) or 0.0,
            )
            return base + prob * 100

        ranked_records = sorted(valid_records, key=rank_record, reverse=True)
        analysis_stocks = [str(r.get("ticker", "")).replace(".NS", "") for r in ranked_records]
        trend_map = {str(r.get("ticker", "")).replace(".NS", ""): r.get("trend", "") for r in ranked_records}

    # Merge: analysis stocks first (they have trend context), then base stocks, deduped
    seen: set[str] = set()
    merged_stocks: list[str] = []
    for s in list(analysis_stocks) + list(BASE_NEWS_STOCKS):
        if s and s not in seen:
            seen.add(s)
            merged_stocks.append(s)

    news_rows = fetch_stock_news_rows(tuple(merged_stocks))
    price_data = fetch_stock_price_data(tuple(merged_stocks))

    for row in news_rows:
        stock = row.get("stock", "")
        row["price"] = price_data.get(stock, {})
        row["trend"] = trend_map.get(stock, "")

    global_cue_rows, overall_global_cue = fetch_global_cues_rows()
    return news_rows, global_cue_rows, overall_global_cue


def apply_page_style() -> None:
    st.markdown(
        """
        <style>
        /* ── Hide ALL Streamlit chrome including the header bar ── */
        #MainMenu, footer,
        header[data-testid="stHeader"],
        [data-testid="stFooter"], [data-testid="stFooter"] *,
        [data-testid="stStatusWidget"], [data-testid="stDecoration"],
        [data-testid="stHeaderActionElements"], [data-testid="stHeaderActionElements"] *,
        .stHeaderActionElements, [data-testid="stAppViewContainer__fork-button"],
        [data-testid="stGithubButton"], [data-testid="stRepoButton"],
        [data-testid="stToolbarActions"] a,
        header[data-testid="stHeader"] [data-testid="stToolbarActions"],
        header[data-testid="stHeader"] a, header[data-testid="stHeader"] [role="link"],
        .stAppDeployButton, a[href*="streamlit.io"], a[href*="share.streamlit.io"],
        a[href*="github.com"], [data-testid="stSidebar"],
        [data-testid="collapsedControl"], [data-testid="stSidebarCollapseButton"],
        button[aria-label="Close sidebar"], button[aria-label="Open sidebar"] {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
            min-height: 0 !important;
        }

        /* ── Zero out the gap the header leaves behind ── */
        .stApp > header { display: none !important; height: 0 !important; }
        .stApp [data-testid="stAppViewContainer"] { padding-top: 0 !important; }
        .stApp { margin-top: 0 !important; }

        /* ── App background ── */
        .stApp {
            background: linear-gradient(180deg, #f0f4ff 0%, #e8f0fe 100%);
        }
        .block-container {
            padding-top: 0 !important;
            padding-bottom: 2rem;
            max-width: 1400px !important;
        }

        /* ── Hero header banner ── */
        .hero-banner {
            background: linear-gradient(135deg, #0a0f2e 0%, #0f2070 55%, #1a3fa8 100%);
            border-radius: 0 0 20px 20px;
            padding: 1.4rem 2rem 1.3rem 2rem;
            margin-top: 0;
            margin-bottom: 0;
            box-shadow: 0 8px 32px rgba(10,15,70,0.25);
            border: none;
            position: relative;
            overflow: hidden;
        }
        .hero-banner::before {
            content: "";
            position: absolute;
            top: -60px; right: -60px;
            width: 220px; height: 220px;
            background: radial-gradient(circle, rgba(96,165,250,0.18) 0%, transparent 70%);
            pointer-events: none;
        }
        .hero-title {
            font-size: 2rem;
            font-weight: 900;
            color: #ffffff;
            letter-spacing: -0.03em;
            margin: 0 0 0.2rem 0;
            line-height: 1.15;
        }
        .hero-title .accent { color: #60a5fa; }
        .hero-subtitle {
            font-size: 0.88rem;
            color: #93c5fd;
            margin: 0;
            font-weight: 400;
        }

        /* ── Control strip ── */
        .control-strip {
            background: #ffffff;
            border-radius: 0 0 16px 16px;
            border: 1px solid #e2e8f0;
            border-top: none;
            padding: 0.75rem 1.2rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 4px 16px rgba(15,23,42,0.07);
        }
        .ctrl-label {
            font-size: 0.78rem;
            font-weight: 700;
            color: #1e40af;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-bottom: 4px;
        }

        /* ── Mode info pill ── */
        .mode-pill {
            background: #f0f4ff;
            border: 1.5px solid #bfdbfe;
            border-radius: 10px;
            padding: 7px 14px;
            color: #1e40af;
            font-size: 0.8rem;
            font-weight: 600;
            white-space: normal;
            word-break: break-word;
            display: block;
            line-height: 1.6;
        }
        .mode-pill strong { color: #1d4ed8; font-weight: 800; }

        /* ── Progress bar override ── */
        .stProgress > div > div {
            background: linear-gradient(90deg, #2563eb, #60a5fa) !important;
            border-radius: 99px !important;
        }
        .stProgress { margin-bottom: 0.5rem; }

        /* ── Tabs styling ── */
        .stTabs [data-baseweb="tab-list"] {
            background: #ffffff;
            border-radius: 12px;
            padding: 4px;
            gap: 4px;
            box-shadow: 0 2px 8px rgba(15,23,42,0.08);
            margin-bottom: 1rem;
        }
        .stTabs [data-baseweb="tab"] {
            border-radius: 9px;
            font-weight: 600;
            font-size: 0.88rem;
            padding: 0.5rem 1.1rem;
            color: #64748b;
            border: none !important;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
            color: #ffffff !important;
        }
        .stTabs [data-baseweb="tab-border"] { display: none !important; }
        .stTabs [data-baseweb="tab-panel"] { padding-top: 0.5rem; }

        /* ── Dashboard hero ── */
        .dashboard-hero {
            background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 100%);
            color: white;
            padding: 1rem 1.4rem;
            border-radius: 16px;
            box-shadow: 0 12px 32px rgba(30,64,175,0.22);
            margin-bottom: 1rem;
            border: 1px solid rgba(191,219,254,0.18);
        }
        .dashboard-hero h2 {
            color: #ffffff; margin: 0; font-size: 1.4rem; font-weight: 700;
        }
        .dashboard-hero p {
            margin: 0.35rem 0 0 0; color: #dbeafe; font-size: 0.88rem;
        }

        /* ── Metric cards ── */
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 0.9rem;
            box-shadow: 0 4px 16px rgba(37,99,235,0.08);
        }

        /* ── Trend tables ── */
        .trend-card {
            background: #ffffff;
            border: 1px solid #dbeafe;
            border-radius: 16px;
            padding: 1rem 1rem 0.5rem 1rem;
            box-shadow: 0 4px 16px rgba(15,23,42,0.07);
            margin-bottom: 1rem;
        }
        .trend-card.uptrend-card {
            border-top: 4px solid #16a34a;
            background: linear-gradient(180deg, #f0fdf4 0%, #ffffff 25%);
        }
        .trend-card.downtrend-card {
            border-top: 4px solid #dc2626;
            background: linear-gradient(180deg, #fef2f2 0%, #ffffff 25%);
        }
        .trend-card h3 { margin-top: 0.1rem; margin-bottom: 0.8rem; }
        .trend-table-wrapper { width: 100%; overflow-x: auto; }
        .trend-table {
            width: 100%; border-collapse: separate; border-spacing: 0;
            border-radius: 12px; overflow: hidden;
        }
        .trend-table th, .trend-table td {
            text-align: center; vertical-align: middle; padding: 9px 12px;
        }
        .trend-table th { text-transform: uppercase; font-weight: 700; color: #0f172a; }
        .trend-table.up-table { border: 1px solid #16a34a; }
        .trend-table.up-table th { background: #bbf7d0; border-bottom: 2px solid #16a34a; }
        .trend-table.up-table td { background: #f0fdf4; border-bottom: 1px solid rgba(22,163,74,0.12); }
        .trend-table.down-table { border: 1px solid #dc2626; }
        .trend-table.down-table th { background: #fecaca; border-bottom: 2px solid #dc2626; }
        .trend-table.down-table td { background: #fef2f2; border-bottom: 1px solid rgba(220,38,38,0.12); }

        /* ── AI Predictor cards ── */
        .predictor-card {
            background: #ffffff; border-radius: 16px; padding: 1rem;
            box-shadow: 0 8px 24px rgba(15,23,42,0.08); margin-bottom: 1rem;
            border: 1px solid rgba(191,219,254,0.9); overflow: hidden;
        }
        .predictor-card.up-card {
            border-top: 4px solid #16a34a;
            background: linear-gradient(180deg, #ecfdf5 0%, #ffffff 30%);
        }
        .predictor-card.down-card {
            border-top: 4px solid #dc2626;
            background: linear-gradient(180deg, #fef2f2 0%, #ffffff 30%);
        }
        .predictor-card h3 { margin: 0 0 0.25rem 0; font-size: 1.1rem; font-weight: 800; }
        .predictor-card p { margin: 0 0 0.7rem 0; color: #475569; font-size: 0.88rem; }
        .predictor-badge {
            display: inline-flex; align-items: center; padding: 0.22rem 0.6rem;
            border-radius: 999px; font-size: 0.72rem; font-weight: 800;
            letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 0.6rem;
        }
        .predictor-card.up-card .predictor-badge { background: rgba(22,163,74,0.12); color: #15803d; }
        .predictor-card.down-card .predictor-badge { background: rgba(220,38,38,0.12); color: #b91c1c; }
        .predictor-table-wrapper { width: 100%; overflow-x: auto; border-radius: 14px; }
        .predictor-table {
            width: 100%; border-collapse: separate; border-spacing: 0;
            overflow: hidden; border-radius: 14px;
        }
        .predictor-table th, .predictor-table td {
            padding: 0.75rem 0.9rem; text-align: center; vertical-align: middle;
        }
        .predictor-table th { text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.74rem; font-weight: 800; }
        .predictor-table tr:last-child td { border-bottom: none; }
        .predictor-table.up-table { border: 1px solid rgba(22,163,74,0.28); }
        .predictor-table.up-table th { background: linear-gradient(180deg, #22c55e 0%, #16a34a 100%); color: #f0fdf4; }
        .predictor-table.up-table td { background: rgba(240,253,244,0.95); border-bottom: 1px solid rgba(22,163,74,0.14); color: #14532d; }
        .predictor-table.up-table tbody tr:nth-child(even) td { background: rgba(220,252,231,0.92); }
        .predictor-table.down-table { border: 1px solid rgba(220,38,38,0.24); }
        .predictor-table.down-table th { background: linear-gradient(180deg, #ef4444 0%, #dc2626 100%); color: #fef2f2; }
        .predictor-table.down-table td { background: rgba(254,242,242,0.96); border-bottom: 1px solid rgba(220,38,38,0.14); color: #7f1d1d; }
        .predictor-table.down-table tbody tr:nth-child(even) td { background: rgba(254,226,226,0.92); }

        /* ── Global cue card ── */
        .cue-card {
            background: #ffffff; border-radius: 14px; padding: 14px 16px;
            border: 1px solid #e2e8f0; margin-bottom: 10px;
            box-shadow: 0 2px 8px rgba(15,23,42,0.06);
            display: flex; align-items: center; justify-content: space-between;
        }
        .cue-name { font-size: 0.9rem; font-weight: 700; color: #1e293b; }
        .cue-price { font-size: 0.85rem; color: #475569; margin-top: 2px; }

        /* ── Buttons ── */
        .stButton > button {
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            color: #ffffff; border: none; border-radius: 10px;
            padding: 0.55rem 1rem; font-weight: 700;
            box-shadow: 0 6px 16px rgba(37,99,235,0.28);
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
            color: #ffffff;
        }

        /* ── Misc ── */
        .stAlert { border-radius: 12px; }
        div[data-testid="stDataFrame"] {
            background: #ffffff; border: 1px solid #dbeafe;
            border-radius: 14px; padding: 0.45rem;
            box-shadow: 0 4px 14px rgba(15,23,42,0.07);
        }
        h1, h2, h3 { color: #0f172a; }
        @media (max-width: 768px) {
            .block-container { padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_runtime_setting(name: str, default: str = "") -> str:
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value

    try:
        secret_value = st.secrets.get(name)
    except Exception:
        secret_value = None
    if isinstance(secret_value, str) and secret_value.strip():
        return secret_value.strip()

    try:
        github_section = st.secrets.get("github")
    except Exception:
        github_section = None
    if hasattr(github_section, "get"):
        for candidate_key in (name, name.lower(), name.removeprefix("GITHUB_").lower()):
            candidate_value = github_section.get(candidate_key)
            if isinstance(candidate_value, str) and candidate_value.strip():
                return candidate_value.strip()

    return default


def get_project_python() -> str:
    configured = get_runtime_setting("PROJECT_PYTHON")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    return sys.executable


def clear_latest_job_dir() -> None:
    if not LATEST_JOB_DIR.exists():
        return
    for child in LATEST_JOB_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def records_have_rsi_values(records: list[dict]) -> bool:
    return any(record.get("rsi") is not None for record in records if isinstance(record, dict))


def to_portable_artifact_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_artifact_path(path_value: str | Path) -> Path:
    raw_path = str(path_value)
    normalized_raw_path = raw_path.replace("\\", "/")
    candidate = Path(normalized_raw_path).expanduser()
    if candidate.exists():
        return candidate
    if not candidate.is_absolute():
        repo_relative = (REPO_ROOT / candidate).resolve()
        if repo_relative.exists():
            return repo_relative

    for pure_path in (PureWindowsPath(raw_path), PurePosixPath(raw_path)):
        parts = [part for part in pure_path.parts if part not in (pure_path.anchor, pure_path.root, "\\", "/")]
        if "jobs" in parts:
            jobs_relative = REPO_ROOT.joinpath(*parts[parts.index("jobs"):])
            if jobs_relative.exists():
                return jobs_relative

    return candidate


def sanitize_index_name(index_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in index_name.strip().lower())


def build_index_summary(records: list[dict]) -> dict:
    total = len([record for record in records if "trend" in record])
    up = len([record for record in records if record.get("trend") in BULLISH_TRENDS])
    down = len([record for record in records if record.get("trend") in BEARISH_TRENDS])
    percent_up = (up / total) if total else None
    percent_down = (down / total) if total else None
    market_trend = "neutral"
    if percent_up is not None:
        if percent_up >= 0.6:
            market_trend = "bullish"
        elif percent_down is not None and percent_down >= 0.6:
            market_trend = "bearish"
    return {
        "timestamp": utc_now(),
        "total_tickers": total,
        "percent_up": percent_up,
        "percent_down": percent_down,
        "market_trend": market_trend,
        "trade_mode": str(records[0].get("trade_mode", "swing")) if records else "swing",
        "trade_horizon": str(records[0].get("trade_horizon", "5-10 trading days")) if records else "5-10 trading days",
    }


def write_per_index_json_files(output_json: Path, out_dir: Path, indices: list[str]) -> dict:
    combined_payload = load_json(output_json)
    all_records = combined_payload.get("records", [])
    artifacts = {}
    for index_name in indices:
        index_records = [record for record in all_records if record.get("index") == index_name]
        index_payload = {
            "summary": build_index_summary(index_records),
            "records": index_records,
        }
        out_path = out_dir / f"results_trend_{sanitize_index_name(index_name)}.json"
        write_json(out_path, index_payload)
        artifacts[f"{sanitize_index_name(index_name)}_json_report"] = to_portable_artifact_path(out_path)
    return artifacts


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("PROJECT_PYTHON", "FYERS_API_KEY", "FYERS_ACCESS_TOKEN", "GITHUB_TOKEN", "GITHUB_MODEL"):
        value = get_runtime_setting(name)
        if value:
            env[name] = value
    return env


def run_command(
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    command_env = build_subprocess_env()
    if extra_env:
        command_env.update(extra_env)
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, env=command_env)
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    return result


def refresh_trend_data(index_name: str, trade_mode: str) -> dict:
    clear_latest_job_dir()
    LATEST_JOB_DIR.mkdir(parents=True, exist_ok=True)
    selected_indices = expand_index_selection(index_name)
    mode_settings = get_trade_mode_settings(trade_mode)
    lookback = int(mode_settings["lookback_days"])
    intraday = bool(mode_settings["intraday"])

    output_json = LATEST_JOB_DIR / "results_nifty_trend.json"

    agent_stdout = LATEST_JOB_DIR / "agent.stdout.log"
    agent_stderr = LATEST_JOB_DIR / "agent.stderr.log"

    python_executable = get_project_python()
    agent_cmd = [
        python_executable,
        str(REPO_ROOT / "scripts" / "agent_nifty_trend.py"),
        "--indices", ",".join(selected_indices),
        "--trade-mode", trade_mode,
        "--lookback", str(lookback),
        "--out", str(output_json),
    ]

    progress = st.progress(5, text="Preparing output folder...")
    progress.progress(20, text="Running trend analyzer...")
    analyzer_result = run_command(agent_cmd, agent_stdout, agent_stderr)
    if analyzer_result.returncode != 0:
        failure_payload = {
            "status": "failed",
            "failed_step": "agent_nifty_trend.py",
            "exit_code": analyzer_result.returncode,
            "error_excerpt": (analyzer_result.stderr or analyzer_result.stdout or "").strip()[-4000:],
            "artifacts": {
                "job_dir": to_portable_artifact_path(LATEST_JOB_DIR),
                "results_json": to_portable_artifact_path(output_json),
                "agent_stdout_log": to_portable_artifact_path(agent_stdout),
                "agent_stderr_log": to_portable_artifact_path(agent_stderr),
            },
            "params": {
                "index": index_name,
                "indices": selected_indices,
                "trade_mode": trade_mode,
                "lookback": lookback,
                "intraday": intraday,
                "python_executable": python_executable,
            },
        }
        write_json(LATEST_JOB_DIR / "status.json", failure_payload)
        progress.progress(100, text="Trend refresh failed.")
        return failure_payload

    progress.progress(70, text="Preparing selected index data...")
    per_index_artifacts = write_per_index_json_files(output_json, LATEST_JOB_DIR, selected_indices)

    status_payload = {
        "status": "completed",
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "artifacts": {
            "job_dir": to_portable_artifact_path(LATEST_JOB_DIR),
            "results_json": to_portable_artifact_path(output_json),
            "agent_stdout_log": to_portable_artifact_path(agent_stdout),
            "agent_stderr_log": to_portable_artifact_path(agent_stderr),
            **per_index_artifacts,
        },
        "params": {
            "index": index_name,
            "indices": selected_indices,
            "trade_mode": trade_mode,
            "lookback": lookback,
            "intraday": intraday,
            "python_executable": python_executable,
        },
    }
    progress.progress(100, text="Trend refresh complete.")

    write_json(LATEST_JOB_DIR / "status.json", status_payload)
    return status_payload


def load_index_payload_from_status(status_payload: dict, index_name: str) -> dict | None:
    if index_name == "all_indices":
        artifacts = status_payload.get("artifacts", {})
        combined_report_path = artifacts.get("results_json")
        if combined_report_path:
            resolved_combined_report_path = resolve_artifact_path(combined_report_path)
            if resolved_combined_report_path.exists():
                return load_json(resolved_combined_report_path)
        return None

    artifacts = status_payload.get("artifacts", {})
    artifact_key = f"{sanitize_index_name(index_name)}_json_report"
    report_path = artifacts.get(artifact_key)
    payload = None
    resolved_report_path = None
    if report_path:
        resolved_report_path = resolve_artifact_path(report_path)
        if resolved_report_path.exists():
            payload = load_json(resolved_report_path)

    combined_report_path = artifacts.get("results_json")
    if combined_report_path:
        resolved_combined_report_path = resolve_artifact_path(combined_report_path)
        if resolved_combined_report_path.exists():
            combined_payload = load_json(resolved_combined_report_path)
            combined_records = [
                record for record in combined_payload.get("records", [])
                if record.get("index") == index_name
            ]
            if combined_records and (payload is None or not records_have_rsi_values(payload.get("records", []))):
                payload = {
                    "summary": build_index_summary(combined_records),
                    "records": combined_records,
                }
                if resolved_report_path is not None:
                    write_json(resolved_report_path, payload)

    return payload


def get_index_report_path_from_status(status_payload: dict, index_name: str) -> Path | None:
    if index_name == "all_indices":
        report_path = status_payload.get("artifacts", {}).get("results_json")
        if not report_path:
            return None
        resolved_report_path = resolve_artifact_path(report_path)
        if resolved_report_path.exists():
            return resolved_report_path
        return None

    artifacts = status_payload.get("artifacts", {})
    artifact_key = f"{sanitize_index_name(index_name)}_json_report"
    report_path = artifacts.get(artifact_key)
    if not report_path:
        return None
    resolved_report_path = resolve_artifact_path(report_path)
    if resolved_report_path.exists():
        return resolved_report_path
    return None


def run_openai_predictor(index_name: str, status_payload: dict) -> dict:
    return run_ai_predictor(index_name, status_payload, predictor_mode="all")


def compute_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_cache_number(value: object) -> object:
    if isinstance(value, float):
        return round(value, 4)
    return value


def build_predictor_cache_fingerprint(index_name: str, payload: dict, predictor_mode: str) -> str:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if predictor_mode == "turning":
        allowed_trends = {"turning_bullish", "turning_bearish"}
        selected_fields = [
            "ticker", "trend", "close", "rsi", "percent_change", "volume",
            "avg_volume_20", "volume_ratio", "atr14",
            "turning_bullish_score", "turning_bearish_score",
            "turning_bullish_probability", "turning_bearish_probability", "index",
        ]
    else:
        allowed_trends = {"up", "down"}
        selected_fields = [
            "ticker", "trend", "close", "rsi", "percent_change", "volume",
            "avg_volume_20", "volume_ratio", "ema20_slope", "ma_crossover",
        ]

    filtered_records = []
    for record in records:
        if not isinstance(record, dict) or record.get("trend") not in allowed_trends:
            continue
        filtered_record = {}
        for field in selected_fields:
            if field in record:
                filtered_record[field] = normalize_cache_number(record.get(field))
        filtered_records.append(filtered_record)

    filtered_records.sort(key=lambda item: (str(item.get("ticker", "")), str(item.get("trend", ""))))
    fingerprint_payload = {
        "index": sanitize_index_name(index_name),
        "predictor_mode": predictor_mode,
        "market_trend": summary.get("market_trend"),
        "records": filtered_records,
    }
    return hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")).hexdigest()


@st.cache_data(show_spinner=False, ttl=900)
def run_ai_predictor_cached(
    index_name: str,
    predictor_mode: str,
    report_path_str: str,
    predictor_fingerprint: str,
    python_executable: str,
) -> dict:
    output_path = LATEST_JOB_DIR / f"prediction_{sanitize_index_name(index_name)}_{predictor_mode}.json"
    predictor_stdout = LATEST_JOB_DIR / f"predictor_{predictor_mode}.stdout.log"
    predictor_stderr = LATEST_JOB_DIR / f"predictor_{predictor_mode}.stderr.log"
    predictor_cmd = [
        python_executable,
        str(REPO_ROOT / "scripts" / "github_stock_predictor.py"),
        "--input",
        report_path_str,
        "--index",
        index_name,
        "--mode",
        predictor_mode,
        "--out",
        str(output_path),
    ]
    predictor_result = run_command(predictor_cmd, predictor_stdout, predictor_stderr)
    if predictor_result.returncode != 0:
        raise ValueError((predictor_result.stderr or predictor_result.stdout or "OpenAI predictor failed.").strip())
    if not output_path.exists():
        raise ValueError("GitHub Models predictor did not create an output file.")
    return load_json(output_path)


def run_ai_predictor(index_name: str, status_payload: dict, predictor_mode: str = "all") -> dict:
    payload = load_index_payload_from_status(status_payload, index_name)
    if payload is None:
        raise ValueError(f"Refreshed data is unavailable for {index_name}. Please refresh the data first.")

    report_path = get_index_report_path_from_status(status_payload, index_name)
    if report_path is None:
        raise ValueError(f"Refreshed report file is unavailable for {index_name}. Please refresh the data first.")

    report_hash = compute_file_hash(report_path)
    predictor_fingerprint = build_predictor_cache_fingerprint(index_name, payload, predictor_mode)
    prediction_payload = run_ai_predictor_cached(
        index_name=index_name,
        predictor_mode=predictor_mode,
        report_path_str=str(report_path),
        predictor_fingerprint=predictor_fingerprint,
        python_executable=get_project_python(),
    )
    output_path = LATEST_JOB_DIR / f"prediction_{sanitize_index_name(index_name)}_{predictor_mode}.json"
    write_json(output_path, prediction_payload)
    return prediction_payload


def render_summary(summary: dict) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Tickers", summary.get("total_tickers", 0))
    col2.metric("Percent Up", f"{(summary.get('percent_up') or 0) * 100:.2f}%")
    col3.metric("Percent Down", f"{(summary.get('percent_down') or 0) * 100:.2f}%")
    col4.metric("Market Trend", str(summary.get("market_trend", "unknown")).title())
    horizon = str(summary.get("trade_horizon", "")).strip()
    mode = str(summary.get("trade_mode", "swing")).strip()
    if horizon:
        st.caption(f"Mode: {TRADE_MODE_LABELS.get(mode, mode.title())} | Horizon: {horizon}")


def render_news_tab(news_rows: list[dict]) -> None:
    today_str = datetime.now(timezone.utc).strftime("%d %b %Y")
    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">'
        f'<div style="font-size:1rem;font-weight:700;color:#0f172a;">📰 Stocks In News Today</div>'
        f'<div style="font-size:0.78rem;color:#64748b;background:#f1f5f9;border-radius:8px;padding:3px 10px;">'
        f'🕐 {today_str} &nbsp;·&nbsp; Last 24 hrs &nbsp;·&nbsp; {len(news_rows)} stock(s)</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not news_rows:
        st.info("No stocks with recent news in the last 24 hours. Markets may be closed or news feeds are quiet.")
        return
    cols = st.columns(2, gap="medium")
    for idx, row in enumerate(news_rows):
        stock = str(row.get("stock", ""))
        signal = str(row.get("signal", "Neutral"))
        trend = str(row.get("trend", ""))
        price = row.get("price", {})
        headlines = row.get("headlines", [])

        signal_color = "#16a34a" if signal == "Bullish" else "#dc2626" if signal == "Bearish" else "#64748b"
        signal_bg = "#dcfce7" if signal == "Bullish" else "#fee2e2" if signal == "Bearish" else "#f1f5f9"
        signal_icon = "▲" if signal == "Bullish" else "▼" if signal == "Bearish" else "●"

        trend_label = ""
        if "turning_bullish" in trend:
            trend_label = '<span style="background:#bbf7d0;color:#15803d;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:700;margin-left:5px;">Turning ▲</span>'
        elif "turning_bearish" in trend:
            trend_label = '<span style="background:#fecaca;color:#b91c1c;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:700;margin-left:5px;">Turning ▼</span>'
        elif trend == "up":
            trend_label = '<span style="background:#dcfce7;color:#16a34a;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:700;margin-left:5px;">Uptrend</span>'
        elif trend == "down":
            trend_label = '<span style="background:#fee2e2;color:#dc2626;border-radius:4px;padding:1px 6px;font-size:0.68rem;font-weight:700;margin-left:5px;">Downtrend</span>'

        ltp = price.get("ltp")
        change_pct = price.get("change_pct")
        vol = price.get("volume")
        price_html = ""
        if ltp is not None and change_pct is not None:
            chg_color = "#16a34a" if change_pct >= 0 else "#dc2626"
            chg_arrow = "▲" if change_pct >= 0 else "▼"
            vol_str = f"&nbsp;·&nbsp;Vol: {vol:,}" if vol else ""
            price_html = (
                f'<div style="display:flex;align-items:baseline;gap:8px;margin:5px 0 8px 0;">'
                f'<span style="font-size:1.1rem;font-weight:800;color:#1e293b;">₹{ltp:,.2f}</span>'
                f'<span style="font-size:0.85rem;font-weight:700;color:{chg_color};">{chg_arrow} {abs(change_pct):.2f}%</span>'
                f'<span style="font-size:0.72rem;color:#94a3b8;">{vol_str}</span>'
                f'</div>'
            )

        headlines_html = ""
        for h in headlines:
            cat = html.escape(str(h.get("category", "📰 News")))
            hlink = html.escape(str(h.get("link", "")), quote=True)
            htitle = html.escape(str(h.get("headline", "")))
            hsignal = str(h.get("signal", "Neutral"))
            hcolor = "#16a34a" if hsignal == "Bullish" else "#dc2626" if hsignal == "Bearish" else "#64748b"
            hbg = "#f0fdf4" if hsignal == "Bullish" else "#fff1f2" if hsignal == "Bearish" else "#f8fafc"
            headlines_html += (
                f'<div style="background:{hbg};border-left:3px solid {hcolor};border-radius:0 5px 5px 0;'
                f'padding:6px 10px;margin-bottom:6px;">'
                f'<span style="font-size:0.67rem;background:#e2e8f0;color:#475569;border-radius:3px;'
                f'padding:1px 5px;margin-right:5px;font-weight:600;">{cat}</span>'
                f'<a href="{hlink}" target="_blank" style="font-size:0.8rem;color:#1e40af;line-height:1.35;'
                f'text-decoration:none;">{htitle}</a>'
                f'</div>'
            )

        card_html = (
            '<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;'
            'padding:12px 14px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div style="font-size:0.95rem;font-weight:800;color:#0f172a;">{html.escape(stock)}{trend_label}</div>'
            f'<span style="background:{signal_bg};color:{signal_color};border-radius:12px;'
            f'padding:2px 10px;font-size:0.72rem;font-weight:700;white-space:nowrap;">'
            f'{signal_icon} {signal}</span>'
            f'</div>'
            + price_html
            + headlines_html
            + '</div>'
        )
        cols[idx % 2].markdown(card_html, unsafe_allow_html=True)


def render_global_cues_tab(global_cue_rows: list[dict], overall_global_cue: str) -> None:
    if overall_global_cue == "Bullish":
        overall_color = "#15803d"; overall_bg = "rgba(22,163,74,0.15)"; overall_border = "#16a34a"; overall_icon = "▲"
    elif overall_global_cue == "Bearish":
        overall_color = "#b91c1c"; overall_bg = "rgba(220,38,38,0.15)"; overall_border = "#dc2626"; overall_icon = "▼"
    else:
        overall_color = "#92400e"; overall_bg = "rgba(217,119,6,0.15)"; overall_border = "#d97706"; overall_icon = "◉"
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#0f172a,#1e3a8a);border-radius:14px;padding:14px 18px;'
        f'margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;">'
        f'<span style="color:#e2e8f0;font-weight:700;font-size:1rem;">Overall Global Cue</span>'
        f'<span style="background:{overall_bg};color:{overall_color};border:1.5px solid {overall_border};'
        f'border-radius:10px;padding:5px 16px;font-weight:800;font-size:0.92rem;letter-spacing:0.02em;">'
        f'{overall_icon} {overall_global_cue}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not global_cue_rows:
        st.info("No live global cue data available.")
        return
    cols = st.columns(3, gap="medium")
    for idx, row in enumerate(global_cue_rows):
        signal = str(row.get("signal", "Neutral"))
        if signal == "Bullish":
            signal_color = "#15803d"; signal_bg = "#dcfce7"; signal_border = "#16a34a"; signal_icon = "▲"
        elif signal == "Bearish":
            signal_color = "#b91c1c"; signal_bg = "#fee2e2"; signal_border = "#dc2626"; signal_icon = "▼"
        else:
            signal_color = "#92400e"; signal_bg = "#fef3c7"; signal_border = "#d97706"; signal_icon = "◉"
        chg = float(row.get("change_pct", 0.0))
        chg_color = "#15803d" if chg >= 0 else "#b91c1c"
        cols[idx % 3].markdown(
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;'
            f'margin-bottom:12px;box-shadow:0 2px 8px rgba(15,23,42,0.06);">'
            f'<div style="font-size:0.88rem;font-weight:700;color:#1e293b;margin-bottom:4px;">{html.escape(str(row.get("market","")))} </div>'
            f'<div style="font-size:1.05rem;font-weight:800;color:#0f172a;">{float(row.get("last",0.0)):,.2f}</div>'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;">'
            f'<span style="font-size:0.82rem;font-weight:700;color:{chg_color};">{"▲" if chg>=0 else "▼"} {abs(chg):.2f}%</span>'
            f'<span style="background:{signal_bg};color:{signal_color};border:1.5px solid {signal_border};'
            f'border-radius:8px;padding:3px 10px;font-size:0.75rem;font-weight:800;">{signal_icon} {signal}</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def render_index_result(title: str, payload: dict) -> None:
    render_summary(payload.get("summary", {}))

    records = payload.get("records", [])
    if not records:
        st.warning("No records found for this index.")
        return

    turning_records = [
        {**record, **build_turning_trade_levels(record)}
        for record in records
        if isinstance(record, dict) and record.get("trend") in {"turning_bullish", "turning_bearish"}
    ]
    if not turning_records:
        st.info("No turning bullish or turning bearish stocks met the standardized trade filter in the latest refresh.")
        return

    frame = pd.DataFrame(turning_records)
    if "ticker" in frame.columns:
        frame["stock_name"] = frame["ticker"].astype(str).str.replace(".NS", "", regex=False)
    if "trend" in frame.columns:
        frame["trend direction"] = frame["trend"].astype(str).str.replace("_", " ").str.title()
    visible_columns = [
        column for column in [
            "stock_name", "trend direction", "turning_bullish_probability",
            "turning_bearish_probability", "close", "percent_change", "rsi"
        ] if column in frame.columns
    ]
    display_frame = frame[visible_columns] if visible_columns else frame

    if "stock_name" in display_frame.columns:
        display_frame = display_frame.rename(
            columns={
                "stock_name": "Stock Name",
                "trend direction": "Trend Direction",
                "turning_bullish_probability": "Bullish Probability",
                "turning_bearish_probability": "Bearish Probability",
                "close": "Close Price",
                "percent_change": "% Change",
                "rsi": "RSI",
            }
        )

    uptrend_frame = (
        display_frame[display_frame["Trend Direction"] == "Turning Bullish"]
        if "Trend Direction" in display_frame.columns else pd.DataFrame()
    )
    downtrend_frame = (
        display_frame[display_frame["Trend Direction"] == "Turning Bearish"]
        if "Trend Direction" in display_frame.columns else pd.DataFrame()
    )
    if "Bullish Probability" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.sort_values(by=["Bullish Probability", "RSI"], ascending=[False, False], na_position="last")
    elif "RSI" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.sort_values(by=["RSI"], ascending=[False], na_position="last")
    if "Bearish Probability" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.sort_values(by=["Bearish Probability", "RSI"], ascending=[False, True], na_position="last")
    elif "RSI" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.sort_values(by=["RSI"], ascending=[True], na_position="last")

    def themed_table_html(dataframe: pd.DataFrame, theme: str):
        table_class = "up-table" if theme == "up" else "down-table"
        headers = "".join(f"<th>{column}</th>" for column in dataframe.columns)
        rows = []
        for _, row in dataframe.iterrows():
            formatted_values = []
            for column, value in row.items():
                if column in {"Bullish Probability", "Bearish Probability", "Close Price", "RSI", "% Change"} and isinstance(value, (int, float)):
                    formatted_values.append(f"{value:.2f}")
                else:
                    formatted_values.append(html.escape(str(value)))
            cells = "".join(f"<td>{value}</td>" for value in formatted_values)
            rows.append(f"<tr>{cells}</tr>")
        body = "".join(rows)
        return (
            '<div class="trend-table-wrapper">'
            f'<table class="trend-table {table_class}">'
            f'<thead><tr>{headers}</tr></thead>'
            f'<tbody>{body}</tbody>'
            '</table>'
            '</div>'
        )

    st.markdown('<div class="trend-card uptrend-card">', unsafe_allow_html=True)
    st.subheader("Turning Bullish Stocks")
    if uptrend_frame.empty:
        st.info("No turning bullish stocks met the standardized trade filter in the latest refresh.")
    else:
        st.markdown(themed_table_html(uptrend_frame, "up"), unsafe_allow_html=True)
        selected_bullish_stock = st.pills(
            f"Select turning bullish stock in {title}",
            options=uptrend_frame["Stock Name"].tolist(),
            selection_mode="single",
            key=f"selected_trade::{title}::turning_bullish",
        )
        bullish_record = None
        if selected_bullish_stock:
            bullish_record = next(
                (record for record in turning_records if str(record.get("ticker", "")).replace(".NS", "") == selected_bullish_stock),
                None,
            )
        if bullish_record is not None:
            bullish_trade = build_turning_trade_levels(bullish_record)
            st.caption(f"Selected: {selected_bullish_stock}")
            st.markdown(
                f"**Buy:** {bullish_trade['buy_price']:.2f} &nbsp;&nbsp; **Target:** {bullish_trade['target_price']:.2f} &nbsp;&nbsp; **Stop Loss:** {bullish_trade['stop_loss']:.2f}",
                unsafe_allow_html=True,
            )
    st.markdown('</div>', unsafe_allow_html=True)


    st.markdown('<div class="trend-card downtrend-card">', unsafe_allow_html=True)
    st.subheader("Turning Bearish Stocks")
    if downtrend_frame.empty:
        st.info("No turning bearish stocks met the standardized trade filter in the latest refresh.")
    else:
        st.markdown(themed_table_html(downtrend_frame, "down"), unsafe_allow_html=True)
        selected_bearish_stock = st.pills(
            f"Select turning bearish stock in {title}",
            options=downtrend_frame["Stock Name"].tolist(),
            selection_mode="single",
            key=f"selected_trade::{title}::turning_bearish",
        )
        bearish_record = None
        if selected_bearish_stock:
            bearish_record = next(
                (record for record in turning_records if str(record.get("ticker", "")).replace(".NS", "") == selected_bearish_stock),
                None,
            )
        if bearish_record is not None:
            bearish_trade = build_turning_trade_levels(bearish_record)
            st.caption(f"Selected: {selected_bearish_stock}")
            st.markdown(
                f"**Buy:** {bearish_trade['buy_price']:.2f} &nbsp;&nbsp; **Target:** {bearish_trade['target_price']:.2f} &nbsp;&nbsp; **Stop Loss:** {bearish_trade['stop_loss']:.2f}",
                unsafe_allow_html=True,
            )
    st.markdown('</div>', unsafe_allow_html=True)


def render_results(status_payload: dict) -> None:
    selected_index = status_payload.get("params", {}).get("index", DEFAULT_INDEX)
    selected_trade_mode = str(status_payload.get("params", {}).get("trade_mode", "swing"))
    if selected_index == "all_indices":
        st.markdown(
            f"""
            <div class="dashboard-hero">
                <h2>All Supported Indices</h2>
                <p>Latest {TRADE_MODE_LABELS.get(selected_trade_mode, 'Swing (5-10 Days)').lower()} turning-trend refresh grouped by index.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        for index_key in expand_index_selection(selected_index):
            payload = load_index_payload_from_status(status_payload, index_key)
            if payload is not None:
                render_index_result(INDEX_LABELS.get(index_key, index_key), payload)
        return

    payload = load_index_payload_from_status(status_payload, selected_index)
    if payload is None:
        st.info("Latest cached report is unavailable on this deployment. Click **Refresh Data** to generate a new report.")
        return

    summary_mode = str(payload.get("summary", {}).get("trade_mode", selected_trade_mode))
    st.markdown(
        f"""
        <div class="dashboard-hero">
            <h2>{INDEX_LABELS.get(selected_index, selected_index)}</h2>
            <p>Latest {TRADE_MODE_LABELS.get(summary_mode, 'Swing (5-10 Days)').lower()} turning bullish and turning bearish trade candidates.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_index_result(INDEX_LABELS.get(selected_index, selected_index), payload)


def render_prediction_results(prediction: dict) -> None:
    st.markdown(
        f"""
        <div class="dashboard-hero">
            <h2>AI Predictor - {INDEX_LABELS.get(prediction.get("index", DEFAULT_INDEX), prediction.get("index", DEFAULT_INDEX))}</h2>
            <p>Overall sentiment: {str(prediction.get("market_sentiment", "Unknown")).title()} | Source: {prediction.get("provider", "Unknown")}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if prediction.get("analysis"):
        st.info(str(prediction["analysis"]))

    def picks_to_frame(picks: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(picks)
        if frame.empty:
            return frame
        frame["ticker"] = frame["ticker"].astype(str).str.replace(".NS", "", regex=False)
        if "index" in frame.columns:
            frame["index"] = frame["index"].astype(str).map(lambda item: INDEX_LABELS.get(item, item))
        if "trend" in frame.columns:
            frame["trend"] = frame["trend"].astype(str).str.replace("_", " ").str.title()
        frame = frame.rename(
            columns={
                "ticker": "Stock Name",
                "trend": "Trend Direction",
                "rsi": "RSI",
                "close": "Close Price",
                "buy_price": "Buy Price",
                "target_price": "Target",
                "stop_loss": "Stop Loss",
                "signal_strength": "Signal Strength",
                "turning_score": "Turning Score",
                "volume_ratio": "Volume Ratio",
            }
        )
        visible_columns = [
            column for column in [
                "Stock Name", "Trend Direction", "RSI", "Close Price", "Buy Price",
                "Target", "Stop Loss", "Turning Score", "Signal Strength", "Volume Ratio"
            ] if column in frame.columns
        ]
        return frame[visible_columns]

    def predictor_table_html(dataframe: pd.DataFrame, theme: str) -> str:
        table_class = "up-table" if theme == "up" else "down-table"
        headers = "".join(f"<th>{column}</th>" for column in dataframe.columns)
        rows = []
        for _, row in dataframe.iterrows():
            formatted_values = []
            for column, value in row.items():
                if column in {"RSI", "Close Price", "Buy Price", "Target", "Stop Loss", "Volume Ratio"} and isinstance(value, (int, float)):
                    formatted_values.append(f"{value:.2f}")
                else:
                    formatted_values.append(value)
            cells = "".join(f"<td>{value}</td>" for value in formatted_values)
            rows.append(f"<tr>{cells}</tr>")
        body = "".join(rows)
        return (
            '<div class="predictor-table-wrapper">'
            f'<table class="predictor-table {table_class}">'
            f"<thead><tr>{headers}</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
        )

    def themed_predictor_block(title: str, subtitle: str, dataframe: pd.DataFrame, theme: str) -> None:
        st.markdown(
            f"""
            <div class="predictor-card {'up-card' if theme == 'up' else 'down-card'}">
                <div class="predictor-badge">{'Bullish Setup' if theme == 'up' else 'Bearish Setup'}</div>
                <h3>{title}</h3>
                <p>{subtitle}</p>
                {predictor_table_html(dataframe, theme) if not dataframe.empty else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if dataframe.empty:
            st.info(f"No {theme}trend picks available.")

    uptrend_frame = picks_to_frame(prediction.get("uptrend_picks", []))
    downtrend_frame = picks_to_frame(prediction.get("downtrend_picks", []))
    themed_predictor_block(
        "AI Uptrend Picks",
        "Top bullish continuation candidates with buy, target, and stop-loss levels.",
        uptrend_frame,
        "up",
    )
    themed_predictor_block(
        "AI Downtrend Picks",
        "Top bearish continuation candidates with buy, target, and stop-loss levels.",
        downtrend_frame,
        "down",
    )


def render_turning_prediction_results(prediction: dict) -> None:
    if isinstance(prediction.get("indices"), list):
        st.markdown(
            """
            <div class="dashboard-hero">
                <h2>AI Turning Predictor - All Supported Indices</h2>
                <p>Standardized turning bullish and turning bearish lists grouped by index.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        for index_prediction in prediction["indices"]:
            render_turning_prediction_results(index_prediction)
        return

    st.markdown(
        f"""
        <div class="dashboard-hero">
            <h2>AI Turning Predictor - {INDEX_LABELS.get(prediction.get("index", DEFAULT_INDEX), prediction.get("index", DEFAULT_INDEX))}</h2>
            <p>Turning bullish and turning bearish setups only | Source: {prediction.get("provider", "Unknown")}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if prediction.get("analysis"):
        st.info(str(prediction["analysis"]))

    def picks_to_frame(picks: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(picks)
        if frame.empty:
            return frame
        frame["ticker"] = frame["ticker"].astype(str).str.replace(".NS", "", regex=False)
        if "trend" in frame.columns:
            frame["trend"] = frame["trend"].astype(str).str.replace("_", " ").str.title()
        frame = frame.rename(
            columns={
                "ticker": "Stock Name",
                "trend": "Trend Direction",
                "rsi": "RSI",
                "close": "Close Price",
                "buy_price": "Buy Price",
                "target_price": "Target",
                "stop_loss": "Stop Loss",
                "turning_score": "Turning Score",
                "turning_probability": "Probability",
                "signal_strength": "Signal Strength",
                "volume_ratio": "Volume Ratio",
                "index": "Index",
            }
        )
        visible_columns = [
            column for column in [
                "Index", "Stock Name", "Trend Direction", "Probability", "RSI", "Close Price", "Buy Price",
                "Target", "Stop Loss", "Turning Score", "Signal Strength", "Volume Ratio"
            ] if column in frame.columns
        ]
        return frame[visible_columns]

    def predictor_table_html(dataframe: pd.DataFrame, theme: str) -> str:
        table_class = "up-table" if theme == "up" else "down-table"
        headers = "".join(f"<th>{column}</th>" for column in dataframe.columns)
        rows = []
        for _, row in dataframe.iterrows():
            formatted_values = []
            for column, value in row.items():
                if column in {"Probability", "RSI", "Close Price", "Buy Price", "Target", "Stop Loss", "Volume Ratio"} and isinstance(value, (int, float)):
                    formatted_values.append(f"{value:.2f}")
                else:
                    formatted_values.append(value)
            cells = "".join(f"<td>{value}</td>" for value in formatted_values)
            rows.append(f"<tr>{cells}</tr>")
        body = "".join(rows)
        return (
            '<div class="predictor-table-wrapper">'
            f'<table class="predictor-table {table_class}">'
            f"<thead><tr>{headers}</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
        )

    def themed_predictor_block(title: str, subtitle: str, dataframe: pd.DataFrame, theme: str) -> None:
        st.markdown(
            f"""
            <div class="predictor-card {'up-card' if theme == 'up' else 'down-card'}">
                <div class="predictor-badge">{'Bullish Reversal' if theme == 'up' else 'Bearish Reversal'}</div>
                <h3>{title}</h3>
                <p>{subtitle}</p>
                {predictor_table_html(dataframe, theme) if not dataframe.empty else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if dataframe.empty:
            st.info(f"No turning {'bullish' if theme == 'up' else 'bearish'} picks available.")

    themed_predictor_block(
        "AI Turning Bullish Picks",
        "Standardized bullish reversal candidates ranked by reversal probability.",
        picks_to_frame(prediction.get("turning_bullish_picks", [])),
        "up",
    )
    themed_predictor_block(
        "AI Turning Bearish Picks",
        "Standardized bearish reversal candidates ranked by reversal probability.",
        picks_to_frame(prediction.get("turning_bearish_picks", [])),
        "down",
    )


def load_existing_status() -> dict | None:
    status_path = LATEST_JOB_DIR / "status.json"
    if status_path.exists():
        return load_json(status_path)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# OPTION ANALYZER
# ──────────────────────────────────────────────────────────────────────────────

OPTION_INDEX_SYMBOLS = {
    "NIFTY": {"lot_size": 25, "step": 50},
    "BANKNIFTY": {"lot_size": 15, "step": 100},
    "FINNIFTY": {"lot_size": 25, "step": 50},
    "MIDCPNIFTY": {"lot_size": 75, "step": 25},
    "SENSEX": {"lot_size": 10, "step": 100},
}

OPTION_STOCK_SAMPLES = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "KOTAKBANK", "LT", "BAJFINANCE", "WIPRO", "HINDUNILVR", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "DRREDDY", "TATASTEEL", "JSWSTEEL", "ONGC",
    "NTPC", "POWERGRID", "ADANIENT", "ADANIPORTS", "TITAN", "ITC",
    "EICHERMOT", "DIVISLAB", "APOLLOHOSP", "HCLTECH", "TECHM",
    "BAJAJFINSV", "BRITANNIA", "GRASIM", "ULTRACEMCO", "NESTLEIND",
    "M&M", "HEROMOTOCO", "INDUSINDBK", "COALINDIA", "BPCL",
]

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_nse_option_chain(symbol: str, is_index: bool = True) -> dict | None:
    """Fetch live option chain from NSE India using browser-impersonating TLS."""
    import time as _time

    try:
        from curl_cffi import requests as cffi_req
        session_cls = lambda: cffi_req.Session(impersonate="chrome124")
    except ImportError:
        session_cls = requests.Session

    if is_index:
        api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        api_url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

    extra_headers = {
        "Referer": "https://www.nseindia.com/option-chain",
        "X-Requested-With": "XMLHttpRequest",
    }

    for attempt in range(2):
        try:
            s = session_cls()
            s.get("https://www.nseindia.com", timeout=10)
            _time.sleep(0.8)
            s.get("https://www.nseindia.com/option-chain", timeout=10)
            _time.sleep(0.8)
            resp = s.get(api_url, headers=extra_headers, timeout=15)
            if resp.status_code == 404:
                return {"error": "market_closed"}
            resp.raise_for_status()
            data = resp.json()
            if not data or not data.get("records", {}).get("data"):
                return {"error": "market_closed"}
            return data
        except Exception as exc:
            if attempt == 1:
                return {"error": str(exc)}
            _time.sleep(2)


def _calculate_max_pain(strike_data: dict) -> float:
    """Calculate max pain strike — where option writers lose the least."""
    strikes = sorted(strike_data.keys())
    min_loss = float("inf")
    max_pain = strikes[0]
    for target in strikes:
        total_loss = 0.0
        for strike in strikes:
            ce_oi = strike_data[strike].get("CE", {}).get("oi", 0)
            pe_oi = strike_data[strike].get("PE", {}).get("oi", 0)
            if target > strike:
                total_loss += ce_oi * (target - strike)
            elif target < strike:
                total_loss += pe_oi * (strike - target)
        if total_loss < min_loss:
            min_loss = total_loss
            max_pain = target
    return max_pain


def analyze_option_chain(data: dict, step: int = 50) -> dict | None:
    """Parse NSE option chain response and generate trade signals."""
    try:
        records = data.get("records", {})
        spot_price = float(records.get("underlyingValue", 0))
        expiry_dates = records.get("expiryDates", [])
        all_data = records.get("data", [])
        if not all_data or spot_price == 0:
            return None

        # Use nearest expiry
        expiry = expiry_dates[0] if expiry_dates else None
        exp_data = [r for r in all_data if r.get("expiryDate") == expiry]

        strikes_raw = sorted(set(r["strikePrice"] for r in exp_data))
        atm_strike = min(strikes_raw, key=lambda x: abs(x - spot_price))

        # Focus on strikes within ±10 steps of ATM for analysis
        near_strikes = [s for s in strikes_raw if abs(s - atm_strike) <= step * 10]

        strike_data: dict = {}
        total_ce_oi = 0
        total_pe_oi = 0
        total_ce_oi_chg = 0
        total_pe_oi_chg = 0

        for row in exp_data:
            s = row["strikePrice"]
            if s not in near_strikes:
                continue
            entry: dict = {"CE": {}, "PE": {}}
            for opt in ("CE", "PE"):
                if opt in row:
                    d = row[opt]
                    entry[opt] = {
                        "oi": d.get("openInterest", 0),
                        "chng_oi": d.get("changeinOpenInterest", 0),
                        "ltp": d.get("lastPrice", 0),
                        "iv": d.get("impliedVolatility", 0),
                        "volume": d.get("totalTradedVolume", 0),
                        "bid": d.get("bidprice", 0),
                        "ask": d.get("askPrice", 0),
                    }
                    if opt == "CE":
                        total_ce_oi += d.get("openInterest", 0)
                        total_ce_oi_chg += d.get("changeinOpenInterest", 0)
                    else:
                        total_pe_oi += d.get("openInterest", 0)
                        total_pe_oi_chg += d.get("changeinOpenInterest", 0)
            strike_data[s] = entry

        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0.0
        pcr_chg = round(total_pe_oi_chg / total_ce_oi_chg, 2) if total_ce_oi_chg > 0 else 0.0

        max_pain = _calculate_max_pain(strike_data)

        # Resistance = strike with highest CE OI above spot
        ce_above = {s: strike_data[s]["CE"].get("oi", 0) for s in near_strikes if s >= atm_strike}
        pe_below = {s: strike_data[s]["PE"].get("oi", 0) for s in near_strikes if s <= atm_strike}
        resistance = max(ce_above, key=ce_above.get) if ce_above else atm_strike
        support = max(pe_below, key=pe_below.get) if pe_below else atm_strike

        # OI buildup signals
        ce_oi_building = total_ce_oi_chg > 0  # CE writing → resistance building
        pe_oi_building = total_pe_oi_chg > 0  # PE writing → support building

        # Momentum from OI change: PE adding faster → bullish, CE adding faster → bearish
        oi_momentum = "bullish" if total_pe_oi_chg > total_ce_oi_chg else "bearish"

        # --- Signal generation ---
        signal_type = "NEUTRAL"
        signal_action = ""
        signal_strike = atm_strike
        signal_option = ""
        signal_reason: list[str] = []
        confidence = "Medium"

        spot_vs_max_pain = spot_price - max_pain
        spot_vs_resistance = resistance - spot_price
        spot_vs_support = spot_price - support

        # ATM IV to classify environment
        atm_ce_iv = strike_data.get(atm_strike, {}).get("CE", {}).get("iv", 0)
        atm_pe_iv = strike_data.get(atm_strike, {}).get("PE", {}).get("iv", 0)
        avg_iv = (atm_ce_iv + atm_pe_iv) / 2 if (atm_ce_iv + atm_pe_iv) > 0 else 0

        # High IV → prefer selling; Low IV → prefer buying
        prefer_buy = avg_iv < 20
        prefer_sell = avg_iv > 30

        if pcr >= 1.3 and oi_momentum == "bullish":
            # Bullish: CE OI building above = resistance, PE writing = support → buy call
            signal_type = "BULLISH"
            signal_reason.append(f"PCR {pcr} ≥ 1.3 (strong put writing = bullish support)")
            signal_reason.append(f"OI momentum: PE adding faster than CE")
            if prefer_sell:
                signal_action = "SELL PUT"
                signal_option = "PE"
                signal_strike = support
                signal_reason.append(f"High IV ({avg_iv:.0f}%) → premium selling preferred")
                confidence = "High" if pcr >= 1.5 else "Medium"
            else:
                signal_action = "BUY CALL"
                signal_option = "CE"
                signal_strike = atm_strike if spot_vs_resistance > step else atm_strike + step
                signal_reason.append(f"Low/Mid IV → directional buying preferred")
                confidence = "High" if pcr >= 1.5 else "Medium"

        elif pcr <= 0.7 and oi_momentum == "bearish":
            # Bearish
            signal_type = "BEARISH"
            signal_reason.append(f"PCR {pcr} ≤ 0.7 (heavy call writing = bearish resistance)")
            signal_reason.append(f"OI momentum: CE adding faster than PE")
            if prefer_sell:
                signal_action = "SELL CALL"
                signal_option = "CE"
                signal_strike = resistance
                signal_reason.append(f"High IV ({avg_iv:.0f}%) → premium selling preferred")
                confidence = "High" if pcr <= 0.5 else "Medium"
            else:
                signal_action = "BUY PUT"
                signal_option = "PE"
                signal_strike = atm_strike if spot_vs_support > step else atm_strike - step
                signal_reason.append(f"Low/Mid IV → directional buying preferred")
                confidence = "High" if pcr <= 0.5 else "Medium"

        elif 0.7 < pcr < 1.3:
            # Sideways / range-bound → sell straddle or wait
            signal_type = "NEUTRAL"
            signal_reason.append(f"PCR {pcr} in neutral zone (0.7–1.3) → range-bound")
            signal_reason.append(f"Max pain at {max_pain:.0f} → spot may gravitate here")
            if prefer_sell:
                signal_action = "SELL STRADDLE"
                signal_option = "CE+PE"
                signal_strike = atm_strike
                signal_reason.append(f"High IV ({avg_iv:.0f}%) → sell ATM straddle for time decay")
                confidence = "Medium"
            else:
                signal_action = "WAIT"
                signal_option = ""
                signal_reason.append("Low IV in neutral market → no clear edge, wait for breakout")
                confidence = "Low"

        # Compute trade levels for the signal strike
        ce_ltp = strike_data.get(signal_strike, {}).get("CE", {}).get("ltp", 0)
        pe_ltp = strike_data.get(signal_strike, {}).get("PE", {}).get("ltp", 0)

        if signal_option == "CE":
            entry_price = ce_ltp
        elif signal_option == "PE":
            entry_price = pe_ltp
        elif signal_option == "CE+PE":
            entry_price = ce_ltp + pe_ltp
        else:
            entry_price = 0

        if "BUY" in signal_action and entry_price > 0:
            target = round(entry_price * 1.50, 1)
            stop_loss = round(entry_price * 0.50, 1)
            rr = "2:1"
        elif "SELL" in signal_action and entry_price > 0 and "STRADDLE" not in signal_action:
            target = round(entry_price * 0.30, 1)   # keep 70% of premium
            stop_loss = round(entry_price * 2.0, 1)   # exit at 2x premium loss
            rr = "1:1"
        elif "STRADDLE" in signal_action and entry_price > 0:
            target = round(entry_price * 0.40, 1)   # keep 60% of combined premium
            stop_loss = round(entry_price * 1.50, 1)
            rr = "1.5:1"
        else:
            target = 0
            stop_loss = 0
            rr = "–"

        return {
            "spot": spot_price,
            "expiry": expiry,
            "atm_strike": atm_strike,
            "pcr": pcr,
            "pcr_chg": pcr_chg,
            "max_pain": max_pain,
            "support": support,
            "resistance": resistance,
            "avg_iv": round(avg_iv, 1),
            "signal_type": signal_type,
            "signal_action": signal_action,
            "signal_option": signal_option,
            "signal_strike": signal_strike,
            "entry_price": entry_price,
            "target": target,
            "stop_loss": stop_loss,
            "rr": rr,
            "confidence": confidence,
            "signal_reason": signal_reason,
            "strike_data": strike_data,
            "near_strikes": near_strikes,
            "ce_oi_building": ce_oi_building,
            "pe_oi_building": pe_oi_building,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _render_option_signal_card(result: dict) -> None:
    """Render the signal card and trade levels for option analysis."""
    sig = result["signal_action"]
    sig_type = result["signal_type"]

    if sig_type == "BULLISH":
        border_color = "#22c55e"
        bg = "#f0fdf4"
        icon = "🟢"
        sig_color = "#166534"
    elif sig_type == "BEARISH":
        border_color = "#ef4444"
        bg = "#fef2f2"
        icon = "🔴"
        sig_color = "#991b1b"
    else:
        border_color = "#f59e0b"
        bg = "#fffbeb"
        icon = "🟡"
        sig_color = "#92400e"

    confidence_color = {"High": "#166534", "Medium": "#92400e", "Low": "#6b7280"}.get(result["confidence"], "#6b7280")

    # Signal header
    st.markdown(
        f"""
        <div style="border:2px solid {border_color};border-radius:12px;background:{bg};padding:18px 22px;margin-bottom:16px;">
            <div style="font-size:1.3rem;font-weight:800;color:{sig_color};margin-bottom:6px;">
                {icon} {sig} &nbsp;&nbsp;
                <span style="font-size:0.9rem;background:{border_color};color:white;padding:3px 10px;border-radius:20px;font-weight:600;">
                    {result['confidence']} Confidence
                </span>
            </div>
            <div style="font-size:1rem;color:#374151;margin-bottom:4px;">
                <strong>Strike:</strong> {result['signal_strike']:,.0f} &nbsp;|&nbsp;
                <strong>Expiry:</strong> {result['expiry']} &nbsp;|&nbsp;
                <strong>Spot:</strong> {result['spot']:,.2f}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if result["entry_price"] > 0:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(
                f'<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:0.75rem;color:#1e40af;font-weight:700;text-transform:uppercase;">Entry Premium</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:#1e3a8a;">₹{result["entry_price"]:.1f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:0.75rem;color:#166534;font-weight:700;text-transform:uppercase;">Target</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:#14532d;">₹{result["target"]:.1f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:0.75rem;color:#991b1b;font-weight:700;text-transform:uppercase;">Stop Loss</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:#7f1d1d;">₹{result["stop_loss"]:.1f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c4:
            st.markdown(
                f'<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:0.75rem;color:#6b21a8;font-weight:700;text-transform:uppercase;">Risk:Reward</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:#581c87;">{result["rr"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)

    # Reasons
    if result.get("signal_reason"):
        reasons_html = "".join(f"<li style='margin-bottom:4px;'>{r}</li>" for r in result["signal_reason"])
        st.markdown(
            f'<div style="background:#f8fafc;border-left:4px solid #64748b;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:12px;">'
            f'<div style="font-weight:700;color:#334155;margin-bottom:6px;">📋 Signal Reasoning</div>'
            f'<ul style="margin:0;padding-left:18px;color:#475569;font-size:0.9rem;">{reasons_html}</ul>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_option_metrics(result: dict) -> None:
    """Render PCR, Max Pain, IV, Support/Resistance metrics."""
    pcr = result["pcr"]
    pcr_color = "#166534" if pcr >= 1.3 else "#991b1b" if pcr <= 0.7 else "#92400e"
    pcr_label = "Bullish" if pcr >= 1.3 else "Bearish" if pcr <= 0.7 else "Neutral"

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.markdown(
            f'<div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#64748b;font-weight:700;text-transform:uppercase;">PCR</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{pcr_color};">{pcr}</div>'
            f'<div style="font-size:0.75rem;color:{pcr_color};font-weight:600;">{pcr_label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f'<div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#64748b;font-weight:700;text-transform:uppercase;">Max Pain</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:#1e40af;">{result["max_pain"]:,.0f}</div>'
            f'<div style="font-size:0.75rem;color:#64748b;">Expiry magnet</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f'<div style="background:white;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#64748b;font-weight:700;text-transform:uppercase;">Avg IV</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:#7c3aed;">{result["avg_iv"]}%</div>'
            f'<div style="font-size:0.75rem;color:#64748b;">{"High → Sell" if result["avg_iv"]>30 else "Low → Buy" if result["avg_iv"]<20 else "Neutral"}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            f'<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 12px;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#166534;font-weight:700;text-transform:uppercase;">Support</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:#14532d;">{result["support"]:,.0f}</div>'
            f'<div style="font-size:0.75rem;color:#166534;">Highest PE OI</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with m5:
        st.markdown(
            f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:10px 12px;text-align:center;">'
            f'<div style="font-size:0.72rem;color:#991b1b;font-weight:700;text-transform:uppercase;">Resistance</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:#7f1d1d;">{result["resistance"]:,.0f}</div>'
            f'<div style="font-size:0.75rem;color:#991b1b;">Highest CE OI</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown("<br>", unsafe_allow_html=True)


def _render_option_chain_table(result: dict, num_strikes: int = 10) -> None:
    """Render a compact option chain table around ATM."""
    strike_data = result["strike_data"]
    atm = result["atm_strike"]
    strikes = sorted(strike_data.keys())
    # Pick nearest N strikes around ATM
    idx = strikes.index(atm) if atm in strikes else len(strikes) // 2
    lo = max(0, idx - num_strikes // 2)
    hi = min(len(strikes), idx + num_strikes // 2 + 1)
    display_strikes = strikes[lo:hi]

    rows = []
    for s in reversed(display_strikes):
        ce = strike_data[s].get("CE", {})
        pe = strike_data[s].get("PE", {})
        label = "◀ ATM" if s == atm else ("ITM" if s < atm else "OTM")
        rows.append({
            "CE OI (K)": f"{ce.get('oi',0)/1000:.1f}",
            "CE Chg OI": f"{ce.get('chng_oi',0)/1000:+.1f}",
            "CE IV%": f"{ce.get('iv',0):.1f}",
            "CE LTP": f"₹{ce.get('ltp',0):.1f}",
            "Strike": f"{'→ ' if s==atm else ''}{s:,.0f}",
            "PE LTP": f"₹{pe.get('ltp',0):.1f}",
            "PE IV%": f"{pe.get('iv',0):.1f}",
            "PE Chg OI": f"{pe.get('chng_oi',0)/1000:+.1f}",
            "PE OI (K)": f"{pe.get('oi',0)/1000:.1f}",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_option_analyzer_tab() -> None:
    """Render the Option Analyzer tab with Index and Stock sub-sections."""
    from datetime import timezone as _tz
    now_ist = datetime.now(timezone.utc).astimezone(_tz(timedelta(hours=5, minutes=30)))
    is_market_hours = (
        now_ist.weekday() < 5 and
        (9 * 60 + 15) <= (now_ist.hour * 60 + now_ist.minute) <= (15 * 60 + 30)
    )
    market_status_html = (
        '<span style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;padding:3px 10px;border-radius:20px;font-size:0.82rem;font-weight:700;">🟢 Market Open — Live Data Available</span>'
        if is_market_hours else
        '<span style="background:#fef3c7;color:#92400e;border:1px solid #fcd34d;padding:3px 10px;border-radius:20px;font-size:0.82rem;font-weight:700;">🟡 Market Closed — Data available 9:15 AM–3:30 PM IST</span>'
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:16px;margin:8px 0 16px 0;">'
        f'<span style="font-size:1.15rem;font-weight:700;color:#1e40af;">⚡ Live Option Chain Analysis — NSE India</span>'
        f'&nbsp;&nbsp;{market_status_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

    sub_index, sub_stock = st.tabs(["📈 Index Options", "📊 Stock Options"])

    # ── Index Options ──────────────────────────────────────────────────
    with sub_index:
        st.markdown("##### Select Index")
        col_sym, col_btn = st.columns([3, 1])
        with col_sym:
            idx_symbol = st.selectbox(
                "Index Symbol",
                options=list(OPTION_INDEX_SYMBOLS.keys()),
                index=0,
                label_visibility="collapsed",
            )
        with col_btn:
            idx_fetch = st.button("🔍 Analyse", key="idx_fetch", type="primary", use_container_width=True)

        if idx_fetch or st.session_state.get("idx_result_cache_sym") == idx_symbol:
            if idx_fetch:
                fetch_nse_option_chain.clear()  # clear stale cache on manual fetch
            with st.spinner(f"Fetching {idx_symbol} option chain from NSE… (may take 5-10s)"):
                raw = fetch_nse_option_chain(idx_symbol, is_index=True)

            if raw and "error" in raw:
                if raw["error"] == "market_closed":
                    st.warning(
                        "⏰ **NSE Option Chain data is only available during market hours (Mon–Fri, 9:15 AM – 3:30 PM IST).**\n\n"
                        "Please try again when the market is open."
                    )
                else:
                    st.error(f"NSE fetch failed: {raw['error']}")
            elif raw:
                step = OPTION_INDEX_SYMBOLS[idx_symbol]["step"]
                result = analyze_option_chain(raw, step=step)
                if result and "error" not in result:
                    st.session_state["idx_result_cache_sym"] = idx_symbol
                    st.session_state["idx_result_cache"] = result

        result = st.session_state.get("idx_result_cache") if st.session_state.get("idx_result_cache_sym") == idx_symbol else None

        if result and "error" not in result:
            st.markdown(
                f'<div style="color:#475569;font-size:0.85rem;margin-bottom:12px;">'
                f'Expiry: <strong>{result["expiry"]}</strong> &nbsp;|&nbsp; Spot: <strong>{result["spot"]:,.2f}</strong> &nbsp;|&nbsp; ATM Strike: <strong>{result["atm_strike"]:,.0f}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
            _render_option_metrics(result)
            _render_option_signal_card(result)
            with st.expander("📋 View Option Chain (±5 strikes around ATM)", expanded=False):
                _render_option_chain_table(result, num_strikes=10)
        else:
            st.info("Select an index above and click **🔍 Analyse** to fetch live option chain data from NSE.")

    # ── Stock Options ──────────────────────────────────────────────────
    with sub_stock:
        st.markdown("##### Select Stock")
        col_stk, col_sbtn = st.columns([3, 1])
        with col_stk:
            stk_symbol = st.selectbox(
                "Stock Symbol",
                options=OPTION_STOCK_SAMPLES,
                index=0,
                label_visibility="collapsed",
            )
        with col_sbtn:
            stk_fetch = st.button("🔍 Analyse", key="stk_fetch", type="primary", use_container_width=True)

        if stk_fetch or st.session_state.get("stk_result_cache_sym") == stk_symbol:
            if stk_fetch:
                fetch_nse_option_chain.clear()  # clear stale cache on manual fetch
            with st.spinner(f"Fetching {stk_symbol} option chain from NSE… (may take 5-10s)"):
                raw_stk = fetch_nse_option_chain(stk_symbol, is_index=False)

            if raw_stk and "error" in raw_stk:
                if raw_stk["error"] == "market_closed":
                    st.warning(
                        "⏰ **NSE Option Chain data is only available during market hours (Mon–Fri, 9:15 AM – 3:30 PM IST).**\n\n"
                        "Please try again when the market is open."
                    )
                else:
                    st.error(f"NSE fetch failed: {raw_stk['error']}")
            elif raw_stk:
                result_stk = analyze_option_chain(raw_stk, step=5)
                if result_stk and "error" not in result_stk:
                    st.session_state["stk_result_cache_sym"] = stk_symbol
                    st.session_state["stk_result_cache"] = result_stk

        result_stk = st.session_state.get("stk_result_cache") if st.session_state.get("stk_result_cache_sym") == stk_symbol else None

        if result_stk and "error" not in result_stk:
            st.markdown(
                f'<div style="color:#475569;font-size:0.85rem;margin-bottom:12px;">'
                f'Expiry: <strong>{result_stk["expiry"]}</strong> &nbsp;|&nbsp; Spot: <strong>{result_stk["spot"]:,.2f}</strong> &nbsp;|&nbsp; ATM Strike: <strong>{result_stk["atm_strike"]:,.0f}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
            _render_option_metrics(result_stk)
            _render_option_signal_card(result_stk)
            with st.expander("📋 View Option Chain (±5 strikes around ATM)", expanded=False):
                _render_option_chain_table(result_stk, num_strikes=10)
        else:
            st.info("Select a stock above and click **🔍 Analyse** to fetch live option chain data from NSE.")




def main() -> None:
    st.set_page_config(page_title="Stock Analyzer AI", layout="wide", initial_sidebar_state="collapsed")
    apply_page_style()

    if "latest_status" not in st.session_state:
        st.session_state["latest_status"] = load_existing_status()

    # ── Hero header ──────────────────────────────────────────────────
    st.markdown(
        """
        <div class="hero-banner">
            <div class="hero-title">📈 Stock <span class="accent">Analyzer</span> AI</div>
            <div class="hero-subtitle">Real-time Nifty trend analysis · Turning-point detection · AI-powered trade picks</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── News & Global Cues: always independent, never blocked by refresh ──
    latest_status = st.session_state.get("latest_status")
    market_payload = None
    if latest_status and latest_status.get("status") == "completed" and status_covers_index(latest_status, latest_status.get("params", {}).get("index", DEFAULT_INDEX)):
        market_payload = load_index_payload_from_status(latest_status, latest_status.get("params", {}).get("index", DEFAULT_INDEX))

    analysis_stocks: list[str] = []
    trend_map: dict[str, str] = {}
    if isinstance(market_payload, dict):
        records = market_payload.get("records", [])
        valid_records = [r for r in records if isinstance(r, dict) and r.get("ticker")]

        def _rank(record: dict) -> float:
            trend = record.get("trend", "")
            base = 100.0 if "turning" in str(trend) else 50.0 if trend in ("up", "down") else 0.0
            prob = max(
                normalize_numeric(record.get("turning_bullish_probability"), 0.0) or 0.0,
                normalize_numeric(record.get("turning_bearish_probability"), 0.0) or 0.0,
            )
            return base + prob * 100

        ranked = sorted(valid_records, key=_rank, reverse=True)
        analysis_stocks = [str(r.get("ticker", "")).replace(".NS", "") for r in ranked]
        trend_map = {str(r.get("ticker", "")).replace(".NS", ""): r.get("trend", "") for r in ranked}

    seen: set[str] = set()
    merged_stocks: list[str] = []
    for s in analysis_stocks + list(BASE_NEWS_STOCKS):
        if s and s not in seen:
            seen.add(s)
            merged_stocks.append(s)

    news_rows = fetch_stock_news_rows(tuple(merged_stocks))
    price_data = fetch_stock_price_data(tuple(merged_stocks))
    for row in news_rows:
        stock = row.get("stock", "")
        row["price"] = price_data.get(stock, {})
        row["trend"] = trend_map.get(stock, "")
    global_cue_rows, overall_global_cue = fetch_global_cues_rows()

    # ── All tabs below hero ───────────────────────────────────────────
    tab_news, tab_global, tab_trend, tab_option = st.tabs([
        "📰 Today's Stock In News",
        "🌍 Global Cues",
        "📊 Trend Analysis",
        "⚡ Option Analyzer",
    ])

    with tab_news:
        render_news_tab(news_rows)

    with tab_global:
        render_global_cues_tab(global_cue_rows, overall_global_cue)

    with tab_trend:
        # ── Control strip ─────────────────────────────────────────────
        st.markdown('<div class="control-strip">', unsafe_allow_html=True)
        ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([3, 3, 3, 1], gap="small")
        with ctrl_col1:
            st.markdown('<div class="ctrl-label">📂 Index</div>', unsafe_allow_html=True)
            selected_index = st.selectbox(
                "Index",
                options=INDEX_OPTIONS,
                format_func=lambda item: INDEX_LABELS[item],
                index=INDEX_OPTIONS.index(DEFAULT_INDEX),
                label_visibility="collapsed",
            )
        with ctrl_col2:
            st.markdown('<div class="ctrl-label">⚙️ Trade Mode</div>', unsafe_allow_html=True)
            trade_mode = st.selectbox(
                "Trade Mode",
                options=list(TRADE_MODE_OPTIONS),
                format_func=lambda item: TRADE_MODE_LABELS[item],
                index=0,
                label_visibility="collapsed",
            )
        trade_mode_settings = get_trade_mode_settings(trade_mode)
        with ctrl_col3:
            st.markdown('<div class="ctrl-label">📅 Settings</div>', unsafe_allow_html=True)
            lb = trade_mode_settings["lookback_days"]
            horizon = trade_mode_settings["horizon"]
            st.markdown(
                f'<div class="mode-pill">'
                f'Lookback: <strong>{lb} Days</strong><br/>'
                f'Horizon: <strong>{horizon}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with ctrl_col4:
            st.markdown('<div class="ctrl-label">&nbsp;</div>', unsafe_allow_html=True)
            refresh_clicked = st.button("🔄 Refresh", type="primary", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # ── Refresh with progress bar ──────────────────────────────────
        if refresh_clicked:
            progress_bar = st.progress(0, text="⏳ Initialising refresh…")
            status_text = st.empty()

            def _update(pct: int, msg: str) -> None:
                progress_bar.progress(pct, text=msg)
                status_text.caption(msg)

            try:
                _update(10, "📡 Connecting to data sources…")
                import time as _time; _time.sleep(0.3)
                _update(25, "📥 Fetching price history…")
                result = refresh_trend_data(selected_index, trade_mode)
                _update(80, "🔍 Evaluating trend signals…")
                _time.sleep(0.3)
                st.session_state["latest_status"] = result
                _update(100, "✅ Refresh complete!")
                _time.sleep(0.5)
            except Exception as exc:
                progress_bar.empty()
                status_text.empty()
                st.error(f"Refresh failed: {exc}")
            else:
                progress_bar.empty()
                status_text.empty()
                st.success("✅ Data refreshed successfully!")

        # ── Trend results ──────────────────────────────────────────────
        latest_status = st.session_state.get("latest_status")
        if latest_status and latest_status.get("status") == "failed":
            st.error("Trend refresh failed.")
            if latest_status.get("failed_step"):
                st.caption(f"Failed step: `{latest_status['failed_step']}`")
            if latest_status.get("error_excerpt"):
                st.code(latest_status["error_excerpt"])
        elif not latest_status:
            st.info("Select your index and trade mode above, then click **🔄 Refresh** to run the trend analyzer.")
        else:
            render_results(latest_status)

    with tab_option:
        render_option_analyzer_tab()


if __name__ == "__main__":
    main()
