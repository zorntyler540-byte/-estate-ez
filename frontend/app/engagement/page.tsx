"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { ShellActions } from "../../components/ShellActions";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Label } from "../../components/ui/input";
import { Select } from "../../components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { useToast } from "../../components/ui/toast";
import { cn } from "../../lib/utils";
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
  no_interest: number;
  needs_eyes: number;
  rescue?: number;
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

type FacebookStatus = {
  configured?: boolean;
  connected?: boolean;
  needs_reconnect?: boolean;
  page_name?: string | null;
  instagram_enabled?: boolean;
  instagram_connected?: boolean;
  instagram_username?: string | null;
  timezone?: string;
  timezone_label?: string;
  timezone_choices?: { value: string; label: string }[];
};

type CalendarPost = {
  order: number;
  post_date: string;
  local_time: string;
  zone: string;
  zone_label?: string;
  platform: string;
  hook: string;
  post_type: string;
  sku: string;
  title: string;
  item_url: string;
  lot_id?: string | null;
  photo_ids: string[];
  paste_copy: string;
  group_copy?: string;
  bucket?: string;
  status: string;
  preflight: string;
  slot_key?: string;
  intended_platform?: string;
  publish_target?: string;
  facebook_schedulable?: boolean;
  facebook_can_post_now?: boolean;
  facebook_can_cancel?: boolean;
  facebook_status?: string;
  facebook_status_label?: string;
  facebook_blocked_reason?: string | null;
  facebook_slot_at?: string | null;
  facebook_post_id?: string | null;
  facebook_error?: string | null;
  facebook_review_note?: string | null;
  facebook_caption?: string | null;
  pinned?: boolean;
};

// A snapshot older than this may rank lots that have since sold or moved.
const STALE_SNAPSHOT_HOURS = 6;

function calendarRowKey(post: CalendarPost): string {
  return post.slot_key || `${post.post_date}-${post.local_time}-${post.lot_id || post.sku}`;
}

function saleIsClosed(sale: SaleSummary, now = Date.now()): boolean {
  if (!sale.sale_ends_at) return false;
  const ends = new Date(sale.sale_ends_at).getTime();
  return !Number.isNaN(ends) && ends <= now;
}

function hoursSince(value?: string | null): number | null {
  if (!value) return null;
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return null;
  return (Date.now() - at) / 3_600_000;
}

const FACEBOOK_STATUS_LABELS: Record<string, string> = {
  planned: "Planned",
  missed: "Missed",
  too_soon: "Too soon (under 10 min)",
  out_of_window: "Too far (over 75 days)",
  scheduled: "Scheduled",
  publishing: "Publishing…",
  posted: "Posted",
  failed: "Failed",
  cancelled: "Cancelled",
  no_lot: "No cataloged lot",
  not_applicable: "Not posting to the Page",
  copy_only: "Copy only"
};

const PUBLISH_TARGET_LABELS: Record<string, string> = {
  facebook_page: "Facebook Page feed",
  instagram_feed: "Instagram feed",
  stories: "FB + IG Stories",
  copy_only: "Copy only — paste it yourself"
};

function publishTargetLabel(post: CalendarPost): string {
  const target = post.publish_target || "";
  if (target === "copy_only" && post.platform === "TikTok") return "TikTok — copy only";
  return PUBLISH_TARGET_LABELS[target] || "";
}

function isCopyOnly(post: CalendarPost): boolean {
  return post.publish_target === "copy_only" || post.facebook_status === "copy_only";
}

function facebookStatusLabel(post: CalendarPost): string {
  const raw = (post.facebook_status_label || "").trim();
  if (raw) return raw;
  const status = (post.facebook_status || "planned").trim();
  return FACEBOOK_STATUS_LABELS[status] || status.replace(/_/g, " ");
}

function facebookBlockedReason(post: CalendarPost): string {
  if (post.facebook_blocked_reason) return post.facebook_blocked_reason;
  const status = post.facebook_status || "";
  if (status === "missed") return "Can't schedule: this slot time has already passed. Use Post now.";
  if (status === "too_soon") return "Can't schedule: under 10 minutes. Facebook needs 10 minutes — use Post now.";
  if (status === "scheduled") return "Already approved for this slot.";
  if (status === "publishing") return "Being published right now.";
  if (status === "posted") return "Already posted.";
  if (status === "out_of_window") return "Can't schedule: more than 75 days ahead. Use Post now to publish immediately.";
  if (status === "no_lot") return "Catalog this lot before posting to the Page.";
  return "";
}

function toCalendarSlot(post: CalendarPost): FacebookCalendarSlot {
  return {
    slot_key: post.slot_key as string,
    lot_id: post.lot_id as string,
    at: post.facebook_slot_at || "",
    platform: post.platform,
    hook: post.hook,
    local_time: post.local_time,
    zone_label: post.zone_label || post.zone
  };
}

const FILTERS = ["Push", "Needs eyes", "No interest", "Protect", "Let Ride"] as const;

const BUCKET_VALUE_CLASS: Record<(typeof FILTERS)[number], string> = {
  Push: "text-primary",
  "Needs eyes": "text-[#fde68a]",
  "No interest": "text-muted-foreground",
  Protect: "text-[#4ade80]",
  "Let Ride": "text-[#ffb199]"
};

function scoreVariant(score: string): "positive" | "warning" | "neutral" {
  if (score === "Strong") return "positive";
  if (score === "Moderate") return "warning";
  return "neutral";
}

function facebookBadgeVariant(status?: string): "positive" | "negative" | "warning" | "info" | "neutral" {
  if (status === "posted" || status === "scheduled") return "positive";
  if (status === "failed") return "negative";
  if (status === "missed" || status === "too_soon" || status === "out_of_window") return "warning";
  if (status === "planned" || status === "publishing") return "info";
  return "neutral";
}

function displayBucket(item: { bucket: string; watchers: number; bids: number }): string {
  if (item.bucket === "Rescue") {
    return item.watchers === 0 && item.bids === 0 ? "No interest" : "Needs eyes";
  }
  return item.bucket;
}

function filterCount(detail: SaleDetail, name: (typeof FILTERS)[number]): number {
  if (name === "Push") return detail.push;
  if (name === "Needs eyes") return detail.needs_eyes;
  if (name === "No interest") return detail.no_interest;
  if (name === "Protect") return detail.protect;
  return detail.let_ride;
}

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
    <TableHead
      className={numeric ? "text-right" : undefined}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        className={cn(
          "inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 font-sans text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground",
          numeric && "w-full justify-end"
        )}
        onClick={() => onSort(sortKey)}
      >
        <span>{label}</span>
        <span aria-hidden="true" className="text-[0.65rem] text-primary">
          {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </TableHead>
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
  const [copied, setCopied] = useState<string | null>(null);
  const [copiedGroup, setCopiedGroup] = useState<string | null>(null);
  const [facebook, setFacebook] = useState<FacebookStatus>({});
  const [showClosed, setShowClosed] = useState(false);
  const [savingZone, setSavingZone] = useState(false);
  const [accountRole, setAccountRole] = useState("");
  const [selectedLotIds, setSelectedLotIds] = useState<Set<string>>(() => new Set());
  const [facebookLotIds, setFacebookLotIds] = useState<string[] | null>(null);
  const [facebookLotSchedules, setFacebookLotSchedules] = useState<Record<string, string> | null>(null);
  const [facebookCalendarSlots, setFacebookCalendarSlots] = useState<FacebookCalendarSlot[] | null>(null);
  const [facebookModalSource, setFacebookModalSource] = useState<FacebookPostSource>("push_queue");
  const [facebookPublishNow, setFacebookPublishNow] = useState(false);
  const [cancellingKey, setCancellingKey] = useState("");
  const [refreshingInstagram, setRefreshingInstagram] = useState(false);
  const toast = useToast();
  const seenError = useRef("");
  const isOwner = isOwnerRole(accountRole);
  const facebookReady = Boolean(facebook.configured && facebook.connected && !facebook.needs_reconnect);
  const canPostFacebook = facebookReady && isOwner;
  const openSales = useMemo(() => sales.filter((sale) => !saleIsClosed(sale)), [sales]);
  const saleChoices = useMemo(() => {
    const rows = showClosed ? sales : openSales;
    const current = sales.find((sale) => sale.ctseller_sale_id === saleId);
    return current && !rows.includes(current) ? [current, ...rows] : rows;
  }, [sales, openSales, showClosed, saleId]);
  const snapshotAgeHours = hoursSince(detail?.captured_at);
  const snapshotStale = snapshotAgeHours != null && snapshotAgeHours > STALE_SNAPSHOT_HOURS;
  const zoneLabel = facebook.timezone_label || posts[0]?.zone_label || "PT";
  const upcomingSlots = posts.filter((post) => post.facebook_schedulable && post.lot_id && post.slot_key);

  function openCalendarApprove(rows: CalendarPost[], { postNow = false } = {}) {
    const slots = rows.filter((post) => post.lot_id && post.slot_key).map(toCalendarSlot);
    if (!slots.length) return;
    setFacebookModalSource("push_calendar");
    setFacebookPublishNow(postNow);
    setFacebookLotSchedules(null);
    setFacebookCalendarSlots(slots);
    setFacebookLotIds(slots.map((slot) => slot.lot_id));
  }

  async function cancelCalendarSlot(post: CalendarPost) {
    if (!token || !saleId || !post.slot_key || cancellingKey) return;
    const target = publishTargetLabel(post) || "Facebook Page";
    if (!window.confirm(`Unapprove this ${target} slot? It will not go live.`)) return;
    setCancellingKey(post.slot_key);
    setError("");
    try {
      await apiFetch(`/engagement/sales/${saleId}/calendar/facebook/cancel`, token, {
        method: "POST",
        body: JSON.stringify({ slot_keys: [post.slot_key] })
      });
      await loadSale(token, saleId);
      toast.success(`Unapproved the ${target} slot.`);
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
      .then((data) => setFacebook(data as FacebookStatus))
      .catch(() => setFacebook({}));
    void apiFetch("/engagement/sales", t)
      .then((data) => {
        const rows = data as SaleSummary[];
        setSales(rows);
        const first = rows.find((sale) => !saleIsClosed(sale)) || rows[0];
        if (first) setSaleId(first.ctseller_sale_id);
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
    const text = error.trim();
    if (!text) {
      seenError.current = "";
      return;
    }
    if (text === seenError.current) return;
    seenError.current = text;
    toast.error(text);
  }, [error, toast]);

  useEffect(() => {
    if (!token || saleId == null) return;
    setLoading(true);
    void loadSale(token, saleId)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load that sale"))
      .finally(() => setLoading(false));
  }, [token, saleId, loadSale]);

  const visible = useMemo(() => {
    const rows = (detail?.items || []).filter((item) => displayBucket(item) === bucket);
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

  async function saveTimezone(zone: string) {
    if (!token || !zone || zone === facebook.timezone) return;
    setSavingZone(true);
    try {
      const next = (await apiFetch("/integrations/facebook/settings", token, {
        method: "PATCH",
        body: JSON.stringify({ timezone: zone })
      })) as FacebookStatus;
      setFacebook(next);
      if (saleId != null) await loadSale(token, saleId);
      toast.success(`Calendar times now in ${next.timezone_label || zone}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the time zone");
    } finally {
      setSavingZone(false);
    }
  }

  async function refreshInstagram() {
    if (!token || refreshingInstagram) return;
    setRefreshingInstagram(true);
    try {
      const next = (await apiFetch("/integrations/facebook/instagram/refresh", token, {
        method: "POST"
      })) as FacebookStatus;
      setFacebook(next);
      if (saleId != null) await loadSale(token, saleId);
      if (next.instagram_connected) toast.success(`Instagram linked: @${next.instagram_username || "account"}.`);
      else toast.error("No Instagram professional account is linked to this Facebook Page yet.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check Instagram");
    } finally {
      setRefreshingInstagram(false);
    }
  }

  async function copyPost(post: CalendarPost) {
    try {
      await navigator.clipboard.writeText(post.facebook_caption || post.paste_copy);
      setCopied(calendarRowKey(post));
      setCopiedGroup(null);
      toast.success("Copied post.");
    } catch {
      setCopied(null);
      toast.error("Could not copy.");
    }
  }

  async function copyGroupPost(post: CalendarPost) {
    const text = (post.group_copy || "").trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedGroup(calendarRowKey(post));
      setCopied(null);
      toast.success("Copied group caption.");
    } catch {
      setCopiedGroup(null);
      toast.error("Could not copy.");
    }
  }

  if (!token) {
    return (
      <main className="mx-auto flex min-h-[50vh] w-full max-w-md items-center px-4">
        <section className="w-full rounded-xl border border-border bg-card px-5 py-6 shadow-sm shadow-black/30">
          <p className="m-0 text-foreground">Please sign in first.</p>
          <p className="mb-0 mt-4">
            <Button asChild>
              <Link href="/">Go to sign in</Link>
            </Button>
          </p>
        </section>
      </main>
    );
  }

  return (
    <AppShell
      actions={<ShellActions onLogout={logout} links={[{ href: "/dashboard", label: "Dashboard" }]} />}
    >
      <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-4">
        <header>
          <h1 className="m-0 text-2xl font-bold tracking-tight text-foreground">Push queue</h1>
          <p className="mb-0 mt-1 max-w-3xl text-sm text-muted-foreground">
            The Watcher sends views, watchers, bids, price, and the lot link after each refresh.
            Check cataloged lots and approve posts from the social calendar. Facebook slots go to the
            Page feed, Instagram slots to the linked Instagram account, and Stories slots to Page and
            Instagram Stories. TikTok slots are copy only.
          </p>
        </header>

        {isOwner && facebookReady && facebook.instagram_enabled ? (
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5 text-sm text-muted-foreground"
            role="status"
          >
            <span>
              {facebook.instagram_connected ? (
                <>
                  Instagram: <span className="text-foreground">@{facebook.instagram_username || "linked"}</span>{" "}
                  (linked to {facebook.page_name || "the Page"})
                </>
              ) : (
                "Instagram is not linked to this Facebook Page, so Instagram slots are copy only. Link an Instagram professional account to the Page in Meta, then check again."
              )}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={refreshingInstagram}
              onClick={() => void refreshInstagram()}
            >
              {refreshingInstagram ? "Checking…" : "Check Instagram link"}
            </Button>
          </div>
        ) : null}

        {isOwner && facebook.configured && (facebook.needs_reconnect || !facebook.connected) ? (
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[rgba(245,158,11,0.45)] bg-[rgba(251,191,36,0.12)] px-3 py-2.5 text-sm text-[#fde68a]"
            role="status"
          >
            <span>
              {facebook.needs_reconnect
                ? "Facebook access expired or was revoked. Nothing posts (including auto-approve) until you reconnect the Page."
                : "Connect a Facebook Page to approve Push-calendar posts."}
            </span>
            <Button asChild size="sm">
              <Link href="/account">{facebook.needs_reconnect ? "Reconnect Facebook" : "Connect Facebook Page"}</Link>
            </Button>
          </div>
        ) : null}

        {error ? (
          <div
            className="rounded-lg border border-[rgba(248,113,113,0.45)] bg-[rgba(239,68,68,0.12)] px-3 py-2.5 text-sm text-[#fca5a5]"
            role="alert"
          >
            {error}
          </div>
        ) : null}

        {sales.length > 0 ? (
          <div className="flex flex-wrap items-end gap-3">
            <Label className="w-full max-w-md">
              CT Bids sale
              <Select
                value={saleId ?? ""}
                onChange={(e) => setSaleId(Number(e.target.value))}
                aria-describedby="sale-select-help"
              >
                {saleChoices.map((sale) => (
                  <option key={sale.ctseller_sale_id} value={sale.ctseller_sale_id}>
                    {sale.sale_title || `Sale ${sale.ctseller_sale_id}`} · {sale.push} Push
                    {saleIsClosed(sale) ? " · closed" : ""}
                  </option>
                ))}
              </Select>
            </Label>
            {isOwner && facebook.configured && facebook.timezone_choices?.length ? (
              <Label className="w-full max-w-[14rem]">
                Calendar time zone
                <Select
                  value={facebook.timezone || ""}
                  disabled={savingZone}
                  onChange={(e) => void saveTimezone(e.target.value)}
                >
                  {facebook.timezone_choices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </Select>
              </Label>
            ) : null}
            {sales.length > openSales.length ? (
              <label className="flex h-10 items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={showClosed}
                  onChange={(e) => setShowClosed(e.target.checked)}
                />
                Show closed sales ({sales.length - openSales.length})
              </label>
            ) : null}
            <p id="sale-select-help" className="sr-only">
              Closed sales are hidden unless Show closed sales is checked.
            </p>
          </div>
        ) : null}

        {loading ? (
          <div
            className="rounded-xl border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground"
            role="status"
            aria-busy="true"
          >
            Loading push queue…
          </div>
        ) : null}

        {!loading && sales.length === 0 ? (
          <section className="rounded-xl border border-dashed border-border bg-card px-4 py-6 text-sm text-foreground shadow-sm shadow-black/30">
            <p className="m-0">
              No live snapshot yet. Sign in to Estate-EZ from the Watcher, open the sale, and refresh.
              Counts show up here after that refresh.
            </p>
          </section>
        ) : null}

        {detail ? (
          <>
            <p className="m-0 text-sm text-muted-foreground">
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
            {snapshotStale ? (
              <div
                className="rounded-lg border border-[rgba(245,158,11,0.45)] bg-[rgba(251,191,36,0.12)] px-3 py-2.5 text-sm text-[#fde68a]"
                role="status"
              >
                This snapshot is {Math.floor(snapshotAgeHours as number)} hours old. Refresh the sale in the
                Watcher before approving so ranks, bids, and lots are current.
              </div>
            ) : null}
            <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5" aria-label="Engagement buckets">
              {FILTERS.map((name) => {
                const count = filterCount(detail, name);
                const selected = bucket === name;
                return (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setBucket(name)}
                    aria-pressed={selected}
                    className={cn(
                      "flex cursor-pointer flex-col gap-1 rounded-lg border bg-[var(--ct-bg-elevated)] px-3 py-2.5 text-left transition-colors",
                      selected
                        ? "border-primary shadow-[0_0_0_1px_var(--primary)]"
                        : "border-border hover:border-primary/40"
                    )}
                  >
                    <span className={cn("text-[1.35rem] font-bold leading-none tracking-tight", BUCKET_VALUE_CLASS[name])}>
                      {count}
                    </span>
                    <span className="text-[0.76rem] font-medium uppercase tracking-[0.06em] text-muted-foreground">
                      {name}
                    </span>
                  </button>
                );
              })}
            </section>

            {canPostFacebook ? (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    setSelectedLotIds(new Set(visible.map((item) => item.lot_id).filter((id): id is string => Boolean(id))))
                  }
                >
                  Select cataloged in {bucket}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setSelectedLotIds(new Set())}>
                  Clear
                </Button>
                <Button
                  type="button"
                  disabled={selectedLotIds.size < 1}
                  onClick={() => {
                    setFacebookModalSource("push_queue");
                    setFacebookPublishNow(false);
                    setFacebookLotSchedules(null);
                    setFacebookCalendarSlots(null);
                    setFacebookLotIds([...selectedLotIds]);
                  }}
                >
                  Post selected to Facebook Page ({selectedLotIds.size})
                </Button>
              </div>
            ) : null}

            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {canPostFacebook ? <TableHead>Post</TableHead> : null}
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
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.length === 0 ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={canPostFacebook ? 9 : 8} className="py-8 text-center text-muted-foreground">
                      No {bucket} lots in this snapshot.
                    </TableCell>
                  </TableRow>
                ) : (
                  visible.map((item) => (
                    <TableRow key={`${item.ctseller_item_id}-${item.sku}`}>
                      {canPostFacebook ? (
                        <TableCell>
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
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      ) : null}
                      <TableCell className="tabular-nums">{item.push_rank ?? "—"}</TableCell>
                      <TableCell>
                        {item.lot_id ? (
                          <LotThumbnail
                            lotId={item.lot_id}
                            token={token}
                            hasThumbnail={item.has_thumbnail}
                            size={56}
                            maxPx={220}
                          />
                        ) : (
                          <span className="text-muted-foreground">No catalog photo</span>
                        )}
                      </TableCell>
                      <TableCell>
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
                          <div className="text-muted-foreground">Cataloged lots match on CT Bids item id, then SKU.</div>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{item.views}</TableCell>
                      <TableCell className="text-right tabular-nums">{item.watchers}</TableCell>
                      <TableCell className="text-right tabular-nums">{item.bids}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {money(item.price)}
                        <div className="text-muted-foreground">reserve {money(item.reserve)}</div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge variant={scoreVariant(item.engagement_score)}>{item.engagement_score}</Badge>
                          <Badge variant="outline">{item.momentum}</Badge>
                        </div>
                        {item.notes ? <div className="mt-1 text-muted-foreground">{item.notes}</div> : null}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>

            <h2 className="m-0 mt-2 text-xl font-bold tracking-tight text-foreground">Social calendar</h2>
            <p className="m-0 text-sm text-muted-foreground">
              Top Push lots get two or three slots before close (09:00 and 16:30 on different days,
              plus a closing-day 17:40 countdown for #1). Lower-ranked Push lots get at least one;
              Needs eyes lots fill leftover slots lightly (at most one per day). No interest lots
              stay off the calendar. Approved Facebook slots are scheduled on the Page; Instagram and
              Stories slots are held and published by Estate-EZ at slot time (Meta cannot schedule
              them). TikTok slots, and Instagram slots without a linked account, are copy only.
              Missed or too-soon slots cannot be scheduled — use Post now to publish them immediately.
              Approved slots are pinned: later snapshots keep them on the same day and time. Times are{" "}
              {zoneLabel}.
            </p>
            {canPostFacebook && posts.length > 0 ? (
              <div>
                <Button
                  type="button"
                  disabled={upcomingSlots.length < 1}
                  title={
                    upcomingSlots.length < 1
                      ? "No upcoming slots inside Facebook's 10-minute to 75-day window."
                      : "Preview and schedule every in-window slot on the Page"
                  }
                  onClick={() => openCalendarApprove(upcomingSlots)}
                >
                  Approve all upcoming slots ({upcomingSlots.length})
                </Button>
              </div>
            ) : null}
            {posts.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border bg-card px-4 py-6 text-sm text-muted-foreground">
                No Push or Needs eyes lots, so there is nothing to schedule.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>When</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Lot</TableHead>
                          <TableHead>Copy</TableHead>
                    {canPostFacebook ? <TableHead>Publish</TableHead> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {posts.map((post) => (
                    <TableRow key={calendarRowKey(post)}>
                      <TableCell>
                        <div>{post.post_date}</div>
                        <div>
                          {post.local_time} {post.zone_label || post.zone}
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <Badge variant="neutral">{post.status}</Badge>
                          {post.pinned ? (
                            <Badge variant="outline" title="Approved or posted: later snapshots keep this slot in place">
                              Pinned
                            </Badge>
                          ) : null}
                          {post.publish_target === "facebook_page" || post.facebook_status ? (
                            <Badge variant={facebookBadgeVariant(post.facebook_status)}>
                              {facebookStatusLabel(post)}
                            </Badge>
                          ) : null}
                        </div>
                        {post.facebook_post_id ? (
                          <div className="mt-1 text-xs text-muted-foreground">{post.facebook_post_id}</div>
                        ) : null}
                        {post.facebook_error && post.facebook_status !== "posted" && post.facebook_status !== "scheduled" ? (
                          <div className="mt-1 text-xs text-[#fca5a5]">{post.facebook_error}</div>
                        ) : null}
                        {post.facebook_review_note ? (
                          <div className="mt-1 max-w-[16rem] text-xs text-[#fde68a]">{post.facebook_review_note}</div>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <div>{post.platform}</div>
                        {publishTargetLabel(post) ? (
                          <div className="mt-1">
                            <Badge
                              variant={isCopyOnly(post) ? "outline" : "info"}
                              title={isCopyOnly(post) ? facebookBlockedReason(post) : undefined}
                            >
                              {publishTargetLabel(post)}
                            </Badge>
                          </div>
                        ) : null}
                        <div className="text-muted-foreground">{post.hook}</div>
                        <div className="text-muted-foreground">{post.post_type}</div>
                      </TableCell>
                      <TableCell>
                        {post.lot_id ? <Link href={`/lots/${post.lot_id}`}>{post.sku}</Link> : post.sku}
                        <div>{post.title}</div>
                        {post.bucket ? (
                          <div className="mt-1">
                            <Badge variant={post.bucket === "Push" ? "default" : "info"}>{post.bucket}</Badge>
                          </div>
                        ) : null}
                        {post.item_url ? (
                          <div>
                            <a href={post.item_url} target="_blank" rel="noreferrer">
                              Listing
                            </a>
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell className="min-w-[16rem]">
                        {post.facebook_caption ? (
                          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            {post.facebook_status === "posted" ? "Posted caption" : "Approved caption"}
                          </div>
                        ) : null}
                        <pre className="m-0 whitespace-pre-wrap font-sans text-sm text-foreground">
                          {post.facebook_caption || post.paste_copy}
                        </pre>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <Button type="button" variant="ghost" size="sm" onClick={() => void copyPost(post)}>
                            {copied === calendarRowKey(post) ? "Copied" : "Copy"}
                          </Button>
                          {post.group_copy ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              title="Shorter caption for sharing a Page post into a Facebook group"
                              onClick={() => void copyGroupPost(post)}
                            >
                              {copiedGroup === calendarRowKey(post) ? "Copied for groups" : "Copy for groups"}
                            </Button>
                          ) : null}
                        </div>
                        <div className="mt-1 text-muted-foreground">{post.preflight}</div>
                      </TableCell>
                      {canPostFacebook ? (
                        <TableCell>
                          <div className="flex flex-col items-start gap-1.5">
                            {isCopyOnly(post) ? null : (
                              <Button
                                type="button"
                                size="sm"
                                disabled={!post.facebook_schedulable || !post.lot_id || !post.slot_key}
                                title={
                                  post.facebook_schedulable
                                    ? `Preview caption and photos, then approve this ${publishTargetLabel(post) || "Page"} slot`
                                    : facebookBlockedReason(post)
                                }
                                onClick={() => openCalendarApprove([post])}
                              >
                                Approve
                              </Button>
                            )}
                            {post.facebook_can_post_now && post.lot_id && post.slot_key ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                title="Preview caption and photos, then publish this slot immediately"
                                onClick={() => openCalendarApprove([post], { postNow: true })}
                              >
                                Post now
                              </Button>
                            ) : null}
                            {post.facebook_can_cancel && post.slot_key ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="danger-outline"
                                disabled={cancellingKey === post.slot_key}
                                onClick={() => void cancelCalendarSlot(post)}
                              >
                                {cancellingKey === post.slot_key ? "Removing…" : "Cancel / Unapprove"}
                              </Button>
                            ) : null}
                            {!post.facebook_schedulable && facebookBlockedReason(post) ? (
                              <div className="max-w-[14rem] text-xs text-muted-foreground">{facebookBlockedReason(post)}</div>
                            ) : null}
                          </div>
                        </TableCell>
                      ) : null}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
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
          publishNow={facebookPublishNow}
          onClose={() => {
            setFacebookLotIds(null);
            setFacebookLotSchedules(null);
            setFacebookCalendarSlots(null);
            setFacebookModalSource("push_queue");
            setFacebookPublishNow(false);
          }}
          onPosted={() => {
            if (token && saleId) void loadSale(token, saleId);
          }}
        />
      ) : null}
    </AppShell>
  );
}
