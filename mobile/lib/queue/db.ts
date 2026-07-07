import * as SQLite from "expo-sqlite";
import * as FileSystem from "expo-file-system/legacy";

import type { LotDraft, LotDraftStatus, LotPhotoRow } from "@/lib/types";
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
      return db;
    })();
  }
  return dbPromise;
}

function rowToDraft(row: Record<string, unknown>, photoCount: number): LotDraft {
  return {
    id: String(row.id),
    sale_id: String(row.sale_id),
    manual_sku: row.manual_sku != null ? String(row.manual_sku) : null,
    status: row.status as LotDraftStatus,
    error_message: row.error_message != null ? String(row.error_message) : null,
    created_at: Number(row.created_at),
    updated_at: Number(row.updated_at),
    photo_count: photoCount
  };
}

export async function createLotDraft(saleId: string, manualSku?: string): Promise<string> {
  const db = await getDb();
  const id = newLocalId();
  const now = Date.now();
  await db.runAsync(
    `INSERT INTO lot_drafts (id, sale_id, manual_sku, status, error_message, created_at, updated_at)
     VALUES (?, ?, ?, 'draft', NULL, ?, ?)`,
    [id, saleId, manualSku?.trim() || null, now, now]
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
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_drafts WHERE status IN ('queued', 'failed')"
  );
  return Number(row?.c ?? 0);
}
