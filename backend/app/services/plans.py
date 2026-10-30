"""Account plans.

``beta_free`` (and any other stored code) keeps the full catalog.
``watcher`` is a self-serve Auction Watcher login. It can use the Push queue
and Facebook posting only when those switches are on.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

PLAN_FULL = "beta_free"
PLAN_WATCHER = "watcher"
ASSIGNABLE_PLANS = frozenset({PLAN_FULL, PLAN_WATCHER})

WATCHER_BLOCKED_DETAIL = (
    "Auction Watcher accounts can use the Push queue and Facebook posting. "
    "Catalog, AI, and the other tools are turned off."
)
ANALYTICS_OFF_DETAIL = "The Push queue is turned off for this account."
FACEBOOK_OFF_DETAIL = "Facebook posting is turned off for this account."

_ALWAYS = {
    ("GET", "/auth/me"),
    ("PATCH", "/auth/password"),
    ("POST", "/auth/logout-all"),
}

_ANALYTICS_EXACT = {
    ("POST", "/integrations/engagement/snapshots"),
    ("GET", "/engagement/sales"),
}

_FACEBOOK_EXACT = {
    ("GET", "/integrations/facebook/status"),
    ("GET", "/integrations/facebook/oauth/start"),
    ("GET", "/integrations/facebook/pages"),
    ("POST", "/integrations/facebook/pages/select"),
    ("DELETE", "/integrations/facebook/connection"),
    ("PATCH", "/integrations/facebook/settings"),
    ("POST", "/facebook/posts/preview"),
    ("POST", "/facebook/posts"),
    ("GET", "/facebook/posts"),
    ("GET", "/facebook/clicks"),
}


def normalize_plan(plan: str | None) -> str:
    return (plan or "").strip().lower()


def plan_of(account: Any) -> str:
    return normalize_plan(getattr(account, "plan_code", None))


def is_watcher_plan(plan: str | None) -> bool:
    return normalize_plan(plan) == PLAN_WATCHER


def is_watcher_account(account: Any) -> bool:
    return account is not None and is_watcher_plan(plan_of(account))


def request_path(path: str) -> str:
    raw = (path or "").split("?", 1)[0]
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw


def watcher_route_feature(method: str, path: str) -> str | None:
    """Which watcher switch this route needs: always, analytics, facebook, or None."""
    verb = (method or "").strip().upper()
    raw = request_path(path)
    if (verb, raw) in _ALWAYS:
        return "always"
    if (verb, raw) in _ANALYTICS_EXACT:
        return "analytics"
    if (verb, raw) in _FACEBOOK_EXACT:
        return "facebook"
    parts = [p for p in raw.split("/") if p]
    if len(parts) >= 3 and parts[0] == "engagement" and parts[1] == "sales" and parts[2].isdigit():
        rest = parts[3:]
        if not rest and verb == "GET":
            return "analytics"
        if rest == ["calendar"] and verb == "GET":
            return "analytics"
        if rest == ["calendar", "facebook"] and verb == "POST":
            return "facebook"
        if rest == ["calendar", "facebook", "preview"] and verb == "POST":
            return "facebook"
        if rest == ["calendar", "facebook", "cancel"] and verb == "POST":
            return "facebook"
        return None
    if (
        verb == "GET"
        and len(parts) == 4
        and parts[0] == "facebook"
        and parts[1] == "posts"
        and parts[2] == "batches"
        and parts[3]
    ):
        return "facebook"
    return None


def enforce_watcher_http(account: Any, method: str, path: str) -> None:
    """Raise when a watcher login hits catalog or a switch that is off."""
    if not is_watcher_account(account):
        return
    feature = watcher_route_feature(method, path)
    if feature is None:
        raise HTTPException(status_code=403, detail=WATCHER_BLOCKED_DETAIL)
    if feature == "analytics" and not bool(getattr(account, "feature_analytics", True)):
        raise HTTPException(status_code=403, detail=ANALYTICS_OFF_DETAIL)
    if feature == "facebook" and not bool(getattr(account, "feature_facebook", True)):
        raise HTTPException(status_code=403, detail=FACEBOOK_OFF_DETAIL)


def features_for_client(account: Any) -> tuple[str, bool, bool]:
    """Plan and switches returned by ``/auth/me``.

    Full-catalog plans always report both switches on. The stored switches
    only limit Auction Watcher accounts.
    """
    plan = plan_of(account) or PLAN_FULL
    if plan != PLAN_WATCHER:
        return plan, True, True
    return (
        PLAN_WATCHER,
        bool(getattr(account, "feature_analytics", True)),
        bool(getattr(account, "feature_facebook", True)),
    )
