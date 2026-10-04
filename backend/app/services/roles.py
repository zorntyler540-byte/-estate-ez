"""Account roles: owner, catalogger, and client.

Cataloggers share the sale inventory and can catalog (upload, edit listings, photos,
post to CT Seller). Owners keep destructive and account-admin actions.

Clients sign in only to upload photos into one sale the owner already chose.
They are not cataloggers.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

ROLE_OWNER = "owner"
ROLE_CATALOGGER = "catalogger"
ROLE_CLIENT = "client"
# Staff who can catalog. Clients are a separate login, not a catalogger.
ALLOWED_ROLES = frozenset({ROLE_OWNER, ROLE_CATALOGGER})
ASSIGNABLE_ROLES = frozenset({ROLE_OWNER, ROLE_CATALOGGER, ROLE_CLIENT})

CLIENT_UPLOAD_DETAIL = "This login can only upload photos to its assigned sale."


def normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def role_of(user: Any) -> str:
    return normalize_role(getattr(user, "role", None))


def is_owner_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_OWNER


def is_catalogger_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_CATALOGGER


def is_client_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_CLIENT


def is_owner(user: Any) -> bool:
    return is_owner_role(role_of(user))


def is_client(user: Any) -> bool:
    return is_client_role(role_of(user))


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
    """Owners and cataloggers edit the catalog. Clients only upload into one sale."""
    return normalize_role(role) in ALLOWED_ROLES


def client_http_allowed(method: str, path: str) -> bool:
    """Routes a client login may call. Sale matching is enforced separately."""
    verb = (method or "").strip().upper()
    raw = (path or "").split("?", 1)[0]
    if len(raw) > 1:
        raw = raw.rstrip("/")
    exact = {
        ("GET", "/auth/me"),
        ("PATCH", "/auth/password"),
        ("GET", "/sales"),
        ("GET", "/jobs"),
        ("POST", "/jobs/upload"),
    }
    if (verb, raw) in exact:
        return True
    if verb == "GET" and raw.startswith("/sales/") and raw.endswith("/cataloged-mobile-drafts"):
        sale_id = raw[len("/sales/") : -len("/cataloged-mobile-drafts")].strip("/")
        return bool(sale_id) and "/" not in sale_id
    if not raw.startswith("/jobs/"):
        return False
    rest = raw[len("/jobs/") :]
    parts = [p for p in rest.split("/") if p]
    if verb == "DELETE" and len(parts) == 1 and parts[0] != "upload":
        return True
    if len(parts) != 2 or parts[0] == "upload":
        return False
    action = parts[1]
    if verb == "POST" and action in {"upload-files", "run", "cancel"}:
        return True
    if verb == "GET" and action == "events":
        return True
    return False


def enforce_client_http(user: Any, method: str, path: str) -> None:
    """Raise when a client login hits a route outside photo upload for their sale."""
    if not is_client(user):
        return
    if client_http_allowed(method, path):
        return
    raise HTTPException(status_code=403, detail=CLIENT_UPLOAD_DETAIL)
