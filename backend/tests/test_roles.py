from types import SimpleNamespace

from app.services.roles import (
    ALLOWED_ROLES,
    can_catalog,
    can_delete_lot,
    can_manage_integrations,
    can_manage_team,
    can_purge_catalog,
    can_run_audience_hunt,
    is_catalogger_role,
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
