import { ApiError } from "../apiError";
import {
  RETRY_MAX_DELAY_MS,
  classifyUploadError,
  formatRetryWait,
  nextAttemptAt,
  retryAllowed,
  retryDelayMs
} from "./retryPolicy";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(classifyUploadError(new ApiError("Lot not found", 404)) === "permanent", "404 permanent");
assert(classifyUploadError(new ApiError("too many photos", 400)) === "permanent", "400 permanent");
assert(classifyUploadError(new ApiError("Sale closed", 403)) === "permanent", "403 permanent");
assert(classifyUploadError(new ApiError("expired", 401)) === "transient", "401 transient");
assert(classifyUploadError(new ApiError("timeout", 408)) === "transient", "408 transient");
assert(classifyUploadError(new ApiError("Sale is closed", 409)) === "permanent", "409 permanent");
assert(classifyUploadError(new ApiError("slow down", 429)) === "transient", "429 transient");
assert(classifyUploadError(new ApiError("boom", 500)) === "transient", "500 transient");
assert(classifyUploadError(new ApiError("bad gateway", 502)) === "transient", "502 transient");
assert(classifyUploadError(new Error("Network request failed")) === "transient", "network transient");
assert(
  classifyUploadError(new Error("Upload timed out after 300s — photos are still on this phone")) ===
    "transient",
  "timeout transient"
);
assert(
  classifyUploadError(new Error("Photo file is not readable on this phone — re-snap")) === "permanent",
  "unreadable file permanent"
);
assert(
  classifyUploadError(new Error("Could not open any photos for this lot")) === "permanent",
  "no photos permanent"
);
assert(classifyUploadError("weird") === "transient", "unknown transient");

assert(retryDelayMs(1) === 30_000, "first retry 30s");
assert(retryDelayMs(2) === 60_000, "second 1m");
assert(retryDelayMs(3) === 120_000, "third 2m");
assert(retryDelayMs(0) === 30_000, "attempts floor");
assert(retryDelayMs(50) === RETRY_MAX_DELAY_MS, "capped at 30 min");
assert(retryDelayMs(7) === RETRY_MAX_DELAY_MS, "30s * 64 > 30m → cap");
assert(nextAttemptAt(1, 1_000) === 31_000, "nextAttemptAt adds delay");

const now = 1_000_000;
assert(retryAllowed({ now }) === true, "fresh lot allowed");
assert(retryAllowed({ errorKind: "transient", nextAttemptAt: now + 5, now }) === false, "backoff waits");
assert(retryAllowed({ errorKind: "transient", nextAttemptAt: now - 5, now }) === true, "backoff over");
assert(
  retryAllowed({ errorKind: "transient", nextAttemptAt: now + 5, now, manual: true }) === true,
  "manual Sync skips backoff"
);
assert(retryAllowed({ errorKind: "permanent", now }) === false, "permanent waits for Retry");
assert(retryAllowed({ errorKind: "permanent", now, manual: true }) === false, "manual Sync ≠ Retry");

assert(formatRetryWait(null, now) === null, "no wait");
assert(formatRetryWait(now - 1, now) === null, "past wait");
assert(formatRetryWait(now + 12_000, now) === "Retrying in 12s", "seconds");
assert(formatRetryWait(now + 125_000, now) === "Retrying in 3 min", "minutes");

console.log("retryPolicy selftest ok");
