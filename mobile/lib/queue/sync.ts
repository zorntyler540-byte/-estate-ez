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
 * Compresses + chunks photos so Sync cannot hang forever on a giant multipart.
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
  for (const draft of drafts) {
    if (draft.status === "uploading") {
      await setDraftStatus(draft.id, "queued", null);
    }
  }
  onProgress?.();

  const pending = (await listLotDrafts()).filter(
    (d) => d.status === "queued" || d.status === "failed"
  );

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

      const prepared = await preparePhotosForUpload(
        photos.map((p, i) => ({
          id: p.id,
          uri: p.file_uri,
          name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
        }))
      );

      if (!prepared.photos.length) {
        throw new Error(
          "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
        );
      }

      const chunks = chunkPhotos(prepared.photos, UPLOAD_CHUNK_SIZE);
      const first = chunks[0]!;
      const job = await uploadSingleLotJob({
        saleId: draft.sale_id,
        token: authToken,
        manualSku: draft.manual_sku ?? undefined,
        listingChannel: draft.listing_channel === "facebook" ? "facebook" : "ctseller",
        photoUris: first
      });
      onProgress?.();

      for (let c = 1; c < chunks.length; c++) {
        await appendFilesToQueuedJob(authToken, job.id, chunks[c]!);
        onProgress?.();
      }

      await runJob(job.id, authToken);
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
