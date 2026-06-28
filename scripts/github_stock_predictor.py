#!/usr/bin/env python3
"""
github_stock_predictor.py

Read refreshed index trend data and ask GitHub Models to pick the top 2 uptrend
and top 2 downtrend stocks for the selected index and day range.
"""
import argparse
import json
import math
import os
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "openai/gpt-4.1-mini").strip() or "openai/gpt-4.1-mini"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def signal_strength(record: dict) -> int:
    signals = record.get("signals", {})
    if not isinstance(signals, dict):
        return 0
    return sum(1 for value in signals.values() if value is True)


def normalize_numeric(value: object, default: float | None) -> float | None:
    if isinstance(value, (int, float)) and not math.isnan(float(value)):
        return float(value)
    return default


def rank_prediction_candidates(records: list[dict], trend: str) -> list[dict]:
    trend_records = [record for record in records if record.get("trend") == trend]
    if trend == "up":
        return sorted(
            trend_records,
            key=lambda record: (
                normalize_numeric(record.get("rsi"), -1.0),
                normalize_numeric(record.get("volume_ratio"), -1.0),
                signal_strength(record),
                normalize_numeric(record.get("close"), -1.0),
            ),
            reverse=True,
        )
    return sorted(
        trend_records,
        key=lambda record: (
            normalize_numeric(record.get("rsi"), 101.0),
            -normalize_numeric(record.get("volume_ratio"), 0.0),
            -signal_strength(record),
            normalize_numeric(record.get("close"), 10**12),
        ),
    )


def build_prediction_candidates(records: list[dict], trend: str, limit: int = 8) -> list[dict]:
    ranked_records = rank_prediction_candidates(records, trend)
    return [
        {
            "ticker": str(record.get("ticker", "")),
            "rsi": normalize_numeric(record.get("rsi"), None),
            "close": normalize_numeric(record.get("close"), None),
            "signal_strength": signal_strength(record),
            "volume": normalize_numeric(record.get("volume"), None),
            "avg_volume_20": normalize_numeric(record.get("avg_volume_20"), None),
            "volume_ratio": normalize_numeric(record.get("volume_ratio"), None),
        }
        for record in ranked_records[:limit]
    ]


def extract_message_content(message_content: object) -> str:
    if isinstance(message_content, str):
        return message_content.strip()
    if isinstance(message_content, list):
        parts = []
        for item in message_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()
    return ""


def request_github_prediction(index_name: str, payload: dict) -> dict:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured. Add it to the repo-root .env file or environment variables.")

    summary = payload.get("summary", {})
    records = payload.get("records", [])
    candidate_payload = {
        "index": index_name,
        "market_sentiment": summary.get("market_trend", "neutral"),
        "uptrend_candidates": build_prediction_candidates(records, "up"),
        "downtrend_candidates": build_prediction_candidates(records, "down"),
    }
    if not candidate_payload["uptrend_candidates"] and not candidate_payload["downtrend_candidates"]:
        raise RuntimeError("No uptrend or downtrend stocks are available in the refreshed data.")

    prompt = (
        "You are analyzing refreshed Indian stock index trend data for one trading-day setup.\n"
        "From the candidate lists, choose the top 2 stocks with the highest probability of continuing uptrend "
        "and the top 2 stocks with the highest probability of continuing downtrend for the selected day range.\n"
        "Use only the given candidates. Respect the overall market sentiment when choosing the picks.\n"
        "Return strict JSON only with keys: market_sentiment, uptrend_picks, downtrend_picks, analysis.\n"
        "Each object inside uptrend_picks and downtrend_picks must contain ticker, rsi, close, signal_strength, "
        "volume, avg_volume_20, volume_ratio.\n"
        "Use volume_ratio together with RSI and signal strength to judge continuation probability.\n\n"
        f"Candidate data:\n{json.dumps(candidate_payload, indent=2)}"
    )
    response = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "model": GITHUB_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful stock-ranking assistant. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        details = ""
        try:
            error_payload = response.json()
            details = str(error_payload.get("error", {}).get("message") or "").strip()
        except ValueError:
            details = response.text.strip()
        if response.status_code == 401:
            raise RuntimeError(
                "GitHub Models authentication failed. Check that GITHUB_TOKEN in your repo-root .env file is valid and has models:read access."
            ) from exc
        if response.status_code == 403:
            raise RuntimeError(
                "GitHub Models access was denied. Check that your GitHub token has models:read permission and your account can access GitHub Models."
            ) from exc
        if details:
            raise RuntimeError(f"GitHub Models request failed: {details}") from exc
        raise RuntimeError(f"GitHub Models request failed with HTTP {response.status_code}.") from exc
    response_json = response.json()
    output_text = ""
    choices = response_json.get("choices")
    if isinstance(choices, list) and choices:
        output_text = extract_message_content(choices[0].get("message", {}).get("content"))
    if not output_text:
        raise RuntimeError("GitHub Models returned an empty response.")
    prediction = json.loads(output_text)
    if not isinstance(prediction, dict):
        raise RuntimeError("GitHub Models returned an invalid prediction payload.")

    prediction["provider"] = f"GitHub Models ({GITHUB_MODEL})"
    prediction["index"] = index_name
    prediction["input_market_sentiment"] = str(summary.get("market_trend", "neutral")).title()
    return prediction


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Predict top 2 uptrend and downtrend stocks using GitHub Models")
    parser.add_argument("--input", required=True, help="Input refreshed per-index JSON file")
    parser.add_argument("--index", required=True, help="Index name")
    parser.add_argument("--out", required=True, help="Output JSON file")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    payload = load_json(input_path)
    prediction = request_github_prediction(args.index, payload)
    write_json(Path(args.out), prediction)
    print(f"Wrote prediction to {args.out}")


if __name__ == "__main__":
    main()
