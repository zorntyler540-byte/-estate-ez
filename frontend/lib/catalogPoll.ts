/** Dashboard / client-page catalog job polling.

When any sale has receiving/queued/processing jobs, poll about every 6s.
When the queue is idle, back off to 45s. Hidden tabs do not poll; the caller
should refetch immediately on visibility/focus and after the user starts a job.
*/

export const CATALOG_POLL_ACTIVE_MS = 6000;
export const CATALOG_POLL_IDLE_MS = 45000;

export type CatalogPollClock = {
  hidden: boolean;
  hasActive: boolean;
};

/** Interval in ms, or ``null`` when polling should be paused. */
export function catalogPollIntervalMs(clock: CatalogPollClock): number | null {
  if (clock.hidden) return null;
  return clock.hasActive ? CATALOG_POLL_ACTIVE_MS : CATALOG_POLL_IDLE_MS;
}

export type CatalogStatusSale = {
  sale_id: string;
  receiving: number;
  queued: number;
  processing: number;
  stuck_count: number;
};

export function catalogStatusHasActive(sales: CatalogStatusSale[]): boolean {
  return sales.some((s) => s.receiving + s.queued + s.processing > 0);
}

export function catalogStatusPath(saleId?: string | null): string {
  const sid = (saleId || "").trim();
  if (!sid) return "/jobs/catalog-status";
  return `/jobs/catalog-status?sale_id=${encodeURIComponent(sid)}`;
}
