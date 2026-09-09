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

/** -1 previous, 1 next, 0 stay — only when not zoomed in. */
export function shouldSwipePreview(scale: number, dx: number, threshold = 56): -1 | 1 | 0 {
  if (scale > 1.05) return 0;
  if (dx > threshold) return -1;
  if (dx < -threshold) return 1;
  return 0;
}

export function pinchDistance(
  a: { pageX: number; pageY: number },
  b: { pageX: number; pageY: number }
): number {
  return Math.hypot(a.pageX - b.pageX, a.pageY - b.pageY);
}
