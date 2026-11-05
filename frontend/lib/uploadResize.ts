/**
 * Shrink big JPEGs in the browser before a website bulk upload.
 *
 * The server keeps at most ``CATALOG_UPLOAD_MAX_EDGE`` (4096) and Gemini sees 2048px, so a
 * 12–48 MP phone original mostly costs upload time on site Wi‑Fi. 2560px stays sharp for
 * listings. The original EXIF block is carried over (with Orientation reset to 1 because the
 * canvas already applied it) so the server still reads DateTimeOriginal as the capture time.
 * Only JPEGs are touched: PNG/WebP and small files are sent as-is.
 */

export const UPLOAD_RESIZE_MAX_EDGE = 2560;
export const UPLOAD_RESIZE_JPEG_QUALITY = 0.9;
/** Below this a JPEG is already cheap to send; skip the decode/re-encode. */
export const UPLOAD_RESIZE_MIN_BYTES = 1_200_000;

const EXIF_HEADER = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00]; // "Exif\0\0"

export function isJpegBytes(bytes: Uint8Array): boolean {
  return bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
}

/** Whole APP1 Exif segment (marker through payload) or null. Stops at SOS. */
export function extractExifSegment(jpeg: Uint8Array): Uint8Array | null {
  if (!isJpegBytes(jpeg)) return null;
  let i = 2;
  while (i + 4 <= jpeg.length) {
    if (jpeg[i] !== 0xff) return null;
    const marker = jpeg[i + 1]!;
    if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
      i += 2;
      continue;
    }
    if (marker === 0xda || marker === 0xd9) return null;
    const len = (jpeg[i + 2]! << 8) | jpeg[i + 3]!;
    if (len < 2 || i + 2 + len > jpeg.length) return null;
    if (marker === 0xe1 && len >= 8 && EXIF_HEADER.every((b, k) => jpeg[i + 4 + k] === b)) {
      return jpeg.slice(i, i + 2 + len);
    }
    i += 2 + len;
  }
  return null;
}

/** Set IFD0 Orientation (0x0112) to 1 in an APP1 Exif segment copy. */
export function withOrientationReset(segment: Uint8Array): Uint8Array {
  const out = segment.slice();
  const tiff = 10; // FF E1 len(2) "Exif\0\0"
  if (out.length < tiff + 8) return out;
  const little = out[tiff] === 0x49 && out[tiff + 1] === 0x49;
  const big = out[tiff] === 0x4d && out[tiff + 1] === 0x4d;
  if (!little && !big) return out;
  const u16 = (o: number) => (little ? out[o]! | (out[o + 1]! << 8) : (out[o]! << 8) | out[o + 1]!);
  const u32 = (o: number) =>
    little
      ? (out[o]! | (out[o + 1]! << 8) | (out[o + 2]! << 16) | (out[o + 3]! << 24)) >>> 0
      : ((out[o]! << 24) | (out[o + 1]! << 16) | (out[o + 2]! << 8) | out[o + 3]!) >>> 0;
  const ifd0 = tiff + u32(tiff + 4);
  if (ifd0 + 2 > out.length) return out;
  const count = u16(ifd0);
  for (let n = 0; n < count; n++) {
    const entry = ifd0 + 2 + n * 12;
    if (entry + 12 > out.length) break;
    if (u16(entry) === 0x0112) {
      const value = entry + 8;
      if (little) {
        out[value] = 1;
        out[value + 1] = 0;
      } else {
        out[value] = 0;
        out[value + 1] = 1;
      }
      break;
    }
  }
  return out;
}

/** Insert an APP1 Exif segment right after SOI, dropping any Exif the encoder wrote. */
export function insertExifSegment(
  jpeg: Uint8Array<ArrayBuffer>,
  segment: Uint8Array
): Uint8Array<ArrayBuffer> {
  if (!isJpegBytes(jpeg)) return jpeg;
  const existing = extractExifSegment(jpeg);
  let body = jpeg.subarray(2);
  if (existing) {
    const at = indexOfSegment(jpeg, existing);
    if (at > 0) {
      const merged = new Uint8Array(jpeg.length - existing.length);
      merged.set(jpeg.subarray(0, at), 0);
      merged.set(jpeg.subarray(at + existing.length), at);
      body = merged.subarray(2);
    }
  }
  const out = new Uint8Array(2 + segment.length + body.length);
  out[0] = 0xff;
  out[1] = 0xd8;
  out.set(segment, 2);
  out.set(body, 2 + segment.length);
  return out;
}

function indexOfSegment(haystack: Uint8Array, needle: Uint8Array): number {
  outer: for (let i = 2; i + needle.length <= haystack.length; i++) {
    for (let k = 0; k < 6; k++) if (haystack[i + k] !== needle[k]) continue outer;
    return i;
  }
  return -1;
}

export function scaledSize(width: number, height: number, maxEdge: number): { width: number; height: number } {
  const long = Math.max(width, height);
  if (long <= maxEdge || long <= 0) return { width, height };
  const scale = maxEdge / long;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

function looksJpeg(file: File): boolean {
  return /^image\/jpe?g$/i.test(file.type) || /\.jpe?g$/i.test(file.name);
}

/**
 * Resized copy of ``file`` (same name / type / lastModified), or ``file`` itself when it is
 * not a big JPEG, cannot be decoded, or the re-encode would not be smaller.
 */
export async function resizeJpegForUpload(file: File): Promise<File> {
  if (typeof document === "undefined" || typeof createImageBitmap !== "function") return file;
  if (!looksJpeg(file) || file.size < UPLOAD_RESIZE_MIN_BYTES) return file;
  let bmp: ImageBitmap;
  try {
    bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return file;
  }
  try {
    const target = scaledSize(bmp.width, bmp.height, UPLOAD_RESIZE_MAX_EDGE);
    if (target.width === bmp.width && target.height === bmp.height && file.size < 3 * UPLOAD_RESIZE_MIN_BYTES) {
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = target.width;
    canvas.height = target.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bmp, 0, 0, target.width, target.height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/jpeg", UPLOAD_RESIZE_JPEG_QUALITY)
    );
    canvas.width = 0;
    canvas.height = 0;
    if (!blob || blob.size < 1) return file;
    let bytes: Uint8Array<ArrayBuffer> = new Uint8Array(await blob.arrayBuffer());
    const head = new Uint8Array(await file.slice(0, 256 * 1024).arrayBuffer());
    const exif = extractExifSegment(head);
    if (exif) bytes = insertExifSegment(bytes, withOrientationReset(exif));
    if (bytes.byteLength >= file.size) return file;
    return new File([bytes], file.name, { type: "image/jpeg", lastModified: file.lastModified });
  } catch {
    return file;
  } finally {
    try {
      bmp.close();
    } catch {
      /* ignore */
    }
  }
}

/** Resize with at most ``concurrency`` decodes in flight (each full-res bitmap is ~50–200 MB). */
export async function resizeJpegsForUpload(
  files: readonly File[],
  opts: { concurrency?: number; onProgress?: (done: number, total: number) => void } = {}
): Promise<File[]> {
  const out = new Array<File>(files.length);
  const limit = Math.max(1, opts.concurrency ?? 2);
  let next = 0;
  let done = 0;
  const workers = Array.from({ length: Math.min(limit, files.length) }, async () => {
    while (next < files.length) {
      const i = next++;
      out[i] = await resizeJpegForUpload(files[i]!);
      done += 1;
      opts.onProgress?.(done, files.length);
    }
  });
  await Promise.all(workers);
  return out;
}
