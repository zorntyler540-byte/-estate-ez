"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../lib/api";
import { LotPhotoThumb } from "./LotPhotoThumb";

export type FacebookPostSource = "lot" | "selection" | "push_queue" | "push_calendar";

export type FacebookCalendarSlot = {
  slot_key: string;
  lot_id: string;
  at: string;
  platform: string;
  hook?: string;
  local_time?: string;
};

type ExistingPost = {
  id: string;
  status: string;
  facebook_post_id?: string | null;
  scheduled_publish_time?: string | null;
  posted_at?: string | null;
  created_at?: string | null;
};

type PreviewLot = {
  lot_id: string;
  sku?: string | null;
  title: string;
  item_url: string;
  sale_ends_at?: string | null;
  caption: string;
  group_caption?: string;
  photo_ids: string[];
  photo_count: number;
  already_posted: boolean;
  existing_posts: ExistingPost[];
  error?: string | null;
  calendar_key?: string | null;
  intended_platform?: string | null;
  publish_target?: string | null;
  local_time?: string | null;
  hook?: string | null;
};

type ScheduleSuggestion = { at: string; label: string };

type Preview = {
  configured: boolean;
  connected: boolean;
  needs_reconnect: boolean;
  page_id?: string | null;
  page_name?: string | null;
  include_follow_hook: boolean;
  follow_hook_line: string;
  schedule_suggestions: ScheduleSuggestion[];
  lots: PreviewLot[];
};

type PublishResult = {
  lot_id: string;
  ok: boolean;
  skipped?: boolean;
  facebook_post_id?: string | null;
  status?: string | null;
  needs_reconnect?: boolean;
  error?: string | null;
  calendar_key?: string | null;
};

type PublishOut = {
  queued?: boolean;
  batch_id?: string | null;
  active?: boolean;
  total?: number;
  done?: number;
  posted?: number;
  failed?: number;
  results?: PublishResult[];
};

type Props = {
  token: string;
  lotIds: string[];
  source: FacebookPostSource;
  lotSchedules?: Record<string, string>;
  calendarSlots?: FacebookCalendarSlot[];
  calendarSaleId?: number | null;
  publishNow?: boolean;
  onClose: () => void;
  onPosted?: () => void;
};

const FOLLOW_HOOK = "Follow our Page for upcoming sales.";

function localInputValue(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function applyFollowHook(caption: string, include: boolean): string {
  let text = caption.replace(/\s+$/, "");
  if (text.endsWith(FOLLOW_HOOK)) {
    text = text.slice(0, -FOLLOW_HOOK.length).replace(/\s+$/, "");
  }
  if (!include) return text;
  return text ? `${text}\n\n${FOLLOW_HOOK}` : FOLLOW_HOOK;
}

function previewKey(lot: PreviewLot): string {
  return lot.calendar_key || lot.lot_id;
}

const SALE_PREFIX = "See the whole sale:";

function groupFriendlyCopy(caption: string, itemUrl: string, fallback: string): string {
  if (fallback && !caption.trim()) return fallback;
  let text = caption.replace(/\s+$/, "");
  if (text.endsWith(FOLLOW_HOOK)) {
    text = text.slice(0, -FOLLOW_HOOK.length).replace(/\s+$/, "");
  }
  const body: string[] = [];
  let bidding = "";
  let saleLine = "";
  let lotLine = "";
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line === FOLLOW_HOOK) continue;
    if (/^#\w/.test(line) && !line.includes(" ")) continue;
    if (/^Bidding closes\b/i.test(line)) {
      bidding = line.endsWith(".") ? line : `${line}.`;
      continue;
    }
    if (line.startsWith(SALE_PREFIX)) {
      saleLine = line;
      continue;
    }
    if (itemUrl && (line === itemUrl || line.replace(/\/$/, "") === itemUrl.replace(/\/$/, ""))) {
      lotLine = itemUrl;
      continue;
    }
    if (body.join(" ").length < 180) body.push(line);
  }
  const hook = body.join(" ").slice(0, 240).trim();
  const ships = "Ships nationwide.";
  const shipLine = [hook, bidding].some((part) => (part || "").toLowerCase().includes("ships nationwide"))
    ? ""
    : ships;
  const parts = [hook, bidding, shipLine, lotLine || itemUrl, saleLine].filter(Boolean);
  return parts.join("\n\n").trim() || fallback;
}

export function FacebookPagePostModal({
  token,
  lotIds,
  source,
  lotSchedules,
  calendarSlots,
  calendarSaleId,
  publishNow = false,
  onClose,
  onPosted
}: Props) {
  const calendarMode =
    source === "push_calendar" && Boolean(calendarSlots?.length || lotSchedules);
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [captions, setCaptions] = useState<Record<string, string>>({});
  const [followHook, setFollowHook] = useState(false);
  const [force, setForce] = useState(false);
  const [confirmImmediate, setConfirmImmediate] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"now" | "slot" | "custom">("now");
  const [slotAt, setSlotAt] = useState("");
  const [customAt, setCustomAt] = useState("");
  const [results, setResults] = useState<PublishResult[] | null>(null);
  const [batchProgress, setBatchProgress] = useState("");
  const [copiedGroup, setCopiedGroup] = useState("");
  const immediate = publishNow || (!calendarMode && scheduleMode === "now");

  const loadPreview = useCallback(
    async (hook: boolean) => {
      setLoading(true);
      setError("");
      setResults(null);
      try {
        const data = (await apiFetch(
          calendarMode && calendarSaleId
            ? `/engagement/sales/${calendarSaleId}/calendar/facebook/preview`
            : "/facebook/posts/preview",
          token,
          {
            method: "POST",
            body: JSON.stringify(
              calendarMode && calendarSaleId
                ? {
                    include_follow_hook: hook,
                    slot_keys: (calendarSlots || []).map((slot) => slot.slot_key)
                  }
                : { lot_ids: lotIds, include_follow_hook: hook }
            )
          }
        )) as Preview;
        setPreview(data);
        setFollowHook(Boolean(data.include_follow_hook));
        setConfirmImmediate(false);
        const next: Record<string, string> = {};
        for (const lot of data.lots) next[previewKey(lot)] = lot.caption;
        setCaptions(next);
        if (data.schedule_suggestions[0]) {
          setSlotAt(data.schedule_suggestions[0].at);
          setCustomAt(localInputValue(data.schedule_suggestions[0].at));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not draft Facebook posts");
      } finally {
        setLoading(false);
      }
    },
    [calendarMode, calendarSaleId, calendarSlots, lotIds, token]
  );

  useEffect(() => {
    if (!token) return;
    if (calendarMode) {
      if (!calendarSaleId || !(calendarSlots || []).length) return;
    } else if (lotIds.length < 1) {
      return;
    }
    void loadPreview(false);
  }, [token, lotIds, calendarMode, calendarSaleId, calendarSlots, loadPreview]);

  const alreadyPosted = useMemo(
    () => (preview?.lots || []).filter((lot) => lot.already_posted).length,
    [preview]
  );
  const blocked = useMemo(
    () => (preview?.lots || []).filter((lot) => lot.error).length,
    [preview]
  );

  function toggleFollow(next: boolean) {
    setFollowHook(next);
    setCaptions((prev) => {
      const out: Record<string, string> = {};
      for (const [id, caption] of Object.entries(prev)) out[id] = applyFollowHook(caption, next);
      return out;
    });
  }

  async function startConnect() {
    setError("");
    try {
      const res = (await apiFetch("/integrations/facebook/oauth/start", token, {
        credentials: "include"
      })) as { url: string };
      if (res.url) window.location.href = res.url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start Facebook sign-in");
    }
  }

  async function waitForBatch(batchId: string): Promise<PublishResult[]> {
    for (;;) {
      const row = (await apiFetch(`/facebook/posts/batches/${batchId}`, token)) as PublishOut;
      const done = Number(row.done || 0);
      const total = Number(row.total || 0);
      if (total > 0) setBatchProgress(`Posting ${done} of ${total}…`);
      if (!row.active) return row.results || [];
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }

  async function publish() {
    if (!preview?.connected || posting) return;
    if (immediate && !confirmImmediate) {
      setError("Check the confirmation box before posting now. It will go live on the Page immediately.");
      return;
    }
    const items = (preview.lots || [])
      .filter((lot) => !lot.error)
      .map((lot) => {
        const key = previewKey(lot);
        const row: {
          lot_id: string;
          caption: string;
          scheduled_publish_time?: string;
          calendar_key?: string;
          calendar_sale_id?: number;
          photo_ids?: string[];
        } = {
          lot_id: lot.lot_id,
          caption: (captions[key] || "").trim()
        };
        const slotAtIso =
          lot.calendar_key && calendarSlots?.find((slot) => slot.slot_key === lot.calendar_key)?.at;
        if (calendarMode && !publishNow && (slotAtIso || lotSchedules?.[lot.lot_id])) {
          row.scheduled_publish_time = slotAtIso || lotSchedules?.[lot.lot_id];
        } else if (!calendarMode && scheduleMode === "slot" && slotAt) {
          row.scheduled_publish_time = slotAt;
        } else if (!calendarMode && scheduleMode === "custom" && customAt) {
          const iso = new Date(customAt).toISOString();
          row.scheduled_publish_time = iso;
        }
        if (calendarMode && calendarSaleId) {
          row.calendar_key = lot.calendar_key || `pushcal:${calendarSaleId}:${lot.lot_id}`;
          row.calendar_sale_id = calendarSaleId;
        }
        if (lot.photo_ids?.length) row.photo_ids = lot.photo_ids;
        return row;
      })
      .filter((row) => row.caption);
    if (!items.length) {
      setError("Every lot needs a caption and at least one listing photo.");
      return;
    }
    setPosting(true);
    setError("");
    setBatchProgress(items.length > 1 ? `Queueing ${items.length} posts…` : "Posting…");
    try {
      const path =
        calendarMode && calendarSaleId
          ? `/engagement/sales/${calendarSaleId}/calendar/facebook`
          : "/facebook/posts";
      const body =
        calendarMode && calendarSaleId
          ? {
              confirm: true,
              force,
              include_follow_hook: followHook,
              post_now: publishNow,
              slot_keys: items.map((row) => row.calendar_key).filter(Boolean),
              captions: Object.fromEntries(items.map((row) => [row.calendar_key || row.lot_id, row.caption]))
            }
          : {
              confirm: true,
              force,
              include_follow_hook: followHook,
              source,
              items
            };
      const res = (await apiFetch(path, token, {
        method: "POST",
        body: JSON.stringify(body)
      })) as PublishOut;
      let rows = res.results || [];
      if (res.queued && res.batch_id) {
        rows = await waitForBatch(res.batch_id);
      }
      setResults(rows);
      if (rows.some((row) => row.ok)) onPosted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Facebook post failed");
    } finally {
      setBatchProgress("");
      setPosting(false);
    }
  }

  const title = calendarMode
    ? publishNow
      ? lotIds.length === 1
        ? "Post this Push-calendar Facebook slot now"
        : `Post ${lotIds.length} Push-calendar Facebook slots now`
      : lotIds.length === 1
        ? "Approve this Push-calendar Facebook slot"
        : `Approve ${lotIds.length} Push-calendar Facebook slots`
    : lotIds.length === 1
      ? "Post this lot to Facebook Page"
      : `Post ${lotIds.length} lots to Facebook Page`;

  return (
    <div className="ct-sale-covers-modal" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="ct-sale-covers-modal__panel ct-fb-post-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ct-sale-covers-modal__head">
          <h3 style={{ margin: 0 }}>{title}</h3>
          <button type="button" className="ct-btn ct-btn--ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="ct-muted" style={{ marginTop: "0.35rem" }}>
          Nothing goes to Facebook until you click Post to Page. SKU-tape photos stay off the Page.
          Native Instagram, Stories, Groups, Marketplace, and TikTok publishers are not wired yet.
        </p>
        {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
        {loading ? <p className="ct-muted">Drafting captions…</p> : null}

        {!loading && preview && !preview.configured ? (
          <p className="ct-muted">Facebook Page posting is not configured on this server.</p>
        ) : null}

        {!loading && preview?.configured && (!preview.connected || preview.needs_reconnect) ? (
          <div className="ct-banner ct-banner--warning">
            {preview.needs_reconnect
              ? "Facebook access expired or was revoked. Reconnect the Page, then post."
              : "Connect a Facebook Page on Account settings first."}
            <div style={{ marginTop: "0.55rem" }}>
              <button type="button" className="ct-btn ct-btn--primary" onClick={() => void startConnect()}>
                {preview.needs_reconnect ? "Reconnect Facebook" : "Connect Facebook Page"}
              </button>
            </div>
          </div>
        ) : null}

        {!loading && preview?.connected && !preview.needs_reconnect ? (
          <>
            <p style={{ marginTop: 0 }}>
              Posting as <strong>{preview.page_name || "Facebook Page"}</strong>
            </p>
            <label className="ct-row" style={{ gap: "0.4rem", marginBottom: "0.65rem" }}>
              <input
                type="checkbox"
                checked={followHook}
                onChange={(e) => toggleFollow(e.target.checked)}
              />
              <span>Add “{FOLLOW_HOOK}”</span>
            </label>
            {calendarMode ? (
              <p className="ct-muted">
                {publishNow
                  ? "This publishes to the Facebook Page immediately. The spreadsheet time is ignored, and Facebook's 10-minute schedule window does not apply. Nothing is sent until you confirm and click Post to Page now."
                  : "Each spreadsheet slot is scheduled as a Facebook Page feed post at its calendar time. Captions and photos vary by slot. Nothing is sent until you click Schedule on Page."}
              </p>
            ) : (
            <div className="ct-field" style={{ marginBottom: "0.75rem" }}>
              <span className="ct-muted">When</span>
              <div className="ct-row" style={{ flexWrap: "wrap", gap: "0.5rem", marginTop: "0.35rem" }}>
                <label className="ct-row" style={{ gap: "0.3rem" }}>
                  <input
                    type="radio"
                    name="fb-when"
                    checked={scheduleMode === "now"}
                    onChange={() => setScheduleMode("now")}
                  />
                  Post now
                </label>
                <label className="ct-row" style={{ gap: "0.3rem" }}>
                  <input
                    type="radio"
                    name="fb-when"
                    checked={scheduleMode === "slot"}
                    onChange={() => setScheduleMode("slot")}
                    disabled={!preview.schedule_suggestions.length}
                  />
                  Suggested time
                </label>
                <label className="ct-row" style={{ gap: "0.3rem" }}>
                  <input
                    type="radio"
                    name="fb-when"
                    checked={scheduleMode === "custom"}
                    onChange={() => setScheduleMode("custom")}
                  />
                  Custom (10 min–75 days)
                </label>
              </div>
              {scheduleMode === "slot" ? (
                <select
                  className="ct-input"
                  style={{ marginTop: "0.4rem", maxWidth: 420 }}
                  value={slotAt}
                  onChange={(e) => setSlotAt(e.target.value)}
                >
                  {preview.schedule_suggestions.map((slot) => (
                    <option key={slot.at} value={slot.at}>
                      {slot.label}
                    </option>
                  ))}
                </select>
              ) : null}
              {scheduleMode === "custom" ? (
                <input
                  className="ct-input"
                  style={{ marginTop: "0.4rem", maxWidth: 280 }}
                  type="datetime-local"
                  value={customAt}
                  onChange={(e) => setCustomAt(e.target.value)}
                />
              ) : null}
            </div>
            )}
            {alreadyPosted > 0 ? (
              <label className="ct-row" style={{ gap: "0.4rem", marginBottom: "0.65rem" }}>
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                <span>
                  {alreadyPosted} already posted or scheduled. Check this to post again.
                </span>
              </label>
            ) : null}
            {immediate ? (
              <label className="ct-row" style={{ gap: "0.4rem", marginBottom: "0.65rem", alignItems: "flex-start" }}>
                <input
                  type="checkbox"
                  checked={confirmImmediate}
                  onChange={(e) => setConfirmImmediate(e.target.checked)}
                />
                <span>
                  Post now — this goes live on the Page immediately. I have checked the listing.
                </span>
              </label>
            ) : null}

            <div className="ct-fb-post-list">
              {preview.lots.map((lot) => (
                <article key={previewKey(lot)} className="ct-card ct-card--padded" style={{ marginBottom: "0.65rem" }}>
                  <strong>
                    {lot.sku || "Lot"} — {lot.title || "Untitled"}
                  </strong>
                  {calendarMode && (lot.intended_platform || lot.local_time) ? (
                    <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
                      {lot.local_time ? `${lot.local_time} PT · ` : ""}
                      {lot.intended_platform && lot.intended_platform !== "Facebook"
                        ? `Posting to Facebook in place of ${lot.intended_platform}`
                        : "Facebook Page feed"}
                      {lot.hook ? ` · ${lot.hook}` : ""}
                    </p>
                  ) : null}
                  {lot.item_url ? (
                    <div>
                      <a href={lot.item_url} target="_blank" rel="noreferrer">
                        CT Bids listing
                      </a>
                    </div>
                  ) : (
                    <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
                      No CT Bids link yet — add it in the caption if you have one.
                    </p>
                  )}
                  {lot.already_posted ? (
                    <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
                      Already on the Page
                      {lot.existing_posts[0]?.facebook_post_id
                        ? ` (${lot.existing_posts[0].facebook_post_id})`
                        : ""}
                      .
                    </p>
                  ) : null}
                  {calendarMode ? (
                    <p className="ct-muted" style={{ margin: "0.25rem 0 0" }}>
                      Calendar slot:{" "}
                      {new Date(
                        calendarSlots?.find((slot) => slot.slot_key === lot.calendar_key)?.at ||
                          lotSchedules?.[lot.lot_id] ||
                          ""
                      ).toLocaleString()}
                    </p>
                  ) : null}
                  {lot.error ? (
                    <p className="ct-error" style={{ margin: "0.35rem 0 0" }}>
                      {lot.error}
                    </p>
                  ) : (
                    <>
                      <div className="ct-row" style={{ gap: "0.4rem", flexWrap: "wrap", margin: "0.45rem 0" }}>
                        {lot.photo_ids.slice(0, 6).map((photoId) => (
                          <LotPhotoThumb
                            key={photoId}
                            lotId={lot.lot_id}
                            photoId={photoId}
                            token={token}
                            label="Listing photo"
                            maxPx={160}
                            minHeight={56}
                            maxHeight={72}
                          />
                        ))}
                        {lot.photo_count > 6 ? (
                          <span className="ct-muted">+{lot.photo_count - 6} more</span>
                        ) : null}
                      </div>
                      <textarea
                        className="ct-input ct-fb-caption"
                        value={captions[previewKey(lot)] || ""}
                        onChange={(e) =>
                          setCaptions((prev) => ({ ...prev, [previewKey(lot)]: e.target.value }))
                        }
                        rows={8}
                        aria-label={`Caption for ${lot.sku || lot.title}`}
                      />
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        style={{ marginTop: "0.4rem" }}
                        onClick={() => {
                          const text = groupFriendlyCopy(
                            captions[previewKey(lot)] || "",
                            lot.item_url,
                            lot.group_caption || ""
                          );
                          void navigator.clipboard.writeText(text).then(
                            () => setCopiedGroup(previewKey(lot)),
                            () => setError("Could not copy group caption")
                          );
                        }}
                      >
                        {copiedGroup === previewKey(lot) ? "Copied for groups" : "Copy for groups"}
                      </button>
                    </>
                  )}
                </article>
              ))}
            </div>

            {results ? (
              <div className="ct-banner" style={{ marginBottom: "0.75rem" }}>
                {results.map((row) => (
                  <div key={row.calendar_key || row.lot_id}>
                    {row.ok
                      ? `${row.status === "scheduled" ? "Scheduled" : "Posted"} ${row.facebook_post_id || ""}`
                      : row.error || "Failed"}
                  </div>
                ))}
              </div>
            ) : null}

            {posting && batchProgress ? <p className="ct-muted">{batchProgress}</p> : null}
            <div className="ct-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={posting || blocked === preview.lots.length || (immediate && !confirmImmediate)}
                onClick={() => void publish()}
              >
                {posting
                  ? batchProgress || "Posting…"
                  : immediate
                    ? "Post to Page now"
                    : "Schedule on Page"}
              </button>
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => void loadPreview(followHook)}>
                Redraft captions
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
