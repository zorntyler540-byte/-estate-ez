"""Durable job SSE events so dashboard progress works across API/worker processes."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import JobSseEvent, ProcessingJob  # noqa: F401 — register metadata
from app.services import job_events
from app.services.job_events import snapshot_updates_from_payload


def test_snapshot_batch_start_and_photo_analyzed() -> None:
    start = snapshot_updates_from_payload({"type": "batch_start", "total_photos": 12, "phase": "pipeline"})
    assert start["progress_total"] == 12
    assert start["progress_done"] == 0
    assert start["progress_phase"] == "pipeline_triage"

    step = snapshot_updates_from_payload(
        {"type": "photo_analyzed", "index": 3, "total": 12, "file_name": "c.jpg"},
        current_done=3,
    )
    assert step["progress_done"] == 4
    assert step["progress_total"] == 12
    assert step["progress_file_name"] == "c.jpg"

    inv = snapshot_updates_from_payload({"type": "inventory_step", "index": 0, "total": 12, "file_name": "a.jpg"})
    assert inv["progress_phase"] == "inventory"
    assert inv["progress_done"] == 1


def test_publish_persists_events_and_job_snapshot(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'job-sse.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(job_events, "SessionLocal", Session)

    with Session() as db:
        db.add(
            ProcessingJob(
                id="job-1",
                account_id="acct-1",
                user_id="user-1",
                sale_id="sale-1",
                input_folder="/tmp/batch",
            )
        )
        db.commit()

    job_events.publish("acct-1", "job-1", {"type": "batch_start", "total_photos": 3, "phase": "pipeline"})
    job_events.publish(
        "acct-1",
        "job-1",
        {"type": "photo_analyzed", "index": 0, "total": 3, "file_name": "a.jpg", "job_id": "job-1"},
    )

    rows = job_events.list_events_after("acct-1", "job-1", 0)
    assert len(rows) == 2
    assert '"batch_start"' in rows[0][1]
    assert '"photo_analyzed"' in rows[1][1]
    assert job_events.list_events_after("acct-1", "job-1", rows[0][0])[0][0] == rows[1][0]

    with Session() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == "job-1").one()
        assert job.progress_total == 3
        assert job.progress_done == 1
        assert job.progress_file_name == "a.jpg"
        assert job.progress_phase == "pipeline_triage"
