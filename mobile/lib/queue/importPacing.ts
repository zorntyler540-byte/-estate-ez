/** ``GET /jobs/quota`` (newer APIs; older ones 404 and Sync is not paced). */
export type CatalogQuota = {
  queued_jobs: number;
  max_queued_jobs: number;
  photos_today: number;
  max_photos_per_day: number;
  photos_remaining_today: number;
  max_photos_per_job?: number;
  max_photos_per_lot?: number;
  resets_at?: string | null;
};

/** Queued-job slots left for the website and other phones, so one big import cannot fill them. */
export const QUEUE_HEADROOM = 3;

/** Prefix of the Queue note on a lot held back by pacing (Queue shows "Waiting for website"). */
export const PACED_PREFIX = "Waiting —";

export function isPacedMessage(message: string | null | undefined): boolean {
  return (message || "").trim().startsWith(PACED_PREFIX);
}

export type PaceDecision = { ok: true } | { ok: false; reason: string };

function resetsLabel(resetsAt: string | null | undefined): string {
  const t = resetsAt ? Date.parse(resetsAt) : NaN;
  if (!Number.isFinite(t)) return "tomorrow";
  const d = new Date(t);
  const hh = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, "0");
  const h12 = hh % 12 || 12;
  return `at ${h12}:${mm} ${hh < 12 ? "AM" : "PM"}`;
}

/**
 * Paces new catalog jobs within one Sync so a big import does not trip the website's
 * queued-job or daily-photo limits (which used to fail every later lot with a 429).
 * Lots held back stay ``queued`` and go on a later Sync, once earlier lots catalog.
 * Resuming a lot whose job already exists is never paced — it is already counted.
 */
export class RunPacer {
  private jobsLeft: number;
  private photosLeft: number;
  private readonly resetsAt: string | null;
  readonly active: boolean;

  constructor(quota: CatalogQuota | null, headroom = QUEUE_HEADROOM) {
    this.active = Boolean(quota);
    this.jobsLeft = quota ? Math.max(0, quota.max_queued_jobs - quota.queued_jobs - headroom) : Infinity;
    this.photosLeft = quota ? Math.max(0, quota.photos_remaining_today) : Infinity;
    this.resetsAt = quota?.resets_at ?? null;
  }

  /** Reserve a slot for a lot about to create its job. */
  admit(photoCount: number): PaceDecision {
    if (photoCount > this.photosLeft) {
      return {
        ok: false,
        reason: `${PACED_PREFIX} the website's daily photo limit is reached. This lot uploads on its own after it resets ${resetsLabel(this.resetsAt)}.`
      };
    }
    if (this.jobsLeft <= 0) {
      return {
        ok: false,
        reason: `${PACED_PREFIX} the website is still cataloging earlier lots. This lot uploads on its own as they finish.`
      };
    }
    this.jobsLeft -= 1;
    this.photosLeft -= photoCount;
    return { ok: true };
  }

  /** The job was never created (upload failed before the first photo landed). */
  release(photoCount: number): void {
    if (!this.active) return;
    this.jobsLeft += 1;
    this.photosLeft += photoCount;
  }
}
