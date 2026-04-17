from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from app.config import settings

TRANSIENT_HTTP = frozenset({429, 502, 503, 504})


def _retry_after_seconds(response: requests.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return min(fallback, 180)
    try:
        return min(180, float(raw))
    except ValueError:
        return min(fallback, 180)


def _mime_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "image/jpeg"


def _parse_model_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _coerce_photo_dict(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return None


def _model_url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/"
        f"{settings.gemini_api_version}/models/{settings.gemini_model}:generateContent"
    )


def _generate_content(payload: dict[str, Any], timeout: int = 120) -> str | None:
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.google_api_key}
    backoff = 8.0
    for _ in range(12):
        try:
            response = requests.post(_model_url(), headers=headers, json=payload, timeout=timeout)
            if response.status_code in TRANSIENT_HTTP:
                wait = _retry_after_seconds(response, backoff)
                time.sleep(wait)
                backoff = min(backoff * 2, 180) if response.status_code == 429 else min(
                    max(backoff, 4.0) * 1.5, 90.0
                )
                continue
            response.raise_for_status()
            body = response.json()
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (requests.RequestException, KeyError, IndexError, ValueError):
            return None
    return None


def analyze_photo(photo_path: Path) -> dict[str, Any] | None:
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are an expert estate liquidator reviewing one photo for a sale catalog.\n"
        "Rules:\n"
        "- START A NEW LOT ONLY when BOTH are true: purple/lavender estate tape is visible and "
        "a clear lot number is written ON that tape. Set SKU to digits only.\n"
        "- If tape-number is missing/uncertain, set SKU to null.\n"
        "- Extract Brands and LabelsAndTags (sizes/fits only).\n"
        "- Return AuctionTitle, ProfessionalDescription, SpecificationLines, Brands, LabelsAndTags, "
        "Condition, StartingPrice.\n"
        "- ProfessionalDescription should include visible brands with **bold** at first mention.\n"
        "Return JSON only."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": _mime_for_path(photo_path), "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _generate_content(payload)
    if text is None:
        return None
    try:
        parsed = _parse_model_json(text)
    except json.JSONDecodeError:
        return None
    return _coerce_photo_dict(parsed)


def verify_tape_sku(photo_path: Path, claimed_digits: str) -> bool | None:
    with open(photo_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    prompt = (
        "Strict visual audit. Return JSON keys new_lot_from_tape (boolean) and lot_digits_on_tape "
        "(string or null). True only if purple/lavender painter's tape has readable digits on tape. "
        f"Claimed lot digits are {claimed_digits!r}. Respond JSON only."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": _mime_for_path(photo_path), "data": img_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    text = _generate_content(payload, timeout=90)
    if text is None:
        return None
    try:
        obj = _parse_model_json(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    flag = obj.get("new_lot_from_tape")
    if isinstance(flag, str):
        flag = flag.lower().strip() in ("true", "1", "yes")
    if flag is not True:
        return False
    tape_digits = re.sub(r"\D", "", str(obj.get("lot_digits_on_tape") or ""))
    return bool(tape_digits) and int(tape_digits) == int(claimed_digits)

