import type { CameraPictureOptions } from "expo-camera";

/** Full sensor JPEG; server applies EXIF orientation on upload. */
export const MAX_DETAIL_CAPTURE_OPTIONS: CameraPictureOptions = {
  quality: 1,
  skipProcessing: true
};

/** Pick the highest-resolution ``WxH`` string from expo-camera. */
export function pickLargestPictureSize(sizes: string[]): string | undefined {
  let best: { size: string; pixels: number } | null = null;
  for (const size of sizes) {
    const match = /^(\d+)x(\d+)$/i.exec(size.trim());
    if (!match) continue;
    const pixels = Number(match[1]) * Number(match[2]);
    if (!best || pixels > best.pixels) best = { size, pixels };
  }
  return best?.size;
}
