import type { Sale } from "./types";

/**
 * Last open-sales list, so the sale picker still works when the phone starts offline.
 * Scoped to the signed-in email: another account on the same phone never sees these sales.
 */
export const SALES_CACHE_KEY = "open_sales_v1";

type CachedSales = { owner: string; sales: Sale[] };

function ownerKey(email: string | null | undefined): string {
  return (email || "").trim().toLowerCase();
}

export function serializeSalesCache(owner: string | null | undefined, sales: readonly Sale[]): string | null {
  const key = ownerKey(owner);
  if (!key) return null;
  const open = sales
    .filter((s) => s && s.id && !s.closed_at)
    .map((s) => ({
      id: String(s.id),
      account_id: String(s.account_id || ""),
      user_id: String(s.user_id || ""),
      name: String(s.name || ""),
      closed_at: null,
      created_at: String(s.created_at || "")
    }));
  return JSON.stringify({ owner: key, sales: open } satisfies CachedSales);
}

export function parseSalesCache(raw: string | null | undefined, owner: string | null | undefined): Sale[] {
  const key = ownerKey(owner);
  if (!raw || !key) return [];
  try {
    const parsed = JSON.parse(raw) as Partial<CachedSales>;
    if (parsed.owner !== key || !Array.isArray(parsed.sales)) return [];
    return parsed.sales.filter(
      (s): s is Sale => Boolean(s && typeof s.id === "string" && s.id && typeof s.name === "string" && !s.closed_at)
    );
  } catch {
    return [];
  }
}
