import * as FileSystem from "expo-file-system/legacy";
import * as ImageManipulator from "expo-image-manipulator";

/** Keep multipart uploads small enough for field Wi‑Fi / cellular. */
export const UPLOAD_MAX_EDGE_PX = 2048;
export const UPLOAD_JPEG_QUALITY = 0.82;
/** Photos per `/jobs/upload` or `/upload-files` request. */
export const UPLOAD_CHUNK_SIZE = 4;

export type UploadPhotoUri = { uri: string; name: string };

/**
 * Verify local files exist and compress for upload.
 * Returns upload-ready JPEG URIs (may be temp files under cache).
 */
export async function preparePhotosForUpload(
  photos: UploadPhotoUri[]
): Promise<UploadPhotoUri[]> {
  const out: UploadPhotoUri[] = [];
  for (let i = 0; i < photos.length; i++) {
    const photo = photos[i]!;
    const info = await FileSystem.getInfoAsync(photo.uri);
    if (!info.exists) {
      throw new Error(`Photo ${i + 1} is missing on this phone — re-snap that lot.`);
    }
    const manipulated = await ImageManipulator.manipulateAsync(
      photo.uri,
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
  }
  return out;
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
