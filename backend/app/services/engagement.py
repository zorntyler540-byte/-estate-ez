"""Score a live CT Bids snapshot the way the Oct 15 engagement sheet did.

Inputs are counts and prices only. Bidder names, emails, phones, and addresses
never enter this module. Push rank is computed across one sale's Push lots.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

# A thin bid under this current price is still a social Push. The Oct 15 gold
# ring (12 watchers, 1 bid, $450) stayed Rescue.
PUSH_PRICE_MAX = 100.0

BUCKETS = ("Rescue", "Push", "Protect", "Let Ride")


@dataclass(frozen=True)
class EngagementReading:
    views: int
    watchers: int
    bids: int
    price: float
    reserve: float
    engagement_score: str
    momentum: str
    notes: str
    rescue_candidate: bool
    late_surge: str
    trajectory: str
    bucket: str
    push_rank: int | None = None


def _whole_dollars(price: float) -> str:
    rounded = round(float(price or 0))
    if abs(float(price or 0) - rounded) < 0.001:
        return str(int(rounded))
    text = f"{float(price):.2f}".rstrip("0").rstrip(".")
    return text


def engagement_score(watchers: int, bids: int) -> str:
    if watchers >= 2 and bids >= 2:
        return "Strong"
    if watchers >= 2 or bids >= 2:
        return "Moderate"
    return "Weak"


def momentum_status(views: int, watchers: int, bids: int) -> str:
    building = bids >= 1 and watchers >= 1 and (bids >= 2 or watchers >= 2 or views >= 10)
    if watchers == 0 and bids == 0 and views <= 3:
        direction = "Declining"
    elif building:
        direction = "Building"
    else:
        direction = "Stalled"
    if direction == "Building":
        stable = watchers >= 2 and (watchers >= 3 or bids >= 3)
    elif direction == "Stalled" and watchers == 1 and bids == 1:
        stable = True
    else:
        stable = False
    return f"{direction}-{'Stable' if stable else 'Unstable'}"


def operational_bucket(watchers: int, bids: int, price: float, reserve: float) -> str:
    price = float(price or 0)
    reserve = float(reserve or 0)
    near_reserve = reserve > 0 and price >= reserve * 0.85
    if bids >= 3 and watchers >= 3 and near_reserve and price > 25:
        return "Protect"
    if bids >= 5 and watchers <= 1 and reserve > 0 and price >= reserve:
        return "Let Ride"
    if watchers >= 2 and bids >= 1 and price < PUSH_PRICE_MAX:
        return "Push"
    return "Rescue"


def trajectory_band(views: int, watchers: int, bids: int, bucket: str) -> str:
    if bucket == "Push":
        return "Late Climb"
    if bucket in ("Protect", "Let Ride"):
        return "Steady Finish"
    if bids == 0 and views >= 10:
        return "Low-Conversion Risk"
    if bids >= 1:
        return "Fragile Finish"
    return "Flat Finish"


def late_surge(bucket: str, momentum: str, bids: int, watchers: int) -> str:
    if bucket == "Push" and momentum == "Building-Stable" and bids <= 2:
        return "Yes"
    if bucket in ("Protect", "Let Ride") or watchers == 0:
        return "Unlikely"
    return "Possible"


def momentum_notes(views: int, watchers: int, bids: int, price: float) -> str:
    dollars = _whole_dollars(price)
    if watchers == 0 and bids == 0:
        return f"{views} views; no watcher or bid conversion"
    if bids == 0:
        return f"{watchers} watchers from {views} views; no bids yet"
    if watchers == 0:
        return f"{bids} bids at ${dollars}; no watcher depth"
    if watchers > bids and bids < 3:
        return f"{watchers} watchers versus {bids} bids at ${dollars}; conversion upside"
    if watchers == bids and bids <= 2:
        return f"{bids} bids and {watchers} watchers at ${dollars}; limited depth"
    return f"{bids} bids and {watchers} watchers at ${dollars}; active competition"


def score_item(
    *,
    views: int,
    watchers: int,
    bids: int,
    price: float,
    reserve: float,
) -> EngagementReading:
    views_n = max(0, int(views or 0))
    watchers_n = max(0, int(watchers or 0))
    bids_n = max(0, int(bids or 0))
    price_n = float(price or 0)
    reserve_n = float(reserve or 0)
    bucket = operational_bucket(watchers_n, bids_n, price_n, reserve_n)
    momentum = momentum_status(views_n, watchers_n, bids_n)
    return EngagementReading(
        views=views_n,
        watchers=watchers_n,
        bids=bids_n,
        price=price_n,
        reserve=reserve_n,
        engagement_score=engagement_score(watchers_n, bids_n),
        momentum=momentum,
        notes=momentum_notes(views_n, watchers_n, bids_n, price_n),
        rescue_candidate=bucket == "Rescue",
        late_surge=late_surge(bucket, momentum, bids_n, watchers_n),
        trajectory=trajectory_band(views_n, watchers_n, bids_n, bucket),
        bucket=bucket,
    )


def push_rank_key(reading: EngagementReading) -> tuple:
    """Conversion room first, then attention.

    The Oct 15 sheet's Push Rank is this idea with a few jewelry lots swapped
    when watcher and bid counts match. Live sales use this key.
    """
    late = {"Yes": 0, "Possible": 1, "Unlikely": 2}.get(reading.late_surge, 3)
    return (
        late,
        -(reading.watchers - reading.bids),
        -reading.watchers,
        -reading.views,
        reading.bids,
        reading.price,
    )


def assign_push_ranks(readings: list[EngagementReading]) -> list[EngagementReading]:
    ranked = sorted(
        (row for row in readings if row.bucket == "Push"),
        key=push_rank_key,
    )
    order = {id(row): index for index, row in enumerate(ranked, start=1)}
    out: list[EngagementReading] = []
    for row in readings:
        rank = order.get(id(row))
        if rank is None:
            out.append(row)
        else:
            out.append(
                EngagementReading(
                    views=row.views,
                    watchers=row.watchers,
                    bids=row.bids,
                    price=row.price,
                    reserve=row.reserve,
                    engagement_score=row.engagement_score,
                    momentum=row.momentum,
                    notes=row.notes,
                    rescue_candidate=row.rescue_candidate,
                    late_surge=row.late_surge,
                    trajectory=row.trajectory,
                    bucket=row.bucket,
                    push_rank=rank,
                )
            )
    return out


def _pacific(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(PACIFIC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=PACIFIC)
    return moment.astimezone(PACIFIC)


def calendar_days(captured_at: datetime | None, sale_ends_at: datetime | None) -> list[date]:
    start = _pacific(captured_at).date()
    if sale_ends_at is None:
        end = start + timedelta(days=5)
    else:
        end = _pacific(sale_ends_at).date()
        if end < start:
            end = start
        if (end - start).days > 13:
            end = start + timedelta(days=13)
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days or [start]


_SLOTS: tuple[tuple[time, str, str, str], ...] = (
    (time(9, 0), "Facebook", "Collector find", "Static Photo / Collector Spotlight"),
    (time(12, 15), "Instagram", "Listing spotlight", "Instagram Carousel (3 slides)"),
    (time(16, 30), "FB + IG Stories", "Which would you choose?", "Interactive Story Poll"),
    (time(18, 0), "TikTok", "Would you bid on this?", "TikTok / IG Reel (15–20 sec)"),
)

PREFLIGHT = (
    "Confirm the lot is still open, shipping is unchanged, and the current bid "
    "matches the listing before you post. This row does not publish anything."
)


def _copy_for(
    platform: str,
    *,
    sale_title: str,
    title: str,
    price: float,
    watchers: int,
    item_url: str,
    closing: bool,
) -> str:
    place = (sale_title or "Estate auction").strip()
    dollars = _whole_dollars(price)
    watch = f"{watchers} watcher" if watchers == 1 else f"{watchers} watchers"
    if platform == "Instagram":
        return (
            f"{place}: {title}. Swipe the listing photos. "
            f"{watch} and the current bid is ${dollars}. {item_url}"
        )
    if platform == "FB + IG Stories":
        hook = "Closing countdown" if closing else "Which would you check out first?"
        return (
            f"POLL: {hook}\n"
            f"A: {title}\n"
            f"Current bid ${dollars}. Open the lot and bid if it is still up.\n"
            f"{item_url}"
        )
    if platform == "TikTok":
        return (
            "ON-SCREEN: “Would you bid on this?”\n"
            "CLIP 1 (0–3s): Show the full item.\n"
            "CLIP 2 (3–12s): Details from the listing photos.\n"
            f"{title} — current bid ${dollars}.\n"
            f"{item_url}"
        )
    lead = "Closing day" if closing else "Collector find"
    return (
        f"{place} — {lead}: {title}. {watch}, current bid ${dollars}. "
        f"Listing photos only. {item_url}"
    )


def build_social_calendar(
    push_rows: list[dict],
    *,
    sale_title: str = "",
    captured_at: datetime | None = None,
    sale_ends_at: datetime | None = None,
) -> list[dict]:
    """Planned posts for Push lots only. Status stays Planned."""
    ordered = sorted(push_rows, key=lambda row: int(row.get("push_rank") or 9999))
    if not ordered:
        return []
    days = calendar_days(captured_at, sale_ends_at)
    posts: list[dict] = []
    index = 0
    for day in days:
        closing = sale_ends_at is not None and day == _pacific(sale_ends_at).date()
        for slot_time, platform, hook, fmt in _SLOTS:
            if index >= len(ordered):
                break
            row = ordered[index]
            index += 1
            title = str(row.get("title") or row.get("sku") or "Lot").strip()
            url = str(row.get("item_url") or "").strip()
            posts.append(
                {
                    "order": len(posts) + 1,
                    "post_date": day.isoformat(),
                    "local_time": slot_time.strftime("%H:%M"),
                    "zone": "PT",
                    "platform": platform,
                    "hook": "Closing day: " + hook if closing else hook,
                    "post_type": fmt,
                    "sku": row.get("sku") or "",
                    "title": title,
                    "item_url": url,
                    "lot_id": row.get("lot_id"),
                    "photo_ids": list(row.get("photo_ids") or []),
                    "paste_copy": _copy_for(
                        platform,
                        sale_title=sale_title,
                        title=title,
                        price=float(row.get("price") or 0),
                        watchers=int(row.get("watchers") or 0),
                        item_url=url,
                        closing=closing,
                    ),
                    "status": "Planned",
                    "preflight": PREFLIGHT,
                }
            )
        if index >= len(ordered):
            break
    if sale_ends_at is not None and ordered:
        top = ordered[0]
        end_day = _pacific(sale_ends_at).date()
        if end_day >= days[0]:
            title = str(top.get("title") or top.get("sku") or "Lot").strip()
            url = str(top.get("item_url") or "").strip()
            posts.append(
                {
                    "order": len(posts) + 1,
                    "post_date": end_day.isoformat(),
                    "local_time": "17:40",
                    "zone": "PT",
                    "platform": "FB + IG Stories",
                    "hook": "Closing countdown",
                    "post_type": "Story Countdown Sticker",
                    "sku": top.get("sku") or "",
                    "title": title,
                    "item_url": url,
                    "lot_id": top.get("lot_id"),
                    "photo_ids": list(top.get("photo_ids") or []),
                    "paste_copy": _copy_for(
                        "FB + IG Stories",
                        sale_title=sale_title,
                        title=title,
                        price=float(top.get("price") or 0),
                        watchers=int(top.get("watchers") or 0),
                        item_url=url,
                        closing=True,
                    ),
                    "status": "Planned",
                    "preflight": PREFLIGHT,
                }
            )
    return posts


def reading_dict(reading: EngagementReading, *, captured_at: datetime | None = None) -> dict:
    return {
        "views": reading.views,
        "watchers": reading.watchers,
        "bids": reading.bids,
        "price": reading.price,
        "reserve": reading.reserve,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat() if captured_at else None,
        "bucket": reading.bucket,
        "engagement_score": reading.engagement_score,
        "momentum": reading.momentum,
        "notes": reading.notes,
        "late_surge": reading.late_surge,
        "trajectory": reading.trajectory,
        "rescue_candidate": reading.rescue_candidate,
        "push_rank": reading.push_rank,
    }
