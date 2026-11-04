"""GET /jobs/catalog-status — one poll for activity across every sale."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_current_user
from app.db import Base, get_db
from app.main import app
from app.models import Account, JobStatus, ProcessingJob, Sale, User


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog-status.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed(db):
    account = Account(id="acct-cs", name="CT")
    user = User(
        id="user-cs",
        account_id=account.id,
        email="owner@example.com",
        password_hash="x",
        role="owner",
    )
    sale_a = Sale(id="sale-a", account_id=account.id, user_id=user.id, name="Alpha")
    sale_b = Sale(id="sale-b", account_id=account.id, user_id=user.id, name="Bravo")
    db.add_all([account, user, sale_a, sale_b])
    db.commit()
    return account, user, sale_a, sale_b


def _add_job(db, *, job_id: str, sale_id: str, status: JobStatus, user_id: str = "user-cs") -> None:
    db.add(
        ProcessingJob(
            id=job_id,
            account_id="acct-cs",
            user_id=user_id,
            sale_id=sale_id,
            status=status,
            input_folder="batch",
        )
    )
    db.commit()


def test_catalog_status_idle_account(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, _a, _b = _seed(db)
    _add_job(db, job_id="done", sale_id="sale-a", status=JobStatus.completed)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.get("/jobs/catalog-status")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["has_active"] is False
        assert body["active_job_count"] == 0
        assert body["sales"] == []
        assert body["jobs"] == []
        assert body["stuck"]["count"] == 0
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_catalog_status_batches_active_sales_and_optional_job_list(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, _a, _b = _seed(db)
    _add_job(db, job_id="q1", sale_id="sale-a", status=JobStatus.queued)
    _add_job(db, job_id="p1", sale_id="sale-b", status=JobStatus.processing)
    _add_job(db, job_id="old", sale_id="sale-a", status=JobStatus.completed)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        summary = client.get("/jobs/catalog-status")
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["has_active"] is True
        assert body["active_job_count"] == 2
        by_id = {row["sale_id"]: row for row in body["sales"]}
        assert set(by_id) == {"sale-a", "sale-b"}
        assert by_id["sale-a"]["queued"] == 1
        assert by_id["sale-a"]["processing"] == 0
        assert by_id["sale-b"]["processing"] == 1
        assert body["jobs"] == []

        scoped = client.get("/jobs/catalog-status", params={"sale_id": "sale-a"})
        assert scoped.status_code == 200, scoped.text
        listed = scoped.json()
        assert listed["has_active"] is True
        assert listed["active_job_count"] == 2
        ids = {j["id"] for j in listed["jobs"]}
        assert ids == {"q1", "old"}
        assert all(j["sale_id"] == "sale-a" for j in listed["jobs"])

        leftover = client.get("/jobs")
        assert leftover.status_code == 200
        assert {j["id"] for j in leftover.json()} == {"q1", "p1", "old"}
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_catalog_status_hides_other_account_jobs(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    _account, user, _a, _b = _seed(db)
    other = Account(id="acct-other", name="Other")
    other_user = User(
        id="user-other",
        account_id=other.id,
        email="other@example.com",
        password_hash="x",
        role="owner",
    )
    other_sale = Sale(id="sale-other", account_id=other.id, user_id=other_user.id, name="Other")
    db.add_all([other, other_user, other_sale])
    db.add(
        ProcessingJob(
            id="foreign",
            account_id=other.id,
            user_id=other_user.id,
            sale_id=other_sale.id,
            status=JobStatus.processing,
            input_folder="batch",
        )
    )
    db.commit()

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        res = client.get("/jobs/catalog-status")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["has_active"] is False
        assert body["sales"] == []
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_catalog_status_client_is_scoped_to_assigned_sale(tmp_path: Path) -> None:
    Session = _session(tmp_path)
    db = Session()
    account, owner, _a, _b = _seed(db)
    client_user = User(
        id="user-client",
        account_id=account.id,
        email="client@example.com",
        password_hash="x",
        role="client",
        assigned_sale_id="sale-a",
    )
    db.add(client_user)
    db.commit()
    _add_job(db, job_id="mine-q", sale_id="sale-a", status=JobStatus.queued, user_id=client_user.id)
    _add_job(db, job_id="other-p", sale_id="sale-b", status=JobStatus.processing, user_id=owner.id)

    def override_db():
        inner = Session()
        try:
            yield inner
        finally:
            inner.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: client_user
    http = TestClient(app)
    try:
        res = http.get("/jobs/catalog-status", params={"sale_id": "sale-a"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["has_active"] is True
        assert body["active_job_count"] == 1
        assert [row["sale_id"] for row in body["sales"]] == ["sale-a"]
        assert {j["id"] for j in body["jobs"]} == {"mine-q"}

        denied = http.get("/jobs/catalog-status", params={"sale_id": "sale-b"})
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        db.close()
