/**
 * One Sync at a time. A request that arrives mid-Sync (a lot was just queued) is remembered and
 * run once the current Sync ends — it used to be dropped, so that lot waited for the next
 * foreground / network change. Several requests collapse into one rerun; ``manual`` and
 * ``allowCellular`` stick. The app and the background task share ``sharedSyncGate`` so the OS
 * waking the app for a background Sync never runs beside the foreground one.
 */
export type SyncRequest = {
  manual?: boolean;
  /** Staff chose "Upload now on cellular" while Upload on Wi‑Fi only is on. */
  allowCellular?: boolean;
};

export class SyncGate {
  private running = false;
  private pending: SyncRequest | null = null;
  private rerun: ((req: SyncRequest) => void) | null = null;

  get busy(): boolean {
    return this.running;
  }

  /** True = start now. False = a Sync is running; this request runs after it. */
  begin(req: SyncRequest = {}): boolean {
    if (this.running) {
      this.pending = {
        manual: Boolean(this.pending?.manual || req.manual),
        allowCellular: Boolean(this.pending?.allowCellular || req.allowCellular)
      };
      return false;
    }
    this.running = true;
    return true;
  }

  /** Finish the current Sync; returns the request to run next, if any. */
  end(): SyncRequest | null {
    this.running = false;
    const next = this.pending;
    this.pending = null;
    return next;
  }

  /** Who runs a queued rerun (the app's Sync). Without one, ``finish`` keeps it pending. */
  setRerunHandler(fn: ((req: SyncRequest) => void) | null): void {
    this.rerun = fn;
  }

  /** ``end()`` and hand any queued request to the rerun handler. */
  finish(): void {
    const next = this.end();
    if (!next) return;
    if (this.rerun) this.rerun(next);
    else this.pending = next;
  }
}

export const sharedSyncGate = new SyncGate();
