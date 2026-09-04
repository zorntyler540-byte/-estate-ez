"""Account roles: owner vs catalogger.

Cataloggers share the sale inventory and can catalog (upload, edit listings, photos,
post to CT Seller). Owners keep destructive and account-admin actions.
"""

from __future__ import annotations

from typing import Any

ROLE_OWNER = "owner"
ROLE_CATALOGGER = "catalogger"
ALLOWED_ROLES = frozenset({ROLE_OWNER, ROLE_CATALOGGER})


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def role_of(user: Any) -> str:
    return normalize_role(getattr(user, "role", None))


def is_owner_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_OWNER


def is_catalogger_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_CATALOGGER


def is_owner(user: Any) -> bool:
    return is_owner_role(role_of(user))


def can_manage_team(role: str | None) -> bool:
    return is_owner_role(role)


def can_manage_integrations(role: str | None) -> bool:
    return is_owner_role(role)


def can_close_or_delete_sale(role: str | None) -> bool:
    return is_owner_role(role)


def can_purge_catalog(role: str | None) -> bool:
    return is_owner_role(role)


def can_delete_lot(role: str | None) -> bool:
    return is_owner_role(role)


def can_run_audience_hunt(role: str | None) -> bool:
    """Audience hunt burns Pro tokens — owners only."""
    return is_owner_role(role)


def can_catalog(role: str | None) -> bool:
    return normalize_role(role) in ALLOWED_ROLES
