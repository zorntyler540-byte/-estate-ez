import NetInfo from "@react-native-community/netinfo";

import {
  appendPhotoToJob,
  createLotJobWithPhoto,
  deleteJob,
  getJobReceivedFiles,
  getMobileDraftStatuses,
  getSaleUploadHealth,
  listCatalogedMobileDraftsDetailed,
  listJobsForSale,
  listSales,
  runJob,
  uploadLotPhoto,
  uploadLotVideo,
  type MobileDraftStatus
} from "../api";
import { apiErrorStatus, isPermanentClientError } from "../apiError";
import { CTBIDS_MAX_PHOTOS_PER_LOT } from "../catalogLimits";
import { formatUploadHealthSummary } from "../formatUploadHealth";
import { isVideoDraftRow, shouldPrepareAsPhoto, splitDraftMedia } from "../mediaKind";
import { refreshSessionIfPossible } from "../sessionBridge";
import type { Job, LotDraft, LotPhotoRow } from "../types";
import {
  prepareCachedUploadJpeg,
  resolveUploadFileUri,
  type UploadPhotoUri
} from "../uploadPrepare";
import { LOTS_AT_ONCE, mapPool, runUploadPipeline, uploadConcurrencyFor } from "../uploadPool";
import { draftNeedsVideoAttach } from "./attachVideos";
import { processPendingCaptures } from "./captureFiles";
import {
  archiveCatalogedDraft,
  archiveCatalogedDraftIfReady,
  assignUploadNames,
  clearDraftRetry,
  getLotDraft,
  listDraftPhotos,
  listLotDrafts,
  markDraftMediaBlocked,
  markDraftMediaUploaded,
  preparedUploadUri,
  recordDraftFailure,
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
import { namesStillMissing, planResume } from "./resumePlan";
import { classifyUploadError, retryAllowed } from "./retryPolicy";
import { decideSyncRecovery, findJobForDraft, isTransientSyncError, jobIsLiveOrDone } from "./syncRecovery";
import { clipUploadName } from "./uploadNames";

/** Lots queued while Sync is running are picked up in a later round (bounded). */
const MAX_SYNC_ROUNDS = 5;

function errorMessage(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** Clips uploaded for this draft. A clip the website refuses for good is marked blocked. */
async function uploadPendingDraftVideos(
  token: string,
  draftId: string,
  lotId: string,
  appendSession = false
): Promise<number> {
  const rows = await assignUploadNames(draftId, appendSession);
  const pending = rows.filter((p) => isVideoDraftRow(p) && !p.uploaded && !p.upload_blocked);
  let sent = 0;
  for (let i = 0; i < pending.length; i++) {
    const row = pending[i]!;
    // Stable per-clip name: the server stores (lot, name) once, so a retry is never a second copy.
    const name =
      row.upload_name || clipUploadName(appendSession ? "append" : "mobile", draftId, i + 1, row.file_uri);
    try {
      await uploadLotVideo(token, lotId, { uri: row.file_uri, name });
    } catch (e) {
      if (!isPermanentClientError(e)) throw e;
      await markDraftMediaBlocked(draftId, row.id, errorMessage(e, "Website refused this clip"));
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

/** New-lot drafts still on the phone. Add photos sessions have their own upload path. */
async function activeDrafts(): Promise<LotDraft[]> {
  return (await listLotDrafts()).filter((d) => !d.archived_at && !d.append_lot_id);
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

type UploadItem = { id: string; uri: string; name: string; capturedAtMs?: number };

function uploadItems(rows: LotPhotoRow[]): UploadItem[] {
  return rows.map((p) => ({
    id: p.id,
    uri: p.file_uri,
    name: p.upload_name!,
    capturedAtMs: p.captured_at && p.captured_at > 0 ? p.captured_at : undefined
  }));
}

/** Compress once into the draft's prepared folder; later attempts reuse that JPEG. */
async function preparePhoto(draftId: string, item: UploadItem): Promise<UploadPhotoUri | null> {
  if (!shouldPrepareAsPhoto(item)) return null;
  const cached = await prepareCachedUploadJpeg(preparedUploadUri(draftId, item.name), item.uri);
  if (cached) return { uri: cached, name: item.name, capturedAtMs: item.capturedAtMs };
  try {
    const uri = await resolveUploadFileUri({ uri: item.uri, name: item.name });
    return { uri, name: item.name, capturedAtMs: item.capturedAtMs };
  } catch {
    return null;
  }
}

type SyncCtx = {
  token: string;
  manual: boolean;
  concurrency: number;
  index: WebsiteIndex;
  /** Lots whose ``/run`` was accepted this Sync — confirmed in one batched call at the end. */
  sent: LotDraft[];
  onProgress?: () => void;
};

type LotOutcome = "sent" | "failed" | "skipped";

async function failDraft(draftId: string, e: unknown): Promise<void> {
  const kind = classifyUploadError(e);
  const msg = errorMessage(e, "Upload failed");
  await recordDraftFailure(
    draftId,
    kind === "transient" && isTransientSyncError(msg)
      ? "Website was busy — Sync retries automatically. Do not Resend if the lot is already online."
      : msg,
    kind
  );
}

/** The server's batch for this job, or null when it is gone / unreadable on this API. */
async function receivedForJob(
  token: string,
  jobId: string
): Promise<{ status: string; names: string[]; folderMissing: boolean } | null> {
  try {
    const res = await getJobReceivedFiles(token, jobId);
    return { status: res.status, names: res.names, folderMissing: res.folder_missing };
  } catch (e) {
    if (apiErrorStatus(e) === 404) return null;
    throw e;
  }
}

async function dropJob(token: string, draftId: string, jobId: string): Promise<void> {
  try {
    await deleteJob(jobId, token);
  } catch {
    /* already gone — server stale reconcile handles the rest */
  }
  await setDraftServerJobId(draftId, null);
}

/**
 * Send one new lot: resume its ``receiving`` batch when the server still has a clean subset,
 * create the job with the first photo, then upload the rest in parallel while the next photo
 * compresses. ``/run`` only after the server reports every photo. No wait for catalog here.
 */
async function uploadNewLot(ctx: SyncCtx, queued: LotDraft): Promise<LotOutcome> {
  const draft = await getLotDraft(queued.id);
  // Staff may have reopened the lot (Add photos) since this round was planned.
  if (!draft || draft.status !== "queued" || draft.archived_at) return "skipped";
  const { token } = ctx;

  await processPendingCaptures(draft.id).catch(() => 0);
  const rows = await assignUploadNames(draft.id, false);
  const { images } = splitDraftMedia(rows);
  if (!images.length) {
    if (draft.server_lot_id) {
      try {
        await uploadPendingDraftVideos(token, draft.id, draft.server_lot_id);
        await clearDraftRetry(draft.id);
        await setDraftStatus(draft.id, "uploaded", null);
        return "sent";
      } catch (e) {
        await failDraft(draft.id, e);
        return "failed";
      }
    }
    await recordDraftFailure(
      draft.id,
      "Snap at least one photo — videos attach after the lot is cataloged.",
      "permanent"
    );
    return "failed";
  }
  if (images.length > CTBIDS_MAX_PHOTOS_PER_LOT) {
    await recordDraftFailure(
      draft.id,
      `This lot has ${images.length} photos; CT Bids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT}. Delete ${
        images.length - CTBIDS_MAX_PHOTOS_PER_LOT
      } on this phone, then tap Retry.`,
      "permanent"
    );
    return "failed";
  }

  const items = uploadItems(images);
  const expected = items.map((i) => i.name);
  const total = items.length;
  let jobId = draft.server_job_id;
  /** Union of every name the server reported; null = this API does not report names. */
  let received: Set<string> | null = null;
  const sentHere = new Set<string>();
  const unreadable = new Set<string>();
  const missing = () =>
    items.filter((i) => !received?.has(i.name) && !sentHere.has(i.name) && !unreadable.has(i.name));
  const doneCount = () => expected.filter((n) => received?.has(n) || sentHere.has(n)).length;
  const progress = async (text: string) => {
    await setDraftStatus(draft.id, "uploading", text);
    ctx.onProgress?.();
  };

  try {
    await progress(`Preparing ${total} photo${total === 1 ? "" : "s"}…`);

    if (jobId) {
      const batch = await receivedForJob(token, jobId);
      if (batch && jobIsLiveOrDone(batch.status)) {
        await setDraftStatus(draft.id, "uploaded", "On server — cataloging…");
        ctx.sent.push(draft);
        return "sent";
      }
      const plan =
        batch && batch.status === "receiving"
          ? planResume({ expected, received: batch.names, folderMissing: batch.folderMissing })
          : null;
      if (plan?.action === "resume") {
        received = new Set(plan.received);
      } else {
        await dropJob(token, draft.id, jobId);
        jobId = null;
      }
    }

    // The first photo creates the job (or lands in this draft's existing receiving job).
    for (let attempt = 0; !jobId && attempt < 2; attempt++) {
      let first: UploadPhotoUri | null = null;
      for (const item of missing()) {
        first = await preparePhoto(draft.id, item);
        if (first) break;
        unreadable.add(item.name);
      }
      if (!first) {
        throw new Error(
          "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
        );
      }
      await progress(`Sending photo 1 of ${total}…`);
      const job = await createLotJobWithPhoto({
        saleId: draft.sale_id,
        token,
        manualSku: draft.manual_sku ?? undefined,
        listingChannel: draft.listing_channel === "facebook" ? "facebook" : "ctseller",
        clientDraftId: draft.id,
        photo: first
      });
      jobId = job.id;
      await setDraftServerJobId(draft.id, jobId);
      sentHere.add(first.name);
      if (job.received_files) {
        const plan = planResume({ expected, received: job.received_files });
        if (plan.action === "restart") {
          // The server reused an older batch holding a photo since deleted on this phone.
          await dropJob(token, draft.id, jobId);
          jobId = null;
          sentHere.clear();
          received = null;
          continue;
        }
        received = new Set(plan.received);
      }
    }
    if (!jobId) throw new Error("The website kept an old copy of this lot — tap Sync to retry.");

    const liveJobId = jobId;
    const result = await runUploadPipeline({
      items: missing(),
      concurrency: ctx.concurrency,
      prepare: (item) => preparePhoto(draft.id, item),
      upload: async (photo) => {
        const job = await appendPhotoToJob(token, liveJobId, photo, draft.id);
        sentHere.add(photo.name);
        if (job.received_files && received) {
          for (const name of job.received_files) received.add(name);
        }
        await progress(`On server ${doneCount()}/${total}…`);
      }
    });
    for (const item of result.skipped) unreadable.add(item.name);
    const sendable = expected.filter((n) => !unreadable.has(n));
    if (!sendable.length) {
      throw new Error(
        "Could not open any photos for this lot — open a thumb to confirm it loads, then re-snap if needed."
      );
    }
    const stillMissing = namesStillMissing(sendable, received ? [...received] : null);
    if (stillMissing?.length) {
      throw new Error(
        `${stillMissing.length} photo${stillMissing.length === 1 ? "" : "s"} did not reach the website — Sync will resume.`
      );
    }

    await progress(`Starting catalog (${sendable.length} photos) — Lots appear when AI finishes`);
    await runJob(liveJobId, token);
    // Sent as soon as catalog is queued; the batched status check below and the follow-up
    // timer confirm it. Waiting here is what made a killed Sync start a second AI job.
    await clearDraftRetry(draft.id);
    await setDraftStatus(draft.id, "uploaded", "On server — cataloging…");
    ctx.sent.push(draft);
    return "sent";
  } catch (e) {
    // If /run already queued catalog, keep that job and treat the lot as sent — deleting it
    // used to start a duplicate. A receiving job is kept so the next Sync resumes it.
    if (jobId) {
      const looked = await fetchServerJobChecked(token, draft.sale_id, jobId);
      if (!looked.ok || (looked.job && jobIsLiveOrDone(looked.job.status))) {
        await setDraftStatus(
          draft.id,
          "uploaded",
          looked.ok ? "On server — cataloging…" : "On server — check the website"
        );
        if (looked.ok) ctx.sent.push(draft);
        return "sent";
      }
      if (!looked.job) {
        await setDraftServerJobId(draft.id, null);
      } else if (looked.job.status === "failed") {
        await dropJob(token, draft.id, jobId);
      }
    }
    await failDraft(draft.id, e);
    return "failed";
  }
}

/**
 * Add photos session: send each new shot to the website lot under its stable name and mark
 * it uploaded, then clips. The server skips names it already has, so a retry never duplicates.
 */
async function uploadAppendSession(ctx: SyncCtx, queued: LotDraft): Promise<LotOutcome> {
  const draft = await getLotDraft(queued.id);
  if (!draft || draft.status !== "queued" || draft.archived_at || !draft.append_lot_id) return "skipped";
  const lotId = draft.append_lot_id;
  const { token } = ctx;
  try {
    await processPendingCaptures(draft.id).catch(() => 0);
    const rows = await assignUploadNames(draft.id, true);
    const { images, videos } = splitDraftMedia(rows);
    if (!images.length && !videos.length) {
      await archiveCatalogedDraft(draft.id);
      return "skipped";
    }
    const pending = images.filter((p) => !p.uploaded && !p.upload_blocked);
    const total = pending.length;
    let done = 0;
    await setDraftStatus(draft.id, "uploading", total ? `Sending ${total} photo${total === 1 ? "" : "s"} to the lot…` : "Sending videos to the lot…");
    ctx.onProgress?.();
    const result = await runUploadPipeline({
      items: uploadItems(pending),
      concurrency: ctx.concurrency,
      prepare: (item) => preparePhoto(draft.id, item),
      upload: async (photo, item) => {
        await uploadLotPhoto(token, lotId, photo);
        await markDraftMediaUploaded(draft.id, item.id);
        done += 1;
        await setDraftStatus(draft.id, "uploading", `Added ${done}/${total} to the lot…`);
        ctx.onProgress?.();
      }
    });
    if (result.skipped.length) {
      throw new Error(
        `${result.skipped.length} photo${result.skipped.length === 1 ? " is" : "s are"} not readable on this phone — open this lot, delete ${
          result.skipped.length === 1 ? "it" : "them"
        }, then tap Retry.`
      );
    }
    await uploadPendingDraftVideos(token, draft.id, lotId, true);
    await clearDraftRetry(draft.id);
    await setDraftServerLotId(draft.id, lotId);
    await setDraftStatus(draft.id, "uploaded", null);
    await archiveCatalogedDraft(draft.id);
    return "sent";
  } catch (e) {
    await failDraft(draft.id, e);
    return "failed";
  }
}

/**
 * Upload queued/failed lot drafts when online.
 * Also recovers lots stuck in "uploading" (app killed mid-sync) by re-queuing them; their
 * ``receiving`` job is kept and resumed (only the missing photos are sent).
 * Two lots upload at once; within a lot, 3 photos at a time on Wi‑Fi and 1 on cellular.
 * Pass the ``WebsiteIndex`` the caller already built so one Sync reads it once.
 * ``manual`` = the user tapped Sync: transient failures skip their backoff wait.
 */
export async function syncPendingLots(
  token: string,
  onProgress?: () => void,
  websiteIndex?: WebsiteIndex,
  opts: { manual?: boolean } = {}
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
  const manual = Boolean(opts.manual);

  const drafts = await activeDrafts();
  const index = websiteIndex ?? (await loadWebsiteIndex(authToken, drafts));
  const ctx: SyncCtx = {
    token: authToken,
    manual,
    concurrency: uploadConcurrencyFor(net.type),
    index,
    sent: [],
    onProgress
  };

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
    await clearDraftRetry(draft.id);
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
        await clearDraftRetry(draft.id);
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
      if (draft.status !== "failed") {
        await recordDraftFailure(draft.id, serverFailMessage(job), "transient");
      }
    } else if (jobIsLiveOrDone(job.status)) {
      if (job.id !== draft.server_job_id) {
        await setDraftServerJobId(draft.id, job.id);
      }
      await setDraftStatus(draft.id, "uploaded", "Still cataloging on server…");
    } else if (job.status === "receiving" && job.id !== draft.server_job_id) {
      // Half-sent batch from a killed Sync: remember it so the upload resumes instead of restarting.
      await setDraftServerJobId(draft.id, job.id);
    }
  }
  onProgress?.();

  await attachPendingLotVideos(authToken, index);
  const archivedEarly = await sweepArchiveConfirmedDrafts(authToken, index);
  onProgress?.();

  const now = Date.now();
  for (const draft of await activeDrafts()) {
    if (draft.status !== "uploading" && draft.status !== "failed" && draft.status !== "queued") {
      continue;
    }
    if (
      draft.status === "failed" &&
      !retryAllowed({ errorKind: draft.error_kind, nextAttemptAt: draft.next_attempt_at, now, manual })
    ) {
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
      // A failed job is cleared and the lot sent again. A receiving job is kept: the upload
      // resumes it. Never delete queued/processing — that started a second AI run.
      if (job?.status === "failed") {
        await dropJob(authToken, draft.id, job.id);
      } else if (job?.status === "receiving") {
        if (job.id !== draft.server_job_id) await setDraftServerJobId(draft.id, job.id);
      } else {
        await setDraftServerJobId(draft.id, null);
      }
    }
    if (draft.status !== "queued" || draft.error_message) {
      await setDraftStatus(draft.id, "queued", null);
    }
  }
  onProgress?.();

  const attempted = new Set<string>();
  async function nextRound(): Promise<LotDraft[]> {
    const out: LotDraft[] = [];
    for (const d of await listLotDrafts()) {
      if (d.archived_at || d.status !== "queued" || attempted.has(d.id)) continue;
      if (d.append_lot_id) {
        out.push(d);
        continue;
      }
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
      out.push(d);
    }
    return out;
  }

  for (let round = 0; round < MAX_SYNC_ROUNDS; round++) {
    const pending = await nextRound();
    if (!pending.length) break;
    for (const d of pending) attempted.add(d.id);
    await mapPool(pending, LOTS_AT_ONCE, async (draft) => {
      const outcome = draft.append_lot_id
        ? await uploadAppendSession(ctx, draft)
        : await uploadNewLot(ctx, draft);
      if (outcome === "sent") uploaded += 1;
      else if (outcome === "failed") failed += 1;
      onProgress?.();
    });
  }

  // One batched status call for every lot sent this Sync (no per-lot polling). Lots still
  // cataloging are left to the follow-up timer.
  if (ctx.sent.length) {
    const looked = await lookupDraftStatuses(
      authToken,
      ctx.sent.map((d) => d.id)
    );
    if (looked.ok) {
      for (const draft of ctx.sent) {
        const row = looked.rows.get(normalizeDraftKey(draft.id));
        if (!row) continue;
        const lotId = row.lot_id ? String(row.lot_id) : null;
        if (row.cataloged || lotId || row.job_status === "completed") {
          noteDraftConfirmed(index, draft.id, lotId);
          await setDraftServerJobId(draft.id, null);
          const resolved = resolveDraftOnServer(draft, index).lotId;
          if (resolved) {
            await rememberServerLot(draft, resolved);
            try {
              await uploadPendingDraftVideos(authToken, draft.id, resolved);
            } catch {
              /* clip upload retries on the next sync */
            }
          }
          await setDraftStatus(draft.id, "uploaded", null);
        } else if (row.job_status === "failed") {
          // Keep server_job_id so the next sync's recovery pass clears the failed job before retrying.
          await recordDraftFailure(draft.id, serverFailMessage({ error_message: row.error_message ?? null }), "transient");
          uploaded -= 1;
          failed += 1;
        }
      }
      onProgress?.();
    }
  }

  await attachPendingLotVideos(authToken, index);
  const archived = archivedEarly + (await sweepArchiveConfirmedDrafts(authToken, index));

  const healthSaleIds = [
    ...new Set(
      (await listLotDrafts())
        .filter((d) => !d.append_lot_id)
        .map((d) => d.sale_id)
        .filter(Boolean)
    )
  ];
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
