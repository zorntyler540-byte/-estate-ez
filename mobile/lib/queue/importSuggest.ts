/**
 * Suggested lot splits for a camera-roll import: a blank frame (on the phone) or a tape-roll
 * shot (one server pass). Staff can undo either before the lots are queued.
 */

import * as FileSystem from "expo-file-system/legacy";
import * as ImageManipulator from "expo-image-manipulator";
import * as jpeg from "jpeg-js";

import { apiUrl } from "../config";
import { orderPicks, type ImportPick } from "./importGroups";

const BLANK_EDGE = 0.08;
const HINT_MAX = 80;

function edgeFraction(rgba: Uint8Array, width: number, height: number): number {
  if (width < 8 || height < 8) return 1;
  let strong = 0;
  let total = 0;
  const lum = (i: number) => rgba[i]! * 0.299 + rgba[i + 1]! * 0.587 + rgba[i + 2]! * 0.114;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = (y * width + x) * 4;
      const here = lum(i);
      const right = lum(i + 4);
      const down = lum(i + width * 4);
      if (Math.abs(here - right) > 28 || Math.abs(here - down) > 28) strong += 1;
      total += 1;
    }
  }
  return total ? strong / total : 1;
}

/** Photos that look like a blank sheet or an empty wall. The first frame is never a split. */
export async function blankSplitKeys(picks: readonly ImportPick[]): Promise<string[]> {
  const ordered = orderPicks(picks, "one");
  const keys: string[] = [];
  for (let i = 1; i < ordered.length; i++) {
    const pick = ordered[i]!;
    try {
      const out = await ImageManipulator.manipulateAsync(pick.uri, [{ resize: { width: 64 } }], {
        compress: 0.7,
        format: ImageManipulator.SaveFormat.JPEG,
        base64: true
      });
      if (!out.base64) continue;
      const bytes = Uint8Array.from(atob(out.base64), (c) => c.charCodeAt(0));
      const decoded = jpeg.decode(bytes, { useTArray: true, formatAsRGBA: true });
      if (edgeFraction(decoded.data, decoded.width, decoded.height) <= BLANK_EDGE) keys.push(pick.key);
    } catch {
      // A frame we cannot read stays in its lot. Staff can still tap Split.
    }
  }
  return keys;
}

/**
 * Server pass over small copies, in camera order. Returns keys that should start a lot
 * (tape roll, including beside the items, or a blank divider). Null when the check cannot run.
 */
export async function tapeSplitKeys(picks: readonly ImportPick[], token: string): Promise<string[] | null> {
  const ordered = orderPicks(picks, "one").slice(0, HINT_MAX);
  if (ordered.length < 2) return [];
  const body = new FormData();
  const temps: string[] = [];
  try {
    for (let i = 0; i < ordered.length; i++) {
      const pick = ordered[i]!;
      const out = await ImageManipulator.manipulateAsync(pick.uri, [{ resize: { width: 512 } }], {
        compress: 0.7,
        format: ImageManipulator.SaveFormat.JPEG
      });
      temps.push(out.uri);
      body.append("files", { uri: out.uri, name: `${String(i).padStart(4, "0")}.jpg`, type: "image/jpeg" } as unknown as Blob);
    }
    const res = await fetch(apiUrl("/mobile/import-hints"), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { splits?: number[] };
    return (data.splits || []).map((i) => ordered[i]?.key).filter((k): k is string => Boolean(k));
  } catch {
    return null;
  } finally {
    await Promise.all(temps.map((uri) => FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined)));
  }
}
