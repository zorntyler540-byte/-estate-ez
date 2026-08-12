import * as SQLite from "expo-sqlite";
import * as FileSystem from "expo-file-system/legacy";

import type { ListingChannel, LotDraft, LotDraftStatus, LotPhotoRow } from "@/lib/types";
import { newLocalId } from "@/lib/uuid";

const DB_NAME = "ct_capture.db";

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

async function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync(DB_NAME);
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS lot_drafts (
          id TEXT PRIMARY KEY NOT NULL,
          sale_id TEXT NOT NULL,
          manual_sku TEXT,
          status TEXT NOT NULL,
          error_message TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lot_photos (
          id TEXT PRIMARY KEY NOT NULL,
          draft_id TEXT NOT NULL,
          file_uri TEXT NOT NULL,
          sort_order INTEGER NOT NULL,
          FOREIGN KEY (draft_id) REFERENCES lot_drafts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_lot_photos_draft ON lot_photos(draft_id);
      `);
      const cols = await db.getAllAsync<{ name: string }>("PRAGMA table_info(lot_drafts)");
      if (!cols.some((c) => c.name === "sale_name")) {
        await db.execAsync("ALTER TABLE lot_drafts ADD COLUMN sale_name TEXT");
      }
      if (!cols.some((c) => c.name === "listing_channel")) {
        await db.execAsync(
          "ALTER TABLE lot_drafts ADD COLUMN listing_channel TEXT NOT NULL DEFAULT 'ctseller'"
        );
      }
      if (!cols.some((c) => c.name === "server_job_id")) {
        await db.execAsync("ALTER TABLE lot_drafts ADD COLUMN server_job_id TEXT");
      }
      return db;
    })();
  }
  return dbPromise;
}

function rowToDraft(row: Record<string, unknown>, photoCount: number): LotDraft {
  const channelRaw = row.listing_channel != null ? String(row.listing_channel) : "ctseller";
  const listing_channel: ListingChannel = channelRaw === "facebook" ? "facebook" : "ctseller";
  return {
    id: String(row.id),
    sale_id: String(row.sale_id),
    sale_name: row.sale_name != null ? String(row.sale_name) : null,
    manual_sku: row.manual_sku != null ? String(row.manual_sku) : null,
    listing_channel,
    status: row.status as LotDraftStatus,
    error_message: row.error_message != null ? String(row.error_message) : null,
    server_job_id: row.server_job_id != null ? String(row.server_job_id) : null,
    created_at: Number(row.created_at),
    updated_at: Number(row.updated_at),
    photo_count: photoCount
  };
}

export async function createLotDraft(
  saleId: string,
  saleName: string | null,
  manualSku?: string,
  listingChannel: ListingChannel = "ctseller"
): Promise<string> {
  const db = await getDb();
  const id = newLocalId();
  const now = Date.now();
  const channel: ListingChannel = listingChannel === "facebook" ? "facebook" : "ctseller";
  await db.runAsync(
    `INSERT INTO lot_drafts (id, sale_id, sale_name, manual_sku, listing_channel, status, error_message, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, 'draft', NULL, ?, ?)`,
    [id, saleId, saleName?.trim() || null, manualSku?.trim() || null, channel, now, now]
  );
  return id;
}

export async function addPhotoToDraft(draftId: string, fileUri: string): Promise<string> {
  const db = await getDb();
  const id = newLocalId();
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_photos WHERE draft_id = ?",
    [draftId]
  );
  const sortOrder = Number(row?.c ?? 0);
  await db.runAsync(
    "INSERT INTO lot_photos (id, draft_id, file_uri, sort_order) VALUES (?, ?, ?, ?)",
    [id, draftId, fileUri, sortOrder]
  );
  await db.runAsync("UPDATE lot_drafts SET updated_at = ? WHERE id = ?", [Date.now(), draftId]);
  return id;
}

export async function listLotDrafts(): Promise<LotDraft[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<Record<string, unknown>>(
    "SELECT * FROM lot_drafts ORDER BY updated_at DESC"
  );
  const out: LotDraft[] = [];
  for (const row of rows) {
    const pc = await db.getFirstAsync<{ c: number }>(
      "SELECT COUNT(*) AS c FROM lot_photos WHERE draft_id = ?",
      [String(row.id)]
    );
    out.push(rowToDraft(row, Number(pc?.c ?? 0)));
  }
  return out;
}

export async function getLotDraft(draftId: string): Promise<LotDraft | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<Record<string, unknown>>(
    "SELECT * FROM lot_drafts WHERE id = ?",
    [draftId]
  );
  if (!row) return null;
  const pc = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_photos WHERE draft_id = ?",
    [draftId]
  );
  return rowToDraft(row, Number(pc?.c ?? 0));
}

/**
 * Newest unfinished lot for a sale + listing channel.
 * Capture sessions remount after leaving the app; this is how we resume photos
 * that were already saved to SQLite / disk.
 */
export async function findOpenDraft(
  saleId: string,
  listingChannel: ListingChannel = "ctseller"
): Promise<LotDraft | null> {
  const db = await getDb();
  const channel: ListingChannel = listingChannel === "facebook" ? "facebook" : "ctseller";
  const row = await db.getFirstAsync<Record<string, unknown>>(
    `SELECT * FROM lot_drafts
     WHERE sale_id = ? AND listing_channel = ? AND status = 'draft'
     ORDER BY updated_at DESC
     LIMIT 1`,
    [saleId, channel]
  );
  if (!row) return null;
  const id = String(row.id);
  const pc = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_photos WHERE draft_id = ?",
    [id]
  );
  return rowToDraft(row, Number(pc?.c ?? 0));
}

export async function listDraftPhotos(draftId: string): Promise<LotPhotoRow[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<Record<string, unknown>>(
    "SELECT * FROM lot_photos WHERE draft_id = ? ORDER BY sort_order ASC",
    [draftId]
  );
  return rows.map((r) => ({
    id: String(r.id),
    draft_id: String(r.draft_id),
    file_uri: String(r.file_uri),
    sort_order: Number(r.sort_order)
  }));
}

export async function setDraftStatus(
  draftId: string,
  status: LotDraftStatus,
  errorMessage?: string | null
): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    "UPDATE lot_drafts SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
    [status, errorMessage ?? null, Date.now(), draftId]
  );
}

/** Remember the server ``receiving`` job so a killed Sync can delete the orphan and retry cleanly. */
export async function setDraftServerJobId(draftId: string, serverJobId: string | null): Promise<void> {
  const db = await getDb();
  await db.runAsync("UPDATE lot_drafts SET server_job_id = ?, updated_at = ? WHERE id = ?", [
    serverJobId,
    Date.now(),
    draftId
  ]);
}

export async function updateDraftManualSku(draftId: string, manualSku: string): Promise<void> {
  const db = await getDb();
  await db.runAsync("UPDATE lot_drafts SET manual_sku = ?, updated_at = ? WHERE id = ?", [
    manualSku.trim() || null,
    Date.now(),
    draftId
  ]);
}

/** Fill missing ``sale_name`` on local drafts from the server sale list. */
export async function backfillDraftSaleNames(nameById: Map<string, string>): Promise<void> {
  if (!nameById.size) return;
  const db = await getDb();
  const rows = await db.getAllAsync<{ id: string; sale_id: string; sale_name: string | null }>(
    "SELECT id, sale_id, sale_name FROM lot_drafts WHERE sale_name IS NULL OR TRIM(sale_name) = ''"
  );
  const now = Date.now();
  for (const row of rows) {
    const name = nameById.get(row.sale_id)?.trim();
    if (!name) continue;
    await db.runAsync("UPDATE lot_drafts SET sale_name = ?, updated_at = ? WHERE id = ?", [
      name,
      now,
      row.id
    ]);
  }
}

export async function deletePhotoFromDraft(draftId: string, fileUri: string): Promise<boolean> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ id: string }>(
    "SELECT id FROM lot_photos WHERE draft_id = ? AND file_uri = ?",
    [draftId, fileUri]
  );
  if (!row) return false;
  await db.runAsync("DELETE FROM lot_photos WHERE id = ?", [row.id]);
  await db.runAsync("UPDATE lot_drafts SET updated_at = ? WHERE id = ?", [Date.now(), draftId]);
  return true;
}

export async function deletePhotoById(draftId: string, photoId: string): Promise<boolean> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ id: string }>(
    "SELECT id FROM lot_photos WHERE draft_id = ? AND id = ?",
    [draftId, photoId]
  );
  if (!row) return false;
  await db.runAsync("DELETE FROM lot_photos WHERE id = ?", [row.id]);
  await db.runAsync("UPDATE lot_drafts SET updated_at = ? WHERE id = ?", [Date.now(), draftId]);
  return true;
}

export async function deleteLotDraft(draftId: string): Promise<void> {
  const photos = await listDraftPhotos(draftId);
  for (const photo of photos) {
    try {
      await FileSystem.deleteAsync(photo.file_uri, { idempotent: true });
    } catch {
      /* file may already be gone */
    }
  }
  const db = await getDb();
  await db.runAsync("DELETE FROM lot_photos WHERE draft_id = ?", [draftId]);
  await db.runAsync("DELETE FROM lot_drafts WHERE id = ?", [draftId]);
}

export async function countPendingDrafts(): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_drafts WHERE status IN ('draft', 'queued', 'failed')"
  );
  return Number(row?.c ?? 0);
}

export async function countUploadPending(): Promise<number> {
  const db = await getDb();
  // Include "uploading" so Sync Now / foreground sync recover lots stuck after an app kill.
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_drafts WHERE status IN ('queued', 'failed', 'uploading')"
  );
  return Number(row?.c ?? 0);
}
