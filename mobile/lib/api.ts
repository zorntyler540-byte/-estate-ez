import { API_BASE } from "./config";
import type { Job, Sale, TokenResponse } from "./types";

export function parseApiError(raw: string, fallback = "Request failed"): string {
  const t = raw.trim();
  if (!t) return fallback;
  try {
    const j = JSON.parse(t) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string" && d.trim()) return d.trim();
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

export async function apiFetch<T>(
  path: string,
  token: string | undefined,
  init?: RequestInit
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(parseApiError(text, `HTTP ${res.status}`));
  }
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/login", undefined, {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export async function listSales(token: string): Promise<Sale[]> {
  return apiFetch<Sale[]>("/sales", token);
}

export type UploadLotJobOptions = {
  saleId: string;
  token: string;
  manualSku?: string;
  photoUris: { uri: string; name: string }[];
};

/** Same contract as web dashboard ``/jobs/upload`` (single lot mode). */
export async function uploadSingleLotJob(opts: UploadLotJobOptions): Promise<Job> {
  const form = new FormData();
  form.append("sale_id", opts.saleId);
  form.append("inherit_sku_from_subfolder", "false");
  form.append("single_lot_mode", "true");
  if (opts.manualSku?.trim()) {
    form.append("single_lot_manual_sku", opts.manualSku.trim());
  }
  for (const photo of opts.photoUris) {
    form.append("files", {
      uri: photo.uri,
      name: photo.name,
      type: "image/jpeg"
    } as unknown as Blob);
  }

  const res = await fetch(`${API_BASE}/jobs/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${opts.token}` },
    body: form
  });
  const text = await res.text();
  if (!res.ok) throw new Error(parseApiError(text, `Upload failed (${res.status})`));
  return JSON.parse(text) as Job;
}

export async function runJob(jobId: string, token: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${jobId}/run`, token, { method: "POST" });
}
