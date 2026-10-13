from types import SimpleNamespace

from app.services.roles import (
    ALLOWED_ROLES,
    CLIENT_UPLOAD_DETAIL,
    can_catalog,
    can_delete_lot,
    can_manage_integrations,
    can_manage_team,
    can_purge_catalog,
    can_run_audience_hunt,
    client_http_allowed,
    enforce_client_http,
    is_catalogger_role,
    is_client_role,
    is_owner,
    is_owner_role,
    normalize_role,
)


def test_normalize_and_allowed_roles() -> None:
    assert normalize_role(" Owner ") == "owner"
    assert normalize_role("CATALOGGER") == "catalogger"
    assert ALLOWED_ROLES == {"owner", "catalogger"}


def test_owner_capabilities() -> None:
    assert is_owner_role("owner")
    assert is_owner(SimpleNamespace(role="owner"))
    assert can_catalog("owner")
    assert can_manage_team("owner")
    assert can_manage_integrations("owner")
    assert can_delete_lot("owner")
    assert can_purge_catalog("owner")
    assert can_run_audience_hunt("owner")


def test_catalogger_can_catalog_but_not_destroy() -> None:
    assert is_catalogger_role("catalogger")
    assert not is_owner(SimpleNamespace(role="catalogger"))
    assert can_catalog("catalogger")
    assert not can_manage_team("catalogger")
    assert not can_manage_integrations("catalogger")
    assert not can_delete_lot("catalogger")
    assert not can_purge_catalog("catalogger")
    assert not can_run_audience_hunt("catalogger")


def test_unknown_role_is_not_owner() -> None:
    assert not is_owner_role("")
    assert not can_catalog("viewer")
    assert not can_delete_lot(None)


def test_client_is_not_a_catalogger() -> None:
    assert is_client_role(" Client ")
    assert not can_catalog("client")
    assert not can_manage_team("client")
    assert not can_delete_lot("client")
    assert not can_run_audience_hunt("client")
    assert "client" not in ALLOWED_ROLES


def test_client_http_allowlist_is_upload_only() -> None:
    assert client_http_allowed("GET", "/auth/me")
    assert client_http_allowed("GET", "/sales")
    assert client_http_allowed("GET", "/jobs")
    assert client_http_allowed("POST", "/jobs/upload")
    assert client_http_allowed("POST", "/jobs/job-1/upload-files")
    assert client_http_allowed("POST", "/jobs/job-1/run")
    assert client_http_allowed("DELETE", "/jobs/job-1")
    assert client_http_allowed("GET", "/sales/sale-1/cataloged-mobile-drafts")
    assert not client_http_allowed("POST", "/sales")
    assert not client_http_allowed("GET", "/lots")
    assert not client_http_allowed("PATCH", "/lots/lot-1")
    assert not client_http_allowed("POST", "/lots/lot-1/post-to-ctseller")
    assert not client_http_allowed("POST", "/jobs")
    assert not client_http_allowed("POST", "/sales/sync-from-ctseller")


def test_enforce_client_http_blocks_catalog_edits() -> None:
    import pytest
    from fastapi import HTTPException

    client = SimpleNamespace(role="client")
    owner = SimpleNamespace(role="owner")
    enforce_client_http(owner, "POST", "/sales")
    enforce_client_http(client, "POST", "/jobs/upload")
    with pytest.raises(HTTPException) as exc:
        enforce_client_http(client, "POST", "/sales")
    assert exc.value.status_code == 403
    assert exc.value.detail == CLIENT_UPLOAD_DETAIL
