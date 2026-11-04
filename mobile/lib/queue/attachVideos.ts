/** Pure helpers for attaching lot clips after catalog. Not sent through Gemini. */

export function draftNeedsVideoAttach(opts: {
  hasPendingLocalVideos: boolean;
  lotId?: string | null;
}): boolean {
  return Boolean(opts.hasPendingLocalVideos && String(opts.lotId || "").trim());
}

/**
 * Sync used to stop once photos looked "uploaded", so clips never got a second
 * chance after catalog finished (the usual case — Gemini takes longer than 45s).
 */
export function draftKeepsSyncAlive(opts: {
  status: string;
  errorMessage?: string | null;
  serverJobId?: string | null;
  hasPendingLocalVideos?: boolean;
}): boolean {
  if (opts.status === "queued" || opts.status === "failed" || opts.status === "uploading") {
    return true;
  }
  if (opts.status === "uploaded" && (opts.errorMessage || opts.serverJobId)) {
    return true;
  }
  return Boolean(opts.hasPendingLocalVideos);
}

export function countPhotoJobsPending(opts: { status: string }): boolean {
  return opts.status === "queued" || opts.status === "failed" || opts.status === "uploading";
}
