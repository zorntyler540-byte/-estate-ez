/** Leftover copy while the worker is still running — do not strip local photos yet. */
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

/**
 * Archive only after the website has the lot (cataloged-mobile-drafts or a completed job).
 * Never archive on ``/run`` 200 or "On server — cataloging…".
 */
export function shouldArchiveCatalogedDraft(opts: {
  alreadyArchived?: boolean;
  status: string;
  errorMessage?: string | null;
  confirmedOnServer: boolean;
}): boolean {
  if (opts.alreadyArchived) return false;
  if (opts.status !== "uploaded") return false;
  if (!opts.confirmedOnServer) return false;
  if (isStillCatalogingMessage(opts.errorMessage)) return false;
  return true;
}
