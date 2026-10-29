import { isStillCatalogingMessage } from "./archiveCataloged";
import { isTransientSyncError } from "./syncRecovery";

export type QueueRowTone = "ok" | "progress" | "warn" | "error";

export type QueueRowView = {
  label: string;
  hint: string;
  tone: QueueRowTone;
  /** Extra line only when it adds facts the label/hint do not already cover. */
  detail: string | null;
  showResend: boolean;
  removeLabel: "Delete" | "Remove";
  removeTitle: string;
  removeBody: string;
};

export type QueueRowInput = {
  status: string;
  error_message?: string | null;
  archived_at?: number | null;
  server_lot_id?: string | null;
  photo_count?: number;
  manual_sku?: string | null;
  online?: boolean | null;
  syncing?: boolean;
};

function photoWord(n: number): string {
  return `${n} photo${n === 1 ? "" : "s"}`;
}

export function queueRowTitle(item: {
  archived_at?: number | null;
  photo_count?: number;
  manual_sku?: string | null;
}): string {
  const n = item.photo_count || 0;
  const sku = (item.manual_sku || "").trim();
  const photos = photoWord(n);
  if (item.archived_at) {
    return sku ? `${sku} · ${photos} · on website` : `${photos} · on website`;
  }
  return sku ? `${sku} · ${photos}` : photos;
}

/** Resend starts a second catalog job — only when photos are still local and the website does not have this lot. */
export function canResendQueueRow(item: {
  status: string;
  archived_at?: number | null;
  photo_count?: number;
  server_lot_id?: string | null;
  error_message?: string | null;
}): boolean {
  if (item.archived_at) return false;
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

export function queueRowView(item: QueueRowInput): QueueRowView {
  const archived = Boolean(item.archived_at);
  const error = (item.error_message || "").trim() || null;
  const linked = Boolean(item.server_lot_id);
  const cataloging = isStillCatalogingMessage(error);
  const transient = isTransientSyncError(error);
  const showResend = canResendQueueRow(item);
  const phoneOnlyRemove = archived || item.status === "uploaded" || linked;

  const remove = phoneOnlyRemove
    ? {
        removeLabel: "Remove" as const,
        removeTitle: "Remove from this phone?",
        removeBody: archived
          ? "This listing stays on the website. This only removes the phone archive row."
          : `${photoWord(item.photo_count || 0)} already sent. This only removes the copy on this phone — the website listing stays.`
      }
    : {
        removeLabel: "Delete" as const,
        removeTitle: "Delete local lot?",
        removeBody: `Delete ${photoWord(item.photo_count || 0)} from this phone? This cannot be undone.`
      };

  if (archived) {
    return {
      label: "On website",
      hint: "This listing stays on the website. This row is only on this phone — Add photos, or Remove when you are done.",
      tone: "ok",
      detail: null,
      showResend: false,
      ...remove
    };
  }

  if (linked && item.status !== "uploading" && item.status !== "queued" && item.status !== "draft") {
    return {
      label: "On website",
      hint: "Next Sync moves this to Phone archive and removes photos from this phone. Do not Resend.",
      tone: "ok",
      detail: null,
      showResend: false,
      ...remove
    };
  }

  switch (item.status) {
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
      return {
        label: "Ready to upload",
        hint: item.syncing
          ? "In line — Sync is sending lots now."
          : item.online === false
            ? "Will send when you're back online."
            : "Tap Sync now to send.",
        tone: "progress",
        detail: null,
        showResend: false,
        ...remove
      };
    case "uploading":
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
          detail: null,
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
          detail: null,
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
  return "Nothing queued to upload. Cataloged lots stay in Phone archive.";
}
