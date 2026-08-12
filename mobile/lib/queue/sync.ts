import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  deleteJob,
  runJob,
  uploadSingleLotJob
} from "../api";
import { refreshSessionIfPossible } from "../sessionBridge";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import { listDraftPhotos, listLotDrafts, setDraftServerJobId, setDraftStatus } from "./db";

/**
 * Upload queued/failed lot drafts when online.
 * Also recovers lots stuck in "uploading" (app killed mid-sync) by re-queuing them.
 * Persists ``server_job_id`` so a killed Sync can delete the orphan ``receiving`` job
 * before retrying the full lot (avoids dashboard jobs stuck forever mid-upload).
 * One photo per request; catalog ``run`` only after the full lot is on the server.
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

  for (const draft of drafts) {
    if (draft.status === "uploading" || draft.status === "failed" || draft.status === "queued") {
      // Drop any orphan receiving job from a previous attempt before retrying.
      if (draft.server_job_id) {
        try {
          await deleteJob(draft.server_job_id, authToken);
        } catch {
          /* job may already be gone / failed-reconciled */
        }
        await setDraftServerJobId(draft.id, null);
      }
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

    const total = photos.length;
    let jobId: string | null = null;
    let uploadedCount = 0;

    try {
      await setDraftStatus(draft.id, "uploading", `Preparing photos (0/${total})…`);
      onProgress?.();

      const rows = photos.map((p, i) => ({
        id: p.id,
        uri: p.file_uri,
        name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
      }));
      const rowChunks = chunkPhotos(rows, UPLOAD_CHUNK_SIZE);

      for (let c = 0; c < rowChunks.length; c++) {
        const chunk = rowChunks[c]!;
        const from = uploadedCount + 1;
        await setDraftStatus(
          draft.id,
          "uploading",
          `Preparing photo ${from} of ${total}…`
        );
        onProgress?.();

        const prepared = await preparePhotosForUpload(chunk);
        if (!prepared.photos.length) {
          if (uploadedCount === 0) {
            throw new Error(
              "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
            );
          }
          break;
        }

        await setDraftStatus(
          draft.id,
          "uploading",
          `Sending photo ${from} of ${total}…`
        );
        onProgress?.();

        if (!jobId) {
          const job = await uploadSingleLotJob({
            saleId: draft.sale_id,
            token: authToken,
            manualSku: draft.manual_sku ?? undefined,
            listingChannel: draft.listing_channel === "facebook" ? "facebook" : "ctseller",
            photoUris: prepared.photos
          });
          jobId = job.id;
          await setDraftServerJobId(draft.id, jobId);
        } else {
          await appendFilesToQueuedJob(authToken, jobId, prepared.photos);
        }
        uploadedCount += prepared.photos.length;
        await setDraftStatus(
          draft.id,
          "uploading",
          `On server ${uploadedCount}/${total} — Jobs may show receiving until catalog starts`
        );
        onProgress?.();
      }

      if (!jobId || uploadedCount === 0) {
        throw new Error(
          "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
        );
      }

      await setDraftStatus(
        draft.id,
        "uploading",
        `Starting catalog (${uploadedCount} photos) — Lots appear when AI finishes`
      );
      onProgress?.();

      await runJob(jobId, authToken);
      await setDraftServerJobId(draft.id, null);
      await setDraftStatus(draft.id, "uploaded", null);
      uploaded += 1;
      onProgress?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";

      // Never start catalog with a partial lot — that was creating 1–2 photo listings.
      // Delete the receiving job so Sync can retry the full lot cleanly.
      if (jobId) {
        try {
          await deleteJob(jobId, authToken);
        } catch {
          /* leave orphan rather than mask the real error — server stale reconcile will fail it */
        }
      }
      await setDraftServerJobId(draft.id, null);

      await setDraftStatus(draft.id, "failed", msg);
      failed += 1;
      onProgress?.();
    }
  }

  return { uploaded, failed };
}
