import type { Lot } from "../types";

export type CatalogLotRef = {
  draftId?: string;
  lotId: string;
  sku?: string | null;
  photoCount: number;
  title?: string | null;
};

export function normalizeDraftId(id: string | null | undefined): string {
  return String(id || "").trim().toLowerCase();
}

export function normalizeSku(sku: string | null | undefined): string {
  return String(sku || "").trim().toLowerCase();
}

/**
 * Find the website lot for a Queue draft so Add photos can append (not Resend).
 * Prefer a cached lot id, then the sale's mobile-draft map, then a unique SKU match.
 */
export function pickCatalogLotForDraft(opts: {
  draftId: string;
  manualSku?: string | null;
  cachedLotId?: string | null;
  mapped: CatalogLotRef[];
  lotsBySku?: Lot[];
}): CatalogLotRef | null {
  const cached = String(opts.cachedLotId || "").trim();
  if (cached) {
    const hit = opts.mapped.find((row) => row.lotId === cached);
    if (hit) return hit;
    return {
      lotId: cached,
      sku: opts.manualSku ?? null,
      photoCount: 0
    };
  }

  const draftId = normalizeDraftId(opts.draftId);
  const fromMap = opts.mapped.find((row) => normalizeDraftId(row.draftId) === draftId);
  if (fromMap) return fromMap;

  const sku = normalizeSku(opts.manualSku);
  if (!sku || !opts.lotsBySku?.length) return null;
  const matches = opts.lotsBySku.filter((lot) => normalizeSku(lot.sku) === sku);
  if (matches.length !== 1) return null;
  const lot = matches[0]!;
  return {
    lotId: lot.id,
    sku: lot.sku,
    photoCount: lot.photo_count,
    title: lot.auction_title
  };
}

export function queueAddPhotosIsLocal(status: string): boolean {
  return status === "draft" || status === "queued" || status === "failed";
}
