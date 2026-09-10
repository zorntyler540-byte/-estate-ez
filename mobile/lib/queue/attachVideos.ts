/** Pure helpers for attaching lot clips after catalog. Not sent through Gemini. */

export function videoUploadName(draftId: string, fileUri: string, index: number): string {
  const match = String(fileUri || "")
    .toLowerCase()
    .match(/\.(mp4|mov|m4v|webm)(?:\?|$)/);
  const ext = match?.[1] ? `.${match[1]}` : ".mp4";
  return `mobile-${draftId}-vid-${String(index + 1).padStart(2, "0")}${ext}`;
}

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
