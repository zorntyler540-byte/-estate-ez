from __future__ import annotations

import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from app.config import settings


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SUGGESTION_FALLBACK_PATH = _BACKEND_DIR / "runtime" / "suggestions.jsonl"


def smtp_configured() -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and (settings.smtp_user or "").strip()
        and (settings.smtp_password or "").strip()
    )


def send_suggestion_email(*, subject: str, body: str) -> None:
    """Send plain-text email via SMTP (TLS on port 587, or SSL wrapper on 465)."""
    if not smtp_configured():
        raise ValueError("smtp_not_configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email or settings.smtp_user
    msg["To"] = settings.feedback_to_email
    msg.set_content(body, charset="utf-8")

    host = settings.smtp_host.strip()
    port = int(settings.smtp_port)
    user = settings.smtp_user.strip()
    password = settings.smtp_password

    timeout = 25
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)


def save_suggestion_local(record: dict[str, str]) -> Path:
    """
    Fallback sink when SMTP is unavailable.
    Appends one JSON object per line for easy review/audit.
    """
    _SUGGESTION_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with _SUGGESTION_FALLBACK_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return _SUGGESTION_FALLBACK_PATH


def read_recent_saved_suggestions(limit: int = 50) -> list[dict[str, str]]:
    """Read most recent locally-saved suggestions from JSONL fallback store."""
    n = max(1, min(int(limit), 200))
    if not _SUGGESTION_FALLBACK_PATH.is_file():
        return []
    rows: list[dict[str, str]] = []
    with _SUGGESTION_FALLBACK_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append({str(k): str(v) for k, v in obj.items() if v is not None})
    rows.reverse()
    return rows[:n]
