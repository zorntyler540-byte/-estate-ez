import NetInfo from "@react-native-community/netinfo";

import { runJob, uploadSingleLotJob } from "../api";
import { listDraftPhotos, listLotDrafts, setDraftServerJobId, setDraftStatus } from "./db";

/** Upload queued/failed lot drafts when online. Returns counts for UI feedback. */
export async function syncPendingLots(token: string): Promise<{ uploaded: number; failed: number }> {
  const net = await NetInfo.fetch();
  if (!net.isConnected) {
    return { uploaded: 0, failed: 0 };
  }

  let uploaded = 0;
  let failed = 0;
  const drafts = await listLotDrafts();
  const pending = drafts.filter((d) => d.status === "queued" || d.status === "failed");

  for (const draft of pending) {
    const photos = await listDraftPhotos(draft.id);
    if (!photos.length) {
      await setDraftStatus(draft.id, "failed", "No photos in lot");
      failed += 1;
      continue;
    }

    let jobId = draft.server_job_id;
    try {
      await setDraftStatus(draft.id, "uploading", null);
      if (!jobId) {
        const job = await uploadSingleLotJob({
          saleId: draft.sale_id,
          token,
          manualSku: draft.manual_sku ?? undefined,
          photoUris: photos.map((p, i) => ({
            uri: p.file_uri,
            name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
          }))
        });
        jobId = job.id;
        await setDraftServerJobId(draft.id, jobId);
      }
      await runJob(jobId, token);
      await setDraftStatus(draft.id, "uploaded", null);
      uploaded += 1;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      if (jobId && (msg.includes("Job must be queued or failed") || msg.includes("already running"))) {
        await setDraftStatus(draft.id, "uploaded", null);
        uploaded += 1;
        continue;
      }
      await setDraftStatus(draft.id, "failed", msg);
      failed += 1;
    }
  }

  return { uploaded, failed };
}
