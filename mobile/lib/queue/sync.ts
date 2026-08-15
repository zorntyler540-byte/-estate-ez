import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  deleteJob,
  listJobsForSale,
  runJob,
  uploadSingleLotJob
} from "../api";
import { refreshSessionIfPossible } from "../sessionBridge";
import type { Job } from "../types";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import { listDraftPhotos, listLotDrafts, setDraftServerJobId, setDraftStatus } from "./db";
import { decideSyncRecovery, jobIsLiveOrDone } from "./syncRecovery";

/**
 * After ``/run`` is accepted we confirm the server actually catalogs the lot instead of
 * trusting the HTTP 200 (a dedicated worker can still fail or stall). We poll briefly; lots
 * that are still processing are reconciled on a later sync (Phase 0 below).
 */
const SERVER_CONFIRM_TIMEOUT_MS = 45_000;
const SERVER_CONFIRM_INTERVAL_MS = 3_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function serverFailMessage(job: Job): string {
  return (job.error_message || "").trim() || "Server could not process this lot — tap Sync now to retry.";
}

async function fetchServerJob(
  token: string,
  saleId: string,
  jobId: string
): Promise<Job | null> {
  try {
    const jobs = await listJobsForSale(token, saleId);
    return jobs.find((j) => j.id === jobId) ?? null;
  } catch {
    return null;
  }
}

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

  // Phase 0: reconcile lots we already sent but had not confirmed cataloged (e.g. the confirm
  // window timed out, or the app was closed). Turns a silent "sent" into the real server outcome.
  for (const draft of drafts) {
    if (draft.status === "uploaded" && draft.server_job_id) {
      const job = await fetchServerJob(authToken, draft.sale_id, draft.server_job_id);
      if (!job) {
        // Job is gone (deleted/expired) — we can no longer confirm; stop tracking it.
        await setDraftServerJobId(draft.id, null);
      } else if (job.status === "completed") {
        await setDraftServerJobId(draft.id, null);
      } else if (job.status === "failed") {
        // Keep server_job_id so the recovery pass on the next sync deletes it before retrying.
        await setDraftStatus(draft.id, "failed", serverFailMessage(job));
      }
      // else still receiving/queued/processing: leave as-is and check again next sync.
    }
  }
  onProgress?.();

  for (const draft of drafts) {
    if (draft.status !== "uploading" && draft.status !== "failed" && draft.status !== "queued") {
      continue;
    }
    if (draft.server_job_id) {
      const job = await fetchServerJob(authToken, draft.sale_id, draft.server_job_id);
      if (decideSyncRecovery(draft.status, job) === "adopt") {
        if (job?.status === "completed") {
          await setDraftServerJobId(draft.id, null);
          await setDraftStatus(draft.id, "uploaded", null);
        } else {
          await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
        }
        continue;
      }
      // Only delete a job that never cataloged (receiving) or already failed. Never delete
      // queued/processing — that left the first AI run alive and started a second one.
      if (job && (job.status === "receiving" || job.status === "failed")) {
        try {
          await deleteJob(draft.server_job_id, authToken);
        } catch {
          /* job may already be gone */
        }
      }
      await setDraftServerJobId(draft.id, null);
    }
    if (draft.status === "uploading" || draft.status === "failed") {
      await setDraftStatus(draft.id, "queued", null);
    } else if (draft.error_message) {
      await setDraftStatus(draft.id, "queued", null);
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
      // Mark sent as soon as catalog is queued. Staying on "uploading" until the 45s
      // confirm window ended is what made a killed Sync start a second AI job.
      await setDraftStatus(draft.id, "uploaded", "On server — cataloging…");
      onProgress?.();

      // Confirm the server actually catalogs it (don't trust the /run 200 alone).
      const deadline = Date.now() + SERVER_CONFIRM_TIMEOUT_MS;
      let outcome: "completed" | "failed" | "pending" = "pending";
      let serverError: string | null = null;
      while (Date.now() < deadline) {
        const job = await fetchServerJob(authToken, draft.sale_id, jobId);
        if (job?.status === "completed") {
          outcome = "completed";
          break;
        }
        if (job?.status === "failed") {
          outcome = "failed";
          serverError = serverFailMessage(job);
          break;
        }
        await sleep(SERVER_CONFIRM_INTERVAL_MS);
      }

      if (outcome === "failed") {
        // Keep server_job_id so the next sync's recovery pass clears the failed job before retrying.
        await setDraftStatus(draft.id, "failed", serverError);
        failed += 1;
      } else if (outcome === "completed") {
        await setDraftServerJobId(draft.id, null);
        await setDraftStatus(draft.id, "uploaded", null);
        uploaded += 1;
      } else {
        await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
        uploaded += 1;
      }
      onProgress?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Upload failed";

      // Only delete a receiving/failed job. If /run already queued catalog, keep that job
      // and treat the lot as sent — deleting it (or ignoring a 409) used to start a duplicate.
      if (jobId) {
        const existing = await fetchServerJob(authToken, draft.sale_id, jobId);
        if (existing && jobIsLiveOrDone(existing.status)) {
          await setDraftStatus(draft.id, "uploaded", "On server — cataloging…");
          onProgress?.();
          uploaded += 1;
          continue;
        }
        if (!existing || existing.status === "receiving" || existing.status === "failed") {
          try {
            await deleteJob(jobId, authToken);
          } catch {
            /* leave orphan rather than mask the real error — server stale reconcile will fail it */
          }
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
