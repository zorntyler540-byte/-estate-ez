/**
 * Persists the dashboard upload-box staging area in IndexedDB so refresh/navigation
 * does not silently drop captured photos (still in-browser only).
 */

import { getStagedUploadRel, markStagedUploadRel, readSourceUploadRel } from "./stagedUploadPath";

const DB_NAME = "ct-staging-draft";
const STORE = "draft";
const KEY = "current";

type SerializedFile = {
  name: string;
  type: string;
  lastModified: number;
  uploadRel?: string;
  data: ArrayBuffer;
};

export type StagingDraftPayload = {
  folderLabel: string;
  imageFiles: File[];
  thumbUrls: string[];
  skippedCount: number;
  singleLotMode?: boolean;
  singleLotManualSku?: string;
  saleId?: string;
};

type StagingDraftRecord = {
  folderLabel: string;
  skippedCount: number;
  singleLotMode?: boolean;
  singleLotManualSku?: string;
  saleId?: string;
  files: SerializedFile[];
};

export function stagingDraftSupported(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB open failed"));
  });
}

function runTransaction<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const store = tx.objectStore(STORE);
        const req = fn(store);
        req.onsuccess = () => resolve(req.result as T);
        req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
        tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
        tx.oncomplete = () => db.close();
      })
  );
}

export async function saveStagingDraft(
  pick: {
    folderLabel: string;
    imageFiles: File[];
    skippedCount: number;
    singleLotMode?: boolean;
  },
  opts?: { saleId?: string; singleLotManualSku?: string }
): Promise<void> {
  if (!stagingDraftSupported() || !pick.imageFiles.length) return;
  const files: SerializedFile[] = await Promise.all(
    pick.imageFiles.map(async (f) => ({
      name: f.name,
      type: f.type || "image/jpeg",
      lastModified: f.lastModified,
      uploadRel: readSourceUploadRel(f) || getStagedUploadRel(f),
      data: await f.arrayBuffer()
    }))
  );
  const record: StagingDraftRecord = {
    folderLabel: pick.folderLabel,
    skippedCount: pick.skippedCount,
    singleLotMode: pick.singleLotMode,
    singleLotManualSku: opts?.singleLotManualSku?.trim() || undefined,
    saleId: opts?.saleId || undefined,
    files
  };
  await runTransaction("readwrite", (store) => store.put(record, KEY));
}

export async function loadStagingDraft(): Promise<StagingDraftPayload | null> {
  if (!stagingDraftSupported()) return null;
  let record: StagingDraftRecord | undefined;
  try {
    record = await runTransaction<StagingDraftRecord | undefined>("readonly", (store) => store.get(KEY));
  } catch {
    return null;
  }
  if (!record?.files?.length) return null;
  const imageFiles = record.files.map((f) => {
    const file = new File([f.data], f.name, { type: f.type, lastModified: f.lastModified });
    if (f.uploadRel) markStagedUploadRel(file, f.uploadRel);
    return file;
  });
  const thumbUrls = imageFiles.map((f) => URL.createObjectURL(f));
  return {
    folderLabel: record.folderLabel,
    imageFiles,
    thumbUrls,
    skippedCount: record.skippedCount,
    singleLotMode: record.singleLotMode,
    singleLotManualSku: record.singleLotManualSku,
    saleId: record.saleId
  };
}

export async function clearStagingDraft(): Promise<void> {
  if (!stagingDraftSupported()) return;
  try {
    await runTransaction("readwrite", (store) => store.delete(KEY));
  } catch {
    /* ignore */
  }
}
