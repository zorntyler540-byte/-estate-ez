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

function normalizeFileUri(uri: string): string {
  const raw = (uri || "").trim();
  if (!raw) return "";
  if (raw.startsWith("file://")) return raw;
  if (raw.startsWith("/")) return `file://${raw}`;
  return raw;
}

function uriCandidates(uri: string): string[] {
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

async function compressForUpload(uri: string): Promise<string | null> {
  for (const candidate of uriCandidates(uri)) {
    try {
      const manipulated = await withTimeout(
        ImageManipulator.manipulateAsync(
          normalizeFileUri(candidate) || candidate,
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
      // try next URI form / fall through
    }
  }
  return null;
}

/**
 * Stage a JPEG under cache with a stable ``.jpg`` filename so native multipart
 * uploads send the name FastAPI / catalog expect.
 */
export async function stageUploadJpeg(photo: UploadPhotoUri): Promise<string> {
  const cache = FileSystem.cacheDirectory;
  if (!cache) {
    throw new Error("Phone storage unavailable for upload staging");
  }
  const dir = `${cache}ct-upload-stage/`;
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
  const safe = photo.name.replace(/[^\w.\-]+/g, "_");
  const dest = `${dir}${Date.now()}-${safe}`;
  await FileSystem.copyAsync({ from: photo.uri, to: dest });
  return dest;
}

/**
 * Prepare queue photos for upload. Prefer a timed compress; otherwise use the
 * original file URI that Queue/expo-image already displays.
 */
export async function preparePhotosForUpload(
  photos: { id: string; uri: string; name: string }[]
): Promise<PreparePhotosResult> {
  const out: UploadPhotoUri[] = [];
  const skippedIds: string[] = [];

  for (const photo of photos) {
    const name = photo.name.replace(/\.[^.]+$/, "") + ".jpg";
    const compressed = await compressForUpload(photo.uri);
    if (compressed) {
      out.push({ uri: compressed, name });
      continue;
    }
    const fallback = normalizeFileUri(photo.uri);
    if (fallback) {
      out.push({ uri: fallback, name });
      continue;
    }
    skippedIds.push(photo.id);
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
