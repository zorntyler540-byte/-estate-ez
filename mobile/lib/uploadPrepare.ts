import * as ImageManipulator from "expo-image-manipulator";

/** Keep multipart uploads small enough for field Wi‑Fi / cellular. */
export const UPLOAD_MAX_EDGE_PX = 2048;
export const UPLOAD_JPEG_QUALITY = 0.82;
/** Photos per `/jobs/upload` or `/upload-files` request. */
export const UPLOAD_CHUNK_SIZE = 3;

export type UploadPhotoUri = { uri: string; name: string };

export type PreparePhotosResult = {
  photos: UploadPhotoUri[];
  /** Local lot_photos.id values we could not open for upload. */
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

/**
 * Compress for upload. Prefer ImageManipulator (same loader family as camera crop)
 * over FileSystem.getInfoAsync — that exists-check was rejecting paths Queue still
 * displays via expo-image, which produced "Photo 1 is missing" on every lot.
 */
async function compressForUpload(uri: string): Promise<string | null> {
  for (const candidate of uriCandidates(uri)) {
    try {
      const manipulated = await ImageManipulator.manipulateAsync(
        normalizeFileUri(candidate) || candidate,
        [{ resize: { width: UPLOAD_MAX_EDGE_PX } }],
        {
          compress: UPLOAD_JPEG_QUALITY,
          format: ImageManipulator.SaveFormat.JPEG
        }
      );
      if (manipulated.uri) return manipulated.uri;
    } catch {
      // try next URI form
    }
  }
  return null;
}

/**
 * Prepare queue photos for multipart upload.
 * Never gates on getInfoAsync — if the image opens for compress, upload it;
 * otherwise fall back to the original file URI (FormData / expo-image can still
 * read paths getInfoAsync reports as missing).
 * Does not delete local draft rows; Sync decides how to report failures.
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

    // Last resort: send the same URI Queue/expo-image uses. Upload may still succeed
    // even when getInfoAsync / manipulateAsync disagreed.
    const fallback = normalizeFileUri(photo.uri);
    if (fallback) {
      out.push({ uri: fallback, name });
      continue;
    }

    skippedIds.push(photo.id);
  }

  return {
    photos: out,
    skippedIds,
    skipped: skippedIds.length
  };
}

export function chunkPhotos<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

/**
 * Per-chunk HTTP timeout. Build 11 used ~160s for 4 files and aborted healthy
 * uploads; web dashboard allows up to 15 minutes for the same endpoint.
 */
export function uploadTimeoutMs(fileCount: number): number {
  const n = Math.max(1, fileCount);
  return Math.min(900_000, 180_000 + n * 120_000);
}
