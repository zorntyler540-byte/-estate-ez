/** Live FastAPI — used for TestFlight / EAS Update even if a local ``.env`` says localhost. */
export const PRODUCTION_API_BASE = "https://caringtransitions-ai-production.up.railway.app";

export function isLocalApiBase(url: string): boolean {
  const raw = url.trim();
  if (!raw) return false;
  try {
    const host = new URL(raw).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "10.0.2.2";
  } catch {
    return /localhost|127\.0\.0\.1/i.test(raw);
  }
}

/**
 * ``eas update`` does **not** read ``eas.json`` ``build.env``. SDK 54 uses local ``.env``,
 * which is often ``http://localhost:8000``. Release bundles (``__DEV__ === false``) must
 * never keep that URL or field phones try to talk to the handset itself.
 */
export function resolveApiBase(fromEnv: string | undefined, isDev: boolean): string {
  const raw = (fromEnv || "").trim().replace(/\/$/, "");
  if (!isDev) {
    if (!raw || isLocalApiBase(raw)) return PRODUCTION_API_BASE;
    return raw;
  }
  return raw || "http://localhost:8000";
}

export const API_BASE = resolveApiBase(
  process.env.EXPO_PUBLIC_API_BASE,
  Boolean((globalThis as { __DEV__?: boolean }).__DEV__)
);

export const SELECTED_SALE_KEY = "ct_mobile_selected_sale_id";

export const SELECTED_SALE_NAME_KEY = "ct_mobile_selected_sale_name";
