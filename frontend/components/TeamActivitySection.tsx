"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { LotThumbnail } from "./LotThumbnail";
import { apiFetch } from "../lib/api";
import { accountRoleLabel } from "../lib/roles";

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
  sold_price: number;
  posted_at?: string | null;
  user_email?: string;
};

export type SoldCategoryRollup = {
  category: string;
  sold_count: number;
  revenue: number;
  avg_price: number;
  min_price: number;
  max_price: number;
};

type ActivityPreset = "today" | "7d" | "30d" | "90d" | "custom";

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

function formatLotShootTime(lot: {
  capture_minutes?: number | null;
  photo_count?: number;
  timing_source?: string;
}): string {
  if (lot.timing_source === "upload_burst") return "uploaded together";
  if (lot.timing_source !== "camera" && lot.capture_minutes != null && lot.capture_minutes <= 0.75) {
    return "uploaded together";
  }
  if (lot.capture_minutes == null) {
    return (lot.photo_count || 0) < 2 ? "—" : "uploaded together";
  }
  const core = formatActivityMinutes(lot.capture_minutes);
  if (lot.timing_source === "camera") return core;
  return `${core} (upload)`;
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

function formatActivityMoney(n: number | undefined): string {
  if (!n) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
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
  const headers = ["SKU", "Title", "Category", "Hammer", "Sale", "Posted", "Catalogger", "Lot ID"];
  const lines = [headers.map(csvCell).join(",")];
  for (const item of row.sold_items || []) {
    lines.push(
      [
        item.sku || "",
        item.auction_title || "",
        item.category || "",
        item.sold_price,
        item.sale_name || "",
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
  const headers = ["Category", "Sold", "Hammer", "Average", "Low", "High"];
  const lines = [headers.map(csvCell).join(",")];
  for (const cat of row.sold_by_category || []) {
    lines.push(
      [cat.category, cat.sold_count, cat.revenue, cat.avg_price, cat.min_price, cat.max_price]
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
    "First photo",
    "Last photo",
    "Last lot",
    "Photos",
    "Lots",
    "Posted",
    "Sold",
    "Unsold",
    "Live",
    "Hammer",
    "Jobs",
    "Active hours",
    "Photos/hr",
    "Lots/hr",
    "Lots photographed",
    "Median min/lot"
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
        m.lots_posted ?? 0,
        m.lots_sold ?? 0,
        m.lots_unsold ?? 0,
        m.lots_live ?? 0,
        m.sold_revenue ?? 0,
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
      t.lots_posted ?? 0,
      t.lots_sold ?? 0,
      t.lots_unsold ?? 0,
      t.lots_live ?? 0,
      t.sold_revenue ?? 0,
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
  const headers = ["Person", "SKU", "Title", "Photos", "Minutes", "First photo", "Last photo", "Lot ID"];
  const lines = [headers.map(csvCell).join(",")];
  for (const m of row.members) {
    for (const lot of m.lots || []) {
      lines.push(
        [
          m.email,
          lot.sku || "",
          lot.auction_title || "",
          lot.photo_count,
          lot.timing_source === "upload_burst" || lot.capture_minutes == null
            ? lot.photo_count >= 2
              ? "uploaded together"
              : ""
            : lot.capture_minutes,
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

  useEffect(() => {
    if (!token) return;
    if (activityPreset === "custom") return;
    void refreshTeamActivity();
  }, [token, activityPreset, refreshTeamActivity]);

  return (
    <section className="ct-card ct-card--padded">
      <h2 className="ct-section-title" style={{ marginTop: 0 }}>
        Team activity
      </h2>
      <p className="ct-lead" style={{ marginTop: "0.35rem", marginBottom: "0.5rem", fontSize: "0.95rem" }}>
        Photos, lots, catalog jobs, CT Bids posted/sold, and time-per-lot for pictures each login
        actually uploaded.
      </p>
      <details className="ct-help-details" style={{ marginBottom: "0.75rem" }}>
        <summary>How this is counted</summary>
        <div className="ct-help-details__body">
          Credit goes to the login that uploaded the photo or started the catalog job. Deleted photos
          drop off. Shared logins look like one person. <strong>Posted / Sold / Unsold / Live</strong>{" "}
          are lots that login created and that went to CT Bids in this window (post time, or catalog
          time if the post stamp is missing). Hammer is the sum of reported sold prices.{" "}
          <strong>First photo</strong>, <strong>Last photo</strong>, and <strong>Last lot</strong> are
          when that login&apos;s first/last photo or last lot in this window landed in the catalog
          (upload time, not the camera shutter). On Today that is that day; on 7d/30d/90d it is last
          in the selected range. Photos/hr and Lots/hr use <strong>active hours</strong>: each UTC
          day with a photo or lot is the time from that person&apos;s first event to their last that
          day, at least one hour. Totals divide team counts by the sum of those person-hours. This is
          cataloging pace, not hours on the clock. <strong>Min/lot</strong> is first-to-last photo on
          that lot (the densest cluster; a gap over 15 minutes is a later add-on, not one shoot). One
          photo cannot measure duration. Phone Sync often writes the whole lot in a few seconds —
          that is upload time, not the shoot, so those lots say <strong>uploaded together</strong>{" "}
          until the phone sends shutter time. Folder JPEGs with camera EXIF use that clock. The{" "}
          <strong>sold docket</strong> lists every lot posted in this window that CT Seller now
          reports as sold — hammer price and CT Bids category — so you can see what actually
          moved. Refresh after a sale closes so statuses and prices catch up.
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
            {(activity.sold_items || []).length > 0 ? (
              <>
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() => downloadCsv(soldDocketCsv(activity), `sold-docket-${activityPreset}.csv`)}
                >
                  Download sold CSV
                </button>
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() =>
                    downloadCsv(soldCategoryCsv(activity), `sold-categories-${activityPreset}.csv`)
                  }
                >
                  Download categories CSV
                </button>
              </>
            ) : null}
          </>
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
            {activity.totals.lots_posted
              ? ` · ${activity.totals.lots_posted} posted · ${activity.totals.lots_sold ?? 0} sold`
              : ""}
            {activity.totals.sold_revenue
              ? ` · ${formatActivityMoney(activity.totals.sold_revenue)} hammer`
              : ""}
            {activity.totals.median_minutes_per_lot != null
              ? ` · median ${formatActivityMinutes(activity.totals.median_minutes_per_lot)}/lot`
              : ""}
          </p>
          <div className="ct-table-wrap" style={{ marginBottom: 0 }}>
            <table className="ct-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>First photo</th>
                  <th>Last photo</th>
                  <th>Last lot</th>
                  <th className="ct-table__num">Photos</th>
                  <th className="ct-table__num">Lots</th>
                  <th className="ct-table__num">Posted</th>
                  <th className="ct-table__num">Sold</th>
                  <th className="ct-table__num">Unsold</th>
                  <th className="ct-table__num">Live</th>
                  <th className="ct-table__num">Hammer</th>
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
                    <td className="ct-table__num">{m.lots_posted ?? 0}</td>
                    <td className="ct-table__num">{m.lots_sold ?? 0}</td>
                    <td className="ct-table__num">{m.lots_unsold ?? 0}</td>
                    <td className="ct-table__num">{m.lots_live ?? 0}</td>
                    <td className="ct-table__num">{formatActivityMoney(m.sold_revenue)}</td>
                    <td className="ct-table__num">{m.jobs_started}</td>
                    <td className="ct-table__num">{formatActivityHours(m.active_hours)}</td>
                    <td className="ct-table__num">{formatActivityRate(m.photos_per_hour)}</td>
                    <td className="ct-table__num">{formatActivityRate(m.lots_per_hour)}</td>
                    <td className="ct-table__num">{m.lots_photographed ?? 0}</td>
                    <td className="ct-table__num">{formatActivityMinutes(m.median_minutes_per_lot)}</td>
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
                  <th className="ct-table__num">{activity.totals.lots_posted ?? 0}</th>
                  <th className="ct-table__num">{activity.totals.lots_sold ?? 0}</th>
                  <th className="ct-table__num">{activity.totals.lots_unsold ?? 0}</th>
                  <th className="ct-table__num">{activity.totals.lots_live ?? 0}</th>
                  <th className="ct-table__num">{formatActivityMoney(activity.totals.sold_revenue)}</th>
                  <th className="ct-table__num">{activity.totals.jobs_started}</th>
                  <th className="ct-table__num">{formatActivityHours(activity.totals.active_hours)}</th>
                  <th className="ct-table__num">{formatActivityRate(activity.totals.photos_per_hour)}</th>
                  <th className="ct-table__num">{formatActivityRate(activity.totals.lots_per_hour)}</th>
                  <th className="ct-table__num">{activity.totals.lots_photographed ?? 0}</th>
                  <th className="ct-table__num">
                    {formatActivityMinutes(activity.totals.median_minutes_per_lot)}
                  </th>
                </tr>
              </tfoot>
            </table>
          </div>
          {(activity.sold_by_category || []).length > 0 || (activity.sold_items || []).length > 0 ? (
            <div style={{ marginTop: "1.25rem" }}>
              <h3 className="ct-section-title" style={{ fontSize: "1.05rem", marginBottom: "0.35rem" }}>
                Sold docket
              </h3>
              <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.65rem", fontSize: "0.86rem" }}>
                {(activity.sold_items || []).length} sold
                {activity.totals.sold_revenue
                  ? ` · ${formatHammer(activity.totals.sold_revenue)} hammer`
                  : ""}
                . Categories ranked by hammer. Item list is highest hammer first.
              </p>
              {(activity.sold_by_category || []).length > 0 ? (
                <div className="ct-table-wrap" style={{ marginBottom: "0.85rem" }}>
                  <table className="ct-table">
                    <thead>
                      <tr>
                        <th>Category</th>
                        <th className="ct-table__num">Sold</th>
                        <th className="ct-table__num">Hammer</th>
                        <th className="ct-table__num">Avg</th>
                        <th className="ct-table__num">Low</th>
                        <th className="ct-table__num">High</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(activity.sold_by_category || []).map((cat) => (
                        <tr key={cat.category}>
                          <td style={{ fontWeight: 600 }}>{cat.category}</td>
                          <td className="ct-table__num">{cat.sold_count}</td>
                          <td className="ct-table__num">{formatHammer(cat.revenue)}</td>
                          <td className="ct-table__num">{formatHammer(cat.avg_price)}</td>
                          <td className="ct-table__num">{formatHammer(cat.min_price)}</td>
                          <td className="ct-table__num">{formatHammer(cat.max_price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {(activity.sold_items || []).length > 0 ? (
                <div className="ct-table-wrap">
                  <table className="ct-table">
                    <thead>
                      <tr>
                        <th>SKU</th>
                        <th>Item</th>
                        <th>Category</th>
                        <th className="ct-table__num">Hammer</th>
                        <th>Sale</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {(activity.sold_items || []).map((item) => (
                        <tr key={item.lot_id}>
                          <td style={{ fontWeight: 700, whiteSpace: "nowrap" }}>
                            {item.sku?.trim() || "—"}
                          </td>
                          <td>{item.auction_title || "—"}</td>
                          <td>
                            {item.category}
                            {item.subcategory ? (
                              <div className="ct-muted" style={{ fontSize: "0.78rem" }}>
                                {item.subcategory}
                              </div>
                            ) : null}
                          </td>
                          <td className="ct-table__num">{formatHammer(item.sold_price)}</td>
                          <td>
                            {item.sale_name || "—"}
                            {item.posted_at ? (
                              <div className="ct-muted" style={{ fontSize: "0.78rem" }}>
                                {formatActivityStamp(item.posted_at)}
                              </div>
                            ) : null}
                          </td>
                          <td>
                            <Link href={`/lots/${item.lot_id}`} className="ct-btn ct-btn--ghost">
                              Open
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : activity.totals.lots_posted ? (
            <p className="ct-muted" style={{ marginTop: "1rem", marginBottom: 0 }}>
              No sold lots in this window yet. After the sale closes and CT Seller statuses
              sync, they land here with hammer and category.
            </p>
          ) : null}
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
                        ? ` · median ${formatActivityMinutes(m.median_minutes_per_lot)}`
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
                            width: 128,
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
                              {lot.photo_count} {lot.photo_count === 1 ? "photo" : "photos"} ·{" "}
                              {formatLotShootTime(lot)}
                            </div>
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
                        Showing {(m.lots || []).length} of {m.lots_photographed}. Narrow the dates to
                        see the rest.
                      </p>
                    ) : null}
                  </details>
                ))}
            </div>
          ) : null}
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
