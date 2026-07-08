import type { AccessTokenClaims } from "@/lib/types";

function decodeBase64Url(value: string): string | null {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
  if (typeof globalThis.atob !== "function") return null;

  try {
    const binary = globalThis.atob(padded);
    let escaped = "";
    for (let i = 0; i < binary.length; i += 1) {
      escaped += `%${binary.charCodeAt(i).toString(16).padStart(2, "0")}`;
    }
    try {
      return decodeURIComponent(escaped);
    } catch {
      return binary;
    }
  } catch {
    return null;
  }
}

export function accessTokenClaims(token: string | null): AccessTokenClaims | null {
  if (!token) return null;
  const [, payload] = token.split(".");
  if (!payload) return null;
  const decoded = decodeBase64Url(payload);
  if (!decoded) return null;

  try {
    const claims = JSON.parse(decoded) as Record<string, unknown>;
    return {
      sub: typeof claims.sub === "string" ? claims.sub : undefined,
      account_id: typeof claims.account_id === "string" ? claims.account_id : undefined,
      role: typeof claims.role === "string" ? claims.role : undefined,
      exp: typeof claims.exp === "number" ? claims.exp : undefined
    };
  } catch {
    return null;
  }
}

export function accountIdFromToken(token: string | null): string | null {
  return accessTokenClaims(token)?.account_id ?? null;
}
