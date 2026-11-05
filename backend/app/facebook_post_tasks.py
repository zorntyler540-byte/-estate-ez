"""Durable social publish queue: batches and jobs in Postgres, drained by the worker.

* The API validates, writes a ``social_publish_batches`` row plus one ``social_publish_jobs``
  row per post, and returns. ``GET /facebook/posts/batches/{id}`` reads the jobs back, so
  progress survives API restarts and works across replicas.
* The worker parent polls ``has_due_jobs`` (cheap; this module imports no pipeline code) and
  spawns ``python -m app.worker --social-publish``, which runs ``drain_publish_queue``.
  When catalog jobs run in this process (dev), ``kick`` drains on a daemon thread instead.
* Accounts drain concurrently; one account's jobs run one at a time (Page rate limits, and a
  dead token stops the rest). A per-account Postgres advisory lock makes that hold across
  worker replicas.
* Retries: a job whose Graph error is transient goes back to ``queued`` with backoff, at most
  ``JOB_MAX_ATTEMPTS`` times. Errors where the post may already exist are never retried.
  ``active_key`` (one live job per calendar slot) and the Push-calendar row claim keep a slot
  from posting twice; the batch ``idempotency_key`` makes a repeated POST return the same batch.
* A ``running`` job older than ``JOB_STALE`` belonged to a dead process: requeued when nothing
  was claimed, failed with a check-the-Page note when a post may be half-sent.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import SessionLocal
from app.models import Account, FacebookPagePost, SocialPublishBatch, SocialPublishJob, User

_LOG = logging.getLogger(__name__)
UTC = timezone.utc

JOB_MAX_ATTEMPTS = 3
JOB_RETRY_BASE = timedelta(seconds=30)
JOB_STALE = timedelta(minutes=15)
DRAIN_MAX_SECONDS = 10 * 60.0
DRAIN_ACCOUNT_CONCURRENCY = 3
WAITING_FOR_WORKER_AFTER = timedelta(seconds=90)
BATCH_RETENTION = timedelta(days=30)

LIVE_STATUSES = ("queued", "running", "waiting")
FINISHED_STATUSES = ("done", "failed", "skipped")
ALREADY_QUEUED = "This slot is already queued to post."
STALE_UNKNOWN = (
    "The publisher stopped while sending this post, so it may already be live. "
    "Check the Page before posting this slot again."
)
STALE_GAVE_UP = "The publisher stopped before this post was sent; gave up after retries."
RECONNECT_SKIP = "Not sent: reconnect the Facebook Page first."


def _start_thread(target: Callable[[], None], name: str) -> None:
    threading.Thread(target=target, daemon=True, name=name).start()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _dumps(value: Any) -> str:
    return json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))


def _loads(raw: str | None, default: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def slot_active_key(calendar_key: str | None) -> str | None:
    key = (calendar_key or "").strip()
    return f"slot:{key}" if key else None


def retry_backoff(attempts: int) -> timedelta:
    """30s, 2m, 8m ..."""
    return JOB_RETRY_BASE * (4 ** max(0, attempts - 1))


# --- enqueue -----------------------------------------------------------------------------


def enqueue_batch(
    db: Session,
    *,
    account_id: str,
    user_id: str,
    items: list[dict[str, Any]],
    include_follow_hook: bool,
    force: bool,
    source: str,
    skipped: Iterable[dict[str, Any]] = (),
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Persist a batch. Returns ``(batch_id, created)``; an idempotency key seen before returns
    the earlier batch with ``created=False`` and enqueues nothing."""
    moment = now or _utcnow()
    key = (idempotency_key or "").strip()[:120] or None
    if key:
        existing = (
            db.query(SocialPublishBatch.id)
            .filter(SocialPublishBatch.account_id == account_id, SocialPublishBatch.idempotency_key == key)
            .scalar()
        )
        if existing:
            return str(existing), False
    batch = SocialPublishBatch(
        account_id=account_id,
        user_id=user_id,
        source=(source or "lot")[:32],
        include_follow_hook=bool(include_follow_hook),
        force=bool(force),
        idempotency_key=key,
    )
    db.add(batch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(SocialPublishBatch.id)
            .filter(SocialPublishBatch.account_id == account_id, SocialPublishBatch.idempotency_key == key)
            .scalar()
        )
        return str(existing), False
    batch_id = batch.id
    position = 0

    def _finished(row: dict[str, Any], status: str = "skipped") -> SocialPublishJob:
        return SocialPublishJob(
            batch_id=batch_id,
            account_id=account_id,
            position=position,
            lot_id=str(row.get("lot_id") or "")[:36] or None,
            calendar_key=row.get("calendar_key"),
            publish_target=str(row.get("publish_target") or "facebook_page")[:32],
            status=status,
            result_json=_dumps(row),
            finished_at=moment,
        )

    for row in skipped:
        db.add(_finished(row))
        position += 1
    for item in items:
        calendar_key = str(item.get("calendar_key") or "").strip() or None
        job = SocialPublishJob(
            batch_id=batch_id,
            account_id=account_id,
            position=position,
            lot_id=str(item.get("lot_id") or "")[:36] or None,
            calendar_key=calendar_key,
            publish_target=str(item.get("publish_target") or "facebook_page")[:32],
            item_json=_dumps(item),
            status="queued",
            run_at=moment,
            active_key=slot_active_key(calendar_key),
        )
        position += 1
        if job.active_key is None:
            db.add(job)
            continue
        try:
            with db.begin_nested():
                db.add(job)
                db.flush()
        except IntegrityError:
            db.add(
                _finished(
                    {
                        "lot_id": job.lot_id or "",
                        "calendar_key": calendar_key,
                        "publish_target": job.publish_target,
                        "ok": False,
                        "skipped": True,
                        "error": ALREADY_QUEUED,
                    }
                )
            )
    db.commit()
    return batch_id, True


def live_slot_keys(db: Session, account_id: str) -> set[str]:
    """Calendar keys that already have a queued / running / waiting job."""
    rows = (
        db.query(SocialPublishJob.calendar_key)
        .filter(
            SocialPublishJob.account_id == account_id,
            SocialPublishJob.status.in_(LIVE_STATUSES),
            SocialPublishJob.calendar_key.isnot(None),
        )
        .all()
    )
    return {str(row[0]) for row in rows}


def cancel_slot_jobs(db: Session, account_id: str, calendar_key: str, *, reason: str) -> int:
    """Owner unapproved a slot: finish its live job so the slot can be approved again."""
    jobs = (
        db.query(SocialPublishJob)
        .filter(
            SocialPublishJob.account_id == account_id,
            SocialPublishJob.active_key == slot_active_key(calendar_key),
            SocialPublishJob.status.in_(("queued", "waiting")),
        )
        .all()
    )
    for job in jobs:
        job.status = "skipped"
        job.active_key = None
        job.finished_at = _utcnow()
        job.result_json = _dumps(
            {
                "lot_id": job.lot_id or "",
                "calendar_key": job.calendar_key,
                "publish_target": job.publish_target,
                "ok": False,
                "skipped": True,
                "error": reason,
            }
        )
    if jobs:
        db.commit()
    return len(jobs)


# --- claim / run -------------------------------------------------------------------------


def _account_lock(db: Session, account_id: str) -> None:
    if _is_postgres(db):
        from sqlalchemy import text

        key = zlib.crc32(f"social-publish:{account_id}".encode()) & 0x7FFFFFFF
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def claim_next_job(db: Session, account_id: str, *, now: datetime) -> str | None:
    """Claim this account's next due job, unless one is already running somewhere."""
    _account_lock(db, account_id)
    busy = (
        db.query(SocialPublishJob.id)
        .filter(
            SocialPublishJob.account_id == account_id,
            SocialPublishJob.status == "running",
            SocialPublishJob.claimed_at > now - JOB_STALE,
        )
        .first()
    )
    if busy is not None:
        db.rollback()
        return None
    query = (
        db.query(SocialPublishJob.id, SocialPublishJob.status)
        .filter(
            SocialPublishJob.account_id == account_id,
            SocialPublishJob.status.in_(("queued", "waiting")),
            or_(SocialPublishJob.run_at.is_(None), SocialPublishJob.run_at <= now),
        )
        .order_by(SocialPublishJob.run_at.asc(), SocialPublishJob.created_at.asc(), SocialPublishJob.position.asc())
    )
    if _is_postgres(db):
        query = query.with_for_update(skip_locked=True)
    row = query.first()
    if row is None:
        db.rollback()
        return None
    job_id, status = row
    won = (
        db.query(SocialPublishJob)
        .filter(SocialPublishJob.id == job_id, SocialPublishJob.status == status)
        .update(
            {"status": "running", "claimed_at": now, "attempts": SocialPublishJob.attempts + 1},
            synchronize_session=False,
        )
    )
    if won != 1:
        db.rollback()
        return None
    db.commit()
    return str(job_id)


def _load_item(raw: str) -> dict[str, Any]:
    item = _loads(raw, {})
    when = item.get("scheduled_publish_time")
    if isinstance(when, str) and when.strip():
        item["scheduled_publish_time"] = datetime.fromisoformat(when)
    return item


def run_job(db: Session, job_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Publish one claimed job and record the outcome. Never raises."""
    from app.services.facebook_page import _page_token, publish_item

    moment = now or _utcnow()
    job = db.get(SocialPublishJob, job_id)
    batch = db.get(SocialPublishBatch, job.batch_id) if job else None
    if job is None or batch is None:
        return {}
    item = _load_item(job.item_json)
    state = _loads(job.state_json, {})
    base = {"lot_id": job.lot_id or "", "calendar_key": job.calendar_key, "publish_target": job.publish_target}

    def persist(current: dict[str, Any]) -> None:
        db.query(SocialPublishJob).filter(SocialPublishJob.id == job_id).update(
            {"state_json": _dumps(current)}, synchronize_session=False
        )
        db.commit()

    def on_claimed(record_id: str) -> None:
        db.query(SocialPublishJob).filter(SocialPublishJob.id == job_id).update(
            {"post_record_id": record_id}, synchronize_session=False
        )
        db.commit()

    account = db.query(Account).filter(Account.id == job.account_id).first()
    user_id = batch.user_id
    include_follow_hook = bool(batch.include_follow_hook)
    force = bool(batch.force)
    source = batch.source or "lot"
    try:
        if account is None or db.get(User, user_id) is None:
            result = {**base, "ok": False, "error": "Account or user not found"}
        else:
            page_id, page_token = _page_token(account)
            result = publish_item(
                db,
                account=account,
                user_id=user_id,
                page_id=page_id,
                page_token=page_token,
                item=item,
                include_follow_hook=include_follow_hook,
                force=force,
                source=source,
                now=moment,
                state=state,
                persist_state=persist,
                on_claimed=on_claimed,
            )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        if detail == str(exc):
            _LOG.exception("Social publish job crashed job=%s", job_id)
        result = {**base, "ok": False, "error": str(detail)[:500]}
    _finish_job(db, job_id, result, state, now=moment)
    if result.get("needs_reconnect"):
        _skip_queued_for_reconnect(db, job.account_id, batch_id=job.batch_id, now=moment)
    return result


def _finish_job(db: Session, job_id: str, result: dict[str, Any], state: dict[str, Any], *, now: datetime) -> None:
    job = db.get(SocialPublishJob, job_id)
    if job is None:
        return
    public = {k: v for k, v in result.items() if k not in ("retryable", "deferred_until")}
    deferred = result.get("deferred_until")
    job.claimed_at = None
    if result.get("ok") and deferred is not None:
        state["phase"] = "reserved"
        job.status = "waiting"
        job.run_at = _aware(deferred)
        job.attempts = 0
        job.result_json = _dumps(public)
    elif not result.get("ok") and result.get("retryable") and int(job.attempts or 0) < JOB_MAX_ATTEMPTS:
        job.status = "queued"
        job.run_at = now + retry_backoff(int(job.attempts or 1))
        job.last_error = str(result.get("error") or "")[:2000]
    else:
        job.status = "done" if result.get("ok") else ("skipped" if result.get("skipped") else "failed")
        job.active_key = None
        job.finished_at = now
        job.result_json = _dumps(public)
        job.last_error = None if result.get("ok") else str(result.get("error") or "")[:2000]
    job.state_json = _dumps(state)
    db.add(job)
    db.commit()
    _maybe_finish_batch(db, job.batch_id, now=now)


def _maybe_finish_batch(db: Session, batch_id: str, *, now: datetime) -> None:
    pending = (
        db.query(SocialPublishJob.id)
        .filter(SocialPublishJob.batch_id == batch_id, SocialPublishJob.status.in_(("queued", "running")))
        .first()
    )
    if pending is None:
        db.query(SocialPublishBatch).filter(
            SocialPublishBatch.id == batch_id, SocialPublishBatch.finished_at.is_(None)
        ).update({"finished_at": now}, synchronize_session=False)
        db.commit()


def _skip_queued_for_reconnect(db: Session, account_id: str, *, batch_id: str, now: datetime) -> None:
    jobs = (
        db.query(SocialPublishJob)
        .filter(
            SocialPublishJob.account_id == account_id,
            SocialPublishJob.batch_id == batch_id,
            SocialPublishJob.status == "queued",
        )
        .all()
    )
    for job in jobs:
        job.status = "skipped"
        job.active_key = None
        job.finished_at = now
        job.result_json = _dumps(
            {
                "lot_id": job.lot_id or "",
                "calendar_key": job.calendar_key,
                "publish_target": job.publish_target,
                "ok": False,
                "skipped": True,
                "needs_reconnect": True,
                "error": RECONNECT_SKIP,
            }
        )
    if jobs:
        db.commit()
        _maybe_finish_batch(db, batch_id, now=now)


def recover_stale_jobs(db: Session, *, now: datetime | None = None) -> int:
    """Jobs left ``running`` by a dead process: requeue, or fail safe if a post may be half-sent."""
    from app.services.facebook_calendar import AUTO_APPROVE_MAX_ATTEMPTS
    from app.services.social_targets import PUBLISH_TARGET_INSTAGRAM_FEED

    moment = now or _utcnow()
    stale = (
        db.query(SocialPublishJob)
        .filter(SocialPublishJob.status == "running", SocialPublishJob.claimed_at <= moment - JOB_STALE)
        .all()
    )
    for job in stale:
        record = db.get(FacebookPagePost, job.post_record_id) if job.post_record_id else None
        state = _loads(job.state_json, {})
        half_sent = record is not None and record.status == "publishing"
        # Instagram keeps its container id, so a retry asks Graph instead of guessing.
        resumable = job.publish_target == PUBLISH_TARGET_INSTAGRAM_FEED and bool(
            (state.get("instagram") or {}).get("creation_id")
        )
        if half_sent and resumable and int(job.attempts or 0) < JOB_MAX_ATTEMPTS:
            record.status = "failed"
            record.claimed_at = None
            job.status = "queued"
            job.run_at = moment
            job.claimed_at = None
        elif half_sent:
            record.status = "failed"
            record.claimed_at = None
            record.error_message = STALE_UNKNOWN
            record.review_note = STALE_UNKNOWN
            record.attempt_count = max(int(record.attempt_count or 0), AUTO_APPROVE_MAX_ATTEMPTS)
            _close(job, ok=False, error=STALE_UNKNOWN, now=moment)
        elif int(job.attempts or 0) < JOB_MAX_ATTEMPTS:
            job.status = "queued"
            job.run_at = moment
            job.claimed_at = None
        else:
            _close(job, ok=False, error=STALE_GAVE_UP, now=moment)
    if stale:
        db.commit()
        for batch_id in {job.batch_id for job in stale}:
            _maybe_finish_batch(db, batch_id, now=moment)
    return len(stale)


def _close(job: SocialPublishJob, *, ok: bool, error: str, now: datetime) -> None:
    job.status = "done" if ok else "failed"
    job.active_key = None
    job.claimed_at = None
    job.finished_at = now
    job.last_error = error
    job.result_json = _dumps(
        {
            "lot_id": job.lot_id or "",
            "calendar_key": job.calendar_key,
            "publish_target": job.publish_target,
            "ok": ok,
            "post_id": job.post_record_id,
            "error": error,
        }
    )


# --- drain -------------------------------------------------------------------------------


def has_due_jobs(db: Session, *, now: datetime | None = None) -> bool:
    moment = now or _utcnow()
    due = (
        db.query(SocialPublishJob.id)
        .filter(
            or_(
                (SocialPublishJob.status.in_(("queued", "waiting")))
                & or_(SocialPublishJob.run_at.is_(None), SocialPublishJob.run_at <= moment),
                (SocialPublishJob.status == "running") & (SocialPublishJob.claimed_at <= moment - JOB_STALE),
            )
        )
        .first()
    )
    return due is not None


def due_account_ids(db: Session, *, now: datetime, limit: int = 100) -> list[str]:
    rows = (
        db.query(SocialPublishJob.account_id)
        .filter(
            SocialPublishJob.status.in_(("queued", "waiting")),
            or_(SocialPublishJob.run_at.is_(None), SocialPublishJob.run_at <= now),
        )
        .distinct()
        .limit(limit)
        .all()
    )
    return [str(row[0]) for row in rows]


def drain_account(
    factory: Callable[[], Session],
    account_id: str,
    *,
    now_fn: Callable[[], datetime] = _utcnow,
    deadline: float | None = None,
) -> int:
    """Run this account's due jobs one at a time. Returns jobs run."""
    ran = 0
    while deadline is None or time.monotonic() < deadline:
        with factory() as db:
            job_id = claim_next_job(db, account_id, now=now_fn())
            if job_id is None:
                break
            run_job(db, job_id, now=now_fn())
        ran += 1
    return ran


def drain_publish_queue(
    factory: Callable[[], Session] | None = None,
    *,
    max_seconds: float = DRAIN_MAX_SECONDS,
    concurrency: int = DRAIN_ACCOUNT_CONCURRENCY,
    now_fn: Callable[[], datetime] = _utcnow,
) -> int:
    """Drain every account's due jobs, accounts in parallel. Returns jobs run."""
    make = factory or SessionLocal
    deadline = time.monotonic() + max_seconds
    with make() as db:
        try:
            recover_stale_jobs(db, now=now_fn())
        except Exception:  # noqa: BLE001
            db.rollback()
            _LOG.exception("stale social publish job recovery failed")
    total = 0
    while time.monotonic() < deadline:
        with make() as db:
            accounts = due_account_ids(db, now=now_fn())
        if not accounts:
            break
        if len(accounts) == 1 or concurrency <= 1:
            ran = sum(drain_account(make, acc, now_fn=now_fn, deadline=deadline) for acc in accounts)
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(accounts)), thread_name_prefix="social-pub") as pool:
                ran = sum(pool.map(lambda acc: drain_account(make, acc, now_fn=now_fn, deadline=deadline), accounts))
        total += ran
        if ran == 0:
            break
    return total


_kick_lock = threading.Lock()
_draining: set[str] = set()
_again: set[str] = set()


def _drains_in_this_process() -> bool:
    """Dev / single-process API drains on a thread; the worker and its children never do."""
    return bool(settings.run_catalog_jobs_in_process) and os.environ.get("WORKER_POLL_LOOP") != "1"


def kick(bind: Engine | None, account_id: str, *, now: datetime | None = None) -> None:
    """Start draining ``account_id`` in this process when no worker does it for us."""
    if not _drains_in_this_process():
        return
    with _kick_lock:
        if account_id in _draining:
            _again.add(account_id)
            return
        _draining.add(account_id)
    factory = sessionmaker(bind=bind, autoflush=False, autocommit=False, future=True) if bind else SessionLocal
    now_fn = (lambda: now) if now is not None else _utcnow

    def _runner() -> None:
        try:
            while True:
                try:
                    with factory() as db:
                        recover_stale_jobs(db, now=now_fn())
                    drain_account(factory, account_id, now_fn=now_fn, deadline=time.monotonic() + DRAIN_MAX_SECONDS)
                except Exception:  # noqa: BLE001
                    _LOG.exception("In-process social publish drain failed account=%s", account_id)
                with _kick_lock:
                    if account_id not in _again:
                        _draining.discard(account_id)
                        return
                    _again.discard(account_id)
        finally:
            with _kick_lock:
                _draining.discard(account_id)

    _start_thread(_runner, f"social-publish-{account_id[:8]}")


def reset_facebook_post_runtime_state() -> None:
    with _kick_lock:
        _draining.clear()
        _again.clear()


# --- status ------------------------------------------------------------------------------


def facebook_post_batch_status(
    db: Session, batch_id: str, account_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    batch = (
        db.query(SocialPublishBatch)
        .filter(SocialPublishBatch.id == batch_id, SocialPublishBatch.account_id == account_id)
        .first()
    )
    if batch is None:
        return None
    jobs = (
        db.query(SocialPublishJob)
        .filter(SocialPublishJob.batch_id == batch_id)
        .order_by(SocialPublishJob.position.asc())
        .all()
    )
    moment = now or _utcnow()
    results: list[dict[str, Any]] = []
    posted = failed = retrying = 0
    active = False
    running_lot = None
    oldest_queued: datetime | None = None
    for job in jobs:
        if job.status in ("queued", "running"):
            active = True
            if job.status == "running":
                running_lot = job.lot_id
            elif int(job.attempts or 0) > 0:
                retrying += 1
            else:
                created = _aware(job.created_at)
                if created is not None and (oldest_queued is None or created < oldest_queued):
                    oldest_queued = created
            continue
        row = _loads(job.result_json, {})
        if not row:
            continue
        results.append(row)
        if row.get("ok"):
            posted += 1
        elif job.status == "failed":
            failed += 1
    waiting_for_worker = bool(
        active and running_lot is None and oldest_queued is not None and moment - oldest_queued > WAITING_FOR_WORKER_AFTER
    )
    return {
        "queued": True,
        "batch_id": batch_id,
        "active": active,
        "total": len(jobs),
        "done": len(results),
        "posted": posted,
        "failed": failed,
        "retrying": retrying,
        "waiting_for_worker": waiting_for_worker,
        "current_lot_id": running_lot,
        "results": results,
    }


def purge_old_batches(db: Session, *, now: datetime | None = None) -> int:
    cutoff = (now or _utcnow()) - BATCH_RETENTION
    old = [
        str(row[0])
        for row in db.query(SocialPublishBatch.id)
        .filter(SocialPublishBatch.finished_at.isnot(None), SocialPublishBatch.finished_at < cutoff)
        .limit(500)
        .all()
    ]
    if not old:
        return 0
    live = {
        str(row[0])
        for row in db.query(SocialPublishJob.batch_id)
        .filter(SocialPublishJob.batch_id.in_(old), SocialPublishJob.status.in_(LIVE_STATUSES))
        .distinct()
        .all()
    }
    doomed = [bid for bid in old if bid not in live]
    if doomed:
        db.query(SocialPublishJob).filter(SocialPublishJob.batch_id.in_(doomed)).delete(synchronize_session=False)
        db.query(SocialPublishBatch).filter(SocialPublishBatch.id.in_(doomed)).delete(synchronize_session=False)
        db.commit()
    return len(doomed)
