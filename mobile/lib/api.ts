import { API_BASE } from "./config";
import { refreshSessionIfPossible } from "./sessionBridge";
import type { Job, Lot, LotPhoto, Sale, TokenResponse } from "./types";
import { uploadTimeoutMs, type UploadPhotoUri } from "./uploadPrepare";

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

function abortSignalFor(ms: number): AbortSignal {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(ms);
  }
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

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

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      signal: init?.signal ?? abortSignalFor(60_000)
    });
  } catch (e) {
    if (e instanceof Error && (e.name === "AbortError" || /aborted/i.test(e.message))) {
      throw new Error("Request timed out — check Wi‑Fi and try Sync again.");
    }
    throw e;
  }
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
  photoUris: UploadPhotoUri[];
};

/**
 * Multipart upload with the same 401 → silent re-login retry as ``apiFetch``.
 * Rebuilds FormData on retry; aborts if the request hangs past ``uploadTimeoutMs``.
 */
async function uploadFormData(
  path: string,
  token: string,
  buildForm: () => FormData,
  fallbackError: string,
  fileCount: number,
  alreadyRetried = false
): Promise<string> {
  const timeoutMs = uploadTimeoutMs(fileCount);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: buildForm(),
      signal: abortSignalFor(timeoutMs)
    });
  } catch (e) {
    if (e instanceof Error && (e.name === "AbortError" || /aborted/i.test(e.message))) {
      throw new Error(
        `Upload timed out after ${Math.round(timeoutMs / 1000)}s — try Sync again on stronger Wi‑Fi.`
      );
    }
    throw e;
  }
  const text = await res.text();
  if (!res.ok) {
    if (res.status === 401 && token && !alreadyRetried && !path.startsWith("/auth/login")) {
      const refreshed = await refreshSessionIfPossible();
      if (refreshed && refreshed !== token) {
        return uploadFormData(path, refreshed, buildForm, fallbackError, fileCount, true);
      }
    }
    throw new Error(parseApiError(text, `${fallbackError} (${res.status})`));
  }
  return text;
}

function appendJpegFiles(form: FormData, photoUris: UploadPhotoUri[]) {
  for (const photo of photoUris) {
    form.append("files", {
      uri: photo.uri,
      name: photo.name,
      type: "image/jpeg"
    } as unknown as Blob);
  }
}

/** Same contract as web dashboard ``/jobs/upload`` (single lot mode). */
export async function uploadSingleLotJob(opts: UploadLotJobOptions): Promise<Job> {
  const buildForm = () => {
    const form = new FormData();
    form.append("sale_id", opts.saleId);
    form.append("inherit_sku_from_subfolder", "false");
    form.append("single_lot_mode", "true");
    form.append("listing_channel", opts.listingChannel === "facebook" ? "facebook" : "ctseller");
    if (opts.manualSku?.trim()) {
      form.append("single_lot_manual_sku", opts.manualSku.trim());
    }
    appendJpegFiles(form, opts.photoUris);
    return form;
  };

  const text = await uploadFormData(
    "/jobs/upload",
    opts.token,
    buildForm,
    "Upload failed",
    opts.photoUris.length
  );
  return JSON.parse(text) as Job;
}

/** Append more images to a queued upload job (chunked mobile sync). */
export async function appendFilesToQueuedJob(
  token: string,
  jobId: string,
  photoUris: UploadPhotoUri[]
): Promise<Job> {
  if (!photoUris.length) {
    throw new Error("No photos to append");
  }
  const buildForm = () => {
    const form = new FormData();
    appendJpegFiles(form, photoUris);
    return form;
  };
  const text = await uploadFormData(
    `/jobs/${encodeURIComponent(jobId)}/upload-files`,
    token,
    buildForm,
    "Upload failed",
    photoUris.length
  );
  return JSON.parse(text) as Job;
}

/** Append photos to an existing catalog lot (same as web lot page “Add photos”). */
export async function uploadLotPhotos(
  token: string,
  lotId: string,
  photoUris: UploadPhotoUri[]
): Promise<LotPhoto[]> {
  if (!photoUris.length) {
    throw new Error("No photos to upload");
  }
  const buildForm = () => {
    const form = new FormData();
    appendJpegFiles(form, photoUris);
    return form;
  };
  const text = await uploadFormData(
    `/lots/${encodeURIComponent(lotId)}/photos/upload`,
    token,
    buildForm,
    "Upload failed",
    photoUris.length
  );
  const raw = text ? (JSON.parse(text) as unknown) : null;
  if (!Array.isArray(raw)) {
    throw new Error("Upload response was not a photo list");
  }
  return raw as LotPhoto[];
}

export async function runJob(jobId: string, token: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${jobId}/run`, token, { method: "POST" });
}

/** Download Facebook Marketplace inventory PDF bytes for a sale. */
export async function downloadFacebookInventoryPdf(
  token: string,
  saleId: string
): Promise<ArrayBuffer> {
  const res = await fetch(
    `${API_BASE}/exports/facebook-inventory.pdf?sale_id=${encodeURIComponent(saleId)}`,
    {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store"
    }
  );
  const textOrBuf = await (res.ok ? res.arrayBuffer() : res.text());
  if (!res.ok) {
    throw new Error(
      parseApiError(typeof textOrBuf === "string" ? textOrBuf : "", `PDF export failed (${res.status})`)
    );
  }
  return textOrBuf as ArrayBuffer;
}
