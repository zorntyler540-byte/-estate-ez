"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../lib/api";
import { LotPhotoThumb } from "./LotPhotoThumb";

export type FacebookPostSource = "lot" | "selection" | "push_queue";

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
  photo_ids: string[];
  photo_count: number;
  already_posted: boolean;
  existing_posts: ExistingPost[];
  error?: string | null;
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

export function FacebookPagePostModal({ token, lotIds, source, onClose, onPosted }: Props) {
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [captions, setCaptions] = useState<Record<string, string>>({});
  const [followHook, setFollowHook] = useState(false);
  const [force, setForce] = useState(false);
  const [scheduleMode, setScheduleMode] = useState<"now" | "slot" | "custom">("now");
  const [slotAt, setSlotAt] = useState("");
  const [customAt, setCustomAt] = useState("");
  const [results, setResults] = useState<PublishResult[] | null>(null);
  const [batchProgress, setBatchProgress] = useState("");

  const loadPreview = useCallback(
    async (hook: boolean) => {
      setLoading(true);
      setError("");
      setResults(null);
      try {
        const data = (await apiFetch("/facebook/posts/preview", token, {
          method: "POST",
          body: JSON.stringify({ lot_ids: lotIds, include_follow_hook: hook })
        })) as Preview;
        setPreview(data);
        setFollowHook(Boolean(data.include_follow_hook));
        const next: Record<string, string> = {};
        for (const lot of data.lots) next[lot.lot_id] = lot.caption;
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
    [lotIds, token]
  );

  useEffect(() => {
    if (!token || lotIds.length < 1) return;
    void loadPreview(false);
  }, [token, lotIds, loadPreview]);

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
      const res = (await apiFetch("/integrations/facebook/oauth/start", token)) as { url: string };
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
    const items = (preview.lots || [])
      .filter((lot) => !lot.error)
      .map((lot) => {
        const row: {
          lot_id: string;
          caption: string;
          scheduled_publish_time?: string;
        } = {
          lot_id: lot.lot_id,
          caption: (captions[lot.lot_id] || "").trim()
        };
        if (scheduleMode === "slot" && slotAt) row.scheduled_publish_time = slotAt;
        if (scheduleMode === "custom" && customAt) {
          const iso = new Date(customAt).toISOString();
          row.scheduled_publish_time = iso;
        }
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
      const res = (await apiFetch("/facebook/posts", token, {
        method: "POST",
        body: JSON.stringify({
          confirm: true,
          force,
          include_follow_hook: followHook,
          source,
          items
        })
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

  const title =
    lotIds.length === 1 ? "Post this lot to Facebook Page" : `Post ${lotIds.length} lots to Facebook Page`;

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
          Instagram, Stories, Groups, and Marketplace are not part of this.
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
            {alreadyPosted > 0 ? (
              <label className="ct-row" style={{ gap: "0.4rem", marginBottom: "0.65rem" }}>
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                <span>
                  {alreadyPosted} already posted or scheduled. Check this to post again.
                </span>
              </label>
            ) : null}

            <div className="ct-fb-post-list">
              {preview.lots.map((lot) => (
                <article key={lot.lot_id} className="ct-card ct-card--padded" style={{ marginBottom: "0.65rem" }}>
                  <strong>
                    {lot.sku || "Lot"} — {lot.title || "Untitled"}
                  </strong>
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
                        value={captions[lot.lot_id] || ""}
                        onChange={(e) =>
                          setCaptions((prev) => ({ ...prev, [lot.lot_id]: e.target.value }))
                        }
                        rows={8}
                        aria-label={`Caption for ${lot.sku || lot.title}`}
                      />
                    </>
                  )}
                </article>
              ))}
            </div>

            {results ? (
              <div className="ct-banner" style={{ marginBottom: "0.75rem" }}>
                {results.map((row) => (
                  <div key={row.lot_id}>
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
                disabled={posting || blocked === preview.lots.length}
                onClick={() => void publish()}
              >
                {posting
                  ? batchProgress || "Posting…"
                  : scheduleMode === "now"
                    ? "Post to Page"
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
