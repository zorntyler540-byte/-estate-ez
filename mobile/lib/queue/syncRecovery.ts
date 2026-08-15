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
