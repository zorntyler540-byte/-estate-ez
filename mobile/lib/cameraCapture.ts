import type { CameraPictureOptions } from "expo-camera";

const MAX_DETAIL_CAPTURE_PIXELS = 12_000_000;

/** High-quality JPEG without forcing the native camera into maximum sensor buffers. */
export const MAX_DETAIL_CAPTURE_OPTIONS: CameraPictureOptions = {
  quality: 0.95,
  skipProcessing: false
};

/** Pick the highest-resolution ``WxH`` string at or below the mobile-safe capture cap. */
export function pickDetailPictureSize(sizes: string[]): string | undefined {
  let best: { size: string; pixels: number } | null = null;
  for (const size of sizes) {
    const match = /^(\d+)x(\d+)$/i.exec(size.trim());
    if (!match) continue;
    const pixels = Number(match[1]) * Number(match[2]);
    if (pixels > MAX_DETAIL_CAPTURE_PIXELS) continue;
    if (!best || pixels > best.pixels) best = { size, pixels };
  }
  return best?.size;
}
