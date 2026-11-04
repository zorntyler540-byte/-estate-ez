import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  deleteJob,
  getMobileDraftStatuses,
  getSaleUploadHealth,
  listCatalogedMobileDraftsDetailed,
  listJobsForSale,
  listSales,
  runJob,
  uploadLotVideos,
  uploadSingleLotJob,
  type MobileDraftStatus
} from "../api";
import { apiErrorStatus, isPermanentClientError } from "../apiError";
import { formatUploadHealthSummary } from "../formatUploadHealth";
import {
  catalogPhotoFileName,
  isVideoDraftRow,
  mediaKindFromName,
  splitDraftMedia
} from "../mediaKind";
import { refreshSessionIfPossible } from "../sessionBridge";
import type { Job, LotDraft } from "../types";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import { draftNeedsVideoAttach, videoUploadName } from "./attachVideos";
import { processPendingCaptures } from "./captureFiles";
import {
  archiveCatalogedDraftIfReady,
  listDraftPhotos,
  listLotDrafts,
  markDraftMediaBlocked,
  markDraftMediaUploaded,
  setDraftServerJobId,
  setDraftServerLotId,
  setDraftStatus
} from "./db";
import { shouldArchiveCatalogedDraft, shouldSkipReupload } from "./archiveCataloged";
import {
  emptyWebsiteIndex,
  noteDraftConfirmed,
  normalizeDraftKey,
  resolveDraftOnServer,
  saleIndexFromResponse,
  setSaleIndex,
  type CatalogedSaleIndex,
  type WebsiteIndex
} from "./draftConfirm";
import {
  decideCatalogConfirm,
  decideSyncRecovery,
  findJobForDraft,
  isTransientSyncError,
  jobIsLiveOrDone
} from "./syncRecovery";

/**
 * After ``/run`` is accepted we confirm the server actually catalogs the lot instead of
 * trusting the HTTP 200 (a dedicated worker can still fail or stall). We poll briefly; lots
 * that are still processing are reconciled by the follow-up check or a later Sync.
 */
const SERVER_CONFIRM_TIMEOUT_MS = 45_000;
const SERVER_CONFIRM_INTERVAL_MS = 3_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Clips uploaded for this draft. A clip the website refuses for good is marked blocked. */
async function uploadPendingDraftVideos(
  token: string,
  draftId: string,
  lotId: string
): Promise<number> {
  const rows = await listDraftPhotos(draftId);
  const pending = rows.filter((p) => isVideoDraftRow(p) && !p.uploaded && !p.upload_blocked);
  let sent = 0;
  for (let i = 0; i < pending.length; i++) {
    const row = pending[i]!;
    // media_kind is the source of truth — a missing suffix used to skip the clip forever.
    const name =
      mediaKindFromName(row.file_uri) === "video"
        ? videoUploadName(draftId, row.file_uri, i)
        : videoUploadName(draftId, `${row.file_uri}.mp4`, i);
    try {
      await uploadLotVideos(token, lotId, [{ uri: row.file_uri, name }]);
    } catch (e) {
      if (!isPermanentClientError(e)) throw e;
      await markDraftMediaBlocked(
        draftId,
        row.id,
        e instanceof Error ? e.message : "Website refused this clip"
      );
      continue;
    }
    await markDraftMediaUploaded(draftId, row.id);
    sent += 1;
  }
  return sent;
}

async function draftHasPendingClip(draftId: string): Promise<boolean> {
  const rows = await listDraftPhotos(draftId);
  return rows.some((p) => isVideoDraftRow(p) && !p.uploaded && !p.upload_blocked);
}

/** Remember a lot id only when it came from an exact draft mapping (never a SKU). */
async function rememberServerLot(draft: LotDraft, lotId: string | null): Promise<void> {
  if (lotId && draft.server_lot_id !== lotId) {
    await setDraftServerLotId(draft.id, lotId);
    draft.server_lot_id = lotId;
  }
}

async function activeDrafts(): Promise<LotDraft[]> {
  return (await listLotDrafts()).filter((d) => !d.archived_at);
}

export async function attachPendingLotVideos(token: string, index?: WebsiteIndex): Promise<number> {
  const withClips: LotDraft[] = [];
  for (const draft of await activeDrafts()) {
    if (await draftHasPendingClip(draft.id)) withClips.push(draft);
  }
  if (!withClips.length) return 0;
  const idx = index ?? (await loadWebsiteIndex(token, withClips));
  let attached = 0;
  for (const draft of withClips) {
    const { lotId } = resolveDraftOnServer(draft, idx);
    if (!draftNeedsVideoAttach({ hasPendingLocalVideos: true, lotId })) continue;
    try {
      await rememberServerLot(draft, lotId);
      if ((await uploadPendingDraftVideos(token, draft.id, lotId!)) > 0) attached += 1;
    } catch {
      /* retry on the next Sync — do not fail the cataloged lot */
    }
  }
  return attached;
}

function serverFailMessage(job: Pick<Job, "error_message">): string {
  return (job.error_message || "").trim() || "Server could not process this lot — tap Sync now to retry.";
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

async function catalogedSaleIndex(token: string, saleId: string): Promise<CatalogedSaleIndex> {
  try {
    return saleIndexFromResponse(await listCatalogedMobileDraftsDetailed(token, saleId));
  } catch {
    // Empty is not the same as "nothing cataloged" — a failed lookup must not
    // make Sync re-upload every lot already on the website.
    return { ok: false };
  }
}

/**
 * Exact draft → lot mapping for the drafts' sales plus every open sale (moved lots still
 * match). Build once per Sync and pass it to each phase. Skipped when nothing is local.
 */
export async function loadWebsiteIndex(
  token: string,
  drafts?: Pick<LotDraft, "sale_id">[]
): Promise<WebsiteIndex> {
  const local = drafts ?? (await activeDrafts());
  const index = emptyWebsiteIndex();
  if (!local.length) return index;
  const ids = [...new Set(local.map((d) => d.sale_id).filter(Boolean))];
  try {
    const sales = await listSales(token);
    for (const sale of sales) {
      if (sale.closed_at) continue;
      if (!ids.includes(sale.id)) ids.push(sale.id);
    }
  } catch {
    /* use the sale ids already on the drafts */
  }
  for (const saleId of ids) {
    setSaleIndex(index, saleId, await catalogedSaleIndex(token, saleId));
  }
  return index;
}

/** After the website confirms this exact draft, strip local JPEGs. Safe to call from Queue focus. */
export async function sweepArchiveConfirmedDrafts(token: string, index?: WebsiteIndex): Promise<number> {
  const drafts = await activeDrafts();
  if (!drafts.length) return 0;
  const idx = index ?? (await loadWebsiteIndex(token, drafts));
  let archived = 0;
  for (const draft of drafts) {
    const state = resolveDraftOnServer(draft, idx);
    if (
      !shouldArchiveCatalogedDraft({
        alreadyArchived: Boolean(draft.archived_at),
        status: draft.status,
        errorMessage: draft.error_message,
        confirmedOnServer: state.confirmed,
        hasServerLotId: Boolean(draft.server_lot_id)
      })
    ) {
      continue;
    }
    await rememberServerLot(draft, state.lotId);
    if (await archiveCatalogedDraftIfReady(draft.id)) archived += 1;
  }
  return archived;
}

export type DraftStatusLookup =
  | { ok: true; rows: Map<string, MobileDraftStatus> }
  | { ok: false; endpointMissing: boolean; permanent: boolean };

/** Older API deploys have no ``/mobile/drafts/status``; remember that for this app run. */
let draftStatusEndpointMissing = false;

export async function lookupDraftStatuses(
  token: string,
  draftIds: string[]
): Promise<DraftStatusLookup> {
  if (draftStatusEndpointMissing) return { ok: false, endpointMissing: true, permanent: false };
  try {
    const rows = await getMobileDraftStatuses(token, draftIds);
    const byDraft = new Map<string, MobileDraftStatus>();
    for (const row of rows) byDraft.set(normalizeDraftKey(row.draft_id), row);
    return { ok: true, rows: byDraft };
  } catch (e) {
    if (apiErrorStatus(e) === 404) {
      draftStatusEndpointMissing = true;
      return { ok: false, endpointMissing: true, permanent: false };
    }
    return { ok: false, endpointMissing: false, permanent: isPermanentClientError(e) };
  }
}

type DraftJobLook =
  | { ok: true; status: string | null; error: string | null; lotId: string | null; cataloged: boolean }
  | { ok: false };

/** One draft's job state: batched status endpoint, or ``GET /jobs`` on older deploys. */
async function lookupDraftJob(
  token: string,
  draft: Pick<LotDraft, "id" | "sale_id">,
  jobId: string
): Promise<DraftJobLook> {
  const looked = await lookupDraftStatuses(token, [draft.id]);
  if (looked.ok) {
    const row = looked.rows.get(normalizeDraftKey(draft.id));
    return {
      ok: true,
      status: row?.job_status ?? null,
      error: row?.error_message ?? null,
      lotId: row?.lot_id ? String(row.lot_id) : null,
      cataloged: Boolean(row?.cataloged)
    };
  }
  if (!looked.endpointMissing) return { ok: false };
  const fallback = await fetchServerJobChecked(token, draft.sale_id, jobId);
  if (!fallback.ok) return { ok: false };
  return {
    ok: true,
    status: fallback.job?.status ?? null,
    error: fallback.job?.error_message ?? null,
    lotId: null,
    cataloged: fallback.job?.status === "completed"
  };
}

/**
 * Upload queued/failed lot drafts when online.
 * Also recovers lots stuck in "uploading" (app killed mid-sync) by re-queuing them.
 * Persists ``server_job_id`` so a killed Sync can delete the orphan ``receiving`` job
 * before retrying the full lot (avoids dashboard jobs stuck forever mid-upload).
 * One photo per request; catalog ``run`` only after the full lot is on the server.
 * Pass the ``WebsiteIndex`` the caller already built so one Sync reads it once.
 */
export async function syncPendingLots(
  token: string,
  onProgress?: () => void,
  websiteIndex?: WebsiteIndex
): Promise<{
  uploaded: number;
  failed: number;
  reconciled: number;
  archived: number;
  healthSummary?: string | null;
}> {
  const net = await NetInfo.fetch();
  if (!net.isConnected) {
    return { uploaded: 0, failed: 0, reconciled: 0, archived: 0, healthSummary: null };
  }

  let uploaded = 0;
  let failed = 0;
  let reconciled = 0;
  let authToken = token;
  const refreshed = await refreshSessionIfPossible();
  if (refreshed) authToken = refreshed;

  const drafts = await activeDrafts();
  const index = websiteIndex ?? (await loadWebsiteIndex(authToken, drafts));

  const jobsBySale = new Map<string, Job[]>();
  /** ``null`` = could not read jobs. Never treat that as "no job" (that re-uploads a live lot). */
  async function jobsForSale(saleId: string): Promise<Job[] | null> {
    if (jobsBySale.has(saleId)) return jobsBySale.get(saleId)!;
    try {
      const list = await listJobsForSale(authToken, saleId);
      jobsBySale.set(saleId, list);
      return list;
    } catch {
      return null;
    }
  }

  async function adoptCompleted(draft: LotDraft): Promise<void> {
    await setDraftServerJobId(draft.id, null);
    noteDraftConfirmed(index, draft.id);
    const { lotId } = resolveDraftOnServer(draft, index);
    if (lotId) {
      await rememberServerLot(draft, lotId);
      try {
        await uploadPendingDraftVideos(authToken, draft.id, lotId);
      } catch {
        /* final attach pass retries */
      }
    }
    await setDraftStatus(draft.id, "uploaded", null);
  }

  // Phase 0: if the website already has this exact draft, adopt it. Do not re-upload.
  for (const draft of drafts) {
    const state = resolveDraftOnServer(draft, index);
    if (state.confirmed) {
      await rememberServerLot(draft, state.lotId);
      if (state.lotId) {
        try {
          await uploadPendingDraftVideos(authToken, draft.id, state.lotId);
        } catch {
          /* leftover clips retry on the next Sync */
        }
      }
      if (draft.status !== "uploaded" || draft.error_message || draft.server_job_id) {
        await setDraftServerJobId(draft.id, null);
        await setDraftStatus(draft.id, "uploaded", null);
        reconciled += 1;
      }
      continue;
    }
    if (state.lookupFailed) continue;
    if (
      draft.status !== "uploaded" &&
      draft.status !== "failed" &&
      draft.status !== "uploading" &&
      draft.status !== "queued"
    ) {
      continue;
    }
    const saleJobs = await jobsForSale(draft.sale_id);
    if (saleJobs === null) continue;
    const job = findJobForDraft(saleJobs, draft.id, draft.server_job_id);
    if (!job) {
      if (draft.server_job_id) await setDraftServerJobId(draft.id, null);
    } else if (job.status === "completed") {
      await adoptCompleted(draft);
      reconciled += 1;
    } else if (job.status === "failed") {
      if (job.id !== draft.server_job_id) {
        await setDraftServerJobId(draft.id, job.id);
      }
      await setDraftStatus(draft.id, "failed", serverFailMessage(job));
    } else if (jobIsLiveOrDone(job.status)) {
      if (job.id !== draft.server_job_id) {
        await setDraftServerJobId(draft.id, job.id);
      }
      await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
    }
  }
  onProgress?.();

  await attachPendingLotVideos(authToken, index);
  const archivedEarly = await sweepArchiveConfirmedDrafts(authToken, index);
  onProgress?.();

  for (const draft of await activeDrafts()) {
    if (draft.status !== "uploading" && draft.status !== "failed" && draft.status !== "queued") {
      continue;
    }
    const state = resolveDraftOnServer(draft, index);
    if (state.lookupFailed && !state.confirmed) continue;
    const recoveryJobs = await jobsForSale(draft.sale_id);
    if (recoveryJobs === null) continue;
    const matchedJob = findJobForDraft(recoveryJobs, draft.id, draft.server_job_id);
    if (
      shouldSkipReupload({
        confirmedOnServer: state.confirmed,
        catalogLookupFailed: state.lookupFailed,
        hasServerLotId: Boolean(draft.server_lot_id),
        matchingJobLiveOrDone: jobIsLiveOrDone(matchedJob?.status)
      })
    ) {
      continue;
    }
    if (draft.server_job_id || matchedJob) {
      let job = matchedJob;
      if (!job) {
        const looked = await fetchServerJobChecked(authToken, draft.sale_id, draft.server_job_id!);
        if (!looked.ok) continue;
        job = looked.job;
      }
      if (decideSyncRecovery(draft.status, job) === "adopt") {
        if (job?.status === "completed") {
          await adoptCompleted(draft);
        } else {
          await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
        }
        continue;
      }
      // Only delete a job that never cataloged (receiving) or already failed. Never delete
      // queued/processing — that left the first AI run alive and started a second one.
      if (job && (job.status === "receiving" || job.status === "failed")) {
        try {
          await deleteJob(job.id, authToken);
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

  const pending: LotDraft[] = [];
  for (const d of await activeDrafts()) {
    if (d.status !== "queued") continue;
    const state = resolveDraftOnServer(d, index);
    // Unknown server state (catalog list or jobs unreadable) waits for the next Sync.
    if (state.lookupFailed) continue;
    const saleJobs = await jobsForSale(d.sale_id);
    if (saleJobs === null) continue;
    const liveJob = findJobForDraft(saleJobs, d.id, d.server_job_id);
    if (
      shouldSkipReupload({
        confirmedOnServer: state.confirmed,
        catalogLookupFailed: state.lookupFailed,
        hasServerLotId: Boolean(d.server_lot_id),
        matchingJobLiveOrDone: jobIsLiveOrDone(liveJob?.status)
      })
    ) {
      continue;
    }
    pending.push(d);
  }

  for (const draft of pending) {
    // Raw shots from a killed capture session get their orientation/crop before upload.
    await processPendingCaptures(draft.id).catch(() => 0);
    const photos = await listDraftPhotos(draft.id);
    const { images } = splitDraftMedia(photos);
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
        name: catalogPhotoFileName(draft.id, i),
        capturedAtMs: p.captured_at && p.captured_at > 0 ? p.captured_at : undefined
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
            photoUris: prepared.photos,
            clientDraftId: draft.id
          });
          jobId = job.id;
          await setDraftServerJobId(draft.id, jobId);
        } else {
          await appendFilesToQueuedJob(authToken, jobId, prepared.photos, draft.id);
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
      let confirmedLotId: string | null = null;
      for (;;) {
        const look = await lookupDraftJob(authToken, draft, jobId);
        if (look.ok) {
          if (look.lotId) confirmedLotId = look.lotId;
          outcome = decideCatalogConfirm({
            jobStatus: look.cataloged ? "completed" : look.status,
            lotId: look.lotId
          });
          if (outcome === "failed") serverError = serverFailMessage({ error_message: look.error });
          if (outcome !== "pending") break;
        }
        if (Date.now() + SERVER_CONFIRM_INTERVAL_MS > deadline) break;
        await sleep(SERVER_CONFIRM_INTERVAL_MS);
      }

      if (outcome === "failed") {
        // Keep server_job_id so the next sync's recovery pass clears the failed job before retrying.
        await setDraftStatus(draft.id, "failed", serverError);
        failed += 1;
      } else if (outcome === "completed") {
        await setDraftServerJobId(draft.id, null);
        if (!confirmedLotId) {
          // Older API (no status endpoint): refresh just this sale's exact draft mapping.
          setSaleIndex(index, draft.sale_id, await catalogedSaleIndex(authToken, draft.sale_id));
        }
        noteDraftConfirmed(index, draft.id, confirmedLotId);
        const { lotId } = resolveDraftOnServer(draft, index);
        if (lotId) {
          await rememberServerLot(draft, lotId);
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

  await attachPendingLotVideos(authToken, index);
  const archived = archivedEarly + (await sweepArchiveConfirmedDrafts(authToken, index));

  const healthSaleIds = [...new Set((await listLotDrafts()).map((d) => d.sale_id).filter(Boolean))];
  let cataloging = 0;
  let stuck = 0;
  for (const saleId of healthSaleIds) {
    try {
      const health = await getSaleUploadHealth(authToken, saleId);
      cataloging += Number(health.cataloging_count) || 0;
      stuck += Number(health.stuck_count) || 0;
    } catch {
      /* health is advisory — do not fail Sync */
    }
  }
  const healthSummary = formatUploadHealthSummary({
    cataloging_count: cataloging,
    stuck_count: stuck
  });
  return { uploaded, failed, reconciled, archived, healthSummary };
}
