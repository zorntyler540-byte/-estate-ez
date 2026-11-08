"""Website batches split into per-lot jobs after triage; bulk work has its own lane; quotas."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.job_queue import claim_next_fair_job
from app.main import app
from app.models import JobStatus, ProcessingJob
from app.services import processing
from app.services.processing import SPLIT_TRIAGE_SIDECAR, cap_lot_segments


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fanout.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (90, 20, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _batch_folder(tmp_path: Path, rels: list[str]) -> tuple[Path, list[Path]]:
    folder = tmp_path / "uploads" / "acct-1" / "batch-1"
    paths = []
    for rel in rels:
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_jpeg())
        paths.append(p)
    (folder / (rels[0] + ".captured_at")).write_text("2026-11-01T10:00:00+00:00")
    return folder, paths


def _parent(db, folder: Path, **kw) -> ProcessingJob:
    job = ProcessingJob(
        id=kw.pop("id", "parent-1"),
        account_id="acct-1",
        user_id="user-1",
        sale_id="sale-1",
        status=JobStatus.processing,
        input_folder=str(folder),
        inherit_sku_from_subfolder=True,
        single_lot_mode=False,
        **kw,
    )
    db.add(job)
    db.commit()
    return job


def test_segments_over_forty_photos_become_review_parts() -> None:
    big = [(Path(f"/x/{i}.jpg"), f"{i}.jpg") for i in range(95)]
    small = big[:3]
    lots = cap_lot_segments([small, big])
    assert [(len(e), part, parts) for e, part, parts in lots] == [
        (3, 1, 1),
        (40, 1, 3),
        (40, 2, 3),
        (15, 3, 3),
    ]
    flat = [entry for e, _p, _n in lots[1:] for entry in e]
    assert flat == big


def test_split_queues_one_single_lot_job_per_lot_and_completes_parent(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    folder, paths = _batch_folder(tmp_path, ["035/a.jpg", "035/b.jpg", "036/a.jpg"])
    triage = {p.name if i < 2 else "a.jpg#2": {"filename": "x", "photo_role": "item"} for i, p in enumerate(paths)}
    lots = [
        ([(paths[0], "a.jpg"), (paths[1], "b.jpg")], 1, 1),
        ([(paths[2], "a.jpg#2")], 1, 1),
    ]
    with Session() as db:
        parent = _parent(db, folder)
        children = processing._split_batch_into_lot_jobs(db, parent, folder, lots, triage)
        db.refresh(parent)
        assert parent.status == JobStatus.completed
        assert parent.progress_phase == "split" and parent.progress_total == 2
        rows = db.query(ProcessingJob).filter(ProcessingJob.parent_job_id == "parent-1").all()
        assert len(rows) == 2
        rows.sort(key=lambda r: r.created_at)
        assert [r.id for r in rows] == [c.id for c in children]
        for r in rows:
            assert r.status == JobStatus.queued
            assert r.single_lot_mode is True
            assert r.inherit_sku_from_subfolder is True
            assert Path(r.input_folder).parent == folder.parent

        first = Path(rows[0].input_folder)
        assert sorted(p.relative_to(first).as_posix() for p in first.rglob("*.jpg")) == ["035/a.jpg", "035/b.jpg"]
        assert (first / "035/a.jpg.captured_at").is_file()
        side = json.loads((first / SPLIT_TRIAGE_SIDECAR).read_text())
        assert side["parent_job_id"] == "parent-1"
        assert [r["filename"] for r in side["rows"]] == ["a.jpg", "b.jpg"]
        second = Path(rows[1].input_folder)
        # Re-keyed to the child's own leaf name (``a.jpg#2`` only existed across subfolders).
        assert [r["filename"] for r in json.loads((second / SPLIT_TRIAGE_SIDECAR).read_text())["rows"]] == ["a.jpg"]


def test_split_is_all_or_nothing(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    folder, paths = _batch_folder(tmp_path, ["a.jpg", "b.jpg"])
    calls = {"n": 0}
    real = processing._link_or_copy

    def flaky(src, dest):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        real(src, dest)

    monkeypatch.setattr(processing, "_link_or_copy", flaky)
    lots = [([(paths[0], "a.jpg")], 1, 1), ([(paths[1], "b.jpg")], 1, 1)]
    with Session() as db:
        parent = _parent(db, folder)
        with pytest.raises(OSError):
            processing._split_batch_into_lot_jobs(db, parent, folder, lots, {})
        db.refresh(parent)
        assert parent.status == JobStatus.processing
        assert db.query(ProcessingJob).filter(ProcessingJob.parent_job_id.isnot(None)).count() == 0
    assert sorted(p.name for p in folder.parent.iterdir()) == ["batch-1"]


def test_batch_pipeline_fans_out_instead_of_cataloging_inline(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    folder, paths = _batch_folder(tmp_path, [f"IMG_{i}.jpg" for i in range(1, 6)])
    monkeypatch.setattr(
        processing,
        "triage_ordered_photo_batch",
        lambda photos, **_k: [(p, p.name, {"filename": p.name}) for p in photos],
    )
    monkeypatch.setattr(
        processing,
        "_segment_batch_lots_by_tape",
        lambda _f, ordered, **_k: [[(p, k) for p, k, _r in ordered[:2]], [(p, k) for p, k, _r in ordered[2:]]],
    )

    def no_inline(*_a, **_k):
        raise AssertionError("lots must run as their own jobs, not inside the batch job")

    monkeypatch.setattr(processing, "run_single_lot_pipeline", no_inline)
    with Session() as db:
        parent = _parent(db, folder)
        processing._process_job_batch_pipeline(db, parent, paths, folder)
        db.refresh(parent)
        assert parent.status == JobStatus.completed
        kids = db.query(ProcessingJob).filter(ProcessingJob.parent_job_id == parent.id).all()
        assert sorted(len(list(Path(k.input_folder).glob("*.jpg"))) for k in kids) == [2, 3]


def test_child_reuses_batch_triage(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    folder, paths = _batch_folder(tmp_path, ["a.jpg", "b.jpg"])
    seen: dict = {}

    def capture(photos, **kwargs):
        seen["rows"] = kwargs.get("pre_triage_rows")
        raise processing.PipelineCancelled()

    monkeypatch.setattr(processing, "run_single_lot_pipeline", capture)
    with Session() as db:
        parent = _parent(db, folder)
        triage = {"a.jpg": {"filename": "a.jpg", "photo_role": "hero"}, "b.jpg": {"filename": "b.jpg"}}
        (child,) = processing._split_batch_into_lot_jobs(
            db, parent, folder, [([(paths[0], "a.jpg"), (paths[1], "b.jpg")], 1, 1)], triage
        )
        child_dir = Path(child.input_folder)
        child_photos = sorted(child_dir.glob("*.jpg"))
        processing._process_job_single_lot_pipeline(db, child, child_photos, folder=child_dir)
    assert [r["filename"] for r in seen["rows"]] == ["a.jpg", "b.jpg"]
    assert seen["rows"][0]["photo_role"] == "hero"


def _queued(db, job_id: str, *, single: bool, parent: str | None = None, status=JobStatus.queued, age_min=5):
    when = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    db.add(
        ProcessingJob(
            id=job_id,
            account_id="acct-1",
            user_id="user-1",
            sale_id="sale-1",
            status=status,
            input_folder="/tmp/x",
            single_lot_mode=single,
            parent_job_id=parent,
            created_at=when,
            updated_at=when,
        )
    )
    db.commit()


def test_phone_lots_never_wait_behind_split_lots(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.job_queue.SessionLocal", Session)
    monkeypatch.setattr("app.config.settings.catalog_job_max_concurrent", 1)
    monkeypatch.setattr("app.config.settings.catalog_bulk_max_concurrent", 2)
    with Session() as db:
        for i in range(4):
            _queued(db, f"lot-{i}", single=True, parent="parent-1", age_min=30 - i)
        _queued(db, "phone", single=True, age_min=1)

    claimed = [claim_next_fair_job() for _ in range(4)]
    ids = [c[0] for c in claimed if c]
    # Two split lots run side by side, and the newer phone lot still gets its own slot.
    assert sorted(ids) == ["lot-0", "lot-1", "phone"]
    assert claimed[3] is None

    with Session() as db:
        db.get(ProcessingJob, "lot-0").status = JobStatus.completed
        db.commit()
    assert claim_next_fair_job() == ("lot-2", "acct-1")


def test_single_lot_lane_cap_does_not_block_bulk(tmp_path: Path, monkeypatch) -> None:
    Session = _session(tmp_path)
    monkeypatch.setattr("app.job_queue.SessionLocal", Session)
    monkeypatch.setattr("app.config.settings.catalog_job_max_concurrent", 1)
    monkeypatch.setattr("app.config.settings.catalog_bulk_max_concurrent", 1)
    with Session() as db:
        _queued(db, "phone-run", single=True, status=JobStatus.processing)
        _queued(db, "phone-wait", single=True, age_min=20)
        _queued(db, "batch", single=False, age_min=2)
    assert claim_next_fair_job() == ("batch", "acct-1")
    assert claim_next_fair_job() is None


def _client(tmp_path, monkeypatch):
    Session = _session(tmp_path)
    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    res = client.post(
        "/auth/register",
        json={"account_name": "Quota Team", "email": "quota@example.com", "password": "password1"},
    )
    assert res.status_code == 200, res.text
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    me = client.get("/auth/me", headers=headers).json()
    sale_id = client.get("/sales", headers=headers).json()[0]["id"]
    return client, Session, headers, me, sale_id


def test_quota_preflight_and_split_lots_do_not_count_as_queued(tmp_path: Path, monkeypatch) -> None:
    client, Session, headers, me, sale_id = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.catalog_max_queued_jobs_per_account", 2)
    monkeypatch.setattr("app.config.settings.catalog_max_photos_per_account_per_day", 100)
    try:
        q = client.get("/jobs/quota", headers=headers)
        assert q.status_code == 200, q.text
        body = q.json()
        assert body["photos_remaining_today"] == 100
        assert body["max_photos_per_lot"] == 40
        assert body["queued_jobs"] == 0

        with Session() as db:
            for i in range(5):
                db.add(
                    ProcessingJob(
                        id=f"split-{i}",
                        account_id=me["account_id"],
                        user_id=me["id"],
                        sale_id=sale_id,
                        status=JobStatus.queued,
                        input_folder="/nowhere",
                        single_lot_mode=True,
                        parent_job_id="parent",
                    )
                )
            db.commit()
        assert client.get("/jobs/quota", headers=headers).json()["queued_jobs"] == 0

        form = {"sale_id": sale_id, "single_lot_mode": "false", "client_draft_id": "web-0123456789ab"}
        files = [("files", ("web-0123456789ab-0001-a.jpg", _jpeg(), "image/jpeg"))]
        too_big = client.post("/jobs/upload", headers=headers, data={**form, "expected_photos": "101"}, files=files)
        assert too_big.status_code == 429, too_big.text
        assert int(too_big.headers["retry-after"]) >= 60
        over_job = client.post("/jobs/upload", headers=headers, data={**form, "expected_photos": "251"}, files=files)
        assert over_job.status_code == 400, over_job.text
        ok = client.post("/jobs/upload", headers=headers, data={**form, "expected_photos": "60"}, files=files)
        assert ok.status_code == 200, ok.text
        assert ok.json()["parent_job_id"] is None
    finally:
        app.dependency_overrides.pop(get_db, None)
