"""POST /mobile/drafts/status: batched job + lot state for specific phone drafts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app
from app.models import JobStatus, Lot, LotVideo, Photo, ProcessingJob
from app.services.mobile_drafts import mobile_draft_statuses, normalize_draft_ids

DRAFT_A = "1c86c230-a1c1-4433-b64e-5c2957e79027"
DRAFT_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DRAFT_C = "cccccccc-1111-2222-3333-444444444444"
DRAFT_D = "dddddddd-1111-2222-3333-444444444444"


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'status.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _lot(lot_id: str, sale_id: str, sku: str | None = None, created_at: datetime | None = None) -> Lot:
    return Lot(
        id=lot_id,
        account_id="acct-1",
        user_id="user-1",
        sale_id=sale_id,
        auction_title=lot_id,
        sku=sku,
        photo_count=1,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _job(job_id: str, draft_id: str, status: JobStatus, **kw) -> ProcessingJob:
    return ProcessingJob(
        id=job_id,
        account_id=kw.pop("account_id", "acct-1"),
        user_id=kw.pop("user_id", "user-1"),
        sale_id=kw.pop("sale_id", "sale-1"),
        status=status,
        input_folder="/tmp/x",
        single_lot_mode=True,
        client_draft_id=draft_id,
        **kw,
    )


def test_normalize_draft_ids_drops_junk_and_dupes() -> None:
    ids = normalize_draft_ids([DRAFT_A.upper(), DRAFT_A, "", "not-a-uuid", "mobile-%", DRAFT_B])
    assert ids == [DRAFT_A, DRAFT_B]


def test_statuses_map_lots_by_exact_media_name_only(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add_all(
        [
            _lot("lot-a", "sale-1", sku="JL91500"),
            _lot("lot-vid", "sale-2"),
            Photo(
                id="p1",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-a",
                original_name=f"mobile-{DRAFT_A}-01.jpg",
                storage_path="/c/a1.jpg",
                sort_order=0,
            ),
            LotVideo(
                id="v1",
                account_id="acct-1",
                user_id="user-1",
                lot_id="lot-vid",
                original_name=f"mobile-{DRAFT_B}-vid-01.mp4",
                storage_path="/c/v1.mp4",
                sort_order=0,
            ),
            _job("job-a", DRAFT_A, JobStatus.completed),
            _job("job-c", DRAFT_C, JobStatus.processing),
        ]
    )
    db.commit()
    rows = {r.draft_id: r for r in mobile_draft_statuses(db, "acct-1", [DRAFT_A, DRAFT_B, DRAFT_C, DRAFT_D])}
    assert rows[DRAFT_A].lot_id == "lot-a" and rows[DRAFT_A].cataloged
    assert rows[DRAFT_A].job_status == "completed"
    assert rows[DRAFT_B].lot_id == "lot-vid" and rows[DRAFT_B].sale_id == "sale-2"
    assert rows[DRAFT_C].lot_id is None and not rows[DRAFT_C].cataloged
    assert rows[DRAFT_C].job_status == "processing"
    assert rows[DRAFT_D].job_id is None and rows[DRAFT_D].lot_id is None and not rows[DRAFT_D].cataloged
    db.close()


def test_completed_job_wins_over_leftover_receiving(tmp_path: Path) -> None:
    db = _session(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.add_all(
        [
            _job("job-done", DRAFT_A, JobStatus.completed, created_at=old),
            _job("job-left", DRAFT_A, JobStatus.receiving),
        ]
    )
    db.commit()
    [row] = mobile_draft_statuses(db, "acct-1", [DRAFT_A])
    assert row.job_id == "job-done"
    assert row.cataloged is True
    db.close()


def test_other_account_and_client_scope_are_hidden(tmp_path: Path) -> None:
    db = _session(tmp_path)
    db.add_all(
        [
            _job("job-other-acct", DRAFT_A, JobStatus.completed, account_id="acct-2"),
            _job("job-other-sale", DRAFT_B, JobStatus.processing, sale_id="sale-9"),
            _job("job-mine", DRAFT_C, JobStatus.queued, user_id="client-1"),
        ]
    )
    db.commit()
    rows = {r.draft_id: r for r in mobile_draft_statuses(db, "acct-1", [DRAFT_A, DRAFT_B, DRAFT_C])}
    assert rows[DRAFT_A].job_id is None
    assert rows[DRAFT_B].job_id == "job-other-sale"

    scoped = {
        r.draft_id: r
        for r in mobile_draft_statuses(
            db, "acct-1", [DRAFT_B, DRAFT_C], client_sale_id="sale-1", client_user_id="client-1"
        )
    }
    assert scoped[DRAFT_B].job_id is None
    assert scoped[DRAFT_C].job_id == "job-mine"
    db.close()


def test_route_returns_rows_for_owner(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'route.db'}")
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
    try:
        client = TestClient(app)
        res = client.post(
            "/auth/register",
            json={"account_name": "Estate Team", "email": "status@example.com", "password": "password1"},
        )
        assert res.status_code == 200, res.text
        headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
        me = client.get("/auth/me", headers=headers).json()
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        db = Session()
        db.add(
            _job(
                "job-route",
                DRAFT_A,
                JobStatus.queued,
                account_id=me["account_id"],
                user_id=me["id"],
                sale_id=sale_id,
            )
        )
        db.commit()
        db.close()
        res = client.post(
            "/mobile/drafts/status",
            headers=headers,
            json={"draft_ids": [DRAFT_A, DRAFT_B, "junk"]},
        )
        assert res.status_code == 200, res.text
        rows = {r["draft_id"]: r for r in res.json()["drafts"]}
        assert set(rows) == {DRAFT_A, DRAFT_B}
        assert rows[DRAFT_A]["job_status"] == "queued"
        assert rows[DRAFT_A]["sale_id"] == sale_id
        assert rows[DRAFT_B]["job_id"] is None
        too_many = client.post(
            "/mobile/drafts/status",
            headers=headers,
            json={"draft_ids": [DRAFT_A] * 201},
        )
        assert too_many.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
