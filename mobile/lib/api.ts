import { API_BASE } from "./config";
import { refreshSessionIfPossible } from "./sessionBridge";
import type { Job, Lot, LotPhoto, Sale, TokenResponse } from "./types";

export type SaleLotCoverUrls = {
  expires_in: number;
  urls: Record<string, string>;
};

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

type ApiFetchInit = RequestInit & {
  /** Internal: already retried after a silent credential refresh. */
  _authRetried?: boolean;
};

export async function apiFetch<T>(
  path: string,
  token: string | undefined,
  init?: ApiFetchInit
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const text = await res.text();
  if (!res.ok) {
    if (res.status === 401 && token && !init?._authRetried && !path.startsWith("/auth/login")) {
      const refreshed = await refreshSessionIfPossible();
      if (refreshed && refreshed !== token) {
        return apiFetch<T>(path, refreshed, { ...init, _authRetried: true });
      }
    }
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

export async function listLots(
  token: string,
  saleId: string,
  opts?: {
    sort?: "created_desc" | "created_asc" | "sku_asc";
    limit?: number;
    listingChannel?: "ctseller" | "facebook";
  }
): Promise<Lot[]> {
  const params = new URLSearchParams({ sale_id: saleId });
  if (opts?.sort) params.set("sort", opts.sort);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.listingChannel) params.set("listing_channel", opts.listingChannel);
  return apiFetch<Lot[]>(`/lots?${params}`, token);
}

export async function getLot(token: string, lotId: string): Promise<Lot> {
  return apiFetch<Lot>(`/lots/${lotId}`, token);
}

export async function listLotPhotos(token: string, lotId: string): Promise<LotPhoto[]> {
  return apiFetch<LotPhoto[]>(`/lots/${lotId}/photos`, token);
}

/** Batch presigned R2 cover URLs for a sale (one request for the whole grid). */
export async function listSaleLotCoverUrls(
  token: string,
  saleId: string,
  maxPx = 160
): Promise<SaleLotCoverUrls> {
  return apiFetch<SaleLotCoverUrls>(
    `/sales/${encodeURIComponent(saleId)}/lot-cover-urls?max_px=${maxPx}`,
    token
  );
}

/** Authenticated thumbnail URL for ``expo-image`` (Bearer header required). */
export function lotThumbnailSource(lotId: string, token: string, maxPx = 220) {
  return {
    uri: `${API_BASE}/lots/${lotId}/thumbnail?max_px=${maxPx}`,
    headers: { Authorization: `Bearer ${token}` }
  };
}

export function lotPhotoThumbnailSource(
  lotId: string,
  photoId: string,
  token: string,
  maxPx = 220
) {
  return {
    uri: `${API_BASE}/lots/${lotId}/photos/${photoId}/thumbnail?max_px=${maxPx}`,
    headers: { Authorization: `Bearer ${token}` }
  };
}

export type UploadLotJobOptions = {
  saleId: string;
  token: string;
  manualSku?: string;
  listingChannel?: "ctseller" | "facebook";
  photoUris: { uri: string; name: string }[];
};

/** Same contract as web dashboard ``/jobs/upload`` (single lot mode). */
export async function uploadSingleLotJob(opts: UploadLotJobOptions): Promise<Job> {
  const form = new FormData();
  form.append("sale_id", opts.saleId);
  form.append("inherit_sku_from_subfolder", "false");
  form.append("single_lot_mode", "true");
  form.append("listing_channel", opts.listingChannel === "facebook" ? "facebook" : "ctseller");
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

/** Append photos to an existing catalog lot (same as web lot page “Add photos”). */
export async function uploadLotPhotos(
  token: string,
  lotId: string,
  photoUris: { uri: string; name: string }[]
): Promise<LotPhoto[]> {
  if (!photoUris.length) {
    throw new Error("No photos to upload");
  }
  const form = new FormData();
  for (const photo of photoUris) {
    form.append("files", {
      uri: photo.uri,
      name: photo.name,
      type: "image/jpeg"
    } as unknown as Blob);
  }
  const res = await fetch(`${API_BASE}/lots/${encodeURIComponent(lotId)}/photos/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form
  });
  const text = await res.text();
  if (!res.ok) throw new Error(parseApiError(text, `Upload failed (${res.status})`));
  const raw = text ? (JSON.parse(text) as unknown) : null;
  if (!Array.isArray(raw)) {
    throw new Error("Upload response was not a photo list");
  }
  return raw as LotPhoto[];
}

export async function runJob(jobId: string, token: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${jobId}/run`, token, { method: "POST" });
}
