// Plain JS so next.config.mjs (build-time guard) and lib/api.ts (runtime) share one rule.

export const DEV_DEFAULT_API_BASE = "http://localhost:8000";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

/** @param {string} url */
export function isLocalApiBase(url) {
  const raw = (url || "").trim();
  if (!raw) return false;
  try {
    return LOCAL_HOSTS.has(new URL(raw).hostname.toLowerCase());
  } catch {
    return /localhost|127\.0\.0\.1/i.test(raw);
  }
}

/**
 * Production builds need an explicit, valid NEXT_PUBLIC_API_BASE: https:// for a deployed
 * API, or http(s)://localhost for a local production build. There is no fallback URL.
 * Development (`next dev`) defaults to the local API.
 *
 * @param {string | undefined} fromEnv
 * @param {{ production: boolean }} opts
 * @returns {{ url: string, error: string | null }}
 */
export function checkApiBase(fromEnv, { production }) {
  const raw = (fromEnv || "").trim().replace(/\/+$/, "");
  if (!production) return { url: raw || DEV_DEFAULT_API_BASE, error: null };
  if (!raw) {
    return {
      url: "",
      error:
        "NEXT_PUBLIC_API_BASE is not set. Production builds need the public API URL, " +
        "e.g. NEXT_PUBLIC_API_BASE=https://your-api.up.railway.app (no trailing slash)."
    };
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return { url: "", error: `NEXT_PUBLIC_API_BASE is not a valid URL: "${raw}".` };
  }
  const local = LOCAL_HOSTS.has(parsed.hostname.toLowerCase());
  if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) {
    return {
      url: "",
      error: `NEXT_PUBLIC_API_BASE must start with https:// (http:// is only allowed for localhost): "${raw}".`
    };
  }
  return { url: raw, error: null };
}
