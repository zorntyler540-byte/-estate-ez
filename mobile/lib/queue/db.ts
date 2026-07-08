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
          account_id TEXT NOT NULL,
          sale_id TEXT NOT NULL,
          manual_sku TEXT,
          server_job_id TEXT,
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
      if (!cols.some((c) => c.name === "account_id")) {
        await db.execAsync("ALTER TABLE lot_drafts ADD COLUMN account_id TEXT");
      }
      if (!cols.some((c) => c.name === "sale_name")) {
        await db.execAsync("ALTER TABLE lot_drafts ADD COLUMN sale_name TEXT");
      }
      if (!cols.some((c) => c.name === "server_job_id")) {
        await db.execAsync("ALTER TABLE lot_drafts ADD COLUMN server_job_id TEXT");
      }
      await db.execAsync(
        "CREATE INDEX IF NOT EXISTS idx_lot_drafts_account_status ON lot_drafts(account_id, status)"
      );
      return db;
    })();
  }
  return dbPromise;
}

function rowToDraft(row: Record<string, unknown>, photoCount: number): LotDraft {
  return {
    id: String(row.id),
    account_id: String(row.account_id),
    sale_id: String(row.sale_id),
    sale_name: row.sale_name != null ? String(row.sale_name) : null,
    manual_sku: row.manual_sku != null ? String(row.manual_sku) : null,
    server_job_id: row.server_job_id != null ? String(row.server_job_id) : null,
    status: row.status as LotDraftStatus,
    error_message: row.error_message != null ? String(row.error_message) : null,
    created_at: Number(row.created_at),
    updated_at: Number(row.updated_at),
    photo_count: photoCount
  };
}

export async function createLotDraft(
  accountId: string,
  saleId: string,
  saleName: string | null,
  manualSku?: string
): Promise<string> {
  const db = await getDb();
  const id = newLocalId();
  const now = Date.now();
  await db.runAsync(
    `INSERT INTO lot_drafts (id, account_id, sale_id, sale_name, manual_sku, status, error_message, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, 'draft', NULL, ?, ?)`,
    [id, accountId, saleId, saleName?.trim() || null, manualSku?.trim() || null, now, now]
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

export async function listLotDrafts(accountId: string): Promise<LotDraft[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<Record<string, unknown>>(
    "SELECT * FROM lot_drafts WHERE account_id = ? ORDER BY updated_at DESC",
    [accountId]
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

export async function setDraftServerJob(draftId: string, jobId: string): Promise<void> {
  const db = await getDb();
  await db.runAsync("UPDATE lot_drafts SET server_job_id = ?, updated_at = ? WHERE id = ?", [
    jobId,
    Date.now(),
    draftId
  ]);
}

/** Fill missing ``sale_name`` on local drafts from the server sale list. */
export async function backfillDraftSaleNames(
  accountId: string,
  nameById: Map<string, string>
): Promise<void> {
  if (!nameById.size) return;
  const db = await getDb();
  const rows = await db.getAllAsync<{ id: string; sale_id: string; sale_name: string | null }>(
    "SELECT id, sale_id, sale_name FROM lot_drafts WHERE account_id = ? AND (sale_name IS NULL OR TRIM(sale_name) = '')",
    [accountId]
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

export async function recoverInterruptedUploads(accountId: string): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    "UPDATE lot_drafts SET status = 'queued', error_message = NULL, updated_at = ? WHERE account_id = ? AND status = 'uploading'",
    [Date.now(), accountId]
  );
}

export async function countPendingDrafts(accountId: string): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_drafts WHERE account_id = ? AND status IN ('draft', 'queued', 'failed')",
    [accountId]
  );
  return Number(row?.c ?? 0);
}

export async function countUploadPending(accountId: string): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ c: number }>(
    "SELECT COUNT(*) AS c FROM lot_drafts WHERE account_id = ? AND status IN ('queued', 'failed')",
    [accountId]
  );
  return Number(row?.c ?? 0);
}
