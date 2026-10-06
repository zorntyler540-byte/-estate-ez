/**
 * Extra copy of a capture in the phone's Photos app.
 * The queue file is the upload source. This copy is only a backup.
 */

export type LibrarySaveResult = "saved" | "denied" | "unavailable" | "failed";

export type SavePlatform = "ios" | "android" | "other";

export type LibrarySaver = {
  save: (uri: string) => Promise<void>;
  /** Android may need a write prompt before MediaStore will accept the file. */
  ensureWritePermission?: () => Promise<boolean>;
};

export function librarySaveNote(result: LibrarySaveResult): string | null {
  if (result === "denied") {
    return "This shot is in the Queue. To also keep a copy in Photos, allow adding photos in Settings.";
  }
  if (result === "failed") {
    return "This shot is in the Queue. A copy could not be saved to the Photos app.";
  }
  return null;
}

export function isLibraryPermissionError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || "");
  return /denied|not authorized|permission|phphotos|e_no_perms|missing_permissions/i.test(message);
}

export async function saveCaptureCopy(
  uri: string,
  saver: LibrarySaver | null,
  platform: SavePlatform = "ios"
): Promise<LibrarySaveResult> {
  const local = (uri || "").trim();
  if (!local || !saver) return saver ? "failed" : "unavailable";
  try {
    if (platform === "android" && saver.ensureWritePermission) {
      const allowed = await saver.ensureWritePermission();
      if (!allowed) return "denied";
    }
    await saver.save(local);
    return "saved";
  } catch (error) {
    if (isLibraryPermissionError(error)) return "denied";
    return "failed";
  }
}

type MediaLibraryModule = {
  saveToLibraryAsync: (localUri: string) => Promise<void>;
  getPermissionsAsync: (
    writeOnly?: boolean,
    granularPermissions?: string[]
  ) => Promise<{ granted?: boolean }>;
  requestPermissionsAsync: (
    writeOnly?: boolean,
    granularPermissions?: string[]
  ) => Promise<{ granted?: boolean }>;
};

let mediaLibraryLoad: Promise<MediaLibraryModule | null> | null = null;

export function loadMediaLibraryModule(): Promise<MediaLibraryModule | null> {
  if (!mediaLibraryLoad) {
    mediaLibraryLoad = import("expo-media-library")
      .then((mod) => {
        if (typeof mod.saveToLibraryAsync !== "function") return null;
        return mod as MediaLibraryModule;
      })
      .catch(() => null);
  }
  return mediaLibraryLoad;
}

/** True only when this install includes the native Photos module. */
export async function photoLibraryBackupAvailable(): Promise<boolean> {
  const mod = await loadMediaLibraryModule();
  return mod != null;
}

function detectPlatform(): SavePlatform {
  try {
    // Loaded only for a real save. Tests pass a platform and a fake saver.
    const reactNative = require("react-native") as { Platform?: { OS?: string } };
    const os = reactNative.Platform?.OS;
    if (os === "ios" || os === "android") return os;
  } catch {
    /* node */
  }
  return "other";
}

async function defaultSaver(platform: SavePlatform): Promise<LibrarySaver | null> {
  if (platform === "other") return null;
  const mod = await loadMediaLibraryModule();
  if (!mod) return null;
  return {
    async ensureWritePermission() {
      const current = await mod.getPermissionsAsync(true, ["photo", "video"]);
      if (current.granted) return true;
      const next = await mod.requestPermissionsAsync(true, ["photo", "video"]);
      return Boolean(next.granted);
    },
    async save(uri: string) {
      await mod.saveToLibraryAsync(uri);
    }
  };
}

export async function saveCaptureToPhotoLibrary(
  uri: string,
  options?: { saver?: LibrarySaver | null; platform?: SavePlatform }
): Promise<LibrarySaveResult> {
  if (options && "saver" in options) {
    return saveCaptureCopy(uri, options.saver ?? null, options.platform ?? "ios");
  }
  const platform = options?.platform ?? detectPlatform();
  const saver = await defaultSaver(platform);
  return saveCaptureCopy(uri, saver, platform);
}
