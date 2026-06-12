"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Database,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  ImagePlus,
  Minus,
  Pencil,
  Plus,
  RotateCcw,
  RotateCw,
  Trash2,
  X
} from "lucide-react";
import { LotThumbnail } from "../../components/LotThumbnail";
import { AppShell } from "../../components/AppShell";
import { apiFetch, apiUploadFiles, API_BASE, parseApiErrorMessage } from "../../lib/api";
import {
  clearStagingDraft,
  loadStagingDraft,
  saveStagingDraft,
  stagingDraftSupported
} from "../../lib/stagingDraft";

const SALE_STORAGE_KEY = "ct_selected_sale_id";
/** Staged upload waits for Gemini per-photo; upload box clears after all frames are analyzed (not full job done). */
const STAGING_AWAIT_GEMINI_JOB_KEY = "ct_staging_await_gemini_job";
const STAGING_AWAIT_GEMINI_TOTAL_KEY = "ct_staging_await_gemini_total";
const STAGING_AWAIT_GEMINI_SEEN_KEY = "ct_staging_await_gemini_seen";

/** Saved when opening lot Details; restored after ``← Back to dashboard`` on the lot page. */
const DASHBOARD_SCROLL_Y_KEY = "ct_dashboard_scroll_y";
const DASHBOARD_SCROLL_SAVED_AT_KEY = "ct_dashboard_scroll_saved_at";
const DASHBOARD_SCROLL_RESTORE_PENDING_KEY = "ct_dashboard_scroll_restore_pending";
const DASHBOARD_SCROLL_MAX_AGE_MS = 12 * 60 * 1000;

function rememberDashboardScrollBeforeLotDetails() {
  try {
    sessionStorage.setItem(DASHBOARD_SCROLL_Y_KEY, String(window.scrollY));
    sessionStorage.setItem(DASHBOARD_SCROLL_SAVED_AT_KEY, String(Date.now()));
  } catch {
    /* private mode */
  }
}

function isMobileDashboardLayout(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(max-width: 1280px)").matches;
}

function scrollStagingUploadBoxIntoView(): boolean {
  const el = document.getElementById("staging-upload-box");
  if (!el) return false;
  el.scrollIntoView({
    behavior: "smooth",
    block: isMobileDashboardLayout() ? "start" : "nearest",
    inline: "nearest"
  });
  return true;
}

/** Retry until React mounts the staging box (hidden on mobile until photos are staged). */
function scheduleScrollStagingUploadBoxIntoView() {
  if (typeof window === "undefined") return;
  const tryScroll = (attempt: number) => {
    window.requestAnimationFrame(() => {
      if (scrollStagingUploadBoxIntoView()) return;
      if (attempt < 12) {
        window.setTimeout(() => tryScroll(attempt + 1), 60);
      }
    });
  };
  tryScroll(0);
}

/**
 * Max files per multipart POST. Smaller chunks shorten each request (less “stuck” behind proxies)
 * while still batching enough for large estate folders.
 */
const UPLOAD_FILE_CHUNK = 48;

function uploadProgressFromBytes(prefix: string, loaded: number, total: number | null): string {
  const loMb = (n: number) => (n / (1024 * 1024)).toFixed(1);
  if (total != null && total > 0) {
    const pct = Math.min(99, Math.round((100 * loaded) / total));
    return `${prefix} ${pct}% (${loMb(loaded)} / ${loMb(total)} MB)`;
  }
  return `${prefix} ${loMb(loaded)} MB sent…`;
}

/**
 * Per-listing catalog photo cap (null = none). Keep in sync with ``backend/app/catalog_limits.py``.
 * Re-enable (e.g. 40) when CTBids limits should apply again.
 */
const CTBIDS_MAX_PHOTOS_PER_LOT: number | null = null;

/** UI zoom range: 1 = wide; higher = closer. Uses optical zoom when the device supports it. */
const LOT_CAMERA_ZOOM_MIN = 1;
const LOT_CAMERA_ZOOM_MAX = 4;
const LOT_CAMERA_ZOOM_STEP = 0.25;
const LOT_CAMERA_JPEG_QUALITY = 0.92;

const LOT_CAMERA_MEDIA: MediaStreamConstraints = {
  video: {
    facingMode: { ideal: "environment" },
    width: { ideal: 3840 },
    height: { ideal: 2160 }
  },
  audio: false
};

type VideoTrackCaps = MediaTrackCapabilities & {
  focusMode?: string[];
  pointsOfInterest?: boolean;
  zoom?: { min: number; max: number; step?: number };
};

type LotCameraZoomCap = { min: number; max: number; step?: number };

function clampLotCameraUiZoom(z: number): number {
  return Math.round(Math.min(LOT_CAMERA_ZOOM_MAX, Math.max(LOT_CAMERA_ZOOM_MIN, z)) * 100) / 100;
}

/** Optical zoom span used by the UI (not the device's full telephoto range). */
const LOT_CAMERA_HARDWARE_SPAN_MAX = 3;

function effectiveHardwareZoomRange(cap: LotCameraZoomCap): LotCameraZoomCap | null {
  // Some devices report min > 1 (already zoomed); optical +/- is jumpy there — use digital only.
  if (cap.min > 1.2) return null;
  const min = cap.min;
  let max = Math.min(cap.max, min + LOT_CAMERA_HARDWARE_SPAN_MAX);
  if (cap.step && cap.step > 0) {
    max = min + Math.floor((max - min) / cap.step) * cap.step;
  }
  if (max <= min + 0.01) return null;
  return { min, max, step: cap.step };
}

function readTrackZoom(track: MediaStreamTrack): number | null {
  const z = (track.getSettings?.() as { zoom?: number } | undefined)?.zoom;
  return typeof z === "number" && Number.isFinite(z) ? z : null;
}

function mapUiZoomToHardware(uiZoom: number, cap: LotCameraZoomCap): number {
  const eff = effectiveHardwareZoomRange(cap);
  if (!eff) return cap.min;
  const span = LOT_CAMERA_ZOOM_MAX - LOT_CAMERA_ZOOM_MIN;
  const t = span > 0 ? (uiZoom - LOT_CAMERA_ZOOM_MIN) / span : 0;
  let hz = eff.min + t * (eff.max - eff.min);
  if (eff.step && eff.step > 0) {
    hz = Math.round(hz / eff.step) * eff.step;
  }
  return Math.min(eff.max, Math.max(eff.min, Math.round(hz * 100) / 100));
}

function mapHardwareToUiZoom(hz: number, cap: LotCameraZoomCap): number {
  const eff = effectiveHardwareZoomRange(cap);
  if (!eff) return LOT_CAMERA_ZOOM_MIN;
  const span = LOT_CAMERA_ZOOM_MAX - LOT_CAMERA_ZOOM_MIN;
  const t = (hz - eff.min) / (eff.max - eff.min);
  return clampLotCameraUiZoom(LOT_CAMERA_ZOOM_MIN + t * span);
}

/** One +/- tap on hardware — small fraction of the capped optical span. */
function hardwareZoomNudgeAmount(cap: LotCameraZoomCap): number {
  const eff = effectiveHardwareZoomRange(cap);
  if (!eff) return 0;
  const span = eff.max - eff.min;
  const uiSteps = (LOT_CAMERA_ZOOM_MAX - LOT_CAMERA_ZOOM_MIN) / LOT_CAMERA_ZOOM_STEP;
  const perTap = span / Math.max(4, uiSteps);
  if (eff.step && eff.step > 0 && eff.step < perTap * 1.5) {
    return eff.step;
  }
  return Math.max(0.08, Math.round(perTap * 100) / 100);
}

function readLotCameraZoomCap(track: MediaStreamTrack): LotCameraZoomCap | null {
  const raw = track.getCapabilities?.() as VideoTrackCaps | undefined;
  const z = raw?.zoom;
  if (!z || typeof z.min !== "number" || typeof z.max !== "number" || z.max <= z.min) return null;
  return { min: z.min, max: z.max, step: z.step };
}

/** Ask for the rear camera at the highest resolution the device exposes. */
async function configureLotCameraTrack(track: MediaStreamTrack): Promise<LotCameraZoomCap | null> {
  const caps = track.getCapabilities?.();
  if (!caps || !track.applyConstraints) return readLotCameraZoomCap(track);

  const width = caps.width;
  const height = caps.height;
  const target: MediaTrackConstraints = { facingMode: { ideal: "environment" } };
  if (width && typeof width === "object" && "max" in width && typeof width.max === "number") {
    target.width = { ideal: width.max };
  } else {
    target.width = { ideal: 3840 };
  }
  if (height && typeof height === "object" && "max" in height && typeof height.max === "number") {
    target.height = { ideal: height.max };
  } else {
    target.height = { ideal: 2160 };
  }

  try {
    await track.applyConstraints(target);
  } catch {
    try {
      await track.applyConstraints({
        width: { ideal: 1920 },
        height: { ideal: 1080 }
      });
    } catch {
      /* keep default stream size */
    }
  }
  return readLotCameraZoomCap(track);
}

async function applyHardwareZoomValue(
  track: MediaStreamTrack,
  hz: number,
  cap: LotCameraZoomCap
): Promise<boolean> {
  if (!track.applyConstraints) return false;
  const eff = effectiveHardwareZoomRange(cap);
  if (!eff) return false;
  const clamped = Math.min(eff.max, Math.max(eff.min, Math.round(hz * 100) / 100));
  try {
    await track.applyConstraints({ advanced: [{ zoom: clamped }] } as unknown as MediaTrackConstraints);
    return true;
  } catch {
    try {
      await track.applyConstraints({ zoom: clamped } as unknown as MediaTrackConstraints);
      return true;
    } catch {
      return false;
    }
  }
}

async function applyHardwareZoomOnTrack(
  track: MediaStreamTrack,
  cap: LotCameraZoomCap,
  uiZoom: number
): Promise<boolean> {
  return applyHardwareZoomValue(track, mapUiZoomToHardware(uiZoom, cap), cap);
}

/** Digital crop from a full-resolution still (or video frame); 1 = no crop. */
function drawZoomedFrameToCanvas(
  ctx: CanvasRenderingContext2D,
  source: CanvasImageSource,
  w: number,
  h: number,
  uiZoom: number
): void {
  const z = Math.max(LOT_CAMERA_ZOOM_MIN, uiZoom);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  if (z <= LOT_CAMERA_ZOOM_MIN + 0.001) {
    ctx.drawImage(source, 0, 0, w, h, 0, 0, w, h);
    return;
  }
  const sw = w / z;
  const sh = h / z;
  const sx = (w - sw) / 2;
  const sy = (h - sh) / 2;
  ctx.drawImage(source, sx, sy, sw, sh, 0, 0, w, h);
}

function canvasToJpegBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", LOT_CAMERA_JPEG_QUALITY);
  });
}

function readScreenOrientationAngle(): number {
  if (typeof screen !== "undefined" && screen.orientation?.angle != null) {
    return ((screen.orientation.angle % 360) + 360) % 360;
  }
  const legacy = typeof window !== "undefined" ? (window as Window & { orientation?: number }).orientation : undefined;
  if (typeof legacy === "number" && Number.isFinite(legacy)) {
    return ((legacy % 360) + 360) % 360;
  }
  if (typeof window !== "undefined") {
    return window.innerWidth > window.innerHeight ? 90 : 0;
  }
  return 0;
}

/** True when the device is held in either landscape orientation. */
function isScreenLandscape(): boolean {
  const angle = readScreenOrientationAngle();
  return angle === 90 || angle === 270;
}

/**
 * Rotate live frames so preview/capture match how the user holds the phone (iOS back camera).
 * In landscape both handednesses need the same correction — mixing +90/-90 or 0/180 flips one side upside down.
 */
function lotCameraFrameRotationDeg(video: HTMLVideoElement): number {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (vw < 2 || vh < 2) return 0;
  if (!isScreenLandscape()) return 0;

  const framePortrait = vh > vw * 1.05;
  if (framePortrait) {
    return 90;
  }
  return 0;
}

/**
 * Rotation baked into saved JPEGs. iOS applies its own display correction on the ``<video>`` element,
 * so canvas ``drawImage(video)`` / ``takePhoto`` need the opposite quarter-turn from CSS preview.
 */
function lotCameraCaptureRotationDeg(video: HTMLVideoElement): number {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (vw < 2 || vh < 2) return 0;
  if (!isScreenLandscape()) return 0;

  const framePortrait = vh > vw * 1.05;
  if (framePortrait) {
    return -90;
  }
  return 0;
}

function lotCameraPreviewTransform(
  video: HTMLVideoElement | null,
  uiZoom: number,
  hardwareZoom: boolean
): string {
  const rot = video ? lotCameraFrameRotationDeg(video) : 0;
  const z = hardwareZoom ? 1 : uiZoom;
  if (rot === 90 || rot === -90) {
    const vw = video?.videoWidth ?? 1;
    const vh = video?.videoHeight ?? 1;
    const fill = Math.max(vw / vh, vh / vw);
    return `rotate(${rot}deg) scale(${z * fill})`;
  }
  if (rot !== 0) return `rotate(${rot}deg) scale(${z})`;
  return `scale(${z})`;
}

function drawOrientedVideoFrameToCanvas(
  ctx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  rotationDeg: number,
  uiZoom: number,
  useDigitalCrop: boolean
): void {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const rot = ((rotationDeg % 360) + 360) % 360;
  const swapped = rot === 90 || rot === 270;
  const cw = swapped ? vh : vw;
  const ch = swapped ? vw : vh;
  const canvas = ctx.canvas;
  canvas.width = cw;
  canvas.height = ch;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.save();
  ctx.translate(cw / 2, ch / 2);
  ctx.rotate((rotationDeg * Math.PI) / 180);

  const z = Math.max(LOT_CAMERA_ZOOM_MIN, uiZoom);
  if (useDigitalCrop && z > LOT_CAMERA_ZOOM_MIN + 0.001) {
    const sw = vw / z;
    const sh = vh / z;
    const sx = (vw - sw) / 2;
    const sy = (vh - sh) / 2;
    ctx.drawImage(video, sx, sy, sw, sh, -vw / 2, -vh / 2, vw, vh);
  } else {
    ctx.drawImage(video, -vw / 2, -vh / 2, vw, vh);
  }
  ctx.restore();
}

async function normalizeCaptureFileOrientation(file: File): Promise<File | null> {
  const decoded = await decodeImageForCanvas(file);
  if (!decoded) return null;
  const { source, width, height, release } = decoded;
  if (width < 2 || height < 2) {
    release?.();
    return null;
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    release?.();
    return null;
  }
  ctx.drawImage(source, 0, 0, width, height);
  release?.();
  const blob = await canvasToJpegBlob(canvas);
  if (!blob) return null;
  return new File([blob], file.name, { type: "image/jpeg" });
}

/** Short label for the zoom row (optical vs digital vs 1×). */
function lotCameraZoomModeLabel(
  uiZoom: number,
  hardwareActive: boolean,
  opticalAvailable: boolean
): string {
  if (uiZoom <= LOT_CAMERA_ZOOM_MIN + 0.001) {
    return opticalAvailable ? "1× — optical zoom available" : "1× — no zoom";
  }
  if (hardwareActive) return "Optical zoom (camera lens)";
  return "Digital zoom — move closer for sharpest detail";
}

type LotCameraZoomControlsProps = {
  variant: "inline" | "actions";
  uiZoom: number;
  hardwareActive: boolean;
  opticalAvailable: boolean;
  onNudge: (direction: -1 | 1) => void;
};

function LotCameraZoomControls({
  variant,
  uiZoom,
  hardwareActive,
  opticalAvailable,
  onNudge
}: LotCameraZoomControlsProps) {
  const compact = variant === "actions";
  return (
    <div
      className={`ct-row ct-lot-camera-zoom-row ct-lot-camera-zoom-row--${variant}`}
      style={{
        marginTop: compact ? 0 : "0.5rem",
        flexWrap: "wrap",
        gap: compact ? "0.35rem" : "0.45rem",
        alignItems: "center",
        justifyContent: compact ? "center" : "space-between"
      }}
    >
      {compact ? (
        <>
          <button
            type="button"
            className="ct-btn ct-btn--ghost"
            aria-label="Zoom out"
            disabled={uiZoom <= LOT_CAMERA_ZOOM_MIN + 0.001}
            onClick={() => onNudge(-1)}
          >
            <Minus size={20} aria-hidden />
          </button>
          <span className="ct-lot-camera-zoom-actions-label" aria-live="polite">
            Zoom {uiZoom.toFixed(2)}×
          </span>
          <button
            type="button"
            className="ct-btn ct-btn--ghost"
            aria-label="Zoom in"
            disabled={uiZoom >= LOT_CAMERA_ZOOM_MAX - 0.001}
            onClick={() => onNudge(1)}
          >
            <Plus size={20} aria-hidden />
          </button>
        </>
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
            <span className="ct-muted" style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.8)" }}>
              Zoom {uiZoom.toFixed(2)}×
            </span>
            <span
              className={`ct-lot-camera-zoom-mode${
                hardwareActive && uiZoom > LOT_CAMERA_ZOOM_MIN + 0.001
                  ? " ct-lot-camera-zoom-mode--optical"
                  : uiZoom > LOT_CAMERA_ZOOM_MIN + 0.001
                    ? " ct-lot-camera-zoom-mode--digital"
                    : ""
              }`}
              role="status"
              aria-live="polite"
            >
              {lotCameraZoomModeLabel(uiZoom, hardwareActive, opticalAvailable)}
            </span>
          </div>
          <div className="ct-row" style={{ gap: 6, alignItems: "center" }}>
            <button
              type="button"
              className="ct-btn ct-btn--ghost"
              aria-label="Zoom out"
              disabled={uiZoom <= LOT_CAMERA_ZOOM_MIN + 0.001}
              onClick={() => onNudge(-1)}
              style={{ minWidth: 44, minHeight: 44, padding: 0, color: "#fff" }}
            >
              <Minus size={22} aria-hidden />
            </button>
            <button
              type="button"
              className="ct-btn ct-btn--ghost"
              aria-label="Zoom in"
              disabled={uiZoom >= LOT_CAMERA_ZOOM_MAX - 0.001}
              onClick={() => onNudge(1)}
              style={{ minWidth: 44, minHeight: 44, padding: 0, color: "#fff" }}
            >
              <Plus size={22} aria-hidden />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function captureVideoFrameBlob(
  video: HTMLVideoElement,
  uiZoom: number,
  useDigitalCrop: boolean
): Promise<Blob | null> {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (w < 2 || h < 2) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  const rotationDeg = lotCameraCaptureRotationDeg(video);
  drawOrientedVideoFrameToCanvas(ctx, video, rotationDeg, uiZoom, useDigitalCrop);
  return canvasToJpegBlob(canvas);
}

/**
 * iOS Safari can hand back camera-capture File handles that become unreadable after reopening
 * the capture sheet. Clone bytes immediately so staged thumbnails/uploads stay valid.
 */
async function stabilizeCapturedFiles(files: File[]): Promise<File[]> {
  const out: File[] = [];
  for (const f of files) {
    try {
      const bytes = await f.arrayBuffer();
      const type = (f.type || "image/jpeg").trim() || "image/jpeg";
      const fallbackExt =
        type === "image/png"
          ? "png"
          : type === "image/webp"
            ? "webp"
            : type === "image/heic"
              ? "heic"
              : type === "image/heif"
                ? "heif"
                : "jpg";
      const hasExt = /\.[a-z0-9]{2,6}$/i.test(f.name || "");
      const baseName = (f.name || "").trim() || `capture-${Date.now()}`;
      const safeName = hasExt ? baseName : `${baseName}.${fallbackExt}`;
      out.push(
        new File([bytes], safeName, {
          type,
          lastModified: f.lastModified || Date.now()
        })
      );
    } catch {
      out.push(f);
    }
  }
  return out;
}

function lotCameraPinchDistance(e: TouchEvent): number {
  if (e.touches.length < 2) return 0;
  const a = e.touches[0];
  const b = e.touches[1];
  if (!a || !b) return 0;
  const dx = a.clientX - b.clientX;
  const dy = a.clientY - b.clientY;
  return Math.hypot(dx, dy);
}

/** Reused for a short shutter-style beep after each create-one-lot snap. */
let lotCameraShutterAudioCtx: AudioContext | null = null;

function playLotCameraShutterSound() {
  try {
    const w = window as Window & typeof globalThis & { webkitAudioContext?: typeof AudioContext };
    const AC = window.AudioContext ?? w.webkitAudioContext;
    if (!AC) return;
    if (!lotCameraShutterAudioCtx || lotCameraShutterAudioCtx.state === "closed") {
      lotCameraShutterAudioCtx = new AC();
    }
    const ctx = lotCameraShutterAudioCtx;
    const run = () => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.connect(gain);
      gain.connect(ctx.destination);
      const t = ctx.currentTime;
      osc.frequency.setValueAtTime(920, t);
      osc.frequency.exponentialRampToValueAtTime(240, t + 0.055);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.linearRampToValueAtTime(0.065, t + 0.002);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.075);
      osc.start(t);
      osc.stop(t + 0.08);
    };
    void (ctx.state === "suspended" ? ctx.resume() : Promise.resolve()).then(run);
  } catch {
    /* autoplay policy, private mode, or no Web Audio */
  }
}

function pulseLotCameraHaptic() {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate([14, 28, 14]);
    }
  } catch {
    /* ignore */
  }
}

/** Unlock Web Audio after the user opens the camera (helps iOS play the shutter on later snaps). */
function primeLotCameraAudio() {
  try {
    const w = window as Window & typeof globalThis & { webkitAudioContext?: typeof AudioContext };
    const AC = window.AudioContext ?? w.webkitAudioContext;
    if (!AC) return;
    if (!lotCameraShutterAudioCtx || lotCameraShutterAudioCtx.state === "closed") {
      lotCameraShutterAudioCtx = new AC();
    }
    void lotCameraShutterAudioCtx.resume();
  } catch {
    /* ignore */
  }
}

/** Instant tap feedback (haptic + white flash) — visible even when sound is muted. */
function triggerLotCameraSnapFeedbackImmediate() {
  pulseLotCameraHaptic();
  playLotCameraShutterSound();
}

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const u = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(u);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(u);
      reject(new Error("Could not load image"));
    };
    img.src = u;
  });
}

/**
 * Decode a file for canvas drawing with phone/camera EXIF orientation applied when possible.
 * Plain ``Image`` + ``drawImage`` often ignores EXIF, so saved JPEGs could stay sideways; this matches
 * what the server already does via Pillow ``exif_transpose`` on upload.
 */
async function decodeImageForCanvas(
  file: File
): Promise<{ source: CanvasImageSource; width: number; height: number; release?: () => void } | null> {
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      return {
        source: bmp,
        width: bmp.width,
        height: bmp.height,
        release: () => {
          try {
            bmp.close();
          } catch {
            /* ignore */
          }
        }
      };
    } catch {
      /* older engines or odd formats — fall back */
    }
  }
  try {
    const img = await loadImageFromFile(file);
    const width = img.naturalWidth;
    const height = img.naturalHeight;
    return { source: img, width, height };
  } catch {
    return null;
  }
}

/** Basic tone controls; values are CSS ``filter`` percentages (100 = unchanged). */
type RasterizeBasicAdjust = {
  brightnessPct: number;
  contrastPct: number;
  saturationPct: number;
};

const RASTER_ADJUST_DEFAULT: RasterizeBasicAdjust = {
  brightnessPct: 100,
  contrastPct: 100,
  saturationPct: 100
};

function clampRasterAdjust(a: RasterizeBasicAdjust): RasterizeBasicAdjust {
  return {
    brightnessPct: Math.max(50, Math.min(150, Math.round(a.brightnessPct))),
    contrastPct: Math.max(50, Math.min(150, Math.round(a.contrastPct))),
    saturationPct: Math.max(0, Math.min(150, Math.round(a.saturationPct)))
  };
}

function rasterAdjustCssFilter(a: RasterizeBasicAdjust): string {
  const c = clampRasterAdjust(a);
  return `brightness(${c.brightnessPct}%) contrast(${c.contrastPct}%) saturate(${c.saturationPct}%)`;
}

function isRasterAdjustDefault(a: RasterizeBasicAdjust): boolean {
  return (
    a.brightnessPct === 100 && a.contrastPct === 100 && a.saturationPct === 100
  );
}

/**
 * Uniform edge trim (0–35, same as CSS ``inset`` % on each side) then 0/90/180/270° rotation;
 * optional brightness/contrast/saturation via canvas ``filter``; re-encode as JPEG.
 */
async function rasterizeLotCameraEdit(
  file: File,
  rotationDeg: number,
  edgeTrimPct: number,
  adjust: RasterizeBasicAdjust = RASTER_ADJUST_DEFAULT
): Promise<File | null> {
  const trim = Math.max(0, Math.min(35, edgeTrimPct));
  const rot = (((rotationDeg % 360) + 360) % 360) as 0 | 90 | 180 | 270;
  if (rot !== 0 && rot !== 90 && rot !== 180 && rot !== 270) return null;

  const decoded = await decodeImageForCanvas(file);
  if (!decoded) return null;
  const { source, width: w0, height: h0, release } = decoded;
  if (w0 < 2 || h0 < 2) {
    release?.();
    return null;
  }

  const sx = Math.floor((w0 * trim) / 100);
  const sy = Math.floor((h0 * trim) / 100);
  const sw = Math.floor(w0 - (2 * w0 * trim) / 100);
  const sh = Math.floor(h0 - (2 * h0 * trim) / 100);
  if (sw < 2 || sh < 2) {
    release?.();
    return null;
  }

  const c1 = document.createElement("canvas");
  c1.width = sw;
  c1.height = sh;
  const g1 = c1.getContext("2d");
  if (!g1) {
    release?.();
    return null;
  }
  g1.drawImage(source, sx, sy, sw, sh, 0, 0, sw, sh);

  const rad = (rot * Math.PI) / 180;
  const outW = rot % 180 === 0 ? sw : sh;
  const outH = rot % 180 === 0 ? sh : sw;
  const c2 = document.createElement("canvas");
  c2.width = outW;
  c2.height = outH;
  const g2 = c2.getContext("2d");
  if (!g2) {
    release?.();
    return null;
  }
  if (!isRasterAdjustDefault(adjust)) {
    g2.filter = rasterAdjustCssFilter(adjust);
  }
  g2.translate(outW / 2, outH / 2);
  g2.rotate(rad);
  g2.imageSmoothingEnabled = true;
  g2.imageSmoothingQuality = "high";
  g2.drawImage(c1, -sw / 2, -sh / 2);

  const baseName = (file.name || "lot.jpg").replace(/\.[^.]+$/i, "") || "lot";
  return new Promise((resolve) => {
    c2.toBlob(
      (blob) => {
        release?.();
        if (!blob) {
          resolve(null);
          return;
        }
        resolve(new File([blob], `${baseName}.jpg`, { type: "image/jpeg" }));
      },
      "image/jpeg",
      0.92
    );
  });
}

function readStoredToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem("token") || "";
}

function readStoredSaleId(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(SALE_STORAGE_KEY) || "";
}

function isAuthFailureMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("invalid authentication") ||
    m.includes("401") ||
    m.includes("unauthorized")
  );
}

/** One retry helps flaky mobile / wake-from-sleep where the first ``fetch`` throws. */
async function withNetworkRetry<T>(fn: () => Promise<T>, attempts = 2): Promise<T> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (e) {
      last = e;
      const msg = e instanceof Error ? e.message : "";
      const maybeTransient =
        /failed to fetch|networkerror|network request failed|load failed|aborted/i.test(msg) ||
        (e instanceof TypeError && !msg);
      if (!maybeTransient || i === attempts - 1) throw e;
      await new Promise((r) => setTimeout(r, 280 + i * 220));
    }
  }
  throw last;
}

type Sale = {
  id: string;
  name: string;
  closed_at: string | null;
  created_at: string;
};

type AdminAccount = {
  id: string;
  name: string;
  owner_email?: string | null;
  plan_code: string;
  is_active: boolean;
  created_at: string;
  user_count: number;
  sale_count: number;
  lot_count: number;
  job_count: number;
};

type AdminUsageAccount = {
  account_id: string;
  account_name: string;
  jobs_started: number;
  jobs_completed: number;
  jobs_failed: number;
  photos_uploaded: number;
  lots_created: number;
  avg_photos_per_job: number;
  avg_job_duration_seconds: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_total_cost_usd: number;
};

type AdminUsageSummary = {
  since_days: number;
  total_accounts: number;
  totals: AdminUsageAccount;
  by_account: AdminUsageAccount[];
};

type Lot = {
  id: string;
  sale_id: string;
  sku: string | null;
  auction_title: string;
  is_completed: boolean;
  completed_at: string | null;
  starting_price: number | null;
  condition_text: string;
  photo_count: number;
  has_thumbnail: boolean;
  created_at: string;
  sku_needs_review: boolean;
  many_photos_review: boolean;
  sku_duplicate_in_sale: boolean;
  sku_text_mismatch: boolean;
};

type LotPhotoHealthRow = {
  lot_id: string;
  sku: string | null;
  auction_title: string;
  photo_count: number;
  linked_photo_count: number;
  missing_count: number;
};

function photoHealthMapFromResponse(data: unknown): Record<string, number> {
  if (!data || typeof data !== "object" || !("lots" in data)) return {};
  const rows = (data as { lots: unknown }).lots;
  if (!Array.isArray(rows)) return {};
  const m: Record<string, number> = {};
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const r = row as LotPhotoHealthRow;
    if (typeof r.lot_id === "string" && typeof r.missing_count === "number" && r.missing_count > 0) {
      m[r.lot_id] = r.missing_count;
    }
  }
  return m;
}

/** One inventory row streamed from ``GET /jobs/{id}/events`` (``lot_ready``). */
type LiveLotRowPayload = Lot;

function normalizeStreamedLot(row: LiveLotRowPayload): Lot {
  return {
    id: row.id,
    sale_id: row.sale_id ?? "",
    sku: row.sku ?? null,
    sku_needs_review: Boolean(row.sku_needs_review),
    many_photos_review: Boolean(row.many_photos_review),
    sku_duplicate_in_sale: Boolean(
      (row as { sku_duplicate_in_sale?: unknown }).sku_duplicate_in_sale
    ),
    sku_text_mismatch: Boolean((row as { sku_text_mismatch?: unknown }).sku_text_mismatch),
    auction_title: row.auction_title ?? "",
    is_completed: Boolean(row.is_completed),
    completed_at: row.completed_at ?? null,
    condition_text: row.condition_text ?? "",
    starting_price: row.starting_price ?? null,
    photo_count: typeof row.photo_count === "number" ? row.photo_count : Number(row.photo_count) || 0,
    has_thumbnail: Boolean(row.has_thumbnail),
    created_at: row.created_at ?? ""
  };
}

function formatLotPrice(price: number | null): string {
  if (price == null || Number.isNaN(price)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2
  }).format(price);
}

function lotSkuBadgeClass(lot: Lot): string {
  if (lot.sku_needs_review || !lot.sku) return "ct-lot-sku-badge ct-lot-sku-badge--review";
  return "ct-lot-sku-badge ct-lot-sku-badge--ok";
}

function lotSkuCellTitle(lot: Lot): string | undefined {
  if (lot.many_photos_review) {
    return "Unusually many photos for this sale — confirm images belong to this listing.";
  }
  if (lot.sku_needs_review || !lot.sku) {
    return "Verify handwritten lot code on colored estate tape (or enter manually on the lot page).";
  }
  if (lot.sku_duplicate_in_sale) return "Duplicate SKU in this sale.";
  if (lot.sku_text_mismatch) return "Listing text may reference a different lot number.";
  return undefined;
}

type Job = {
  id: string;
  user_id: string;
  sale_id: string;
  status: string;
  input_folder: string;
  created_at: string;
  error_message?: string;
  inherit_sku_from_subfolder?: boolean;
  single_lot_mode?: boolean;
  single_lot_manual_sku?: string | null;
};

function jobFromUploadResponse(data: unknown): Job {
  if (!data || typeof data !== "object") {
    throw new Error("Upload finished but the server did not return a job — check the Jobs list or try again.");
  }
  const id = (data as Job).id;
  if (typeof id !== "string" || !id.trim()) {
    throw new Error("Upload finished but the server did not return a job id — check the Jobs list or try again.");
  }
  return data as Job;
}

/** Short label for job folder / upload batch (avoids full UUID rows in the UI). */
function jobInputLabel(folder: string): string {
  const raw = (folder || "").trim();
  if (!raw) return "—";
  const base = raw.replace(/\\/g, "/").split("/").pop() || raw;
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(base)) {
    return `Batch ${base.slice(0, 8)}…`;
  }
  if (base.length > 52) return `${base.slice(0, 49)}…`;
  return base;
}

/** Human label for pipeline triage roles shown while Add one lot runs. */
function pipelineTriageRoleLabel(role: string | undefined): string {
  const r = (role || "").trim().toLowerCase();
  switch (r) {
    case "unusable":
      return "Not passed to Pro";
    case "angle_only":
      return "Extra angle";
    case "item_front":
      return "Item front";
    case "hero":
      return "Hero shot";
    case "tape":
      return "SKU tape";
    default:
      return role?.replace(/_/g, " ") || "";
  }
}

type PipelinePhotoSelection = {
  heroFilename: string | null;
  proFilenames: Set<string>;
  listingFilenames: Set<string>;
  makerStickerFilename: string | null;
};

function normalizePipelineFilename(filename: string): string {
  return filename.trim().toLowerCase();
}

/** After triage selection: show which frames go to Pro, listing Flash, or catalog only. */
function pipelineSlotLabel(
  fileName: string | undefined,
  triageRole: string | undefined,
  selection: PipelinePhotoSelection | null
): string {
  if (!selection) return pipelineTriageRoleLabel(triageRole);
  const key = fileName ? normalizePipelineFilename(fileName) : "";
  if (!key) return pipelineTriageRoleLabel(triageRole);

  const matches = (name: string) => normalizePipelineFilename(name) === key;

  if ([...selection.proFilenames].some(matches)) {
    if (selection.makerStickerFilename && matches(selection.makerStickerFilename)) {
      return "Brand shot (Pro) · maker sticker";
    }
    return "Brand shot (Pro)";
  }
  if (selection.heroFilename && matches(selection.heroFilename)) {
    return "Hero shot (listing)";
  }
  if ([...selection.listingFilenames].some(matches)) {
    return "Listing draft";
  }
  if ((triageRole || "").trim().toLowerCase() === "unusable") {
    return "Not passed to Pro";
  }
  return "Catalog only";
}

type SaleZipExport = {
  id: string;
  sale_id: string;
  status: string;
  total_photos_expected: number;
  photo_files_written: number;
  error_message?: string | null;
  expires_at?: string | null;
  created_at: string;
  ready: boolean;
};

/** One placeholder row per queued photo (stable slot for “where the next result lands”). */
type PhotoPendingSlot = {
  id: string;
  jobId: string;
  index: number;
  total: number;
  status: "queued" | "analyzed";
  fileName?: string;
  previewTitle?: string;
  previewSku?: string | null;
};

type PhotoProcessingProgressProps = {
  slots: PhotoPendingSlot[];
  analyzedDone: number;
  analyzedTotal: number;
  singleLotMode: boolean;
  pipelinePhase: string | null;
  pipelinePhotoSelection: PipelinePhotoSelection | null;
  inInventoryPhase: boolean;
  catalogStepProgress: { index: number; total: number; fileName: string } | null;
};

function photoProgressPhaseLine({
  analyzedDone,
  analyzedTotal,
  singleLotMode,
  pipelinePhase,
  inInventoryPhase,
  catalogStepProgress
}: Omit<PhotoProcessingProgressProps, "slots" | "pipelinePhotoSelection">): string {
  if (inInventoryPhase) {
    if (!catalogStepProgress) return "Saving lots to the catalog…";
    return `Saving lots — photo ${catalogStepProgress.index + 1} of ${catalogStepProgress.total}${
      catalogStepProgress.fileName ? ` (${catalogStepProgress.fileName})` : ""
    }`;
  }
  if (singleLotMode && pipelinePhase) {
    if (pipelinePhase === "pipeline_triage") {
      return `Sorting photos for one lot (${analyzedDone} / ${analyzedTotal || "…"} classified)`;
    }
    if (pipelinePhase === "pipeline_listing") return "Writing the listing draft…";
    if (pipelinePhase === "pipeline_pro_proofread") return "Pro is proofreading title and details…";
    return "Working on this lot…";
  }
  if (analyzedTotal > 0) {
    if (analyzedDone < analyzedTotal) {
      return `Reading photos with AI (${analyzedDone} / ${analyzedTotal} done)`;
    }
    return "Photos analyzed — finishing catalog merge…";
  }
  return "Processing photos…";
}

function PhotoProcessingProgressPanel({
  slots,
  analyzedDone,
  analyzedTotal,
  singleLotMode,
  pipelinePhase,
  pipelinePhotoSelection,
  inInventoryPhase,
  catalogStepProgress
}: PhotoProcessingProgressProps) {
  if (!slots.length) return null;
  const pct = analyzedTotal > 0 ? Math.min(100, Math.round((analyzedDone / analyzedTotal) * 100)) : 0;
  const phaseLine = photoProgressPhaseLine({
    analyzedDone,
    analyzedTotal,
    singleLotMode,
    pipelinePhase,
    inInventoryPhase,
    catalogStepProgress
  });

  return (
    <div className="ct-photo-progress" aria-live="polite">
      <div className="ct-photo-progress__head">
        <span className="ct-photo-progress__title">Live photo progress</span>
        <span className="ct-photo-progress__count">
          {inInventoryPhase && catalogStepProgress
            ? `${catalogStepProgress.index + 1} / ${catalogStepProgress.total}`
            : `${analyzedDone} / ${analyzedTotal}`}
        </span>
      </div>
      <div
        className="ct-photo-progress__bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={inInventoryPhase && catalogStepProgress ? undefined : pct}
        aria-label="Photo processing progress"
      >
        <div
          className="ct-photo-progress__bar-fill"
          style={{
            width: inInventoryPhase && catalogStepProgress
              ? `${Math.min(100, Math.round(((catalogStepProgress.index + 1) / catalogStepProgress.total) * 100))}%`
              : `${pct}%`
          }}
        />
      </div>
      <p className="ct-photo-progress__phase">{phaseLine}</p>
      <details className="ct-photo-progress__details">
        <summary>Per-photo status ({slots.length})</summary>
        <ul className="ct-photo-progress__list">
          {slots.map((slot) => (
            <li
              key={slot.id}
              className={`ct-photo-progress__item${
                slot.status === "queued" ? " ct-photo-progress__item--queued" : " ct-photo-progress__item--done"
              }`}
            >
              <span className="ct-photo-progress__item-icon" aria-hidden>
                {slot.status === "queued" ? "…" : "✓"}
              </span>
              <span className="ct-photo-progress__item-text">
                <strong>
                  {slot.index + 1}/{slot.total}
                </strong>
                {slot.fileName ? (
                  <span className="ct-photo-progress__item-file"> · {slot.fileName}</span>
                ) : null}
                {slot.status === "queued" ? (
                  <span className="ct-photo-progress__item-note">
                    {" "}
                    — {singleLotMode ? "Sorting…" : "Waiting…"}
                  </span>
                ) : slot.previewTitle ? (
                  <span className="ct-photo-progress__item-note">
                    {" "}
                    — {pipelineSlotLabel(slot.fileName, slot.previewTitle, pipelinePhotoSelection)}
                  </span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

const CATALOG_IMAGE_RE = /\.(jpe?g|png|webp|heic|heif)$/i;

function isCatalogImageFile(f: File): boolean {
  if (f.type.startsWith("image/")) return true;
  return CATALOG_IMAGE_RE.test(f.name);
}

function folderLabelFromFile(f: File): string {
  const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
  const parts = rel.split("/").filter(Boolean);
  if (parts.length >= 2) return parts[0] ?? "Folder";
  return "Selected photos";
}

function revokeObjectUrls(urls: string[]) {
  urls.forEach((u) => URL.revokeObjectURL(u));
}

/** Ask before replacing or clearing staged photos in the upload box. */
function confirmStagingOverwrite(prev: FolderPickPreview | null, detail: string): boolean {
  const n = prev?.imageFiles.length ?? 0;
  if (!n) return true;
  return window.confirm(
    `The upload box already has ${n} staged photo${n === 1 ? "" : "s"}.\n\n${detail}`
  );
}

type FolderPickPreview = {
  folderLabel: string;
  imageFiles: File[];
  thumbUrls: string[];
  skippedCount: number;
  /** When true, this upload is processed as one catalog lot (one SKU row) for every photo. */
  singleLotMode?: boolean;
};

type SingleLotStagingApply = { pick: FolderPickPreview; droppedFromCap: number };

function capIncomingSingleLotFiles(existingCount: number, incoming: File[]): { files: File[]; dropped: number } {
  if (CTBIDS_MAX_PHOTOS_PER_LOT == null) return { files: incoming, dropped: 0 };
  const room = Math.max(0, CTBIDS_MAX_PHOTOS_PER_LOT - existingCount);
  if (incoming.length <= room) return { files: incoming, dropped: 0 };
  return { files: incoming.slice(0, room), dropped: incoming.length - room };
}

/** Merge or replace staged files for one-lot uploads (picker + inline camera). Caps when ``CTBIDS_MAX_PHOTOS_PER_LOT`` is set. */
function applySingleLotFilesToStaging(
  prev: FolderPickPreview | null,
  incoming: File[],
  extraSkipped: number
): SingleLotStagingApply {
  const appendSingleLot = Boolean(prev?.singleLotMode);
  if (appendSingleLot) {
    const { files: cappedIncoming, dropped } = capIncomingSingleLotFiles(prev!.imageFiles.length, incoming);
    const merged = [...prev!.imageFiles, ...cappedIncoming];
    const mergedSkipped = prev!.skippedCount + extraSkipped + dropped;
    const newThumbUrls = cappedIncoming.map((f) => URL.createObjectURL(f));
    const thumbUrls = [...prev!.thumbUrls, ...newThumbUrls];
    return {
      pick: {
        folderLabel:
          merged.length === 1 ? folderLabelFromFile(merged[0]!) : `One lot (${merged.length} photos)`,
        imageFiles: merged,
        thumbUrls,
        skippedCount: mergedSkipped,
        singleLotMode: true
      },
      droppedFromCap: dropped
    };
  }
  const { files: cappedIncoming, dropped } = capIncomingSingleLotFiles(0, incoming);
  if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
  const thumbUrls = cappedIncoming.map((f) => URL.createObjectURL(f));
  return {
    pick: {
      folderLabel:
        cappedIncoming.length === 1
          ? folderLabelFromFile(cappedIncoming[0]!)
          : `One lot (${cappedIncoming.length} photos)`,
      imageFiles: cappedIncoming,
      thumbUrls,
      skippedCount: extraSkipped + dropped,
      singleLotMode: true
    },
    droppedFromCap: dropped
  };
}

export default function DashboardPage() {
  const router = useRouter();
  const folderInputRef = useRef<HTMLInputElement>(null);
  const photosInputRef = useRef<HTMLInputElement>(null);
  /** Next file-picker result: normal batch vs one combined lot. */
  const uploadPickModeRef = useRef<"batch" | "single_lot">("batch");
  /** True after "Add one lot" camera flow until multi-file pick, upload, clear, or picker cancel. Chains camera opens on mobile. */
  const singleLotCaptureChainingRef = useRef(false);
  /** Timestamp of last native file/camera picker open — skip bfcache reload briefly after return. */
  const nativePickerOpenedAtRef = useRef(0);
  const folderPickRef = useRef<FolderPickPreview | null>(null);
  const scrollToStagingPendingRef = useRef(false);
  const stagingAwaitingGeminiJobIdRef = useRef<string | null>(null);
  const stagingAwaitingGeminiTotalRef = useRef(0);
  const stagingAwaitingGeminiSeenRef = useRef<Set<number>>(new Set());
  /** Inline multi-snap camera (getUserMedia); avoids iOS blocking repeated file-picker opens. */
  const lotCameraVideoRef = useRef<HTMLVideoElement | null>(null);
  /** Remount the ``<video>`` after iOS backgrounds the tab (preview black, capture still works). */
  const [lotCameraVideoMountKey, setLotCameraVideoMountKey] = useState(0);
  const lotCameraResumeTimerRef = useRef<number | null>(null);
  /** Opens the device camera app (``capture``) while the one-lot dialog stays open. */
  const lotCameraNativeInputRef = useRef<HTMLInputElement | null>(null);
  const lotCameraNativeChainingRef = useRef(false);
  /** True while the OS camera sheet is open — inline preview must stay suspended. */
  const lotCameraNativePickerOpenRef = useRef(false);
  const lotCameraNativeChainTimerRef = useRef<number | null>(null);
  const lotCameraStreamRef = useRef<MediaStream | null>(null);
  const lotCameraSnapsRef = useRef<File[]>([]);
  const [lotCameraOpen, setLotCameraOpen] = useState(false);
  const [lotCameraStream, setLotCameraStream] = useState<MediaStream | null>(null);
  const [lotCameraSnaps, setLotCameraSnaps] = useState<File[]>([]);
  const [lotCameraSnapThumbUrls, setLotCameraSnapThumbUrls] = useState<string[]>([]);
  const [lotCameraPreviewReady, setLotCameraPreviewReady] = useState(false);
  /** Bumps when orientation or video dimensions change so preview rotation recalculates. */
  const [lotCameraPreviewLayoutTick, setLotCameraPreviewLayoutTick] = useState(0);
  /** Index into ``lotCameraSnapThumbUrls`` for full-screen preview, or null. */
  const [lotCameraViewerIndex, setLotCameraViewerIndex] = useState<number | null>(null);
  /** In-app delete confirm — avoids ``window.confirm`` which freezes iOS camera preview. */
  const [lotCameraDeleteConfirmIndex, setLotCameraDeleteConfirmIndex] = useState<number | null>(null);
  /** Optional post-capture edit (rotate + trim edges); null when editor closed. */
  const [lotCameraEditIndex, setLotCameraEditIndex] = useState<number | null>(null);
  const [lotCameraEditRotation, setLotCameraEditRotation] = useState(0);
  const [lotCameraEditTrim, setLotCameraEditTrim] = useState(0);
  const [lotCameraEditBrightness, setLotCameraEditBrightness] = useState(100);
  const [lotCameraEditContrast, setLotCameraEditContrast] = useState(100);
  const [lotCameraEditSaturation, setLotCameraEditSaturation] = useState(100);
  const [lotCameraEditBusy, setLotCameraEditBusy] = useState(false);
  const [lotCameraZoom, setLotCameraZoom] = useState(LOT_CAMERA_ZOOM_MIN);
  const lotCameraZoomRef = useRef(LOT_CAMERA_ZOOM_MIN);
  /** When true, zoom is applied on the camera lens — preview uses CSS scale 1. */
  const [lotCameraHardwareZoom, setLotCameraHardwareZoom] = useState(false);
  const lotCameraHardwareZoomRef = useRef(false);
  const [lotCameraOpticalAvailable, setLotCameraOpticalAvailable] = useState(false);
  const lotCameraZoomCapRef = useRef<LotCameraZoomCap | null>(null);
  const lotCameraZoomApplyTimerRef = useRef<number | null>(null);
  const lotCameraZoomWrapRef = useRef<HTMLDivElement | null>(null);
  const lotCameraPinchRef = useRef<{ startDist: number; startZoom: number } | null>(null);
  const lotCameraTapStartRef = useRef<{ x: number; y: number; t: number } | null>(null);
  const [lotCameraFocusRing, setLotCameraFocusRing] = useState<{
    xPct: number;
    yPct: number;
    key: number;
  } | null>(null);
  const [lotCameraShutterFlash, setLotCameraShutterFlash] = useState(false);
  const lotCameraShutterFlashTimerRef = useRef<number | null>(null);
  const lotCameraStripRef = useRef<HTMLDivElement | null>(null);
  /** Scrollable column inside the camera modal — kept at bottom so Snap/Done stay in view on mobile. */
  const lotCameraScrollRef = useRef<HTMLDivElement | null>(null);
  const inventoryFirstLoad = useRef(true);
  const salesLoadId = useRef(0);
  const inventoryLoadId = useRef(0);
  const [token, setToken] = useState<string>(readStoredToken);
  const [sales, setSales] = useState<Sale[]>([]);
  const [selectedSaleId, setSelectedSaleId] = useState<string>(readStoredSaleId);
  /** Bumps after bfcache restore or return from lot detail so sales/lots/jobs refetch. */
  const [dataReloadNonce, setDataReloadNonce] = useState(0);
  const selectedSaleIdRef = useRef<string>("");
  const [newSaleName, setNewSaleName] = useState("");
  const [creatingSale, setCreatingSale] = useState(false);
  const [closingSale, setClosingSale] = useState(false);
  const [isMasterAdmin, setIsMasterAdmin] = useState(false);
  const [adminLoadAttempted, setAdminLoadAttempted] = useState(false);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminSavingAccountId, setAdminSavingAccountId] = useState("");
  const [adminError, setAdminError] = useState("");
  const [adminAccounts, setAdminAccounts] = useState<AdminAccount[]>([]);
  const [adminSearch, setAdminSearch] = useState("");
  const [adminNameDraftById, setAdminNameDraftById] = useState<Record<string, string>>({});
  const [adminPlanDraftById, setAdminPlanDraftById] = useState<Record<string, string>>({});
  const [adminUsage, setAdminUsage] = useState<AdminUsageSummary | null>(null);
  const [adminUsageLoading, setAdminUsageLoading] = useState(false);
  const [lots, setLots] = useState<Lot[]>([]);
  /** ``lot_id`` → count of catalog photos whose files are missing on the API host (from ``GET /sales/{id}/photo-health``). */
  const [photoHealthMissing, setPhotoHealthMissing] = useState<Record<string, number>>({});
  /** Filter ``GET /lots`` to rows with SKU sanity flags (duplicate digit-SKU in sale, or mixed lot numbers in text). */
  const [inventorySkuFlagFilter, setInventorySkuFlagFilter] = useState<
    "" | "duplicate" | "text_mismatch" | "any"
  >("");
  const inventorySkuFlagFilterRef = useRef<"" | "duplicate" | "text_mismatch" | "any">("");
  inventorySkuFlagFilterRef.current = inventorySkuFlagFilter;
  const [jobs, setJobs] = useState<Job[]>([]);
  const [completedJobsExpanded, setCompletedJobsExpanded] = useState(false);
  const [folder, setFolder] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [stagingSuccess, setStagingSuccess] = useState("");
  const stagingDraftRestoredRef = useRef(false);
  /** Shown during multi-request folder uploads (large batches). */
  const [uploadProgress, setUploadProgress] = useState("");
  /** When set, top-level subfolders named with 2–6 digits (e.g. ``035/``) set each lot's SKU on finalize. */
  const [inheritSkuFromSubfolder, setInheritSkuFromSubfolder] = useState(false);
  const [exporting, setExporting] = useState<"" | "markdown" | "json" | "csv">("");
  const [saleZipExport, setSaleZipExport] = useState<SaleZipExport | null>(null);
  const [saleZipBusy, setSaleZipBusy] = useState(false);
  const [folderPick, setFolderPick] = useState<FolderPickPreview | null>(null);
  /** Full-screen preview index for staged upload thumbnails, or null. */
  const [stagingViewerIndex, setStagingViewerIndex] = useState<number | null>(null);
  /** Post-stage edit (rotate + trim) for one staged file. */
  const [stagingEditIndex, setStagingEditIndex] = useState<number | null>(null);
  const [stagingEditRotation, setStagingEditRotation] = useState(0);
  const [stagingEditTrim, setStagingEditTrim] = useState(0);
  const [stagingEditBrightness, setStagingEditBrightness] = useState(100);
  const [stagingEditContrast, setStagingEditContrast] = useState(100);
  const [stagingEditSaturation, setStagingEditSaturation] = useState(100);
  const [stagingEditBusy, setStagingEditBusy] = useState(false);
  /** Fixed jump scroll buttons when the dashboard is taller than the viewport. */
  const [showDashboardScrollJumpNav, setShowDashboardScrollJumpNav] = useState(false);
  /** Optional lot/tape marking for one-lot uploads (camera + file picker). */
  const [singleLotManualSku, setSingleLotManualSku] = useState("");
  /** One loading row per catalog photo while the active job runs (SSE). */
  const [photoPendingSlots, setPhotoPendingSlots] = useState<PhotoPendingSlot[]>([]);
  const [inInventoryPhase, setInInventoryPhase] = useState(false);
  /** Single-lot pipeline stage from SSE ``batch_phase`` (triage → listing → Pro). */
  const [pipelinePhase, setPipelinePhase] = useState<string | null>(null);
  /** Post-triage selection: which filenames are sent to Pro / listing Flash (SSE ``pipeline_selection``). */
  const [pipelinePhotoSelection, setPipelinePhotoSelection] = useState<PipelinePhotoSelection | null>(
    null
  );
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const [cancelNotice, setCancelNotice] = useState("");
  /** Server-reported position in the post-Gemini catalog merge loop (SSE ``inventory_step``). */
  const [catalogStepProgress, setCatalogStepProgress] = useState<{
    index: number;
    total: number;
    fileName: string;
  } | null>(null);
  /** Bumps when returning to the tab so ``EventSource`` reconnects and replays recent SSE (mobile sleep / flaky LTE). */
  const [jobSseSession, setJobSseSession] = useState(0);
  const sseRetryAfterErrorRef = useRef(0);
  /** Dashboard progress table + SSE follow this upload's job, not an older concurrent run. */
  const liveProgressJobIdRef = useRef<string | null>(null);
  const [liveProgressJobId, setLiveProgressJobId] = useState<string | null>(null);
  folderPickRef.current = folderPick;
  selectedSaleIdRef.current = selectedSaleId;

  const busyProcessing = jobs.some((j) => j.status === "processing");
  const busyProcessingRef = useRef(false);
  busyProcessingRef.current = busyProcessing;
  const processingJob = useMemo(() => {
    const active = jobs.filter((j) => j.status === "processing");
    if (!active.length) return null;
    const preferred = liveProgressJobIdRef.current ?? liveProgressJobId;
    if (preferred) {
      const hit = active.find((j) => j.id === preferred);
      if (hit) return hit;
    }
    return (
      [...active].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))[0] ?? null
    );
  }, [jobs, liveProgressJobId]);
  const processingJobId = processingJob?.id ?? null;
  const photoHealthLotCount = useMemo(
    () => Object.keys(photoHealthMissing).length,
    [photoHealthMissing]
  );

  const { activeJobs, completedJobs, queuedJobCount } = useMemo(() => {
    const active: Job[] = [];
    const completed: Job[] = [];
    let queuedCount = 0;
    for (const j of jobs) {
      if (j.status === "completed") completed.push(j);
      else {
        active.push(j);
        if (j.status === "queued") queuedCount += 1;
      }
    }
    return { activeJobs: active, completedJobs: completed, queuedJobCount: queuedCount };
  }, [jobs]);
  const selectedSale = useMemo(() => sales.find((s) => s.id === selectedSaleId) ?? null, [sales, selectedSaleId]);
  const selectedSaleClosed = Boolean(selectedSale?.closed_at);
  const dashboardStats = useMemo(() => {
    const photoTotal = lots.reduce((n, l) => n + (l.photo_count || 0), 0);
    const withCondition = lots.filter((l) => (l.condition_text || "").trim().length > 0).length;
    return {
      totalLots: lots.length,
      photoTotal,
      withCondition,
      completedJobCount: completedJobs.length
    };
  }, [lots, completedJobs.length]);
  const filteredAdminAccounts = useMemo(() => {
    const q = adminSearch.trim().toLowerCase();
    if (!q) return adminAccounts;
    return adminAccounts.filter((a) => {
      const name = (a.name || "").toLowerCase();
      const email = (a.owner_email || "").toLowerCase();
      const id = (a.id || "").toLowerCase();
      return name.includes(q) || email.includes(q) || id.includes(q);
    });
  }, [adminAccounts, adminSearch]);

  const geminiAnalyzedCount = useMemo(() => {
    if (!photoPendingSlots.length) return { done: 0, total: 0 };
    const total = photoPendingSlots.length;
    const done = photoPendingSlots.filter((s) => s.status === "analyzed").length;
    return { done, total };
  }, [photoPendingSlots]);

  useEffect(() => {
    try {
      if (sessionStorage.getItem("ct_from_lot_detail") === "1") {
        sessionStorage.removeItem("ct_from_lot_detail");
        setToken(localStorage.getItem("token") || "");
        setDataReloadNonce((n) => n + 1);
      }
    } catch {
      /* private mode */
    }
  }, []);

  function readStagingAwaitingGeminiJobId(): string | null {
    if (stagingAwaitingGeminiJobIdRef.current) return stagingAwaitingGeminiJobIdRef.current;
    try {
      const id = sessionStorage.getItem(STAGING_AWAIT_GEMINI_JOB_KEY);
      if (id) stagingAwaitingGeminiJobIdRef.current = id;
      return id;
    } catch {
      return null;
    }
  }

  function persistStagingAwaitingGemini(jobId: string, totalPhotos: number) {
    stagingAwaitingGeminiJobIdRef.current = jobId;
    stagingAwaitingGeminiTotalRef.current = totalPhotos;
    stagingAwaitingGeminiSeenRef.current = new Set();
    try {
      sessionStorage.setItem(STAGING_AWAIT_GEMINI_JOB_KEY, jobId);
      sessionStorage.setItem(STAGING_AWAIT_GEMINI_TOTAL_KEY, String(totalPhotos));
      sessionStorage.setItem(STAGING_AWAIT_GEMINI_SEEN_KEY, "");
    } catch {
      /* private mode */
    }
  }

  function restoreStagingAwaitingGeminiFromSession() {
    try {
      const jobId = sessionStorage.getItem(STAGING_AWAIT_GEMINI_JOB_KEY);
      if (!jobId) return;
      stagingAwaitingGeminiJobIdRef.current = jobId;
      const total = parseInt(sessionStorage.getItem(STAGING_AWAIT_GEMINI_TOTAL_KEY) || "0", 10);
      stagingAwaitingGeminiTotalRef.current = Number.isFinite(total) ? total : 0;
      const seenRaw = sessionStorage.getItem(STAGING_AWAIT_GEMINI_SEEN_KEY) || "";
      const seen = new Set<number>();
      for (const part of seenRaw.split(",")) {
        const n = parseInt(part, 10);
        if (Number.isFinite(n)) seen.add(n);
      }
      stagingAwaitingGeminiSeenRef.current = seen;
    } catch {
      /* private mode */
    }
  }

  function persistStagingAwaitingGeminiSeen() {
    try {
      const parts = [...stagingAwaitingGeminiSeenRef.current].sort((a, b) => a - b);
      sessionStorage.setItem(STAGING_AWAIT_GEMINI_SEEN_KEY, parts.join(","));
    } catch {
      /* private mode */
    }
  }

  function clearStagingAwaitingGeminiJobId() {
    stagingAwaitingGeminiJobIdRef.current = null;
    stagingAwaitingGeminiTotalRef.current = 0;
    stagingAwaitingGeminiSeenRef.current = new Set();
    try {
      sessionStorage.removeItem(STAGING_AWAIT_GEMINI_JOB_KEY);
      sessionStorage.removeItem(STAGING_AWAIT_GEMINI_TOTAL_KEY);
      sessionStorage.removeItem(STAGING_AWAIT_GEMINI_SEEN_KEY);
    } catch {
      /* private mode */
    }
  }

  function clearStagingAfterAllGeminiPhotos() {
    if (!readStagingAwaitingGeminiJobId()) return;
    clearFolderPick();
    clearStagingAwaitingGeminiJobId();
    setStagingSuccess(
      "All staged photos were sent to Gemini — upload box cleared. Catalog may still be building lots below."
    );
    setError("");
  }

  function noteStagingGeminiPhotoAnalyzed(jobId: string, index: number, totalFromEvent: number) {
    const awaiting = readStagingAwaitingGeminiJobId();
    if (!awaiting || awaiting !== jobId) return;
    let total = stagingAwaitingGeminiTotalRef.current;
    if (total <= 0 && totalFromEvent > 0) {
      total = totalFromEvent;
      stagingAwaitingGeminiTotalRef.current = total;
      try {
        sessionStorage.setItem(STAGING_AWAIT_GEMINI_TOTAL_KEY, String(total));
      } catch {
        /* private mode */
      }
    }
    if (total <= 0) return;
    const seen = stagingAwaitingGeminiSeenRef.current;
    if (seen.has(index)) return;
    seen.add(index);
    persistStagingAwaitingGeminiSeen();
    if (seen.size >= total) {
      clearStagingAfterAllGeminiPhotos();
    }
  }

  function maybeClearStagingAfterJobTerminal(jobId: string, status: string) {
    const awaiting = readStagingAwaitingGeminiJobId();
    if (!awaiting || awaiting !== jobId) return;
    if (status === "failed") {
      setStagingSuccess("");
      setError(
        "Catalog job failed — your staged photos are still in the upload box. Fix the issue in Jobs and tap Run again, or use Clear selection."
      );
      return;
    }
    if (status === "completed" && folderPickRef.current) {
      clearStagingAfterAllGeminiPhotos();
    }
  }

  useEffect(() => {
    if (!token) return;
    restoreStagingAwaitingGeminiFromSession();
    const awaiting = readStagingAwaitingGeminiJobId();
    if (!awaiting) return;
    const total = stagingAwaitingGeminiTotalRef.current;
    const seen = stagingAwaitingGeminiSeenRef.current;
    if (total > 0 && seen.size >= total) {
      clearStagingAfterAllGeminiPhotos();
      stagingDraftRestoredRef.current = true;
      return;
    }
    void apiFetch(`/jobs/${encodeURIComponent(awaiting)}`, token)
      .then((row) => {
        const job = row as Job;
        if (job.status === "failed") {
          maybeClearStagingAfterJobTerminal(job.id, "failed");
          stagingDraftRestoredRef.current = true;
        }
      })
      .catch(() => {
        /* job row may be gone */
      });
  }, [token]);

  useEffect(() => {
    if (!token || folderPick || stagingDraftRestoredRef.current) return;
    stagingDraftRestoredRef.current = true;
    void loadStagingDraft().then((draft) => {
      if (!draft?.imageFiles.length) return;
      setFolderPick({
        folderLabel: draft.folderLabel,
        imageFiles: draft.imageFiles,
        thumbUrls: draft.thumbUrls,
        skippedCount: draft.skippedCount,
        singleLotMode: draft.singleLotMode
      });
      if (draft.singleLotManualSku) setSingleLotManualSku(draft.singleLotManualSku);
      if (draft.saleId) {
        try {
          localStorage.setItem(SALE_STORAGE_KEY, draft.saleId);
        } catch {
          /* private mode */
        }
        setSelectedSaleId(draft.saleId);
      }
      setStagingSuccess(
        `Restored ${draft.imageFiles.length} staged photo${draft.imageFiles.length === 1 ? "" : "s"} from your last session on this device.`
      );
    });
  }, [token, folderPick]);

  useEffect(() => {
    if (!folderPick?.imageFiles.length) {
      void clearStagingDraft();
      return;
    }
    const pick = folderPick;
    const t = window.setTimeout(() => {
      void saveStagingDraft(pick, {
        saleId: selectedSaleId || undefined,
        singleLotManualSku: singleLotManualSku.trim() || undefined
      });
    }, 500);
    return () => window.clearTimeout(t);
  }, [folderPick, selectedSaleId, singleLotManualSku]);

  useEffect(() => {
    if (!scrollToStagingPendingRef.current || !folderPick?.imageFiles.length) return;
    if (scrollStagingUploadBoxIntoView()) {
      scrollToStagingPendingRef.current = false;
    }
  }, [folderPick]);

  useEffect(() => {
    const onPageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      // Returning from the OS photo/camera picker restores bfcache; refetching here flashes
      // "Loading dashboard…" and drops the sticky upload bar until fetches finish.
      if (Date.now() - nativePickerOpenedAtRef.current < 60_000) return;
      try {
        setToken(localStorage.getItem("token") || "");
        setDataReloadNonce((n) => n + 1);
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, []);

  useEffect(() => {
    setSaleZipExport(null);
  }, [selectedSaleId]);

  /** Refresh/close during multipart upload aborts the request and clears staging — warn first. */
  useEffect(() => {
    if (typeof window === "undefined" || !uploading) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [uploading]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const update = () => {
      const doc = document.documentElement;
      setShowDashboardScrollJumpNav(doc.scrollHeight > window.innerHeight + 120);
    };
    update();
    window.addEventListener("resize", update, { passive: true });
    window.addEventListener("scroll", update, { passive: true });
    const t1 = window.setTimeout(update, 400);
    const t2 = window.setTimeout(update, 1500);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update);
    };
  }, [
    lots.length,
    jobs.length,
    folderPick,
    lotCameraOpen,
    lotCameraSnaps.length,
    photoPendingSlots.length,
    uploadProgress,
    inInventoryPhase,
    catalogStepProgress,
    saleZipExport,
    dataReloadNonce,
    selectedSaleId,
    saleZipBusy,
    loading
  ]);

  /** After returning from lot detail via ``Back to dashboard``, scroll to where Details was opened. */
  useEffect(() => {
    if (typeof window === "undefined" || !token || loading) return;
    let cancelled = false;
    let yToRestore: number | null = null;
    try {
      if (sessionStorage.getItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY) !== "1") return;
      const raw = sessionStorage.getItem(DASHBOARD_SCROLL_Y_KEY);
      const rawAt = sessionStorage.getItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
      const savedAt = rawAt !== null ? Number.parseInt(rawAt, 10) : NaN;
      if (
        !Number.isFinite(savedAt) ||
        Date.now() - savedAt > DASHBOARD_SCROLL_MAX_AGE_MS
      ) {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        if (raw !== null) sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
        return;
      }
      const y = raw !== null ? Number.parseFloat(raw) : NaN;
      if (!Number.isFinite(y) || y < 0) {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        if (raw !== null) sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
        return;
      }
      yToRestore = y;
    } catch {
      return;
    }

    const apply = () => {
      if (cancelled || yToRestore === null) return;
      window.scrollTo({ top: yToRestore, behavior: "auto" });
    };
    apply();
    const raf = window.requestAnimationFrame(apply);
    const t1 = window.setTimeout(apply, 80);
    const t2 = window.setTimeout(apply, 280);
    const t3 = window.setTimeout(apply, 700);
    const tClear = window.setTimeout(() => {
      if (cancelled) return;
      try {
        sessionStorage.removeItem(DASHBOARD_SCROLL_RESTORE_PENDING_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_Y_KEY);
        sessionStorage.removeItem(DASHBOARD_SCROLL_SAVED_AT_KEY);
      } catch {
        /* private mode */
      }
    }, 900);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.clearTimeout(t3);
      window.clearTimeout(tClear);
    };
  }, [loading, token, lots.length, jobs.length, dataReloadNonce]);

  useEffect(() => {
    if (!token || !saleZipExport?.id) return;
    if (saleZipExport.status !== "queued" && saleZipExport.status !== "processing") return;
    const id = saleZipExport.id;
    let cancelled = false;
    let intervalId: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      try {
        const row = (await apiFetch(`/exports/sale-photos-zip/${encodeURIComponent(id)}`, token)) as SaleZipExport;
        if (!cancelled) setSaleZipExport(row);
        if (row.ready || row.status === "failed") {
          cancelled = true;
          if (intervalId !== undefined) window.clearInterval(intervalId);
        }
      } catch {
        /* keep last row; user can refresh */
      }
    };
    intervalId = window.setInterval(() => void tick(), 2500);
    void tick();
    return () => {
      cancelled = true;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [token, saleZipExport?.id]);

  useEffect(() => {
    const t = localStorage.getItem("token") || "";
    setToken(t);
    if (!t) {
      inventoryFirstLoad.current = true;
      setLoading(false);
      setSales([]);
      setSelectedSaleId("");
      return;
    }
    if (inventoryFirstLoad.current) {
      setLoading(true);
    }
    const loadId = ++salesLoadId.current;
    void withNetworkRetry(() => apiFetch("/sales", t))
      .then((sl) => {
        if (loadId !== salesLoadId.current) return;
        setError("");
        const arr = sl as Sale[];
        setSales(arr);
        if (!arr.length) {
          setSelectedSaleId("");
          setLots([]);
          setJobs([]);
          setLoading(false);
          return;
        }
        setSelectedSaleId((prev) => {
          if (prev && arr.some((s) => s.id === prev)) return prev;
          const stored = localStorage.getItem(SALE_STORAGE_KEY);
          if (stored && arr.some((s) => s.id === stored)) return stored;
          return arr[0]!.id;
        });
      })
      .catch((e) => {
        if (loadId !== salesLoadId.current) return;
        const msg = e instanceof Error ? e.message : "Load failed";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => {
        if (loadId !== salesLoadId.current) return;
        if (!inventoryFirstLoad.current) {
          setLoading(false);
        }
      });
  }, [router, dataReloadNonce]);

  useEffect(() => {
    if (!token || !selectedSaleId) {
      return;
    }
    const t = token;
    if (inventoryFirstLoad.current) {
      setLoading(true);
    }
    localStorage.setItem(SALE_STORAGE_KEY, selectedSaleId);
    const loadId = ++inventoryLoadId.current;
    const skuQ =
      inventorySkuFlagFilter === ""
        ? ""
        : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilter)}`;
    void withNetworkRetry(() =>
      Promise.all([
        apiFetch(`/lots?sale_id=${encodeURIComponent(selectedSaleId)}${skuQ}`, t),
        apiFetch(`/jobs?sale_id=${encodeURIComponent(selectedSaleId)}`, t)
      ])
    )
      .then(([lotsData, jobsData]) => {
        if (loadId !== inventoryLoadId.current) return;
        setError("");
        setLots(lotsData as Lot[]);
        setJobs(jobsData as Job[]);
      })
      .catch((e) => {
        if (loadId !== inventoryLoadId.current) return;
        const msg = e instanceof Error ? e.message : "Load failed";
        if (isAuthFailureMessage(msg)) {
          localStorage.removeItem("token");
          setToken("");
          router.replace("/");
          return;
        }
        setError(msg);
      })
      .finally(() => {
        if (loadId !== inventoryLoadId.current) return;
        inventoryFirstLoad.current = false;
        setLoading(false);
      });
    void apiFetch(`/sales/${encodeURIComponent(selectedSaleId)}/photo-health`, t)
      .then((d) => {
        if (loadId !== inventoryLoadId.current) return;
        setPhotoHealthMissing(photoHealthMapFromResponse(d));
      })
      .catch(() => {
        if (loadId !== inventoryLoadId.current) return;
        setPhotoHealthMissing({});
      });
  }, [token, selectedSaleId, router, dataReloadNonce, inventorySkuFlagFilter]);

  useEffect(() => {
    if (!token) {
      setIsMasterAdmin(false);
      setAdminLoadAttempted(false);
      setAdminAccounts([]);
      setAdminPlanDraftById({});
      setAdminError("");
      return;
    }
    let cancelled = false;
    setAdminLoading(true);
    setAdminLoadAttempted(true);
    setAdminError("");
    void apiFetch("/admin/accounts", token)
      .then((rows) => {
        if (cancelled) return;
        const arr = rows as AdminAccount[];
        setAdminAccounts(arr);
        setIsMasterAdmin(true);
        const drafts: Record<string, string> = {};
        const nameDrafts: Record<string, string> = {};
        arr.forEach((a) => {
          drafts[a.id] = a.plan_code || "beta_free";
          nameDrafts[a.id] = a.name || "";
        });
        setAdminNameDraftById(nameDrafts);
        setAdminPlanDraftById(drafts);
        setAdminUsageLoading(true);
        void apiFetch("/admin/analytics?since_days=30", token)
          .then((u) => {
            if (!cancelled) setAdminUsage(u as AdminUsageSummary);
          })
          .catch(() => {
            if (!cancelled) setAdminUsage(null);
          })
          .finally(() => {
            if (!cancelled) setAdminUsageLoading(false);
          });
      })
      .catch((e) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : "Could not load admin accounts";
        if (msg.includes("403") || msg.toLowerCase().includes("master admin")) {
          setIsMasterAdmin(false);
          setAdminAccounts([]);
          setAdminNameDraftById({});
          setAdminPlanDraftById({});
          setAdminUsage(null);
          return;
        }
        setIsMasterAdmin(false);
        setAdminError(msg);
      })
      .finally(() => {
        if (!cancelled) setAdminLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, dataReloadNonce]);

  useEffect(() => {
    const bumpSse = () => {
      if (document.visibilityState !== "visible" || !busyProcessingRef.current) return;
      setJobSseSession((n) => n + 1);
    };
    document.addEventListener("visibilitychange", bumpSse);
    return () => document.removeEventListener("visibilitychange", bumpSse);
  }, []);

  useEffect(() => {
    if (!token || loading || !processingJobId) return;

    const url = `${API_BASE}/jobs/${processingJobId}/events?access_token=${encodeURIComponent(token)}`;
    sseRetryAfterErrorRef.current = 0;
    const es = new EventSource(url);
    es.onopen = () => {
      sseRetryAfterErrorRef.current = 0;
    };

    const refreshJobsAndLots = () => {
      const sid = selectedSaleIdRef.current;
      if (!sid) return;
      const fq =
        inventorySkuFlagFilterRef.current === ""
          ? ""
          : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilterRef.current)}`;
      void apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)
        .then((d) => setJobs(d as Job[]))
        .catch(() => {
          /* ignore */
        });
      void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}${fq}`, token)
        .then((d) => setLots(d as Lot[]))
        .catch(() => {
          /* ignore */
        });
      void apiFetch(`/sales/${encodeURIComponent(sid)}/photo-health`, token)
        .then((d) => setPhotoHealthMissing(photoHealthMapFromResponse(d)))
        .catch(() => {
          /* ignore */
        });
    };

    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as {
          type?: string;
          status?: string;
          job_id?: string;
          error_message?: string | null;
          lot?: LiveLotRowPayload;
          total_photos?: number;
          phase?: string;
          index?: number;
          total?: number;
          file_name?: string;
          preview?: { auction_title: string; sku: string | null };
        };
        if (
          msg.type === "batch_start" &&
          typeof msg.total_photos === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          const n = msg.total_photos;
          const jid = processingJobId;
          setInInventoryPhase(false);
          setCatalogStepProgress(null);
          setPipelinePhase(msg.phase === "pipeline" ? "pipeline_triage" : null);
          setPipelinePhotoSelection(null);
          setCancelNotice("");
          setPhotoPendingSlots(() =>
            Array.from({ length: n }, (_, i) => ({
              id: `${jid}-slot-${i}`,
              jobId: jid,
              index: i,
              total: n,
              status: "queued" as const
            }))
          );
        }
        if (
          msg.type === "photo_analyzed" &&
          typeof msg.index === "number" &&
          typeof msg.total === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          setPhotoPendingSlots((prev) => {
            if (prev.length === 0) {
              const jid = processingJobId;
              const n = msg.total!;
              const base: PhotoPendingSlot[] = Array.from({ length: n }, (_, i) => ({
                id: `${jid}-slot-${i}`,
                jobId: jid,
                index: i,
                total: n,
                status: "queued"
              }));
              return base.map((s) =>
                s.index === msg.index
                  ? {
                      ...s,
                      status: "analyzed",
                      fileName: msg.file_name ?? "",
                      previewTitle: msg.preview?.auction_title,
                      previewSku: msg.preview?.sku ?? null
                    }
                  : s
              );
            }
            return prev.map((s) =>
              s.index === msg.index
                ? {
                    ...s,
                    status: "analyzed",
                    fileName: msg.file_name ?? s.fileName,
                    previewTitle: msg.preview?.auction_title,
                    previewSku: msg.preview?.sku ?? null
                  }
                : s
            );
          });
        }
        if (
          msg.type === "photo_analyzed" &&
          typeof msg.index === "number" &&
          typeof msg.total === "number"
        ) {
          const analyzedJobId =
            typeof msg.job_id === "string" && msg.job_id ? msg.job_id : processingJobId;
          if (analyzedJobId) {
            noteStagingGeminiPhotoAnalyzed(analyzedJobId, msg.index, msg.total);
          }
        }
        if (
          msg.type === "pipeline_selection" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          const proRaw = (msg as { pro_filenames?: unknown }).pro_filenames;
          const listingRaw = (msg as { listing_filenames?: unknown }).listing_filenames;
          const proList = Array.isArray(proRaw) ? proRaw.filter((x): x is string => typeof x === "string") : [];
          const listingList = Array.isArray(listingRaw)
            ? listingRaw.filter((x): x is string => typeof x === "string")
            : [];
          const heroRaw = (msg as { hero_filename?: unknown }).hero_filename;
          const makerRaw = (msg as { maker_sticker_filename?: unknown }).maker_sticker_filename;
          setPipelinePhotoSelection({
            heroFilename: typeof heroRaw === "string" && heroRaw ? heroRaw : null,
            proFilenames: new Set(proList),
            listingFilenames: new Set(listingList),
            makerStickerFilename: typeof makerRaw === "string" && makerRaw ? makerRaw : null
          });
        }
        if (msg.type === "batch_phase" && typeof msg.phase === "string") {
          const phaseJobId =
            typeof msg.job_id === "string" && msg.job_id ? msg.job_id : processingJobId;
          if (
            (msg.phase === "pipeline_triage" ||
              msg.phase === "pipeline_listing" ||
              msg.phase === "pipeline_pro_proofread") &&
            (!msg.job_id || msg.job_id === processingJobId)
          ) {
            setPipelinePhase(msg.phase);
            setInInventoryPhase(false);
          }
          if (msg.phase === "inventory") {
            if (phaseJobId && readStagingAwaitingGeminiJobId() === phaseJobId) {
              clearStagingAfterAllGeminiPhotos();
            }
            if (!msg.job_id || msg.job_id === processingJobId) {
              setInInventoryPhase(true);
              setPipelinePhase(null);
            }
          }
        }
        if (
          msg.type === "inventory_step" &&
          typeof msg.index === "number" &&
          typeof msg.total === "number" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          setCatalogStepProgress({
            index: msg.index,
            total: msg.total,
            fileName: typeof msg.file_name === "string" ? msg.file_name : ""
          });
        }
        if (
          msg.type === "job_log" &&
          typeof (msg as { message?: unknown }).message === "string" &&
          processingJobId &&
          (!msg.job_id || msg.job_id === processingJobId)
        ) {
          console.info("[job]", (msg as { message: string }).message);
        }
        if (msg.type === "lot_ready" && msg.lot?.id && (!msg.job_id || msg.job_id === processingJobId)) {
          const row = normalizeStreamedLot(msg.lot);
          if (row.sale_id && row.sale_id !== selectedSaleIdRef.current) {
            return;
          }
          setLots((prev) => {
            const exists = prev.some((l) => l.id === row.id);
            if (exists) {
              return prev.map((l) => (l.id === row.id ? { ...l, ...row } : l));
            }
            return [row, ...prev];
          });
        }
        if (msg.type === "status" && (msg.status === "completed" || msg.status === "failed")) {
          setPhotoPendingSlots([]);
          setInInventoryPhase(false);
          setPipelinePhase(null);
          setPipelinePhotoSelection(null);
          setCatalogStepProgress(null);
          setCancellingJobId(null);
          setCancelNotice("");
          const finishedJobId =
            typeof msg.job_id === "string" && msg.job_id
              ? msg.job_id
              : processingJobId;
          if (finishedJobId && msg.status) {
            maybeClearStagingAfterJobTerminal(finishedJobId, msg.status);
          }
          if (finishedJobId && liveProgressJobIdRef.current === finishedJobId) {
            liveProgressJobIdRef.current = null;
            setLiveProgressJobId(null);
          }
          refreshJobsAndLots();
        }
      } catch {
        /* ignore */
      }
    };
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    es.onerror = () => {
      es.close();
      if (sseRetryAfterErrorRef.current >= 12) return;
      sseRetryAfterErrorRef.current += 1;
      reconnectTimer = setTimeout(() => setJobSseSession((n) => n + 1), 1800);
    };
    return () => {
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      es.close();
      // Intentionally do not clear photoPendingSlots here: React Strict Mode remounts this effect
      // before the worker finishes, and the server replays recent SSE payloads on reconnect.
    };
  }, [token, loading, processingJobId, jobSseSession]);

  useEffect(() => {
    if (processingJobId) return;
    if (jobs.some((j) => j.status === "queued")) return;
    setPhotoPendingSlots([]);
    setInInventoryPhase(false);
    setPipelinePhase(null);
    setPipelinePhotoSelection(null);
    setCatalogStepProgress(null);
    setCancellingJobId(null);
    liveProgressJobIdRef.current = null;
    setLiveProgressJobId(null);
  }, [processingJobId, jobs]);

  useEffect(() => {
    if (!token || loading) return;
    if (!busyProcessing && queuedJobCount === 0) return;

    /** Keep syncing while the phone is locked or Safari is in the background (field use). Slightly slower when hidden to save radio. */
    const pollMs = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return 45000;
      if (queuedJobCount > 0 && !busyProcessing) return 4000;
      return 12000;
    };

    const tick = async () => {
      try {
        const sid = selectedSaleIdRef.current;
        if (!sid) return;
        const jlist = (await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)) as Job[];
        const processing = jlist.filter((j) => j.status === "processing");
        if (processing.length) {
          const trackedId = liveProgressJobIdRef.current;
          const stillTracked = trackedId && processing.some((j) => j.id === trackedId);
          if (!stillTracked) {
            const next = [...processing].sort((a, b) =>
              (b.created_at || "").localeCompare(a.created_at || "")
            )[0];
            if (next) {
              liveProgressJobIdRef.current = next.id;
              setLiveProgressJobId(next.id);
              setJobSseSession((n) => n + 1);
            }
          }
        }
        setJobs((prev) => {
          const hadProcessing = prev.some((j) => j.status === "processing");
          const nowProcessing = jlist.some((j) => j.status === "processing");
          if (hadProcessing && !nowProcessing) {
            const fq =
              inventorySkuFlagFilterRef.current === ""
                ? ""
                : `&sku_flags=${encodeURIComponent(inventorySkuFlagFilterRef.current)}`;
            void apiFetch(`/lots?sale_id=${encodeURIComponent(sid)}${fq}`, token).then((lotsData) =>
              setLots(lotsData as Lot[])
            );
            void apiFetch(`/sales/${encodeURIComponent(sid)}/photo-health`, token)
              .then((d) => setPhotoHealthMissing(photoHealthMapFromResponse(d)))
              .catch(() => {
                /* ignore */
              });
            const awaiting = readStagingAwaitingGeminiJobId();
            if (awaiting) {
              const finished = jlist.find((j) => j.id === awaiting);
              if (finished && (finished.status === "completed" || finished.status === "failed")) {
                maybeClearStagingAfterJobTerminal(finished.id, finished.status);
              }
            }
          }
          return jlist;
        });
      } catch {
        /* ignore */
      }
    };

    void tick();
    let id = setInterval(() => void tick(), pollMs());
    const onVis = () => {
      void tick();
      clearInterval(id);
      id = setInterval(() => void tick(), pollMs());
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [token, loading, busyProcessing, queuedJobCount, selectedSaleId, inventorySkuFlagFilter]);

  useEffect(() => {
    return () => {
      const p = folderPickRef.current;
      if (p?.thumbUrls.length) revokeObjectUrls(p.thumbUrls);
      lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
      lotCameraStreamRef.current = null;
    };
  }, []);

  useEffect(() => {
    const el = lotCameraVideoRef.current;
    const s = lotCameraStream;
    if (!el || !s) return;
    let cancelled = false;
    void (async () => {
      el.pause();
      el.srcObject = null;
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      );
      if (cancelled) return;
      el.srcObject = s;
      el.muted = true;
      try {
        await el.play();
          if (!cancelled && (el.readyState >= 2 || el.videoWidth > 0)) {
          setLotCameraPreviewReady(true);
          setLotCameraPreviewLayoutTick((t) => t + 1);
        }
      } catch {
        if (!cancelled && lotCameraOpen) void reinitLotCameraStream();
      }
    })();
    return () => {
      cancelled = true;
      el.pause();
      el.srcObject = null;
    };
  }, [lotCameraStream, lotCameraVideoMountKey]);

  /** iOS pauses or ends the camera when the tab/app backgrounds (home screen, app switch, native dialogs). */
  useEffect(() => {
    if (!lotCameraOpen) return;
    const resume = () => scheduleResumeLotCameraPreview();
    const onVisible = () => {
      if (document.visibilityState === "visible") resume();
    };
    const onOrientation = () => {
      setLotCameraPreviewLayoutTick((t) => t + 1);
      scheduleResumeLotCameraPreview();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("orientationchange", onOrientation);
    screen.orientation?.addEventListener("change", onOrientation);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("orientationchange", onOrientation);
      screen.orientation?.removeEventListener("change", onOrientation);
    };
  }, [lotCameraOpen]);

  useEffect(() => {
    const strip = lotCameraStripRef.current;
    if (!strip || !lotCameraSnapThumbUrls.length) return;
    strip.scrollLeft = strip.scrollWidth;
  }, [lotCameraSnapThumbUrls.length]);

  useEffect(() => {
    if (!lotCameraFocusRing) return;
    const t = window.setTimeout(() => setLotCameraFocusRing(null), 1400);
    return () => window.clearTimeout(t);
  }, [lotCameraFocusRing?.key]);

  /** Center the live photo preview in the scroll panel so it stays fully in view (actions sit in a fixed bottom bar). */
  useEffect(() => {
    if (!lotCameraOpen || !lotCameraStream || !lotCameraPreviewReady) return;
    const wrap = lotCameraZoomWrapRef.current;
    if (!wrap) return;

    const centerPreview = () => {
      wrap.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
    };

    centerPreview();
    requestAnimationFrame(centerPreview);
    const t1 = window.setTimeout(centerPreview, 80);
    const t2 = window.setTimeout(centerPreview, 320);

    let resizeRaf = 0;
    const onResizeOrOrientation = () => {
      cancelAnimationFrame(resizeRaf);
      resizeRaf = window.requestAnimationFrame(() => {
        lotCameraZoomWrapRef.current?.scrollIntoView({
          block: "center",
          inline: "nearest",
          behavior: "auto"
        });
      });
    };
    window.addEventListener("orientationchange", onResizeOrOrientation);
    window.addEventListener("resize", onResizeOrOrientation);

    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      cancelAnimationFrame(resizeRaf);
      window.removeEventListener("orientationchange", onResizeOrOrientation);
      window.removeEventListener("resize", onResizeOrOrientation);
    };
  }, [lotCameraOpen, lotCameraStream, lotCameraPreviewReady]);

  /** Pinch-to-zoom on the live preview (non-passive ``touchmove`` so Safari does not scroll the page). */
  useEffect(() => {
    const el = lotCameraZoomWrapRef.current;
    if (!el || !lotCameraOpen || !lotCameraStream) return;

    const clampZoom = (z: number) => clampLotCameraUiZoom(z);

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        const d = lotCameraPinchDistance(e);
        if (d > 0) {
          lotCameraPinchRef.current = { startDist: d, startZoom: lotCameraZoomRef.current };
        }
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 2 || !lotCameraPinchRef.current) return;
      const d = lotCameraPinchDistance(e);
      if (d < 1) return;
      e.preventDefault();
      const { startDist, startZoom } = lotCameraPinchRef.current;
      if (startDist < 1) return;
      const z = clampZoom(startZoom * (d / startDist));
      scheduleLotCameraZoomApply(z, true);
    };

    const onTouchEnd = () => {
      lotCameraPinchRef.current = null;
      if (lotCameraZoomApplyTimerRef.current != null) {
        window.clearTimeout(lotCameraZoomApplyTimerRef.current);
        lotCameraZoomApplyTimerRef.current = null;
      }
      void applyLotCameraZoomLevel(lotCameraZoomRef.current);
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd);
    el.addEventListener("touchcancel", onTouchEnd);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [lotCameraOpen, lotCameraStream]);

  function scheduleLotCameraZoomApply(next: number, duringPinch: boolean) {
    const z = clampLotCameraUiZoom(next);
    lotCameraZoomRef.current = z;
    setLotCameraZoom(z);
    if (!duringPinch) {
      void applyLotCameraZoomLevel(z);
      return;
    }
    if (lotCameraZoomApplyTimerRef.current != null) {
      window.clearTimeout(lotCameraZoomApplyTimerRef.current);
    }
    lotCameraZoomApplyTimerRef.current = window.setTimeout(() => {
      lotCameraZoomApplyTimerRef.current = null;
      void applyLotCameraZoomLevel(lotCameraZoomRef.current);
    }, 80);
  }

  async function applyLotCameraZoomLevel(next: number) {
    const z = clampLotCameraUiZoom(next);
    lotCameraZoomRef.current = z;
    setLotCameraZoom(z);

    const track = lotCameraStreamRef.current?.getVideoTracks()[0];
    const cap = lotCameraZoomCapRef.current;
    let hardware = false;
    if (track && cap && effectiveHardwareZoomRange(cap)) {
      hardware = await applyHardwareZoomOnTrack(track, cap, z);
    }
    lotCameraHardwareZoomRef.current = hardware;
    setLotCameraHardwareZoom(hardware);
  }

  /** +/- buttons: nudge optical zoom in small device steps instead of remapping 1–4 to full telephoto range. */
  async function nudgeLotCameraZoom(direction: -1 | 1) {
    const track = lotCameraStreamRef.current?.getVideoTracks()[0];
    const cap = lotCameraZoomCapRef.current;
    const eff = cap ? effectiveHardwareZoomRange(cap) : null;

    if (track && cap && eff) {
      const cur = readTrackZoom(track) ?? eff.min;
      const delta = hardwareZoomNudgeAmount(cap) * direction;
      const nextHz = Math.min(eff.max, Math.max(eff.min, cur + delta));
      const hardware = await applyHardwareZoomValue(track, nextHz, cap);
      if (hardware) {
        const ui = mapHardwareToUiZoom(readTrackZoom(track) ?? nextHz, cap);
        lotCameraZoomRef.current = ui;
        setLotCameraZoom(ui);
        lotCameraHardwareZoomRef.current = true;
        setLotCameraHardwareZoom(true);
        return;
      }
    }

    const z = clampLotCameraUiZoom(lotCameraZoomRef.current + direction * LOT_CAMERA_ZOOM_STEP);
    lotCameraZoomRef.current = z;
    setLotCameraZoom(z);
    lotCameraHardwareZoomRef.current = false;
    setLotCameraHardwareZoom(false);
  }

  function setLotCameraZoomLevel(next: number) {
    void applyLotCameraZoomLevel(next);
  }

  async function applyLotCameraTapFocus(clientX: number, clientY: number) {
    const wrap = lotCameraZoomWrapRef.current;
    const stream = lotCameraStreamRef.current;
    if (!wrap || !stream) return;
    const rect = wrap.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return;
    const x = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    setLotCameraFocusRing({ xPct: x * 100, yPct: y * 100, key: Date.now() });

    const track = stream.getVideoTracks()[0];
    if (!track?.applyConstraints) return;

    const caps = track.getCapabilities?.() as VideoTrackCaps | undefined;
    const advanced: Record<string, unknown>[] = [];
    if (caps?.pointsOfInterest) {
      advanced.push({ pointsOfInterest: [{ x, y }] });
    }
    if (caps?.focusMode?.includes("single-shot")) {
      advanced.push({ focusMode: "single-shot" });
    } else if (caps?.focusMode?.includes("continuous")) {
      advanced.push({ focusMode: "continuous" });
    }

    try {
      if (advanced.length) {
        await track.applyConstraints({ advanced } as MediaTrackConstraints);
      }
    } catch {
      try {
        await track.applyConstraints({
          // Safari / WebKit: top-level pointsOfInterest
          pointsOfInterest: [{ x, y }]
        } as MediaTrackConstraints);
      } catch {
        /* device may not support tap-to-focus */
      }
    }
  }

  function onLotCameraPreviewPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    lotCameraTapStartRef.current = { x: e.clientX, y: e.clientY, t: Date.now() };
  }

  function onLotCameraPreviewPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const start = lotCameraTapStartRef.current;
    lotCameraTapStartRef.current = null;
    if (!start || lotCameraPinchRef.current) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (dx * dx + dy * dy > 24 * 24) return;
    if (Date.now() - start.t > 700) return;
    void applyLotCameraTapFocus(e.clientX, e.clientY);
  }

  function clearFolderPick() {
    void clearStagingDraft();
    clearStagingAwaitingGeminiJobId();
    setStagingSuccess("");
    singleLotCaptureChainingRef.current = false;
    setSingleLotManualSku("");
    setStagingViewerIndex(null);
    setStagingEditIndex(null);
    setStagingEditBusy(false);
    setStagingEditRotation(0);
    setStagingEditTrim(0);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
    setFolderPick((prev) => {
      if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
      return null;
    });
  }

  function restoreSingleLotStaging(files: File[], manualSku: string, skippedCount: number) {
    if (!files.length) return;
    const thumbUrls = files.map((f) => URL.createObjectURL(f));
    setFolderPick({
      folderLabel:
        files.length === 1 ? folderLabelFromFile(files[0]!) : `One lot (${files.length} photos)`,
      imageFiles: files,
      thumbUrls,
      skippedCount,
      singleLotMode: true
    });
    setSingleLotManualSku(manualSku);
  }

  function tryRemoveStagingImage(index: number) {
    const p = folderPickRef.current;
    if (!p || index < 0 || index >= p.imageFiles.length) return;
    const name = p.imageFiles[index]?.name || `photo ${index + 1}`;
    if (
      !window.confirm(
        `Remove “${name}” from the upload box?\n\nIt has not been uploaded to the server until you tap Upload.`
      )
    ) {
      return;
    }
    removeStagingImage(index);
  }

  function removeStagingImage(index: number) {
    setStagingSuccess("");
    setFolderPick((prev) => {
      if (!prev || index < 0 || index >= prev.imageFiles.length) return prev;
      const oldThumb = prev.thumbUrls[index];
      if (oldThumb) URL.revokeObjectURL(oldThumb);
      const nextFiles = prev.imageFiles.filter((_, j) => j !== index);
      const thumbUrls = prev.thumbUrls.filter((_, j) => j !== index);
      const folderLabel =
        prev.singleLotMode && nextFiles.length > 0
          ? nextFiles.length === 1
            ? folderLabelFromFile(nextFiles[0]!)
            : `One lot (${nextFiles.length} photos)`
          : prev.folderLabel;
      return { ...prev, imageFiles: nextFiles, thumbUrls, folderLabel };
    });
    setStagingViewerIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
    setStagingEditIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
  }

  function replaceStagingImageFile(index: number, file: File) {
    setFolderPick((prev) => {
      if (!prev || index < 0 || index >= prev.imageFiles.length) return prev;
      const nextFiles = [...prev.imageFiles];
      nextFiles[index] = file;
      const thumbUrls = [...prev.thumbUrls];
      const oldThumb = thumbUrls[index];
      if (oldThumb) URL.revokeObjectURL(oldThumb);
      thumbUrls[index] = URL.createObjectURL(file);
      return { ...prev, imageFiles: nextFiles, thumbUrls };
    });
  }

  function openStagingEdit(i: number) {
    const p = folderPickRef.current;
    if (!p || i < 0 || i >= p.imageFiles.length) return;
    setStagingViewerIndex(null);
    setStagingEditIndex(i);
    setStagingEditRotation(0);
    setStagingEditTrim(0);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
  }

  function closeStagingEdit() {
    if (stagingEditBusy) return;
    setStagingEditIndex(null);
  }

  async function applyStagingEdit() {
    const i = stagingEditIndex;
    if (i === null) return;
    const p = folderPickRef.current;
    const file = p?.imageFiles[i];
    if (!file) return;
    const adjust: RasterizeBasicAdjust = {
      brightnessPct: stagingEditBrightness,
      contrastPct: stagingEditContrast,
      saturationPct: stagingEditSaturation
    };
    if (
      stagingEditRotation === 0 &&
      stagingEditTrim === 0 &&
      isRasterAdjustDefault(adjust)
    ) {
      setStagingEditIndex(null);
      return;
    }
    setStagingEditBusy(true);
    try {
      const out = await rasterizeLotCameraEdit(
        file,
        stagingEditRotation,
        stagingEditTrim,
        adjust
      );
      if (!out) return;
      replaceStagingImageFile(i, out);
      setStagingEditIndex(null);
      setStagingEditRotation(0);
      setStagingEditTrim(0);
      setStagingEditBrightness(100);
      setStagingEditContrast(100);
      setStagingEditSaturation(100);
    } finally {
      setStagingEditBusy(false);
    }
  }

  function flashLotCameraShutter() {
    if (lotCameraShutterFlashTimerRef.current !== null) {
      window.clearTimeout(lotCameraShutterFlashTimerRef.current);
    }
    setLotCameraShutterFlash(true);
    lotCameraShutterFlashTimerRef.current = window.setTimeout(() => {
      setLotCameraShutterFlash(false);
      lotCameraShutterFlashTimerRef.current = null;
    }, 150);
  }

  async function bindLotCameraStream(stream: MediaStream) {
    const track = stream.getVideoTracks()[0];
    if (track) {
      lotCameraZoomCapRef.current = await configureLotCameraTrack(track);
      setLotCameraOpticalAvailable(!!lotCameraZoomCapRef.current);
      const cap = lotCameraZoomCapRef.current;
      const uiZ = lotCameraZoomRef.current;
      if (cap && uiZ > LOT_CAMERA_ZOOM_MIN + 0.001) {
        const hw = await applyHardwareZoomOnTrack(track, cap, uiZ);
        lotCameraHardwareZoomRef.current = hw;
        setLotCameraHardwareZoom(hw);
      }
    } else {
      lotCameraZoomCapRef.current = null;
      setLotCameraOpticalAvailable(false);
    }
    lotCameraStreamRef.current = stream;
    setLotCameraStream(stream);
  }

  function scheduleResumeLotCameraPreview() {
    if (!lotCameraOpen) return;
    if (lotCameraNativePickerOpenRef.current) return;
    if (lotCameraNativeChainTimerRef.current != null) return;
    if (lotCameraResumeTimerRef.current !== null) {
      window.clearTimeout(lotCameraResumeTimerRef.current);
    }
    lotCameraResumeTimerRef.current = window.setTimeout(() => {
      lotCameraResumeTimerRef.current = null;
      void resumeLotCameraPreview();
    }, 120);
  }

  /** Release inline getUserMedia so iOS can hand the lens to the native ``capture`` picker (especially landscape). */
  function suspendInlineLotCameraForNativePicker() {
    if (lotCameraResumeTimerRef.current != null) {
      window.clearTimeout(lotCameraResumeTimerRef.current);
      lotCameraResumeTimerRef.current = null;
    }
    lotCameraStreamRef.current?.getVideoTracks().forEach((t) => t.stop());
    lotCameraStreamRef.current = null;
    setLotCameraStream(null);
    setLotCameraPreviewReady(false);
  }

  async function resumeInlineLotCameraAfterNativePicker() {
    if (!lotCameraOpen || lotCameraNativePickerOpenRef.current) return;
    if (lotCameraNativeChainTimerRef.current != null) return;
    if (lotCameraNativeChainingRef.current) return;
    await reinitLotCameraStream();
  }

  async function reinitLotCameraStream() {
    if (typeof window === "undefined" || !window.isSecureContext || !lotCameraOpen) return;
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia) return;
    lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
    lotCameraStreamRef.current = null;
    setLotCameraStream(null);
    setLotCameraPreviewReady(false);
    try {
      const stream = await md.getUserMedia(LOT_CAMERA_MEDIA);
      await bindLotCameraStream(stream);
      setLotCameraVideoMountKey((k) => k + 1);
    } catch {
      setError("Camera stopped — close this screen and tap Add one lot again.");
    }
  }

  async function resumeLotCameraPreview() {
    if (!lotCameraOpen) return;
    if (lotCameraNativePickerOpenRef.current) return;
    if (lotCameraNativeChainTimerRef.current != null) return;
    const stream = lotCameraStreamRef.current;
    const track = stream?.getVideoTracks()[0];
    const coarse =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(pointer: coarse)").matches;

    if (!stream || !track || track.readyState === "ended") {
      await reinitLotCameraStream();
      return;
    }

    if (coarse) {
      // iOS keeps the track live (snaps look fine) but the preview layer stays black after app switch.
      await reinitLotCameraStream();
      return;
    }

    setLotCameraPreviewReady(false);
    setLotCameraVideoMountKey((k) => k + 1);
  }

  function stopLotCameraTracks() {
    if (lotCameraShutterFlashTimerRef.current !== null) {
      window.clearTimeout(lotCameraShutterFlashTimerRef.current);
      lotCameraShutterFlashTimerRef.current = null;
    }
    setLotCameraDeleteConfirmIndex(null);
    setLotCameraShutterFlash(false);
    if (lotCameraResumeTimerRef.current !== null) {
      window.clearTimeout(lotCameraResumeTimerRef.current);
      lotCameraResumeTimerRef.current = null;
    }
    lotCameraStreamRef.current?.getTracks().forEach((t) => t.stop());
    lotCameraStreamRef.current = null;
    setLotCameraStream(null);
    setLotCameraOpen(false);
    setLotCameraPreviewReady(false);
    setLotCameraViewerIndex(null);
    setLotCameraEditIndex(null);
    setLotCameraEditBusy(false);
    setLotCameraEditBrightness(100);
    setLotCameraEditContrast(100);
    setLotCameraEditSaturation(100);
    lotCameraPinchRef.current = null;
    if (lotCameraZoomApplyTimerRef.current != null) {
      window.clearTimeout(lotCameraZoomApplyTimerRef.current);
      lotCameraZoomApplyTimerRef.current = null;
    }
    lotCameraZoomCapRef.current = null;
    lotCameraZoomRef.current = LOT_CAMERA_ZOOM_MIN;
    setLotCameraZoom(LOT_CAMERA_ZOOM_MIN);
    lotCameraHardwareZoomRef.current = false;
    setLotCameraHardwareZoom(false);
    setLotCameraOpticalAvailable(false);
    lotCameraNativeChainingRef.current = false;
    lotCameraNativePickerOpenRef.current = false;
    if (lotCameraNativeChainTimerRef.current != null) {
      window.clearTimeout(lotCameraNativeChainTimerRef.current);
      lotCameraNativeChainTimerRef.current = null;
    }
    setLotCameraFocusRing(null);
    lotCameraTapStartRef.current = null;
  }

  function clearLotCameraSnapsAndThumbs() {
    setLotCameraSnapThumbUrls((urls) => {
      urls.forEach((u) => URL.revokeObjectURL(u));
      return [];
    });
    lotCameraSnapsRef.current = [];
    setLotCameraSnaps([]);
  }

  function pushLotCameraSnap(file: File): number {
    const index = lotCameraSnapsRef.current.length;
    lotCameraSnapsRef.current = [...lotCameraSnapsRef.current, file];
    setLotCameraSnaps([...lotCameraSnapsRef.current]);
    setLotCameraSnapThumbUrls((prev) => [...prev, URL.createObjectURL(file)]);
    return index;
  }

  function replaceLotCameraSnapAt(index: number, file: File) {
    if (index < 0 || index >= lotCameraSnapsRef.current.length) return;
    setLotCameraSnaps((prev) => {
      if (index < 0 || index >= prev.length) return prev;
      const next = [...prev];
      next[index] = file;
      lotCameraSnapsRef.current = next;
      return next;
    });
    setLotCameraSnapThumbUrls((prev) => {
      if (index < 0 || index >= prev.length) return prev;
      const next = [...prev];
      const old = next[index];
      if (old) URL.revokeObjectURL(old);
      next[index] = URL.createObjectURL(file);
      return next;
    });
  }

  function cancelLotCameraSession() {
    clearLotCameraSnapsAndThumbs();
    stopLotCameraTracks();
  }

  /** Cancel away from Snap/Done; confirm only when snaps would be lost (not uploaded until Done). */
  function tryCancelLotCameraSession() {
    const n = lotCameraSnapsRef.current.length;
    if (n > 0) {
      const msg =
        n === 1
          ? "Discard 1 captured photo and close the camera? Photos are not uploaded until you tap Done."
          : `Discard ${n} captured photos and close the camera? They are not uploaded until you tap Done.`;
      if (!window.confirm(msg)) return;
    }
    cancelLotCameraSession();
  }

  async function finishLotCameraSession() {
    lotCameraNativeChainingRef.current = false;
    if (lotCameraNativeChainTimerRef.current != null) {
      window.clearTimeout(lotCameraNativeChainTimerRef.current);
      lotCameraNativeChainTimerRef.current = null;
    }
    const files = [...lotCameraSnapsRef.current];
    if (!files.length) {
      stopLotCameraTracks();
      return;
    }
    const prev = folderPickRef.current;
    const replacing = prev?.imageFiles.length && !prev.singleLotMode;
    const detail = replacing
      ? `Add ${files.length} camera photo${files.length === 1 ? "" : "s"} as one lot and replace the current staged set?`
      : prev?.singleLotMode
        ? `Add ${files.length} more camera photo${files.length === 1 ? "" : "s"} to this lot?`
        : `Add ${files.length} camera photo${files.length === 1 ? "" : "s"} to the upload box?`;
    if (!confirmStagingOverwrite(prev, detail)) return;
    const stableFiles = await stabilizeCapturedFiles(files);
    clearLotCameraSnapsAndThumbs();
    stopLotCameraTracks();
    const { pick, droppedFromCap } = applySingleLotFilesToStaging(folderPickRef.current, stableFiles, 0);
    scrollToStagingPendingRef.current = true;
    setFolderPick(pick);
    scheduleScrollStagingUploadBoxIntoView();
    if (droppedFromCap > 0 && CTBIDS_MAX_PHOTOS_PER_LOT != null) {
      setError(
        `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. ${droppedFromCap} photo(s) from the camera were not added because the staging area was already full.`
      );
    }
  }

  function appendLotCameraSnapFiles(files: File[]) {
    if (!files.length) return;
    let dropped = 0;
    let added = 0;
    const newThumbs: string[] = [];
    const next = [...lotCameraSnapsRef.current];
    for (const f of files) {
      if (CTBIDS_MAX_PHOTOS_PER_LOT != null && next.length >= CTBIDS_MAX_PHOTOS_PER_LOT) {
        dropped += 1;
        continue;
      }
      next.push(f);
      newThumbs.push(URL.createObjectURL(f));
      added += 1;
    }
    lotCameraSnapsRef.current = next;
    setLotCameraSnaps(next);
    if (newThumbs.length) {
      setLotCameraSnapThumbUrls((prev) => [...prev, ...newThumbs]);
    }
    if (dropped > 0 && CTBIDS_MAX_PHOTOS_PER_LOT != null) {
      setError(`CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing.`);
    }
    if (added > 0) triggerLotCameraSnapFeedbackImmediate();
  }

  function replaceLotCameraSnapTail(stableFiles: File[], startIndex: number) {
    if (!stableFiles.length) return;
    const next = [...lotCameraSnapsRef.current];
    const newThumbs: string[] = [];
    for (let i = 0; i < stableFiles.length; i++) {
      const idx = startIndex + i;
      if (idx < 0 || idx >= next.length) continue;
      next[idx] = stableFiles[i]!;
    }
    lotCameraSnapsRef.current = next;
    setLotCameraSnaps(next);
    setLotCameraSnapThumbUrls((prev) => {
      const thumbs = [...prev];
      for (let i = 0; i < stableFiles.length; i++) {
        const idx = startIndex + i;
        if (idx < 0 || idx >= thumbs.length) continue;
        const old = thumbs[idx];
        if (old) URL.revokeObjectURL(old);
        thumbs[idx] = URL.createObjectURL(stableFiles[i]!);
      }
      return thumbs;
    });
  }

  function markNativePhotoPickerOpening() {
    nativePickerOpenedAtRef.current = Date.now();
  }

  function openPhotosFilePicker() {
    markNativePhotoPickerOpening();
    photosInputRef.current?.click();
  }

  function openFolderFilePicker() {
    markNativePhotoPickerOpening();
    folderInputRef.current?.click();
  }

  function openPhoneCameraForLot() {
    if (
      CTBIDS_MAX_PHOTOS_PER_LOT != null &&
      lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT
    ) {
      setError(`CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing.`);
      return;
    }
    if (lotCameraNativeChainTimerRef.current != null) {
      window.clearTimeout(lotCameraNativeChainTimerRef.current);
      lotCameraNativeChainTimerRef.current = null;
    }
    lotCameraNativeChainingRef.current = true;
    suspendInlineLotCameraForNativePicker();
    lotCameraNativePickerOpenRef.current = true;
    markNativePhotoPickerOpening();
    lotCameraNativeInputRef.current?.click();
  }

  async function onLotCameraNativeInputChange(list: FileList | null) {
    lotCameraNativePickerOpenRef.current = false;
    if (!list?.length) {
      lotCameraNativeChainingRef.current = false;
      void resumeInlineLotCameraAfterNativePicker();
      return;
    }
    const imageFiles = Array.from(list).filter(isCatalogImageFile);
    if (lotCameraNativeInputRef.current) lotCameraNativeInputRef.current.value = "";
    if (!imageFiles.length) {
      lotCameraNativeChainingRef.current = false;
      void resumeInlineLotCameraAfterNativePicker();
      return;
    }
    const startIndex = lotCameraSnapsRef.current.length;
    appendLotCameraSnapFiles(imageFiles);
    const stableFiles = await stabilizeCapturedFiles(imageFiles);
    replaceLotCameraSnapTail(stableFiles, startIndex);
    const atCap =
      CTBIDS_MAX_PHOTOS_PER_LOT != null &&
      lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT;
    if (lotCameraNativeChainingRef.current && !atCap && imageFiles.length === 1) {
      if (lotCameraNativeChainTimerRef.current != null) {
        window.clearTimeout(lotCameraNativeChainTimerRef.current);
      }
      const chainDelayMs = isScreenLandscape() ? 750 : 320;
      lotCameraNativeChainTimerRef.current = window.setTimeout(() => {
        lotCameraNativeChainTimerRef.current = null;
        if (!lotCameraNativeChainingRef.current) {
          void resumeInlineLotCameraAfterNativePicker();
          return;
        }
        if (
          CTBIDS_MAX_PHOTOS_PER_LOT != null &&
          lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT
        ) {
          lotCameraNativeChainingRef.current = false;
          void resumeInlineLotCameraAfterNativePicker();
          return;
        }
        suspendInlineLotCameraForNativePicker();
        lotCameraNativePickerOpenRef.current = true;
        markNativePhotoPickerOpening();
        lotCameraNativeInputRef.current?.click();
      }, chainDelayMs);
    } else {
      lotCameraNativeChainingRef.current = false;
      void resumeInlineLotCameraAfterNativePicker();
    }
  }

  async function snapLotPhoto() {
    if (
      CTBIDS_MAX_PHOTOS_PER_LOT != null &&
      lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT
    ) {
      setError(`CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing.`);
      return;
    }
    const v = lotCameraVideoRef.current;
    if (!v || v.readyState < 2 || v.videoWidth < 2) return;
    flashLotCameraShutter();
    triggerLotCameraSnapFeedbackImmediate();
    const z = Math.max(LOT_CAMERA_ZOOM_MIN, lotCameraZoomRef.current);
    const useDigitalCrop = z > LOT_CAMERA_ZOOM_MIN + 0.001 && !lotCameraHardwareZoomRef.current;

    const quickBlob = await captureVideoFrameBlob(v, z, useDigitalCrop);
    if (!quickBlob) return;
    if (
      CTBIDS_MAX_PHOTOS_PER_LOT != null &&
      lotCameraSnapsRef.current.length >= CTBIDS_MAX_PHOTOS_PER_LOT
    ) {
      return;
    }
    const snapId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID().slice(0, 8)
        : String(Math.random()).slice(2, 10);
    const quickFile = new File([quickBlob], `lot-${Date.now()}-${snapId}.jpg`, { type: "image/jpeg" });
    const snapIndex = pushLotCameraSnap(quickFile);

    const track = lotCameraStreamRef.current?.getVideoTracks()[0];
    const ImageCaptureCtor =
      typeof window !== "undefined"
        ? (window as Window & { ImageCapture?: new (t: MediaStreamTrack) => { takePhoto: () => Promise<Blob> } })
            .ImageCapture
        : undefined;
    const screenLandscape = isScreenLandscape();
    // Landscape: quick video-frame capture is already upright; takePhoto() JPEGs arrive with
    // their own EXIF orientation and replacing the thumb makes it flip sideways a moment later.
    if (ImageCaptureCtor && track && !useDigitalCrop && !screenLandscape) {
      void (async () => {
        try {
          const ic = new ImageCaptureCtor(track);
          const hqBlob = await ic.takePhoto();
          if (!hqBlob || snapIndex >= lotCameraSnapsRef.current.length) return;
          const hqFile = new File([hqBlob], quickFile.name, {
            type: hqBlob.type || "image/jpeg"
          });
          const normalized = await normalizeCaptureFileOrientation(hqFile);
          replaceLotCameraSnapAt(snapIndex, normalized ?? hqFile);
        } catch {
          /* keep live-frame capture */
        }
      })();
    }
  }

  function openLotCameraDeleteConfirm(index: number) {
    if (index < 0 || index >= lotCameraSnapsRef.current.length) return;
    setLotCameraDeleteConfirmIndex(index);
  }

  function cancelLotCameraDeleteConfirm() {
    setLotCameraDeleteConfirmIndex(null);
    void resumeLotCameraPreview();
  }

  function confirmLotCameraDeleteSnap() {
    const i = lotCameraDeleteConfirmIndex;
    setLotCameraDeleteConfirmIndex(null);
    if (i === null) return;
    removeLotCameraSnap(i);
    void resumeLotCameraPreview();
  }

  function removeLotCameraSnap(index: number) {
    setLotCameraSnapThumbUrls((prev) => {
      if (index < 0 || index >= prev.length) return prev;
      const old = prev[index];
      if (old) URL.revokeObjectURL(old);
      return prev.filter((_, j) => j !== index);
    });
    setLotCameraSnaps((prev) => {
      if (index < 0 || index >= prev.length) return prev;
      const next = prev.filter((_, j) => j !== index);
      lotCameraSnapsRef.current = next;
      return next;
    });
    setLotCameraViewerIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
    setLotCameraEditIndex((cur) => {
      if (cur === null) return null;
      if (cur === index) return null;
      if (cur > index) return cur - 1;
      return cur;
    });
  }

  function openLotCameraEdit(i: number) {
    if (i < 0 || i >= lotCameraSnapsRef.current.length) return;
    setLotCameraViewerIndex(null);
    setLotCameraEditIndex(i);
    setLotCameraEditRotation(0);
    setLotCameraEditTrim(0);
    setLotCameraEditBrightness(100);
    setLotCameraEditContrast(100);
    setLotCameraEditSaturation(100);
  }

  function closeLotCameraEdit() {
    if (lotCameraEditBusy) return;
    setLotCameraEditIndex(null);
  }

  async function applyLotCameraEdit() {
    const i = lotCameraEditIndex;
    if (i === null) return;
    const file = lotCameraSnapsRef.current[i];
    if (!file) return;
    const adjust: RasterizeBasicAdjust = {
      brightnessPct: lotCameraEditBrightness,
      contrastPct: lotCameraEditContrast,
      saturationPct: lotCameraEditSaturation
    };
    if (
      lotCameraEditRotation === 0 &&
      lotCameraEditTrim === 0 &&
      isRasterAdjustDefault(adjust)
    ) {
      setLotCameraEditIndex(null);
      return;
    }
    setLotCameraEditBusy(true);
    try {
      const out = await rasterizeLotCameraEdit(
        file,
        lotCameraEditRotation,
        lotCameraEditTrim,
        adjust
      );
      if (!out) return;
      replaceLotCameraSnapAt(i, out);
      setLotCameraEditIndex(null);
      setLotCameraEditRotation(0);
      setLotCameraEditTrim(0);
      setLotCameraEditBrightness(100);
      setLotCameraEditContrast(100);
      setLotCameraEditSaturation(100);
    } finally {
      setLotCameraEditBusy(false);
    }
  }

  /** Desktop: open multi-file picker as one listing. Touch devices: camera when available, else picker. */
  async function openAddOneLotFlow() {
    if (typeof window === "undefined") return;
    const coarse =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(pointer: coarse)").matches;
    if (coarse) {
      await openCreateOneLotCameraFlow();
      return;
    }
    singleLotCaptureChainingRef.current = false;
    uploadPickModeRef.current = "single_lot";
    openPhotosFilePicker();
  }

  function openChoosePhotosFlow() {
    singleLotCaptureChainingRef.current = false;
    uploadPickModeRef.current = "batch";
    openPhotosFilePicker();
  }

  async function openCreateOneLotCameraFlow() {
    if (typeof window === "undefined") return;
    const md = navigator.mediaDevices;
    if (window.isSecureContext && md?.getUserMedia) {
      markNativePhotoPickerOpening();
      clearLotCameraSnapsAndThumbs();
      setLotCameraPreviewReady(false);
      setLotCameraViewerIndex(null);
      setLotCameraEditIndex(null);
      setLotCameraEditBusy(false);
      setLotCameraEditBrightness(100);
      setLotCameraEditContrast(100);
      setLotCameraEditSaturation(100);
      lotCameraZoomRef.current = LOT_CAMERA_ZOOM_MIN;
      setLotCameraZoom(LOT_CAMERA_ZOOM_MIN);
      setLotCameraOpen(true);
      try {
        const stream = await md.getUserMedia(LOT_CAMERA_MEDIA);
        lotCameraHardwareZoomRef.current = false;
        setLotCameraHardwareZoom(false);
        await bindLotCameraStream(stream);
        primeLotCameraAudio();
      } catch {
        lotCameraStreamRef.current = null;
        setLotCameraStream(null);
        setLotCameraOpen(false);
        singleLotCaptureChainingRef.current = true;
        uploadPickModeRef.current = "single_lot";
        openPhotosFilePicker();
      }
      return;
    }
    singleLotCaptureChainingRef.current = true;
    uploadPickModeRef.current = "single_lot";
    openPhotosFilePicker();
  }

  async function onFolderInputChange(list: FileList | null) {
    if (!list?.length) {
      if (singleLotCaptureChainingRef.current) {
        singleLotCaptureChainingRef.current = false;
      }
      return;
    }
    const pickMode = uploadPickModeRef.current;
    uploadPickModeRef.current = "batch";
    const all = Array.from(list);
    const imageFiles = all.filter(isCatalogImageFile);
    if (!imageFiles.length) {
      if (folderInputRef.current) folderInputRef.current.value = "";
      if (photosInputRef.current) photosInputRef.current.value = "";
      return;
    }
    const skippedCount = all.length - imageFiles.length;
    const label = folderLabelFromFile(all[0]!);
    const singleLotMode = pickMode === "single_lot";
    if (singleLotMode && imageFiles.length > 1) {
      singleLotCaptureChainingRef.current = false;
    }
    const chainAnotherCamera =
      singleLotMode &&
      singleLotCaptureChainingRef.current &&
      imageFiles.length === 1;

    if (singleLotMode) {
      const prev = folderPickRef.current;
      if (
        prev?.imageFiles.length &&
        !prev.singleLotMode &&
        !confirmStagingOverwrite(
          prev,
          `Use ${imageFiles.length} selected photo${imageFiles.length === 1 ? "" : "s"} as one lot and replace what is currently staged?`
        )
      ) {
        if (photosInputRef.current) photosInputRef.current.value = "";
        return;
      }
      const tailStart = folderPickRef.current?.singleLotMode
        ? (folderPickRef.current?.imageFiles.length ?? 0)
        : 0;
      const { pick, droppedFromCap } = applySingleLotFilesToStaging(
        folderPickRef.current,
        imageFiles,
        skippedCount
      );
      setFolderPick(pick);
      setStagingSuccess("");
      if (droppedFromCap > 0 && CTBIDS_MAX_PHOTOS_PER_LOT != null) {
        setError(
          `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. ${droppedFromCap} photo(s) were not added.`
        );
      }
      void stabilizeCapturedFiles(imageFiles).then((stableImageFiles) => {
        if (!stableImageFiles.length) return;
        setFolderPick((p) => {
          if (!p?.singleLotMode) return p;
          const nextFiles = [...p.imageFiles];
          const nextThumbs = [...p.thumbUrls];
          for (let i = 0; i < stableImageFiles.length; i++) {
            const idx = tailStart + i;
            if (idx < 0 || idx >= nextFiles.length) continue;
            nextFiles[idx] = stableImageFiles[i]!;
            const oldThumb = nextThumbs[idx];
            if (oldThumb) URL.revokeObjectURL(oldThumb);
            nextThumbs[idx] = URL.createObjectURL(stableImageFiles[i]!);
          }
          return { ...p, imageFiles: nextFiles, thumbUrls: nextThumbs };
        });
      });
    } else {
      if (
        !confirmStagingOverwrite(
          folderPickRef.current,
          `Use folder “${label}” (${imageFiles.length} photo${imageFiles.length === 1 ? "" : "s"}) and replace what is currently in the upload box?`
        )
      ) {
        if (folderInputRef.current) folderInputRef.current.value = "";
        return;
      }
      setFolderPick((prev) => {
        if (prev?.thumbUrls.length) revokeObjectUrls(prev.thumbUrls);
        const thumbUrls = imageFiles.map((f) => URL.createObjectURL(f));
        return {
          folderLabel: label,
          imageFiles,
          thumbUrls,
          skippedCount,
          singleLotMode
        };
      });
      setStagingSuccess("");
    }
    if (folderInputRef.current) folderInputRef.current.value = "";
    if (photosInputRef.current) photosInputRef.current.value = "";

    if (chainAnotherCamera) {
      window.setTimeout(() => {
        if (!singleLotCaptureChainingRef.current) return;
        uploadPickModeRef.current = "single_lot";
        openPhotosFilePicker();
      }, 280);
    }
  }

  async function enqueueJob() {
    if (!selectedSaleId) {
      setError("Select or create a sale first.");
      return;
    }
    setError("");
    try {
      const created = await apiFetch("/jobs", token, {
        method: "POST",
        body: JSON.stringify({
          input_folder: folder,
          sale_id: selectedSaleId,
          inherit_sku_from_subfolder: inheritSkuFromSubfolder,
          single_lot_mode: false
        })
      });
      setJobs((prev) => [created, ...prev]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to create job";
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      setError(msg);
    }
  }

  async function uploadStagedFolder() {
    if (!folderPick?.imageFiles.length) return;
    if (!selectedSaleId) {
      setError("Select or create a sale first.");
      return;
    }
    setError("");
    setStagingSuccess("");
    setUploadProgress("");
    setStagingViewerIndex(null);
    setStagingEditIndex(null);
    setStagingEditBusy(false);
    setStagingEditBrightness(100);
    setStagingEditContrast(100);
    setStagingEditSaturation(100);
    const isSingleLot = folderPick.singleLotMode === true;
    const files = folderPick.imageFiles;
    const manualSkuBackup = singleLotManualSku;
    const skippedCount = folderPick.skippedCount;
    const total = files.length;
    const emptyFiles = files.filter((f) => f.size < 1);
    if (emptyFiles.length) {
      setError(
        `${emptyFiles.length} staged photo${emptyFiles.length === 1 ? "" : "s"} had no image data — retake or re-pick those files before uploading.`
      );
      return;
    }
    if (
      isSingleLot &&
      CTBIDS_MAX_PHOTOS_PER_LOT != null &&
      total > CTBIDS_MAX_PHOTOS_PER_LOT
    ) {
      setError(
        `CTBids allows at most ${CTBIDS_MAX_PHOTOS_PER_LOT} photos per listing. Remove ${total - CTBIDS_MAX_PHOTOS_PER_LOT} photo(s) before uploading.`
      );
      return;
    }
    if (isSingleLot) {
      clearFolderPick();
    }
    setUploading(true);
    try {
      const buildFormForSlice = (slice: File[]) => {
        const form = new FormData();
        form.append("sale_id", selectedSaleId);
        form.append("inherit_sku_from_subfolder", inheritSkuFromSubfolder ? "true" : "false");
        form.append("single_lot_mode", isSingleLot ? "true" : "false");
        if (isSingleLot && manualSkuBackup.trim()) {
          form.append("single_lot_manual_sku", manualSkuBackup.trim());
        }
        for (const file of slice) {
          const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
          form.append("files", file, rel);
        }
        return form;
      };

      let created: Job;
      const onUploadPart =
        (label: string) => (ev: { loaded: number; total: number | null }) => {
          setUploadProgress(uploadProgressFromBytes(label, ev.loaded, ev.total));
        };

      if (total <= UPLOAD_FILE_CHUNK) {
        setUploadProgress(`Starting upload of ${total} photo${total === 1 ? "" : "s"}…`);
        created = jobFromUploadResponse(
          await apiUploadFiles(
            "/jobs/upload",
            token,
            buildFormForSlice(files),
            900_000,
            onUploadPart(`Upload ${total} photos`)
          )
        );
      } else {
        const batchCount = Math.ceil(total / UPLOAD_FILE_CHUNK);
        setUploadProgress(`Uploading batch 1/${batchCount} (${Math.min(UPLOAD_FILE_CHUNK, total)} of ${total} files)…`);
        const first = files.slice(0, UPLOAD_FILE_CHUNK);
        created = jobFromUploadResponse(
          await apiUploadFiles(
            "/jobs/upload",
            token,
            buildFormForSlice(first),
            900_000,
            onUploadPart(`Batch 1/${batchCount}`)
          )
        );
        let uploaded = first.length;
        let batchIndex = 2;
        while (uploaded < total) {
          const slice = files.slice(uploaded, uploaded + UPLOAD_FILE_CHUNK);
          setUploadProgress(
            `Uploading batch ${batchIndex}/${batchCount} (${slice.length} files, ${uploaded + slice.length}/${total} total)…`
          );
          const form = new FormData();
          for (const file of slice) {
            const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
            form.append("files", file, rel);
          }
          created = jobFromUploadResponse(
            await apiUploadFiles(
              `/jobs/${created.id}/upload-files`,
              token,
              form,
              900_000,
              onUploadPart(`Batch ${batchIndex}/${batchCount}`)
            )
          );
          uploaded += slice.length;
          batchIndex += 1;
        }
        setUploadProgress(`All ${total} files uploaded — starting job…`);
      }
      setJobs((prev) => [created, ...prev]);
      if (!isSingleLot) {
        persistStagingAwaitingGemini(created.id, total);
      }
      setUploadProgress("Starting catalog job…");
      try {
        const running = (await apiFetch(`/jobs/${created.id}/run`, token, { method: "POST" })) as Job;
        if (running.status === "processing") {
          liveProgressJobIdRef.current = running.id;
          setLiveProgressJobId(running.id);
        } else if (running.status === "queued") {
          void apiFetch(`/jobs?sale_id=${encodeURIComponent(selectedSaleId)}`, token)
            .then((d) => setJobs(d as Job[]))
            .catch(() => {
              /* kick_queue runs on GET /jobs */
            });
        }
        setJobs((prev) => prev.map((j) => (j.id === running.id ? running : j)));
        setUploadProgress("");
        setError("");
        setStagingSuccess(
          isSingleLot
            ? running.status === "queued"
              ? `Uploaded ${total} photo${total === 1 ? "" : "s"} as one lot — queued. It will start automatically when the current lot finishes. You can add another lot now.`
              : `Uploaded ${total} photo${total === 1 ? "" : "s"} as one lot — upload box cleared. Catalog is running; you can add the next lot now.`
            : running.status === "queued"
              ? `Uploaded ${total} photo${total === 1 ? "" : "s"} — queued until the current job finishes.`
              : `Uploaded ${total} photo${total === 1 ? "" : "s"} — catalog is running. Staged previews clear automatically once all ${total} photo${total === 1 ? " has" : "s have"} been sent to Gemini (catalog may still be building lots).`
        );
      } catch (runErr) {
        const runMsg = parseApiErrorMessage(
          runErr instanceof Error ? runErr.message : "",
          "Could not start the catalog job"
        );
        setError(
          isSingleLot
            ? `${runMsg} Your ${total} photo${total === 1 ? "" : "s"} are on the server — tap Run on batch “${jobInputLabel(created.input_folder)}”.`
            : `${runMsg} Your ${total} photo${total === 1 ? "" : "s"} are on the server — tap Run on batch “${jobInputLabel(created.input_folder)}”. Staged previews clear after Gemini analyzes them, then you can stage the next lot.`
        );
      }
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Upload failed";
      const msg = parseApiErrorMessage(raw, "Upload failed");
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      if (isSingleLot) {
        restoreSingleLotStaging(files, manualSkuBackup, skippedCount);
      }
      setError(
        isSingleLot
          ? `${msg} Your staged photos were restored in the upload box — fix the issue and try again without refreshing.`
          : `${msg} Your staged photos are still in the upload box — fix the issue and try again without refreshing.`
      );
    } finally {
      setUploading(false);
      setUploadProgress("");
    }
  }

  async function runJob(jobId: string) {
    setError("");
    const pick = folderPickRef.current;
    const isSingleLot = pick?.singleLotMode === true && (pick?.imageFiles.length ?? 0) > 0;
    if (isSingleLot) {
      clearFolderPick();
      setStagingSuccess("Catalog started — upload box cleared so you can stage the next lot.");
    } else if (pick?.imageFiles.length) {
      const n = pick.imageFiles.length;
      persistStagingAwaitingGemini(jobId, n);
      setStagingSuccess(
        `Catalog started — staged photos clear automatically after all ${n} photo${n === 1 ? " is" : "s are"} sent to Gemini.`
      );
    }
    try {
      const updated = (await apiFetch(`/jobs/${jobId}/run`, token, { method: "POST" })) as Job;
      if (updated.status === "processing") {
        liveProgressJobIdRef.current = updated.id;
        setLiveProgressJobId(updated.id);
      }
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
      if (updated.status === "queued") {
        setStagingSuccess("Job queued — it will start automatically when the current lot finishes.");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Run failed";
      if (isAuthFailureMessage(msg)) {
        localStorage.removeItem("token");
        router.replace("/");
        return;
      }
      setError(msg);
      try {
        const sid = selectedSaleIdRef.current;
        if (!sid) return;
        const refreshed = await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token);
        setJobs(refreshed as Job[]);
      } catch {
        /* ignore */
      }
    }
  }

  async function cancelRun(jobId: string) {
    setError("");
    setCancelNotice("");
    setCancellingJobId(jobId);
    try {
      const updated = (await apiFetch(`/jobs/${jobId}/cancel`, token, { method: "POST" })) as Job;
      const sid = selectedSaleIdRef.current;
      if (!sid) return;
      const jlist = (await apiFetch(`/jobs?sale_id=${encodeURIComponent(sid)}`, token)) as Job[];
      setJobs(jlist);
      const row = jlist.find((j) => j.id === jobId) ?? updated;
      if (row.status === "processing") {
        setCancelNotice(
          "Cancel requested — the job will stop after the current AI step finishes (usually under a minute)."
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancellingJobId((prev) => (prev === jobId ? null : prev));
    }
  }

  function logout() {
    localStorage.removeItem("token");
    router.replace("/");
  }

  async function deleteLotRow(lotId: string) {
    if (!window.confirm("Delete this lot from the database?")) return;
    setError("");
    try {
      await apiFetch(`/lots/${lotId}`, token, { method: "DELETE" });
      setLots((prev) => prev.filter((l) => l.id !== lotId));
      setPhotoHealthMissing((prev) => {
        if (!(lotId in prev)) return prev;
        const next = { ...prev };
        delete next[lotId];
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function toggleLotCompleted(lotId: string, nextValue: boolean) {
    setError("");
    try {
      const updated = (await apiFetch(`/lots/${lotId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ is_completed: nextValue })
      })) as Lot;
      setLots((prev) => prev.map((l) => (l.id === lotId ? updated : l)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update completion");
    }
  }

  async function deleteJobRow(jobId: string) {
    if (
      !window.confirm(
        "Remove this job from the list?\n\nYour catalog lots are kept. Upload folders that still back lot photos are not deleted."
      )
    )
      return;
    setError("");
    try {
      await apiFetch(`/jobs/${jobId}`, token, { method: "DELETE" });
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Delete failed";
      if (msg.includes("409")) {
        setError("Wait until the job finishes processing before deleting.");
        return;
      }
      setError(msg);
    }
  }

  async function deleteAllCompletedJobs() {
    if (!completedJobs.length) return;
    const n = completedJobs.length;
    if (
      !window.confirm(
        `Remove ${n} completed job record${n === 1 ? "" : "s"} from the list?\n\nYour catalog lots are kept. Upload folders that still back lot photos are not deleted.`
      )
    )
      return;
    setError("");
    const ids = completedJobs.map((j) => j.id);
    try {
      for (const id of ids) {
        await apiFetch(`/jobs/${id}`, token, { method: "DELETE" });
      }
      setJobs((prev) => prev.filter((j) => j.status !== "completed"));
      setCompletedJobsExpanded(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function createSale() {
    const name = newSaleName.trim();
    if (!name || !token) return;
    setCreatingSale(true);
    setError("");
    try {
      const created = (await apiFetch("/sales", token, {
        method: "POST",
        body: JSON.stringify({ name })
      })) as Sale;
      setSales((prev) => [...prev, created]);
      setNewSaleName("");
      setSelectedSaleId(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create sale");
    } finally {
      setCreatingSale(false);
    }
  }

  async function endSelectedSale() {
    if (!token || !selectedSaleId || selectedSaleClosed) return;
    const target = selectedSale?.name || "this sale";
    if (!window.confirm(`Close "${target}"? Closed sales remain visible but should no longer be used for new work.`)) {
      return;
    }
    setClosingSale(true);
    setError("");
    try {
      const updated = (await apiFetch(`/sales/${selectedSaleId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ is_closed: true })
      })) as Sale;
      setSales((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close sale");
    } finally {
      setClosingSale(false);
    }
  }

  async function refreshAdminAccounts() {
    if (!token || !adminLoadAttempted) return;
    setAdminError("");
    setAdminLoading(true);
    try {
      const rows = (await apiFetch("/admin/accounts", token)) as AdminAccount[];
      setAdminAccounts(rows);
      const drafts: Record<string, string> = {};
      const nameDrafts: Record<string, string> = {};
      rows.forEach((a) => {
        drafts[a.id] = a.plan_code || "beta_free";
        nameDrafts[a.id] = a.name || "";
      });
      setAdminNameDraftById(nameDrafts);
      setAdminPlanDraftById(drafts);
      setIsMasterAdmin(true);
      setAdminUsageLoading(true);
      try {
        const usage = (await apiFetch("/admin/analytics?since_days=30", token)) as AdminUsageSummary;
        setAdminUsage(usage);
      } catch {
        setAdminUsage(null);
      } finally {
        setAdminUsageLoading(false);
      }
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not refresh admin accounts");
    } finally {
      setAdminLoading(false);
    }
  }

  async function patchAdminAccount(
    accountId: string,
    body: { is_active?: boolean; plan_code?: string; name?: string }
  ) {
    if (!token) return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      const updated = (await apiFetch(`/admin/accounts/${encodeURIComponent(accountId)}`, token, {
        method: "PATCH",
        body: JSON.stringify(body)
      })) as AdminAccount;
      setAdminAccounts((prev) => prev.map((a) => (a.id === accountId ? updated : a)));
      setAdminPlanDraftById((prev) => ({
        ...prev,
        [accountId]: updated.plan_code || prev[accountId] || "beta_free"
      }));
      setAdminNameDraftById((prev) => ({
        ...prev,
        [accountId]: updated.name || prev[accountId] || ""
      }));
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not update account");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function toggleArchiveAdminAccount(accountId: string, nextActive: boolean) {
    if (!token) return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      const endpoint = nextActive
        ? `/admin/accounts/${encodeURIComponent(accountId)}/restore`
        : `/admin/accounts/${encodeURIComponent(accountId)}/archive`;
      const updated = (await apiFetch(endpoint, token, { method: "POST" })) as AdminAccount;
      setAdminAccounts((prev) => prev.map((a) => (a.id === accountId ? updated : a)));
      setAdminPlanDraftById((prev) => ({
        ...prev,
        [accountId]: updated.plan_code || prev[accountId] || "beta_free"
      }));
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not update archive status");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function deleteAdminAccount(accountId: string, label: string) {
    if (!token) return;
    const typed = window.prompt(
      `Delete account "${label}"?\n\nThis permanently removes users, sales, lots, photos, jobs, and exports.\nType DELETE to confirm.`
    );
    if (typed !== "DELETE") return;
    setAdminSavingAccountId(accountId);
    setAdminError("");
    try {
      await apiFetch(`/admin/accounts/${encodeURIComponent(accountId)}`, token, { method: "DELETE" });
      setAdminAccounts((prev) => prev.filter((a) => a.id !== accountId));
      setAdminNameDraftById((prev) => {
        const next = { ...prev };
        delete next[accountId];
        return next;
      });
      setAdminPlanDraftById((prev) => {
        const next = { ...prev };
        delete next[accountId];
        return next;
      });
      setAdminUsage((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          total_accounts: Math.max(0, prev.total_accounts - 1),
          by_account: prev.by_account.filter((u) => u.account_id !== accountId)
        };
      });
    } catch (e) {
      setAdminError(e instanceof Error ? e.message : "Could not delete account");
    } finally {
      setAdminSavingAccountId("");
    }
  }

  async function purgeCurrentSaleCatalog() {
    if (!selectedSaleId) {
      setError("Select a sale first.");
      return;
    }
    const saleLabel = selectedSale?.name?.trim() || "this sale";
    if (
      !window.confirm(
        `Delete ALL lots, photos, and upload jobs for "${saleLabel}" only?\n\nOther sales on your account are not affected. This cannot be undone.`
      )
    ) {
      return;
    }
    setError("");
    try {
      await apiFetch(`/sales/${encodeURIComponent(selectedSaleId)}/purge-catalog`, token, {
        method: "POST"
      });
      setLots([]);
      setPhotoHealthMissing({});
      setJobs([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not clear this sale");
    }
  }

  async function purgeEntireAccountCatalog() {
    const typed = window.prompt(
      "Delete ALL lots, photos, and jobs across EVERY sale on this account?\n\nSales names remain but every catalog is emptied.\n\nType PURGE ALL to confirm."
    );
    if (typed !== "PURGE ALL") {
      return;
    }
    setError("");
    try {
      await apiFetch("/lots/purge-all", token, { method: "POST" });
      setLots([]);
      setPhotoHealthMissing({});
      setJobs([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Account purge failed");
    }
  }

  const exportLinks = useMemo(() => {
    const q = selectedSaleId ? `?sale_id=${encodeURIComponent(selectedSaleId)}` : "";
    return {
      markdown: `${API_BASE}/exports/markdown${q}`,
      json: `${API_BASE}/exports/json${q}`,
      csv: `${API_BASE}/exports/csv${q}`
    };
  }, [selectedSaleId]);

  async function downloadExport(kind: "markdown" | "json" | "csv") {
    if (!token) return;
    setError("");
    setExporting(kind);
    try {
      const res = await fetch(exportLinks[kind], {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Export failed (${res.status})`);
      }

      const blob = await res.blob();
      const ext = kind === "markdown" ? "md" : kind;
      const saleName = sales.find((s) => s.id === selectedSaleId)?.name?.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
      const baseName = saleName || selectedSaleId || "all-sales";
      const fileName = `ct-export-${baseName}.${ext}`;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not export");
    } finally {
      setExporting("");
    }
  }

  async function startSalePhotosZipExport() {
    if (!token || !selectedSaleId) return;
    setError("");
    setSaleZipBusy(true);
    try {
      const row = (await apiFetch(
        `/exports/sale-photos-zip?sale_id=${encodeURIComponent(selectedSaleId)}`,
        token,
        { method: "POST" }
      )) as SaleZipExport;
      setSaleZipExport(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start photo ZIP export");
    } finally {
      setSaleZipBusy(false);
    }
  }

  async function downloadSaleZipExport() {
    if (!token || !saleZipExport?.id || !saleZipExport.ready) return;
    setError("");
    try {
      const res = await fetch(
        `${API_BASE}/exports/sale-photos-zip/${encodeURIComponent(saleZipExport.id)}/download`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store"
        }
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Download failed (${res.status})`);
      }
      const blob = await res.blob();
      const saleName = sales.find((s) => s.id === selectedSaleId)?.name?.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
      const base = saleName || selectedSaleId || "sale";
      const fileName = `sale-${base}-photos.zip`;
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not download ZIP");
    }
  }

  const saleTopBar = (
    <label className="ct-app-sale-select">
      <span className="ct-app-sale-select__label">Change sale</span>
      <select
        className="ct-input ct-app-sale-select__input"
        value={selectedSaleId}
        onChange={(e) => {
          setSelectedSaleId(e.target.value);
          setError("");
        }}
        disabled={!sales.length}
        aria-label="Select sale"
      >
        {!sales.length ? <option value="">No sales yet</option> : null}
        {sales.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
            {s.closed_at ? " (Closed)" : ""}
          </option>
        ))}
      </select>
    </label>
  );

  const shellActions = (
    <>
      <Link href="/account" className="ct-btn ct-btn--ghost ct-app-topbar-btn">
        Account
      </Link>
      <button type="button" className="ct-btn ct-btn--ghost ct-app-topbar-btn" onClick={logout}>
        Log out
      </button>
    </>
  );

  const dashboardMobileBottomBar = (
    <>
      <button
        type="button"
        className="ct-app-mobile-bar__btn"
        onClick={openChoosePhotosFlow}
        disabled={uploading || !selectedSaleId || selectedSaleClosed}
      >
        <ImagePlus size={20} aria-hidden />
        Choose photos
      </button>
      <button
        type="button"
        className="ct-app-mobile-bar__btn ct-app-mobile-bar__btn--primary"
        onClick={() => void openAddOneLotFlow()}
        disabled={uploading || !selectedSaleId || selectedSaleClosed}
      >
        <Plus size={20} aria-hidden />
        Add one lot
      </button>
    </>
  );

  const showJobsCard =
    dashboardStats.completedJobCount > 0 || activeJobs.length > 0 || photoPendingSlots.length > 0;

  const jobsPanelContent = (
    <>
      {photoPendingSlots.length > 0 ? (
        <PhotoProcessingProgressPanel
          slots={photoPendingSlots}
          analyzedDone={geminiAnalyzedCount.done}
          analyzedTotal={geminiAnalyzedCount.total}
          singleLotMode={Boolean(processingJob?.single_lot_mode)}
          pipelinePhase={pipelinePhase}
          pipelinePhotoSelection={pipelinePhotoSelection}
          inInventoryPhase={inInventoryPhase}
          catalogStepProgress={catalogStepProgress}
        />
      ) : null}
      {activeJobs.length === 0 && completedJobs.length === 0 && photoPendingSlots.length === 0 ? (
        <p className="ct-muted ct-dash-jobs-card__empty">No jobs yet.</p>
      ) : null}
      {activeJobs.length > 0 ? (
        <ul className="ct-job-list ct-dash-jobs-card__list">
          {activeJobs.map((job) => (
            <li key={job.id}>
              <span className="ct-job-status">{job.status}</span>
              <span className="ct-job-path" title={job.input_folder}>
                {jobInputLabel(job.input_folder)}
              </span>
              {job.inherit_sku_from_subfolder ? (
                <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="Lot SKUs taken from digit folder names">
                  SKU←folders
                </span>
              ) : null}
              {job.single_lot_mode ? (
                <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="All photos merged into one catalog lot">
                  1 lot
                </span>
              ) : null}
              {job.single_lot_manual_sku ? (
                <span className="ct-muted" style={{ fontSize: "0.78rem" }} title="Manual SKU on this job">
                  SKU:{job.single_lot_manual_sku}
                </span>
              ) : null}
              {job.status === "queued" && busyProcessing ? (
                <span className="ct-muted" style={{ fontSize: "0.82rem" }}>
                  Waiting for current lot…
                </span>
              ) : null}
              {(job.status === "queued" || job.status === "failed") && (
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  onClick={() => void runJob(job.id)}
                  disabled={job.status === "queued" && busyProcessing}
                >
                  {job.status === "failed" ? "Run again" : "Run now"}
                </button>
              )}
              {job.status === "processing" && (
                <button
                  type="button"
                  className="ct-btn"
                  onClick={() => void cancelRun(job.id)}
                  disabled={cancellingJobId === job.id}
                >
                  {cancellingJobId === job.id ? "Cancelling…" : "Cancel"}
                </button>
              )}
              {job.error_message ? (
                <span style={{ color: "var(--ct-error)", fontSize: "0.85rem" }}>— {job.error_message}</span>
              ) : null}
              {job.status !== "processing" && (
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost"
                  onClick={() => void deleteJobRow(job.id)}
                  disabled={busyProcessing}
                >
                  Delete job
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {completedJobs.length > 0 ? (
        <div className="ct-jobs-completed ct-dash-jobs-card__completed">
          <div className="ct-jobs-completed-bar">
            <button
              type="button"
              className="ct-jobs-completed-toggle"
              onClick={() => setCompletedJobsExpanded((v) => !v)}
              aria-expanded={completedJobsExpanded}
            >
              {completedJobsExpanded ? (
                <ChevronDown size={16} aria-hidden />
              ) : (
                <ChevronRight size={16} aria-hidden />
              )}
              <span>
                {completedJobs.length} completed {completedJobs.length === 1 ? "job" : "jobs"}
              </span>
            </button>
            <button
              type="button"
              className="ct-btn ct-btn--ghost"
              style={{ fontSize: "0.82rem", padding: "0.25rem 0.55rem" }}
              onClick={() => void deleteAllCompletedJobs()}
              disabled={busyProcessing}
            >
              Clear job list
            </button>
          </div>
          {completedJobsExpanded ? (
            <ul className="ct-job-list ct-job-list--compact">
              {completedJobs.map((job) => (
                <li key={job.id}>
                  <span className="ct-job-status ct-job-status--done">{job.status}</span>
                  <span className="ct-job-path" title={job.input_folder}>
                    {jobInputLabel(job.input_folder)}
                  </span>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    style={{ fontSize: "0.8rem", padding: "0.2rem 0.45rem", marginLeft: "auto" }}
                    onClick={() => void deleteJobRow(job.id)}
                    disabled={busyProcessing}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </>
  );

  if (!token) {
    return (
      <AppShell actions={shellActions}>
        <div className="ct-dashboard">
          <section className="ct-dash-panel">
            <p style={{ margin: 0 }}>Please sign in first.</p>
            <p style={{ margin: "0.75rem 0 0" }}>
              <Link href="/" className="ct-btn ct-btn--primary">
                Go to sign in
              </Link>
            </p>
          </section>
        </div>
      </AppShell>
    );
  }
  if (loading) {
    return (
      <AppShell topBar={saleTopBar} actions={shellActions} mobileBottomBar={dashboardMobileBottomBar}>
        <div className="ct-dashboard">
          <section className="ct-dash-panel ct-dash-panel--loading" aria-busy="true">
            <p style={{ margin: 0 }}>Loading dashboard…</p>
          </section>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      topBar={saleTopBar}
      actions={shellActions}
      mobileBottomBar={dashboardMobileBottomBar}
    >
      <div id="dashboard-top" className="ct-dashboard">
        <div className="ct-dash-stats-row">
          <div className="ct-dashboard-hero-band">
            <header className="ct-dashboard-hero">
              <h1 className="ct-dashboard-hero__title">
                Lots dashboard
                {selectedSale ? (
                  <span className="ct-dashboard-hero__sale"> — {selectedSale.name}</span>
                ) : null}
              </h1>
              {selectedSaleClosed ? (
                <p className="ct-muted ct-dashboard-hero__note">This sale is closed — uploads and edits are disabled.</p>
              ) : null}
            </header>
            <section className="ct-dash-stats" aria-label="Sale summary">
              <div className="ct-stat-card">
                <span className="ct-stat-card__value">{dashboardStats.totalLots}</span>
                <span className="ct-stat-card__label">Total lots</span>
              </div>
              <div className="ct-stat-card">
                <span className="ct-stat-card__value">{dashboardStats.photoTotal}</span>
                <span className="ct-stat-card__label">Photos uploaded</span>
              </div>
              <div className="ct-stat-card">
                <span className="ct-stat-card__value">{dashboardStats.withCondition}</span>
                <span className="ct-stat-card__label">With condition</span>
              </div>
            </section>
          </div>
          {showJobsCard ? (
            <section className="ct-dash-panel ct-dash-jobs-card" aria-label="Processing jobs">
              <div className="ct-dash-jobs-card__head">
                <span className="ct-dash-jobs-card__title">Recent jobs</span>
                {activeJobs.length > 0 ? (
                  <span className="ct-dash-jobs-card__badge ct-dash-jobs-card__badge--active">
                    {activeJobs.length} active
                  </span>
                ) : (
                  <span className="ct-dash-jobs-card__badge ct-dash-jobs-card__badge--done">
                    {dashboardStats.completedJobCount} completed
                  </span>
                )}
              </div>
              {completedJobs.length > 0 && activeJobs.length === 0 ? (
                <button
                  type="button"
                  className="ct-btn ct-btn--ghost ct-dash-jobs-card__clear"
                  onClick={() => void deleteAllCompletedJobs()}
                  disabled={busyProcessing}
                >
                  Clear job list
                </button>
              ) : null}
              <div className="ct-dash-jobs-card__body">{jobsPanelContent}</div>
            </section>
          ) : null}
        </div>

        <section className="ct-dash-panel ct-dash-sale-manage">
          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", alignItems: "center" }}>
            <input
              className="ct-input"
              style={{ maxWidth: 320, width: "100%", flex: "1 1 200px" }}
              placeholder="New sale name — e.g. Oak Street estate"
              value={newSaleName}
              onChange={(e) => setNewSaleName(e.target.value)}
              disabled={creatingSale}
            />
            <button
              type="button"
              className="ct-btn ct-btn--primary"
              disabled={creatingSale || !newSaleName.trim() || !token}
              onClick={() => void createSale()}
            >
              {creatingSale ? "Creating…" : "Add sale"}
            </button>
            <button
              type="button"
              className="ct-btn"
              disabled={!selectedSaleId || selectedSaleClosed || closingSale || busyProcessing}
              onClick={() => void endSelectedSale()}
            >
              {closingSale ? "Closing…" : "End sale"}
            </button>
          </div>
          {!sales.length ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.88rem" }}>
              Add a sale to start cataloging lots and photos.
            </p>
          ) : null}
        </section>
      {(busyProcessing || queuedJobCount > 0) && (
        <div className="ct-banner ct-banner--warning">
          <p style={{ margin: 0 }}>
            <strong>{busyProcessing ? "Working on your photos." : "Lots are queued."}</strong> New lots will appear in
            the table below as they are ready. Stacked <strong>Add one lot</strong> uploads run{" "}
            <strong>one at a time</strong>
            {queuedJobCount > 0
              ? ` (${queuedJobCount} waiting in queue${queuedJobCount === 1 ? "" : ""})`
              : ""}
            . You can still run <strong>Regenerate AI</strong> on another lot. Use <strong>Cancel</strong> on the job row
            if you need to stop.
          </p>
          {cancelNotice ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              {cancelNotice}
            </p>
          ) : null}
          {processingJob?.single_lot_mode && pipelinePhase && !inInventoryPhase ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              <strong>Step 1 — Add one lot:</strong>{" "}
              {pipelinePhase === "pipeline_triage"
                ? `Sorting ${geminiAnalyzedCount.total || "your"} photos (${geminiAnalyzedCount.done} / ${geminiAnalyzedCount.total || "…"} done). Rows update as each frame is classified.`
                : pipelinePhase === "pipeline_listing"
                  ? "Writing the listing draft from the best photos."
                  : pipelinePhase === "pipeline_pro_proofread"
                    ? "Pro is proofreading title, brands, and details."
                    : "Working…"}
            </p>
          ) : geminiAnalyzedCount.total > 0 && !inInventoryPhase ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              <strong>Step 1 — Reading photos:</strong> {geminiAnalyzedCount.done} / {geminiAnalyzedCount.total} done
              {geminiAnalyzedCount.done < geminiAnalyzedCount.total
                ? " (this usually moves every few seconds)."
                : " — finishing up, then lots are saved."}
            </p>
          ) : null}
          {inInventoryPhase ? (
            <p className="ct-muted" style={{ margin: "0.65rem 0 0", fontSize: "0.92rem" }}>
              <strong>Step 2 — Saving lots:</strong>{" "}
              {catalogStepProgress ? (
                <>
                  photo {catalogStepProgress.index + 1} of {catalogStepProgress.total}
                  {catalogStepProgress.fileName ? (
                    <>
                      {" "}
                      — <code style={{ fontSize: "0.88em" }}>{catalogStepProgress.fileName}</code>
                    </>
                  ) : null}
                  . If this stays stuck for several minutes, try Cancel and run the job again, or ask your tech contact
                  to check the server.
                </>
              ) : (
                <>starting…</>
              )}
            </p>
          ) : null}
          <details className="ct-help-details" style={{ marginTop: "0.65rem", marginBottom: 0 }}>
            <summary>For staff: how the live table works</summary>
            <div className="ct-help-details__body">
              You may see one <strong>Processing…</strong> row per photo until each frame is analyzed; rows then turn
              into catalog entries. Updates stream from the server in real time. If you switch away from this tab,
              progress may refresh more slowly — come back to this page to see the latest.
            </div>
          </details>
        </div>
      )}
      {error ? <div className="ct-banner ct-banner--error">{error}</div> : null}
      {stagingSuccess ? <div className="ct-banner ct-banner--success">{stagingSuccess}</div> : null}
      {uploadProgress ? (
        <div className="ct-banner ct-banner--warning" style={{ marginBottom: "0.5rem" }}>
          {uploadProgress}
        </div>
      ) : null}
      {isMasterAdmin || adminLoading || adminError ? (
        <section className="ct-dash-panel ct-dash-admin" aria-label="Master admin">
          <h2 className="ct-dash-panel__title">Master admin</h2>
          <p className="ct-muted" style={{ marginTop: 0, marginBottom: "0.75rem", maxWidth: "54rem" }}>
            Manage pilot accounts from one place: toggle access and set a plan label for internal tracking.
          </p>
          <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.75rem" }}>
            <button
              type="button"
              className="ct-btn"
              onClick={() => void refreshAdminAccounts()}
              disabled={adminLoading || !isMasterAdmin}
            >
              {adminLoading ? "Refreshing…" : "Refresh accounts"}
            </button>
            <input
              className="ct-input"
              style={{ maxWidth: 360, width: "100%" }}
              placeholder="Search account name, owner email, or ID"
              value={adminSearch}
              onChange={(e) => setAdminSearch(e.target.value)}
              disabled={!isMasterAdmin}
            />
            <button
              type="button"
              className="ct-btn"
              onClick={() => setAdminSearch("")}
              disabled={!isMasterAdmin || !adminSearch.trim()}
            >
              Clear
            </button>
          </div>
          {adminError ? <div className="ct-banner ct-banner--error">{adminError}</div> : null}
          {!isMasterAdmin && !adminLoading ? (
            <p className="ct-muted" style={{ margin: 0 }}>
              Your account is not in <code>MASTER_ADMIN_EMAILS</code>, so this panel is read-only hidden.
            </p>
          ) : null}
          {isMasterAdmin ? (
            <div className="ct-dash-admin__stats">
              <h3 className="ct-dash-admin__stats-title">Analytics (last 30 days)</h3>
              {adminUsageLoading ? (
                <p className="ct-muted" style={{ margin: 0 }}>
                  Loading analytics…
                </p>
              ) : adminUsage ? (
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.9rem" }}>
                  {adminUsage.totals.jobs_started} jobs started ({adminUsage.totals.jobs_completed} completed,{" "}
                  {adminUsage.totals.jobs_failed} failed), {adminUsage.totals.photos_uploaded} photos uploaded,{" "}
                  avg {adminUsage.totals.avg_photos_per_job.toFixed(2)} photos/job, est tokens{" "}
                  {(adminUsage.totals.est_input_tokens + adminUsage.totals.est_output_tokens).toLocaleString()} (
                  est ${adminUsage.totals.est_total_cost_usd.toFixed(2)}).
                </p>
              ) : (
                <p className="ct-muted" style={{ margin: 0 }}>
                  Analytics data unavailable yet.
                </p>
              )}
            </div>
          ) : null}
          {isMasterAdmin ? (
            <div className="ct-dashboard-table-desktop ct-table-wrap ct-dash-admin-table">
              <table className="ct-table ct-table--admin">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Plan</th>
                    <th>Status</th>
                    <th>Usage</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAdminAccounts.map((a) => {
                    const busy = adminSavingAccountId === a.id;
                    const draft = adminPlanDraftById[a.id] ?? a.plan_code ?? "beta_free";
                    const nameDraft = adminNameDraftById[a.id] ?? a.name ?? "";
                    const usage = adminUsage?.by_account.find((u) => u.account_id === a.id) || null;
                    return (
                      <tr key={a.id}>
                        <td>
                          <input
                            className="ct-input"
                            style={{ width: 220, fontWeight: 600 }}
                            value={nameDraft}
                            onChange={(e) =>
                              setAdminNameDraftById((prev) => ({ ...prev, [a.id]: e.target.value }))
                            }
                            disabled={busy}
                          />
                          {a.owner_email ? (
                            <div className="ct-muted" style={{ fontSize: "0.8rem" }}>
                              {a.owner_email}
                            </div>
                          ) : null}
                          <div className="ct-muted" style={{ fontSize: "0.8rem" }}>
                            {a.id}
                          </div>
                        </td>
                        <td>
                          <input
                            className="ct-input"
                            style={{ width: 150 }}
                            value={draft}
                            onChange={(e) =>
                              setAdminPlanDraftById((prev) => ({ ...prev, [a.id]: e.target.value }))
                            }
                            disabled={busy}
                          />
                        </td>
                        <td>{a.is_active ? "Active" : "Inactive"}</td>
                        <td className="ct-muted" style={{ fontSize: "0.85rem" }}>
                          {a.user_count} users · {a.sale_count} sales · {a.lot_count} lots · {a.job_count} jobs
                          {usage ? (
                            <>
                              <br />
                              30d: {usage.photos_uploaded} photos · avg {usage.avg_photos_per_job.toFixed(2)} photos/job
                              · est tokens {(usage.est_input_tokens + usage.est_output_tokens).toLocaleString()} · est $
                              {usage.est_total_cost_usd.toFixed(2)}
                            </>
                          ) : null}
                        </td>
                        <td>
                          <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap" }}>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void toggleArchiveAdminAccount(a.id, !a.is_active)}
                              disabled={busy}
                            >
                              {busy ? "Saving…" : a.is_active ? "Archive" : "Restore"}
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() =>
                                void patchAdminAccount(a.id, {
                                  name: (nameDraft || "").trim() || a.name
                                })
                              }
                              disabled={busy || (nameDraft || "").trim().length < 2}
                            >
                              Save name
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void patchAdminAccount(a.id, { plan_code: draft.trim() || "beta_free" })}
                              disabled={busy || draft.trim().length < 2}
                            >
                              Save plan
                            </button>
                            <button
                              type="button"
                              className="ct-btn"
                              onClick={() => void deleteAdminAccount(a.id, a.name || a.id)}
                              disabled={busy}
                              title="Permanently delete this account and all related data"
                            >
                              Delete account
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredAdminAccounts.length === 0 && !adminLoading ? (
                    <tr>
                      <td colSpan={5} className="ct-muted">
                        No accounts match that search.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      ) : null}

        <div className="ct-dash-grid">
          <div className="ct-dash-col ct-dash-col--left">
            <section
              className={`ct-dash-panel ct-dash-upload${folderPick ? " ct-dash-upload--active" : ""}`}
            >
              <input
                ref={folderInputRef}
                type="file"
                multiple
                style={{ display: "none" }}
                accept="image/*,.heic,.heif"
                {...({ webkitdirectory: "" } as Record<string, unknown>)}
                onChange={(e) => onFolderInputChange(e.target.files)}
              />
              <input
                ref={photosInputRef}
                type="file"
                multiple
                style={{ display: "none" }}
                accept="image/*,.heic,.heif"
                onChange={(e) => onFolderInputChange(e.target.files)}
              />

              <div className="ct-dash-upload__desktop">
                <h2 className="ct-dash-panel__title">Upload photos</h2>
                <p className="ct-upload-intro">
                  Add images for the selected sale. Review thumbnails, then upload.
                </p>

                <div className="ct-upload-options" role="group" aria-label="Select photos">
                  <div className="ct-upload-option ct-upload-option--featured">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <FolderOpen size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Choose folder</span>
                      <span className="ct-upload-option__desc">
                        Entire folder from disk; one subfolder or estate tape per lot.
                      </span>
                    </div>
                    <button
                      type="button"
                      className="ct-btn ct-btn--primary ct-upload-option__btn"
                      onClick={() => {
                        singleLotCaptureChainingRef.current = false;
                        uploadPickModeRef.current = "batch";
                        openFolderFilePicker();
                      }}
                      disabled={uploading || !selectedSaleId || selectedSaleClosed}
                    >
                      Select folder…
                    </button>
                  </div>
                  <div className="ct-upload-option">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <ImagePlus size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Choose photos</span>
                      <span className="ct-upload-option__desc">
                        Individual files with thumbnail preview in the picker.
                      </span>
                    </div>
                    <button
                      type="button"
                      className="ct-btn ct-upload-option__btn"
                      onClick={openChoosePhotosFlow}
                      disabled={uploading || !selectedSaleId || selectedSaleClosed}
                    >
                      Select photos…
                    </button>
                  </div>
                  <div className="ct-upload-option">
                    <span className="ct-upload-option__icon" aria-hidden>
                      <Plus size={20} strokeWidth={1.75} />
                    </span>
                    <div className="ct-upload-option__text">
                      <span className="ct-upload-option__title">Add one lot</span>
                      <span className="ct-upload-option__desc">
                        All selected images become one catalog row; SKU from tape when visible.
                      </span>
                    </div>
                    <button
                      type="button"
                      className="ct-btn ct-upload-option__btn"
                      onClick={() => void openAddOneLotFlow()}
                      disabled={uploading || !selectedSaleId || selectedSaleClosed}
                    >
                      Add one lot…
                    </button>
                  </div>
                </div>

                <label className="ct-upload-setting">
                  <input
                    type="checkbox"
                    checked={inheritSkuFromSubfolder}
                    onChange={(e) => setInheritSkuFromSubfolder(e.target.checked)}
                  />
                  <span className="ct-upload-setting__label">
                    <strong>Use subfolder names as lot numbers</strong>
                    <span className="ct-upload-setting__hint">
                      Numeric folder names (e.g. <code>035</code>, <code>036</code>) set the SKU instead of
                      reading tape in photos.
                    </span>
                  </span>
                </label>

                <details className="ct-help-details">
                  <summary>Upload guide</summary>
                  <div className="ct-help-details__body">
                    <ul>
                      <li>
                        <strong>Choose folder</strong> opens your system folder dialog — thumbnails may not appear
                        there; previews show here after you confirm.
                      </li>
                      <li>
                        <strong>Choose photos</strong> is preferred on phones when you need previews in the picker.
                      </li>
                      <li>
                        Large folders upload in chunks of about <strong>{UPLOAD_FILE_CHUNK} images</strong> per request
                        into the <strong>same</strong> job so the browser does not time out.
                      </li>
                      <li>
                        <strong>Separate items:</strong> use one subfolder per item (e.g. <code>035/</code>,{" "}
                        <code>036/</code>) when not using estate tape on every group.
                      </li>
                      <li>
                        <strong>One flat folder:</strong> colored estate tape marks new lots; subfolders work if you
                        prefer not to use tape on every group.
                      </li>
                      <li>
                        <strong>Add one lot</strong> merges every selected image into one catalog row. On touch devices
                        the camera may open first; desktop opens the file picker directly.
                      </li>
                      <li>
                        While a job runs, progress appears in <strong>Recent jobs</strong>; you can cancel from the job
                        row.
                      </li>
                    </ul>
                  </div>
                </details>

                <details className="ct-help-details ct-upload-staff">
                  <summary>Staff — server path job</summary>
                  <div className="ct-help-details__body">
                    <p style={{ margin: "0 0 0.65rem" }}>
                      Queue processing for images already on the API server. Nothing uploads from the browser.
                    </p>
                    <div className="ct-upload-staff__row">
                      <input
                        className="ct-input"
                        value={folder}
                        onChange={(e) => setFolder(e.target.value)}
                        placeholder="Path on the server where images already live"
                        aria-label="Server folder path for queued job"
                      />
                      <button
                        type="button"
                        className="ct-btn"
                        onClick={() => void enqueueJob()}
                        disabled={!folder || uploading || !selectedSaleId || selectedSaleClosed}
                      >
                        Queue job
                      </button>
                    </div>
                  </div>
                </details>
              </div>

              {folderPick && !folderPick.singleLotMode ? (
                <label className="ct-upload-setting ct-upload-setting--compact ct-dash-upload__mobile-settings">
                  <input
                    type="checkbox"
                    checked={inheritSkuFromSubfolder}
                    onChange={(e) => setInheritSkuFromSubfolder(e.target.checked)}
                  />
                  <span className="ct-upload-setting__label">
                    <strong>Use subfolder names as lot numbers</strong>
                  </span>
                </label>
              ) : null}

        {folderPick && (
          <div
            id="staging-upload-box"
            className="ct-card ct-card--padded ct-upload-staging"
          >
            {folderPick.singleLotMode ? (
              <div style={{ marginBottom: "0.65rem" }}>
                <p className="ct-muted" style={{ margin: "0 0 0.5rem", fontSize: "0.88rem" }}>
                  <strong>One lot only:</strong> all photos below become <strong>one</strong> listing (one row in the
                  table). The SKU is read from colored estate tape when it appears in any frame, or you can set it
                  below.
                  {CTBIDS_MAX_PHOTOS_PER_LOT != null ? (
                    <>
                      {" "}
                      CTBids allows at most <strong>{CTBIDS_MAX_PHOTOS_PER_LOT}</strong> photos per listing — extra
                      files are skipped.
                    </>
                  ) : null}
                </p>
                <label className="ct-muted" style={{ display: "block", fontSize: "0.85rem" }}>
                  SKU for this lot <span style={{ fontWeight: 400 }}>(optional)</span>
                  <input
                    className="ct-input"
                    style={{ width: "100%", maxWidth: 360, marginTop: 6, display: "block" }}
                    value={singleLotManualSku}
                    onChange={(e) => setSingleLotManualSku(e.target.value)}
                    placeholder="e.g. 035 or Lot #042"
                    autoComplete="off"
                    enterKeyHint="done"
                  />
                </label>
              </div>
            ) : null}
            <div className="ct-row ct-row--gap-lg" style={{ marginBottom: "0.65rem" }}>
              <strong>
                {folderPick.imageFiles.length
                  ? folderPick.singleLotMode
                    ? CTBIDS_MAX_PHOTOS_PER_LOT != null
                      ? `${folderPick.imageFiles.length} / ${CTBIDS_MAX_PHOTOS_PER_LOT} image${
                          folderPick.imageFiles.length === 1 ? "" : "s"
                        } in “${folderPick.folderLabel}”`
                      : `${folderPick.imageFiles.length} image${
                          folderPick.imageFiles.length === 1 ? "" : "s"
                        } in “${folderPick.folderLabel}”`
                    : `${folderPick.imageFiles.length} image${folderPick.imageFiles.length === 1 ? "" : "s"} in “${folderPick.folderLabel}”`
                  : `No catalog images found in “${folderPick.folderLabel}”`}
              </strong>
              {folderPick.skippedCount > 0 ? (
                <span className="ct-muted" style={{ fontSize: "0.82rem" }}>
                  ({folderPick.skippedCount} non-image file{folderPick.skippedCount === 1 ? "" : "s"} skipped)
                </span>
              ) : null}
            </div>
            {folderPick.imageFiles.length > 0 ? (
              <p className="ct-muted" style={{ margin: "0 0 0.5rem", fontSize: "0.8rem" }}>
                {folderPick.singleLotMode
                  ? "Tap Upload to send — this box clears right away so you can photograph the next lot while catalog runs"
                  : "Staged photos stay until Gemini has analyzed every upload, then this box clears (catalog may still be building lots)"}
                {stagingDraftSupported() ? " (also saved on this device if you refresh)" : ""}. Tap to enlarge;
                pencil = edit; trash = remove (asks to confirm). Use Clear selection to empty early.
              </p>
            ) : null}
            {folderPick.imageFiles.length > 0 && (
              <div
                className="ct-thumb-strip"
                style={{ flexWrap: "nowrap", overflowX: "auto", WebkitOverflowScrolling: "touch" }}
              >
                {folderPick.thumbUrls.map((url, i) => (
                  <div
                    key={url}
                    style={{
                      position: "relative",
                      flex: "0 0 auto",
                      width: 72,
                      height: 72
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => !uploading && setStagingViewerIndex(i)}
                      aria-label={`Preview staged image ${i + 1}`}
                      disabled={uploading}
                      title={folderPick.imageFiles[i]?.name ?? ""}
                      style={{
                        display: "block",
                        width: 72,
                        height: 72,
                        padding: 0,
                        border: "1px solid var(--ct-border)",
                        borderRadius: 6,
                        overflow: "hidden",
                        cursor: uploading ? "not-allowed" : "pointer",
                        background: "var(--ct-surface)",
                        opacity: uploading ? 0.55 : 1
                      }}
                    >
                      <img
                        src={url}
                        alt=""
                        width={72}
                        height={72}
                        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", pointerEvents: "none" }}
                      />
                    </button>
                    <button
                      type="button"
                      aria-label={`Edit staged image ${i + 1}`}
                      disabled={uploading}
                      onClick={(e) => {
                        e.stopPropagation();
                        openStagingEdit(i);
                      }}
                      className="ct-btn ct-btn--ghost"
                      style={{
                        position: "absolute",
                        bottom: -2,
                        left: -2,
                        minWidth: 34,
                        minHeight: 34,
                        width: 34,
                        height: 34,
                        padding: 0,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "rgba(12,15,18,0.92)",
                        border: "1px solid rgba(255,255,255,0.35)",
                        color: "#c8d4ff",
                        boxShadow: "0 1px 4px rgba(0,0,0,0.25)"
                      }}
                    >
                      <Pencil size={15} aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove staged image ${i + 1}`}
                      disabled={uploading}
                      onClick={(e) => {
                        e.stopPropagation();
                        tryRemoveStagingImage(i);
                      }}
                      className="ct-btn ct-btn--ghost"
                      style={{
                        position: "absolute",
                        top: -2,
                        right: -2,
                        minWidth: 34,
                        minHeight: 34,
                        width: 34,
                        height: 34,
                        padding: 0,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "rgba(12,15,18,0.92)",
                        border: "1px solid rgba(255,255,255,0.35)",
                        color: "#f5a5a5",
                        boxShadow: "0 1px 4px rgba(0,0,0,0.25)"
                      }}
                    >
                      <Trash2 size={15} aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="ct-row ct-row--gap-lg">
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                onClick={() => void uploadStagedFolder()}
                disabled={uploading || !folderPick.imageFiles.length || !selectedSaleId || selectedSaleClosed}
              >
                {uploading
                  ? "Uploading…"
                  : folderPick.singleLotMode
                    ? `Upload ${folderPick.imageFiles.length} photo(s) as one lot`
                    : `Upload ${folderPick.imageFiles.length} photo(s) to server`}
              </button>
              <button
                type="button"
                className="ct-btn"
                disabled={uploading}
                onClick={() => {
                  const n = folderPick.imageFiles.length;
                  const msg =
                    n === 0
                      ? "Close this selection? Nothing has been uploaded yet."
                      : n === 1
                        ? "Remove the 1 staged photo from this upload box? Nothing has been uploaded yet."
                        : `Remove all ${n} staged photos from this upload box? Nothing has been uploaded yet.`;
                  if (!window.confirm(msg)) return;
                  clearFolderPick();
                }}
              >
                Clear selection
              </button>
            </div>
          </div>
        )}
            </section>

          </div>

          <div className="ct-dash-col ct-dash-col--right">
            <section className="ct-dash-panel ct-dash-lots" aria-label="Catalog lots">
      {photoHealthLotCount > 0 ? (
        <div className="ct-banner ct-banner--error" style={{ marginBottom: "0.75rem" }}>
          <strong>
            {photoHealthLotCount} lot{photoHealthLotCount === 1 ? "" : "s"} missing photo files on the server.
          </strong>{" "}
          Rows below are flagged — open <strong>Details</strong> to see which frames are gone and use{" "}
          <strong>Add photos…</strong> to re-upload.
        </div>
      ) : null}
      <header className="ct-dash-lots__header">
        <h2 className="ct-dash-panel__title">Lots ({lots.length})</h2>
        <p className="ct-dash-lots__stats">
          {lots.filter((l) => l.is_completed).length} completed ·{" "}
          {lots.filter((l) => !l.is_completed).length} not done
        </p>
        <p className="ct-upload-intro ct-dash-lots__intro">
          Open <strong>Details</strong> to edit listing copy, photos, and SKU for each lot.
        </p>
        <div className="ct-dash-lots__toolbar">
          <div className="ct-dash-lots__toolbar-filters">
            <label className="ct-row ct-row--gap-lg" style={{ alignItems: "center", gap: "0.45rem" }}>
              <span className="ct-dash-lots__filter-label">SKU checks</span>
              <select
                className="ct-input"
                value={inventorySkuFlagFilter}
                onChange={(e) =>
                  setInventorySkuFlagFilter(
                    (e.target.value || "") as "" | "duplicate" | "text_mismatch" | "any"
                  )
                }
                aria-label="Filter lots by SKU sanity flags"
              >
                <option value="">All lots</option>
                <option value="any">Flagged: any issue</option>
                <option value="duplicate">Duplicate SKU (same sale)</option>
                <option value="text_mismatch">Mixed lot # in text</option>
              </select>
            </label>
          </div>
          <div className="ct-dash-lots__toolbar-actions">
            <button
              type="button"
              className="ct-btn ct-btn--danger-outline"
              onClick={() => void purgeCurrentSaleCatalog()}
              disabled={busyProcessing || !selectedSaleId}
              title={
                selectedSaleId
                  ? "Remove lots, photos, and jobs for the selected sale only"
                  : "Select a sale first"
              }
            >
              Clear lots &amp; jobs (this sale)
            </button>
            <button
              type="button"
              className="ct-btn ct-btn--ghost ct-dash-lots__danger-link"
              onClick={() => void purgeEntireAccountCatalog()}
              disabled={busyProcessing}
              title="Removes catalog data for every sale on this account"
            >
              Clear entire account…
            </button>
          </div>
        </div>
      </header>
      <div className="ct-dashboard-lots-mobile" aria-label="Lots (mobile layout)">
        {lots.map((lot) => (
          <div
            key={`m-${lot.id}`}
            className={`ct-lot-mobile-card ct-card ct-card--padded${
              lot.many_photos_review ||
              lot.sku_duplicate_in_sale ||
              lot.sku_text_mismatch ||
              (photoHealthMissing[lot.id] ?? 0) > 0
                ? " ct-lot-mobile-card--warn"
                : ""
            }`}
          >
            <div className="ct-lot-mobile-card__main">
              <div className="ct-lot-mobile-card__thumb">
                <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={56} />
              </div>
              <div className="ct-lot-mobile-card__body">
                <span className={lotSkuBadgeClass(lot)} title={lotSkuCellTitle(lot)}>
                  {lot.sku || "—"}
                </span>
                <div className="ct-lot-mobile-card__title">{lot.auction_title || "—"}</div>
                <div className="ct-lot-mobile-card__meta">
                  <span className="ct-lot-mobile-card__meta-item">
                    <strong>{lot.photo_count}</strong> photo{lot.photo_count === 1 ? "" : "s"}
                  </span>
                  {(photoHealthMissing[lot.id] ?? 0) > 0 ? (
                    <span className="ct-lot-mobile-card__meta-item ct-lot-mobile-card__meta-item--error">
                      {photoHealthMissing[lot.id]} missing
                    </span>
                  ) : null}
                  <span className="ct-lot-mobile-card__meta-item">{formatLotPrice(lot.starting_price)}</span>
                  {lot.condition_text ? (
                    <span className="ct-lot-condition-chip">{lot.condition_text}</span>
                  ) : null}
                </div>
              </div>
            </div>
            <div className="ct-lot-mobile-card__actions">
              <Link
                href={`/lots/${lot.id}#listing-details`}
                className="ct-lots-table__details-btn ct-lot-mobile-card__details-btn"
                onClick={rememberDashboardScrollBeforeLotDetails}
              >
                Details
              </Link>
              <label
                className={`ct-lot-detail-status ct-lot-mobile-card__status${
                  lot.is_completed ? " ct-lot-detail-status--done" : ""
                }`}
              >
                <input
                  type="checkbox"
                  checked={lot.is_completed}
                  onChange={(e) => void toggleLotCompleted(lot.id, e.target.checked)}
                />
                <span>{lot.is_completed ? "Done" : "Not done"}</span>
              </label>
              <button
                type="button"
                className="ct-btn ct-btn--danger-outline ct-lot-mobile-card__delete-btn"
                onClick={() => void deleteLotRow(lot.id)}
                disabled={busyProcessing}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="ct-dashboard-table-desktop ct-table-wrap ct-dash-lots-table">
        <table className="ct-table ct-table--lots">
        <thead>
          <tr>
            <th scope="col">Photo</th>
            <th scope="col">SKU</th>
            <th scope="col">Auction Title</th>
            <th scope="col">Price</th>
            <th scope="col">Condition</th>
            <th scope="col">Status</th>
            <th scope="col">Photos</th>
            <th scope="col">Details</th>
            <th scope="col">Delete</th>
          </tr>
        </thead>
        <tbody>
          {lots.map((lot) => (
            <tr
              key={lot.id}
              className={
                lot.many_photos_review ||
                lot.sku_duplicate_in_sale ||
                lot.sku_text_mismatch ||
                (photoHealthMissing[lot.id] ?? 0) > 0
                  ? "ct-table-row--photo-outlier"
                  : undefined
              }
            >
              <td className="ct-table__photo">
                <LotThumbnail lotId={lot.id} token={token} hasThumbnail={lot.has_thumbnail} size={56} />
              </td>
              <td className="ct-sku-cell" title={lotSkuCellTitle(lot)}>
                <span className={lotSkuBadgeClass(lot)}>{lot.sku || "—"}</span>
              </td>
              <td className="ct-lots-table__title" title={lot.auction_title || undefined}>
                {lot.auction_title || "—"}
              </td>
              <td className="ct-lots-table__price">{formatLotPrice(lot.starting_price)}</td>
              <td>
                {lot.condition_text ? (
                  <span className="ct-lot-condition-chip">{lot.condition_text}</span>
                ) : (
                  <span className="ct-muted">—</span>
                )}
              </td>
              <td>
                <label
                  className={`ct-lot-detail-status ct-lot-detail-status--table${
                    lot.is_completed ? " ct-lot-detail-status--done" : ""
                  }`}
                  style={{ cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={lot.is_completed}
                    onChange={(e) => void toggleLotCompleted(lot.id, e.target.checked)}
                  />
                  <span>{lot.is_completed ? "Done" : "Not done"}</span>
                </label>
              </td>
              <td className="ct-lots-table__photo-count">
                {lot.photo_count}
                {(photoHealthMissing[lot.id] ?? 0) > 0 ? (
                  <span
                    className="ct-lots-table__photo-missing"
                    title="Catalog photo records exist but files are missing on the server — open Details to re-upload."
                  >
                    {photoHealthMissing[lot.id]} missing
                  </span>
                ) : null}
              </td>
              <td>
                <Link
                  href={`/lots/${lot.id}#listing-details`}
                  className="ct-lots-table__details-btn"
                  onClick={rememberDashboardScrollBeforeLotDetails}
                  title={
                    (photoHealthMissing[lot.id] ?? 0) > 0
                      ? "Photos missing on server — open to re-upload"
                      : undefined
                  }
                >
                  Details
                </Link>
              </td>
              <td>
                <button
                  type="button"
                  className="ct-btn ct-btn--danger-outline ct-lots-table__delete-btn"
                  onClick={() => void deleteLotRow(lot.id)}
                  disabled={busyProcessing}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
        </table>
      </div>
            </section>
          </div>

          <section className="ct-dash-panel ct-dash-exports ct-dash-col--exports" aria-label="Sale exports">
            <h2 className="ct-dash-panel__title">Exports</h2>
            <p className="ct-upload-intro">Download catalog data and photos for the selected sale.</p>
            <div className="ct-upload-options" role="group" aria-label="Export formats">
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <FileText size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">Markdown</span>
                  <span className="ct-upload-option__desc">
                    Readable catalog — titles, descriptions, prices, and SKUs.
                  </span>
                </div>
                <button
                  type="button"
                  className="ct-btn ct-upload-option__btn"
                  onClick={() => void downloadExport("markdown")}
                  disabled={exporting !== "" && exporting !== "markdown"}
                >
                  {exporting === "markdown" ? "Downloading…" : "Download"}
                </button>
              </div>
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <Database size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">JSON</span>
                  <span className="ct-upload-option__desc">Structured catalog data for tools and integrations.</span>
                </div>
                <button
                  type="button"
                  className="ct-btn ct-upload-option__btn"
                  onClick={() => void downloadExport("json")}
                  disabled={exporting !== "" && exporting !== "json"}
                >
                  {exporting === "json" ? "Downloading…" : "Download"}
                </button>
              </div>
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <FileSpreadsheet size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">CTBids CSV</span>
                  <span className="ct-upload-option__desc">Spreadsheet formatted for CTBids catalog upload.</span>
                </div>
                <button
                  type="button"
                  className="ct-btn ct-upload-option__btn"
                  onClick={() => void downloadExport("csv")}
                  disabled={exporting !== "" && exporting !== "csv"}
                >
                  {exporting === "csv" ? "Downloading…" : "Download"}
                </button>
              </div>
              <div className="ct-upload-option">
                <span className="ct-upload-option__icon" aria-hidden>
                  <Archive size={20} strokeWidth={1.75} />
                </span>
                <div className="ct-upload-option__text">
                  <span className="ct-upload-option__title">Photo ZIP</span>
                  <span className="ct-upload-option__desc">
                    Every catalog photo for this sale — one folder per lot in the archive.
                  </span>
                </div>
                <div className="ct-upload-option__actions">
                  <button
                    type="button"
                    className="ct-btn ct-upload-option__btn"
                    onClick={() => void startSalePhotosZipExport()}
                    disabled={
                      !selectedSaleId ||
                      selectedSaleClosed ||
                      saleZipBusy ||
                      busyProcessing ||
                      saleZipExport?.status === "queued" ||
                      saleZipExport?.status === "processing"
                    }
                  >
                    {saleZipBusy ? "Starting…" : "Prepare ZIP…"}
                  </button>
                  {saleZipExport?.ready ? (
                    <button
                      type="button"
                      className="ct-btn ct-btn--primary ct-upload-option__btn"
                      onClick={() => void downloadSaleZipExport()}
                    >
                      Download ZIP
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
            <details className="ct-help-details ct-dash-exports__guide">
              <summary>Photo ZIP guide</summary>
              <div className="ct-help-details__body">
                <ul>
                  <li>
                    Original images, <strong>one folder per lot</strong> (named by SKU, e.g. <code>lot-045</code>).
                  </li>
                  <li>Progress counts update every few seconds while the server builds the archive.</li>
                  <li>
                    To zip <strong>one listing only</strong>, open that lot&apos;s Details page and use the download
                    there.
                  </li>
                </ul>
              </div>
            </details>
            {saleZipExport && (saleZipExport.status === "queued" || saleZipExport.status === "processing") ? (
              <p className="ct-dash-exports__status ct-muted">
                Building ZIP… <strong>{saleZipExport.photo_files_written}</strong> /{" "}
                <strong>{saleZipExport.total_photos_expected}</strong> files in archive
              </p>
            ) : null}
            {saleZipExport?.status === "failed" ? (
              <p className="ct-dash-exports__status" style={{ color: "var(--ct-error)" }}>
                {saleZipExport.error_message || "Export failed"}
              </p>
            ) : null}
          </section>
        </div>

      <input
        ref={lotCameraNativeInputRef}
        type="file"
        accept="image/*,.heic,.heif"
        capture="environment"
        style={{ display: "none" }}
        onChange={(e) => onLotCameraNativeInputChange(e.target.files)}
      />

      {lotCameraOpen ? (
        <div
          className="ct-lot-camera-dialog ct-lot-camera-dialog-shell"
          role="dialog"
          aria-modal="true"
          aria-label="Snap photos for one lot"
        >
          <div ref={lotCameraScrollRef} className="ct-lot-camera-scroll">
            <div className="ct-card ct-card--padded ct-lot-camera-card">
            <p className="ct-muted ct-lot-camera-intro" style={{ marginTop: 0, fontSize: "0.9rem" }}>
              Snap as many photos as you need for this listing, then <strong>Done</strong> to return here and
              upload. Tap the preview to focus; pinch or +/− to zoom (see the zoom mode label). For maximum sharpness you
              can use <strong>Phone camera</strong> — same multi-photo session, then <strong>Done</strong>. Tap a
              thumbnail to preview; use the pencil to rotate, trim edges, or tweak brightness / contrast / saturation
              when needed; use the trash control to remove one shot.
            </p>
            <label
              className="ct-muted ct-lot-camera-sku"
              style={{ display: "block", marginTop: "0.55rem", fontSize: "0.85rem" }}
            >
              SKU for this lot <span style={{ fontWeight: 400 }}>(optional)</span>
              <input
                className="ct-input"
                style={{ width: "100%", marginTop: 6, boxSizing: "border-box" }}
                value={singleLotManualSku}
                onChange={(e) => setSingleLotManualSku(e.target.value)}
                placeholder="e.g. 035 or Lot #042"
                autoComplete="off"
                enterKeyHint="done"
              />
            </label>
            {!lotCameraStream ? (
              <p style={{ margin: "0.75rem 0", textAlign: "center" }}>Starting camera…</p>
            ) : (
              <>
                <div
                  ref={lotCameraZoomWrapRef}
                  className="ct-lot-camera-live"
                  role="button"
                  tabIndex={0}
                  aria-label="Camera preview — tap to focus"
                  onPointerDown={onLotCameraPreviewPointerDown}
                  onPointerUp={onLotCameraPreviewPointerUp}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      const wrap = lotCameraZoomWrapRef.current;
                      if (!wrap) return;
                      const rect = wrap.getBoundingClientRect();
                      void applyLotCameraTapFocus(rect.left + rect.width / 2, rect.top + rect.height / 2);
                    }
                  }}
                >
                  <video
                    key={lotCameraVideoMountKey}
                    ref={lotCameraVideoRef}
                    autoPlay
                    playsInline
                    muted
                    onLoadedData={() => {
                      setLotCameraPreviewReady(true);
                      setLotCameraPreviewLayoutTick((t) => t + 1);
                    }}
                    style={{
                      width: "100%",
                      height: "100%",
                      display: "block",
                      objectFit: "cover",
                      transform: (() => {
                        void lotCameraPreviewLayoutTick;
                        return lotCameraPreviewTransform(
                          lotCameraVideoRef.current,
                          lotCameraZoom,
                          lotCameraHardwareZoom
                        );
                      })(),
                      transformOrigin: "center center",
                      pointerEvents: "none"
                    }}
                  />
                  {lotCameraFocusRing ? (
                    <span
                      key={lotCameraFocusRing.key}
                      className="ct-lot-camera-focus-ring"
                      style={{
                        left: `${lotCameraFocusRing.xPct}%`,
                        top: `${lotCameraFocusRing.yPct}%`
                      }}
                      aria-hidden
                    />
                  ) : null}
                  {lotCameraShutterFlash ? (
                    <span className="ct-lot-camera-shutter-flash" aria-hidden />
                  ) : null}
                </div>
                <LotCameraZoomControls
                  variant="inline"
                  uiZoom={lotCameraZoom}
                  hardwareActive={lotCameraHardwareZoom}
                  opticalAvailable={lotCameraOpticalAvailable}
                  onNudge={(d) => void nudgeLotCameraZoom(d)}
                />
              </>
            )}
            <p className="ct-lot-camera-meta" style={{ margin: "0.65rem 0 0", fontSize: "0.95rem" }}>
              <strong>
                {lotCameraSnaps.length}
                {CTBIDS_MAX_PHOTOS_PER_LOT != null ? ` / ${CTBIDS_MAX_PHOTOS_PER_LOT}` : ""}
              </strong>{" "}
              photo{lotCameraSnaps.length === 1 ? "" : "s"} captured
              {CTBIDS_MAX_PHOTOS_PER_LOT != null && lotCameraSnaps.length >= CTBIDS_MAX_PHOTOS_PER_LOT ? (
                <span className="ct-muted" style={{ display: "block", marginTop: 4, fontSize: "0.82rem" }}>
                  CTBids limit reached — tap <strong>Done</strong> to add these to the upload box, or remove shots with
                  the trash icon.
                </span>
              ) : null}
            </p>
            {lotCameraSnapThumbUrls.length > 0 ? (
              <div className="ct-lot-camera-strip-wrap" style={{ marginTop: "0.65rem", width: "100%" }}>
                <p
                  className="ct-muted"
                  style={{ margin: "0 0 6px", fontSize: "0.78rem", color: "rgba(255,255,255,0.75)" }}
                >
                  Captured (scroll sideways) — tap to preview, pencil to edit
                </p>
                <div ref={lotCameraStripRef} className="ct-lot-camera-strip">
                  {lotCameraSnapThumbUrls.map((url, i) => (
                    <div key={url} className="ct-lot-camera-strip-item">
                      <button
                        type="button"
                        className="ct-lot-camera-strip-thumb"
                        onClick={() => setLotCameraViewerIndex(i)}
                        aria-label={`Preview captured photo ${i + 1}`}
                      >
                        <img src={url} alt="" />
                      </button>
                      <div className="ct-lot-camera-strip-item-actions">
                        <button
                          type="button"
                          aria-label={`Edit captured photo ${i + 1}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            openLotCameraEdit(i);
                          }}
                          className="ct-btn ct-btn--ghost ct-lot-camera-strip-edit"
                        >
                          <Pencil size={15} aria-hidden />
                        </button>
                        <button
                          type="button"
                          aria-label={`Delete captured photo ${i + 1}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            openLotCameraDeleteConfirm(i);
                          }}
                          className="ct-btn ct-btn--ghost ct-lot-camera-strip-delete"
                        >
                          <Trash2 size={15} aria-hidden />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            </div>
          </div>
          <div className="ct-lot-camera-actions">
            <div className="ct-lot-camera-actions-inner">
              <button type="button" className="ct-btn ct-btn--ghost" onClick={() => tryCancelLotCameraSession()}>
                Cancel
              </button>
              {lotCameraStream ? (
                <LotCameraZoomControls
                  variant="actions"
                  uiZoom={lotCameraZoom}
                  hardwareActive={lotCameraHardwareZoom}
                  opticalAvailable={lotCameraOpticalAvailable}
                  onNudge={(d) => void nudgeLotCameraZoom(d)}
                />
              ) : null}
              <div className="ct-lot-camera-actions-primary ct-row" style={{ gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="ct-btn"
                  title="Use your phone’s built-in camera app (full quality); returns here after each shot"
                  onClick={() => openPhoneCameraForLot()}
                  disabled={
                    CTBIDS_MAX_PHOTOS_PER_LOT != null &&
                    lotCameraSnaps.length >= CTBIDS_MAX_PHOTOS_PER_LOT
                  }
                >
                  Phone camera
                </button>
                <button
                  type="button"
                  className="ct-btn ct-btn--primary"
                  onClick={() => void snapLotPhoto()}
                  disabled={
                    !lotCameraStream ||
                    !lotCameraPreviewReady ||
                    (CTBIDS_MAX_PHOTOS_PER_LOT != null &&
                      lotCameraSnaps.length >= CTBIDS_MAX_PHOTOS_PER_LOT)
                  }
                >
                  Snap photo
                </button>
                <button type="button" className="ct-btn" onClick={() => void finishLotCameraSession()}>
                  Done
                </button>
              </div>
            </div>
          </div>

          {lotCameraDeleteConfirmIndex !== null ? (
            <div
              className="ct-lot-camera-confirm"
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="lot-camera-delete-title"
              aria-describedby="lot-camera-delete-desc"
              onClick={cancelLotCameraDeleteConfirm}
            >
              <div className="ct-lot-camera-confirm-panel" onClick={(e) => e.stopPropagation()}>
                <p id="lot-camera-delete-title" style={{ margin: "0 0 0.5rem", fontWeight: 600 }}>
                  Delete this photo?
                </p>
                <p id="lot-camera-delete-desc" className="ct-muted" style={{ margin: "0 0 1rem", fontSize: "0.9rem" }}>
                  It is not in the upload box until you tap <strong>Done</strong>.
                </p>
                <div className="ct-row ct-row--gap-lg" style={{ justifyContent: "flex-end", flexWrap: "wrap" }}>
                  <button type="button" className="ct-btn" onClick={cancelLotCameraDeleteConfirm}>
                    Cancel
                  </button>
                  <button type="button" className="ct-btn ct-btn--danger" onClick={confirmLotCameraDeleteSnap}>
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          {lotCameraViewerIndex !== null &&
          lotCameraViewerIndex >= 0 &&
          lotCameraViewerIndex < lotCameraSnapThumbUrls.length ? (
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Photo preview"
              onClick={() => setLotCameraViewerIndex(null)}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 10001,
                background: "rgba(0,0,0,0.94)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "12px",
                paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
                boxSizing: "border-box"
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "0.75rem",
                  width: "100%",
                  maxWidth: "min(100vw - 24px, 720px)"
                }}
              >
                <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="ct-muted" style={{ color: "rgba(255,255,255,0.85)", fontSize: "0.9rem" }}>
                    Photo {lotCameraViewerIndex + 1} of {lotCameraSnapThumbUrls.length}
                  </span>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Close preview"
                    onClick={() => setLotCameraViewerIndex(null)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <X size={22} aria-hidden />
                  </button>
                </div>
                <img
                  src={lotCameraSnapThumbUrls[lotCameraViewerIndex] ?? ""}
                  alt={`Captured photo ${lotCameraViewerIndex + 1}`}
                  style={{
                    maxWidth: "100%",
                    maxHeight: "min(72vh, 640px)",
                    width: "auto",
                    height: "auto",
                    objectFit: "contain",
                    borderRadius: 8,
                    border: "1px solid rgba(255,255,255,0.2)"
                  }}
                />
                <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
                  {lotCameraSnapThumbUrls.length > 1 ? (
                    <>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        aria-label="Previous photo"
                        disabled={lotCameraViewerIndex <= 0}
                        onClick={() => setLotCameraViewerIndex((j) => (j !== null && j > 0 ? j - 1 : j))}
                        style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                      >
                        <ChevronLeft size={24} aria-hidden />
                      </button>
                      <button
                        type="button"
                        className="ct-btn ct-btn--ghost"
                        aria-label="Next photo"
                        disabled={lotCameraViewerIndex >= lotCameraSnapThumbUrls.length - 1}
                        onClick={() =>
                          setLotCameraViewerIndex((j) =>
                            j !== null && j < lotCameraSnapThumbUrls.length - 1 ? j + 1 : j
                          )
                        }
                        style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                      >
                        <ChevronRight size={24} aria-hidden />
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    onClick={() => {
                      const idx = lotCameraViewerIndex;
                      if (idx !== null) openLotCameraEdit(idx);
                    }}
                    style={{ color: "#fff" }}
                  >
                    <Pencil size={16} aria-hidden style={{ verticalAlign: "middle", marginRight: 6 }} />
                    Edit
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--danger"
                    onClick={() => {
                      const idx = lotCameraViewerIndex;
                      if (idx !== null) openLotCameraDeleteConfirm(idx);
                    }}
                  >
                    Delete this photo
                  </button>
                </div>
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  Tap outside the image to close
                </p>
              </div>
            </div>
          ) : null}

          {lotCameraEditIndex !== null &&
          lotCameraEditIndex >= 0 &&
          lotCameraEditIndex < lotCameraSnapThumbUrls.length ? (
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Edit captured photo"
              onClick={() => closeLotCameraEdit()}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 10002,
                background: "rgba(0,0,0,0.94)",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "12px",
                paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
                boxSizing: "border-box"
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "stretch",
                  gap: "0.75rem",
                  width: "100%",
                  maxWidth: "min(100vw - 24px, 520px)"
                }}
              >
                <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
                  <span className="ct-muted" style={{ color: "rgba(255,255,255,0.9)", fontSize: "0.95rem" }}>
                    Edit photo {lotCameraEditIndex + 1}
                  </span>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Close editor"
                    disabled={lotCameraEditBusy}
                    onClick={() => closeLotCameraEdit()}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <X size={22} aria-hidden />
                  </button>
                </div>
                <div
                  style={{
                    overflow: "hidden",
                    maxHeight: "min(50dvh, 480px)",
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 8,
                    border: "1px solid rgba(255,255,255,0.2)",
                    background: "#111"
                  }}
                >
                  <div
                    style={{
                      transform: `rotate(${lotCameraEditRotation}deg)`,
                      transformOrigin: "center center"
                    }}
                  >
                    <img
                      src={lotCameraSnapThumbUrls[lotCameraEditIndex] ?? ""}
                      alt=""
                      style={{
                        maxWidth: "min(92vw, 480px)",
                        maxHeight: "min(46dvh, 420px)",
                        width: "auto",
                        height: "auto",
                        display: "block",
                        objectFit: "contain",
                        clipPath: `inset(${lotCameraEditTrim}%)`,
                        filter: rasterAdjustCssFilter({
                          brightnessPct: lotCameraEditBrightness,
                          contrastPct: lotCameraEditContrast,
                          saturationPct: lotCameraEditSaturation
                        })
                      }}
                    />
                  </div>
                </div>
                <div className="ct-row" style={{ flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Rotate counter-clockwise"
                    disabled={lotCameraEditBusy}
                    onClick={() => setLotCameraEditRotation((r) => (r + 270) % 360)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <RotateCcw size={22} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Rotate clockwise"
                    disabled={lotCameraEditBusy}
                    onClick={() => setLotCameraEditRotation((r) => (r + 90) % 360)}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <RotateCw size={22} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={lotCameraEditBusy}
                    onClick={() => {
                      setLotCameraEditRotation(0);
                      setLotCameraEditTrim(0);
                      setLotCameraEditBrightness(100);
                      setLotCameraEditContrast(100);
                      setLotCameraEditSaturation(100);
                    }}
                    style={{ color: "#fff" }}
                  >
                    Reset
                  </button>
                </div>
                <label className="ct-muted" style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.85)" }}>
                  Trim edges (uniform %)
                  <input
                    type="range"
                    min={0}
                    max={35}
                    step={1}
                    value={lotCameraEditTrim}
                    disabled={lotCameraEditBusy}
                    onChange={(e) => setLotCameraEditTrim(Number(e.target.value))}
                    style={{ width: "100%", marginTop: 8, display: "block" }}
                  />
                  <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>{lotCameraEditTrim}%</span>
                </label>
                <div
                  className="ct-muted"
                  style={{
                    fontSize: "0.85rem",
                    color: "rgba(255,255,255,0.85)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem"
                  }}
                >
                  <label>
                    Brightness (100% = unchanged)
                    <input
                      type="range"
                      min={50}
                      max={150}
                      step={1}
                      value={lotCameraEditBrightness}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditBrightness(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditBrightness}%
                    </span>
                  </label>
                  <label>
                    Contrast
                    <input
                      type="range"
                      min={50}
                      max={150}
                      step={1}
                      value={lotCameraEditContrast}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditContrast(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditContrast}%
                    </span>
                  </label>
                  <label>
                    Saturation (0% = grayscale)
                    <input
                      type="range"
                      min={0}
                      max={150}
                      step={1}
                      value={lotCameraEditSaturation}
                      disabled={lotCameraEditBusy}
                      onChange={(e) => setLotCameraEditSaturation(Number(e.target.value))}
                      style={{ width: "100%", marginTop: 6, display: "block" }}
                    />
                    <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                      {lotCameraEditSaturation}%
                    </span>
                  </label>
                </div>
                <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    disabled={lotCameraEditBusy}
                    onClick={() => closeLotCameraEdit()}
                    style={{ color: "#fff" }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--primary"
                    disabled={lotCameraEditBusy}
                    onClick={() => void applyLotCameraEdit()}
                  >
                    {lotCameraEditBusy ? "Applying…" : "Apply"}
                  </button>
                </div>
                <p className="ct-muted" style={{ margin: 0, fontSize: "0.76rem", color: "rgba(255,255,255,0.6)" }}>
                  Apply saves a new JPEG (trim, rotate, brightness, contrast, color). Tap outside to cancel.
                </p>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {folderPick &&
      stagingViewerIndex !== null &&
      stagingViewerIndex >= 0 &&
      stagingViewerIndex < folderPick.thumbUrls.length ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Staged photo preview"
          onClick={() => !uploading && setStagingViewerIndex(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10003,
            background: "rgba(0,0,0,0.94)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "12px",
            paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
            boxSizing: "border-box"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "0.75rem",
              width: "100%",
              maxWidth: "min(100vw - 24px, 720px)"
            }}
          >
            <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
              <span className="ct-muted" style={{ color: "rgba(255,255,255,0.85)", fontSize: "0.9rem" }}>
                Staged photo {stagingViewerIndex + 1} of {folderPick.thumbUrls.length}
              </span>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Close preview"
                disabled={uploading}
                onClick={() => setStagingViewerIndex(null)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <X size={22} aria-hidden />
              </button>
            </div>
            <img
              src={folderPick.thumbUrls[stagingViewerIndex] ?? ""}
              alt={`Staged photo ${stagingViewerIndex + 1}`}
              style={{
                maxWidth: "100%",
                maxHeight: "min(72vh, 640px)",
                width: "auto",
                height: "auto",
                objectFit: "contain",
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.2)"
              }}
            />
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
              {folderPick.thumbUrls.length > 1 ? (
                <>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Previous staged photo"
                    disabled={uploading || stagingViewerIndex <= 0}
                    onClick={() => setStagingViewerIndex((j) => (j !== null && j > 0 ? j - 1 : j))}
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <ChevronLeft size={24} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="ct-btn ct-btn--ghost"
                    aria-label="Next staged photo"
                    disabled={uploading || stagingViewerIndex >= folderPick.thumbUrls.length - 1}
                    onClick={() =>
                      setStagingViewerIndex((j) =>
                        j !== null && j < folderPick.thumbUrls.length - 1 ? j + 1 : j
                      )
                    }
                    style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
                  >
                    <ChevronRight size={24} aria-hidden />
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={uploading}
                onClick={() => {
                  const idx = stagingViewerIndex;
                  if (idx !== null) openStagingEdit(idx);
                }}
                style={{ color: "#fff" }}
              >
                <Pencil size={16} aria-hidden style={{ verticalAlign: "middle", marginRight: 6 }} />
                Edit
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--danger"
                disabled={uploading}
                onClick={() => {
                  const idx = stagingViewerIndex;
                  if (idx !== null) tryRemoveStagingImage(idx);
                }}
              >
                Delete this photo
              </button>
            </div>
            <p className="ct-muted" style={{ margin: 0, fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
              Tap outside the image to close
            </p>
          </div>
        </div>
      ) : null}

      {folderPick &&
      stagingEditIndex !== null &&
      stagingEditIndex >= 0 &&
      stagingEditIndex < folderPick.thumbUrls.length ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Edit staged photo"
          onClick={() => closeStagingEdit()}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10004,
            background: "rgba(0,0,0,0.94)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "12px",
            paddingBottom: "max(16px, calc(env(safe-area-inset-bottom, 0px) + 64px))",
            boxSizing: "border-box"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "stretch",
              gap: "0.75rem",
              width: "100%",
              maxWidth: "min(100vw - 24px, 520px)"
            }}
          >
            <div className="ct-row" style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
              <span className="ct-muted" style={{ color: "rgba(255,255,255,0.9)", fontSize: "0.95rem" }}>
                Edit staged photo {stagingEditIndex + 1}
              </span>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Close editor"
                disabled={stagingEditBusy}
                onClick={() => closeStagingEdit()}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <X size={22} aria-hidden />
              </button>
            </div>
            <div
              style={{
                overflow: "hidden",
                maxHeight: "min(50dvh, 480px)",
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.2)",
                background: "#111"
              }}
            >
              <div
                style={{
                  transform: `rotate(${stagingEditRotation}deg)`,
                  transformOrigin: "center center"
                }}
              >
                <img
                  src={folderPick.thumbUrls[stagingEditIndex] ?? ""}
                  alt=""
                  style={{
                    maxWidth: "min(92vw, 480px)",
                    maxHeight: "min(46dvh, 420px)",
                    width: "auto",
                    height: "auto",
                    display: "block",
                    objectFit: "contain",
                    clipPath: `inset(${stagingEditTrim}%)`,
                    filter: rasterAdjustCssFilter({
                      brightnessPct: stagingEditBrightness,
                      contrastPct: stagingEditContrast,
                      saturationPct: stagingEditSaturation
                    })
                  }}
                />
              </div>
            </div>
            <div className="ct-row" style={{ flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Rotate counter-clockwise"
                disabled={stagingEditBusy}
                onClick={() => setStagingEditRotation((r) => (r + 270) % 360)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <RotateCcw size={22} aria-hidden />
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                aria-label="Rotate clockwise"
                disabled={stagingEditBusy}
                onClick={() => setStagingEditRotation((r) => (r + 90) % 360)}
                style={{ color: "#fff", minWidth: 44, minHeight: 44 }}
              >
                <RotateCw size={22} aria-hidden />
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={stagingEditBusy}
                onClick={() => {
                  setStagingEditRotation(0);
                  setStagingEditTrim(0);
                  setStagingEditBrightness(100);
                  setStagingEditContrast(100);
                  setStagingEditSaturation(100);
                }}
                style={{ color: "#fff" }}
              >
                Reset
              </button>
            </div>
            <label className="ct-muted" style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.85)" }}>
              Trim edges (uniform %)
              <input
                type="range"
                min={0}
                max={35}
                step={1}
                value={stagingEditTrim}
                disabled={stagingEditBusy}
                onChange={(e) => setStagingEditTrim(Number(e.target.value))}
                style={{ width: "100%", marginTop: 8, display: "block" }}
              />
              <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>{stagingEditTrim}%</span>
            </label>
            <div
              className="ct-muted"
              style={{
                fontSize: "0.85rem",
                color: "rgba(255,255,255,0.85)",
                display: "flex",
                flexDirection: "column",
                gap: "0.5rem"
              }}
            >
              <label>
                Brightness (100% = unchanged)
                <input
                  type="range"
                  min={50}
                  max={150}
                  step={1}
                  value={stagingEditBrightness}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditBrightness(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditBrightness}%
                </span>
              </label>
              <label>
                Contrast
                <input
                  type="range"
                  min={50}
                  max={150}
                  step={1}
                  value={stagingEditContrast}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditContrast(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditContrast}%
                </span>
              </label>
              <label>
                Saturation (0% = grayscale)
                <input
                  type="range"
                  min={0}
                  max={150}
                  step={1}
                  value={stagingEditSaturation}
                  disabled={stagingEditBusy}
                  onChange={(e) => setStagingEditSaturation(Number(e.target.value))}
                  style={{ width: "100%", marginTop: 6, display: "block" }}
                />
                <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.65)" }}>
                  {stagingEditSaturation}%
                </span>
              </label>
            </div>
            <div className="ct-row ct-row--gap-lg" style={{ flexWrap: "wrap", justifyContent: "center" }}>
              <button
                type="button"
                className="ct-btn ct-btn--ghost"
                disabled={stagingEditBusy}
                onClick={() => closeStagingEdit()}
                style={{ color: "#fff" }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="ct-btn ct-btn--primary"
                disabled={stagingEditBusy}
                onClick={() => void applyStagingEdit()}
              >
                {stagingEditBusy ? "Applying…" : "Apply"}
              </button>
            </div>
            <p className="ct-muted" style={{ margin: 0, fontSize: "0.76rem", color: "rgba(255,255,255,0.6)" }}>
              Apply replaces this file in the upload list (including tone). Tap outside to cancel.
            </p>
          </div>
        </div>
      ) : null}

      {showDashboardScrollJumpNav ? (
        <div
          role="navigation"
          aria-label="Jump scroll"
          style={{
            position: "fixed",
            right: "max(12px, env(safe-area-inset-right, 0px))",
            bottom: "max(16px, env(safe-area-inset-bottom, 0px))",
            zIndex: 9990,
            display: "flex",
            flexDirection: "column",
            gap: 6
          }}
        >
          <button
            type="button"
            className="ct-btn"
            title="Top of page"
            aria-label="Scroll to top of page"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            style={{
              minWidth: 48,
              minHeight: 48,
              padding: 0,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 2px 10px rgba(0,0,0,0.18)"
            }}
          >
            <ChevronUp size={22} strokeWidth={2.25} aria-hidden />
          </button>
          <button
            type="button"
            className="ct-btn"
            title="Bottom of page"
            aria-label="Scroll to bottom of page"
            onClick={() =>
              window.scrollTo({
                top: document.documentElement.scrollHeight,
                behavior: "smooth"
              })
            }
            style={{
              minWidth: 48,
              minHeight: 48,
              padding: 0,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 2px 10px rgba(0,0,0,0.18)"
            }}
          >
            <ChevronDown size={22} strokeWidth={2.25} aria-hidden />
          </button>
        </div>
      ) : null}
      </div>
    </AppShell>
  );
}

