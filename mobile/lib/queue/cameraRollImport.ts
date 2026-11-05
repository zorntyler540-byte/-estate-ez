import * as FileSystem from "expo-file-system/legacy";
import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import { Platform } from "react-native";

import { UPLOAD_MAX_EDGE_PX } from "../uploadPrepare";
import { mapPool } from "../uploadPool";
import { addPhotoToDraft, createImportDraft, deleteLotDraft, discardStaleImports, finishImportDraft } from "./db";
import { exifTakenAtMs, IMPORT_MAX_PICKS, importCapturedAt, type ImportPick } from "./importGroups";

/** Lots copied at once (one photo each). Native re-encodes are heavy; Sync may be running too. */
const IMPORT_LOTS_AT_ONCE = 2;
const IMPORT_JPEG_QUALITY = 0.88;

/** Draft ids this app process is still copying; anything else left ``importing`` was cut off. */
const activeImports = new Set<string>();

/** Drop import lots a killed app left half-copied. Safe to call any time. */
export async function discardInterruptedImports(): Promise<number> {
  return discardStaleImports(activeImports);
}

export type PickResult = { picks: ImportPick[]; truncated: number };

/**
 * System photo picker (PHPicker / Android Photo Picker): no photo-library permission, and the
 * module is already in the native build. iOS returns photos in tap order.
 */
export async function pickCameraRollPhotos(): Promise<PickResult | null> {
  const res = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ["images"],
    allowsMultipleSelection: true,
    // Android throws when the limit is over the system max (often 100); 0 = that max.
    selectionLimit: Platform.OS === "ios" ? IMPORT_MAX_PICKS : 0,
    orderedSelection: true,
    exif: true,
    quality: 1,
    preferredAssetRepresentationMode: ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Current
  });
  if (res.canceled || !res.assets?.length) return null;
  const assets = res.assets.filter((a) => (a.type ?? "image") === "image");
  const kept = assets.slice(0, IMPORT_MAX_PICKS);
  const seen = new Set<string>();
  const picks: ImportPick[] = [];
  kept.forEach((a, i) => {
    let key = a.assetId || a.uri;
    if (seen.has(key)) key = `${key}#${i}`;
    seen.add(key);
    picks.push({
      key,
      uri: a.uri,
      width: a.width,
      height: a.height,
      takenAt: exifTakenAtMs(a.exif as Record<string, unknown> | null | undefined),
      pickIndex: i
    });
  });
  return { picks, truncated: Math.max(0, assets.length - kept.length) };
}

async function removePickerCopy(uri: string): Promise<void> {
  const cache = FileSystem.cacheDirectory;
  if (cache && uri.startsWith(cache)) {
    await FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined);
  }
}

/** Upload-sized JPEG under ``Documents/lot-captures/<draft>/`` (picker files can be purged). */
async function copyIntoLot(draftId: string, pick: ImportPick, position: number): Promise<string> {
  const dir = `${FileSystem.documentDirectory}lot-captures/${draftId}/`;
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
  const dest = `${dir}import-${String(position + 1).padStart(2, "0")}-${Date.now()}.jpg`;
  const wide = (pick.width ?? Infinity) > UPLOAD_MAX_EDGE_PX;
  const out = await ImageManipulator.manipulateAsync(
    pick.uri,
    wide ? [{ resize: { width: UPLOAD_MAX_EDGE_PX } }] : [],
    { compress: IMPORT_JPEG_QUALITY, format: ImageManipulator.SaveFormat.JPEG }
  );
  try {
    await FileSystem.moveAsync({ from: out.uri, to: dest });
  } catch {
    await FileSystem.copyAsync({ from: out.uri, to: dest });
    await FileSystem.deleteAsync(out.uri, { idempotent: true }).catch(() => undefined);
  }
  return dest;
}

export type ImportLot = { picks: ImportPick[]; manualSku?: string | null };

export type ImportProgress = { photosDone: number; photos: number; lotsQueued: number; lots: number };

export type ImportOutcome = {
  lotsQueued: number;
  photosCopied: number;
  /** Photos the phone could not open (iCloud-only originals offline, unsupported format). */
  photosSkipped: number;
};

/**
 * Copy each lot into its own ``importing`` draft, then flip it to ``queued`` — lots queue one
 * by one as they finish, so Sync can start on the first while the rest copy. A lot with no
 * readable photo is removed.
 */
export async function importLots(opts: {
  saleId: string;
  saleName: string | null;
  lots: ImportLot[];
  onProgress?: (p: ImportProgress) => void;
  onLotQueued?: () => void;
}): Promise<ImportOutcome> {
  const startedAt = Date.now();
  const photos = opts.lots.reduce((n, l) => n + l.picks.length, 0);
  let photosDone = 0;
  let lotsQueued = 0;
  let photosCopied = 0;
  let photosSkipped = 0;
  const tick = () => opts.onProgress?.({ photosDone, photos, lotsQueued, lots: opts.lots.length });
  tick();

  await mapPool(opts.lots, IMPORT_LOTS_AT_ONCE, async (lot, lotIndex) => {
    const draftId = await createImportDraft({
      saleId: opts.saleId,
      saleName: opts.saleName,
      manualSku: lot.manualSku,
      createdAt: startedAt + lotIndex
    });
    activeImports.add(draftId);
    let copied = 0;
    try {
      for (let i = 0; i < lot.picks.length; i++) {
        const pick = lot.picks[i]!;
        try {
          const uri = await copyIntoLot(draftId, pick, i);
          await addPhotoToDraft(draftId, uri, "image", { capturedAt: importCapturedAt(pick, i, startedAt) });
          copied += 1;
          photosCopied += 1;
          await removePickerCopy(pick.uri);
        } catch (e) {
          if (/no space|ENOSPC|disk full/i.test(e instanceof Error ? e.message : String(e))) throw e;
          photosSkipped += 1;
        }
        photosDone += 1;
        tick();
      }
      if (!copied || !(await finishImportDraft(draftId))) {
        await deleteLotDraft(draftId);
        return;
      }
      lotsQueued += 1;
      tick();
      opts.onLotQueued?.();
    } catch (e) {
      await deleteLotDraft(draftId).catch(() => undefined);
      throw e;
    } finally {
      activeImports.delete(draftId);
    }
  });
  return { lotsQueued, photosCopied, photosSkipped };
}
