/**
 * Persists the dashboard upload-box staging area in IndexedDB so refresh/navigation
 * does not silently drop captured photos (still in-browser only).
 *
 * Each photo is its own record (a ``Blob``, not an ``ArrayBuffer`` copy), written once when it
 * is staged; later saves only add new photos, delete removed ones and rewrite the small meta
 * record. Re-reading every staged photo into memory on every change used to stall / crash
 * phones on large folders. Relative paths (subfolder = lot) and stable upload names survive.
 */

import type { WebUploadSession } from "./webUploadSession.ts";

const DB_NAME = "ct-staging-draft";
const DB_VERSION = 2;
const META_STORE = "draft";
const FILE_STORE = "files";
const KEY = "current";

type StoredFile = {
  name: string;
  type: string;
  lastModified: number;
  /** ``folder/sub/photo.jpg`` as uploaded (subfolders mark lots in batch mode). */
  uploadRel?: string;
  uploadName?: string;
  blob?: Blob;
  /** Engines that cannot store Blobs in IndexedDB. */
  data?: ArrayBuffer;
};

/** v1 records stored every photo inline. */
type LegacySerializedFile = { name: string; type: string; lastModified: number; data: ArrayBuffer };

type StagingDraftRecord = {
  folderLabel: string;
  skippedCount: number;
  singleLotMode?: boolean;
  singleLotManualSku?: string;
  saleId?: string;
  fileKeys?: string[];
  session?: WebUploadSession | null;
  files?: LegacySerializedFile[];
};

export type RestoredStagedFile = {
  file: File;
  uploadRel: string | null;
  uploadName: string | null;
};

export type StagingDraftPayload = {
  folderLabel: string;
  files: RestoredStagedFile[];
  skippedCount: number;
  singleLotMode?: boolean;
  singleLotManualSku?: string;
  saleId?: string;
  session: WebUploadSession | null;
};

export type StagingDraftHooks = {
  uploadRelOf: (file: File) => string | null;
  uploadNameOf: (file: File) => string | undefined;
};

export function stagingDraftSupported(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(META_STORE)) db.createObjectStore(META_STORE);
      if (!db.objectStoreNames.contains(FILE_STORE)) db.createObjectStore(FILE_STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB open failed"));
  });
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB request failed"));
  });
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

const fileKeys = new WeakMap<File, string>();
let keyCounter = 0;

function keyFor(file: File): string {
  let key = fileKeys.get(file);
  if (!key) {
    keyCounter += 1;
    key = `f-${Date.now().toString(36)}-${keyCounter.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    fileKeys.set(file, key);
  }
  return key;
}

/** Keys already written in this tab (a fresh tab re-learns them from ``loadStagingDraft``). */
const writtenKeys = new Set<string>();

async function putFile(db: IDBDatabase, key: string, file: File, hooks: StagingDraftHooks): Promise<void> {
  const base: StoredFile = {
    name: file.name,
    type: file.type || "image/jpeg",
    lastModified: file.lastModified,
    uploadRel: hooks.uploadRelOf(file) || undefined,
    uploadName: hooks.uploadNameOf(file)
  };
  try {
    const tx = db.transaction(FILE_STORE, "readwrite");
    tx.objectStore(FILE_STORE).put({ ...base, blob: file }, key);
    await txDone(tx);
  } catch {
    // Old WebKit: "BlobURLs are not yet supported" — fall back to one inline copy per photo.
    const data = await file.arrayBuffer();
    const tx = db.transaction(FILE_STORE, "readwrite");
    tx.objectStore(FILE_STORE).put({ ...base, data }, key);
    await txDone(tx);
  }
}

let saveChain: Promise<void> = Promise.resolve();

/** Serialized: overlapping debounced saves must not interleave deletes and puts. */
export function saveStagingDraft(
  pick: {
    folderLabel: string;
    imageFiles: File[];
    skippedCount: number;
    singleLotMode?: boolean;
  },
  hooks: StagingDraftHooks,
  opts?: { saleId?: string; singleLotManualSku?: string; session?: WebUploadSession | null }
): Promise<void> {
  const run = () => saveNow(pick, hooks, opts);
  saveChain = saveChain.then(run, run);
  return saveChain;
}

async function saveNow(
  pick: { folderLabel: string; imageFiles: File[]; skippedCount: number; singleLotMode?: boolean },
  hooks: StagingDraftHooks,
  opts?: { saleId?: string; singleLotManualSku?: string; session?: WebUploadSession | null }
): Promise<void> {
  if (!stagingDraftSupported() || !pick.imageFiles.length) return;
  const db = await openDb();
  try {
    const keys = pick.imageFiles.map(keyFor);
    // One photo per transaction: only that photo's bytes are in flight at a time.
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i]!;
      if (writtenKeys.has(key)) continue;
      await putFile(db, key, pick.imageFiles[i]!, hooks);
      writtenKeys.add(key);
    }
    const wanted = new Set(keys);
    const existing = (await request(db.transaction(FILE_STORE, "readonly").objectStore(FILE_STORE).getAllKeys())) as string[];
    const tx = db.transaction([FILE_STORE, META_STORE], "readwrite");
    for (const k of existing) {
      if (!wanted.has(String(k))) {
        tx.objectStore(FILE_STORE).delete(k);
        writtenKeys.delete(String(k));
      }
    }
    const record: StagingDraftRecord = {
      folderLabel: pick.folderLabel,
      skippedCount: pick.skippedCount,
      singleLotMode: pick.singleLotMode,
      singleLotManualSku: opts?.singleLotManualSku?.trim() || undefined,
      saleId: opts?.saleId || undefined,
      fileKeys: keys,
      session: opts?.session ?? null
    };
    tx.objectStore(META_STORE).put(record, KEY);
    await txDone(tx);
  } finally {
    db.close();
  }
}

export async function loadStagingDraft(): Promise<StagingDraftPayload | null> {
  if (!stagingDraftSupported()) return null;
  let db: IDBDatabase;
  try {
    db = await openDb();
  } catch {
    return null;
  }
  try {
    const record = (await request(
      db.transaction(META_STORE, "readonly").objectStore(META_STORE).get(KEY)
    )) as StagingDraftRecord | undefined;
    if (!record) return null;
    const files: RestoredStagedFile[] = [];
    if (record.fileKeys?.length) {
      const store = db.transaction(FILE_STORE, "readonly").objectStore(FILE_STORE);
      const rows = await Promise.all(
        record.fileKeys.map((k) => request(store.get(k)) as Promise<StoredFile | undefined>)
      );
      rows.forEach((row, i) => {
        if (!row) return;
        const body = row.blob ?? (row.data ? new Blob([row.data]) : null);
        if (!body) return;
        const file = new File([body], row.name, { type: row.type, lastModified: row.lastModified });
        const key = record.fileKeys![i]!;
        fileKeys.set(file, key);
        writtenKeys.add(key);
        files.push({ file, uploadRel: row.uploadRel || null, uploadName: row.uploadName || null });
      });
    } else if (record.files?.length) {
      for (const f of record.files) {
        files.push({
          file: new File([f.data], f.name, { type: f.type, lastModified: f.lastModified }),
          uploadRel: null,
          uploadName: null
        });
      }
    }
    if (!files.length) return null;
    return {
      folderLabel: record.folderLabel,
      files,
      skippedCount: record.skippedCount,
      singleLotMode: record.singleLotMode,
      singleLotManualSku: record.singleLotManualSku,
      saleId: record.saleId,
      session: record.session ?? null
    };
  } catch {
    return null;
  } finally {
    db.close();
  }
}

export function clearStagingDraft(): Promise<void> {
  const run = async () => {
    if (!stagingDraftSupported()) return;
    try {
      const db = await openDb();
      try {
        const tx = db.transaction([FILE_STORE, META_STORE], "readwrite");
        tx.objectStore(FILE_STORE).clear();
        tx.objectStore(META_STORE).delete(KEY);
        await txDone(tx);
        writtenKeys.clear();
      } finally {
        db.close();
      }
    } catch {
      /* ignore */
    }
  };
  saveChain = saveChain.then(run, run);
  return saveChain;
}
