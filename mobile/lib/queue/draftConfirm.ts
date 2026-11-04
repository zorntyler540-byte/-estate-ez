/**
 * Whether the website already has a phone draft. Only exact server confirmation counts:
 * the draft id in ``cataloged-mobile-drafts`` (completed job with this ``client_draft_id``
 * or ``mobile-<draft>-…`` media on a lot), or a ``server_lot_id`` this phone's own job
 * produced. A matching SKU never counts — two sales can share SKU numbers, and a typo or
 * prefix can hit another listing, which used to delete photos that were never uploaded.
 */

export type CatalogedSaleIndex =
  | { ok: true; ids: Set<string>; lotByDraft: Map<string, string> }
  | { ok: false };

/** Built once per Sync and reused by every phase (archive, adopt, clip attach). */
export type WebsiteIndex = {
  bySale: Map<string, CatalogedSaleIndex>;
  /** Exact draft id → lot across every open sale, so moved lots still match. */
  allDraftLots: Map<string, string>;
  /** Drafts whose own job (``client_draft_id``) completed after the index was built. */
  jobConfirmed: Set<string>;
};

export type CatalogedDraftsResponse = {
  draftIds: string[];
  lots: { draft_id?: string | null; lot_id?: string | null }[];
};

export function normalizeDraftKey(id: string | null | undefined): string {
  return String(id || "").trim().toLowerCase();
}

export function saleIndexFromResponse(res: CatalogedDraftsResponse): CatalogedSaleIndex {
  const lotByDraft = new Map<string, string>();
  for (const row of res.lots) {
    const draftId = normalizeDraftKey(row.draft_id);
    const lotId = String(row.lot_id || "").trim();
    if (draftId && lotId) lotByDraft.set(draftId, lotId);
  }
  const ids = new Set(res.draftIds.map(normalizeDraftKey).filter(Boolean));
  for (const key of lotByDraft.keys()) ids.add(key);
  return { ok: true, ids, lotByDraft };
}

export function emptyWebsiteIndex(): WebsiteIndex {
  return { bySale: new Map(), allDraftLots: new Map(), jobConfirmed: new Set() };
}

/** Replace one sale's entry (e.g. after a lot finished cataloging mid-Sync). */
export function setSaleIndex(index: WebsiteIndex, saleId: string, sale: CatalogedSaleIndex): void {
  index.bySale.set(saleId, sale);
  if (!sale.ok) return;
  for (const [draftId, lotId] of sale.lotByDraft) {
    index.allDraftLots.set(draftId, lotId);
  }
}

/**
 * The server confirmed this exact draft mid-Sync (its job completed, or the status endpoint
 * mapped it to a lot). Patch the index instead of re-fetching every sale.
 */
export function noteDraftConfirmed(
  index: WebsiteIndex,
  draftId: string,
  lotId?: string | null
): void {
  const key = normalizeDraftKey(draftId);
  if (!key) return;
  index.jobConfirmed.add(key);
  const lot = String(lotId || "").trim();
  if (lot) index.allDraftLots.set(key, lot);
}

export type DraftServerState = {
  /** The website has this exact draft. Safe to archive / skip upload. */
  confirmed: boolean;
  /** Lot this draft became (exact mapping or the phone's own job). */
  lotId: string | null;
  /** Could not read this sale's catalog list — do nothing destructive. */
  lookupFailed: boolean;
};

export function resolveDraftOnServer(
  draft: { id: string; sale_id: string; server_lot_id?: string | null },
  index: WebsiteIndex
): DraftServerState {
  const key = normalizeDraftKey(draft.id);
  const sale = index.bySale.get(draft.sale_id);
  const lookupFailed = Boolean(sale && !sale.ok);
  const saleLot = sale?.ok ? sale.lotByDraft.get(key) : undefined;
  const lotId = saleLot || index.allDraftLots.get(key) || draft.server_lot_id || null;
  const confirmed =
    Boolean(lotId) || index.jobConfirmed.has(key) || Boolean(sale?.ok && sale.ids.has(key));
  return { confirmed, lotId, lookupFailed };
}
