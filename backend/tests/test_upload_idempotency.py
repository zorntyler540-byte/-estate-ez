"""Phone upload retries are idempotent: same draft → same job, same name → same file."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.catalog_limits as limits_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app
from app.models import Lot, LotVideo, Photo, ProcessingJob
from app.services.roles import client_http_allowed

DRAFT = "1c86c230-a1c1-4433-b64e-5c2957e79027"


def _jpeg(color: tuple[int, int, int] = (180, 40, 20)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG")
    return buf.getvalue()


def _mp4(size: int = 64) -> bytes:
    head = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    return head + b"\x00" * max(0, size - len(head))


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
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


def _register(client: TestClient, email: str) -> tuple[dict[str, str], str, str]:
    res = client.post(
        "/auth/register",
        json={"account_name": "Estate Team", "email": email, "password": "password1"},
    )
    assert res.status_code == 200, res.text
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    me = client.get("/auth/me", headers=headers).json()
    sale_id = client.get("/sales", headers=headers).json()[0]["id"]
    return headers, me["account_id"], sale_id


def _photo_name(n: int) -> str:
    return f"mobile-{DRAFT}-{n:02d}.jpg"


def _upload(client, headers, sale_id, names: list[str]):
    return client.post(
        "/jobs/upload",
        headers=headers,
        data={"sale_id": sale_id, "single_lot_mode": "true", "client_draft_id": DRAFT},
        files=[("files", (n, _jpeg(), "image/jpeg")) for n in names],
    )


def _folder_names(Session, job_id: str) -> list[str]:
    db = Session()
    try:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        return sorted(p.name for p in Path(job.input_folder).iterdir() if p.is_file())
    finally:
        db.close()


def _make_lot(Session, account_id: str, sale_id: str, user_id: str | None = None) -> str:
    db = Session()
    try:
        if user_id is None:
            from app.models import User

            user_id = db.query(User).filter(User.account_id == account_id).first().id
        db.add(Lot(id="lot-idem", account_id=account_id, user_id=user_id, sale_id=sale_id, sku="AB1"))
        db.commit()
        return "lot-idem"
    finally:
        db.close()


def test_jobs_upload_reuses_receiving_job_for_same_draft(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, _acct, sale_id = _register(client, "reuse@example.com")
        first = _upload(client, headers, sale_id, [_photo_name(1)])
        assert first.status_code == 200, first.text
        again = _upload(client, headers, sale_id, [_photo_name(1)])
        assert again.status_code == 200, again.text
        assert again.json()["id"] == first.json()["id"]
        assert again.json()["received_files"] == [_photo_name(1)]

        more = _upload(client, headers, sale_id, [_photo_name(2)])
        assert more.json()["id"] == first.json()["id"]
        assert _folder_names(Session, first.json()["id"]) == [_photo_name(1), _photo_name(2)]

        db = Session()
        assert db.query(ProcessingJob).count() == 1
        db.close()
    finally:
        _clear()


def test_upload_files_same_name_is_success_not_a_copy(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, _acct, sale_id = _register(client, "samename@example.com")
        created = _upload(client, headers, sale_id, [_photo_name(1)])
        job_id = created.json()["id"]
        for _ in range(2):
            res = client.post(
                f"/jobs/{job_id}/upload-files",
                headers=headers,
                data={"client_draft_id": DRAFT},
                files=[("files", (_photo_name(2), _jpeg((1, 2, 3)), "image/jpeg"))],
            )
            assert res.status_code == 200, res.text
            assert res.json()["received_files"] == [_photo_name(1), _photo_name(2)]
        assert _folder_names(Session, job_id) == [_photo_name(1), _photo_name(2)]
    finally:
        _clear()


def test_received_files_lists_names_and_reports_missing_folder(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, _acct, sale_id = _register(client, "received@example.com")
        created = _upload(client, headers, sale_id, [_photo_name(1), _photo_name(2)])
        job_id = created.json()["id"]
        res = client.get(f"/jobs/{job_id}/received-files", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["job_id"] == job_id
        assert body["names"] == [_photo_name(1), _photo_name(2)]
        assert body["folder_missing"] is False

        db = Session()
        job = db.get(ProcessingJob, job_id)
        for p in Path(job.input_folder).iterdir():
            p.unlink()
        Path(job.input_folder).rmdir()
        db.close()
        gone = client.get(f"/jobs/{job_id}/received-files", headers=headers).json()
        assert gone["names"] == []
        assert gone["folder_missing"] is True

        assert client.get("/jobs/nope/received-files", headers=headers).status_code == 404
    finally:
        _clear()


def test_clients_may_read_received_files() -> None:
    assert client_http_allowed("GET", "/jobs/job-1/received-files")
    assert not client_http_allowed("POST", "/jobs/job-1/received-files")


def test_run_rejects_a_lot_over_forty_photos(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, _acct, sale_id = _register(client, "cap@example.com")
        created = _upload(client, headers, sale_id, [_photo_name(1)])
        job_id = created.json()["id"]
        db = Session()
        folder = Path(db.get(ProcessingJob, job_id).input_folder)
        db.close()
        for n in range(2, 42):
            (folder / _photo_name(n)).write_bytes(_jpeg())
        res = client.post(f"/jobs/{job_id}/run", headers=headers)
        assert res.status_code == 400, res.text
        assert "41 photos" in res.json()["detail"]
        db = Session()
        assert db.get(ProcessingJob, job_id).status.value == "receiving"
        db.close()
    finally:
        _clear()


def test_lot_photo_upload_skips_stable_names_already_on_the_lot(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, acct, sale_id = _register(client, "lotphoto@example.com")
        lot_id = _make_lot(Session, acct, sale_id)
        name = "append-5f0e1d2c-0001-4abc-9def-123456789abc-01.jpg"
        for _ in range(2):
            res = client.post(
                f"/lots/{lot_id}/photos/upload",
                headers=headers,
                files=[("files", (name, _jpeg(), "image/jpeg"))],
            )
            assert res.status_code == 200, res.text
            assert len(res.json()) == 1
        db = Session()
        assert db.query(Photo).filter(Photo.lot_id == lot_id).count() == 1
        db.close()

        # Arbitrary names (dashboard drag-and-drop) still append every time.
        for _ in range(2):
            res = client.post(
                f"/lots/{lot_id}/photos/upload",
                headers=headers,
                files=[("files", ("IMG_1.jpg", _jpeg(), "image/jpeg"))],
            )
            assert res.status_code == 200, res.text
        assert len(res.json()) == 3
    finally:
        _clear()


def test_lot_video_upload_dedupes_on_lot_and_name(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, acct, sale_id = _register(client, "lotvideo@example.com")
        lot_id = _make_lot(Session, acct, sale_id)
        name = f"mobile-{DRAFT}-vid-01.mp4"
        for _ in range(2):
            res = client.post(
                f"/lots/{lot_id}/videos/upload",
                headers=headers,
                files=[("files", (name, _mp4(), "video/mp4")), ("files", (name, _mp4(), "video/mp4"))],
            )
            assert res.status_code == 200, res.text
            assert [v["original_name"] for v in res.json()] == [name]
        other = client.post(
            f"/lots/{lot_id}/videos/upload",
            headers=headers,
            files=[("files", (f"mobile-{DRAFT}-vid-02.mp4", _mp4(), "video/mp4"))],
        )
        assert other.status_code == 200, other.text
        assert len(other.json()) == 2
        db = Session()
        assert db.query(LotVideo).filter(LotVideo.lot_id == lot_id).count() == 2
        db.close()
    finally:
        _clear()


def test_lot_video_over_size_cap_is_rejected_while_streaming(tmp_path, monkeypatch) -> None:
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, acct, sale_id = _register(client, "bigvideo@example.com")
        lot_id = _make_lot(Session, acct, sale_id)
        monkeypatch.setattr(main_mod, "MAX_VIDEO_BYTES", 4096)
        monkeypatch.setattr(limits_mod, "MAX_VIDEO_BYTES", 4096)
        monkeypatch.setattr(main_mod, "_VIDEO_STREAM_CHUNK", 1024)
        res = client.post(
            f"/lots/{lot_id}/videos/upload",
            headers=headers,
            files=[("files", ("big.mp4", _mp4(20_000), "video/mp4"))],
        )
        assert res.status_code == 400, res.text
        assert "MB or smaller" in res.json()["detail"]
        db = Session()
        assert db.query(LotVideo).count() == 0
        db.close()
    finally:
        _clear()


def test_health_ping_is_unauthenticated_and_touches_no_db(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("ping must not open a DB session")

    monkeypatch.setattr(main_mod, "SessionLocal", boom)
    app.dependency_overrides[get_db] = boom
    try:
        res = TestClient(app).get("/health/ping")
    finally:
        _clear()
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}
