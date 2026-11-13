/** Recording limits for lot clips. Keep the byte cap in sync with ``backend/app/catalog_limits.py``. */

import { MAX_VIDEO_DURATION_SEC } from "./mediaKind";

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

export type VideoRecordOptions = {
  maxDuration: number;
  maxFileSize: number;
  /** iOS only. Android records from the CameraView quality and bitrate props. */
  codec?: typeof VIDEO_CODEC;
};

/**
 * iOS applies ``VIDEO_BITRATE`` only when a codec is passed to ``recordAsync``.
 * That field is iOS-only, so Android gets the duration and file caps without it.
 */
export function videoRecordOptions(platform: string): VideoRecordOptions {
  const options: VideoRecordOptions = {
    maxDuration: MAX_VIDEO_DURATION_SEC,
    maxFileSize: VIDEO_MAX_FILE_BYTES
  };
  if (platform === "ios") options.codec = VIDEO_CODEC;
  return options;
}

export const MICROPHONE_OFF_NOTE =
  "Microphone is off — this clip records without sound. Allow the microphone in Settings to include audio.";

/** Android must be granted RECORD_AUDIO before a clip can include sound. A denial still keeps the take, silent. */
export function microphoneRecordPlan(granted: boolean): { mute: boolean; note: string | null } {
  if (granted) return { mute: false, note: null };
  return { mute: true, note: MICROPHONE_OFF_NOTE };
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
