import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  runJob,
  uploadSingleLotJob
} from "../api";
import { refreshSessionIfPossible } from "../sessionBridge";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import { listDraftPhotos, listLotDrafts, setDraftStatus } from "./db";

/**
 * Upload queued/failed lot drafts when online.
 * Also recovers lots stuck in "uploading" (app killed mid-sync) by re-queuing them.
 * Compresses + uploads in small chunks so Sync can make progress without a 160s abort.
 * ``onProgress`` runs after each status change so Queue can refresh mid-sync.
 */
export async function syncPendingLots(
  token: string,
  onProgress?: () => void
): Promise<{ uploaded: number; failed: number }> {
  const net = await NetInfo.fetch();
  if (!net.isConnected) {
    return { uploaded: 0, failed: 0 };
  }

  let uploaded = 0;
  let failed = 0;
  let authToken = token;
  const refreshed = await refreshSessionIfPossible();
  if (refreshed) authToken = refreshed;

  const drafts = await listLotDrafts();

  // App kill / crash mid-upload leaves status as "uploading" forever unless we reset.
  // Also clear stale failures (e.g. build 9 "Photo 1 is missing") so Queue isn't confusing.
  for (const draft of drafts) {
    if (draft.status === "uploading" || draft.status === "failed" || draft.status === "queued") {
      if (draft.status === "uploading" || draft.status === "failed") {
        await setDraftStatus(draft.id, "queued", null);
      } else if (draft.error_message) {
        await setDraftStatus(draft.id, "queued", null);
      }
    }
  }
  onProgress?.();

  const pending = (await listLotDrafts()).filter((d) => d.status === "queued");

  for (const draft of pending) {
    const photos = await listDraftPhotos(draft.id);
    if (!photos.length) {
      await setDraftStatus(draft.id, "failed", "No photos in lot");
      failed += 1;
      onProgress?.();
      continue;
    }

    try {
      await setDraftStatus(draft.id, "uploading", null);
      onProgress?.();

      const rows = photos.map((p, i) => ({
        id: p.id,
        uri: p.file_uri,
        name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
      }));
      const rowChunks = chunkPhotos(rows, UPLOAD_CHUNK_SIZE);
      let jobId: string | null = null;
      let uploadedCount = 0;

      for (let c = 0; c < rowChunks.length; c++) {
        const chunk = rowChunks[c]!;
        const prepared = await preparePhotosForUpload(chunk);
        if (!prepared.photos.length) {
          if (uploadedCount === 0) {
            throw new Error(
              "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
            );
          }
          // Later chunk empty — stop appending; run with what we have.
          break;
        }

        if (!jobId) {
          const job = await uploadSingleLotJob({
            saleId: draft.sale_id,
            token: authToken,
            manualSku: draft.manual_sku ?? undefined,
            listingChannel: draft.listing_channel === "facebook" ? "facebook" : "ctseller",
            photoUris: prepared.photos
          });
          jobId = job.id;
        } else {
          await appendFilesToQueuedJob(authToken, jobId, prepared.photos);
        }
        uploadedCount += prepared.photos.length;
        onProgress?.();
      }

      if (!jobId || uploadedCount === 0) {
        throw new Error(
          "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
        );
      }

      await runJob(jobId, authToken);
      await setDraftStatus(draft.id, "uploaded", null);
      uploaded += 1;
      onProgress?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      await setDraftStatus(draft.id, "failed", msg);
      failed += 1;
      onProgress?.();
    }
  }

  return { uploaded, failed };
}
