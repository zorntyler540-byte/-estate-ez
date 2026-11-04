import { checkApiBase } from "./apiBaseRule";

export { isLocalApiBase } from "./apiBaseRule";

/**
 * ``eas update`` does **not** read ``eas.json`` ``build.env``; SDK 54 uses the shell env, then
 * local ``.env`` (often localhost). Release bundles (``__DEV__ === false``) fail closed on a
 * missing, invalid, or on-device URL instead of guessing one. ``app.config.js`` runs the same
 * check, so ``eas update`` / ``eas build`` refuse to produce such a bundle in the first place.
 */
export function resolveApiBase(fromEnv: string | undefined, isDev: boolean): string {
  const { url, error } = checkApiBase(fromEnv, !isDev);
  if (error) throw new Error(error);
  return url;
}

const launch = checkApiBase(
  process.env.EXPO_PUBLIC_API_BASE,
  !(globalThis as { __DEV__?: boolean }).__DEV__
);

export const API_BASE: string = launch.url;

/** Set only when this bundle shipped without a usable API URL. Requests refuse to run. */
export const API_BASE_ERROR: string | null = launch.error;

export function apiUrl(path: string): string {
  if (API_BASE_ERROR) throw new Error(API_BASE_ERROR);
  return `${API_BASE}${path}`;
}

/** SecureStore key for the API token (read by the background upload task too). */
export const ACCESS_TOKEN_KEY = "ct_mobile_access_token";
export const SELECTED_SALE_KEY = "ct_mobile_selected_sale_id";

export const SELECTED_SALE_NAME_KEY = "ct_mobile_selected_sale_name";
