import type { Lot } from "../types";

export type CatalogLotRef = {
  draftId?: string;
  lotId: string;
  sku?: string | null;
  photoCount: number;
  title?: string | null;
};

export type PickedCatalogLot = CatalogLotRef & {
  /**
   * ``exact`` = this draft's own lot (verified id or the server's draft map).
   * ``sku`` = only a suggestion: ask staff to confirm and never save it on the draft.
   */
  match: "exact" | "sku";
};

export function normalizeDraftId(id: string | null | undefined): string {
  return String(id || "").trim().toLowerCase();
}

export function normalizeSku(sku: string | null | undefined): string {
  return String(sku || "").trim().toLowerCase();
}

/**
 * Find the website lot for a Queue draft so Add photos can append (not Resend).
 * Prefer the verified lot id, then the sale's mobile-draft map, then a unique SKU
 * *suggestion* (two sales can reuse SKU numbers, so that one needs a human confirm).
 */
export function pickCatalogLotForDraft(opts: {
  draftId: string;
  manualSku?: string | null;
  cachedLotId?: string | null;
  mapped: CatalogLotRef[];
  lotsBySku?: Lot[];
}): PickedCatalogLot | null {
  const cached = String(opts.cachedLotId || "").trim();
  if (cached) {
    const hit = opts.mapped.find((row) => row.lotId === cached);
    if (hit) return { ...hit, match: "exact" };
    return {
      lotId: cached,
      sku: opts.manualSku ?? null,
      photoCount: 0,
      match: "exact"
    };
  }

  const draftId = normalizeDraftId(opts.draftId);
  const fromMap = opts.mapped.find((row) => normalizeDraftId(row.draftId) === draftId);
  if (fromMap) return { ...fromMap, match: "exact" };

  const sku = normalizeSku(opts.manualSku);
  if (!sku || !opts.lotsBySku?.length) return null;
  const matches = opts.lotsBySku.filter((lot) => normalizeSku(lot.sku) === sku);
  if (matches.length !== 1) return null;
  const lot = matches[0]!;
  return {
    lotId: lot.id,
    sku: lot.sku,
    photoCount: lot.photo_count,
    title: lot.auction_title,
    match: "sku"
  };
}

export function queueAddPhotosIsLocal(status: string): boolean {
  return status === "draft" || status === "queued" || status === "failed";
}

/**
 * Add photos on a lot that is still on the phone resumes *that* lot. A queued or failed lot goes
 * back to ``draft`` so the camera can add to it (it used to open a different, new lot). Not while
 * Sync runs — it may be sending that lot right now.
 */
export function reopenForCapture(
  status: string,
  opts: { syncing?: boolean } = {}
): "keep" | "reopen" | "blocked" {
  if (status === "draft") return "keep";
  if (status === "queued" || status === "failed") return opts.syncing ? "blocked" : "reopen";
  return "blocked";
}
