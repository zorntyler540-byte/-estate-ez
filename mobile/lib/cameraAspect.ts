import * as ImageManipulator from "expo-image-manipulator";

/** Output photo aspect presets for mobile capture. */
export type CameraAspectId = "full" | "4:3" | "1:1" | "16:9";

export type CameraAspectPreset = {
  id: CameraAspectId;
  label: string;
  /** width / height; null = keep full sensor (no crop). */
  ratio: number | null;
};

export const CAMERA_ASPECT_PRESETS: CameraAspectPreset[] = [
  { id: "full", label: "Full", ratio: null },
  { id: "4:3", label: "4:3", ratio: 4 / 3 },
  { id: "1:1", label: "1:1", ratio: 1 },
  { id: "16:9", label: "16:9", ratio: 16 / 9 }
];

export function aspectPresetById(id: CameraAspectId): CameraAspectPreset {
  return CAMERA_ASPECT_PRESETS.find((p) => p.id === id) ?? CAMERA_ASPECT_PRESETS[0]!;
}

/** Center crop window inside a view of size ``viewW`` × ``viewH`` for the given aspect. */
export function centeredAspectRect(
  viewW: number,
  viewH: number,
  ratio: number | null
): { x: number; y: number; width: number; height: number } {
  if (!viewW || !viewH || ratio == null || ratio <= 0) {
    return { x: 0, y: 0, width: viewW, height: viewH };
  }
  const viewRatio = viewW / viewH;
  if (viewRatio > ratio) {
    // View is wider than target — letterbox left/right.
    const width = viewH * ratio;
    return { x: (viewW - width) / 2, y: 0, width, height: viewH };
  }
  // View is taller than target — letterbox top/bottom.
  const height = viewW / ratio;
  return { x: 0, y: (viewH - height) / 2, width: viewW, height };
}

/** Pixel crop rect for center-cropping an image to ``ratio`` (width/height). */
export function centerCropPixels(
  imageW: number,
  imageH: number,
  ratio: number
): { originX: number; originY: number; width: number; height: number } {
  const w = Math.max(1, Math.floor(imageW));
  const h = Math.max(1, Math.floor(imageH));
  const imageRatio = w / h;
  if (Math.abs(imageRatio - ratio) < 0.01) {
    return { originX: 0, originY: 0, width: w, height: h };
  }
  if (imageRatio > ratio) {
    const width = Math.max(1, Math.floor(h * ratio));
    const originX = Math.max(0, Math.floor((w - width) / 2));
    return { originX, originY: 0, width, height: h };
  }
  const height = Math.max(1, Math.floor(w / ratio));
  const originY = Math.max(0, Math.floor((h - height) / 2));
  return { originX: 0, originY, width: w, height };
}

/**
 * If ``ratio`` is set, center-crop ``uri`` to that aspect and return a new JPEG file URI.
 * Otherwise return the original URI.
 */
export async function maybeCropToAspect(
  uri: string,
  imageW: number | undefined,
  imageH: number | undefined,
  ratio: number | null
): Promise<string> {
  if (ratio == null || ratio <= 0) return uri;
  let w = imageW && imageW > 0 ? imageW : 0;
  let h = imageH && imageH > 0 ? imageH : 0;
  if (!w || !h) {
    // Probe size via a no-op manipulate (returns width/height).
    const probed = await ImageManipulator.manipulateAsync(uri, [], {
      compress: 1,
      format: ImageManipulator.SaveFormat.JPEG
    });
    w = probed.width;
    h = probed.height;
    // If we already re-encoded for probe and still need crop, continue with probed.uri
    // so EXIF orientation is baked consistently.
    uri = probed.uri;
  }
  const crop = centerCropPixels(w, h, ratio);
  if (crop.width >= w && crop.height >= h && crop.originX === 0 && crop.originY === 0) {
    return uri;
  }
  const out = await ImageManipulator.manipulateAsync(uri, [{ crop }], {
    compress: 1,
    format: ImageManipulator.SaveFormat.JPEG
  });
  return out.uri;
}
