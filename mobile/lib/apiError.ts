const PROXY_BUSY =
  /upstream error|no healthy upstream|application failed to respond|bad gateway|\b502\b|\b503\b|\b504\b/i;

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
