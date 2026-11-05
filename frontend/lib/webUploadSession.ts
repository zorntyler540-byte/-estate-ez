/**
 * One website bulk upload = one ``WebUploadSession``: a random batch id, the next pick-order
 * sequence number, and (once the first chunk lands) the server's ``receiving`` job id.
 *
 * Every staged ``File`` keeps its ``web-<batch>-NNNN`` name for the life of the session (also
 * across refresh via IndexedDB), so a retried / resumed Upload sends the same names. The server
 * then reuses the receiving job (``client_draft_id = web-<batch>``) and skips names it already
 * stored instead of opening a second job for the same photos.
 */

import { newWebUploadBatchId, webUploadName } from "./webUploadNames.ts";

export type WebUploadSession = {
  batchId: string;
  /** Last pick-order number handed out (names are 1-based). */
  nextSeq: number;
  /** ``receiving`` job that already holds some of this batch, once known. */
  jobId: string | null;
};

const uploadNames = new WeakMap<File, string>();

const WEB_BATCH = /(?:^|\/)web-([0-9a-f]{12,32})-\d{4,5}(?:-[^/]*)?\.[a-z0-9]{2,5}$/i;

export function newWebUploadSession(): WebUploadSession {
  return { batchId: newWebUploadBatchId(), nextSeq: 0, jobId: null };
}

/** Sent as ``client_draft_id`` so ``/jobs/upload`` reuses this batch's receiving job (server keeps 36 chars). */
export function webClientDraftId(session: WebUploadSession): string {
  return `web-${session.batchId}`;
}

export function uploadNameOf(file: File): string | undefined {
  return uploadNames.get(file);
}

export function setUploadName(file: File, name: string): void {
  if (name) uploadNames.set(file, name);
}

/** A converted / edited copy of a staged photo keeps the original's slot in pick order. */
export function inheritUploadName(from: File, to: File): void {
  const name = uploadNames.get(from);
  if (name) uploadNames.set(to, name);
}

export function webUploadBatchOf(name: string): string | null {
  const m = WEB_BATCH.exec(name.replace(/\\/g, "/"));
  return m ? m[1]!.toLowerCase() : null;
}

/**
 * Stable upload name for every file, in array (pick) order. Files that already carry a name
 * from this session keep it; new files get the next sequence numbers.
 */
export function assignUploadNames(
  session: WebUploadSession,
  files: readonly File[],
  relPathOf: (file: File) => string
): string[] {
  return files.map((file) => {
    const have = uploadNames.get(file);
    if (have && webUploadBatchOf(have) === session.batchId) {
      const seq = seqOf(have);
      if (seq > session.nextSeq) session.nextSeq = seq;
      return have;
    }
    session.nextSeq += 1;
    const name = webUploadName(session.batchId, session.nextSeq, relPathOf(file));
    uploadNames.set(file, name);
    return name;
  });
}

function seqOf(name: string): number {
  const m = /-(\d{4,5})(?:-[^/]*)?\.[a-z0-9]{2,5}$/i.exec(name);
  return m ? parseInt(m[1]!, 10) : 0;
}

/** The server may store ``.png``/``.heic`` as ``.jpg``; compare names without the extension. */
function nameKey(name: string): string {
  const posix = name.replace(/\\/g, "/").replace(/^\/+/, "").toLowerCase();
  const dot = posix.lastIndexOf(".");
  return dot > posix.lastIndexOf("/") ? posix.slice(0, dot) : posix;
}

/** Indexes of ``names`` the server does not have yet (``received`` = ``/jobs/{id}/received-files``). */
export function missingUploadIndexes(names: readonly string[], received: Iterable<string>): number[] {
  const have = new Set<string>();
  for (const r of received) {
    const key = nameKey(r);
    have.add(key);
    have.add(key.split("/").pop() || key);
  }
  const out: number[] = [];
  names.forEach((name, i) => {
    const key = nameKey(name);
    if (!have.has(key) && !have.has(key.split("/").pop() || key)) out.push(i);
  });
  return out;
}

export type ResumeDecision =
  /** No usable job — upload everything through ``/jobs/upload``. */
  | { kind: "fresh"; discardJobId: string | null }
  /** Job is still receiving — send only the missing indexes, then ``/run``. */
  | { kind: "resume"; jobId: string; missing: number[] }
  /** Batch already handed to the catalog worker — never upload it again. */
  | { kind: "already_submitted"; jobId: string; status: string };

/** ``/jobs/{id}/received-files`` plus the job's ``error_message`` (failed rows). */
export type ServerBatchState = {
  status: string;
  names: string[];
  folder_missing?: boolean;
  error_message?: string | null;
};

/** Mid-upload / lost-disk failures (``_ORPHAN_UPLOAD_ERROR_MARKERS`` in ``job_tasks.py``). */
export function isOrphanUploadError(message: string | null | undefined): boolean {
  const m = (message || "").toLowerCase();
  return [
    "input folder not found",
    "upload disk was lost",
    "re-sync from the phone",
    "upload never finished",
    "sync stopped mid-lot"
  ].some((marker) => m.includes(marker));
}

/**
 * What to do with a session that already has a job id (``state`` ``null`` = job deleted / 404).
 * Pure so the resume rules are unit tested.
 */
export function decideResume(
  session: WebUploadSession,
  names: readonly string[],
  state: ServerBatchState | null
): ResumeDecision {
  if (!session.jobId || !state) return { kind: "fresh", discardJobId: null };
  const status = (state.status || "").toLowerCase();
  if (status === "receiving") {
    if (state.folder_missing) return { kind: "fresh", discardJobId: session.jobId };
    // A photo removed or edited after a partial upload is still on the server; restart so the
    // lot is not cataloged with it.
    if (missingUploadIndexes(state.names, names).length > 0) {
      return { kind: "fresh", discardJobId: session.jobId };
    }
    return { kind: "resume", jobId: session.jobId, missing: missingUploadIndexes(names, state.names) };
  }
  // Photos never all arrived (or the disk was lost): that job can never catalog this batch.
  if (status === "failed" && isOrphanUploadError(state.error_message)) {
    return { kind: "fresh", discardJobId: session.jobId };
  }
  return { kind: "already_submitted", jobId: session.jobId, status };
}
