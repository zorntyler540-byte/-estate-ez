"""Job folder chroot, photo caps, account quotas, and SSE tickets."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.job_batch_storage import resolve_job_batch
from app.main import app
from app.models import JobStatus, ProcessingJob, Sale, User
from app.upload_storage import new_batch_dir


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (180, 40, 20)).save(buf, format="JPEG")
    return buf.getvalue()


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobsec.db'}")
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


def test_resolve_job_batch_refuses_other_account(tmp_path, monkeypatch) -> None:
    root = tmp_path / "uploads"
    (root / "acct-b" / "batch-9").mkdir(parents=True)
    monkeypatch.setattr("app.job_batch_storage.upload_root", lambda: root.resolve())
    assert resolve_job_batch(root / "acct-b" / "batch-9", "acct-a") is None
    assert resolve_job_batch(root / "acct-b" / "batch-9", "acct-b") == ("acct-b", "batch-9")


def test_create_job_rejects_path_outside_account_upload_root(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token_a, _acct_a = _register(client, "a@example.com")
        token_b, acct_b = _register(client, "b@example.com")
        db = Session()
        sale_a = db.query(Sale).filter(Sale.account_id == db.query(User).filter(User.email == "a@example.com").one().account_id).one()
        sale_a_id = sale_a.id
        db.close()
        other = tmp_path / "not-uploads" / "secret"
        other.mkdir(parents=True)
        (other / "x.jpg").write_bytes(_jpeg())
        res = client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"input_folder": str(other), "sale_id": sale_a_id},
        )
        assert res.status_code == 400
        victim = new_batch_dir(acct_b)
        (victim / "y.jpg").write_bytes(_jpeg())
        res2 = client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"input_folder": str(victim), "sale_id": sale_a_id},
        )
        assert res2.status_code == 400
        mine = new_batch_dir(
            Session().query(User).filter(User.email == "a@example.com").one().account_id
        )
        (mine / "z.jpg").write_bytes(_jpeg())
        ok = client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"input_folder": str(mine), "sale_id": sale_a_id},
        )
        assert ok.status_code == 200, ok.text
        assert Path(ok.json()["input_folder"]).resolve() == mine.resolve()
    finally:
        _clear()


def test_upload_enforces_photo_cap_on_batch_and_facebook(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "catalog_max_photos_per_job", 40)
    monkeypatch.setattr(settings, "catalog_max_photos_per_account_per_day", 5000)
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "cap@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        files = [("files", (f"p{i}.jpg", _jpeg(), "image/jpeg")) for i in range(41)]
        batch = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "false"},
            files=files,
        )
        assert batch.status_code == 400, batch.text
        facebook = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true", "listing_channel": "facebook"},
            files=files,
        )
        assert facebook.status_code == 400
        ok_files = [("files", ("p1.jpg", _jpeg(), "image/jpeg"))]
        ok = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=ok_files,
        )
        assert ok.status_code == 200, ok.text
    finally:
        _clear()


def test_queued_job_quota_per_account(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "catalog_max_queued_jobs_per_account", 1)
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, _acct = _register(client, "q@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        first = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=[("files", ("p1.jpg", _jpeg(), "image/jpeg"))],
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=[("files", ("p2.jpg", _jpeg(), "image/jpeg"))],
        )
        assert second.status_code == 429
    finally:
        _clear()


def test_events_ticket_required_instead_of_access_token_query(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        token, acct = _register(client, "sse@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        sale_id = client.get("/sales", headers=headers).json()[0]["id"]
        created = client.post(
            "/jobs/upload",
            headers=headers,
            data={"sale_id": sale_id, "single_lot_mode": "true"},
            files=[("files", ("p1.jpg", _jpeg(), "image/jpeg"))],
        )
        job_id = created.json()["id"]
        leaked = client.get(f"/jobs/{job_id}/events?access_token={token}")
        assert leaked.status_code == 401
        mint = client.post(f"/jobs/{job_id}/events-ticket", headers=headers)
        assert mint.status_code == 200, mint.text
        ticket = mint.json()["ticket"]
        assert ticket
        assert token not in ticket
        with client.stream("GET", f"/jobs/{job_id}/events?sse_ticket={ticket}") as ok:
            assert ok.status_code == 200
        db = Session()
        other = ProcessingJob(
            account_id=acct,
            user_id=db.query(User).filter(User.email == "sse@example.com").one().id,
            sale_id=sale_id,
            status=JobStatus.queued,
            input_folder=str(tmp_path),
        )
        db.add(other)
        db.commit()
        other_id = other.id
        db.close()
        wrong_job = client.get(f"/jobs/{other_id}/events?sse_ticket={ticket}")
        assert wrong_job.status_code == 401
    finally:
        _clear()
