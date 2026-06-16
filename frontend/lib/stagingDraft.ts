/**
 * Persists the dashboard upload-box staging area in IndexedDB so refresh/navigation
 * does not silently drop captured photos (still in-browser only).
 */

const DB_NAME = "ct-staging-draft";
const STORE = "draft";
const KEY = "current";

type SerializedFile = {
  name: string;
  type: string;
  lastModified: number;
  relativePath?: string;
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

type UploadRelativeFile = File & { webkitRelativePath?: string };

function cleanUploadRelativePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\/+/, "");
}

export function getUploadRelativePath(file: File): string {
  const rel = cleanUploadRelativePath((file as UploadRelativeFile).webkitRelativePath || "");
  return rel || file.name || "unnamed";
}

function attachUploadRelativePath<T extends File>(file: T, relativePath?: string): T {
  const rel = cleanUploadRelativePath(relativePath || "");
  if (!rel || rel === file.name) return file;
  try {
    Object.defineProperty(file, "webkitRelativePath", {
      value: rel,
      configurable: true
    });
  } catch {
    /* Some browser File implementations may not allow overriding this readonly hint. */
  }
  return file;
}

export function preserveUploadRelativePath<T extends File>(file: T, source: File): T {
  const sourceRel = getUploadRelativePath(source);
  const parts = sourceRel.split("/").filter(Boolean);
  if (parts.length <= 1) return file;
  return attachUploadRelativePath(file, [...parts.slice(0, -1), file.name || "unnamed"].join("/"));
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
      relativePath: getUploadRelativePath(f),
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
  const imageFiles = record.files.map((f) =>
    attachUploadRelativePath(
      new File([f.data], f.name, { type: f.type, lastModified: f.lastModified }),
      f.relativePath
    )
  );
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
