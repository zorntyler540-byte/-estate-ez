import NetInfo from "@react-native-community/netinfo";

import { runJob, uploadSingleLotJob } from "../api";
import {
  listDraftPhotos,
  listLotDrafts,
  recoverInterruptedUploads,
  setDraftServerJobId,
  setDraftStatus
} from "./db";

function isAlreadyHandedOffError(e: unknown): boolean {
  return e instanceof Error && e.message.includes("Job must be queued or failed to run");
}

async function startOrResumeServerJob(jobId: string, token: string): Promise<void> {
  try {
    await runJob(jobId, token);
  } catch (e) {
    // The backend returns this once a previously submitted job is already processing or completed.
    // At that point the mobile draft must not upload a second copy of the same photos.
    if (isAlreadyHandedOffError(e)) return;
    throw e;
  }
}

/** Upload queued/failed lot drafts when online. Returns counts for UI feedback. */
export async function syncPendingLots(token: string): Promise<{ uploaded: number; failed: number }> {
  const net = await NetInfo.fetch();
  if (!net.isConnected) {
    return { uploaded: 0, failed: 0 };
  }

  let uploaded = 0;
  let failed = 0;
  await recoverInterruptedUploads();
  const drafts = await listLotDrafts();
  const pending = drafts.filter((d) => d.status === "queued" || d.status === "failed");

  for (const draft of pending) {
    const photos = await listDraftPhotos(draft.id);
    if (!photos.length) {
      await setDraftStatus(draft.id, "failed", "No photos in lot");
      failed += 1;
      continue;
    }

    try {
      await setDraftStatus(draft.id, "uploading", null);
      let serverJobId = draft.server_job_id;
      if (!serverJobId) {
        const job = await uploadSingleLotJob({
          saleId: draft.sale_id,
          token,
          manualSku: draft.manual_sku ?? undefined,
          photoUris: photos.map((p, i) => ({
            uri: p.file_uri,
            name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`
          }))
        });
        serverJobId = job.id;
        await setDraftServerJobId(draft.id, serverJobId);
      }
      await startOrResumeServerJob(serverJobId, token);
      await setDraftStatus(draft.id, "uploaded", null);
      uploaded += 1;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      await setDraftStatus(draft.id, "failed", msg);
      failed += 1;
    }
  }

  return { uploaded, failed };
}
