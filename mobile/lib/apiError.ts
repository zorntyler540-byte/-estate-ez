const PROXY_BUSY =
  /upstream error|no healthy upstream|application failed to respond|bad gateway|\b502\b|\b503\b|\b504\b/i;

/** HTTP failure with the response status kept, so callers can tell permanent from transient. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function apiErrorStatus(e: unknown): number | null {
  if (e instanceof ApiError) return e.status;
  const status = (e as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : null;
}

/**
 * 4xx the same request will keep getting (too large, forbidden, gone, invalid).
 * 401 (credential refresh), 408 (timeout) and 429 (rate limit) can succeed later.
 */
export function isPermanentClientError(e: unknown): boolean {
  const status = apiErrorStatus(e);
  if (status == null || status < 400 || status >= 500) return false;
  return status !== 401 && status !== 408 && status !== 429;
}

export function isProxyBusyError(message: string | null | undefined): boolean {
  return PROXY_BUSY.test((message || "").trim());
}

export function parseApiError(raw: string, fallback = "Request failed"): string {
  const t = raw.trim();
  if (!t) return fallback;
  if (isProxyBusyError(t) || isProxyBusyError(t.replace(/<[^>]+>/g, " "))) {
    return "The website was busy. Open this lot again — do not Resend.";
  }
  try {
    const j = JSON.parse(t) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string" && d.trim()) {
      return isProxyBusyError(d) ? "The website was busy. Open this lot again — do not Resend." : d.trim();
    }
    if (Array.isArray(d)) {
      const parts = d
        .map((x) => {
          if (typeof x === "string") return x;
          if (x && typeof x === "object" && "msg" in x) return String((x as { msg: unknown }).msg);
          return "";
        })
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  } catch {
    /* not JSON */
  }
  return t.length > 280 ? `${t.slice(0, 277)}…` : t;
}
