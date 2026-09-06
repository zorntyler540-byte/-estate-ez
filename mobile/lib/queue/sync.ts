import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  deleteJob,
  listCatalogedMobileDraftsDetailed,
  listJobsForSale,
  runJob,
  uploadLotVideos,
  uploadSingleLotJob
} from "../api";
import { mediaKindFromName } from "../mediaKind";
import { refreshSessionIfPossible } from "../sessionBridge";
import type { Job } from "../types";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import {
  listDraftPhotos,
  listLotDrafts,
  markDraftMediaUploaded,
  setDraftServerJobId,
  setDraftServerLotId,
  setDraftStatus
} from "./db";
import { decideSyncRecovery, isTransientSyncError, jobIsLiveOrDone } from "./syncRecovery";

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

function videoUploadName(draftId: string, fileUri: string, index: number): string {
  const match = fileUri.toLowerCase().match(/\.(mp4|mov|m4v|webm)(?:\?|$)/);
  const ext = match?.[1] ? `.${match[1]}` : ".mp4";
  return `mobile-${draftId}-vid-${String(index + 1).padStart(2, "0")}${ext}`;
}

async function uploadPendingDraftVideos(
  token: string,
  draftId: string,
  lotId: string
): Promise<void> {
  const rows = await listDraftPhotos(draftId);
  const pending = rows.filter((p) => p.media_kind === "video" && !p.uploaded);
  for (let i = 0; i < pending.length; i++) {
    const row = pending[i]!;
    if (mediaKindFromName(row.file_uri) !== "video") continue;
    await uploadLotVideos(token, lotId, [
      { uri: row.file_uri, name: videoUploadName(draftId, row.file_uri, i) }
    ]);
    await markDraftMediaUploaded(draftId, row.id);
  }
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

async function fetchServerJobChecked(
  token: string,
  saleId: string,
  jobId: string
): Promise<{ ok: true; job: Job | null } | { ok: false }> {
  try {
    const jobs = await listJobsForSale(token, saleId);
    return { ok: true, job: jobs.find((j) => j.id === jobId) ?? null };
  } catch {
    return { ok: false };
  }
}

async function catalogedDraftIndex(
  token: string,
  saleId: string
): Promise<{ ids: Set<string>; lotByDraft: Map<string, string> }> {
  try {
    const res = await listCatalogedMobileDraftsDetailed(token, saleId);
    const lotByDraft = new Map<string, string>();
    for (const row of res.lots) {
      const draftId = String(row.draft_id || "").trim().toLowerCase();
      const lotId = String(row.lot_id || "").trim();
      if (draftId && lotId) lotByDraft.set(draftId, lotId);
    }
    return { ids: new Set(res.draftIds), lotByDraft };
  } catch {
    return { ids: new Set(), lotByDraft: new Map() };
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
): Promise<{ uploaded: number; failed: number; reconciled: number }> {
  const net = await NetInfo.fetch();
  if (!net.isConnected) {
    return { uploaded: 0, failed: 0, reconciled: 0 };
  }

  let uploaded = 0;
  let failed = 0;
  let reconciled = 0;
  let authToken = token;
  const refreshed = await refreshSessionIfPossible();
  if (refreshed) authToken = refreshed;

  const drafts = await listLotDrafts();

  // Phase 0: if the website already has the lot, clear fake "failed / didn't finish" rows.
  // The phone often timed out on a full DB pool after /run while the worker still cataloged.
  const catalogedBySale = new Map<string, { ids: Set<string>; lotByDraft: Map<string, string> }>();
  for (const draft of drafts) {
    if (!catalogedBySale.has(draft.sale_id)) {
      catalogedBySale.set(draft.sale_id, await catalogedDraftIndex(authToken, draft.sale_id));
    }
    const cataloged = catalogedBySale.get(draft.sale_id);
    const draftKey = draft.id.toLowerCase();
    if (cataloged?.ids.has(draftKey)) {
      const lotId = cataloged.lotByDraft.get(draftKey);
      if (lotId && draft.server_lot_id !== lotId) {
        await setDraftServerLotId(draft.id, lotId);
      }
      if (lotId) {
        try {
          await uploadPendingDraftVideos(authToken, draft.id, lotId);
        } catch {
          /* photos already cataloged — retry clips on the next sync */
        }
      }
      if (draft.status !== "uploaded" || draft.error_message || draft.server_job_id) {
        await setDraftServerJobId(draft.id, null);
        await setDraftStatus(draft.id, "uploaded", null);
        reconciled += 1;
      }
      continue;
    }
    if (!draft.server_job_id) continue;
    if (
      draft.status !== "uploaded" &&
      draft.status !== "failed" &&
      draft.status !== "uploading" &&
      draft.status !== "queued"
    ) {
      continue;
    }
    const looked = await fetchServerJobChecked(authToken, draft.sale_id, draft.server_job_id);
    if (!looked.ok) continue;
    const job = looked.job;
    if (!job) {
      await setDraftServerJobId(draft.id, null);
    } else if (job.status === "completed") {
      await setDraftServerJobId(draft.id, null);
      await setDraftStatus(draft.id, "uploaded", null);
      reconciled += 1;
    } else if (job.status === "failed") {
      await setDraftStatus(draft.id, "failed", serverFailMessage(job));
    } else if (jobIsLiveOrDone(job.status)) {
      await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
    }
  }
  onProgress?.();

  for (const draft of await listLotDrafts()) {
    const cataloged = catalogedBySale.get(draft.sale_id);
    if (cataloged?.ids.has(draft.id.toLowerCase())) {
      continue;
    }
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
    const images = photos.filter((p) => p.media_kind !== "video");
    if (!images.length) {
      if (draft.server_lot_id) {
        try {
          await uploadPendingDraftVideos(authToken, draft.id, draft.server_lot_id);
          await setDraftStatus(draft.id, "uploaded", null);
          uploaded += 1;
        } catch (e) {
          await setDraftStatus(
            draft.id,
            "failed",
            e instanceof Error ? e.message : "Could not upload videos"
          );
          failed += 1;
        }
        onProgress?.();
        continue;
      }
      await setDraftStatus(
        draft.id,
        "failed",
        "Snap at least one photo — videos attach after the lot is cataloged."
      );
      failed += 1;
      onProgress?.();
      continue;
    }

    const total = images.length;
    let jobId: string | null = null;
    let uploadedCount = 0;

    try {
      await setDraftStatus(draft.id, "uploading", `Preparing photos (0/${total})…`);
      onProgress?.();

      const rows = images.map((p, i) => ({
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
        const idx = await catalogedDraftIndex(authToken, draft.sale_id);
        const lotId = idx.lotByDraft.get(draft.id.toLowerCase());
        if (lotId) {
          await setDraftServerLotId(draft.id, lotId);
          try {
            await uploadPendingDraftVideos(authToken, draft.id, lotId);
          } catch {
            /* clip upload retries on the next sync */
          }
        }
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
      // If we cannot read job status (website busy / timeout), do not delete and do not
      // mark failed — the worker may still be cataloging.
      if (jobId) {
        const looked = await fetchServerJobChecked(authToken, draft.sale_id, jobId);
        if (!looked.ok || (looked.job && jobIsLiveOrDone(looked.job.status))) {
          await setDraftStatus(
            draft.id,
            "uploaded",
            looked.ok ? "On server — cataloging…" : "On server — check the website"
          );
          onProgress?.();
          uploaded += 1;
          continue;
        }
        const existing = looked.job;
        if (!existing || existing.status === "receiving" || existing.status === "failed") {
          try {
            await deleteJob(jobId, authToken);
          } catch {
            /* leave orphan rather than mask the real error — server stale reconcile will fail it */
          }
        }
      }
      await setDraftServerJobId(draft.id, null);

      if (isTransientSyncError(msg)) {
        await setDraftStatus(
          draft.id,
          "failed",
          "Website was busy — tap Sync to confirm. Do not Resend if the lot is already online."
        );
      } else {
        await setDraftStatus(draft.id, "failed", msg);
      }
      failed += 1;
      onProgress?.();
    }
  }

  return { uploaded, failed, reconciled };
}
