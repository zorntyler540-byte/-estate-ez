/** Leftover copy while the worker is still running — do not strip local photos yet
 * unless the website has already confirmed the lot. */
export function isStillCatalogingMessage(message: string | null | undefined): boolean {
  const m = (message || "").trim().toLowerCase();
  if (!m) return false;
  return (
    m.includes("cataloging") ||
    m.includes("check the website") ||
    m.includes("lots appear when") ||
    m.includes("starting catalog")
  );
}

const ARCHIVEABLE_STATUS = new Set(["uploaded", "queued", "uploading", "failed"]);

/**
 * Archive after the website lists this draft (cataloged-mobile-drafts).
 * Leftover "cataloging…" / queued / failed / uploading rows still archive once
 * confirmed — Sync must not re-upload those. Draft stays in Queue (still shooting).
 */
export function shouldArchiveCatalogedDraft(opts: {
  alreadyArchived?: boolean;
  status: string;
  errorMessage?: string | null;
  confirmedOnServer: boolean;
  hasServerLotId?: boolean;
}): boolean {
  if (opts.alreadyArchived) return false;
  if (opts.status === "draft") return false;
  if (!ARCHIVEABLE_STATUS.has(opts.status)) return false;
  if (opts.confirmedOnServer || opts.hasServerLotId) return true;
  return false;
}

/** Do not start a new catalog job when the website already has this draft. */
export function shouldSkipReupload(opts: {
  confirmedOnServer: boolean;
  catalogLookupFailed?: boolean;
  hasServerLotId?: boolean;
}): boolean {
  if (opts.hasServerLotId) return true;
  if (opts.catalogLookupFailed) return true;
  return opts.confirmedOnServer;
}
