"""Score a live CT Bids snapshot the way the Oct 15 engagement sheet did.

Inputs are counts and prices only. Bidder names, emails, phones, and addresses
never enter this module. Push rank is computed across one sale's Push lots.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.ctbids_urls import ctbids_sale_url, sale_link_line
from app.services.facebook_caption import group_post_copy

PACIFIC = ZoneInfo("America/Los_Angeles")

# Watcher floor for Push once there is at least one bid. Under $100 the floor
# stays 2 so every previous Push lot still qualifies. Above that it rises
# slowly: a $450 lot needs 6 watchers (the Oct 15 gold ring has 12 and 1 bid).
PUSH_WATCHER_TIERS = (
    (100.0, 2),
    (250.0, 4),
    (500.0, 6),
    (1000.0, 8),
)
PUSH_WATCHER_FLOOR_TOP = 10

BUCKETS = ("No interest", "Needs eyes", "Push", "Protect", "Let Ride")
RESCUE_BUCKETS = frozenset({"No interest", "Needs eyes", "Rescue"})
# Rank 1–3 get a second daily slot (09:00 / 16:30 on different days). Rank 1
# also gets the closing-day 17:40 countdown.
PUSH_MULTI_SLOT_RANKS = 3
PREFERRED_PUSH_TIMES = ("09:00", "16:30")


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


def push_watcher_floor(price: float) -> int:
    """Watchers required for Push after Protect / Let Ride, given current price."""
    amount = float(price or 0)
    for limit, floor in PUSH_WATCHER_TIERS:
        if amount < limit:
            return floor
    return PUSH_WATCHER_FLOOR_TOP


def display_bucket(bucket: str, *, watchers: int = 0, bids: int = 0) -> str:
    """Map stored names onto the live buckets. Legacy Rescue splits by interest."""
    name = (bucket or "").strip()
    if name == "Rescue":
        if int(watchers or 0) == 0 and int(bids or 0) == 0:
            return "No interest"
        return "Needs eyes"
    return name


def operational_bucket(watchers: int, bids: int, price: float, reserve: float) -> str:
    """Assign Push / Rescue / Protect / Let Ride.

    Facebook ``/go/`` click counts are stored on ``EngagementOut.facebook_clicks``
    for a later scoring pass. This function does not read them — thresholds stay
    the same.
    """
    price = float(price or 0)
    reserve = float(reserve or 0)
    near_reserve = reserve > 0 and price >= reserve * 0.85
    if bids >= 3 and watchers >= 3 and near_reserve and price > 25:
        return "Protect"
    if bids >= 5 and watchers <= 1 and reserve > 0 and price >= reserve:
        return "Let Ride"
    if bids >= 1 and watchers >= push_watcher_floor(price):
        return "Push"
    if watchers == 0 and bids == 0:
        return "No interest"
    return "Needs eyes"


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
        rescue_candidate=bucket in RESCUE_BUCKETS,
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


def _with_sale_link(text: str, *, item_url: str, auction_url: str = "") -> str:
    line = sale_link_line(ctbids_sale_url(auction_url=auction_url, item_url=item_url))
    if not line or line in text:
        return text
    return f"{text}\n{line}"


def _copy_for(
    platform: str,
    *,
    sale_title: str,
    title: str,
    price: float,
    watchers: int,
    item_url: str,
    closing: bool,
    auction_url: str = "",
) -> str:
    place = (sale_title or "Estate auction").strip()
    dollars = _whole_dollars(price)
    watch = f"{watchers} watcher" if watchers == 1 else f"{watchers} watchers"
    if platform == "Instagram":
        body = (
            f"{place}: {title}. Swipe the listing photos. "
            f"{watch} and the current bid is ${dollars}. {item_url}"
        )
    elif platform == "FB + IG Stories":
        hook = "Closing countdown" if closing else "Which would you check out first?"
        body = (
            f"POLL: {hook}\n"
            f"A: {title}\n"
            f"Current bid ${dollars}. Open the lot and bid if it is still up.\n"
            f"{item_url}"
        )
    elif platform == "TikTok":
        body = (
            "ON-SCREEN: “Would you bid on this?”\n"
            "CLIP 1 (0–3s): Show the full item.\n"
            "CLIP 2 (3–12s): Details from the listing photos.\n"
            f"{title} — current bid ${dollars}.\n"
            f"{item_url}"
        )
    else:
        lead = "Closing day" if closing else "Collector find"
        body = (
            f"{place} — {lead}: {title}. {watch}, current bid ${dollars}. "
            f"Listing photos only. {item_url}"
        )
    return _with_sale_link(body, item_url=item_url, auction_url=auction_url)


def _lot_key(row: dict) -> str:
    return str(row.get("lot_id") or row.get("sku") or "")


def _push_daily_quota(rank: int) -> int:
    if rank <= PUSH_MULTI_SLOT_RANKS:
        return 2
    return 1


def build_social_calendar(
    push_rows: list[dict],
    *,
    needs_eyes_rows: list[dict] | None = None,
    sale_title: str = "",
    captured_at: datetime | None = None,
    sale_ends_at: datetime | None = None,
    auction_url: str = "",
    ctseller_sale_id: int | None = None,
) -> list[dict]:
    """Push lots get multiple slots; Needs eyes fill leftovers lightly. Status stays Planned."""
    ordered = sorted(push_rows, key=lambda row: int(row.get("push_rank") or 9999))
    needs_eyes = list(needs_eyes_rows or [])
    if not ordered and not needs_eyes:
        return []
    days = calendar_days(captured_at, sale_ends_at)
    end_day = _pacific(sale_ends_at).date() if sale_ends_at is not None else None

    class _Cell:
        __slots__ = ("day", "slot_time", "platform", "hook", "fmt", "row")

        def __init__(self, day: date, slot_time: time, platform: str, hook: str, fmt: str):
            self.day = day
            self.slot_time = slot_time
            self.platform = platform
            self.hook = hook
            self.fmt = fmt
            self.row: dict | None = None

    cells: list[_Cell] = []
    for day in days:
        closing = end_day is not None and day == end_day
        for slot_time, platform, hook, fmt in _SLOTS:
            slot_hook = "Closing day: " + hook if closing else hook
            cells.append(_Cell(day, slot_time, platform, slot_hook, fmt))

    used_days: dict[str, set[date]] = {}

    def _skip_days_for(row: dict, *, rank: int | None = None) -> set[date]:
        blocked = set(used_days.get(_lot_key(row), set()))
        if rank == 1 and end_day is not None:
            blocked.add(end_day)
        return blocked

    def _find_cell(row: dict, *, preferred_times: tuple[str, ...], rank: int | None = None) -> _Cell | None:
        blocked = _skip_days_for(row, rank=rank)
        empty = [cell for cell in cells if cell.row is None and cell.day not in blocked]
        if not empty:
            return None
        for stamp in preferred_times:
            for cell in empty:
                if cell.slot_time.strftime("%H:%M") == stamp:
                    return cell
        return empty[0]

    def _place(row: dict, cell: _Cell) -> None:
        cell.row = row
        used_days.setdefault(_lot_key(row), set()).add(cell.day)

    for row in ordered:
        rank = int(row.get("push_rank") or 9999)
        cell = _find_cell(row, preferred_times=(), rank=rank)
        if cell is not None:
            _place(row, cell)

    for row in ordered:
        rank = int(row.get("push_rank") or 9999)
        extra = _push_daily_quota(rank) - 1
        for _ in range(max(0, extra)):
            cell = _find_cell(
                row,
                preferred_times=(*PREFERRED_PUSH_TIMES[::-1], "12:15", "18:00"),
                rank=rank,
            )
            if cell is None:
                break
            _place(row, cell)

    needs_days: set[date] = set()
    for row in needs_eyes:
        empty = [
            cell
            for cell in cells
            if cell.row is None
            and cell.day not in needs_days
            and cell.day not in used_days.get(_lot_key(row), set())
        ]
        if not empty:
            break
        midday = next((cell for cell in empty if cell.slot_time.strftime("%H:%M") in {"12:15", "18:00"}), empty[0])
        _place(row, midday)
        needs_days.add(midday.day)

    posts: list[dict] = []

    def _append(row: dict, *, day: date, local_time: str, platform: str, hook: str, fmt: str, closing: bool) -> None:
        title = str(row.get("title") or row.get("sku") or "Lot").strip()
        url = str(row.get("item_url") or "").strip()
        sale_url = auction_url or ctbids_sale_url(sale_id=ctseller_sale_id, item_url=url)
        posts.append(
            {
                "order": 0,
                "post_date": day.isoformat(),
                "local_time": local_time,
                "zone": "PT",
                "platform": platform,
                "hook": hook,
                "post_type": fmt,
                "sku": row.get("sku") or "",
                "title": title,
                "item_url": url,
                "lot_id": row.get("lot_id"),
                "photo_ids": list(row.get("photo_ids") or []),
                "bucket": row.get("bucket") or "",
                "paste_copy": _copy_for(
                    platform,
                    sale_title=sale_title,
                    title=title,
                    price=float(row.get("price") or 0),
                    watchers=int(row.get("watchers") or 0),
                    item_url=url,
                    closing=closing,
                    auction_url=sale_url,
                ),
                "group_copy": group_post_copy(
                    title=title,
                    hook=hook,
                    item_url=url,
                    close_at=sale_ends_at,
                    sale_url=sale_url,
                ),
                "status": "Planned",
                "preflight": PREFLIGHT,
            }
        )

    for cell in cells:
        if cell.row is None:
            continue
        _append(
            cell.row,
            day=cell.day,
            local_time=cell.slot_time.strftime("%H:%M"),
            platform=cell.platform,
            hook=cell.hook,
            fmt=cell.fmt,
            closing=end_day is not None and cell.day == end_day,
        )

    if end_day is not None and ordered and end_day >= days[0]:
        top = ordered[0]
        top_key = _lot_key(top)
        if end_day not in used_days.get(top_key, set()):
            _append(
                top,
                day=end_day,
                local_time="17:40",
                platform="FB + IG Stories",
                hook="Closing countdown",
                fmt="Story Countdown Sticker",
                closing=True,
            )

    posts.sort(key=lambda post: (post["post_date"], post["local_time"]))
    for index, post in enumerate(posts, start=1):
        post["order"] = index
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
