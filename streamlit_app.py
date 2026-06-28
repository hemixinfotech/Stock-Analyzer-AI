import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass

JOBS_DIR = REPO_ROOT / "jobs"
LATEST_JOB_DIR = JOBS_DIR / "latest"
INDEX_LABELS = {
    "nifty50": "Nifty 50",
    "nifty_midcap_100": "Nifty Midcap 100",
    "nifty_smallcap_100": "Nifty Smallcap 100",
}
INDEX_OPTIONS = list(INDEX_LABELS.keys())
DEFAULT_INDEX = "nifty50"


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
        [data-testid="stAppHeaderButton"],
        [data-testid="stToolbarActions"] a,
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
        [data-testid="stSidebar"] * {
            color: #e2e8f0;
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
            padding: 1rem 1rem 0.6rem 1rem;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
            margin-bottom: 1rem;
            border: 1px solid #dbeafe;
        }
        .predictor-card.up-card {
            border-top: 5px solid #16a34a;
            background: linear-gradient(180deg, #ecfdf5 0%, #ffffff 32%);
        }
        .predictor-card.down-card {
            border-top: 5px solid #dc2626;
            background: linear-gradient(180deg, #fef2f2 0%, #ffffff 32%);
        }
        .predictor-card h3 {
            margin: 0 0 0.35rem 0;
            font-size: 1.2rem;
        }
        .predictor-card p {
            margin: 0 0 0.8rem 0;
            color: #475569;
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


def get_project_python() -> str:
    configured = os.getenv("PROJECT_PYTHON", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    return sys.executable


def clear_jobs_dir() -> None:
    if not JOBS_DIR.exists():
        return
    for child in JOBS_DIR.iterdir():
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
    up = len([record for record in records if record.get("trend") == "up"])
    down = len([record for record in records if record.get("trend") == "down"])
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


def run_command(command: list[str], stdout_path: Path, stderr_path: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
    stdout_path.write_text(result.stdout or "", encoding="utf-8")
    stderr_path.write_text(result.stderr or "", encoding="utf-8")
    return result


def refresh_trend_data(index_name: str, lookback: int, intraday: bool) -> dict:
    clear_jobs_dir()
    LATEST_JOB_DIR.mkdir(parents=True, exist_ok=True)

    output_json = LATEST_JOB_DIR / "results_nifty_trend.json"

    agent_stdout = LATEST_JOB_DIR / "agent.stdout.log"
    agent_stderr = LATEST_JOB_DIR / "agent.stderr.log"

    python_executable = get_project_python()
    agent_cmd = [
        python_executable,
        str(REPO_ROOT / "scripts" / "agent_nifty_trend.py"),
        "--indices", index_name,
        "--lookback", str(int(lookback)),
        "--out", str(output_json),
    ]
    if intraday:
        agent_cmd.append("--intraday")

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
                "lookback": lookback,
                "intraday": intraday,
                "python_executable": python_executable,
            },
        }
        write_json(LATEST_JOB_DIR / "status.json", failure_payload)
        progress.progress(100, text="Trend refresh failed.")
        return failure_payload

    progress.progress(70, text="Preparing selected index data...")
    per_index_artifacts = write_per_index_json_files(output_json, LATEST_JOB_DIR, [index_name])

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
            "lookback": lookback,
            "intraday": intraday,
            "python_executable": python_executable,
        },
    }
    progress.progress(100, text="Trend refresh complete.")

    write_json(LATEST_JOB_DIR / "status.json", status_payload)
    return status_payload


def load_index_payload_from_status(status_payload: dict, index_name: str) -> dict | None:
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
    payload = load_index_payload_from_status(status_payload, index_name)
    if payload is None:
        raise ValueError(f"Refreshed data is unavailable for {index_name}. Please refresh the data first.")

    report_path = get_index_report_path_from_status(status_payload, index_name)
    if report_path is None:
        raise ValueError(f"Refreshed report file is unavailable for {index_name}. Please refresh the data first.")

    output_path = LATEST_JOB_DIR / f"prediction_{sanitize_index_name(index_name)}.json"
    predictor_stdout = LATEST_JOB_DIR / "predictor.stdout.log"
    predictor_stderr = LATEST_JOB_DIR / "predictor.stderr.log"
    predictor_cmd = [
        get_project_python(),
        str(REPO_ROOT / "scripts" / "github_stock_predictor.py"),
        "--input",
        str(report_path),
        "--index",
        index_name,
        "--out",
        str(output_path),
    ]
    predictor_result = run_command(predictor_cmd, predictor_stdout, predictor_stderr)
    if predictor_result.returncode != 0:
        raise ValueError((predictor_result.stderr or predictor_result.stdout or "OpenAI predictor failed.").strip())
    if not output_path.exists():
        raise ValueError("GitHub Models predictor did not create an output file.")
    return load_json(output_path)


def render_summary(summary: dict) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Tickers", summary.get("total_tickers", 0))
    col2.metric("Percent Up", f"{(summary.get('percent_up') or 0) * 100:.2f}%")
    col3.metric("Percent Down", f"{(summary.get('percent_down') or 0) * 100:.2f}%")
    col4.metric("Market Trend", str(summary.get("market_trend", "unknown")).title())


def render_index_result(title: str, payload: dict) -> None:
    render_summary(payload.get("summary", {}))

    records = payload.get("records", [])
    if not records:
        st.warning("No records found for this index.")
        return

    frame = pd.DataFrame(records)
    if "ticker" in frame.columns:
        frame["stock_name"] = frame["ticker"].astype(str).str.replace(".NS", "", regex=False)
    if "trend" in frame.columns:
        frame["trend direction"] = frame["trend"].astype(str).str.title()
    visible_columns = [column for column in ["stock_name", "trend direction", "close", "rsi"] if column in frame.columns]
    display_frame = frame[visible_columns] if visible_columns else frame

    if "stock_name" in display_frame.columns:
        display_frame = display_frame.rename(
            columns={
                "stock_name": "Stock Name",
                "trend direction": "Trend Direction",
                "close": "Close Price",
                "rsi": "RSI",
            }
        )

    uptrend_frame = display_frame[display_frame["Trend Direction"] == "Up"] if "Trend Direction" in display_frame.columns else pd.DataFrame()
    downtrend_frame = display_frame[display_frame["Trend Direction"] == "Down"] if "Trend Direction" in display_frame.columns else pd.DataFrame()

    if "RSI" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.sort_values(by="RSI", ascending=False, na_position="last")
    if "RSI" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.sort_values(by="RSI", ascending=True, na_position="last")

    def themed_table_html(dataframe: pd.DataFrame, theme: str):
        table_class = "up-table" if theme == "up" else "down-table"
        headers = "".join(f"<th>{column}</th>" for column in dataframe.columns)
        rows = []
        for _, row in dataframe.iterrows():
            formatted_values = []
            for column, value in row.items():
                if column in {"Close Price", "RSI"} and isinstance(value, (int, float)):
                    formatted_values.append(f"{value:.2f}")
                else:
                    formatted_values.append(value)
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
    st.subheader("Uptrend Stocks")
    if uptrend_frame.empty:
        st.info("No uptrend stocks in the latest refresh.")
    else:
        st.markdown(themed_table_html(uptrend_frame, "up"), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="trend-card downtrend-card">', unsafe_allow_html=True)
    st.subheader("Downtrend Stocks")
    if downtrend_frame.empty:
        st.info("No downtrend stocks in the latest refresh.")
    else:
        st.markdown(themed_table_html(downtrend_frame, "down"), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_results(status_payload: dict) -> None:
    selected_index = status_payload.get("params", {}).get("index", DEFAULT_INDEX)

    st.markdown(
        f"""
        <div class="dashboard-hero">
            <h2>{INDEX_LABELS.get(selected_index, selected_index)}</h2>
            <p>Latest trend refresh with separate uptrend and downtrend stock blocks.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    payload = load_index_payload_from_status(status_payload, selected_index)
    if payload is None:
        st.info("Latest cached report is unavailable on this deployment. Click **Refresh Data** to generate a new report.")
        return

    render_index_result(INDEX_LABELS.get(selected_index, selected_index), payload)


def render_prediction_results(prediction: dict) -> None:
    st.markdown(
        f"""
        <div class="dashboard-hero">
            <h2>GitHub AI Stock Predictor - {INDEX_LABELS.get(prediction.get("index", DEFAULT_INDEX), prediction.get("index", DEFAULT_INDEX))}</h2>
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
        return frame.rename(
            columns={
                "ticker": "Stock Name",
                "rsi": "RSI",
                "close": "Close Price",
                "signal_strength": "Signal Strength",
                "volume_ratio": "Volume Ratio",
            }
        )

    def themed_predictor_block(title: str, subtitle: str, dataframe: pd.DataFrame, theme: str) -> None:
        st.markdown(
            f"""
            <div class="predictor-card {'up-card' if theme == 'up' else 'down-card'}">
                <h3>{title}</h3>
                <p>{subtitle}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if dataframe.empty:
            st.info(f"No {theme}trend picks available.")
        else:
            st.dataframe(dataframe, use_container_width=True, hide_index=True)

    uptrend_frame = picks_to_frame(prediction.get("uptrend_picks", []))
    downtrend_frame = picks_to_frame(prediction.get("downtrend_picks", []))

    themed_predictor_block(
        "AI Uptrend Picks",
        "Top bullish continuation candidates for the selected refreshed index.",
        uptrend_frame,
        "up",
    )
    themed_predictor_block(
        "AI Downtrend Picks",
        "Top bearish continuation candidates for the selected refreshed index.",
        downtrend_frame,
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
    st.caption("Runs the trend scripts directly and renders the latest Nifty 50, Midcap 100, and Smallcap 100 JSON results.")

    if "latest_status" not in st.session_state:
        st.session_state["latest_status"] = load_existing_status()
    if "prediction_status" not in st.session_state:
        st.session_state["prediction_status"] = None
    if "prediction_result" not in st.session_state:
        st.session_state["prediction_result"] = None

    with st.sidebar:
        st.header("Refresh Settings")
        selected_index = st.selectbox(
            "Index",
            options=INDEX_OPTIONS,
            format_func=lambda item: INDEX_LABELS[item],
            index=INDEX_OPTIONS.index(DEFAULT_INDEX),
        )
        st.markdown('<div class="sidebar-card"><p>Lookback Period</p></div>', unsafe_allow_html=True)
        lookback = st.number_input("Lookback Days", min_value=1, max_value=30, value=30, step=1)
        intraday = st.checkbox("Enable Intraday VWAP", value=False)
        if st.button("Refresh Data", type="primary", use_container_width=True):
            st.session_state["latest_status"] = refresh_trend_data(selected_index, int(lookback), intraday)
            st.session_state["prediction_status"] = None
            st.session_state["prediction_result"] = None

        st.header("GitHub AI Stock Predictor")
        predictor_index = st.selectbox(
            "Predictor Index",
            options=INDEX_OPTIONS,
            format_func=lambda item: INDEX_LABELS[item],
            index=INDEX_OPTIONS.index(DEFAULT_INDEX),
            key="predictor_index",
        )
        sidebar_predictor_warning = None
        if st.button("GitHub AI Stock Predict", use_container_width=True):
            latest_status = st.session_state.get("latest_status")
            if not latest_status or latest_status.get("status") != "completed":
                st.session_state["prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": "First refresh the data, then use GitHub AI Stock Predictor.",
                }
                sidebar_predictor_warning = st.session_state["prediction_status"]["error_excerpt"]
            elif latest_status.get("params", {}).get("index") != predictor_index:
                st.session_state["prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": f"First refresh data for {INDEX_LABELS[predictor_index]}, then use GitHub AI Stock Predictor.",
                }
                sidebar_predictor_warning = st.session_state["prediction_status"]["error_excerpt"]
            else:
                try:
                    prediction_result = run_openai_predictor(predictor_index, latest_status)
                    st.session_state["prediction_status"] = {"status": "completed"}
                    st.session_state["prediction_result"] = prediction_result
                except ValueError as exc:
                    st.session_state["prediction_status"] = {"status": "failed", "error_excerpt": str(exc)}
                    sidebar_predictor_warning = str(exc)
        if sidebar_predictor_warning:
            st.warning(sidebar_predictor_warning)

    latest_status = st.session_state.get("latest_status")
    prediction_result = st.session_state.get("prediction_result")
    if latest_status:
        if latest_status.get("status") == "failed":
            st.error("Trend refresh failed.")
            if latest_status.get("failed_step"):
                st.caption(f"Failed step: `{latest_status['failed_step']}`")
            if latest_status.get("error_excerpt"):
                st.code(latest_status["error_excerpt"])
        elif not prediction_result:
            st.success("Latest trend data loaded.")
            render_results(latest_status)
    else:
        st.info("Click **Refresh Data** to run the analyzer scripts and load the latest trend JSON files.")

    prediction_status = st.session_state.get("prediction_status")
    if prediction_status and prediction_status.get("status") == "warning":
        if not prediction_result:
            st.info("Use Refresh Data first, then run the GitHub AI Stock Predictor for the same selected index.")
    elif prediction_status and prediction_status.get("status") == "failed":
        st.error("GitHub AI stock prediction failed.")
        if prediction_status.get("error_excerpt"):
            st.code(str(prediction_status["error_excerpt"]))
    elif prediction_result:
        render_prediction_results(prediction_result)


if __name__ == "__main__":
    main()
