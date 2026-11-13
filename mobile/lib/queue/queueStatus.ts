import type { ConnectionPhase } from "../connectivity";
import { formatMediaCounts } from "../mediaKind";
import { isPausedMessage } from "../uploadPause";
import { isPacedMessage } from "./importPacing";
import { isStillCatalogingMessage } from "./archiveCataloged";
import { formatRetryWait } from "./retryPolicy";
import { isTransientSyncError } from "./syncRecovery";

export type QueueRowTone = "ok" | "progress" | "warn" | "error";

export type QueueRowView = {
  label: string;
  hint: string;
  tone: QueueRowTone;
  /** Extra line only when it adds facts the label/hint do not already cover. */
  detail: string | null;
  showResend: boolean;
  /** Permanent failure: Sync stopped retrying until staff tap Retry. */
  showRetry: boolean;
  removeLabel: "Delete" | "Remove";
  removeTitle: string;
  removeBody: string;
  /** Set when Remove needs a second "are you sure" (photos sent, website not confirmed yet). */
  removeConfirm: { title: string; body: string } | null;
};

export type QueueRowInput = {
  status: string;
  error_message?: string | null;
  archived_at?: number | null;
  server_lot_id?: string | null;
  photo_count?: number;
  video_count?: number;
  manual_sku?: string | null;
  online?: boolean | null;
  /** Auto-upload state (offline / waiting for a stable connection / Wi‑Fi only …). */
  connection?: ConnectionPhase | null;
  syncing?: boolean;
  /** ``permanent`` / ``transient`` from the last failed attempt (null on older rows). */
  error_kind?: string | null;
  next_attempt_at?: number | null;
  /** Add-photos session for a lot already on the website. */
  append_lot_id?: string | null;
  append_label?: string | null;
  now?: number;
};

/**
 * Clips the website refused for good (``lot_photos.upload_blocked``). Sync stops resending them,
 * so the row has to say why — otherwise the lot sits in Queue with no explanation.
 */
export function blockedClipLine(reasons: readonly (string | null | undefined)[]): string | null {
  const cleaned = reasons.map((r) => (r || "").trim()).filter(Boolean);
  if (!cleaned.length) return null;
  const unique = [...new Set(cleaned)];
  const n = cleaned.length;
  const head = n === 1 ? "1 video was not accepted by the website" : `${n} videos were not accepted by the website`;
  return `${head}: ${unique.join("; ")}. It stays on this phone until you remove it.`;
}

function mediaWord(item: { photo_count?: number; video_count?: number }): string {
  return formatMediaCounts(item.photo_count || 0, item.video_count || 0);
}

export function queueRowTitle(item: {
  archived_at?: number | null;
  photo_count?: number;
  video_count?: number;
  manual_sku?: string | null;
  append_lot_id?: string | null;
  append_label?: string | null;
}): string {
  const sku = (item.manual_sku || "").trim();
  const media = mediaWord(item);
  if (item.append_lot_id) {
    const label = (item.append_label || "").trim() || "website lot";
    return item.archived_at ? `${media} added to ${label}` : `Add ${media} to ${label}`;
  }
  if (item.archived_at) {
    return sku ? `${sku} · ${media} · in Estate-EZ` : `${media} · in Estate-EZ`;
  }
  return sku ? `${sku} · ${media}` : media;
}

/** Resend starts a second catalog job — only when photos are still local and the website does not have this lot. */
export function canResendQueueRow(item: {
  status: string;
  archived_at?: number | null;
  photo_count?: number;
  server_lot_id?: string | null;
  error_message?: string | null;
  append_lot_id?: string | null;
}): boolean {
  if (item.archived_at || item.append_lot_id) return false;
  if ((item.photo_count || 0) <= 0) return false;
  if (item.status !== "uploaded") return false;
  if (item.server_lot_id) return false;
  if (isStillCatalogingMessage(item.error_message)) return false;
  if (isTransientSyncError(item.error_message)) return false;
  return true;
}

function detailIfUseful(error: string | null | undefined, hint: string, hide: boolean): string | null {
  if (hide) return null;
  const text = (error || "").trim();
  if (!text) return null;
  if (hint.toLowerCase().includes(text.toLowerCase())) return null;
  return text;
}

/**
 * Sent, but the website has not confirmed the lot. If catalog later fails, the phone copy is the
 * only copy left — so Remove asks twice.
 */
export function removeNeedsSecondConfirm(item: {
  status: string;
  archived_at?: number | null;
  server_lot_id?: string | null;
  append_lot_id?: string | null;
}): boolean {
  if (item.archived_at || item.server_lot_id || item.append_lot_id) return false;
  return item.status === "uploaded" || item.status === "uploading";
}

/** Lots on this phone still to send — what "Offline, N lots waiting" counts. */
export function countWaitingLots(
  rows: readonly { status: string; archived_at?: number | null; error_kind?: string | null }[]
): number {
  return rows.filter(
    (r) =>
      !r.archived_at &&
      (r.status === "queued" ||
        r.status === "uploading" ||
        (r.status === "failed" && r.error_kind !== "permanent"))
  ).length;
}

function queuedHint(item: QueueRowInput): string {
  if (item.syncing) return "In line — Sync is sending lots now.";
  const phase = item.connection ?? (item.online === false ? "offline" : null);
  switch (phase) {
    case "offline":
      return "Will send when you're back online.";
    case "waiting_stable":
    case "checking":
      return "Waiting for a stable connection, then uploads on its own.";
    case "unreachable":
      return "Website is not answering — uploads on its own when it does.";
    case "wifi_required":
      return "Waiting for Wi‑Fi (Upload on Wi‑Fi only is on).";
    case "ready":
      return "Uploads on its own — or tap Sync now.";
    default:
      return "Tap Sync now to send.";
  }
}

export function queueRowView(item: QueueRowInput): QueueRowView {
  const archived = Boolean(item.archived_at);
  const error = (item.error_message || "").trim() || null;
  const linked = Boolean(item.server_lot_id);
  const cataloging = isStillCatalogingMessage(error);
  const transient = isTransientSyncError(error);
  const showResend = canResendQueueRow(item);
  const phoneOnlyRemove = archived || item.status === "uploaded" || linked;
  const now = item.now ?? Date.now();

  const remove = {
    ...(phoneOnlyRemove
      ? {
          removeLabel: "Remove" as const,
          removeTitle: "Remove from this phone?",
          removeBody: archived
            ? "This listing stays on the website. This only removes the phone archive row."
            : `${mediaWord(item)} already sent. This only removes the copy on this phone — the website listing stays.`
        }
      : {
          removeLabel: "Delete" as const,
          removeTitle: item.append_lot_id ? "Delete these new shots?" : "Delete local lot?",
          removeBody: item.append_lot_id
            ? `Delete ${mediaWord(item)} from this phone? They have not all reached the website lot yet. This cannot be undone.`
            : `Delete ${mediaWord(item)} from this phone? This cannot be undone.`
        }),
    showRetry: false,
    removeConfirm: removeNeedsSecondConfirm(item)
      ? {
          title: "Website has not confirmed this lot",
          body: "Catalog has not finished yet. If it fails, the photos on this phone are the only copy. Remove anyway?"
        }
      : null
  };

  if (!archived && item.status === "failed" && item.error_kind === "permanent") {
    return {
      label: "Needs attention",
      hint: "The website refused this lot, so Sync stopped retrying. Fix the problem below, then tap Retry.",
      tone: "error",
      detail: error || "The website refused this upload.",
      showResend: false,
      ...remove,
      showRetry: true
    };
  }
  if (!archived && item.status === "failed" && item.error_kind === "transient") {
    const wait = formatRetryWait(item.next_attempt_at, now);
    return {
      label: "Will retry",
      hint: wait
        ? `${wait}. Sync now retries right away. Do not Resend if the lot is already on the website.`
        : "Retries on the next Sync. Do not Resend if the lot is already on the website.",
      tone: "warn",
      detail: error,
      showResend: false,
      ...remove
    };
  }

  if (archived) {
    return {
      label: "In Estate-EZ",
      hint: "On this sale in Estate-EZ. It is not in the seller app until someone posts it from the website. Add photos, or Remove when you are done on this phone.",
      tone: "ok",
      detail: null,
      showResend: false,
      ...remove
    };
  }

  if (linked && item.status !== "uploading" && item.status !== "queued" && item.status !== "draft") {
    return {
      label: "In Estate-EZ",
      hint: "On this sale in Estate-EZ. It is not in the seller app until someone posts it from the website. Next Sync moves this to Phone archive.",
      tone: "ok",
      detail: null,
      showResend: false,
      ...remove
    };
  }

  switch (item.status) {
    case "importing":
      return {
        label: "Importing…",
        hint: "Copying photos from the camera roll. The lot queues for upload when every photo is copied.",
        tone: "progress",
        detail: null,
        showResend: false,
        ...remove
      };
    case "draft":
      return {
        label: "Saved on phone",
        hint: "Tap Add photos to keep shooting, or Queue for upload.",
        tone: "ok",
        detail: null,
        showResend: false,
        ...remove
      };
    case "queued":
      if (isPacedMessage(error)) {
        return {
          label: "Waiting for website",
          hint: "Big imports go a few lots at a time so the website's limits are not hit. Nothing to do here.",
          tone: "progress",
          detail: error,
          showResend: false,
          ...remove
        };
      }
      if (isPausedMessage(error)) {
        return {
          label: "Paused",
          hint: `${queuedHint(item)} Photos already sent are not sent again.`,
          tone: "progress",
          detail: error,
          showResend: false,
          ...remove
        };
      }
      return {
        label: "Ready to upload",
        hint: queuedHint(item),
        tone: "progress",
        detail: null,
        showResend: false,
        ...remove
      };
    case "uploading":
      if (item.syncing === false) {
        // App was killed (or the phone restarted) mid-upload: nothing is sending this right now.
        return {
          label: "Interrupted",
          hint: `${queuedHint(item)} Photos already sent are not sent again.`,
          tone: "progress",
          detail: null,
          showResend: false,
          ...remove
        };
      }
      return {
        label: "Uploading…",
        hint: "Sending to the server. The website listing appears after catalog finishes.",
        tone: "progress",
        detail: detailIfUseful(error, "Sending to the server.", false),
        showResend: false,
        ...remove
      };
    case "uploaded":
      if (cataloging) {
        return {
          label: "Cataloging…",
          hint: "Photos are on the server. Tap Sync now to check. Do not Resend.",
          tone: "progress",
          detail: null,
          showResend: false,
          ...remove
        };
      }
      if (transient) {
        return {
          label: "Needs a check",
          hint: "Tap Sync now. If the lot is already on the website, Remove — do not Resend.",
          tone: "warn",
          detail: detailIfUseful(error, "Tap Sync now.", false),
          showResend: false,
          ...remove
        };
      }
      return {
        label: "Sent",
        hint: "Tap Sync now to confirm it is on the website. Resend only if that listing is missing there.",
        tone: "progress",
        detail: detailIfUseful(error, "Tap Sync now to confirm it is on the website.", false),
        showResend,
        ...remove
      };
    case "failed":
      if (transient || !error) {
        return {
          label: "Needs a check",
          hint: "Tap Sync now. If the lot is already on the website, Remove — do not Resend.",
          tone: "warn",
          detail: error ? detailIfUseful(error, "Tap Sync now.", false) : "The last upload stopped without an error message.",
          showResend: false,
          ...remove
        };
      }
      return {
        label: "Failed",
        hint: "Tap Sync now to retry. Do not Resend if this lot is already on the website.",
        tone: "error",
        detail: error,
        showResend: false,
        ...remove
      };
    default:
      return {
        label: item.status,
        hint: "",
        tone: "ok",
        detail: error,
        showResend: false,
        ...remove
      };
  }
}

function withHealth(message: string, healthSummary?: string | null): string {
  if (!healthSummary) return message;
  const trimmed = message.replace(/\s+$/, "");
  if (trimmed.endsWith(".")) {
    return `${trimmed.slice(0, -1)} (${healthSummary}).`;
  }
  return `${trimmed} (${healthSummary})`;
}

export function formatSyncOutcome(
  result: {
    uploaded: number;
    failed: number;
    reconciled: number;
    archived?: number;
    healthSummary?: string | null;
    paused?: boolean;
    waitingForWifi?: number;
    deferred?: number;
  },
  online?: boolean | null
): string {
  const wifi = result.waitingForWifi || 0;
  const deferred = result.deferred || 0;
  const wifiTail =
    (wifi ? ` ${wifi} lot${wifi === 1 ? "" : "s"} waiting for Wi‑Fi (Upload on Wi‑Fi only is on).` : "") +
    (deferred
      ? ` ${deferred} more lot${deferred === 1 ? "" : "s"} upload${deferred === 1 ? "s" : ""} as the website catalogs earlier ones.`
      : "");
  if (result.paused) {
    const sent = result.uploaded
      ? `Sent ${result.uploaded} lot${result.uploaded === 1 ? "" : "s"}, then paused`
      : "Paused";
    return `${sent} — the connection dropped. Uploads pick up where they left off once the connection is stable.`;
  }
  if (deferred && !result.uploaded && !result.failed) {
    return `${deferred} lot${deferred === 1 ? "" : "s"} waiting for the website to catalog earlier ones — they upload on their own.${
      wifi ? ` ${wifi} lot${wifi === 1 ? "" : "s"} waiting for Wi‑Fi (Upload on Wi‑Fi only is on).` : ""
    }`;
  }
  return formatSyncOutcomeCore(result, online) + wifiTail;
}

function formatSyncOutcomeCore(
  result: {
    uploaded: number;
    failed: number;
    reconciled: number;
    archived?: number;
    healthSummary?: string | null;
    waitingForWifi?: number;
  },
  online?: boolean | null
): string {
  const archived = result.archived || 0;
  const extraArchive = archived
    ? ` Moved ${archived} cataloged lot${archived === 1 ? "" : "s"} to Phone archive.`
    : "";
  if (result.uploaded || result.failed) {
    return withHealth(
      `Synced ${result.uploaded} lot${result.uploaded === 1 ? "" : "s"}${
        result.failed ? `, ${result.failed} need a check` : ""
      }.${extraArchive}`,
      result.healthSummary
    );
  }
  if (archived) {
    return withHealth(
      `Moved ${archived} cataloged lot${archived === 1 ? "" : "s"} to Phone archive — photos were removed from this phone. Website listings stay.`,
      result.healthSummary
    );
  }
  if (result.reconciled > 0) {
    return withHealth(
      `Cleared ${result.reconciled} leftover notice${result.reconciled === 1 ? "" : "s"} — those lots are already on the website.`,
      result.healthSummary
    );
  }
  if (result.healthSummary) {
    return withHealth("Checked leftover lots on the website.", result.healthSummary);
  }
  if (online === false) {
    return "Offline — connect to Wi‑Fi or cellular to sync.";
  }
  if (result.waitingForWifi) return "Checked the website.";
  return "Nothing queued to upload. Cataloged lots stay in Phone archive.";
}
