import { parseApiError } from "./apiError";
import { API_BASE } from "./config";
import { refreshSessionIfPossible } from "./sessionBridge";
import { videoMimeType } from "./mediaKind";
import type { Job, Lot, LotPhoto, LotVideo, Sale, TokenResponse } from "./types";
import {
  UPLOAD_PER_FILE_TIMEOUT_MS,
  resolveUploadFileUri,
  resolveUploadVideoUri,
  uriWithUploadFileName,
  type UploadPhotoUri
} from "./uploadPrepare";
import * as FileSystem from "expo-file-system/legacy";

export type SaleLotCoverUrls = {
  expires_in: number;
  urls: Record<string, string>;
};

export { isProxyBusyError, parseApiError } from "./apiError";

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

export type CtSellerSaleSync = {
  connected?: boolean;
  created?: number;
  renamed?: number;
  sales?: Sale[];
};

/** Mirror open CT Bids auctions into Estate-EZ sales (create missing, refresh names). */
export async function syncSalesFromCtSeller(token: string): Promise<CtSellerSaleSync> {
  return apiFetch<CtSellerSaleSync>("/sales/sync-from-ctseller", token, { method: "POST" });
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

export async function listLotVideos(token: string, lotId: string): Promise<LotVideo[]> {
  return apiFetch<LotVideo[]>(`/lots/${encodeURIComponent(lotId)}/videos`, token);
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

async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_, reject) => {
        timer = setTimeout(() => reject(new Error(label)), ms);
      })
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Native multipart upload (URLSession / OkHttp) — avoids React Native ``fetch``+FormData
 * hangs that were aborting Sync with fake timeout errors.
 */
async function nativeMultipartUpload(opts: {
  path: string;
  token: string;
  fileUri: string;
  fileName: string;
  mimeType?: string;
  parameters?: Record<string, string>;
  alreadyRetried?: boolean;
}): Promise<string> {
  // ``preparePhotosForUpload`` already materializes a cache JPEG when possible.
  // Do not copyAsync — that was the Build 14 "File … is not readable" failure.
  let uploadUri = opts.fileUri.trim();
  if (!uploadUri.startsWith("file://") && uploadUri.startsWith("/")) {
    uploadUri = `file://${uploadUri}`;
  }
  const isVideoUpload = Boolean(opts.mimeType && opts.mimeType.startsWith("video/"));
  if (!uploadUri.startsWith("file://")) {
    uploadUri = isVideoUpload
      ? await resolveUploadVideoUri(opts.fileUri)
      : await resolveUploadFileUri({ uri: opts.fileUri, name: opts.fileName });
  }
  uploadUri = await uriWithUploadFileName(uploadUri, opts.fileName);

  const url = `${API_BASE}${opts.path}`;
  let result: FileSystem.FileSystemUploadResult;
  try {
    result = await withTimeout(
      FileSystem.uploadAsync(url, uploadUri, {
        httpMethod: "POST",
        uploadType: FileSystem.FileSystemUploadType.MULTIPART,
        fieldName: "files",
        mimeType: opts.mimeType || "image/jpeg",
        sessionType: FileSystem.FileSystemSessionType.FOREGROUND,
        headers: { Authorization: `Bearer ${opts.token}` },
        parameters: opts.parameters
      }),
      UPLOAD_PER_FILE_TIMEOUT_MS,
      `Upload timed out after ${Math.round(UPLOAD_PER_FILE_TIMEOUT_MS / 1000)}s — photos are still on this phone; tap Sync to retry.`
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/not readable|could not be opened|no such file/i.test(msg)) {
      // Last chance: rematerialize from the original path / remapped Documents URI.
      // Videos skip JPEG rematerialize — that turned clips into stills.
      const retryUri = isVideoUpload
        ? await resolveUploadVideoUri(opts.fileUri)
        : await resolveUploadFileUri({ uri: opts.fileUri, name: opts.fileName });
      if (retryUri !== uploadUri) {
        result = await withTimeout(
          FileSystem.uploadAsync(url, retryUri, {
            httpMethod: "POST",
            uploadType: FileSystem.FileSystemUploadType.MULTIPART,
            fieldName: "files",
            mimeType: opts.mimeType || "image/jpeg",
            sessionType: FileSystem.FileSystemSessionType.FOREGROUND,
            headers: { Authorization: `Bearer ${opts.token}` },
            parameters: opts.parameters
          }),
          UPLOAD_PER_FILE_TIMEOUT_MS,
          `Upload timed out after ${Math.round(UPLOAD_PER_FILE_TIMEOUT_MS / 1000)}s — photos are still on this phone; tap Sync to retry.`
        );
      } else {
        throw new Error(
          isVideoUpload
            ? "Video file is not readable on this phone — record that clip again."
            : "Photo file is not readable on this phone — open the preview; if it is blank, re-snap that lot."
        );
      }
    } else {
      throw e;
    }
  }

  if (result.status === 401 && opts.token && !opts.alreadyRetried) {
    const refreshed = await refreshSessionIfPossible();
    if (refreshed && refreshed !== opts.token) {
      return nativeMultipartUpload({ ...opts, token: refreshed, alreadyRetried: true });
    }
  }
  if (result.status < 200 || result.status >= 300) {
    throw new Error(parseApiError(result.body ?? "", `Upload failed (${result.status})`));
  }
  return result.body ?? "";
}

/** Same contract as web dashboard ``/jobs/upload`` (single lot mode). */
export async function uploadSingleLotJob(opts: UploadLotJobOptions): Promise<Job> {
  if (!opts.photoUris.length) {
    throw new Error("No photos to upload");
  }
  const [first, ...rest] = opts.photoUris;
  const parameters: Record<string, string> = {
    sale_id: opts.saleId,
    inherit_sku_from_subfolder: "false",
    single_lot_mode: "true",
    listing_channel: opts.listingChannel === "facebook" ? "facebook" : "ctseller"
  };
  if (opts.manualSku?.trim()) {
    parameters.single_lot_manual_sku = opts.manualSku.trim();
  }

  const text = await nativeMultipartUpload({
    path: "/jobs/upload",
    token: opts.token,
    fileUri: first!.uri,
    fileName: first!.name,
    parameters
  });
  const job = JSON.parse(text) as Job;
  for (const photo of rest) {
    await appendFilesToQueuedJob(opts.token, job.id, [photo]);
  }
  return job;
}

/** Append more images to a queued upload job (one photo per request). */
export async function appendFilesToQueuedJob(
  token: string,
  jobId: string,
  photoUris: UploadPhotoUri[]
): Promise<Job> {
  if (!photoUris.length) {
    throw new Error("No photos to append");
  }
  let lastText = "";
  for (const photo of photoUris) {
    lastText = await nativeMultipartUpload({
      path: `/jobs/${encodeURIComponent(jobId)}/upload-files`,
      token,
      fileUri: photo.uri,
      fileName: photo.name
    });
  }
  return JSON.parse(lastText) as Job;
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
  const out: LotPhoto[] = [];
  for (const photo of photoUris) {
    const text = await nativeMultipartUpload({
      path: `/lots/${encodeURIComponent(lotId)}/photos/upload`,
      token,
      fileUri: photo.uri,
      fileName: photo.name
    });
    const raw = text ? (JSON.parse(text) as unknown) : null;
    if (Array.isArray(raw)) {
      out.push(...(raw as LotPhoto[]));
    } else if (raw && typeof raw === "object") {
      out.push(raw as LotPhoto);
    } else {
      throw new Error("Upload response was not a photo list");
    }
  }
  return out;
}

/** Attach clips to an existing catalog lot (skips Gemini / CT Bids). */
export async function uploadLotVideos(
  token: string,
  lotId: string,
  videoUris: UploadPhotoUri[]
): Promise<LotVideo[]> {
  if (!videoUris.length) {
    throw new Error("No videos to upload");
  }
  const out: LotVideo[] = [];
  for (const video of videoUris) {
    const text = await nativeMultipartUpload({
      path: `/lots/${encodeURIComponent(lotId)}/videos/upload`,
      token,
      fileUri: video.uri,
      fileName: video.name,
      mimeType: videoMimeType(video.name)
    });
    const raw = text ? (JSON.parse(text) as unknown) : null;
    if (Array.isArray(raw)) {
      out.push(...(raw as LotVideo[]));
    } else if (raw && typeof raw === "object") {
      out.push(raw as LotVideo);
    } else {
      throw new Error("Upload response was not a video list");
    }
  }
  return out;
}

export async function getLotVideoPlayUrl(
  token: string,
  lotId: string,
  videoId: string
): Promise<string> {
  const res = await apiFetch<{ url?: string }>(
    `/lots/${encodeURIComponent(lotId)}/videos/${encodeURIComponent(videoId)}/media-url`,
    token
  );
  if (!res.url) throw new Error("Could not get a playable video URL");
  return res.url;
}

export function lotVideoStreamSource(lotId: string, videoId: string, token: string) {
  return {
    uri: `${API_BASE}/lots/${encodeURIComponent(lotId)}/videos/${encodeURIComponent(videoId)}/stream`,
    headers: { Authorization: `Bearer ${token}` }
  };
}

export async function runJob(jobId: string, token: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${jobId}/run`, token, { method: "POST" });
}

/** Jobs for a sale (used to confirm a lot actually cataloged on the server, not just that /run was accepted). */
export async function listJobsForSale(token: string, saleId: string): Promise<Job[]> {
  const params = new URLSearchParams({ sale_id: saleId });
  return apiFetch<Job[]>(`/jobs?${params.toString()}`, token);
}

export type CatalogedMobileDraftLot = {
  draft_id: string;
  lot_id: string;
  sku?: string | null;
  photo_count?: number;
  auction_title?: string | null;
};

/** Phone drafts that already have catalog photos on this sale (ids + lot mapping). */
export async function listCatalogedMobileDraftsDetailed(
  token: string,
  saleId: string
): Promise<{ draftIds: string[]; lots: CatalogedMobileDraftLot[] }> {
  const res = await apiFetch<{ draft_ids?: string[]; lots?: CatalogedMobileDraftLot[] }>(
    `/sales/${encodeURIComponent(saleId)}/cataloged-mobile-drafts`,
    token
  );
  const lots = Array.isArray(res.lots) ? res.lots : [];
  const draftIds = (res.draft_ids || lots.map((row) => row.draft_id)).map((id) => id.toLowerCase());
  return { draftIds, lots };
}

/** Phone draft ids that already have catalog photos on this sale. */
export async function listCatalogedMobileDrafts(token: string, saleId: string): Promise<string[]> {
  const res = await listCatalogedMobileDraftsDetailed(token, saleId);
  return res.draftIds;
}

/** Remove a queued/failed job (and its upload folder when safe). */
export async function deleteJob(jobId: string, token: string): Promise<void> {
  await apiFetch<void>(`/jobs/${encodeURIComponent(jobId)}`, token, { method: "DELETE" });
}
