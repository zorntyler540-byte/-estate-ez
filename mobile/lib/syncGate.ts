/**
 * One Sync at a time. A request that arrives mid-Sync (a lot was just queued) is remembered and
 * run once the current Sync ends — it used to be dropped, so that lot waited for the next
 * foreground / network change. Several requests collapse into one rerun; ``manual`` sticks.
 */
export type SyncRequest = { manual?: boolean };

export class SyncGate {
  private running = false;
  private pending: SyncRequest | null = null;

  get busy(): boolean {
    return this.running;
  }

  /** True = start now. False = a Sync is running; this request runs after it. */
  begin(req: SyncRequest = {}): boolean {
    if (this.running) {
      this.pending = { manual: Boolean(this.pending?.manual || req.manual) };
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
}
