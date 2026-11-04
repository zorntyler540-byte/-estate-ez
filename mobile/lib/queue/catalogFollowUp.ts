/**
 * While a lot is still cataloging (or clips are waiting for a lot id), check again soon.
 * Auto-sync's 45s cooldown is what left Queue on "Cataloging…" after the website finished.
 * Checks back off (8s → 5 min) and stop after 30 min so a stuck job cannot poll forever.
 */
export const CATALOG_FOLLOWUP_MS = 8_000;
export const CATALOG_FOLLOWUP_MAX_MS = 5 * 60_000;
export const CATALOG_FOLLOWUP_GIVE_UP_MS = 30 * 60_000;

export function shouldFollowUpCatalogCheck(opts: {
  leftoverCatalogingNotice?: boolean;
  hasPendingLocalVideos?: boolean;
}): boolean {
  return Boolean(opts.leftoverCatalogingNotice || opts.hasPendingLocalVideos);
}

export function followUpDelayMs(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  return Math.min(CATALOG_FOLLOWUP_MS * 2 ** Math.min(n, 20), CATALOG_FOLLOWUP_MAX_MS);
}

export type FollowUpPlan = {
  /** Sorted draft ids being watched; a different set starts a fresh window. */
  key: string;
  startedAt: number;
  attempt: number;
  stopped: boolean;
};

export function followUpKey(draftIds: string[]): string {
  return [...new Set(draftIds.map((id) => id.trim().toLowerCase()).filter(Boolean))]
    .sort()
    .join(",");
}

/** Next timer for the current follow-up set, or ``delayMs: null`` when nothing should run. */
export function planFollowUp(
  prev: FollowUpPlan | null,
  draftIds: string[],
  now: number
): { plan: FollowUpPlan | null; delayMs: number | null } {
  const key = followUpKey(draftIds);
  if (!key) return { plan: null, delayMs: null };
  if (!prev || prev.key !== key) {
    return { plan: { key, startedAt: now, attempt: 0, stopped: false }, delayMs: CATALOG_FOLLOWUP_MS };
  }
  if (prev.stopped) return { plan: prev, delayMs: null };
  const remaining = CATALOG_FOLLOWUP_GIVE_UP_MS - (now - prev.startedAt);
  if (remaining <= 0) return { plan: { ...prev, stopped: true }, delayMs: null };
  return { plan: prev, delayMs: Math.min(followUpDelayMs(prev.attempt), remaining) };
}

export function advanceFollowUp(plan: FollowUpPlan): FollowUpPlan {
  return { ...plan, attempt: plan.attempt + 1 };
}

export function stopFollowUp(plan: FollowUpPlan): FollowUpPlan {
  return { ...plan, stopped: true };
}

export type FollowUpDraft = {
  id: string;
  server_lot_id?: string | null;
  has_pending_clip?: boolean;
};

export type FollowUpServerRow = {
  job_status?: string | null;
  lot_id?: string | null;
  cataloged?: boolean;
};

/**
 * Whether the batched status answer has news a full Sync must act on (archive, attach
 * clips, show a failure, or clean up a job that is gone). Still-cataloging rows just wait.
 */
export function followUpNeedsSync(
  drafts: FollowUpDraft[],
  rows: Map<string, FollowUpServerRow>
): boolean {
  for (const draft of drafts) {
    const row = rows.get(draft.id.trim().toLowerCase());
    if (!row) return true;
    if (row.cataloged || row.lot_id) return true;
    if (row.job_status === "failed") return true;
    if (!row.job_status) return true;
    if (draft.has_pending_clip && draft.server_lot_id) return true;
  }
  return false;
}
