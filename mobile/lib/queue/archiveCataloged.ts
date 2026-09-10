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

/** Phone SKU keys, including a trailing ADD used when a second phone reshoots. */
export function skuLookupKeys(sku: string | null | undefined): string[] {
  const raw = normalizeArchiveSku(sku);
  if (!raw) return [];
  const keys = [raw];
  const noAdd = raw.replace(/add$/i, "").trim();
  if (noAdd && noAdd !== raw) keys.push(noAdd);
  const compact = raw.replace(/[^a-z0-9]/g, "");
  if (compact && !keys.includes(compact)) keys.push(compact);
  return keys;
}

/**
 * Map a phone SKU to one website lot. Exact, ADD-stripped, or a single prefix
 * hit (JL91500ADD ↔ JL91500). Null if two lots could match.
 */
export function findUniqueSkuLot(
  phoneSku: string | null | undefined,
  skuToLot: Map<string, string>
): string | undefined {
  for (const key of skuLookupKeys(phoneSku)) {
    const hit = skuToLot.get(key);
    if (hit) return hit;
  }
  const phoneKeys = skuLookupKeys(phoneSku).filter((k) => k.length >= 4);
  if (!phoneKeys.length) return undefined;
  const prefixHits = new Set<string>();
  for (const [webSku, lotId] of skuToLot) {
    if (webSku.length < 4) continue;
    if (phoneKeys.some((pk) => webSku.startsWith(pk) || pk.startsWith(webSku))) {
      prefixHits.add(lotId);
    }
  }
  if (prefixHits.size === 1) return [...prefixHits][0];
  return undefined;
}

export function mergeUniqueSkuMaps(maps: Map<string, string>[]): Map<string, string> {
  const counts = new Map<string, string[]>();
  for (const map of maps) {
    for (const [sku, lotId] of map) {
      const ids = counts.get(sku) || [];
      if (!ids.includes(lotId)) ids.push(lotId);
      counts.set(sku, ids);
    }
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
