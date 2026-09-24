"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { LotThumbnail } from "./LotThumbnail";
import { apiFetch } from "../lib/api";
import { accountRoleLabel } from "../lib/roles";

export type TeamActivityPhotoTime = {
  at: string;
  source?: string;
};

export type TeamActivityLot = {
  lot_id: string;
  sale_id: string;
  sku?: string | null;
  auction_title?: string;
  photo_count: number;
  capture_minutes?: number | null;
  first_photo_at?: string;
  last_photo_at?: string;
  has_thumbnail?: boolean;
  timing_source?: string;
  photo_times?: TeamActivityPhotoTime[];
};

export type TeamActivityMember = {
  user_id: string;
  email: string;
  role: string;
  photos_uploaded: number;
  lots_created: number;
  jobs_started: number;
  active_hours: number;
  photos_per_hour: number;
  lots_per_hour: number;
  lots_posted?: number;
  lots_sold?: number;
  lots_unsold?: number;
  lots_live?: number;
  lots_pulled?: number;
  sold_revenue?: number;
  first_photo_at?: string | null;
  last_photo_at?: string | null;
  last_lot_at?: string | null;
  lots_photographed?: number;
  median_minutes_per_lot?: number | null;
  mean_minutes_per_lot?: number | null;
  lots?: TeamActivityLot[];
};

export type TeamActivity = {
  since: string;
  until: string;
  members: TeamActivityMember[];
  totals: {
    photos_uploaded: number;
    lots_created: number;
    jobs_started: number;
    member_count: number;
    active_hours: number;
    photos_per_hour: number;
    lots_per_hour: number;
    lots_posted?: number;
    lots_sold?: number;
    lots_unsold?: number;
    lots_live?: number;
    lots_pulled?: number;
    sold_revenue?: number;
    lots_photographed?: number;
    median_minutes_per_lot?: number | null;
    mean_minutes_per_lot?: number | null;
  };
  sold_items?: SoldDocketItem[];
  sold_by_category?: SoldCategoryRollup[];
};

export type SoldDocketItem = {
  lot_id: string;
  sale_id: string;
  sale_name?: string;
  sku?: string | null;
  auction_title?: string;
  category: string;
  subcategory?: string | null;
  outcome?: string;
  sold_price: number;
  posted_at?: string | null;
  user_email?: string;
  source?: string;
  ctseller_item_id?: number | null;
};

export type SoldCategoryRollup = {
  category: string;
  posted_count?: number;
  sold_count: number;
  unsold_count?: number;
  live_count?: number;
  revenue: number;
  avg_price: number;
  min_price: number;
  max_price: number;
  sell_through?: number;
};

type ActivityPreset = "today" | "7d" | "30d" | "90d" | "all" | "custom";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function localDateInputValue(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function startOfLocalDay(dateStr: string): Date {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1, 0, 0, 0, 0);
}

function startOfNextLocalDay(dateStr: string): Date {
  const start = startOfLocalDay(dateStr);
  start.setDate(start.getDate() + 1);
  return start;
}

function activityWindowIso(
  preset: ActivityPreset,
  customSince: string,
  customUntil: string
): { since: string; until: string } {
  const now = new Date();
  if (preset === "today") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return { since: start.toISOString(), until: now.toISOString() };
  }
  if (preset === "all") {
    return { since: new Date(Date.UTC(2000, 0, 1, 0, 0, 0)).toISOString(), until: now.toISOString() };
  }
  if (preset === "7d" || preset === "30d" || preset === "90d") {
    const days = preset === "7d" ? 7 : preset === "30d" ? 30 : 90;
    return { since: new Date(now.getTime() - days * 86400000).toISOString(), until: now.toISOString() };
  }
  return {
    since: startOfLocalDay(customSince).toISOString(),
    until: startOfNextLocalDay(customUntil).toISOString()
  };
}

function formatActivityWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatActivityMinutes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1) return `${Math.max(1, Math.round(n * 60))}s`;
  if (n < 10) return `${n.toFixed(1)} min`;
  return `${Math.round(n)} min`;
}

function formatClockTime(iso: string, withSeconds: boolean): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {})
  });
}

function formatClockRange(first?: string, last?: string): string {
  if (!first) return "";
  const a = new Date(first);
  const b = new Date(last || first);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return "";
  const withSeconds = Math.abs(b.getTime() - a.getTime()) < 2 * 60 * 1000;
  const start = formatClockTime(first, withSeconds);
  const end = formatClockTime(last || first, withSeconds);
  if (!start) return "";
  if (!end || start === end) return start;
  return `${start}–${end}`;
}

function formatLotShootTime(lot: {
  capture_minutes?: number | null;
  photo_count?: number;
  timing_source?: string;
  first_photo_at?: string;
  last_photo_at?: string;
}): string {
  const n = lot.photo_count || 0;
  const photos = `${n} ${n === 1 ? "photo" : "photos"}`;
  if (lot.timing_source !== "camera") {
    return `${photos} · no shutter time`;
  }
  const range = formatClockRange(lot.first_photo_at, lot.last_photo_at);
  const dur =
    lot.capture_minutes == null ? (n < 2 ? "" : "") : formatActivityMinutes(lot.capture_minutes);
  return [photos, dur ? `${dur} shoot` : "", range].filter(Boolean).join(" · ");
}

function formatGapLabel(ms: number): string {
  const sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}s`;
  const min = sec / 60;
  if (min < 10) return `${min.toFixed(1)} min`;
  return `${Math.round(min)} min`;
}

function formatPhotoShutterList(lot: TeamActivityLot): string {
  const times = (lot.photo_times || []).filter((t) => (t.source || "camera") === "camera");
  if (lot.timing_source !== "camera" || !times.length) return "";
  if (times.length === 1) return `Shutter ${formatClockTime(times[0].at, true)}`;
  const gaps: string[] = [];
  for (let i = 1; i < times.length; i += 1) {
    const a = new Date(times[i - 1].at).getTime();
    const b = new Date(times[i].at).getTime();
    if (Number.isNaN(a) || Number.isNaN(b)) continue;
    gaps.push(formatGapLabel(b - a));
  }
  if (!gaps.length) return "";
  return `Between photos: ${gaps.join(" · ")}`;
}

function formatSaleWindowSummary(activity: TeamActivity): string {
  const items = activity.sold_items || [];
  const sold = items.filter((i) => (i.outcome || "").toLowerCase() === "sold");
  const unsold = items.filter((i) => (i.outcome || "").toLowerCase() === "unsold");
  const live = items.filter((i) => (i.outcome || "").toLowerCase() === "live");
  const hammer = sold.reduce((sum, i) => sum + (Number(i.sold_price) || 0), 0);
  const history = items.filter((i) => (i.source || "") === "ctseller").length;
  const extra = history ? ` · ${history} from CT Seller history` : "";
  return ` · ${items.length} posted · ${sold.length} sold · ${unsold.length} unsold · ${live.length} live${hammer ? ` · ${formatHammer(hammer)} hammer` : ""}${extra}`;
}

function formatSaleSource(source?: string): string {
  if ((source || "").toLowerCase() === "ctseller") return "CT Seller";
  return "Catalog";
}

function formatSaleOutcome(outcome?: string): string {
  const o = (outcome || "").toLowerCase();
  if (o === "sold") return "Sold";
  if (o === "unsold") return "Unsold";
  if (o === "live") return "Live";
  if (o === "pulled") return "Pulled";
  return outcome || "—";
}

function formatPercent(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

function formatActivityHours(n: number | undefined): string {
  if (!n) return "—";
  return n.toFixed(n >= 10 ? 1 : 2);
}

function formatActivityRate(n: number | undefined): string {
  if (!n) return "—";
  return n.toFixed(1);
}

function formatActivityStamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function formatHammer(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function soldDocketCsv(row: TeamActivity): string {
  const headers = [
    "SKU",
    "Title",
    "Category",
    "Outcome",
    "Hammer",
        "Sale",
        "Source",
        "Posted",
        "Catalogger",
        "Lot ID"
  ];
  const lines = [headers.map(csvCell).join(",")];
  for (const item of row.sold_items || []) {
    lines.push(
      [
        item.sku || "",
        item.auction_title || "",
        item.category || "",
        item.outcome || "",
        item.outcome === "sold" ? item.sold_price : "",
        item.sale_name || "",
        item.source || "",
        item.posted_at || "",
        item.user_email || "",
        item.lot_id
      ]
        .map(csvCell)
        .join(",")
    );
  }
  return lines.join("\n");
}

function soldCategoryCsv(row: TeamActivity): string {
  const headers = [
    "Category",
    "Posted",
    "Sold",
    "Unsold",
    "Live",
    "Sell-through",
    "Hammer",
    "Average",
    "Low",
    "High"
  ];
  const lines = [headers.map(csvCell).join(",")];
  for (const cat of row.sold_by_category || []) {
    lines.push(
      [
        cat.category,
        cat.posted_count ?? "",
        cat.sold_count,
        cat.unsold_count ?? "",
        cat.live_count ?? "",
        cat.sell_through ?? "",
        cat.revenue,
        cat.avg_price,
        cat.min_price,
        cat.max_price
      ]
        .map(csvCell)
        .join(",")
    );
  }
  return lines.join("\n");
}

function csvCell(value: string | number): string {
  const s = String(value);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function teamActivityCsv(row: TeamActivity): string {
  const headers = [
    "Person",
    "Role",
    "First upload",
    "Last upload",
    "Last lot",
    "Photos",
    "Lots",
    "Jobs",
    "Active hours",
    "Photos/hr",
    "Lots/hr",
    "Lots photographed",
    "Median shutter min/lot"
  ];
  const lines = [headers.map(csvCell).join(",")];
  for (const m of row.members) {
    lines.push(
      [
        m.email,
        m.role,
        m.first_photo_at || "",
        m.last_photo_at || "",
        m.last_lot_at || "",
        m.photos_uploaded,
        m.lots_created,
        m.jobs_started,
        m.active_hours,
        m.photos_per_hour,
        m.lots_per_hour,
        m.lots_photographed ?? 0,
        m.median_minutes_per_lot ?? ""
      ]
        .map(csvCell)
        .join(",")
    );
  }
  const t = row.totals;
  lines.push(
    [
      "Total",
      "",
      "",
      "",
      "",
      t.photos_uploaded,
      t.lots_created,
      t.jobs_started,
      t.active_hours,
      t.photos_per_hour,
      t.lots_per_hour,
      t.lots_photographed ?? 0,
      t.median_minutes_per_lot ?? ""
    ]
      .map(csvCell)
      .join(",")
  );
  return lines.join("\n");
}

function teamActivityLotsCsv(row: TeamActivity): string {
  const headers = [
    "Person",
    "SKU",
    "Title",
    "Photos",
    "Minutes",
    "Timing",
    "Photo times",
    "First photo",
    "Last photo",
    "Lot ID"
  ];
  const lines = [headers.map(csvCell).join(",")];
  for (const m of row.members) {
    for (const lot of m.lots || []) {
      lines.push(
        [
          m.email,
          lot.sku || "",
          lot.auction_title || "",
          lot.photo_count,
          lot.timing_source === "camera" ? lot.capture_minutes ?? "" : "",
          lot.timing_source || "",
          (lot.photo_times || [])
            .filter((t) => (t.source || "camera") === "camera")
            .map((t) => t.at)
            .join("; "),
          lot.first_photo_at || "",
          lot.last_photo_at || "",
          lot.lot_id
        ]
          .map(csvCell)
          .join(",")
      );
    }
  }
  return lines.join("\n");
}

function downloadCsv(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function TeamActivitySection({
  token,
  viewerUserId
}: {
  token: string;
  viewerUserId?: string;
}) {
  const [activityPreset, setActivityPreset] = useState<ActivityPreset>("today");
  const [activitySinceDate, setActivitySinceDate] = useState(() => localDateInputValue(new Date()));
  const [activityUntilDate, setActivityUntilDate] = useState(() => localDateInputValue(new Date()));
  const [activity, setActivity] = useState<TeamActivity | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [activityError, setActivityError] = useState("");
  const [panel, setPanel] = useState<"catalog" | "sales">("catalog");
  const [saleOutcome, setSaleOutcome] = useState<"all" | "sold" | "unsold" | "live">("all");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyNote, setHistoryNote] = useState("");

  const refreshTeamActivity = useCallback(async () => {
    if (!token) return;
    const { since, until } = activityWindowIso(activityPreset, activitySinceDate, activityUntilDate);
    if (new Date(since).getTime() >= new Date(until).getTime()) {
      setActivityError("Start must be before end");
      setActivity(null);
      return;
    }
    setActivityLoading(true);
    setActivityError("");
    try {
      const qs = new URLSearchParams({ since, until });
      const row = (await apiFetch(`/team/activity?${qs.toString()}`, token)) as TeamActivity;
      setActivity(row);
    } catch (e) {
      setActivityError(e instanceof Error ? e.message : "Could not load team activity");
      setActivity(null);
    } finally {
      setActivityLoading(false);
    }
  }, [token, activityPreset, activitySinceDate, activityUntilDate]);

  const pullCtSellerHistory = useCallback(async () => {
    if (!token) return;
    const { since, until } = activityWindowIso(activityPreset, activitySinceDate, activityUntilDate);
    setHistoryBusy(true);
    setHistoryNote("");
    setActivityError("");
    try {
      const qs = new URLSearchParams({ since, until });
      const sync = (await apiFetch(`/team/activity/ctseller-history?${qs.toString()}`, token, {
        method: "POST"
      })) as { ended_sales?: number; open_sales?: number; items_upserted?: number };
      await refreshTeamActivity();
      const ended = sync.ended_sales ?? 0;
      const open = sync.open_sales ?? 0;
      const items = sync.items_upserted ?? 0;
      setHistoryNote(
        `Pulled ${ended} closed sale${ended === 1 ? "" : "s"}${open ? ` · ${open} live` : ""} · ${items} items from CT Seller.${
          activityPreset === "all" && ended >= 120
            ? " Pull again to load older auctions."
            : ""
        }`
      );
    } catch (e) {
      setActivityError(e instanceof Error ? e.message : "Could not pull CT Seller history");
    } finally {
      setHistoryBusy(false);
    }
  }, [token, activityPreset, activitySinceDate, activityUntilDate, refreshTeamActivity]);

  useEffect(() => {
    if (!token) return;
    if (activityPreset === "custom") return;
    void refreshTeamActivity();
  }, [token, activityPreset, refreshTeamActivity]);

  return (
    <section className="ct-card ct-card--padded">
      <h2 className="ct-section-title" style={{ marginTop: 0 }}>
        {panel === "sales" ? "Sale results" : "Cataloging"}
      </h2>
      <p className="ct-lead" style={{ marginTop: "0.35rem", marginBottom: "0.5rem", fontSize: "0.95rem" }}>
        {panel === "sales"
          ? "Sold, unsold, and hammer by CT Bids category — including closed auctions after you pull CT Seller history."
          : "Photos, lots, and shutter time between pictures. Upload clock is not used here."}
      </p>
      <div className="ct-seg" role="group" aria-label="Team analytics view" style={{ marginBottom: "0.75rem" }}>
        <button type="button" disabled={panel === "catalog"} onClick={() => setPanel("catalog")}>
          Cataloging
        </button>
        <button type="button" disabled={panel === "sales"} onClick={() => setPanel("sales")}>
          Sale results
        </button>
      </div>
      <details className="ct-help-details" style={{ marginBottom: "0.75rem" }}>
        <summary>How this is counted</summary>
        <div className="ct-help-details__body">
          {panel === "sales" ? (
            <>
              Sale results are lots posted to CT Bids in this date range, with the current CT Seller
              status. <strong>Sold</strong> includes hammer. <strong>Unsold</strong> did not sell.{" "}
              <strong>Live</strong> is still bidding. Categories show sell-through (sold ÷ posted)
              and hammer stats for sold lots. Pull <strong>CT Seller history</strong> to include
              closed auctions from seller.ctbids.com (sold, unsold, category, hammer), not only lots
              already in this catalog. <strong>All time</strong> runs from 2000 through now. The first
              history pull can take a minute; pull again if older auctions are still missing. Today is
              often empty until a sale closes — use Last 7 / 30 / 90 days or All time, then pull
              history.
            </>
          ) : (
            <>
              Credit goes to the login that uploaded the photo or started the catalog job. Lot cards
              use <strong>camera shutter</strong> only (EXIF or phone shutter), including time
              between photos. If the phone did not send shutter time, the card says no shutter time
              — we do not show upload bursts here. <strong>Min/lot</strong> is first-to-last shutter
              on that lot. First/last photo columns in the table are still when files landed on the
              server.
            </>
          )}
        </div>
      </details>
      <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "center", marginBottom: "0.75rem" }}>
        <div className="ct-seg" role="group" aria-label="Activity timeframe">
          {(
            [
              ["today", "Today"],
              ["7d", "Last 7 days"],
              ["30d", "Last 30 days"],
              ["90d", "Last 90 days"],
              ["all", "All time"],
              ["custom", "Custom"]
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              disabled={activityPreset === id}
              onClick={() => setActivityPreset(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="ct-btn ct-btn--ghost"
          onClick={() => void refreshTeamActivity()}
          disabled={activityLoading}
        >
          {activityLoading ? "Refreshing…" : "Refresh"}
        </button>
        {activity ? (
          panel === "sales" ? (
            <>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() => void pullCtSellerHistory()}
                disabled={historyBusy || activityLoading}
              >
                {historyBusy ? "Pulling CT Seller…" : "Pull CT Seller history"}
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() => downloadCsv(soldDocketCsv(activity), `sale-results-${activityPreset}.csv`)}
              >
                Download lots CSV
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() =>
                  downloadCsv(soldCategoryCsv(activity), `sale-categories-${activityPreset}.csv`)
                }
              >
                Download categories CSV
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() => downloadCsv(teamActivityCsv(activity), `team-activity-${activityPreset}.csv`)}
              >
                Download CSV
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                onClick={() =>
                  downloadCsv(teamActivityLotsCsv(activity), `team-activity-lots-${activityPreset}.csv`)
                }
              >
                Download lots CSV
              </button>
            </>
          )
        ) : null}
      </div>
      {activityPreset === "custom" ? (
        <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "flex-end", marginBottom: "0.75rem" }}>
          <label style={{ margin: 0 }}>
            <span className="ct-label">From</span>
            <input
              className="ct-input"
              type="date"
              value={activitySinceDate}
              onChange={(e) => setActivitySinceDate(e.target.value)}
            />
          </label>
          <label style={{ margin: 0 }}>
            <span className="ct-label">Through</span>
            <input
              className="ct-input"
              type="date"
              value={activityUntilDate}
              onChange={(e) => setActivityUntilDate(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="ct-btn ct-btn--primary"
            onClick={() => void refreshTeamActivity()}
            disabled={activityLoading || !activitySinceDate || !activityUntilDate}
          >
            Apply dates
          </button>
        </div>
      ) : null}
      {activityError ? <div className="ct-banner ct-banner--error">{activityError}</div> : null}
      {activity ? (
        <>
          <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.65rem", fontSize: "0.86rem" }}>
            {formatActivityWhen(activity.since)} – {formatActivityWhen(activity.until)}
            {panel === "sales" ? formatSaleWindowSummary(activity) : ""}
            {panel === "catalog"
              ? `${activity.totals.photos_uploaded} photos · ${activity.totals.lots_photographed ?? 0} lots shot${activity.totals.median_minutes_per_lot != null ? ` · median ${formatActivityMinutes(activity.totals.median_minutes_per_lot)} shutter/lot` : ""}`
              : ""}
          </p>
          {historyNote && panel === "sales" ? (
            <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.65rem", fontSize: "0.86rem" }}>
              {historyNote}
            </p>
          ) : null}
          {panel === "catalog" ? (
            <>
              <div className="ct-table-wrap" style={{ marginBottom: 0 }}>
                <table className="ct-table">
                  <thead>
                    <tr>
                      <th>Person</th>
                      <th>First upload</th>
                      <th>Last upload</th>
                      <th>Last lot</th>
                      <th className="ct-table__num">Photos</th>
                      <th className="ct-table__num">Lots</th>
                      <th className="ct-table__num">Jobs</th>
                      <th className="ct-table__num">Active hrs</th>
                      <th className="ct-table__num">Photos/hr</th>
                      <th className="ct-table__num">Lots/hr</th>
                      <th className="ct-table__num">Shot</th>
                      <th className="ct-table__num">Min/lot</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activity.members.map((m) => (
                      <tr key={m.user_id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{m.email}</div>
                          <div className="ct-muted" style={{ fontSize: "0.8rem" }}>
                            {accountRoleLabel(m.role)}
                            {m.user_id === viewerUserId ? " · you" : ""}
                          </div>
                        </td>
                        <td>{formatActivityStamp(m.first_photo_at)}</td>
                        <td>{formatActivityStamp(m.last_photo_at)}</td>
                        <td>{formatActivityStamp(m.last_lot_at)}</td>
                        <td className="ct-table__num">{m.photos_uploaded}</td>
                        <td className="ct-table__num">{m.lots_created}</td>
                        <td className="ct-table__num">{m.jobs_started}</td>
                        <td className="ct-table__num">{formatActivityHours(m.active_hours)}</td>
                        <td className="ct-table__num">{formatActivityRate(m.photos_per_hour)}</td>
                        <td className="ct-table__num">{formatActivityRate(m.lots_per_hour)}</td>
                        <td className="ct-table__num">{m.lots_photographed ?? 0}</td>
                        <td className="ct-table__num">
                          {formatActivityMinutes(m.median_minutes_per_lot)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th>
                        Total · {activity.totals.member_count}{" "}
                        {activity.totals.member_count === 1 ? "login" : "logins"}
                      </th>
                      <th>—</th>
                      <th>—</th>
                      <th>—</th>
                      <th className="ct-table__num">{activity.totals.photos_uploaded}</th>
                      <th className="ct-table__num">{activity.totals.lots_created}</th>
                      <th className="ct-table__num">{activity.totals.jobs_started}</th>
                      <th className="ct-table__num">
                        {formatActivityHours(activity.totals.active_hours)}
                      </th>
                      <th className="ct-table__num">
                        {formatActivityRate(activity.totals.photos_per_hour)}
                      </th>
                      <th className="ct-table__num">
                        {formatActivityRate(activity.totals.lots_per_hour)}
                      </th>
                      <th className="ct-table__num">{activity.totals.lots_photographed ?? 0}</th>
                      <th className="ct-table__num">
                        {formatActivityMinutes(activity.totals.median_minutes_per_lot)}
                      </th>
                    </tr>
                  </tfoot>
                </table>
              </div>
              {activity.members.some((m) => (m.lots || []).length > 0) ? (
                <div style={{ marginTop: "1rem" }}>
                  {activity.members
                    .filter((m) => (m.lots_photographed || 0) > 0)
                    .map((m) => (
                      <details key={m.user_id} open style={{ marginBottom: "0.85rem" }}>
                        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                          {m.email} · {m.lots_photographed}{" "}
                          {(m.lots_photographed || 0) === 1 ? "lot" : "lots"} photographed
                          {m.median_minutes_per_lot != null
                            ? ` · median ${formatActivityMinutes(m.median_minutes_per_lot)} shutter`
                            : ""}
                        </summary>
                        <div
                          style={{
                            display: "flex",
                            flexWrap: "wrap",
                            gap: "0.65rem",
                            marginTop: "0.65rem"
                          }}
                        >
                          {(m.lots || []).map((lot) => (
                            <div
                              key={lot.lot_id}
                              style={{
                                width: 148,
                                display: "flex",
                                flexDirection: "column",
                                gap: 4
                              }}
                            >
                              <Link
                                href={`/lots/${lot.lot_id}`}
                                style={{ textDecoration: "none", color: "inherit" }}
                              >
                                <LotThumbnail
                                  lotId={lot.lot_id}
                                  token={token}
                                  hasThumbnail={Boolean(lot.has_thumbnail)}
                                  size={128}
                                  maxPx={220}
                                />
                                <div style={{ fontWeight: 700, fontSize: "0.82rem", marginTop: 4 }}>
                                  {lot.sku?.trim() || "No SKU"}
                                </div>
                                <div className="ct-muted" style={{ fontSize: "0.75rem" }}>
                                  {formatLotShootTime(lot)}
                                </div>
                                {formatPhotoShutterList(lot) ? (
                                  <div
                                    className="ct-muted"
                                    style={{ fontSize: "0.68rem", lineHeight: 1.35, marginTop: 2 }}
                                  >
                                    {formatPhotoShutterList(lot)}
                                  </div>
                                ) : null}
                              </Link>
                              <Link
                                href={`/lots/${lot.lot_id}`}
                                className="ct-btn ct-btn--ghost"
                                style={{
                                  display: "inline-flex",
                                  justifyContent: "center",
                                  width: "100%",
                                  padding: "0.28rem 0.4rem",
                                  fontSize: "0.78rem"
                                }}
                              >
                                Open lot
                              </Link>
                            </div>
                          ))}
                        </div>
                        {(m.lots_photographed || 0) > (m.lots || []).length ? (
                          <p className="ct-muted" style={{ fontSize: "0.8rem", margin: "0.5rem 0 0" }}>
                            Showing {(m.lots || []).length} of {m.lots_photographed}. Narrow the dates
                            to see the rest.
                          </p>
                        ) : null}
                      </details>
                    ))}
                </div>
              ) : (
                <p className="ct-muted" style={{ marginTop: "1rem", marginBottom: 0 }}>
                  No photographed lots in this window.
                </p>
              )}
            </>
          ) : (
            <SaleResultsPanel
              activity={activity}
              saleOutcome={saleOutcome}
              setSaleOutcome={setSaleOutcome}
              historyBusy={historyBusy}
              onPullHistory={() => void pullCtSellerHistory()}
            />
          )}
        </>
      ) : activityLoading ? (
        <p className="ct-muted" style={{ marginBottom: 0 }}>
          Loading team activity…
        </p>
      ) : !activityError ? (
        <p className="ct-muted" style={{ marginBottom: 0 }}>
          {activityPreset === "custom" ? "Pick dates and apply to load counts." : "No activity data yet."}
        </p>
      ) : null}
    </section>
  );
}

function SaleResultsPanel({
  activity,
  saleOutcome,
  setSaleOutcome,
  historyBusy,
  onPullHistory
}: {
  activity: TeamActivity;
  saleOutcome: "all" | "sold" | "unsold" | "live";
  setSaleOutcome: (next: "all" | "sold" | "unsold" | "live") => void;
  historyBusy: boolean;
  onPullHistory: () => void;
}) {
  const items = activity.sold_items || [];
  const categories = activity.sold_by_category || [];
  const filtered =
    saleOutcome === "all" ? items : items.filter((row) => (row.outcome || "").toLowerCase() === saleOutcome);

  if (!items.length) {
    return (
      <div className="ct-banner" style={{ marginBottom: 0 }}>
        <p style={{ margin: "0 0 0.75rem" }}>
          No lots in this date range yet. Closed CT Seller auctions are not in this catalog until you
          pull them. Switch to <strong>Last 7 days</strong>, <strong>Last 30 days</strong>, or{" "}
          <strong>All time</strong>, then Pull CT Seller history (needs CT Seller connected on Account).
        </p>
        <button
          type="button"
          className="ct-btn ct-btn--primary"
          onClick={onPullHistory}
          disabled={historyBusy}
        >
          {historyBusy ? "Pulling CT Seller…" : "Pull CT Seller history"}
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="ct-seg" role="group" aria-label="Sale outcome filter" style={{ marginBottom: "0.75rem" }}>
        {(
          [
            ["all", "All"],
            ["sold", "Sold"],
            ["unsold", "Unsold"],
            ["live", "Live"]
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" disabled={saleOutcome === id} onClick={() => setSaleOutcome(id)}>
            {label}
          </button>
        ))}
      </div>
      {categories.length ? (
        <div className="ct-table-wrap" style={{ marginBottom: "1rem" }}>
          <table className="ct-table">
            <thead>
              <tr>
                <th>Category</th>
                <th className="ct-table__num">Posted</th>
                <th className="ct-table__num">Sold</th>
                <th className="ct-table__num">Unsold</th>
                <th className="ct-table__num">Live</th>
                <th className="ct-table__num">Sell-through</th>
                <th className="ct-table__num">Hammer</th>
                <th className="ct-table__num">Average</th>
                <th className="ct-table__num">Low</th>
                <th className="ct-table__num">High</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((cat) => (
                <tr key={cat.category}>
                  <td>{cat.category}</td>
                  <td className="ct-table__num">{cat.posted_count ?? 0}</td>
                  <td className="ct-table__num">{cat.sold_count}</td>
                  <td className="ct-table__num">{cat.unsold_count ?? 0}</td>
                  <td className="ct-table__num">{cat.live_count ?? 0}</td>
                  <td className="ct-table__num">{formatPercent(cat.sell_through)}</td>
                  <td className="ct-table__num">{cat.revenue ? formatHammer(cat.revenue) : "—"}</td>
                  <td className="ct-table__num">{cat.avg_price ? formatHammer(cat.avg_price) : "—"}</td>
                  <td className="ct-table__num">{cat.min_price ? formatHammer(cat.min_price) : "—"}</td>
                  <td className="ct-table__num">{cat.max_price ? formatHammer(cat.max_price) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="ct-table-wrap" style={{ marginBottom: 0 }}>
        <table className="ct-table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Title</th>
              <th>Category</th>
              <th>Outcome</th>
              <th className="ct-table__num">Hammer</th>
              <th>Sale</th>
              <th>Source</th>
              <th>Posted</th>
              <th>Catalogger</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length ? (
              filtered.map((item) => (
                <tr key={`${item.source || "catalog"}-${item.ctseller_item_id || item.lot_id}`}>
                  <td style={{ fontWeight: 600 }}>{item.sku?.trim() || "—"}</td>
                  <td>{item.auction_title || "—"}</td>
                  <td>
                    {item.category}
                    {item.subcategory ? (
                      <div className="ct-muted" style={{ fontSize: "0.78rem" }}>
                        {item.subcategory}
                      </div>
                    ) : null}
                  </td>
                  <td>{formatSaleOutcome(item.outcome)}</td>
                  <td className="ct-table__num">
                    {item.outcome === "sold" ? formatHammer(item.sold_price) : "—"}
                  </td>
                  <td>{item.sale_name || "—"}</td>
                  <td>{formatSaleSource(item.source)}</td>
                  <td>{formatActivityStamp(item.posted_at)}</td>
                  <td>{item.user_email || "—"}</td>
                  <td>
                    {item.lot_id ? (
                      <Link href={`/lots/${item.lot_id}`} className="ct-btn ct-btn--ghost">
                        Open
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={10} className="ct-muted">
                  No {saleOutcome} lots in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
