import * as FileSystem from "expo-file-system/legacy";

import { maybeCropToAspect } from "../cameraAspect";
import { CACHE_SWEEP_DIRS, planCacheCleanup, type CacheFile } from "../cacheCleanup";
import { SerialQueue } from "../serialQueue";
import {
  decodeProcessRatio,
  processCapture,
  type CaptureFileOps,
  type CaptureResult
} from "./captureProcessing";
import {
  finishPhotoProcessing,
  isPhotoPendingProcess,
  listAllPhotoUris,
  listPhotosPendingProcess,
  sweepPreparedDirs
} from "./db";

/** One queue for every full-resolution re-encode (capture screen and Sync share it). */
export const captureQueue = new SerialQueue();

export const captureFileOps: CaptureFileOps = {
  async bake(uri, ratio, size) {
    return maybeCropToAspect(uri, size?.width, size?.height, ratio);
  },
  async copy(from, to) {
    await FileSystem.copyAsync({ from, to });
  },
  async remove(uri) {
    await FileSystem.deleteAsync(uri, { idempotent: true });
  }
};

/** Move the camera's temp JPEG into Documents (rename when possible — fast and atomic). */
export async function moveCaptureIntoPlace(from: string, to: string): Promise<void> {
  const dir = to.slice(0, to.lastIndexOf("/") + 1);
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
  try {
    await FileSystem.moveAsync({ from, to });
  } catch {
    await FileSystem.copyAsync({ from, to });
    await FileSystem.deleteAsync(from, { idempotent: true }).catch(() => undefined);
  }
}

/** Queue a saved draft photo for orientation/crop processing. */
export function queueDraftPhotoProcessing(
  photoId: string,
  rawUri: string,
  ratio: number | null,
  size?: { width?: number; height?: number }
): Promise<CaptureResult> {
  return captureQueue.enqueue(() =>
    processCapture(
      {
        rawUri,
        ratio,
        size,
        isPending: () => isPhotoPendingProcess(photoId, rawUri),
        commit: (newUri) => finishPhotoProcessing(photoId, rawUri, newUri)
      },
      captureFileOps
    )
  );
}

/**
 * Finish any raw captures left by a killed capture session (or still queued). Sync calls
 * this before reading a lot's photos for upload.
 */
export async function processPendingCaptures(draftId: string): Promise<number> {
  const rows = await listPhotosPendingProcess(draftId);
  let done = 0;
  for (const row of rows) {
    const ratio = decodeProcessRatio(row.process_ratio);
    if (ratio === undefined) continue;
    const result = await queueDraftPhotoProcessing(row.id, row.file_uri, ratio);
    if (result.status !== "gone") done += 1;
  }
  await captureQueue.idle();
  return done;
}

async function listCacheFiles(): Promise<CacheFile[]> {
  const root = FileSystem.cacheDirectory;
  if (!root) return [];
  const out: CacheFile[] = [];
  for (const sub of CACHE_SWEEP_DIRS) {
    const dir = `${root}${sub}`;
    let names: string[] = [];
    try {
      names = await FileSystem.readDirectoryAsync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      const uri = `${dir}${name}`;
      try {
        const info = await FileSystem.getInfoAsync(uri);
        if (!info.exists || info.isDirectory) continue;
        const mtime = "modificationTime" in info ? info.modificationTime : undefined;
        out.push({ uri, modifiedMs: typeof mtime === "number" ? mtime * 1000 : null });
      } catch {
        /* skip unreadable entries */
      }
    }
  }
  return out;
}

/**
 * Delete stale scratch JPEGs, and upload-ready copies of lots that are gone or archived.
 * Skipped while capture processing is running.
 */
export async function sweepImageCache(now = Date.now()): Promise<number> {
  if (captureQueue.size > 0) return 0;
  await sweepPreparedDirs().catch(() => 0);
  const [files, referenced] = await Promise.all([listCacheFiles(), listAllPhotoUris()]);
  const doomed = planCacheCleanup({ files, referenced, now });
  let removed = 0;
  for (const uri of doomed) {
    try {
      await FileSystem.deleteAsync(uri, { idempotent: true });
      removed += 1;
    } catch {
      /* in use or already gone */
    }
  }
  return removed;
}
