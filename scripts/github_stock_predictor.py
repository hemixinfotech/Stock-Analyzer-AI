#!/usr/bin/env python3
"""
github_stock_predictor.py

Read refreshed index trend data and ask GitHub Models to pick continuation and
reversal candidates with entry, target, and stop-loss levels.
"""
import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
BULLISH_TRENDS = {"up", "turning_bullish"}
BEARISH_TRENDS = {"down", "turning_bearish"}
ALL_INDICES = "all_indices"

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass


def get_runtime_setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    return default


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


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def rank_prediction_candidates(records: list[dict], trend: str) -> list[dict]:
    if trend == "up":
        trend_records = [record for record in records if record.get("trend") in BULLISH_TRENDS]
    else:
        trend_records = [record for record in records if record.get("trend") in BEARISH_TRENDS]
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


def rank_turning_candidates(records: list[dict], trend: str) -> list[dict]:
    if trend == "turning_bullish":
        trend_records = [record for record in records if record.get("trend") == "turning_bullish"]
        return sorted(
            trend_records,
            key=lambda record: (
                normalize_numeric(record.get("turning_bullish_probability"), -1.0),
                normalize_numeric(record.get("turning_bullish_score"), -1.0),
                normalize_numeric(record.get("volume_ratio"), -1.0),
                normalize_numeric(record.get("rsi"), -1.0),
                signal_strength(record),
            ),
            reverse=True,
        )
    trend_records = [record for record in records if record.get("trend") == "turning_bearish"]
    return sorted(
        trend_records,
        key=lambda record: (
            normalize_numeric(record.get("turning_bearish_probability"), -1.0),
            normalize_numeric(record.get("turning_bearish_score"), -1.0),
            normalize_numeric(record.get("volume_ratio"), -1.0),
            -normalize_numeric(record.get("rsi"), 101.0),
            signal_strength(record),
        ),
        reverse=True,
    )


def build_prediction_candidates(records: list[dict], trend: str, limit: int = 8) -> list[dict]:
    ranked_records = rank_prediction_candidates(records, trend)
    return [
        {
            "ticker": str(record.get("ticker", "")),
            "trend": str(record.get("trend", "")),
            "rsi": normalize_numeric(record.get("rsi"), None),
            "close": normalize_numeric(record.get("close"), None),
            "percent_change": normalize_numeric(record.get("percent_change"), None),
            "signal_strength": signal_strength(record),
            "volume": normalize_numeric(record.get("volume"), None),
            "avg_volume_20": normalize_numeric(record.get("avg_volume_20"), None),
            "volume_ratio": normalize_numeric(record.get("volume_ratio"), None),
        }
        for record in ranked_records[:limit]
    ]


def build_turning_trade_levels(record: dict, trend: str) -> tuple[float | None, float | None, float | None]:
    close = normalize_numeric(record.get("close"), None)
    atr14 = normalize_numeric(record.get("atr14"), None)
    probability_key = "turning_bullish_probability" if trend == "turning_bullish" else "turning_bearish_probability"
    probability = normalize_numeric(record.get(probability_key), 65.0) or 65.0
    if close is None:
        return None, None, None

    fallback_risk = close * 0.0125
    risk_unit = atr14 * 0.9 if atr14 is not None and atr14 > 0 else fallback_risk
    risk_unit = max(risk_unit, close * 0.006)
    reward_multiple = clamp(1.6 + ((probability - 65.0) / 35.0), 1.5, 2.6)

    if trend == "turning_bullish":
        stop_loss = close - risk_unit
        target_price = close + (risk_unit * reward_multiple)
    else:
        stop_loss = close + risk_unit
        target_price = close - (risk_unit * reward_multiple)
    return close, round(target_price, 2), round(stop_loss, 2)


def build_turning_candidates(records: list[dict], trend: str, limit: int | None = None) -> list[dict]:
    ranked_records = rank_turning_candidates(records, trend)
    score_key = "turning_bullish_score" if trend == "turning_bullish" else "turning_bearish_score"
    probability_key = "turning_bullish_probability" if trend == "turning_bullish" else "turning_bearish_probability"
    candidates = []
    for record in ranked_records[:limit]:
        buy_price, target_price, stop_loss = build_turning_trade_levels(record, trend)
        candidates.append(
            {
                "ticker": str(record.get("ticker", "")),
                "index": str(record.get("index", "")),
                "trend": str(record.get("trend", "")),
                "rsi": normalize_numeric(record.get("rsi"), None),
                "close": normalize_numeric(record.get("close"), None),
                "percent_change": normalize_numeric(record.get("percent_change"), None),
                "turning_score": normalize_numeric(record.get(score_key), None),
                "turning_probability": normalize_numeric(record.get(probability_key), None),
                "signal_strength": signal_strength(record),
                "volume": normalize_numeric(record.get("volume"), None),
                "avg_volume_20": normalize_numeric(record.get("avg_volume_20"), None),
                "volume_ratio": normalize_numeric(record.get("volume_ratio"), None),
                "atr14": normalize_numeric(record.get("atr14"), None),
                "buy_price": buy_price,
                "target_price": target_price,
                "stop_loss": stop_loss,
            }
        )
    return candidates


def build_turning_prediction(index_name: str, payload: dict) -> dict:
    summary = payload.get("summary", {})
    records = payload.get("records", [])
    bullish_candidates = build_turning_candidates(records, "turning_bullish")
    bearish_candidates = build_turning_candidates(records, "turning_bearish")
    if not bullish_candidates and not bearish_candidates:
        raise RuntimeError("No standardized turning bullish or turning bearish candidates are available in the refreshed data.")
    return {
        "index": index_name,
        "market_sentiment": summary.get("market_trend", "neutral"),
        "turning_bullish_picks": bullish_candidates,
        "turning_bearish_picks": bearish_candidates,
        "analysis": (
            "Deterministic turning-trend ranking based on weighted reversal probability, prior-trend context, "
            "momentum shift, price reclaim/breakdown, and volume confirmation."
        ),
        "provider": "Deterministic Turning Probability Model",
        "input_market_sentiment": str(summary.get("market_trend", "neutral")).title(),
    }


def split_payload_by_index(payload: dict) -> dict[str, dict]:
    records = payload.get("records", [])
    grouped_records: dict[str, list[dict]] = {}
    for record in records:
        index_name = str(record.get("index", "")).strip()
        if not index_name:
            continue
        grouped_records.setdefault(index_name, []).append(record)
    grouped_payloads = {}
    for index_name, index_records in grouped_records.items():
        grouped_payloads[index_name] = {
            "summary": {
                **payload.get("summary", {}),
                "total_tickers": len([record for record in index_records if record.get("trend")]),
            },
            "records": index_records,
        }
    return grouped_payloads


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


def parse_retry_after_seconds(response: requests.Response, default_seconds: int = 15) -> int:
    header_value = response.headers.get("Retry-After", "").strip()
    if not header_value:
        return default_seconds
    try:
        return max(1, int(float(header_value)))
    except ValueError:
        return default_seconds


def post_github_models_request(github_token: str, github_model: str, prompt: str) -> requests.Response:
    max_attempts = 3
    last_response = None
    for attempt in range(1, max_attempts + 1):
        response = requests.post(
            "https://models.github.ai/inference/chat/completions",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "model": github_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a careful stock-ranking assistant. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        if response.status_code != 429:
            return response
        last_response = response
        if attempt < max_attempts:
            time.sleep(parse_retry_after_seconds(response, default_seconds=15 * attempt))
    return last_response


def request_github_prediction(index_name: str, payload: dict, mode: str = "all") -> dict:
    if mode == "turning":
        if index_name == ALL_INDICES:
            per_index_predictions = []
            for child_index, child_payload in split_payload_by_index(payload).items():
                per_index_predictions.append(build_turning_prediction(child_index, child_payload))
            if not per_index_predictions:
                raise RuntimeError("No index records are available for turning prediction.")
            return {
                "index": index_name,
                "provider": "Deterministic Turning Probability Model",
                "analysis": "Standardized turning bullish and turning bearish lists grouped by index.",
                "indices": per_index_predictions,
            }
        return build_turning_prediction(index_name, payload)

    github_token = get_runtime_setting("GITHUB_TOKEN")
    github_model = get_runtime_setting("GITHUB_MODEL", "openai/gpt-4.1-mini") or "openai/gpt-4.1-mini"
    if not github_token:
        raise RuntimeError("GITHUB_TOKEN is not configured. Add it to the repo-root .env file or environment variables.")

    summary = payload.get("summary", {})
    records = payload.get("records", [])
    candidate_payload = {
        "index": index_name,
        "market_sentiment": summary.get("market_trend", "neutral"),
    }
    candidate_payload["uptrend_candidates"] = build_prediction_candidates(records, "up")
    candidate_payload["downtrend_candidates"] = build_prediction_candidates(records, "down")
    required_keys = ("uptrend_candidates", "downtrend_candidates")

    if not any(candidate_payload[key] for key in required_keys):
        raise RuntimeError("No trend candidates are available in the refreshed data.")

    prompt = (
        "You are analyzing refreshed Indian stock index trend data for one trading-day setup.\n"
        "From the candidate lists, choose the top 2 stocks with the highest probability of continuing uptrend "
        "and the top 2 stocks with the highest probability of continuing downtrend for the selected day range.\n"
        "Use only the given candidates. Respect the overall market sentiment when choosing the picks.\n"
        "Return strict JSON only with keys: market_sentiment, uptrend_picks, downtrend_picks, analysis.\n"
        "Each pick object must contain ticker, trend, rsi, close, signal_strength, volume, avg_volume_20, volume_ratio, buy_price, target_price, stop_loss.\n"
        "Set buy_price near the current close, target_price above buy_price for bullish setups and below buy_price for bearish setups, "
        "and stop_loss on the opposite side with realistic one-day risk-reward logic.\n"
        "Use volume_ratio together with RSI, percent_change, and signal strength to judge continuation probability.\n\n"
        f"Candidate data:\n{json.dumps(candidate_payload, indent=2)}"
    )
    response = post_github_models_request(github_token, github_model, prompt)
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
        if response.status_code == 429:
            wait_seconds = parse_retry_after_seconds(response, default_seconds=30)
            if details:
                raise RuntimeError(
                    f"GitHub Models rate limit hit after retries. Wait about {wait_seconds} seconds and try again. Details: {details}"
                ) from exc
            raise RuntimeError(
                f"GitHub Models rate limit hit after retries. Wait about {wait_seconds} seconds and try again."
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

    prediction["provider"] = f"GitHub Models ({github_model})"
    prediction["index"] = index_name
    prediction["input_market_sentiment"] = str(summary.get("market_trend", "neutral")).title()
    return prediction


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Predict top 2 uptrend and downtrend stocks using GitHub Models")
    parser.add_argument("--input", required=True, help="Input refreshed per-index JSON file")
    parser.add_argument("--index", required=True, help="Index name or all_indices")
    parser.add_argument("--out", required=True, help="Output JSON file")
    parser.add_argument("--mode", choices=["all", "turning"], default="all", help="Prediction mode")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    payload = load_json(input_path)
    prediction = request_github_prediction(args.index, payload, mode=args.mode)
    write_json(Path(args.out), prediction)
    print(f"Wrote prediction to {args.out}")


if __name__ == "__main__":
    main()
