"""POST /jobs/retry-failed re-queues Gemini/Cancelled failures, not lost-upload leftovers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app
from app.models import JobStatus, ProcessingJob
from app import job_tasks


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)
    monkeypatch.setattr(job_tasks, "kick_queue_if_idle", lambda *_a, **_k: None)
    monkeypatch.setattr(job_tasks, "schedule_worker_claim_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(job_tasks, "start_job_in_background", lambda *_a, **_k: False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), Session


def _clear() -> None:
    app.dependency_overrides.pop(get_db, None)


def _register(client: TestClient, email: str) -> tuple[str, str, str]:
    res = client.post(
        "/auth/register",
        json={"account_name": "Estate Team", "email": email, "password": "password1"},
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["account_id"], me["id"]


def _add_failed_job(
    db,
    *,
    account_id: str,
    sale_id: str,
    job_id: str,
    error: str,
    user_id: str = "user-1",
) -> ProcessingJob:
    when = datetime.now(timezone.utc) - timedelta(minutes=8)
    row = ProcessingJob(
        id=job_id,
        account_id=account_id,
        user_id=user_id,
        sale_id=sale_id,
        status=JobStatus.failed,
        input_folder="/tmp/retry-batch",
        single_lot_mode=True,
        error_message=error,
        created_at=when,
        updated_at=when,
    )
    db.add(row)
    db.commit()
    return row


def test_retry_failed_requeues_gemini_skips_lost_upload(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, account_id, user_id = _register(client, "retry@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        db = Session()
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_id,
            job_id="fail-ai",
            error="Gemini 500: overloaded",
            user_id=user_id,
        )
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_id,
            job_id="fail-cancel",
            error="Cancelled",
            user_id=user_id,
        )
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_id,
            job_id="fail-disk",
            error="Input folder not found (upload disk was lost — re-sync from the phone)",
            user_id=user_id,
        )
        db.close()

        res = client.post(
            f"/jobs/retry-failed?sale_id={sale_id}&force_in_process=false",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["retried"] == 2
        assert body["skipped"] == 1
        assert body["started"] is False
        retried_ids = {j["id"] for j in body["jobs"]}
        assert retried_ids == {"fail-ai", "fail-cancel"}
        assert all(j["status"] == "queued" for j in body["jobs"])
        assert all(not j.get("error_message") for j in body["jobs"])

        db = Session()
        assert db.get(ProcessingJob, "fail-ai").status == JobStatus.queued
        assert db.get(ProcessingJob, "fail-cancel").status == JobStatus.queued
        leftover = db.get(ProcessingJob, "fail-disk")
        assert leftover.status == JobStatus.failed
        assert "upload disk was lost" in (leftover.error_message or "").lower()
        db.close()
    finally:
        _clear()


def test_retry_failed_starts_first_when_forced(tmp_path, monkeypatch) -> None:
    started: list[str] = []

    def _start(job_id: str, account_id: str, *, force_in_process: bool = False) -> bool:
        started.append(job_id)
        assert force_in_process is True
        return True

    client, Session = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(job_tasks, "start_job_in_background", _start)
    try:
        token, account_id, user_id = _register(client, "retry-force@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        db = Session()
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_id,
            job_id="fail-first",
            error="Gemini 429",
            user_id=user_id,
        )
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_id,
            job_id="fail-second",
            error="Gemini 500",
            user_id=user_id,
        )
        db.close()

        res = client.post(
            f"/jobs/retry-failed?sale_id={sale_id}&force_in_process=true",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["retried"] == 2
        assert body["started"] is True
        assert started == ["fail-first"]
        assert body["jobs"][0]["id"] == "fail-first"
        assert body["jobs"][0]["status"] == "processing"
        assert body["jobs"][1]["status"] == "queued"
    finally:
        _clear()


def test_retry_failed_is_sale_scoped(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, account_id, user_id = _register(client, "retry-scope@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sales = client.get("/sales", headers=headers).json()
        sale_a = sales[0]["id"]
        other = client.post("/sales", headers=headers, json={"name": "Other sale"})
        assert other.status_code == 201, other.text
        sale_b = other.json()["id"]
        db = Session()
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_a,
            job_id="fail-a",
            error="Gemini 500",
            user_id=user_id,
        )
        _add_failed_job(
            db,
            account_id=account_id,
            sale_id=sale_b,
            job_id="fail-b",
            error="Gemini 500",
            user_id=user_id,
        )
        db.close()

        res = client.post(
            f"/jobs/retry-failed?sale_id={sale_a}&force_in_process=false",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["retried"] == 1
        assert body["jobs"][0]["id"] == "fail-a"
        db = Session()
        assert db.get(ProcessingJob, "fail-a").status == JobStatus.queued
        assert db.get(ProcessingJob, "fail-b").status == JobStatus.failed
        db.close()
    finally:
        _clear()
