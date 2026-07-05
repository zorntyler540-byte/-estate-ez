/**
 * Persists in-progress lot-camera snaps so a refresh or tab kill mid-session
 * does not lose photos before the user taps Done.
 */

const DB_NAME = "ct-camera-session";
const STORE = "session";
const KEY = "lot-camera";

type SerializedFile = {
  name: string;
  type: string;
  lastModified: number;
  data: ArrayBuffer;
};

type CameraSessionRecord = {
  singleLotManualSku?: string;
  savedAt: number;
  files: SerializedFile[];
};

export type CameraSessionDraft = {
  files: File[];
  singleLotManualSku?: string;
  savedAt: number;
};

export function cameraSessionDraftSupported(): boolean {
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

function runTransaction<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>
): Promise<T> {
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

export async function saveCameraSessionDraft(
  files: File[],
  opts?: { singleLotManualSku?: string }
): Promise<void> {
  if (!cameraSessionDraftSupported() || !files.length) return;
  const serialized: SerializedFile[] = [];
  for (const f of files) {
    try {
      serialized.push({
        name: f.name,
        type: f.type || "image/jpeg",
        lastModified: f.lastModified,
        data: await f.arrayBuffer()
      });
    } catch {
      /* skip unreadable file */
    }
  }
  if (!serialized.length) return;
  const record: CameraSessionRecord = {
    singleLotManualSku: opts?.singleLotManualSku?.trim() || undefined,
    savedAt: Date.now(),
    files: serialized
  };
  await runTransaction("readwrite", (store) => store.put(record, KEY));
}

export async function loadCameraSessionDraft(): Promise<CameraSessionDraft | null> {
  if (!cameraSessionDraftSupported()) return null;
  let record: CameraSessionRecord | undefined;
  try {
    record = await runTransaction<CameraSessionRecord | undefined>("readonly", (store) =>
      store.get(KEY)
    );
  } catch {
    return null;
  }
  if (!record?.files?.length) return null;
  const files = record.files.map(
    (f) => new File([f.data], f.name, { type: f.type, lastModified: f.lastModified })
  );
  return {
    files,
    singleLotManualSku: record.singleLotManualSku,
    savedAt: record.savedAt
  };
}

export async function clearCameraSessionDraft(): Promise<void> {
  if (!cameraSessionDraftSupported()) return;
  try {
    await runTransaction("readwrite", (store) => store.delete(KEY));
  } catch {
    /* ignore */
  }
}

export const CAMERA_SAVE_BACKUP_KEY = "ct_camera_save_backup_after_done";

export function readCameraSaveBackupPref(): boolean {
  if (typeof localStorage === "undefined") return false;
  try {
    return localStorage.getItem(CAMERA_SAVE_BACKUP_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeCameraSaveBackupPref(enabled: boolean): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(CAMERA_SAVE_BACKUP_KEY, enabled ? "1" : "0");
  } catch {
    /* ignore */
  }
}
