const CATALOG_IMAGE_RE = /\.(jpe?g|png|webp|heic|heif)$/i;

export function isCatalogImageFile(f: File): boolean {
  if (f.type.startsWith("image/")) return true;
  return CATALOG_IMAGE_RE.test(f.name);
}

/**
 * iOS Safari can hand back camera-capture File handles that become unreadable after reopening
 * the capture sheet. Clone bytes immediately so staged thumbnails/uploads stay valid.
 */
export async function stabilizeCapturedFiles(files: File[]): Promise<File[]> {
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

export async function captureVideoFrameAsJpegFile(
  video: HTMLVideoElement,
  namePrefix = "capture"
): Promise<File | null> {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (w < 2 || h < 2) return null;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, w, h);
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92);
  });
  if (!blob) return null;
  return new File([blob], `${namePrefix}-${Date.now()}.jpg`, {
    type: "image/jpeg",
    lastModified: Date.now()
  });
}

export const APPRAISER_CAMERA_MEDIA: MediaStreamConstraints = {
  video: {
    facingMode: { ideal: "environment" },
    width: { ideal: 1920 },
    height: { ideal: 1080 }
  },
  audio: false
};

export function isCoarsePointerDevice(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(pointer: coarse)").matches
  );
}

export function isMobileLikeDevice(): boolean {
  if (typeof navigator === "undefined") return false;
  return /iPhone|iPad|iPod|Android/i.test(navigator.userAgent || "");
}

/**
 * ``ImageCapture.takePhoto()`` often leaves the live ``<video>`` preview black on iOS / Android
 * even though the track is still live — prefer canvas capture on those devices.
 */
export function shouldUseImageCaptureForStill(): boolean {
  if (isCoarsePointerDevice() || isMobileLikeDevice()) return false;
  return typeof window !== "undefined" && "ImageCapture" in window;
}

/** Re-bind the same stream to recover a frozen black preview without restarting getUserMedia. */
export async function refreshVideoElementPreview(
  video: HTMLVideoElement,
  stream: MediaStream
): Promise<boolean> {
  video.pause();
  video.srcObject = null;
  await new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  );
  video.srcObject = stream;
  video.muted = true;
  try {
    await video.play();
    return video.readyState >= 2 || video.videoWidth > 0;
  } catch {
    return false;
  }
}

export type SaveToPhotosResult = {
  attempted: number;
  shared: number;
  unsupported: boolean;
};

/** True when the browser can hand files to the OS share sheet (Save Image → Photos on iOS). */
export function canSaveFilesToPhotos(): boolean {
  if (typeof navigator === "undefined" || typeof navigator.share !== "function") return false;
  if (typeof navigator.canShare !== "function") return false;
  try {
    const probe = new File(["x"], "probe.jpg", { type: "image/jpeg" });
    return navigator.canShare({ files: [probe] });
  } catch {
    return false;
  }
}

/**
 * Best-effort backup after camera Done — opens the native share sheet so the user can Save Image(s).
 * Browsers cannot write to the camera roll silently; AbortError (dismissed sheet) is ignored.
 */
export async function saveCaptureFilesToPhotos(files: File[]): Promise<SaveToPhotosResult> {
  const images = files.filter((f) => f.size > 0 && isCatalogImageFile(f));
  if (!images.length) {
    return { attempted: 0, shared: 0, unsupported: !canSaveFilesToPhotos() };
  }
  if (!canSaveFilesToPhotos()) {
    return { attempted: images.length, shared: 0, unsupported: true };
  }

  const shareBatch = async (batch: File[]): Promise<"shared" | "aborted" | "failed"> => {
    if (!navigator.canShare?.({ files: batch })) return "failed";
    try {
      await navigator.share({
        files: batch,
        title: batch.length === 1 ? batch[0].name : `${batch.length} photos`
      });
      return "shared";
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return "aborted";
      return "failed";
    }
  };

  const batchResult = await shareBatch(images);
  if (batchResult === "shared") {
    return { attempted: images.length, shared: images.length, unsupported: false };
  }
  if (batchResult === "aborted") {
    return { attempted: images.length, shared: 0, unsupported: false };
  }

  let shared = 0;
  for (const file of images) {
    const one = await shareBatch([file]);
    if (one === "shared") {
      shared += 1;
      continue;
    }
    break;
  }
  return { attempted: images.length, shared, unsupported: false };
}
