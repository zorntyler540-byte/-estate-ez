"""Pub/sub for job SSE.

In-process subscribers still work for local/dev (API runs Gemini itself). Production runs Gemini
on a dedicated worker, so every payload is also persisted to Postgres: the API SSE route replays
and polls that table. Late dashboard subscribers rebuild UI state from the durable log.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import deque
from typing import Any

from app.db import SessionLocal
from app.models import JobSseEvent, ProcessingJob

_LOG = logging.getLogger(__name__)

_lock = threading.Lock()
# (account_id, job_id) -> subscriber queues (str JSON payloads)
_subscribers: dict[tuple[str, str], list[queue.Queue[str]]] = {}
# Last N JSON lines per job so a client that connects mid-run can rebuild UI state (same process).
_replay: dict[tuple[str, str], deque[str]] = {}
_MAX_REPLAY = 300


def snapshot_updates_from_payload(payload: dict[str, Any], *, current_done: int = 0) -> dict[str, Any]:
    """Map an SSE payload to ProcessingJob progress columns (empty dict = no snapshot change)."""
    kind = payload.get("type")
    if kind == "batch_start":
        total = int(payload.get("total_photos") or 0)
        phase = payload.get("phase")
        return {
            "progress_total": total,
            "progress_done": 0,
            "progress_phase": "pipeline_triage" if phase == "pipeline" else (phase or "pipeline_triage"),
            "progress_file_name": None,
        }
    if kind == "photo_analyzed":
        total = payload.get("total")
        done = current_done + 1
        if total is not None:
            done = min(done, int(total))
        out: dict[str, Any] = {
            "progress_done": max(done, 0),
            "progress_file_name": payload.get("file_name") or None,
        }
        if total is not None:
            out["progress_total"] = int(total)
        return out
    if kind == "batch_phase" and isinstance(payload.get("phase"), str):
        return {"progress_phase": payload["phase"]}
    if kind == "inventory_step":
        idx = int(payload.get("index") or 0)
        total = payload.get("total")
        out = {
            "progress_phase": "inventory",
            "progress_done": idx + 1,
            "progress_file_name": payload.get("file_name") or None,
        }
        if total is not None:
            out["progress_total"] = int(total)
        return out
    if kind == "status" and payload.get("status") in ("completed", "failed"):
        return {"progress_phase": str(payload.get("status"))}
    return {}


def subscribe(account_id: str, job_id: str) -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue(maxsize=200)
    key = (account_id, job_id)
    with _lock:
        _subscribers.setdefault(key, []).append(q)
        replay = list(_replay.get(key, ()))
    for line in replay:
        try:
            q.put_nowait(line)
        except queue.Full:
            break
    return q


def unsubscribe(account_id: str, job_id: str, q: queue.Queue[str]) -> None:
    key = (account_id, job_id)
    with _lock:
        subs = _subscribers.get(key)
        if not subs:
            return
        if q in subs:
            subs.remove(q)
        if not subs:
            del _subscribers[key]


def list_events_after(account_id: str, job_id: str, after_id: int, *, limit: int = 400) -> list[tuple[int, str]]:
    """Return ``(id, payload_json)`` rows for this job with ``id > after_id``, oldest first."""
    with SessionLocal() as db:
        rows = (
            db.query(JobSseEvent)
            .filter(
                JobSseEvent.account_id == account_id,
                JobSseEvent.job_id == job_id,
                JobSseEvent.id > after_id,
            )
            .order_by(JobSseEvent.id.asc())
            .limit(limit)
            .all()
        )
        return [(int(r.id), r.payload_json) for r in rows]


def _persist(account_id: str, job_id: str, line: str, payload: dict[str, Any]) -> None:
    with SessionLocal() as db:
        db.add(JobSseEvent(account_id=account_id, job_id=job_id, payload_json=line))
        row = db.query(ProcessingJob).filter(ProcessingJob.id == job_id, ProcessingJob.account_id == account_id).first()
        if row is not None:
            fields = snapshot_updates_from_payload(payload, current_done=int(row.progress_done or 0))
            for key, value in fields.items():
                setattr(row, key, value)
        db.commit()


def publish(account_id: str, job_id: str, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    key = (account_id, job_id)
    with _lock:
        dq = _replay.setdefault(key, deque(maxlen=_MAX_REPLAY))
        dq.append(line)
        subs = list(_subscribers.get(key, ()))
    try:
        _persist(account_id, job_id, line, payload)
    except Exception:
        _LOG.exception("Failed to persist job SSE event job_id=%s", job_id)
    for sub_q in subs:
        try:
            sub_q.put_nowait(line)
        except queue.Full:
            continue
