"""client_draft_id round-trip, cataloged-draft matching, and upload-health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app
from app.models import JobStatus, Lot, Photo, ProcessingJob
from app.services.mobile_drafts import cataloged_mobile_draft_ids, cataloged_mobile_draft_lots
from app import job_tasks


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (180, 40, 20)).save(buf, format="JPEG")
    return buf.getvalue()


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'draftid.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)

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


def _register(client: TestClient, email: str) -> tuple[str, str]:
    res = client.post(
        "/auth/register",
        json={"account_name": "Estate Team", "email": email, "password": "password1"},
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["account_id"]


def test_upload_stores_client_draft_id(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "draft@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        draft_id = "1c86c230-a1c1-4433-b64e-5c2957e79027"
        res = client.post(
            "/jobs/upload",
            headers=headers,
            data={
                "sale_id": sale_id,
                "single_lot_mode": "true",
                "client_draft_id": draft_id,
                "single_lot_manual_sku": "LV101",
            },
            files=[("files", ("mobile-" + draft_id + "-01.jpg", _jpeg(), "image/jpeg"))],
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["client_draft_id"] == draft_id
        db = Session()
        job = db.get(ProcessingJob, body["id"])
        assert job is not None
        assert job.client_draft_id == draft_id
        db.close()
    finally:
        _clear()


def test_upload_without_client_draft_id_stays_null(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "oldapp@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        res = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=[("files", ("p1.jpg", _jpeg(), "image/jpeg"))],
        )
        assert res.status_code == 200, res.text
        assert res.json().get("client_draft_id") in (None, "")
    finally:
        _clear()


def test_upload_files_fills_missing_client_draft_id(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "append@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        created = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=[("files", ("p1.jpg", _jpeg(), "image/jpeg"))],
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        draft_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        appended = client.post(
            f"/jobs/{job_id}/upload-files",
            headers=headers,
            data={"client_draft_id": draft_id},
            files=[("files", ("p2.jpg", _jpeg(), "image/jpeg"))],
        )
        assert appended.status_code == 200, appended.text
        assert appended.json()["client_draft_id"] == draft_id
    finally:
        _clear()


def test_cataloged_drafts_prefer_client_draft_id(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'match.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Lamp",
            sku="LV101",
            photo_count=1,
        )
    )
    db.add(
        Photo(
            id="p1",
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-1",
            original_name="hero.jpg",
            storage_path="/catalog/hero.jpg",
            sort_order=0,
        )
    )
    when = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.add(
        ProcessingJob(
            id="job-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            status=JobStatus.completed,
            input_folder="/tmp/batch",
            single_lot_mode=True,
            single_lot_manual_sku="LV101",
            client_draft_id=draft,
            created_at=when,
            updated_at=when,
        )
    )
    db.commit()

    ids = cataloged_mobile_draft_ids(db, "acct-1", "sale-1")
    assert ids == [draft]
    lots = cataloged_mobile_draft_lots(db, "acct-1", "sale-1")
    assert len(lots) == 1
    assert lots[0].draft_id == draft
    assert lots[0].lot_id == "lot-1"
    assert lots[0].sku == "LV101"


def test_cataloged_drafts_filename_fallback_without_client_draft_id(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fallback.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            auction_title="Lamp",
            sku="LV101",
            photo_count=1,
        )
    )
    db.add(
        Photo(
            id="p1",
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-1",
            original_name=f"mobile-{draft}-01.jpg",
            storage_path="/catalog/a1.jpg",
            sort_order=0,
        )
    )
    when = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.add(
        ProcessingJob(
            id="job-old",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            status=JobStatus.completed,
            input_folder="/tmp/batch",
            created_at=when,
            updated_at=when,
        )
    )
    db.commit()
    assert cataloged_mobile_draft_ids(db, "acct-1", "sale-1") == [draft]


def test_job_outcome_matches_client_draft_id_without_folder(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'heal.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    draft = "1c86c230-a1c1-4433-b64e-5c2957e79027"
    db.add(
        Lot(
            id="lot-1",
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            sku="OTHER",
            auction_title="Lamp",
        )
    )
    db.add(
        Photo(
            id="p1",
            account_id="acct-1",
            user_id="user-1",
            lot_id="lot-1",
            original_name=f"mobile-{draft}-01.jpg",
            storage_path="/catalog/a1.jpg",
            sort_order=0,
        )
    )
    leftover = ProcessingJob(
        id="recv-1",
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.receiving,
        input_folder="/tmp/does-not-exist",
        client_draft_id=draft,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(leftover)
    db.commit()
    assert job_tasks.job_outcome_already_on_sale(db, leftover) is True


def test_upload_health_counts_and_draft_jobs(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "health@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        draft_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        up = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true", "client_draft_id": draft_id},
            files=[("files", ("p1.jpg", _jpeg(), "image/jpeg"))],
        )
        assert up.status_code == 200, up.text
        health = client.get(f"/sales/{sale_id}/upload-health", headers=headers)
        assert health.status_code == 200, health.text
        body = health.json()
        assert body["sale_id"] == sale_id
        assert body["counts"]["receiving"] >= 1
        assert body["stuck_count"] == 0
        assert any(j["client_draft_id"] == draft_id for j in body["jobs_with_client_draft_id"])

        db = Session()
        job = db.get(ProcessingJob, up.json()["id"])
        assert job is not None
        job.status = JobStatus.queued
        stale = datetime.now(timezone.utc) - timedelta(minutes=20)
        job.created_at = stale
        job.updated_at = stale
        db.commit()
        # ORM onupdate would stamp ``now``; write the stale timestamp after that.
        db.execute(
            ProcessingJob.__table__.update()
            .where(ProcessingJob.id == job.id)
            .values(updated_at=stale, created_at=stale, status=JobStatus.queued)
        )
        db.commit()
        db.close()

        monkeypatch.setattr(job_tasks.settings, "job_stuck_queued_seconds", 10 * 60)
        stuck = client.get("/jobs/stuck", headers=headers)
        assert stuck.status_code == 200, stuck.text
        stuck_body = stuck.json()
        assert stuck_body["count"] >= 1
        assert any(j["id"] == up.json()["id"] for j in stuck_body["jobs"])
        assert stuck_body["jobs"][0]["sale_id"] == sale_id
    finally:
        _clear()
