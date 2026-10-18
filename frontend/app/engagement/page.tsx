"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../../components/AppShell";
import {
  FacebookPagePostModal,
  type FacebookCalendarSlot,
  type FacebookPostSource
} from "../../components/FacebookPagePostModal";
import { LotThumbnail } from "../../components/LotThumbnail";
import { apiFetch } from "../../lib/api";
import { isOwnerRole, rememberAccountRole, storedAccountRole } from "../../lib/roles";

type SaleSummary = {
  ctseller_sale_id: number;
  sale_title: string;
  auction_url: string;
  sale_ends_at?: string | null;
  captured_at?: string | null;
  item_count: number;
  rescue: number;
  push: number;
  protect: number;
  let_ride: number;
};

type QueueItem = {
  sku: string;
  title: string;
  views: number;
  watchers: number;
  bids: number;
  price: number;
  reserve: number;
  item_url: string;
  bucket: string;
  engagement_score: string;
  momentum: string;
  notes: string;
  late_surge: string;
  push_rank?: number | null;
  lot_id?: string | null;
  has_thumbnail: boolean;
  photo_ids: string[];
  ctseller_item_id?: number | null;
};

type SaleDetail = SaleSummary & { items: QueueItem[] };

type CalendarPost = {
  order: number;
  post_date: string;
  local_time: string;
  zone: string;
  platform: string;
  hook: string;
  post_type: string;
  sku: string;
  title: string;
  item_url: string;
  lot_id?: string | null;
  photo_ids: string[];
  paste_copy: string;
  status: string;
  preflight: string;
  slot_key?: string;
  intended_platform?: string;
  publish_target?: string;
  facebook_schedulable?: boolean;
  facebook_can_cancel?: boolean;
  facebook_status?: string;
  facebook_status_label?: string;
  facebook_blocked_reason?: string | null;
  facebook_slot_at?: string | null;
  facebook_post_id?: string | null;
  facebook_error?: string | null;
};

const FACEBOOK_STATUS_LABELS: Record<string, string> = {
  planned: "Planned",
  missed: "Missed",
  too_soon: "Too soon (under 10 min)",
  out_of_window: "Too far (over 75 days)",
  scheduled: "Scheduled",
  posted: "Posted",
  failed: "Failed",
  cancelled: "Cancelled",
  no_lot: "No cataloged lot",
  not_applicable: "Not posting to the Page"
};

function facebookStatusLabel(post: CalendarPost): string {
  const raw = (post.facebook_status_label || "").trim();
  if (raw) return raw;
  const status = (post.facebook_status || "planned").trim();
  return FACEBOOK_STATUS_LABELS[status] || status.replace(/_/g, " ");
}

function facebookBlockedReason(post: CalendarPost): string {
  if (post.facebook_blocked_reason) return post.facebook_blocked_reason;
  const status = post.facebook_status || "";
  if (status === "missed") return "This slot time has already passed.";
  if (status === "too_soon") return "Too soon (under 10 min) — Facebook requires at least 10 minutes.";
  if (status === "scheduled") return "Already scheduled on the Page.";
  if (status === "posted") return "Already posted to the Page.";
  if (status === "out_of_window") return "Facebook allows scheduling at most 75 days ahead.";
  if (status === "no_lot") return "Catalog this lot before scheduling a Page post.";
  return "";
}

function toCalendarSlot(post: CalendarPost): FacebookCalendarSlot {
  return {
    slot_key: post.slot_key as string,
    lot_id: post.lot_id as string,
    at: post.facebook_slot_at || "",
    platform: post.platform,
    hook: post.hook,
    local_time: post.local_time
  };
}

const FILTERS = ["Push", "Rescue", "Protect", "Let Ride"] as const;

type SortKey = "rank" | "photo" | "lot" | "views" | "watchers" | "bids" | "price" | "note";
type SortDir = "asc" | "desc";

const SORT_COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: "rank", label: "Rank" },
  { key: "photo", label: "Photo" },
  { key: "lot", label: "Lot" },
  { key: "views", label: "Views", numeric: true },
  { key: "watchers", label: "Watchers", numeric: true },
  { key: "bids", label: "Bids", numeric: true },
  { key: "price", label: "Price", numeric: true },
  { key: "note", label: "Note" }
];

const DEFAULT_SORT_DIR: Record<SortKey, SortDir> = {
  rank: "asc",
  photo: "desc",
  lot: "asc",
  views: "desc",
  watchers: "desc",
  bids: "desc",
  price: "desc",
  note: "asc"
};

const SCORE_ORDER: Record<string, number> = { Weak: 0, Moderate: 1, Strong: 2 };

function skuTie(a: QueueItem, b: QueueItem): number {
  return (a.sku || "").localeCompare(b.sku || "", undefined, { numeric: true, sensitivity: "base" });
}

function compareQueueItems(a: QueueItem, b: QueueItem, key: SortKey, dir: SortDir): number {
  if (key === "rank") {
    if (a.push_rank == null && b.push_rank == null) return skuTie(a, b);
    if (a.push_rank == null) return 1;
    if (b.push_rank == null) return -1;
    const diff = dir === "asc" ? a.push_rank - b.push_rank : b.push_rank - a.push_rank;
    return diff || skuTie(a, b);
  }
  if (key === "photo") {
    const av = a.lot_id && a.has_thumbnail ? 1 : 0;
    const bv = b.lot_id && b.has_thumbnail ? 1 : 0;
    const diff = dir === "asc" ? av - bv : bv - av;
    return diff || skuTie(a, b);
  }
  if (key === "lot") {
    const sku = skuTie(a, b);
    const directed = dir === "asc" ? sku : -sku;
    if (directed) return directed;
    const title = (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" });
    return dir === "asc" ? title : -title;
  }
  if (key === "note") {
    const as = SCORE_ORDER[a.engagement_score] ?? 9;
    const bs = SCORE_ORDER[b.engagement_score] ?? 9;
    const score = dir === "asc" ? as - bs : bs - as;
    if (score) return score;
    const momentum = a.momentum.localeCompare(b.momentum);
    if (momentum) return dir === "asc" ? momentum : -momentum;
    const notes = a.notes.localeCompare(b.notes);
    return (dir === "asc" ? notes : -notes) || skuTie(a, b);
  }
  const diff = a[key] - b[key];
  const directed = dir === "asc" ? diff : -diff;
  return directed || skuTie(a, b);
}

function SortHeader({
  label,
  sortKey,
  activeKey,
  dir,
  numeric,
  onSort
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey | null;
  dir: SortDir;
  numeric?: boolean;
  onSort: (key: SortKey) => void;
}) {
  const active = sortKey === activeKey;
  return (
    <th
      className={numeric ? "ct-table__num" : undefined}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" className="ct-table-sort" onClick={() => onSort(sortKey)}>
        <span className="ct-table-sort__label">{label}</span>
        <span className="ct-table-sort__mark" aria-hidden="true">
          {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );
}

function money(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 0.001) return `$${rounded}`;
  return `$${value.toFixed(2)}`;
}

function when(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export default function EngagementPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [sales, setSales] = useState<SaleSummary[]>([]);
  const [saleId, setSaleId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SaleDetail | null>(null);
  const [posts, setPosts] = useState<CalendarPost[]>([]);
  const [bucket, setBucket] = useState<(typeof FILTERS)[number]>("Push");
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<number | null>(null);
  const [facebookConfigured, setFacebookConfigured] = useState(false);
  const [accountRole, setAccountRole] = useState("");
  const [selectedLotIds, setSelectedLotIds] = useState<Set<string>>(() => new Set());
  const [facebookLotIds, setFacebookLotIds] = useState<string[] | null>(null);
  const [facebookLotSchedules, setFacebookLotSchedules] = useState<Record<string, string> | null>(null);
  const [facebookCalendarSlots, setFacebookCalendarSlots] = useState<FacebookCalendarSlot[] | null>(null);
  const [facebookModalSource, setFacebookModalSource] = useState<FacebookPostSource>("push_queue");
  const [cancellingKey, setCancellingKey] = useState("");
  const canPostFacebook = facebookConfigured && isOwnerRole(accountRole);
  const upcomingSlots = posts.filter((post) => post.facebook_schedulable && post.lot_id && post.slot_key);

  function openCalendarApprove(rows: CalendarPost[]) {
    const slots = rows.filter((post) => post.lot_id && post.slot_key).map(toCalendarSlot);
    if (!slots.length) return;
    setFacebookModalSource("push_calendar");
    setFacebookLotSchedules(null);
    setFacebookCalendarSlots(slots);
    setFacebookLotIds(slots.map((slot) => slot.lot_id));
  }

  async function cancelCalendarSlot(post: CalendarPost) {
    if (!token || !saleId || !post.slot_key || cancellingKey) return;
    if (!window.confirm("Remove this scheduled Facebook Page post? It will not go live.")) return;
    setCancellingKey(post.slot_key);
    setError("");
    try {
      await apiFetch(`/engagement/sales/${saleId}/calendar/facebook/cancel`, token, {
        method: "POST",
        body: JSON.stringify({ slot_keys: [post.slot_key] })
      });
      await loadSale(token, saleId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not unapprove this slot");
    } finally {
      setCancellingKey("");
    }
  }

  const loadSale = useCallback(
    async (t: string, id: number) => {
      const [sale, calendar] = await Promise.all([
        apiFetch(`/engagement/sales/${id}`, t) as Promise<SaleDetail>,
        apiFetch(`/engagement/sales/${id}/calendar`, t) as Promise<CalendarPost[]>
      ]);
      setDetail(sale);
      setPosts(calendar);
    },
    []
  );

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      setLoading(false);
      return;
    }
    setAccountRole(storedAccountRole());
    void apiFetch("/auth/me", t)
      .then((data) => {
        const role = String((data as { role?: string }).role || "");
        rememberAccountRole(role);
        setAccountRole(role);
      })
      .catch(() => setAccountRole(""));
    void apiFetch("/integrations/facebook/status", t)
      .then((data) => setFacebookConfigured(Boolean((data as { configured?: boolean }).configured)))
      .catch(() => setFacebookConfigured(false));
    void apiFetch("/engagement/sales", t)
      .then((data) => {
        const rows = data as SaleSummary[];
        setSales(rows);
        if (rows[0]) setSaleId(rows[0].ctseller_sale_id);
        else setLoading(false);
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : "Could not load the Push queue";
        if (msg.toLowerCase().includes("401") || msg.toLowerCase().includes("authentication")) {
          localStorage.removeItem("token");
          router.replace("/");
          return;
        }
        setError(msg);
        setLoading(false);
      });
  }, [router]);

  useEffect(() => {
    if (!token || saleId == null) return;
    setLoading(true);
    void loadSale(token, saleId)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load that sale"))
      .finally(() => setLoading(false));
  }, [token, saleId, loadSale]);

  const visible = useMemo(() => {
    const rows = (detail?.items || []).filter((item) => item.bucket === bucket);
    if (!sortKey) return rows;
    return [...rows].sort((a, b) => compareQueueItems(a, b, sortKey, sortDir));
  }, [detail, bucket, sortKey, sortDir]);

  function onSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDir(DEFAULT_SORT_DIR[key]);
  }

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function copyPost(post: CalendarPost) {
    try {
      await navigator.clipboard.writeText(post.paste_copy);
      setCopied(post.order);
    } catch {
      setCopied(null);
    }
  }

  if (!token) {
    return (
      <main className="ct-shell">
        <div className="ct-panel">
          <p style={{ margin: 0 }}>Please sign in first.</p>
          <p style={{ margin: "1rem 0 0" }}>
            <Link href="/">Go to sign in</Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <AppShell
      actions={
        <>
          <Link href="/dashboard" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
            Dashboard
          </Link>
          <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
            Log out
          </button>
        </>
      }
    >
      <div className="ct-dashboard" style={{ maxWidth: 1200 }}>
        <header className="ct-page-header">
          <div>
            <h1 className="ct-page-title" style={{ marginBottom: "0.35rem" }}>
              Push queue
            </h1>
            <p className="ct-subtitle" style={{ margin: 0 }}>
              The Watcher sends views, watchers, bids, price, and the lot link after each refresh.
              Check cataloged lots and approve Facebook Page posts from the social calendar.
              Spreadsheet Instagram, Stories, and TikTok slots currently publish as Page feed posts.
            </p>
          </div>
        </header>

        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}

        {sales.length > 0 ? (
          <label className="ct-field" style={{ maxWidth: 420, display: "block", marginBottom: "1rem" }}>
            <span className="ct-muted">CT Bids sale</span>
            <select
              value={saleId ?? ""}
              onChange={(e) => setSaleId(Number(e.target.value))}
              style={{ display: "block", marginTop: "0.35rem", width: "100%" }}
            >
              {sales.map((sale) => (
                <option key={sale.ctseller_sale_id} value={sale.ctseller_sale_id}>
                  {sale.sale_title || `Sale ${sale.ctseller_sale_id}`} · {sale.push} Push
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {loading ? <p className="ct-muted">Loading…</p> : null}

        {!loading && sales.length === 0 ? (
          <section className="ct-card ct-card--padded">
            <p style={{ margin: 0 }}>
              No live snapshot yet. Sign in to Estate-EZ from the Watcher, open the sale, and refresh.
              Counts show up here after that refresh.
            </p>
          </section>
        ) : null}

        {detail ? (
          <>
            <p className="ct-muted" style={{ marginTop: 0 }}>
              Snapshot {when(detail.captured_at)}
              {detail.sale_ends_at ? ` · closes ${when(detail.sale_ends_at)}` : ""}
              {detail.auction_url ? (
                <>
                  {" · "}
                  <a href={detail.auction_url} target="_blank" rel="noreferrer">
                    Auction page
                  </a>
                </>
              ) : null}
            </p>
            <section className="ct-dash-stats" aria-label="Engagement buckets" style={{ marginBottom: "1rem" }}>
              {FILTERS.map((name) => {
                const count =
                  name === "Push"
                    ? detail.push
                    : name === "Rescue"
                      ? detail.rescue
                      : name === "Protect"
                        ? detail.protect
                        : detail.let_ride;
                return (
                  <button
                    key={name}
                    type="button"
                    className="ct-stat-card"
                    onClick={() => setBucket(name)}
                    style={{
                      textAlign: "left",
                      cursor: "pointer",
                      outline: bucket === name ? "2px solid var(--ct-primary)" : undefined
                    }}
                  >
                    <span className="ct-stat-card__value">{count}</span>
                    <span className="ct-stat-card__label">{name}</span>
                  </button>
                );
              })}
            </section>

            {canPostFacebook ? (
              <div className="ct-row" style={{ gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() =>
                    setSelectedLotIds(new Set(visible.map((item) => item.lot_id).filter((id): id is string => Boolean(id))))
                  }
                >
                  Select cataloged in {bucket}
                </button>
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() => setSelectedLotIds(new Set())}
                >
                  Clear
                </button>
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  disabled={selectedLotIds.size < 1}
                  onClick={() => {
                    setFacebookModalSource("push_queue");
                    setFacebookLotSchedules(null);
                    setFacebookLotIds([...selectedLotIds]);
                  }}
                >
                  Post selected to Facebook Page ({selectedLotIds.size})
                </button>
              </div>
            ) : null}

            <div className="ct-table-wrap">
              <table className="ct-table ct-table--engagement">
                <thead>
                  <tr>
                    {canPostFacebook ? <th>Post</th> : null}
                    {SORT_COLUMNS.map((column) => (
                      <SortHeader
                        key={column.key}
                        label={column.label}
                        sortKey={column.key}
                        activeKey={sortKey}
                        dir={sortDir}
                        numeric={column.numeric}
                        onSort={onSort}
                      />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visible.length === 0 ? (
                    <tr>
                      <td colSpan={canPostFacebook ? 9 : 8}>No {bucket} lots in this snapshot.</td>
                    </tr>
                  ) : (
                    visible.map((item) => (
                      <tr key={`${item.ctseller_item_id}-${item.sku}`}>
                        {canPostFacebook ? (
                          <td>
                            {item.lot_id ? (
                              <input
                                type="checkbox"
                                checked={selectedLotIds.has(item.lot_id)}
                                onChange={(e) => {
                                  const id = item.lot_id as string;
                                  setSelectedLotIds((prev) => {
                                    const next = new Set(prev);
                                    if (e.target.checked) next.add(id);
                                    else next.delete(id);
                                    return next;
                                  });
                                }}
                                aria-label={`Select ${item.sku || "lot"} for Facebook Page`}
                              />
                            ) : (
                              <span className="ct-muted">—</span>
                            )}
                          </td>
                        ) : null}
                        <td>{item.push_rank ?? "—"}</td>
                        <td>
                          {item.lot_id ? (
                            <LotThumbnail
                              lotId={item.lot_id}
                              token={token}
                              hasThumbnail={item.has_thumbnail}
                              size={56}
                              maxPx={220}
                            />
                          ) : (
                            <span className="ct-muted">No catalog photo</span>
                          )}
                        </td>
                        <td>
                          <div>
                            {item.lot_id ? (
                              <Link href={`/lots/${item.lot_id}`}>{item.sku || "Lot"}</Link>
                            ) : (
                              <strong>{item.sku || "No SKU"}</strong>
                            )}
                          </div>
                          <div>{item.title}</div>
                          {item.item_url ? (
                            <a href={item.item_url} target="_blank" rel="noreferrer">
                              Listing
                            </a>
                          ) : null}
                          {!item.lot_id ? (
                            <div className="ct-muted">Cataloged lots match on CT Bids item id, then SKU.</div>
                          ) : null}
                        </td>
                        <td className="ct-table__num">{item.views}</td>
                        <td className="ct-table__num">{item.watchers}</td>
                        <td className="ct-table__num">{item.bids}</td>
                        <td className="ct-table__num">
                          {money(item.price)}
                          <div className="ct-muted">reserve {money(item.reserve)}</div>
                        </td>
                        <td>
                          {item.engagement_score} · {item.momentum}
                          <div>{item.notes}</div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <h2 className="ct-page-title" style={{ fontSize: "1.25rem", marginTop: "1.5rem" }}>
              Social calendar
            </h2>
            <p className="ct-muted">
              Planned posts for Push lots only, from the snapshot through the close. Every spreadsheet
              slot (9:00 Facebook, 12:15 Instagram, 16:30 Stories, 18:00 TikTok, closing 17:40) is
              scheduled on the connected Facebook Page at that time after you approve it. Native
              Instagram and TikTok publishers are not wired yet. Missed slots are skipped.
            </p>
            {canPostFacebook && posts.length > 0 ? (
              <div className="ct-row" style={{ gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  disabled={upcomingSlots.length < 1}
                  title={
                    upcomingSlots.length < 1
                      ? "No upcoming slots inside Facebook's 10-minute to 75-day window."
                      : "Preview and schedule every in-window slot on the Page"
                  }
                  onClick={() => openCalendarApprove(upcomingSlots)}
                >
                  Approve all upcoming slots ({upcomingSlots.length})
                </button>
              </div>
            ) : null}
            {posts.length === 0 ? (
              <p className="ct-muted">No Push lots, so there is nothing to schedule.</p>
            ) : (
              <div className="ct-table-wrap">
                <table className="ct-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Platform</th>
                      <th>Lot</th>
                      <th>Copy</th>
                      {canPostFacebook ? <th>Page post</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {posts.map((post) => (
                      <tr key={post.order}>
                        <td>
                          {post.post_date}
                          <div>
                            {post.local_time} {post.zone}
                          </div>
                          <div className="ct-muted">{post.status}</div>
                          {post.publish_target === "facebook_page" || post.facebook_status ? (
                            <div>
                              Page: {facebookStatusLabel(post)}
                              {post.facebook_post_id ? ` · ${post.facebook_post_id}` : ""}
                            </div>
                          ) : null}
                          {post.facebook_error ? <div className="ct-error">{post.facebook_error}</div> : null}
                        </td>
                        <td>
                          {post.platform}
                          {post.publish_target === "facebook_page" && post.platform !== "Facebook" ? (
                            <div className="ct-muted">Posting to Facebook in place of {post.platform}</div>
                          ) : post.publish_target === "facebook_page" ? (
                            <div className="ct-muted">Facebook Page feed</div>
                          ) : null}
                          <div className="ct-muted">{post.hook}</div>
                          <div className="ct-muted">{post.post_type}</div>
                        </td>
                        <td>
                          {post.lot_id ? (
                            <Link href={`/lots/${post.lot_id}`}>{post.sku}</Link>
                          ) : (
                            post.sku
                          )}
                          <div>{post.title}</div>
                          {post.item_url ? (
                            <a href={post.item_url} target="_blank" rel="noreferrer">
                              Listing
                            </a>
                          ) : null}
                        </td>
                        <td>
                          <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>
                            {post.paste_copy}
                          </pre>
                          <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void copyPost(post)}>
                            {copied === post.order ? "Copied" : "Copy"}
                          </button>
                          <div className="ct-muted">{post.preflight}</div>
                        </td>
                        {canPostFacebook ? (
                          <td>
                            <button
                              type="button"
                              className="ct-btn ct-btn--primary"
                              disabled={!post.facebook_schedulable || !post.lot_id || !post.slot_key}
                              title={
                                post.facebook_schedulable
                                  ? "Preview caption and photos, then schedule this slot on the Page"
                                  : facebookBlockedReason(post)
                              }
                              onClick={() => openCalendarApprove([post])}
                            >
                              Approve
                            </button>
                            {post.facebook_can_cancel && post.slot_key ? (
                              <button
                                type="button"
                                className="ct-btn"
                                style={{ marginTop: "0.35rem" }}
                                disabled={cancellingKey === post.slot_key}
                                onClick={() => void cancelCalendarSlot(post)}
                              >
                                {cancellingKey === post.slot_key ? "Removing…" : "Cancel / Unapprove"}
                              </button>
                            ) : null}
                            {!post.facebook_schedulable && facebookBlockedReason(post) ? (
                              <div className="ct-muted" style={{ marginTop: "0.35rem" }}>
                                {facebookBlockedReason(post)}
                              </div>
                            ) : null}
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}
      </div>
      {facebookLotIds && token ? (
        <FacebookPagePostModal
          token={token}
          lotIds={facebookLotIds}
          source={facebookModalSource}
          lotSchedules={facebookLotSchedules || undefined}
          calendarSaleId={facebookModalSource === "push_calendar" ? saleId : null}
          calendarSlots={facebookCalendarSlots || undefined}
          onClose={() => {
            setFacebookLotIds(null);
            setFacebookLotSchedules(null);
            setFacebookCalendarSlots(null);
            setFacebookModalSource("push_queue");
          }}
          onPosted={() => {
            if (token && saleId) void loadSale(token, saleId);
          }}
        />
      ) : null}
    </AppShell>
  );
}
