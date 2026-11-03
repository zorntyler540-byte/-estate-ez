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
const JOB_STATUS_RANK: Record<string, number> = {
  completed: 0,
  processing: 1,
  queued: 2,
  receiving: 3,
  failed: 4
};

function jobStatusRank(status: string | undefined): number {
  return JOB_STATUS_RANK[String(status || "")] ?? 9;
}

function pickPreferredJob<T extends { status?: string }>(matches: T[]): T | null {
  if (!matches.length) return null;
  return matches.slice().sort((a, b) => jobStatusRank(a.status) - jobStatusRank(b.status))[0] ?? null;
}

/** Prefer a job that stored this phone draft UUID; fall back to the local server_job_id.
 * When several rows share a draft (leftover receiving + the real catalog job), pick
 * completed first so Queue does not stay on "Cataloging…" after the lot is listed. */
export function findJobForDraft<T extends Pick<Job, "id" | "client_draft_id"> & { status?: string }>(
  jobs: T[],
  draftId: string,
  serverJobId?: string | null
): T | null {
  const key = (draftId || "").trim().toLowerCase();
  if (key) {
    const byDraft = jobs.filter((j) => (j.client_draft_id || "").trim().toLowerCase() === key);
    const preferred = pickPreferredJob(byDraft);
    if (preferred) return preferred;
  }
  if (serverJobId) {
    const byId = jobs.filter((j) => j.id === serverJobId);
    const preferred = pickPreferredJob(byId);
    if (preferred) return preferred;
  }
  return null;
}

export function isDraftConfirmedOnServer(opts: {
  lotId?: string | null;
  catalogedIds?: Set<string> | null;
  draftKey?: string | null;
}): boolean {
  if (opts.lotId) return true;
  const key = (opts.draftKey || "").trim().toLowerCase();
  return Boolean(key && opts.catalogedIds?.has(key));
}

export function decideCatalogConfirm(opts: {
  jobStatus?: string | null;
  confirmedOnServer?: boolean;
  lotId?: string | null;
}): "completed" | "failed" | "pending" {
  if (opts.jobStatus === "failed") return "failed";
  if (opts.jobStatus === "completed" || opts.confirmedOnServer || opts.lotId) return "completed";
  return "pending";
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
