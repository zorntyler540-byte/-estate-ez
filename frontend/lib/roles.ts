export type AccountRole = "owner" | "catalogger" | string;

export function normalizeAccountRole(role: string | null | undefined): string {
  return (role || "").trim().toLowerCase();
}

export function isOwnerRole(role: string | null | undefined): boolean {
  return normalizeAccountRole(role) === "owner";
}

export function accountRoleLabel(role: string | null | undefined): string {
  const r = normalizeAccountRole(role);
  if (r === "owner") return "Owner";
  if (r === "catalogger") return "Catalogger";
  return role || "Unknown";
}
