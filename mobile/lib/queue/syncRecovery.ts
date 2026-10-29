import type { Job } from "../types";

export type DraftSyncStatus = "draft" | "queued" | "uploading" | "uploaded" | "failed";

export type SyncRecoveryAction = "adopt" | "retry";

/**
 * Decide whether Sync should reuse an existing server job or start a new upload.
 *
 * ``adopt`` — job is already queued/processing/completed. Do not delete it and do not
 * create a second catalog job (that was creating duplicate listings + a second Gemini bill).
 * ``retry`` — upload never finished, job failed, or the server row is gone.
 */
/** Prefer a job that stored this phone draft UUID; fall back to the local server_job_id. */
export function findJobForDraft<T extends Pick<Job, "id" | "client_draft_id">>(
  jobs: T[],
  draftId: string,
  serverJobId?: string | null
): T | null {
  const key = (draftId || "").trim().toLowerCase();
  if (key) {
    const byDraft = jobs.find((j) => (j.client_draft_id || "").trim().toLowerCase() === key);
    if (byDraft) return byDraft;
  }
  if (serverJobId) {
    const byId = jobs.find((j) => j.id === serverJobId);
    if (byId) return byId;
  }
  return null;
}

export function decideSyncRecovery(
  draftStatus: DraftSyncStatus,
  job: Pick<Job, "status"> | null
): SyncRecoveryAction {
  if (draftStatus !== "uploading" && draftStatus !== "failed" && draftStatus !== "queued") {
    return "adopt";
  }
  if (!job) return "retry";
  if (job.status === "queued" || job.status === "processing" || job.status === "completed") {
    return "adopt";
  }
  return "retry";
}

export function jobIsLiveOrDone(status: string | undefined): boolean {
  return status === "queued" || status === "processing" || status === "completed";
}

/** Website was busy (DB pool / timeout) — lot may already be on the server. */
export function isTransientSyncError(message: string | null | undefined): boolean {
  const m = (message || "").toLowerCase();
  if (!m) return false;
  return (
    m.includes("timed out") ||
    m.includes("timeout") ||
    m.includes("queuepool") ||
    m.includes("connection timed out") ||
    m.includes("network request failed") ||
    m.includes("502") ||
    m.includes("503") ||
    m.includes("504") ||
    m.includes("upstream") ||
    m.includes("didn't finish") ||
    m.includes("did not finish") ||
    m.includes("website was busy") ||
    m.includes("website busy") ||
    m.includes("check the website") ||
    m.includes("do not resend")
  );
}
