"""Worker heartbeat: API in-process fallback only when no worker is alive (busy is alive)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app import job_tasks, worker_heartbeat
from app.db import Base, get_db
from app.main import app
from app.models import JobStatus, ProcessingJob, WorkerHeartbeat


def _sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'beat.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(worker_heartbeat, "SessionLocal", Session)
    worker_heartbeat.reset_heartbeat_cache()
    return Session


def test_no_heartbeat_means_worker_down(tmp_path, monkeypatch) -> None:
    _sessions(tmp_path, monkeypatch)
    assert worker_heartbeat.worker_heartbeat_fresh(use_cache=False) is False


def test_busy_worker_heartbeat_is_fresh(tmp_path, monkeypatch) -> None:
    Session = _sessions(tmp_path, monkeypatch)
    writer = worker_heartbeat.HeartbeatWriter(slots=3, instance_id="worker-a:1")
    assert writer.maybe_beat(3, now_mono=100.0) is True
    assert writer.maybe_beat(3, now_mono=101.0) is False
    with Session() as db:
        row = db.get(WorkerHeartbeat, "worker-a:1")
        assert row.slots == 3 and row.slots_in_use == 3
    assert worker_heartbeat.worker_heartbeat_fresh(use_cache=False) is True


def test_old_heartbeat_is_stale_and_pruned(tmp_path, monkeypatch) -> None:
    Session = _sessions(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    worker_heartbeat.write_heartbeat(
        instance_id="old:1",
        started_at=now - timedelta(days=3),
        slots=3,
        slots_in_use=0,
        now=now - timedelta(days=2),
    )
    later = now - timedelta(minutes=10)
    worker_heartbeat.write_heartbeat(
        instance_id="dead:2", started_at=later, slots=3, slots_in_use=1, now=later
    )
    with Session() as db:
        assert db.get(WorkerHeartbeat, "old:1") is None
    assert worker_heartbeat.worker_heartbeat_fresh(now=now, use_cache=False) is False


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)
    monkeypatch.setattr(job_tasks, "kick_queue_if_idle", lambda *_a, **_k: None)
    monkeypatch.setattr(job_tasks, "schedule_worker_claim_fallback", lambda *_a, **_k: None)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), Session


def test_retry_failed_enqueues_to_live_worker_even_when_forced(tmp_path, monkeypatch) -> None:
    """P0: Retry failed must not start Gemini on the API while the worker is alive."""
    client, Session = _client(tmp_path, monkeypatch)
    started: list[str] = []
    monkeypatch.setattr(job_tasks, "catalog_jobs_run_in_process", lambda: False)
    monkeypatch.setattr(job_tasks, "worker_heartbeat_is_fresh", lambda: True)
    monkeypatch.setattr(
        job_tasks, "start_job_in_background", lambda job_id, *_a, **_k: started.append(job_id) or True
    )
    try:
        res = client.post(
            "/auth/register",
            json={"account_name": "Estate Team", "email": "beat@example.com", "password": "password1"},
        )
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/auth/me", headers=headers).json()
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        with Session() as db:
            db.add(
                ProcessingJob(
                    id="fail-1",
                    account_id=me["account_id"],
                    user_id=me["id"],
                    sale_id=sale_id,
                    status=JobStatus.failed,
                    input_folder="/tmp/x",
                    single_lot_mode=True,
                    error_message="Gemini 500",
                )
            )
            db.commit()
        for query in ("", "&force_in_process=true"):
            res = client.post(f"/jobs/retry-failed?sale_id={sale_id}{query}", headers=headers)
            assert res.status_code == 200, res.text
            assert res.json()["started"] is False
            with Session() as db:
                row = db.get(ProcessingJob, "fail-1")
                assert row.status == JobStatus.queued
                row.status = JobStatus.failed
                db.commit()
        assert started == []
    finally:
        app.dependency_overrides.pop(get_db, None)
