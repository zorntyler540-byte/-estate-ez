// Plain CommonJS so app.config.js (blocks release exports) and lib/config.ts (runtime) share one rule.

const DEV_DEFAULT_API_BASE = "http://localhost:8000";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "10.0.2.2"]);

/** @param {string} url */
function isLocalApiBase(url) {
  const raw = (url || "").trim();
  if (!raw) return false;
  try {
    return LOCAL_HOSTS.has(new URL(raw).hostname.toLowerCase());
  } catch {
    return /localhost|127\.0\.0\.1/i.test(raw);
  }
}

/**
 * Release bundles (EAS Build / EAS Update, ``__DEV__ === false``) need an explicit https
 * EXPO_PUBLIC_API_BASE that is not this device. There is no fallback URL. Dev (Metro)
 * defaults to the local API.
 *
 * @param {string | undefined} fromEnv
 * @param {boolean} release
 * @returns {{ url: string, error: string | null }}
 */
function checkApiBase(fromEnv, release) {
  const raw = (fromEnv || "").trim().replace(/\/+$/, "");
  if (!release) return { url: raw || DEV_DEFAULT_API_BASE, error: null };
  if (!raw) {
    return {
      url: "",
      error:
        "EXPO_PUBLIC_API_BASE is not set. Release builds and EAS Updates need the public API URL, " +
        "e.g. EXPO_PUBLIC_API_BASE=https://your-api.up.railway.app"
    };
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return { url: "", error: `EXPO_PUBLIC_API_BASE is not a valid URL: "${raw}".` };
  }
  if (LOCAL_HOSTS.has(parsed.hostname.toLowerCase())) {
    return {
      url: "",
      error: `EXPO_PUBLIC_API_BASE points at this device (${raw}); release builds need the public API URL.`
    };
  }
  if (parsed.protocol !== "https:") {
    return { url: "", error: `EXPO_PUBLIC_API_BASE must start with https:// in release builds: "${raw}".` };
  }
  return { url: raw, error: null };
}

module.exports = { DEV_DEFAULT_API_BASE, checkApiBase, isLocalApiBase };
