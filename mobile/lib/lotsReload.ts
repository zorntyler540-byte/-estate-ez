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
