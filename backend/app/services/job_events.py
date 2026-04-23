"""In-process pub/sub for job SSE (same server process as the API worker thread).

Late subscribers (e.g. EventSource opens after ``POST /run``) still receive recent payloads
via a bounded in-memory replay deque per ``(user_id, job_id)``.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from typing import Any

_lock = threading.Lock()
# (owner_user_id, job_id) -> subscriber queues (str JSON payloads)
_subscribers: dict[tuple[str, str], list[queue.Queue[str]]] = {}
# Last N JSON lines per job so a client that connects mid-run can rebuild UI state.
_replay: dict[tuple[str, str], deque[str]] = {}
_MAX_REPLAY = 150


def subscribe(user_id: str, job_id: str) -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue(maxsize=200)
    key = (user_id, job_id)
    with _lock:
        _subscribers.setdefault(key, []).append(q)
        replay = list(_replay.get(key, ()))
    for line in replay:
        try:
            q.put_nowait(line)
        except queue.Full:
            break
    return q


def unsubscribe(user_id: str, job_id: str, q: queue.Queue[str]) -> None:
    key = (user_id, job_id)
    with _lock:
        subs = _subscribers.get(key)
        if not subs:
            return
        if q in subs:
            subs.remove(q)
        if not subs:
            del _subscribers[key]


def publish(user_id: str, job_id: str, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    key = (user_id, job_id)
    with _lock:
        dq = _replay.setdefault(key, deque(maxlen=_MAX_REPLAY))
        dq.append(line)
        subs = list(_subscribers.get(key, ()))
    for sub_q in subs:
        try:
            sub_q.put_nowait(line)
        except queue.Full:
            continue
