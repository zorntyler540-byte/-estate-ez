"""Website bulk uploads resume into one receiving job and keep pick order."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app import job_tasks
from app.db import Base, get_db
from app.main import app
from app.models import ProcessingJob
from app.services.processing import _job_photo_sort_key
from app.services.upload_names import natural_path_key

BATCH = "0123456789ab"
DRAFT = f"web-{BATCH}"


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _web(n: int, stem: str = "image", folder: str = "") -> str:
    return f"{folder}web-{BATCH}-{n:04d}-{stem}.jpg"


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'web.db'}")
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


def _register(client: TestClient) -> tuple[dict[str, str], str]:
    res = client.post(
        "/auth/register",
        json={"account_name": "Web Team", "email": "web@example.com", "password": "password1"},
    )
    assert res.status_code == 200, res.text
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    sale_id = client.get("/sales", headers=headers).json()[0]["id"]
    return headers, sale_id


def _first_chunk(client, headers, sale_id, names):
    return client.post(
        "/jobs/upload",
        headers=headers,
        data={"sale_id": sale_id, "single_lot_mode": "false", "client_draft_id": DRAFT},
        files=[("files", (n, _jpeg(), "image/jpeg")) for n in names],
    )


def test_resumed_web_batch_reuses_the_receiving_job_and_skips_received_names(tmp_path, monkeypatch):
    client, Session = _client(tmp_path, monkeypatch)
    try:
        headers, sale_id = _register(client)
        first = _first_chunk(client, headers, sale_id, [_web(1), _web(2, folder="035/")])
        assert first.status_code == 200, first.text
        job_id = first.json()["id"]

        # Response lost → the dashboard sends the first chunk again with the same draft id.
        again = _first_chunk(client, headers, sale_id, [_web(1), _web(2, folder="035/")])
        assert again.status_code == 200, again.text
        assert again.json()["id"] == job_id
        assert sorted(again.json()["received_files"]) == sorted([_web(1), _web(2, folder="035/")])

        # Resume: received-files tells the dashboard what is missing; parallel appends land once.
        names = client.get(f"/jobs/{job_id}/received-files", headers=headers).json()["names"]
        assert sorted(names) == sorted([_web(1), _web(2, folder="035/")])
        for _ in range(2):
            res = client.post(
                f"/jobs/{job_id}/upload-files",
                headers=headers,
                data={"client_draft_id": DRAFT},
                files=[("files", (_web(3), _jpeg(), "image/jpeg"))],
            )
            assert res.status_code == 200, res.text

        db = Session()
        assert db.query(ProcessingJob).count() == 1
        folder = Path(db.get(ProcessingJob, job_id).input_folder)
        db.close()
        stored = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.jpg"))
        assert stored == sorted([_web(1), _web(3), _web(2, folder="035/")])

        # Resume reads the job row (status / error_message) before deciding what to send.
        row = client.get(f"/jobs/{job_id}", headers=headers)
        assert row.status_code == 200, row.text
        assert row.json()["status"] == "receiving"
        assert row.json()["client_draft_id"] == DRAFT
        assert client.get("/jobs/nope", headers=headers).status_code == 404
        # Literal routes still win over /jobs/{job_id}.
        assert client.get("/jobs/stuck", headers=headers).status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_web_orphan_receiving_job_gets_website_wording():
    web = SimpleNamespace(client_draft_id=DRAFT)
    phone = SimpleNamespace(client_draft_id="1c86c230-a1c1-4433-b64e-5c2957e79027")
    web_msg = job_tasks._orphan_receiving_message(web)
    phone_msg = job_tasks._orphan_receiving_message(phone)
    assert "website upload" in web_msg and "phone" not in web_msg.lower()
    assert "phone Sync" in phone_msg
    # Both stay orphan-upload failures (dashboard + leftover healing key on these phrases).
    assert job_tasks.is_orphan_upload_error(web_msg)
    assert job_tasks.is_orphan_upload_error(phone_msg)


def test_job_photos_sort_by_pick_order_and_numbers_by_value(tmp_path):
    rels = [
        "IMG_10.jpg",
        "IMG_9.jpg",
        "035/IMG_2.jpg",
        f"web-{BATCH}-0010-zzz.jpg",
        f"web-{BATCH}-0002-aaa.jpg",
    ]
    assert sorted(rels, key=natural_path_key) == [
        "035/IMG_2.jpg",
        "IMG_9.jpg",
        "IMG_10.jpg",
        f"web-{BATCH}-0002-aaa.jpg",
        f"web-{BATCH}-0010-zzz.jpg",
    ]
    # Pick order wins over the camera name inside a web name.
    picks = [f"web-{BATCH}-{n:04d}-{stem}.jpg" for n, stem in [(1, "IMG_9"), (2, "IMG_10"), (3, "DSC_1")]]
    assert sorted(reversed(picks), key=natural_path_key) == picks

    paths = [tmp_path / r for r in ["b/x.jpg", "a/IMG_10.jpg", "a/IMG_9.jpg"]]
    ordered = sorted(paths, key=lambda p: _job_photo_sort_key(tmp_path, p))
    assert [p.relative_to(tmp_path).as_posix() for p in ordered] == ["a/IMG_9.jpg", "a/IMG_10.jpg", "b/x.jpg"]
