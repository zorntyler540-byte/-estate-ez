/**
 * Pause signal for a running Sync. When the connection drops (or Wi‑Fi only is on and the phone
 * fell back to cellular), Sync stops starting new files and in-flight upload tasks are cancelled.
 * The lot goes back to "queued" without counting a failed attempt; the next Sync resumes it from
 * the names the server already has. Pure — no React Native imports (selftested under tsx).
 */

export type PauseReason = "offline" | "wifi_only" | "time_budget";

export const UPLOAD_PAUSED_MESSAGE =
  "Paused — the connection dropped. Picks up where it left off when the connection is stable.";

export class UploadPausedError extends Error {
  readonly reason: PauseReason;
  constructor(reason: PauseReason) {
    super(
      reason === "wifi_only"
        ? "Paused — Upload on Wi‑Fi only is on and this phone left Wi‑Fi."
        : reason === "time_budget"
          ? "Paused — background time ran out. Sync picks up where it left off."
          : UPLOAD_PAUSED_MESSAGE
    );
    this.name = "UploadPausedError";
    this.reason = reason;
  }
}

export function isUploadPausedError(e: unknown): e is UploadPausedError {
  return e instanceof UploadPausedError || (e instanceof Error && e.name === "UploadPausedError");
}

export function isPausedMessage(message: string | null | undefined): boolean {
  return (message || "").trim().startsWith("Paused —");
}

type Listener = (reason: PauseReason) => void;

export class PauseSignal {
  private reasonValue: PauseReason | null = null;
  private listeners = new Set<Listener>();

  get paused(): boolean {
    return this.reasonValue !== null;
  }

  get reason(): PauseReason | null {
    return this.reasonValue;
  }

  /** First reason wins; listeners (in-flight upload tasks) hear it once. */
  pause(reason: PauseReason): void {
    if (this.reasonValue) return;
    this.reasonValue = reason;
    for (const fn of [...this.listeners]) {
      try {
        fn(reason);
      } catch {
        /* a listener must not stop the others from cancelling */
      }
    }
  }

  /** Called when a Sync starts and ends; a pause only applies to the Sync it interrupted. */
  clear(): void {
    this.reasonValue = null;
  }

  throwIfPaused(): void {
    if (this.reasonValue) throw new UploadPausedError(this.reasonValue);
  }

  /** Returns an unsubscribe. Fires immediately when already paused. */
  onPause(fn: Listener): () => void {
    if (this.reasonValue) {
      fn(this.reasonValue);
      return () => undefined;
    }
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }
}

export const uploadPause = new PauseSignal();

/**
 * A failed upload is a pause (no attempt counted, no backoff) when the pause signal fired, or
 * when a transient network error lands while the phone reports no connection — the native task
 * often fails a moment before NetInfo announces the drop.
 */
export function treatAsPause(opts: {
  error: unknown;
  signalPaused: boolean;
  permanent: boolean;
  netOffline: boolean;
}): boolean {
  if (isUploadPausedError(opts.error) || opts.signalPaused) return true;
  if (opts.permanent) return false;
  return opts.netOffline;
}
