"""Franchise-local time for the Push calendar, captions, and click-day buckets."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Los_Angeles"

# Legacy calendar rows said "PT"; keep reading them.
_ALIASES = {"PT": DEFAULT_TIMEZONE, "": DEFAULT_TIMEZONE}

_SHORT_LABELS = {
    "America/Los_Angeles": "PT",
    "America/Denver": "MT",
    "America/Boise": "MT",
    "America/Phoenix": "MST",
    "America/Chicago": "CT",
    "America/New_York": "ET",
    "America/Detroit": "ET",
    "America/Indiana/Indianapolis": "ET",
    "America/Anchorage": "AKT",
    "Pacific/Honolulu": "HT",
}

US_TIMEZONES: tuple[tuple[str, str], ...] = (
    ("America/New_York", "Eastern"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Arizona"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
)


def normalize_timezone(name: str | None) -> str | None:
    """IANA name if valid, else None."""
    raw = (name or "").strip()
    raw = _ALIASES.get(raw, raw)
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return raw


def zone_name(name: str | None) -> str:
    return normalize_timezone(name) or DEFAULT_TIMEZONE


def zone_for(name: str | None) -> ZoneInfo:
    return ZoneInfo(zone_name(name))


def account_zone_name(account) -> str:
    return zone_name(getattr(account, "timezone", None))


def zone_label(name: str | None, *, at: datetime | None = None) -> str:
    """Short label for UI/caption copy: PT, CT, ET … or the zone's own abbreviation."""
    key = zone_name(name)
    if key in _SHORT_LABELS:
        return _SHORT_LABELS[key]
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(key)).tzname() or key


def to_local(moment: datetime | None, name: str | None) -> datetime:
    tz = zone_for(name)
    if moment is None:
        return datetime.now(tz)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz)
