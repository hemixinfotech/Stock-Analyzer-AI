import json
import os
import shutil
import subprocess
import sys
import hashlib
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
BULLISH_TRENDS = {"up", "turning_bullish"}
BEARISH_TRENDS = {"down", "turning_bearish"}
TREND_PRIORITY = {
    "Turning Bullish": 0,
    "Up": 1,
    "Turning Bearish": 0,
    "Down": 1,
}


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


def refresh_trend_data(index_name: str, lookback: int, intraday: bool) -> dict:
    clear_latest_job_dir()
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
            "avg_volume_20", "volume_ratio", "turning_bullish_score", "turning_bearish_score",
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
        frame["trend direction"] = frame["trend"].astype(str).str.replace("_", " ").str.title()
    visible_columns = [column for column in ["stock_name", "trend direction", "close", "percent_change", "rsi"] if column in frame.columns]
    display_frame = frame[visible_columns] if visible_columns else frame

    if "stock_name" in display_frame.columns:
        display_frame = display_frame.rename(
            columns={
                "stock_name": "Stock Name",
                "trend direction": "Trend Direction",
                "close": "Close Price",
                "percent_change": "% Change",
                "rsi": "RSI",
            }
        )

    uptrend_frame = (
        display_frame[display_frame["Trend Direction"].isin(["Up", "Turning Bullish"])]
        if "Trend Direction" in display_frame.columns else pd.DataFrame()
    )
    downtrend_frame = (
        display_frame[display_frame["Trend Direction"].isin(["Down", "Turning Bearish"])]
        if "Trend Direction" in display_frame.columns else pd.DataFrame()
    )

    if "Trend Direction" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.assign(_priority=uptrend_frame["Trend Direction"].map(TREND_PRIORITY).fillna(99))
    if "Trend Direction" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.assign(_priority=downtrend_frame["Trend Direction"].map(TREND_PRIORITY).fillna(99))

    if "RSI" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.sort_values(by=["_priority", "RSI"], ascending=[True, False], na_position="last")
    elif "_priority" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.sort_values(by="_priority", ascending=True, na_position="last")
    if "RSI" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.sort_values(by=["_priority", "RSI"], ascending=[True, True], na_position="last")
    elif "_priority" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.sort_values(by="_priority", ascending=True, na_position="last")

    if "_priority" in uptrend_frame.columns:
        uptrend_frame = uptrend_frame.drop(columns=["_priority"])
    if "_priority" in downtrend_frame.columns:
        downtrend_frame = downtrend_frame.drop(columns=["_priority"])

    def themed_table_html(dataframe: pd.DataFrame, theme: str):
        table_class = "up-table" if theme == "up" else "down-table"
        headers = "".join(f"<th>{column}</th>" for column in dataframe.columns)
        rows = []
        for _, row in dataframe.iterrows():
            formatted_values = []
            for column, value in row.items():
                if column in {"Close Price", "RSI", "% Change"} and isinstance(value, (int, float)):
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
                "signal_strength": "Signal Strength",
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
        "Top bullish reversal candidates with buy, target, and stop-loss levels.",
        picks_to_frame(prediction.get("turning_bullish_picks", [])),
        "up",
    )
    themed_predictor_block(
        "AI Turning Bearish Picks",
        "Top bearish reversal candidates with buy, target, and stop-loss levels.",
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
    st.caption("Runs the trend scripts directly and renders the latest Nifty 50, Midcap 100, and Smallcap 100 JSON results.")

    if "latest_status" not in st.session_state:
        st.session_state["latest_status"] = load_existing_status()
    if "prediction_status" not in st.session_state:
        st.session_state["prediction_status"] = None
    if "prediction_result" not in st.session_state:
        st.session_state["prediction_result"] = None
    if "turning_prediction_status" not in st.session_state:
        st.session_state["turning_prediction_status"] = None
    if "turning_prediction_result" not in st.session_state:
        st.session_state["turning_prediction_result"] = None

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
            st.session_state["turning_prediction_status"] = None
            st.session_state["turning_prediction_result"] = None

        st.header("AI Predictor")
        predictor_index = st.selectbox(
            "Predictor Index",
            options=INDEX_OPTIONS,
            format_func=lambda item: INDEX_LABELS[item],
            index=INDEX_OPTIONS.index(DEFAULT_INDEX),
            key="predictor_index",
        )
        github_token_configured = bool(get_runtime_setting("GITHUB_TOKEN"))
        sidebar_predictor_warning = None
        if st.button("Run AI Predictor", use_container_width=True):
            latest_status = st.session_state.get("latest_status")
            if not github_token_configured:
                st.session_state["prediction_status"] = {
                    "status": "failed",
                    "error_excerpt": "GITHUB_TOKEN is not configured. Add it to Streamlit secrets, the repo-root .env file, or environment variables.",
                }
                sidebar_predictor_warning = st.session_state["prediction_status"]["error_excerpt"]
            elif not latest_status or latest_status.get("status") != "completed":
                st.session_state["prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": "First refresh the data, then use AI Predictor.",
                }
                sidebar_predictor_warning = st.session_state["prediction_status"]["error_excerpt"]
            elif latest_status.get("params", {}).get("index") != predictor_index:
                st.session_state["prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": f"First refresh data for {INDEX_LABELS[predictor_index]}, then use AI Predictor.",
                }
                sidebar_predictor_warning = st.session_state["prediction_status"]["error_excerpt"]
            else:
                try:
                    prediction_result = run_openai_predictor(predictor_index, latest_status)
                    st.session_state["prediction_status"] = {"status": "completed"}
                    st.session_state["prediction_result"] = prediction_result
                    st.session_state["turning_prediction_status"] = None
                    st.session_state["turning_prediction_result"] = None
                except ValueError as exc:
                    st.session_state["prediction_status"] = {"status": "failed", "error_excerpt": str(exc)}
                    sidebar_predictor_warning = str(exc)
        if sidebar_predictor_warning:
            st.warning(sidebar_predictor_warning)

        st.header("AI Turning Predictor")
        turning_predictor_index = st.selectbox(
            "Turning Predictor Index",
            options=INDEX_OPTIONS,
            format_func=lambda item: INDEX_LABELS[item],
            index=INDEX_OPTIONS.index(DEFAULT_INDEX),
            key="turning_predictor_index",
        )
        sidebar_turning_warning = None
        if st.button("Run Turning AI Predictor", use_container_width=True):
            latest_status = st.session_state.get("latest_status")
            if not github_token_configured:
                st.session_state["turning_prediction_status"] = {
                    "status": "failed",
                    "error_excerpt": "GITHUB_TOKEN is not configured. Add it to Streamlit secrets, the repo-root .env file, or environment variables.",
                }
                sidebar_turning_warning = st.session_state["turning_prediction_status"]["error_excerpt"]
            elif not latest_status or latest_status.get("status") != "completed":
                st.session_state["turning_prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": "First refresh the data, then use AI Turning Predictor.",
                }
                sidebar_turning_warning = st.session_state["turning_prediction_status"]["error_excerpt"]
            elif latest_status.get("params", {}).get("index") != turning_predictor_index:
                st.session_state["turning_prediction_status"] = {
                    "status": "warning",
                    "error_excerpt": f"First refresh data for {INDEX_LABELS[turning_predictor_index]}, then use AI Turning Predictor.",
                }
                sidebar_turning_warning = st.session_state["turning_prediction_status"]["error_excerpt"]
            else:
                try:
                    turning_prediction_result = run_ai_predictor(turning_predictor_index, latest_status, predictor_mode="turning")
                    st.session_state["turning_prediction_status"] = {"status": "completed"}
                    st.session_state["turning_prediction_result"] = turning_prediction_result
                    st.session_state["prediction_status"] = None
                    st.session_state["prediction_result"] = None
                except ValueError as exc:
                    st.session_state["turning_prediction_status"] = {"status": "failed", "error_excerpt": str(exc)}
                    sidebar_turning_warning = str(exc)
        if sidebar_turning_warning:
            st.warning(sidebar_turning_warning)

    latest_status = st.session_state.get("latest_status")
    prediction_result = st.session_state.get("prediction_result")
    turning_prediction_result = st.session_state.get("turning_prediction_result")
    if latest_status:
        if latest_status.get("status") == "failed":
            st.error("Trend refresh failed.")
            if latest_status.get("failed_step"):
                st.caption(f"Failed step: `{latest_status['failed_step']}`")
            if latest_status.get("error_excerpt"):
                st.code(latest_status["error_excerpt"])
        elif not prediction_result and not turning_prediction_result:
            st.success("Latest trend data loaded.")
            render_results(latest_status)
    else:
        st.info("Click **Refresh Data** to run the analyzer scripts and load the latest trend JSON files.")

    prediction_status = st.session_state.get("prediction_status")
    if prediction_status and prediction_status.get("status") == "warning":
        if not prediction_result:
            st.info("Use Refresh Data first, then run AI Predictor for the same selected index.")
    elif prediction_status and prediction_status.get("status") == "failed":
        st.error("AI prediction failed.")
        if prediction_status.get("error_excerpt"):
            st.code(str(prediction_status["error_excerpt"]))
    elif prediction_result:
        render_prediction_results(prediction_result)

    turning_prediction_status = st.session_state.get("turning_prediction_status")
    if turning_prediction_status and turning_prediction_status.get("status") == "warning":
        if not turning_prediction_result:
            st.info("Use Refresh Data first, then run AI Turning Predictor for the same selected index.")
    elif turning_prediction_status and turning_prediction_status.get("status") == "failed":
        st.error("AI turning prediction failed.")
        if turning_prediction_status.get("error_excerpt"):
            st.code(str(turning_prediction_status["error_excerpt"]))
    elif turning_prediction_result:
        render_turning_prediction_results(turning_prediction_result)


if __name__ == "__main__":
    main()
