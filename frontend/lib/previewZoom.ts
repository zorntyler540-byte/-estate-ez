export const PREVIEW_ZOOM_MIN = 1;
export const PREVIEW_ZOOM_MAX = 4;

export function clampPreviewZoom(n: number): number {
  if (!Number.isFinite(n)) return PREVIEW_ZOOM_MIN;
  return Math.min(PREVIEW_ZOOM_MAX, Math.max(PREVIEW_ZOOM_MIN, n));
}

export function scaleFromPinch(startScale: number, startDist: number, nowDist: number): number {
  if (!(startDist > 0) || !(nowDist > 0)) return clampPreviewZoom(startScale);
  return clampPreviewZoom(startScale * (nowDist / startDist));
}

export function pinchDistance(a: { clientX: number; clientY: number }, b: { clientX: number; clientY: number }): number {
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

/** Mouse / trackpad wheel: scroll up zooms in. */
export function scaleFromWheel(scale: number, deltaY: number): number {
  if (!Number.isFinite(deltaY) || deltaY === 0) return clampPreviewZoom(scale);
  const factor = deltaY < 0 ? 1.12 : 1 / 1.12;
  const next = clampPreviewZoom(scale * factor);
  return next <= 1.02 ? PREVIEW_ZOOM_MIN : next;
}
