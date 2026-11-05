import { ApiError, parseApiError } from "./apiError";
import { API_BASE, apiUrl } from "./config";
import { refreshSessionIfPossible } from "./sessionBridge";
import { teamActivityQuery } from "./teamActivity";
import { videoMimeType } from "./mediaKind";
import type { Job, Lot, LotPhoto, LotVideo, Sale, TokenResponse } from "./types";
import {
  UPLOAD_PER_FILE_TIMEOUT_MS,
  resolveUploadFileUri,
  resolveUploadVideoUri,
  uriWithUploadFileName,
  type UploadPhotoUri
} from "./uploadPrepare";
import { UploadPausedError, uploadPause } from "./uploadPause";
import * as FileSystem from "expo-file-system/legacy";
import type { CatalogQuota } from "./queue/importPacing";

export type SaleLotCoverUrls = {
  expires_in: number;
  urls: Record<string, string>;
};

export { ApiError, isPermanentClientError, isProxyBusyError, parseApiError } from "./apiError";

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
    res = await fetch(apiUrl(path), {
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
    throw new ApiError(parseApiError(text, `HTTP ${res.status}`), res.status);
  }
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const API_PING_TIMEOUT_MS = 5_000;

/**
 * Quick reachability check before automatic uploads start. Any answer from the API below 500
 * counts (an older API without ``/health/ping`` answers 404 — still up); a proxy 502/503,
 * timeout, or network error does not.
 */
export async function pingApi(timeoutMs = API_PING_TIMEOUT_MS): Promise<boolean> {
  try {
    const res = await fetch(apiUrl("/health/ping"), {
      method: "GET",
      headers: { "Cache-Control": "no-cache" },
      signal: abortSignalFor(timeoutMs)
    });
    return res.status < 500;
  } catch {
    return false;
  }
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

export type AuthMe = {
  id: string;
  account_id: string;
  email: string;
  role: string;
  assigned_sale_id?: string | null;
  assigned_sale_name?: string | null;
};

export async function getAuthMe(token: string): Promise<AuthMe> {
  return apiFetch<AuthMe>("/auth/me", token);
}

export type TeamActivityPhotoTime = {
  at: string;
  source?: string;
};

export type TeamActivityLot = {
  lot_id: string;
  sale_id: string;
  sku?: string | null;
  auction_title?: string;
  photo_count: number;
  capture_minutes?: number | null;
  first_photo_at?: string;
  last_photo_at?: string;
  timing_source?: string;
  has_thumbnail?: boolean;
  photo_times?: TeamActivityPhotoTime[];
};

export type TeamActivityMember = {
  user_id: string;
  email: string;
  role: string;
  photos_uploaded: number;
  lots_created: number;
  jobs_started: number;
  active_hours: number;
  photos_per_hour: number;
  lots_per_hour: number;
  lots_posted?: number;
  lots_sold?: number;
  lots_unsold?: number;
  lots_live?: number;
  sold_revenue?: number;
  lots_photographed?: number;
  median_minutes_per_lot?: number | null;
  lots?: TeamActivityLot[];
};

export type TeamActivity = {
  since: string;
  until: string;
  members: TeamActivityMember[];
  totals: {
    photos_uploaded: number;
    lots_created: number;
    jobs_started: number;
    member_count: number;
    active_hours: number;
    lots_posted?: number;
    lots_sold?: number;
    lots_unsold?: number;
    lots_live?: number;
    sold_revenue?: number;
    lots_photographed?: number;
    median_minutes_per_lot?: number | null;
  };
  sold_items?: {
    lot_id: string;
    sku?: string | null;
    auction_title?: string;
    category: string;
    outcome?: string;
    sold_price: number;
  }[];
  sold_by_category?: {
    category: string;
    posted_count?: number;
    sold_count: number;
    unsold_count?: number;
    live_count?: number;
    revenue: number;
    avg_price: number;
  }[];
};

export async function getTeamActivity(token: string, sinceDays: number): Promise<TeamActivity> {
  return apiFetch<TeamActivity>(`/team/activity?${teamActivityQuery(sinceDays)}`, token);
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
    offset?: number;
    listingChannel?: "ctseller" | "facebook";
  }
): Promise<Lot[]> {
  const params = new URLSearchParams({ sale_id: saleId });
  if (opts?.sort) params.set("sort", opts.sort);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
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
export type HeroBackgroundStatus = {
  running: boolean;
  color: "studio" | "white" | "gray";
  total: number;
  done: number;
  updated: number;
  failed: number;
  skipped: number;
  current_sku?: string | null;
  last_error?: string | null;
};

export async function startSaleHeroBackground(
  token: string,
  saleId: string,
  color: "studio" | "white" | "gray"
): Promise<HeroBackgroundStatus> {
  return apiFetch<HeroBackgroundStatus>(
    `/sales/${encodeURIComponent(saleId)}/hero-background`,
    token,
    { method: "POST", body: JSON.stringify({ color }) }
  );
}

export async function getSaleHeroBackground(token: string, saleId: string): Promise<HeroBackgroundStatus> {
  return apiFetch<HeroBackgroundStatus>(`/sales/${encodeURIComponent(saleId)}/hero-background`, token);
}

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

function uploadTimeoutMessage(): string {
  return `Upload timed out after ${Math.round(UPLOAD_PER_FILE_TIMEOUT_MS / 1000)}s — photos are still on this phone; tap Sync to retry.`;
}

/**
 * One native upload task. BACKGROUND sessions keep going when the screen locks or staff switch
 * apps mid-Sync. A task that hits the timeout — or is still running when the connection drops
 * (``uploadPause``) — is cancelled, not just abandoned: an abandoned task kept the connection
 * (and the server's upload slot) busy and could land a late duplicate.
 */
async function runUploadTask(
  url: string,
  fileUri: string,
  options: FileSystem.FileSystemUploadOptions
): Promise<FileSystem.FileSystemUploadResult> {
  uploadPause.throwIfPaused();
  const task = FileSystem.createUploadTask(url, fileUri, {
    ...options,
    sessionType: FileSystem.FileSystemSessionType.BACKGROUND
  });
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = false;
  let stopListening: () => void = () => undefined;
  try {
    const result = await Promise.race([
      task.uploadAsync(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          cancel = true;
          reject(new Error(uploadTimeoutMessage()));
        }, UPLOAD_PER_FILE_TIMEOUT_MS);
      }),
      new Promise<never>((_, reject) => {
        stopListening = uploadPause.onPause((reason) => {
          cancel = true;
          reject(new UploadPausedError(reason));
        });
      })
    ]);
    if (!result) {
      if (uploadPause.reason) throw new UploadPausedError(uploadPause.reason);
      throw new Error(uploadTimeoutMessage());
    }
    return result;
  } finally {
    if (timer) clearTimeout(timer);
    stopListening();
    if (cancel) void task.cancelAsync().catch(() => undefined);
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
  // Sync hands over upload-ready files already named for the server; anything else is
  // resolved (and renamed into a cache copy) here.
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
  const namedUri = await uriWithUploadFileName(uploadUri, opts.fileName);
  const renamedCopy = namedUri !== uploadUri ? namedUri : null;
  uploadUri = namedUri;

  const url = apiUrl(opts.path);
  const options: FileSystem.FileSystemUploadOptions = {
    httpMethod: "POST",
    uploadType: FileSystem.FileSystemUploadType.MULTIPART,
    fieldName: "files",
    mimeType: opts.mimeType || "image/jpeg",
    headers: { Authorization: `Bearer ${opts.token}` },
    parameters: opts.parameters
  };
  let result: FileSystem.FileSystemUploadResult;
  try {
    try {
      result = await runUploadTask(url, uploadUri, options);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!/not readable|could not be opened|no such file/i.test(msg)) throw e;
      // Last chance: rematerialize from the original path / remapped Documents URI.
      // Videos skip JPEG rematerialize — that turned clips into stills.
      const retryUri = isVideoUpload
        ? await resolveUploadVideoUri(opts.fileUri)
        : await resolveUploadFileUri({ uri: opts.fileUri, name: opts.fileName });
      if (retryUri === uploadUri) {
        throw new Error(
          isVideoUpload
            ? "Video file is not readable on this phone — record that clip again."
            : "Photo file is not readable on this phone — open the preview; if it is blank, re-snap that lot."
        );
      }
      result = await runUploadTask(url, await uriWithUploadFileName(retryUri, opts.fileName), options);
    }
  } finally {
    // Renamed clip copies can be ~80 MB; do not leave them for the 6h cache sweep.
    if (renamedCopy) void FileSystem.deleteAsync(renamedCopy, { idempotent: true }).catch(() => undefined);
  }

  if (result.status === 401 && opts.token && !opts.alreadyRetried) {
    const refreshed = await refreshSessionIfPossible();
    if (refreshed && refreshed !== opts.token) {
      return nativeMultipartUpload({ ...opts, token: refreshed, alreadyRetried: true });
    }
  }
  if (result.status < 200 || result.status >= 300) {
    throw new ApiError(
      parseApiError(result.body ?? "", `Upload failed (${result.status})`),
      result.status
    );
  }
  return result.body ?? "";
}

function jobUploadParameters(opts: {
  photo: UploadPhotoUri;
  clientDraftId?: string;
}): Record<string, string> {
  const parameters: Record<string, string> = {};
  if (opts.photo.capturedAtMs && opts.photo.capturedAtMs > 0) {
    parameters.captured_at_ms = String(opts.photo.capturedAtMs);
  }
  const draftId = opts.clientDraftId?.trim().toLowerCase();
  if (draftId) parameters.client_draft_id = draftId;
  return parameters;
}

/**
 * First photo of a lot: creates the ``receiving`` job, or — when this draft already has one —
 * the server adds to it. The response lists every name already in the batch.
 */
export async function createLotJobWithPhoto(opts: {
  saleId: string;
  token: string;
  manualSku?: string;
  listingChannel?: "ctseller" | "facebook";
  clientDraftId?: string;
  photo: UploadPhotoUri;
}): Promise<Job> {
  const parameters: Record<string, string> = {
    sale_id: opts.saleId,
    inherit_sku_from_subfolder: "false",
    single_lot_mode: "true",
    listing_channel: opts.listingChannel === "facebook" ? "facebook" : "ctseller",
    ...jobUploadParameters({ photo: opts.photo, clientDraftId: opts.clientDraftId })
  };
  if (opts.manualSku?.trim()) {
    parameters.single_lot_manual_sku = opts.manualSku.trim();
  }
  const text = await nativeMultipartUpload({
    path: "/jobs/upload",
    token: opts.token,
    fileUri: opts.photo.uri,
    fileName: opts.photo.name,
    parameters
  });
  return JSON.parse(text) as Job;
}

/** One more photo into a ``receiving`` job. A name the batch already has counts as sent. */
export async function appendPhotoToJob(
  token: string,
  jobId: string,
  photo: UploadPhotoUri,
  clientDraftId?: string
): Promise<Job> {
  const parameters = jobUploadParameters({ photo, clientDraftId });
  const text = await nativeMultipartUpload({
    path: `/jobs/${encodeURIComponent(jobId)}/upload-files`,
    token,
    fileUri: photo.uri,
    fileName: photo.name,
    parameters: Object.keys(parameters).length ? parameters : undefined
  });
  return JSON.parse(text) as Job;
}

export type JobReceivedFiles = { job_id: string; status: string; names: string[]; folder_missing: boolean };

/** Names already staged for a job (resume a half-sent lot). 404 on older APIs or a deleted job. */
export async function getJobReceivedFiles(token: string, jobId: string): Promise<JobReceivedFiles> {
  const res = await apiFetch<Partial<JobReceivedFiles>>(
    `/jobs/${encodeURIComponent(jobId)}/received-files`,
    token
  );
  return {
    job_id: String(res?.job_id || jobId),
    status: String(res?.status || ""),
    names: Array.isArray(res?.names) ? res!.names!.map(String) : [],
    folder_missing: Boolean(res?.folder_missing)
  };
}

function listFromUploadResponse<T>(text: string, what: string): T[] {
  const raw = text ? (JSON.parse(text) as unknown) : null;
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === "object") return [raw as T];
  throw new Error(`Upload response was not a ${what} list`);
}

/** One photo onto an existing catalog lot. A stable phone name already on the lot is skipped. */
export async function uploadLotPhoto(token: string, lotId: string, photo: UploadPhotoUri): Promise<LotPhoto[]> {
  const text = await nativeMultipartUpload({
    path: `/lots/${encodeURIComponent(lotId)}/photos/upload`,
    token,
    fileUri: photo.uri,
    fileName: photo.name,
    parameters:
      photo.capturedAtMs && photo.capturedAtMs > 0
        ? { captured_at_ms: String(photo.capturedAtMs) }
        : undefined
  });
  return listFromUploadResponse<LotPhoto>(text, "photo");
}

/** One clip onto a catalog lot (skips Gemini / CT Bids). The same name twice is stored once. */
export async function uploadLotVideo(token: string, lotId: string, video: UploadPhotoUri): Promise<LotVideo[]> {
  const text = await nativeMultipartUpload({
    path: `/lots/${encodeURIComponent(lotId)}/videos/upload`,
    token,
    fileUri: video.uri,
    fileName: video.name,
    mimeType: videoMimeType(video.name)
  });
  return listFromUploadResponse<LotVideo>(text, "video");
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

/** Account catalog limits and today's usage; ``null`` when this API has no quota endpoint. */
export async function getCatalogQuota(token: string): Promise<CatalogQuota | null> {
  try {
    return await apiFetch<CatalogQuota>("/jobs/quota", token);
  } catch {
    return null;
  }
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

export type MobileDraftStatus = {
  draft_id: string;
  sale_id?: string | null;
  job_id?: string | null;
  job_status?: string | null;
  error_message?: string | null;
  lot_id?: string | null;
  cataloged?: boolean;
};

/** Server accepts at most this many ids per request. */
export const MOBILE_DRAFT_STATUS_MAX_IDS = 200;

/** Job + lot state for specific phone drafts in one request (no per-sale catalog scan). */
export async function getMobileDraftStatuses(
  token: string,
  draftIds: string[]
): Promise<MobileDraftStatus[]> {
  const ids = [...new Set(draftIds.map((id) => id.trim().toLowerCase()).filter(Boolean))].slice(
    0,
    MOBILE_DRAFT_STATUS_MAX_IDS
  );
  if (!ids.length) return [];
  const res = await apiFetch<{ drafts?: MobileDraftStatus[] }>("/mobile/drafts/status", token, {
    method: "POST",
    body: JSON.stringify({ draft_ids: ids })
  });
  return Array.isArray(res?.drafts) ? res.drafts : [];
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

export type SaleUploadHealth = {
  sale_id: string;
  counts: Record<string, number>;
  stuck_count: number;
  cataloging_count: number;
  jobs_with_client_draft_id?: Array<{
    id: string;
    client_draft_id: string;
    status: string;
    sku?: string | null;
    error_message?: string | null;
    age_seconds?: number;
  }>;
};

export async function getSaleUploadHealth(token: string, saleId: string): Promise<SaleUploadHealth> {
  return apiFetch<SaleUploadHealth>(
    `/sales/${encodeURIComponent(saleId)}/upload-health`,
    token
  );
}

export { formatUploadHealthSummary } from "./formatUploadHealth";
