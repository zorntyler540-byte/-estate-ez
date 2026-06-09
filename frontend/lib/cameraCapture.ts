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
