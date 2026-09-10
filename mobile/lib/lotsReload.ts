export type LotReloadRow = {
  id: string;
  photo_count?: number | null;
};

/** Skip a Lots-tab refetch when focus bounces (camera modal open/close on some phones). */
export function shouldSkipLotsFocusReload(
  lastLoadAt: number,
  now: number,
  hasLots: boolean,
  minIntervalMs = 2500
): boolean {
  return hasLots && lastLoadAt > 0 && now - lastLoadAt < minIntervalMs;
}

/** Identity of the list so we can keep existing cover URLs when nothing catalog-changed. */
export function lotListSignature(lots: LotReloadRow[]): string {
  return lots.map((lot) => `${lot.id}:${lot.photo_count ?? 0}`).join(",");
}

export function coverUrlsEqual(
  prev: Record<string, string>,
  next: Record<string, string>
): boolean {
  const prevKeys = Object.keys(prev);
  const nextKeys = Object.keys(next);
  if (prevKeys.length !== nextKeys.length) return false;
  for (const key of prevKeys) {
    if (prev[key] !== next[key]) return false;
  }
  return true;
}

/** Refresh signed covers only when the lot set/counts changed or we have no thumbs yet. */
export function shouldRefreshCoverUrls(
  prevSignature: string,
  nextSignature: string,
  existingCoverCount: number
): boolean {
  if (existingCoverCount === 0 && nextSignature.length > 0) return true;
  return prevSignature !== nextSignature;
}

/** Avoid a SalesProvider focus loop when stored name and API name only differ by whitespace. */
export function saleNameNeedsUpdate(stored: string | null | undefined, apiName: string): boolean {
  return (stored ?? "").trim() !== apiName.trim();
}

export type LotListDisplayRow = LotReloadRow & {
  sku?: string | null;
  auction_title?: string | null;
  ct_category?: string | null;
  has_thumbnail?: boolean;
  ctseller_item_id?: number | null;
  ctseller_posted_at?: string | null;
  sku_needs_review?: boolean;
  sku_duplicate_in_sale?: boolean;
  sku_text_mismatch?: boolean;
  video_count?: number | null;
};

/** Fields the Lots/Facebook rows actually paint — skip setState when these match. */
export function lotRowDisplayKey(lot: LotListDisplayRow): string {
  return [
    lot.id,
    lot.sku ?? "",
    lot.auction_title ?? "",
    lot.photo_count ?? 0,
    lot.ct_category ?? "",
    lot.has_thumbnail ? "1" : "0",
    lot.ctseller_item_id ?? "",
    lot.ctseller_posted_at ?? "",
    lot.sku_needs_review ? "1" : "0",
    lot.sku_duplicate_in_sale ? "1" : "0",
    lot.sku_text_mismatch ? "1" : "0",
    lot.video_count ?? 0
  ].join("|");
}

export function lotRowsUnchanged(prev: LotListDisplayRow[], next: LotListDisplayRow[]): boolean {
  if (prev.length !== next.length) return false;
  for (let i = 0; i < prev.length; i++) {
    if (lotRowDisplayKey(prev[i]!) !== lotRowDisplayKey(next[i]!)) return false;
  }
  return true;
}

export function lotIdsWithChangedPhotoCount(prevSignature: string, nextSignature: string): Set<string> {
  const prev = new Map<string, string>();
  for (const part of prevSignature.split(",")) {
    if (!part) continue;
    const split = part.lastIndexOf(":");
    if (split <= 0) continue;
    prev.set(part.slice(0, split), part.slice(split + 1));
  }
  const changed = new Set<string>();
  for (const part of nextSignature.split(",")) {
    if (!part) continue;
    const split = part.lastIndexOf(":");
    if (split <= 0) continue;
    const id = part.slice(0, split);
    const count = part.slice(split + 1);
    if (prev.get(id) !== count) changed.add(id);
  }
  return changed;
}

/**
 * Keep already-painted signed URLs. Fresh signatures for unchanged lots are what
 * makes the whole Lots grid flash after Add photos.
 */
export function mergeCoverUrls(
  prev: Record<string, string>,
  next: Record<string, string>,
  replaceIds: Set<string>
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, url] of Object.entries(next)) {
    const keep = prev[id] && !replaceIds.has(id);
    out[id] = keep ? prev[id]! : url;
  }
  return coverUrlsEqual(prev, out) ? prev : out;
}

export type LotDetailSnapshotRow = LotListDisplayRow & {
  professional_description?: string | null;
  condition_text?: string | null;
  starting_price?: number | null;
  video_count?: number | null;
  brands?: string[] | null;
  specification_lines?: string[] | null;
};

/** Skip replacing the lot-details model when the painted fields did not change. */
export function lotDetailSnapshot(lot: LotDetailSnapshotRow): string {
  return [
    lotRowDisplayKey(lot),
    lot.professional_description ?? "",
    lot.condition_text ?? "",
    lot.starting_price ?? "",
    lot.video_count ?? 0,
    (lot.brands || []).join(","),
    (lot.specification_lines || []).join("\n")
  ].join("||");
}

export function mediaIdsKey(rows: { id: string }[]): string {
  return rows.map((row) => row.id).join(",");
}

export function selectedSaleUnchanged(
  currentId: string | null | undefined,
  currentName: string | null | undefined,
  nextId: string | null,
  nextName: string | null
): boolean {
  return (currentId ?? null) === nextId && (currentName ?? "").trim() === (nextName ?? "").trim();
}

export function saleRowsUnchanged(
  prev: { id: string; name: string }[],
  next: { id: string; name: string }[]
): boolean {
  if (prev.length !== next.length) return false;
  for (let i = 0; i < prev.length; i++) {
    if (prev[i]!.id !== next[i]!.id || prev[i]!.name !== next[i]!.name) return false;
  }
  return true;
}

export function queueRowFingerprint(row: {
  id: string;
  status: string;
  error_message?: string | null;
  photo_count?: number | null;
  photoUris?: string[];
  sale_id?: string;
  manual_sku?: string | null;
}): string {
  return [
    row.id,
    row.status,
    row.error_message ?? "",
    row.photo_count ?? 0,
    row.sale_id ?? "",
    row.manual_sku ?? "",
    (row.photoUris || []).join(",")
  ].join("|");
}

export function queueRowsUnchanged(
  prev: Parameters<typeof queueRowFingerprint>[0][],
  next: Parameters<typeof queueRowFingerprint>[0][]
): boolean {
  if (prev.length !== next.length) return false;
  for (let i = 0; i < prev.length; i++) {
    if (queueRowFingerprint(prev[i]!) !== queueRowFingerprint(next[i]!)) return false;
  }
  return true;
}
