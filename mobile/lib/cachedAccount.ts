/**
 * Last ``GET /auth/me`` result, kept so a client login stays locked to its sale when the
 * phone opens offline (estate basements). Without it, a failed ``/auth/me`` left the role
 * unknown and the app fell back to the staff UI.
 */
export type CachedAccount = {
  email: string;
  role: string;
  assigned_sale_id: string | null;
  assigned_sale_name: string | null;
};

export type AccountLock =
  | { kind: "staff" }
  | { kind: "client-sale"; saleId: string; saleName: string | null }
  | { kind: "client-no-sale" };

export function serializeAccount(me: {
  email?: string | null;
  role?: string | null;
  assigned_sale_id?: string | null;
  assigned_sale_name?: string | null;
}): string {
  const account: CachedAccount = {
    email: (me.email || "").trim(),
    role: (me.role || "").trim(),
    assigned_sale_id: me.assigned_sale_id || null,
    assigned_sale_name: me.assigned_sale_name || null
  };
  return JSON.stringify(account);
}

/** ``null`` when missing, malformed, or saved for a different email than the one signed in. */
export function parseCachedAccount(
  raw: string | null | undefined,
  signedInEmail?: string | null
): CachedAccount | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const o = parsed as Record<string, unknown>;
  const email = typeof o.email === "string" ? o.email.trim() : "";
  const role = typeof o.role === "string" ? o.role.trim() : "";
  if (!role) return null;
  const expected = (signedInEmail || "").trim().toLowerCase();
  if (expected && email.toLowerCase() !== expected) return null;
  const saleId = typeof o.assigned_sale_id === "string" && o.assigned_sale_id ? o.assigned_sale_id : null;
  const saleName = typeof o.assigned_sale_name === "string" && o.assigned_sale_name ? o.assigned_sale_name : null;
  return { email, role, assigned_sale_id: saleId, assigned_sale_name: saleName };
}

export function accountLock(account: {
  role?: string | null;
  assigned_sale_id?: string | null;
  assigned_sale_name?: string | null;
}): AccountLock {
  if ((account.role || "").trim().toLowerCase() !== "client") return { kind: "staff" };
  if (account.assigned_sale_id) {
    return { kind: "client-sale", saleId: account.assigned_sale_id, saleName: account.assigned_sale_name || null };
  }
  return { kind: "client-no-sale" };
}
