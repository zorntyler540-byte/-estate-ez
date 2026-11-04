import { apiErrorStatus, isPermanentClientError } from "../apiError";

/**
 * ``permanent`` = the same request will keep failing (lot gone, sale closed, over the photo cap,
 * unreadable file). Sync stops retrying until staff tap Retry. ``transient`` = network, timeout,
 * 5xx, 401/408/429 — retried automatically with backoff.
 */
export type RetryKind = "permanent" | "transient";

export const RETRY_BASE_DELAY_MS = 30_000;
export const RETRY_MAX_DELAY_MS = 30 * 60_000;

/** Local problems no retry can fix: the file itself is gone or unreadable. */
const LOCAL_PERMANENT = /not readable|could not open any photos|snap at least one photo|record that clip again/i;

export function classifyUploadError(e: unknown): RetryKind {
  if (isPermanentClientError(e)) return "permanent";
  if (apiErrorStatus(e) != null) return "transient";
  const msg = e instanceof Error ? e.message : String(e ?? "");
  return LOCAL_PERMANENT.test(msg) ? "permanent" : "transient";
}

/** 30s, 1m, 2m, 4m … capped at 30 minutes. ``attempts`` counts failures so far (1 = first). */
export function retryDelayMs(attempts: number): number {
  const n = Math.max(1, Math.floor(attempts));
  const delay = RETRY_BASE_DELAY_MS * 2 ** Math.min(n - 1, 16);
  return Math.min(RETRY_MAX_DELAY_MS, delay);
}

export function nextAttemptAt(attempts: number, now: number): number {
  return now + retryDelayMs(attempts);
}

/**
 * Whether Sync may send this lot now. Permanent failures wait for Retry. Automatic Sync honors
 * the backoff; a Sync the user tapped skips the wait for transient failures.
 */
export function retryAllowed(opts: {
  errorKind?: string | null;
  nextAttemptAt?: number | null;
  now: number;
  manual?: boolean;
}): boolean {
  if (opts.errorKind === "permanent") return false;
  if (opts.manual) return true;
  const at = Number(opts.nextAttemptAt || 0);
  return !at || at <= opts.now;
}

export function formatRetryWait(nextAt: number | null | undefined, now: number): string | null {
  const at = Number(nextAt || 0);
  if (!at || at <= now) return null;
  const secs = Math.ceil((at - now) / 1000);
  if (secs < 60) return `Retrying in ${secs}s`;
  const mins = Math.ceil(secs / 60);
  return `Retrying in ${mins} min`;
}
