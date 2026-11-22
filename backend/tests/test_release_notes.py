"""What's new notes: file selection and the authenticated read endpoints."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.release_notes import (
    clear_release_notes_cache,
    latest_release_note,
    list_release_notes,
    load_release_notes,
)

SHIPPED_WEB_ID = "2026-11-21-ct-seller-post"
SHIPPED_MOBILE_ID = "2026-11-18-uploads-photos-listings"


def _write(path, notes: list[dict]) -> None:
    path.write_text(json.dumps({"notes": notes}), encoding="utf-8")
    clear_release_notes_cache()


def test_shipped_note_is_the_newest_for_both_clients() -> None:
    clear_release_notes_cache()
    web = latest_release_note("web")
    mobile = latest_release_note("mobile")
    assert web is not None and mobile is not None
    assert web.id == SHIPPED_WEB_ID
    assert web.platform == "web"
    assert mobile.id == SHIPPED_MOBILE_ID
    blob = " ".join(web.items).lower()
    assert "ct seller changed" in blob
    assert "category group" in blob
    assert "try posting" in blob
    previous = " ".join(mobile.items).lower()
    assert "white divider" in previous
    assert "lots run in parallel" in previous


def test_platform_filter_date_order_and_skipped_entries(tmp_path) -> None:
    path = tmp_path / "notes.json"
    _write(
        path,
        [
            {
                "id": "old-shared",
                "date": "2026-01-01",
                "title": "Old",
                "platform": "all",
                "items": ["shared"],
            },
            {"id": "not-a-note"},
            {
                "id": "phone-only",
                "date": "2026-11-01",
                "title": "Phone",
                "platform": "mobile",
                "items": ["  phone  ", ""],
            },
            {
                "id": "web-only",
                "date": "2026-11-18",
                "title": "Web",
                "platform": "WEB",
                "items": ["website"],
            },
            {
                "id": "same-day",
                "date": "2026-11-18",
                "title": "Later the same day",
                "platform": "web",
                "items": ["later"],
            },
            {"id": "bad-day", "date": "2026-02-31", "title": "Nope", "items": ["x"]},
            {"id": "no-items", "date": "2026-12-01", "title": "Empty", "items": ["  "]},
        ],
    )
    notes = load_release_notes(path)
    assert [note.id for note in notes] == ["old-shared", "phone-only", "web-only", "same-day"]
    assert latest_release_note("web", path=path).id == "same-day"  # type: ignore[union-attr]
    assert latest_release_note("mobile", path=path).id == "phone-only"  # type: ignore[union-attr]
    assert [note.id for note in list_release_notes("web", path=path)] == ["same-day", "web-only", "old-shared"]
    assert [note.id for note in list_release_notes("mobile", path=path)] == ["phone-only", "old-shared"]
    assert [note.id for note in list_release_notes("all", path=path)] == [
        "same-day",
        "web-only",
        "phone-only",
        "old-shared",
    ]
    phone = latest_release_note("mobile", path=path)
    assert phone is not None and phone.items == ["phone"]


def test_repeated_id_keeps_the_later_entry(tmp_path) -> None:
    path = tmp_path / "notes.json"
    _write(
        path,
        [
            {"id": "same", "date": "2026-01-01", "title": "First", "items": ["old"]},
            {"id": "same", "date": "2026-06-01", "title": "Second", "items": ["new"]},
        ],
    )
    notes = load_release_notes(path)
    assert len(notes) == 1
    assert notes[0].title == "Second"
    assert notes[0].date == "2026-06-01"


def test_broken_or_missing_file_returns_no_notes(tmp_path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    clear_release_notes_cache()
    assert load_release_notes(broken) == ()
    assert latest_release_note("web", path=tmp_path / "missing.json") is None


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'notes.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    import app.auth as auth_mod
    import app.main as main_mod

    monkeypatch.setattr(auth_mod, "SessionLocal", Session)
    monkeypatch.setattr(main_mod, "SessionLocal", Session)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _token(client: TestClient) -> str:
    res = client.post(
        "/auth/register",
        json={"account_name": "CT", "email": "owner@example.com", "password": "password1"},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def test_latest_requires_auth() -> None:
    client = TestClient(app)
    res = client.get("/release-notes/latest")
    assert res.status_code == 401


def test_authenticated_latest_and_list(tmp_path, monkeypatch) -> None:
    clear_release_notes_cache()
    client = _client(tmp_path, monkeypatch)
    try:
        headers = {"Authorization": f"Bearer {_token(client)}"}
        latest = client.get("/release-notes/latest?platform=web", headers=headers)
        assert latest.status_code == 200, latest.text
        assert "private" in latest.headers.get("cache-control", "")
        body = latest.json()
        assert body["note"]["id"] == SHIPPED_WEB_ID
        assert body["note"]["items"]
        mobile = client.get("/release-notes/latest?platform=mobile", headers=headers)
        assert mobile.status_code == 200
        assert mobile.json()["note"]["id"] == SHIPPED_MOBILE_ID
        listed = client.get("/release-notes", headers=headers)
        assert listed.status_code == 200
        ids = [row["id"] for row in listed.json()["notes"]]
        assert ids[0] == SHIPPED_WEB_ID
        rejected = client.get("/release-notes/latest?platform=desktop", headers=headers)
        assert rejected.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        clear_release_notes_cache()


def test_endpoint_uses_platform_filter(tmp_path, monkeypatch) -> None:
    path = tmp_path / "notes.json"
    _write(
        path,
        [
            {"id": "shared", "date": "2026-01-01", "title": "Shared", "platform": "all", "items": ["both"]},
            {"id": "site", "date": "2026-11-18", "title": "Site", "platform": "web", "items": ["site only"]},
        ],
    )
    import app.release_notes as notes_mod

    monkeypatch.setattr(notes_mod, "DEFAULT_PATH", path)
    clear_release_notes_cache()
    client = _client(tmp_path, monkeypatch)
    try:
        headers = {"Authorization": f"Bearer {_token(client)}"}
        web = client.get("/release-notes/latest?platform=web", headers=headers)
        phone = client.get("/release-notes/latest?platform=mobile", headers=headers)
        assert web.status_code == 200, web.text
        assert web.json()["note"]["id"] == "site"
        assert phone.json()["note"]["id"] == "shared"
        listed = client.get("/release-notes?platform=mobile", headers=headers)
        assert [row["id"] for row in listed.json()["notes"]] == ["shared"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        clear_release_notes_cache()
