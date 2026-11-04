/** Recording limits for lot clips. Keep the byte cap in sync with ``backend/app/catalog_limits.py``. */

export const MAX_VIDEO_UPLOAD_BYTES = 80 * 1024 * 1024;
/** 720p is plenty for a condition walk-around and keeps 45s clips far below the upload cap. */
export const VIDEO_QUALITY = "720p" as const;
/** ~5 Mbps × 45s ≈ 28 MB. iOS only applies it when a codec is passed to ``recordAsync``. */
export const VIDEO_BITRATE = 5_000_000;
export const VIDEO_CODEC = "avc1" as const;
/** Native recorder stops here, leaving headroom under the server cap for container overhead. */
export const VIDEO_MAX_FILE_BYTES = 72 * 1024 * 1024;

function mb(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(1);
}

/** Why a recorded clip cannot be kept, or null when it is fine to save. */
export function videoSizeProblem(bytes: number | null | undefined): string | null {
  const size = Number(bytes);
  if (!Number.isFinite(size) || size <= 0) {
    return "The camera saved an empty video. Record it again.";
  }
  if (size > MAX_VIDEO_UPLOAD_BYTES) {
    return `That clip is ${mb(size)} MB — the website takes up to ${mb(MAX_VIDEO_UPLOAD_BYTES)} MB. Record a shorter clip.`;
  }
  return null;
}
