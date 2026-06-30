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
NEWS_POSITIVE_KEYWORDS = {"beat", "wins", "growth", "surge", "approval", "order", "profit", "upgrade", "bullish", "rally", "strong"}
NEWS_NEGATIVE_KEYWORDS = {"fall", "drops", "downgrade", "loss", "probe", "weak", "bearish", "cuts", "decline", "miss", "slump"}
BULLISH_TRENDS = {"up", "turning_bullish"}
BEARISH_TRENDS = {"down", "turning_bearish"}
TREND_PRIORITY = {
    "Turning Bullish": 0,
    "Up": 1,
    "Turning Bearish": 0,
    "Down": 1,
}


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
    probability = normalize_numeric(record.get(probability_key), 65.0) or 65.0
    fallback_risk = close * (0.006 if mode_settings["intraday"] else 0.0125)
    risk_unit = atr14 * 0.9 if atr14 is not None and atr14 > 0 else fallback_risk
    risk_unit = max(risk_unit, close * (0.004 if mode_settings["intraday"] else 0.006))
    reward_multiple = clamp(1.3 + ((probability - 65.0) / 40.0), 1.2, 2.0) if mode_settings["intraday"] else clamp(1.6 + ((probability - 65.0) / 35.0), 1.5, 2.6)

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
    rows = []
    for stock_name in stocks[:8]:
        query = f'"{stock_name}" NSE stock results OR order OR guidance OR upgrade OR downgrade'
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            item = root.find("./channel/item")
            if item is None:
                continue
            headline = str(item.findtext("title", "")).strip()
            link = str(item.findtext("link", "")).strip()
            published = str(item.findtext("pubDate", "")).strip()
            if not headline:
                continue
            rows.append(
                {
                    "stock": stock_name,
                    "headline": headline,
                    "signal": classify_headline_signal(headline),
                    "published": published,
                    "link": link,
                }
            )
        except Exception:
            continue
    return rows


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
    if not isinstance(payload, dict):
        return [], [], "Neutral"
    records = payload.get("records", [])
    turning_records = [
        record for record in records
        if isinstance(record, dict) and record.get("trend") in {"turning_bullish", "turning_bearish"}
    ]
    ranked_turning_records = sorted(
        turning_records,
        key=lambda record: max(
            normalize_numeric(record.get("turning_bullish_probability"), 0.0) or 0.0,
            normalize_numeric(record.get("turning_bearish_probability"), 0.0) or 0.0,
        ),
        reverse=True,
    )
    news_rows = fetch_stock_news_rows(tuple(str(record.get("ticker", "")).replace(".NS", "") for record in ranked_turning_records))
    global_cue_rows, overall_global_cue = fetch_global_cues_rows()
    return news_rows, global_cue_rows, overall_global_cue


def apply_page_style() -> None:
    st.markdown(
        """
        <style>
        #MainMenu,
        footer,
        [data-testid="stFooter"],
        [data-testid="stFooter"] *,
        [data-testid="stStatusWidget"],
        [data-testid="stDecoration"],
        header[data-testid="stHeader"] [data-testid="stHeaderActionElements"],
        header[data-testid="stHeader"] [data-testid="stHeaderActionElements"] *,
        [data-testid="stHeaderActionElements"],
        .stHeaderActionElements,
        [data-testid="stAppViewContainer__fork-button"],
        [data-testid="stGithubButton"],
        [data-testid="stRepoButton"],
        [data-testid="stToolbarActions"] a,
        header[data-testid="stHeader"] [data-testid="stToolbarActions"],
        header[data-testid="stHeader"] a[href*="github.com"],
        header[data-testid="stHeader"] a[href*="streamlit.io"],
        header[data-testid="stHeader"] *[href*="github.com"],
        header[data-testid="stHeader"] *[href*="streamlit.io"],
        header[data-testid="stHeader"] a,
        header[data-testid="stHeader"] [role="link"],
        .stAppDeployButton,
        a[href*="streamlit.io"],
        a[href*="share.streamlit.io"],
        a[href*="github.com"] {
            display: none !important;
            visibility: hidden !important;
        }
        @media (max-width: 768px) {
            .block-container {
                padding-top: 1rem;
                padding-left: 0.8rem;
                padding-right: 0.8rem;
            }
            [data-testid="stSidebar"] {
                min-width: 85vw !important;
            }
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(59, 130, 246, 0.16), transparent 28%),
                linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #020617 0%, #0f172a 45%, #172554 100%);
            border-right: 1px solid rgba(148, 163, 184, 0.18);
        }
        section[data-testid="stSidebar"] > div:first-child {
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding-bottom: 4rem;
            position: relative;
        }
        [data-testid="stSidebar"] * {
            color: #e2e8f0;
        }
        button[aria-label="Close sidebar"],
        [data-testid="stSidebarCollapseButton"] {
            position: fixed !important;
            left: 0.75rem !important;
            bottom: 0.75rem !important;
            top: auto !important;
            right: auto !important;
            z-index: 999999 !important;
            background: rgba(15, 23, 42, 0.92) !important;
            border: 1px solid rgba(148, 163, 184, 0.42) !important;
            border-radius: 999px !important;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.35) !important;
        }
        button[aria-label="Open sidebar"],
        [data-testid="collapsedControl"] {
            position: fixed !important;
            left: 0.75rem !important;
            bottom: 0.75rem !important;
            top: auto !important;
            right: auto !important;
            z-index: 999999 !important;
            background: rgba(15, 23, 42, 0.92) !important;
            border: 1px solid rgba(148, 163, 184, 0.42) !important;
            border-radius: 999px !important;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.35) !important;
        }
        button[aria-label="Close sidebar"] svg,
        button[aria-label="Open sidebar"] svg,
        [data-testid="stSidebarCollapseButton"] svg,
        [data-testid="collapsedControl"] svg {
            fill: #ffffff !important;
            color: #ffffff !important;
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }
        .dashboard-hero {
            background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 100%);
            color: white;
            padding: 1.2rem 1.4rem;
            border-radius: 18px;
            box-shadow: 0 18px 40px rgba(30, 64, 175, 0.25);
            margin-bottom: 1rem;
            border: 1px solid rgba(191, 219, 254, 0.18);
        }
        .dashboard-hero h2 {
            color: #ffffff;
            margin: 0;
            font-size: 1.6rem;
            font-weight: 700;
        }
        .dashboard-hero p {
            margin: 0.45rem 0 0 0;
            color: #dbeafe;
            font-size: 0.95rem;
        }
        .trend-card {
            background: #ffffff;
            border: 1px solid #dbeafe;
            border-radius: 18px;
            padding: 1rem 1rem 0.5rem 1rem;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
            margin-bottom: 1rem;
            text-align: center;
        }
        .trend-card.uptrend-card {
            border-top: 5px solid #16a34a;
            background: linear-gradient(180deg, #f0fdf4 0%, #ffffff 30%);
        }
        .trend-card.downtrend-card {
            border-top: 5px solid #dc2626;
            background: linear-gradient(180deg, #fef2f2 0%, #ffffff 30%);
        }
        .trend-card h3 {
            margin-top: 0.1rem;
            margin-bottom: 0.8rem;
            text-align: center;
        }
        .trend-card table {
            width: 100% !important;
            table-layout: fixed;
        }
        .trend-card .trend-table-wrapper {
            width: 100% !important;
            display: block;
        }
        .trend-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            table-layout: fixed;
            border-radius: 14px;
            overflow: hidden;
        }
        .trend-table th, .trend-table td {
            text-align: center;
            vertical-align: middle;
            padding: 10px 12px;
        }
        .trend-table th {
            text-transform: uppercase;
            font-weight: 700;
            color: #0f172a;
        }
        .trend-table.up-table {
            border: 1px solid #16a34a;
        }
        .trend-table.up-table th {
            background: #bbf7d0;
            border-bottom: 2px solid #16a34a;
        }
        .trend-table.up-table td {
            background: #f0fdf4;
            border-bottom: 1px solid rgba(22, 163, 74, 0.12);
        }
        .trend-table.down-table {
            border: 1px solid #dc2626;
        }
        .trend-table.down-table th {
            background: #fecaca;
            border-bottom: 2px solid #dc2626;
        }
        .trend-table.down-table td {
            background: #fef2f2;
            border-bottom: 1px solid rgba(220, 38, 38, 0.12);
        }
        .trend-card div[data-testid="stInfo"] {
            width: 100% !important;
        }
        .predictor-card {
            background: #ffffff;
            border-radius: 18px;
            padding: 1rem 1rem 1rem 1rem;
            box-shadow: 0 18px 38px rgba(15, 23, 42, 0.1);
            margin-bottom: 1rem;
            border: 1px solid rgba(191, 219, 254, 0.9);
            overflow: hidden;
        }
        .predictor-card.up-card {
            border-top: 5px solid #16a34a;
            background:
                radial-gradient(circle at top right, rgba(34, 197, 94, 0.16), transparent 30%),
                linear-gradient(180deg, #ecfdf5 0%, #ffffff 38%);
        }
        .predictor-card.down-card {
            border-top: 5px solid #dc2626;
            background:
                radial-gradient(circle at top right, rgba(239, 68, 68, 0.16), transparent 30%),
                linear-gradient(180deg, #fef2f2 0%, #ffffff 38%);
        }
        .predictor-card h3 {
            margin: 0 0 0.35rem 0;
            font-size: 1.25rem;
            font-weight: 800;
        }
        .predictor-card p {
            margin: 0 0 0.8rem 0;
            color: #475569;
        }
        .predictor-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.28rem 0.65rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-bottom: 0.75rem;
        }
        .predictor-card.up-card .predictor-badge {
            background: rgba(22, 163, 74, 0.12);
            color: #15803d;
        }
        .predictor-card.down-card .predictor-badge {
            background: rgba(220, 38, 38, 0.12);
            color: #b91c1c;
        }
        .predictor-table-wrapper {
            width: 100%;
            overflow-x: auto;
            border-radius: 16px;
        }
        .predictor-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            overflow: hidden;
            border-radius: 16px;
        }
        .predictor-table th,
        .predictor-table td {
            padding: 0.85rem 0.9rem;
            text-align: center;
            vertical-align: middle;
        }
        .predictor-table th {
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .predictor-table tr:last-child td {
            border-bottom: none;
        }
        .predictor-table.up-table {
            border: 1px solid rgba(22, 163, 74, 0.28);
            box-shadow: 0 10px 22px rgba(34, 197, 94, 0.08);
        }
        .predictor-table.up-table th {
            background: linear-gradient(180deg, #22c55e 0%, #16a34a 100%);
            color: #f0fdf4;
        }
        .predictor-table.up-table td {
            background: rgba(240, 253, 244, 0.95);
            border-bottom: 1px solid rgba(22, 163, 74, 0.14);
            color: #14532d;
        }
        .predictor-table.up-table tbody tr:nth-child(even) td {
            background: rgba(220, 252, 231, 0.92);
        }
        .predictor-table.down-table {
            border: 1px solid rgba(220, 38, 38, 0.24);
            box-shadow: 0 10px 22px rgba(239, 68, 68, 0.08);
        }
        .predictor-table.down-table th {
            background: linear-gradient(180deg, #ef4444 0%, #dc2626 100%);
            color: #fef2f2;
        }
        .predictor-table.down-table td {
            background: rgba(254, 242, 242, 0.96);
            border-bottom: 1px solid rgba(220, 38, 38, 0.14);
            color: #7f1d1d;
        }
        .predictor-table.down-table tbody tr:nth-child(even) td {
            background: rgba(254, 226, 226, 0.92);
        }
        .sidebar-card {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(191, 219, 254, 0.18);
            border-radius: 14px;
            padding: 0.85rem 0.85rem 0.35rem 0.85rem;
            margin-bottom: 0.9rem;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
        }
        .sidebar-card p {
            margin: 0 0 0.5rem 0;
            color: #dbeafe;
            font-size: 0.9rem;
            font-weight: 600;
        }
        div[data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 0.9rem;
            box-shadow: 0 12px 28px rgba(37, 99, 235, 0.08);
        }
        div[data-testid="stDataFrame"] {
            background: #ffffff;
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 0.45rem;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"] > div {
            background: rgba(255, 255, 255, 0.16) !important;
            border: 1px solid rgba(191, 219, 254, 0.32) !important;
            border-radius: 12px !important;
            color: #f8fafc !important;
        }
        div[data-baseweb="select"] span,
        div[data-baseweb="input"] input,
        div[data-baseweb="base-input"] input {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            font-weight: 700 !important;
        }
        div[data-baseweb="input"] svg,
        div[data-baseweb="base-input"] svg,
        div[data-baseweb="select"] svg {
            fill: #ffffff !important;
            color: #ffffff !important;
        }
        [data-testid="stSidebar"] .stNumberInput input,
        [data-testid="stSidebar"] .stNumberInput input[type="number"] {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            caret-color: #ffffff !important;
            font-weight: 700 !important;
            opacity: 1 !important;
        }
        [data-testid="stSidebar"] .stNumberInput div[data-baseweb="input"] > div,
        [data-testid="stSidebar"] .stNumberInput div[data-baseweb="base-input"] > div {
            background: #0f172a !important;
            border: 1px solid rgba(148, 163, 184, 0.42) !important;
        }
        [data-testid="stSidebar"] .stNumberInput button {
            background: #0f172a !important;
            border: 1px solid rgba(148, 163, 184, 0.42) !important;
            border-radius: 10px !important;
            color: #ffffff !important;
        }
        [data-testid="stSidebar"] .stNumberInput button:hover {
            background: #1e293b !important;
            border: 1px solid rgba(191, 219, 254, 0.4) !important;
            color: #ffffff !important;
        }
        .stCheckbox label, .stSelectbox label, .stNumberInput label {
            color: #e2e8f0 !important;
            font-weight: 600;
        }
        .stButton > button {
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            color: #ffffff;
            border: none;
            border-radius: 12px;
            padding: 0.6rem 1rem;
            font-weight: 700;
            box-shadow: 0 12px 24px rgba(37, 99, 235, 0.28);
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
            color: #ffffff;
        }
        .stAlert {
            border-radius: 14px;
        }
        .stCaption {
            color: #475569;
        }
        h1, h2, h3 {
            color: #0f172a;
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


def render_market_intelligence_sidebar(payload: dict | None) -> None:
    news_rows, global_cue_rows, overall_global_cue = build_market_intelligence_payload(payload)
    st.header("Market Intelligence")
    intelligence_section = st.radio(
        "Section",
        options=("Stock In News", "Global Cues"),
        horizontal=True,
        key="market_intelligence_section",
        label_visibility="collapsed",
    )

    if intelligence_section == "Stock In News":
        st.markdown('<div class="sidebar-card"><p>Stock In News</p></div>', unsafe_allow_html=True)
        if news_rows:
            for row in news_rows:
                signal = str(row.get("signal", "Neutral"))
                signal_color = "#15803d" if signal == "Bullish" else "#b91c1c" if signal == "Bearish" else "#475569"
                st.markdown(
                    (
                        '<div class="sidebar-card">'
                        f'<p style="margin-bottom:0.35rem;">{html.escape(str(row.get("stock", "")))}</p>'
                        f'<div style="font-size:0.82rem; line-height:1.35; margin-bottom:0.35rem;">'
                        f'<a href="{html.escape(str(row.get("link", "")), quote=True)}" target="_blank">{html.escape(str(row.get("headline", "")))}</a>'
                        '</div>'
                        f'<div style="font-size:0.78rem; color:{signal_color}; font-weight:700;">{signal}</div>'
                        '</div>'
                    ),
                    unsafe_allow_html=True,
                )
        else:
            st.info("No stock-specific news found for current turning candidates.")
    else:
        st.markdown('<div class="sidebar-card"><p>Global Cues For The Day</p></div>', unsafe_allow_html=True)
        st.caption(f"Overall cue: {overall_global_cue}")
        if global_cue_rows:
            for row in global_cue_rows:
                signal = str(row.get("signal", "Neutral"))
                signal_color = "#15803d" if signal == "Bullish" else "#b91c1c" if signal == "Bearish" else "#475569"
                st.markdown(
                    (
                        '<div class="sidebar-card">'
                        f'<p style="margin-bottom:0.3rem;">{html.escape(str(row.get("market", "")))}</p>'
                        f'<div style="font-size:0.8rem; margin-bottom:0.2rem;">Last: {float(row.get("last", 0.0)):.2f}</div>'
                        f'<div style="font-size:0.8rem; margin-bottom:0.2rem;">Change: {float(row.get("change_pct", 0.0)):.2f}%</div>'
                        f'<div style="font-size:0.78rem; color:{signal_color}; font-weight:700;">{signal}</div>'
                        '</div>'
                    ),
                    unsafe_allow_html=True,
                )
        else:
            st.info("No live global cue data was available right now.")


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


def main() -> None:
    st.set_page_config(page_title="Stock Trend Dashboard", layout="wide")
    apply_page_style()
    st.title("Stock Analyzer Trend Dashboard")
    st.caption("Runs the trend scripts directly and renders the latest Nifty 50, Midcap 100, and Smallcap 100 JSON results, including standardized turning-trend candidates.")

    if "latest_status" not in st.session_state:
        st.session_state["latest_status"] = load_existing_status()
    with st.sidebar:
        st.header("Refresh Settings")
        selected_index = st.selectbox(
            "Index",
            options=INDEX_OPTIONS,
            format_func=lambda item: INDEX_LABELS[item],
            index=INDEX_OPTIONS.index(DEFAULT_INDEX),
        )
        trade_mode = st.selectbox(
            "Trade Mode",
            options=list(TRADE_MODE_OPTIONS),
            format_func=lambda item: TRADE_MODE_LABELS[item],
            index=0,
        )
        trade_mode_settings = get_trade_mode_settings(trade_mode)
        st.markdown(
            f"<div class='sidebar-card'><p>Lookback: {trade_mode_settings['lookback_days']} days<br/>Horizon: {trade_mode_settings['horizon']}</p></div>",
            unsafe_allow_html=True,
        )
        st.caption(trade_mode_settings["caption"])
        if st.button("Refresh Data", type="primary", use_container_width=True):
            st.session_state["latest_status"] = refresh_trend_data(selected_index, trade_mode)

        latest_status = st.session_state.get("latest_status")
        sidebar_payload = None
        if latest_status and latest_status.get("status") == "completed" and status_covers_index(latest_status, selected_index):
            sidebar_payload = load_index_payload_from_status(latest_status, selected_index)
        render_market_intelligence_sidebar(sidebar_payload)

    latest_status = st.session_state.get("latest_status")
    if latest_status:
        if latest_status.get("status") == "failed":
            st.error("Trend refresh failed.")
            if latest_status.get("failed_step"):
                st.caption(f"Failed step: `{latest_status['failed_step']}`")
            if latest_status.get("error_excerpt"):
                st.code(latest_status["error_excerpt"])
        else:
            st.success("Latest trend data loaded.")
            render_results(latest_status)
    else:
        st.info("Click **Refresh Data** to run the analyzer scripts and load the latest trend JSON files.")


if __name__ == "__main__":
    main()
