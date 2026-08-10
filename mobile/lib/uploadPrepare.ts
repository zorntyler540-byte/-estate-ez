import * as FileSystem from "expo-file-system/legacy";
import * as ImageManipulator from "expo-image-manipulator";

/** Keep multipart uploads small enough for field Wi‑Fi / cellular. */
export const UPLOAD_MAX_EDGE_PX = 2048;
export const UPLOAD_JPEG_QUALITY = 0.82;
/** Photos per `/jobs/upload` or `/upload-files` request. */
export const UPLOAD_CHUNK_SIZE = 4;

export type UploadPhotoUri = { uri: string; name: string };

export type PreparePhotosResult = {
  photos: UploadPhotoUri[];
  /** Local lot_photos.id values whose files were missing on disk. */
  skippedIds: string[];
  /** Original file_uri values for skipped rows (for DB cleanup by URI). */
  missingUris: string[];
  skipped: number;
};

function uriCandidates(uri: string): string[] {
  const raw = (uri || "").trim();
  if (!raw) return [];
  const out: string[] = [];
  const add = (u: string) => {
    if (u && !out.includes(u)) out.push(u);
  };
  add(raw);
  if (raw.startsWith("file://")) {
    add(raw.replace(/^file:\/\//, ""));
  } else if (raw.startsWith("/")) {
    add(`file://${raw}`);
  }
  return out;
}

/** Return a readable file:// URI, or null if the photo file is gone. */
export async function resolveQueuePhotoUri(uri: string): Promise<string | null> {
  for (const candidate of uriCandidates(uri)) {
    try {
      const info = await FileSystem.getInfoAsync(candidate);
      if (info.exists && !("isDirectory" in info && info.isDirectory)) {
        return candidate.startsWith("file://") ? candidate : `file://${candidate}`;
      }
    } catch {
      // try next form
    }
  }

  // Last chance: ImageManipulator can sometimes open URIs getInfoAsync rejects.
  try {
    const tryUri = uri.startsWith("file://") ? uri : `file://${uri}`;
    const result = await ImageManipulator.manipulateAsync(
      tryUri,
      [{ resize: { width: UPLOAD_MAX_EDGE_PX } }],
      {
        compress: UPLOAD_JPEG_QUALITY,
        format: ImageManipulator.SaveFormat.JPEG
      }
    );
    return result.uri;
  } catch {
    return null;
  }
}

/**
 * Verify local files exist and compress for upload.
 * Skips missing files instead of failing the whole lot (Build 9 threw
 * "Photo 1 is missing…" when any single frame was gone).
 */
export async function preparePhotosForUpload(
  photos: { id: string; uri: string; name: string }[]
): Promise<PreparePhotosResult> {
  const out: UploadPhotoUri[] = [];
  const skippedIds: string[] = [];
  const missingUris: string[] = [];

  for (const photo of photos) {
    const resolved = await resolveQueuePhotoUri(photo.uri);
    if (!resolved) {
      skippedIds.push(photo.id);
      missingUris.push(photo.uri);
      continue;
    }

    try {
      const manipulated = await ImageManipulator.manipulateAsync(
        resolved,
        [{ resize: { width: UPLOAD_MAX_EDGE_PX } }],
        {
          compress: UPLOAD_JPEG_QUALITY,
          format: ImageManipulator.SaveFormat.JPEG
        }
      );
      out.push({
        uri: manipulated.uri,
        name: photo.name.replace(/\.[^.]+$/, "") + ".jpg"
      });
    } catch {
      // Compress failed but URI resolved — try uploading the original path.
      try {
        const info = await FileSystem.getInfoAsync(resolved);
        if (info.exists) {
          out.push({
            uri: resolved,
            name: photo.name.replace(/\.[^.]+$/, "") + ".jpg"
          });
          continue;
        }
      } catch {
        /* fall through */
      }
      skippedIds.push(photo.id);
      missingUris.push(photo.uri);
    }
  }

  return {
    photos: out,
    skippedIds,
    missingUris,
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

/** Per-request timeout: base + per file, capped. */
export function uploadTimeoutMs(fileCount: number): number {
  return Math.min(8 * 60_000, 40_000 + Math.max(1, fileCount) * 30_000);
}
