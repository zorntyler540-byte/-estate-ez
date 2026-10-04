export type AccountRole = "owner" | "catalogger" | "client" | string;

export const ACCOUNT_ROLE_STORAGE_KEY = "ct_account_role";

export function normalizeAccountRole(role: string | null | undefined): string {
  return (role || "").trim().toLowerCase();
}

export function isOwnerRole(role: string | null | undefined): boolean {
  return normalizeAccountRole(role) === "owner";
}

export function isClientRole(role: string | null | undefined): boolean {
  return normalizeAccountRole(role) === "client";
}

export function accountRoleLabel(role: string | null | undefined): string {
  const r = normalizeAccountRole(role);
  if (r === "owner") return "Owner";
  if (r === "catalogger") return "Catalogger";
  if (r === "client") return "Client";
  return role || "Unknown";
}

export function rememberAccountRole(role: string | null | undefined): void {
  try {
    const value = normalizeAccountRole(role);
    if (value) localStorage.setItem(ACCOUNT_ROLE_STORAGE_KEY, value);
    else localStorage.removeItem(ACCOUNT_ROLE_STORAGE_KEY);
  } catch {
    /* private mode */
  }
}

export function storedAccountRole(): string {
  try {
    return localStorage.getItem(ACCOUNT_ROLE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}
