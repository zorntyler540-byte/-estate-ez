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
export function normalizeArchiveSku(sku: string | null | undefined): string {
  return String(sku || "").trim().toLowerCase();
}

/** Unique SKUs only — never guess when two lots share a tag. */
export function uniqueSkuLotMap(
  lots: { id: string; sku?: string | null }[]
): Map<string, string> {
  const counts = new Map<string, string[]>();
  for (const lot of lots) {
    const sku = normalizeArchiveSku(lot.sku);
    if (!sku) continue;
    const ids = counts.get(sku) || [];
    ids.push(lot.id);
    counts.set(sku, ids);
  }
  const unique = new Map<string, string>();
  for (const [sku, ids] of counts) {
    if (ids.length === 1) unique.set(sku, ids[0]!);
  }
  return unique;
}

export function shouldArchiveCatalogedDraft(opts: {
  alreadyArchived?: boolean;
  status: string;
  errorMessage?: string | null;
  confirmedOnServer: boolean;
  hasServerLotId?: boolean;
  uniqueSkuMatch?: boolean;
}): boolean {
  if (opts.alreadyArchived) return false;
  // A draft whose exact id is already on website photos was uploaded from this row.
  if (opts.status === "draft") return Boolean(opts.confirmedOnServer);
  if (!ARCHIVEABLE_STATUS.has(opts.status)) return false;
  if (opts.confirmedOnServer || opts.hasServerLotId || opts.uniqueSkuMatch) return true;
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
