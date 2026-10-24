"""Operations jobs: auth/tenancy, quote persistence, settlement fee from actual proceeds."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.auth as auth_mod
import app.main as main_mod
from app.db import Base, get_db
from app.main import app


def _client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}")
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


def _register(client: TestClient, email: str, account_name: str = "Estate Team") -> tuple[str, dict]:
    res = client.post(
        "/auth/register",
        json={"account_name": account_name, "email": email, "password": "password1"},
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me


def _job_body(**overrides):
    body = {
        "client_name": "Jane Doe",
        "phone": "555-0100",
        "email": "jane@example.com",
        "address": "12 Oak St",
        "zip": "89101",
        "cuft": 1500,
        "density": "medium",
        "liquidation": "estate",
        "include_sorting": True,
        "include_cleanout": False,
        "include_packing": False,
        "include_resettling": False,
        "space_plan_hours": 0,
        "projected_proceeds": 10000,
        "status": "quoted",
        "notes": "",
    }
    body.update(overrides)
    return body


def test_quote_math_matches_workbook_and_settlement_uses_actual_proceeds(tmp_path, monkeypatch) -> None:
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        token, _me = _register(client, "owner-ops@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post("/ops/jobs", headers=headers, json=_job_body())
        assert created.status_code == 201, created.text
        job = created.json()
        assert job["quote"]["prepPrice"] == 3750
        assert job["quote"]["percentFee"] == 3500
        assert job["quote"]["commission"] == 3750
        assert job["quote"]["feeWinner"] == "prep"
        assert job["quote"]["clientTotal"] == 7500
        assert job["client_total"] == 7500
        job_id = job["id"]

        settled = client.put(
            f"/ops/jobs/{job_id}/settlement",
            headers=headers,
            json={
                "actual_hours": 40,
                "in_person_proceeds": 40000,
                "online_proceeds": 0,
                "buyer_premium": 500,
                "shipping": 100,
                "misc_revenue": 0,
                "direct_expenses": 200,
                "deposit_received": 0,
                "balance_received": 0,
            },
        )
        assert settled.status_code == 200, settled.text
        body = settled.json()
        assert body["status"] == "settled"
        # Quote snapshot keeps expected proceeds; settlement result uses $40k actual.
        assert body["quote"]["commission"] == 3750
        assert body["quote"]["percentFee"] == 3500
        assert body["settlement_result"]["commission"] == 14000
        assert body["settlement_result"]["feeWinner"] == "percent"
        assert body["settlement_result"]["percentFee"] == 14000
        assert body["client_total"] == 3750 + 14000
        assert body["settlement_result"]["invoiced"] == 3750 + 14000 + 600
    finally:
        _clear()


def test_ops_jobs_are_scoped_to_the_account(tmp_path, monkeypatch) -> None:
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        token_a, _ = _register(client, "a-ops@example.com", "Team A")
        token_b, _ = _register(client, "b-ops@example.com", "Team B")
        created = client.post(
            "/ops/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
            json=_job_body(client_name="Only A"),
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]

        stolen = client.get(f"/ops/jobs/{job_id}", headers={"Authorization": f"Bearer {token_b}"})
        assert stolen.status_code == 404

        listed = client.get("/ops/jobs", headers={"Authorization": f"Bearer {token_b}"})
        assert listed.status_code == 200
        assert listed.json() == []
    finally:
        _clear()


def test_client_login_cannot_use_operations(tmp_path, monkeypatch) -> None:
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        owner_token, _ = _register(client, "owner-ops-client@example.com")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        sale = client.post("/sales", headers=owner_headers, json={"name": "Spring estate"})
        assert sale.status_code == 201, sale.text
        invited = client.post(
            "/team/members",
            headers=owner_headers,
            json={
                "email": "ops-client@example.com",
                "password": "client-pass-1",
                "role": "client",
                "sale_id": sale.json()["id"],
            },
        )
        assert invited.status_code == 201, invited.text
        login = client.post("/auth/login", json={"email": "ops-client@example.com", "password": "client-pass-1"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        blocked = client.get("/ops/jobs", headers=headers)
        assert blocked.status_code == 403
    finally:
        _clear()


def test_settled_job_locks_quote_inputs(tmp_path, monkeypatch) -> None:
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        token, _ = _register(client, "lock-ops@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/ops/jobs", headers=headers, json=_job_body())
        job_id = created.json()["id"]
        client.put(
            f"/ops/jobs/{job_id}/settlement",
            headers=headers,
            json={"actual_hours": 10, "in_person_proceeds": 1000},
        )
        updated = client.put(
            f"/ops/jobs/{job_id}",
            headers=headers,
            json=_job_body(client_name="Jane D.", projected_proceeds=99999, notes="keep quote"),
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["client_name"] == "Jane D."
        assert body["notes"] == "keep quote"
        assert body["projected_proceeds"] == 10000
        assert body["quote"]["commission"] == 3750
        assert body["status"] == "settled"
    finally:
        _clear()


def test_job_write_still_accepts_legacy_sqft(tmp_path, monkeypatch) -> None:
    client, _Session = _client(tmp_path, monkeypatch)
    try:
        token, _ = _register(client, "cuft-alias@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        payload = _job_body()
        del payload["cuft"]
        payload["sqft"] = 1500
        created = client.post("/ops/jobs", headers=headers, json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["cuft"] == 1500
        assert body["quote"]["hours"] == 100
        assert "sqft" not in body
    finally:
        _clear()
