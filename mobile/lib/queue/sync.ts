import NetInfo from "@react-native-community/netinfo";

import {
  appendFilesToQueuedJob,
  deleteJob,
  getSaleUploadHealth,
  listCatalogedMobileDraftsDetailed,
  listJobsForSale,
  listLots,
  listSales,
  runJob,
  uploadLotVideos,
  uploadSingleLotJob
} from "../api";
import { formatUploadHealthSummary } from "../formatUploadHealth";
import { mediaKindFromName } from "../mediaKind";
import { refreshSessionIfPossible } from "../sessionBridge";
import type { Job } from "../types";
import {
  UPLOAD_CHUNK_SIZE,
  chunkPhotos,
  preparePhotosForUpload
} from "../uploadPrepare";
import { draftNeedsVideoAttach, videoUploadName } from "./attachVideos";
import {
  archiveCatalogedDraftIfReady,
  listDraftPhotos,
  listLotDrafts,
  markDraftMediaUploaded,
  setDraftServerJobId,
  setDraftServerLotId,
  setDraftStatus
} from "./db";
import {
  findUniqueSkuLot,
  mergeUniqueSkuMaps,
  normalizeArchiveSku,
  shouldArchiveCatalogedDraft,
  shouldSkipReupload,
  uniqueSkuLotMap
} from "./archiveCataloged";
import {
  decideSyncRecovery,
  findJobForDraft,
  isTransientSyncError,
  jobIsLiveOrDone
} from "./syncRecovery";

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

async function uploadPendingDraftVideos(
  token: string,
  draftId: string,
  lotId: string
): Promise<void> {
  const rows = await listDraftPhotos(draftId);
  const pending = rows.filter((p) => p.media_kind === "video" && !p.uploaded);
  for (let i = 0; i < pending.length; i++) {
    const row = pending[i]!;
    // media_kind is the source of truth — a missing suffix used to skip the clip forever.
    const name =
      mediaKindFromName(row.file_uri) === "video"
        ? videoUploadName(draftId, row.file_uri, i)
        : videoUploadName(draftId, `${row.file_uri}.mp4`, i);
    await uploadLotVideos(token, lotId, [{ uri: row.file_uri, name }]);
    await markDraftMediaUploaded(draftId, row.id);
  }
}

export async function attachPendingLotVideos(token: string): Promise<number> {
  const drafts = (await listLotDrafts()).filter((d) => !d.archived_at);
  const { bySale, allDraftLots, allSkuToLot } = await websiteArchiveIndexes(
    token,
    drafts.map((d) => d.sale_id)
  );
  let attached = 0;
  for (const draft of drafts) {
    const photos = await listDraftPhotos(draft.id);
    const hasPending = photos.some((p) => p.media_kind === "video" && !p.uploaded);
    if (!hasPending) continue;
    const cataloged = bySale.get(draft.sale_id);
    const draftKey = draft.id.toLowerCase();
    const skuLot =
      (cataloged?.ok
        ? findUniqueSkuLot(
            draft.manual_sku,
            draft.listing_channel === "facebook" ? cataloged.skuToLot : cataloged.skuToLotCtseller
          )
        : undefined) || findUniqueSkuLot(draft.manual_sku, allSkuToLot);
    const lotId =
      draft.server_lot_id ||
      (cataloged?.ok ? cataloged.lotByDraft.get(draftKey) : null) ||
      allDraftLots.get(draftKey) ||
      skuLot ||
      null;
    if (!draftNeedsVideoAttach({ hasPendingLocalVideos: hasPending, lotId })) continue;
    try {
      if (lotId && draft.server_lot_id !== lotId) {
        await setDraftServerLotId(draft.id, lotId);
      }
      await uploadPendingDraftVideos(token, draft.id, lotId!);
      attached += 1;
    } catch {
      /* retry on the next Sync — do not fail the cataloged lot */
    }
  }
  return attached;
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

type CatalogedIndex =
  | {
      ok: true;
      ids: Set<string>;
      lotByDraft: Map<string, string>;
      skuToLot: Map<string, string>;
      skuToLotCtseller: Map<string, string>;
    }
  | { ok: false };

async function catalogedDraftIndex(token: string, saleId: string): Promise<CatalogedIndex> {
  try {
    const res = await listCatalogedMobileDraftsDetailed(token, saleId);
    const lotByDraft = new Map<string, string>();
    for (const row of res.lots) {
      const draftId = String(row.draft_id || "").trim().toLowerCase();
      const lotId = String(row.lot_id || "").trim();
      if (draftId && lotId) lotByDraft.set(draftId, lotId);
    }
    let skuToLot = new Map<string, string>();
    let skuToLotCtseller = new Map<string, string>();
    try {
      const lots = await listLots(token, saleId, { sort: "sku_asc" });
      skuToLot = uniqueSkuLotMap(lots);
      skuToLotCtseller = uniqueSkuLotMap(
        lots.filter((lot) => (lot.listing_channel || "ctseller") !== "facebook")
      );
    } catch {
      skuToLot = uniqueSkuLotMap(res.lots.map((row) => ({ id: row.lot_id, sku: row.sku })));
      skuToLotCtseller = skuToLot;
    }
    return { ok: true, ids: new Set(res.draftIds), lotByDraft, skuToLot, skuToLotCtseller };
  } catch {
    // Empty is not the same as "nothing cataloged" — a failed lookup must not
    // make Sync re-upload every lot already on the website.
    return { ok: false };
  }
}

/** Draft ids / unique SKUs from every sale this phone knows, so merged or moved lots still match. */
async function websiteArchiveIndexes(
  token: string,
  saleIds: string[]
): Promise<{
  bySale: Map<string, CatalogedIndex>;
  allDraftLots: Map<string, string>;
  allSkuToLot: Map<string, string>;
}> {
  const ids = [...new Set(saleIds.filter(Boolean))];
  try {
    const sales = await listSales(token);
    for (const sale of sales) {
      if (sale.closed_at) continue;
      if (!ids.includes(sale.id)) ids.push(sale.id);
    }
  } catch {
    /* use the sale ids already on the drafts */
  }
  const bySale = new Map<string, CatalogedIndex>();
  const allDraftLots = new Map<string, string>();
  for (const saleId of ids) {
    const idx = await catalogedDraftIndex(token, saleId);
    bySale.set(saleId, idx);
    if (idx.ok) {
      for (const [draftId, lotId] of idx.lotByDraft) {
        if (!allDraftLots.has(draftId)) allDraftLots.set(draftId, lotId);
      }
    }
  }
  const allSkuToLot = mergeUniqueSkuMaps(
    [...bySale.values()]
      .filter((idx): idx is Extract<CatalogedIndex, { ok: true }> => idx.ok)
      .map((idx) => idx.skuToLotCtseller)
  );
  return { bySale, allDraftLots, allSkuToLot };
}

/** After Sync confirms catalog, strip local JPEGs. Safe to call from Queue focus. */
export async function sweepArchiveConfirmedDrafts(token: string): Promise<number> {
  const drafts = await listLotDrafts();
  const saleIds = drafts.map((d) => d.sale_id);
  const { bySale, allDraftLots, allSkuToLot } = await websiteArchiveIndexes(token, saleIds);
  let archived = 0;
  for (const draft of drafts) {
    if (draft.archived_at) continue;
    const cataloged = bySale.get(draft.sale_id);
    const lookupFailed = Boolean(cataloged && !cataloged.ok);
    const draftKey = draft.id.toLowerCase();
    const confirmed = allDraftLots.has(draftKey) || Boolean(cataloged?.ok && cataloged.ids.has(draftKey));
    const skuLot =
      (cataloged?.ok
        ? findUniqueSkuLot(
            draft.manual_sku,
            draft.listing_channel === "facebook" ? cataloged.skuToLot : cataloged.skuToLotCtseller
          )
        : undefined) || findUniqueSkuLot(draft.manual_sku, allSkuToLot);
    const uniqueSkuMatch = Boolean(skuLot);
    const hasServerLotId = Boolean(draft.server_lot_id);
    if (lookupFailed && !hasServerLotId && !confirmed && !uniqueSkuMatch) continue;
    if (
      !shouldArchiveCatalogedDraft({
        alreadyArchived: Boolean(draft.archived_at),
        status: draft.status,
        errorMessage: draft.error_message,
        confirmedOnServer: confirmed,
        hasServerLotId,
        uniqueSkuMatch
      })
    ) {
      continue;
    }
    const lotId =
      (cataloged?.ok ? cataloged.lotByDraft.get(draftKey) : null) ||
      allDraftLots.get(draftKey) ||
      skuLot ||
      draft.server_lot_id;
    if (lotId && draft.server_lot_id !== lotId) {
      await setDraftServerLotId(draft.id, lotId);
    }
    if (await archiveCatalogedDraftIfReady(draft.id)) archived += 1;
  }
  return archived;
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

  const drafts = await listLotDrafts();

  const jobsBySale = new Map<string, Job[]>();
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

  // Phase 0: if the website already has the lot, adopt it. Do not re-upload.
  const { bySale: catalogedBySale, allDraftLots } = await websiteArchiveIndexes(
    authToken,
    drafts.map((d) => d.sale_id)
  );
  for (const draft of drafts) {
    const cataloged = catalogedBySale.get(draft.sale_id);
    const draftKey = draft.id.toLowerCase();
    const sku = normalizeArchiveSku(draft.manual_sku);
    const skuLot = sku && cataloged?.ok ? cataloged.skuToLot.get(sku) : undefined;
    const lotId =
      (cataloged?.ok ? cataloged.lotByDraft.get(draftKey) : undefined) ||
      allDraftLots.get(draftKey) ||
      skuLot ||
      undefined;
    if (lotId) {
      if (draft.server_lot_id !== lotId) {
        await setDraftServerLotId(draft.id, lotId);
      }
      try {
        await uploadPendingDraftVideos(authToken, draft.id, lotId);
      } catch {
        /* leftover clips retry on the next Sync */
      }
      if (draft.status !== "uploaded" || draft.error_message || draft.server_job_id) {
        await setDraftServerJobId(draft.id, null);
        await setDraftStatus(draft.id, "uploaded", null);
        reconciled += 1;
      }
      continue;
    }
    if (cataloged && !cataloged.ok) continue;
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
      await setDraftServerJobId(draft.id, null);
      const lotId = cataloged?.ok ? cataloged.lotByDraft.get(draftKey) : undefined;
      if (lotId) {
        await setDraftServerLotId(draft.id, lotId);
        try {
          await uploadPendingDraftVideos(authToken, draft.id, lotId);
        } catch {
          /* final attach pass retries */
        }
      }
      await setDraftStatus(draft.id, "uploaded", null);
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

  await attachPendingLotVideos(authToken);
  const archivedEarly = await sweepArchiveConfirmedDrafts(authToken);
  onProgress?.();

  for (const draft of await listLotDrafts()) {
    if (draft.archived_at) continue;
    const cataloged = catalogedBySale.get(draft.sale_id);
    const confirmed = allDraftLots.has(draft.id.toLowerCase());
    const lookupFailed = Boolean(cataloged && !cataloged.ok);
    const recoveryJobs = jobsBySale.get(draft.sale_id) || (await jobsForSale(draft.sale_id)) || [];
    const matchedJob = findJobForDraft(recoveryJobs, draft.id, draft.server_job_id);
    if (
      shouldSkipReupload({
        confirmedOnServer: confirmed,
        catalogLookupFailed: lookupFailed,
        hasServerLotId: Boolean(draft.server_lot_id),
        matchingJobLiveOrDone: jobIsLiveOrDone(matchedJob?.status)
      })
    ) {
      continue;
    }
    if (draft.status !== "uploading" && draft.status !== "failed" && draft.status !== "queued") {
      continue;
    }
    if (draft.server_job_id || matchedJob) {
      const job = matchedJob || (await fetchServerJob(authToken, draft.sale_id, draft.server_job_id!));
      if (decideSyncRecovery(draft.status, job) === "adopt") {
        if (job?.status === "completed") {
          await setDraftServerJobId(draft.id, null);
          const lotId = cataloged?.ok ? cataloged.lotByDraft.get(draft.id.toLowerCase()) : undefined;
          if (lotId) {
            await setDraftServerLotId(draft.id, lotId);
            try {
              await uploadPendingDraftVideos(authToken, draft.id, lotId);
            } catch {
              /* final attach pass retries */
            }
          }
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

  const pending = (await listLotDrafts()).filter((d) => {
    if (d.archived_at || d.status !== "queued") return false;
    const cataloged = catalogedBySale.get(d.sale_id);
    const confirmed = allDraftLots.has(d.id.toLowerCase());
    const lookupFailed = Boolean(cataloged && !cataloged.ok);
    const liveJob = findJobForDraft(jobsBySale.get(d.sale_id) || [], d.id, d.server_job_id);
    return !shouldSkipReupload({
      confirmedOnServer: confirmed,
      catalogLookupFailed: lookupFailed,
      hasServerLotId: Boolean(d.server_lot_id),
      matchingJobLiveOrDone: jobIsLiveOrDone(liveJob?.status)
    });
  });

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
        name: `mobile-${draft.id}-${String(i + 1).padStart(2, "0")}.jpg`,
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
        const lotId = idx.ok ? idx.lotByDraft.get(draft.id.toLowerCase()) : undefined;
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

  await attachPendingLotVideos(authToken);
  const archived = archivedEarly + (await sweepArchiveConfirmedDrafts(authToken));

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
