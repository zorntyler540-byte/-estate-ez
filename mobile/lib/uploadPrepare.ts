import * as FileSystem from "expo-file-system/legacy";
import * as ImageManipulator from "expo-image-manipulator";

/** Keep multipart uploads small enough for field Wi‑Fi / cellular. */
export const UPLOAD_MAX_EDGE_PX = 1600;
export const UPLOAD_JPEG_QUALITY = 0.72;
/**
 * One photo per request. RN ``fetch``+FormData multi-file posts were hanging until
 * our abort timer; native ``uploadAsync`` one-at-a-time is reliable.
 */
export const UPLOAD_CHUNK_SIZE = 1;
/** Give native upload plenty of time per photo (not the old 160s multi-file abort). */
export const UPLOAD_PER_FILE_TIMEOUT_MS = 5 * 60_000;
const COMPRESS_TIMEOUT_MS = 25_000;

export type UploadPhotoUri = { uri: string; name: string };

export type PreparePhotosResult = {
  photos: UploadPhotoUri[];
  skippedIds: string[];
  skipped: number;
};

export function normalizeFileUri(uri: string): string {
  const raw = (uri || "").trim();
  if (!raw) return "";
  if (raw.startsWith("file://")) return raw;
  if (raw.startsWith("/")) return `file://${raw}`;
  return raw;
}

/**
 * Build URI variants to try. Includes remapping ``.../Documents/lot-captures/...``
 * onto the current app container (paths can go stale across reinstalls while
 * expo-image still shows a cached thumb).
 */
export function uriCandidates(uri: string): string[] {
  const raw = (uri || "").trim();
  if (!raw) return [];
  const out: string[] = [];
  const add = (u: string) => {
    if (u && !out.includes(u)) out.push(u);
  };
  add(raw);
  const normalized = normalizeFileUri(raw);
  add(normalized);
  if (normalized.startsWith("file://")) {
    add(normalized.replace(/^file:\/\//, ""));
  }

  const doc = FileSystem.documentDirectory;
  const relMatch = raw.match(/\/Documents\/(lot-captures\/[^?#]+)/i);
  if (doc && relMatch?.[1]) {
    add(`${doc}${relMatch[1]}`);
    add(normalizeFileUri(`${doc}${relMatch[1]}`));
  }
  return out;
}

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
 * Produce a cache JPEG ImageManipulator can write (always FileSystem-readable).
 * Avoids ``copyAsync`` which was failing with "File … is not readable" on paths
 * that Queue thumbs still display via expo-image cache.
 */
export async function materializeUploadJpeg(uri: string): Promise<string | null> {
  for (const candidate of uriCandidates(uri)) {
    const tryUri = normalizeFileUri(candidate) || candidate;
    try {
      const manipulated = await withTimeout(
        ImageManipulator.manipulateAsync(
          tryUri,
          [{ resize: { width: UPLOAD_MAX_EDGE_PX } }],
          {
            compress: UPLOAD_JPEG_QUALITY,
            format: ImageManipulator.SaveFormat.JPEG
          }
        ),
        COMPRESS_TIMEOUT_MS,
        "compress-timeout"
      );
      if (manipulated.uri) return manipulated.uri;
    } catch {
      // try next candidate
    }
    // No-op re-encode (sometimes succeeds when resize path fails).
    try {
      const probed = await withTimeout(
        ImageManipulator.manipulateAsync(tryUri, [], {
          compress: UPLOAD_JPEG_QUALITY,
          format: ImageManipulator.SaveFormat.JPEG
        }),
        COMPRESS_TIMEOUT_MS,
        "compress-timeout"
      );
      if (probed.uri) return probed.uri;
    } catch {
      // try next
    }
  }
  return null;
}

/**
 * Resolve a local file URI for native ``uploadAsync``.
 * Prefers a freshly materialized cache JPEG; falls back to a remapped original.
 * Never uses ``copyAsync`` (Build 14 failure mode).
 */
export async function resolveUploadFileUri(photo: UploadPhotoUri): Promise<string> {
  const materialized = await materializeUploadJpeg(photo.uri);
  if (materialized) return materialized;

  for (const candidate of uriCandidates(photo.uri)) {
    const tryUri = normalizeFileUri(candidate) || candidate;
    // Prefer file:// form for URLSession.
    if (tryUri.startsWith("file://")) return tryUri;
  }
  const fallback = normalizeFileUri(photo.uri);
  if (fallback) return fallback;
  throw new Error(
    "Photo file is not readable on this phone — open the preview; if it is blank, re-snap that lot."
  );
}

/**
 * Prepare queue photos for upload.
 */
export async function preparePhotosForUpload(
  photos: { id: string; uri: string; name: string }[]
): Promise<PreparePhotosResult> {
  const out: UploadPhotoUri[] = [];
  const skippedIds: string[] = [];

  for (const photo of photos) {
    const name = photo.name.replace(/\.[^.]+$/, "") + ".jpg";
    try {
      const uri = await resolveUploadFileUri({ uri: photo.uri, name });
      out.push({ uri, name });
    } catch {
      skippedIds.push(photo.id);
    }
  }

  return { photos: out, skippedIds, skipped: skippedIds.length };
}

export function chunkPhotos<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

/** @deprecated Prefer ``UPLOAD_PER_FILE_TIMEOUT_MS``; kept for call sites. */
export function uploadTimeoutMs(fileCount: number): number {
  return UPLOAD_PER_FILE_TIMEOUT_MS * Math.max(1, fileCount);
}
