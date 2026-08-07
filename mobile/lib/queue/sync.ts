import NetInfo from "@react-native-community/netinfo";

import { runJob, uploadSingleLotJob } from "../api";
import { listDraftPhotos, listLotDrafts, setDraftStatus } from "./db";

/**
 * Upload queued/failed lot drafts when online.
 * Also recovers lots stuck in "uploading" (app killed mid-sync) by re-queuing them.
 * ``onProgress`` runs after each status change so Queue can refresh mid-sync.
 * Returns counts for UI feedback.
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
      const job = await uploadSingleLotJob({
        saleId: draft.sale_id,
        token,
        manualSku: draft.manual_sku ?? undefined,
        listingChannel: draft.listing_channel === "facebook" ? "facebook" : "ctseller",
        photoUris: photos.map((p, i) => ({
          uri: p.file_uri,
          name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
        }))
      });
      await runJob(job.id, token);
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
