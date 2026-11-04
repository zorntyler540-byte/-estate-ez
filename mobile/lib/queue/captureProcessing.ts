/**
 * Off-shutter processing for a captured still. The camera JPEG is first moved to
 * ``<lot dir>/raw/<ts>.jpg`` and saved in SQLite, so the shot is durable before the next
 * Snap. Later (serially) it is re-encoded to bake EXIF orientation / crop the aspect into
 * ``<lot dir>/<ts>.jpg``. The raw file is only deleted after SQLite points at the finished
 * file, so a kill at any step leaves a usable photo.
 */

export const RAW_CAPTURE_DIR = "raw/";

export function rawCaptureUri(lotDir: string, ts: number, ext = ".jpg"): string {
  const dir = lotDir.endsWith("/") ? lotDir : `${lotDir}/`;
  return `${dir}${RAW_CAPTURE_DIR}${ts}${ext}`;
}

/** ``…/raw/<ts>.jpg`` → ``…/<ts>.jpg`` (same timestamp name, which upload reads). */
export function processedUriForRaw(rawUri: string): string | null {
  const i = rawUri.lastIndexOf(`/${RAW_CAPTURE_DIR}`);
  if (i < 0) return null;
  return rawUri.slice(0, i + 1) + rawUri.slice(i + 1 + RAW_CAPTURE_DIR.length);
}

/** SQLite ``lot_photos.process_ratio``: ``"full"`` or a width/height number. */
export function encodeProcessRatio(ratio: number | null): string {
  return ratio == null || !(ratio > 0) ? "full" : String(ratio);
}

/** ``undefined`` = nothing pending; ``null`` = keep full frame; number = crop ratio. */
export function decodeProcessRatio(raw: string | null | undefined): number | null | undefined {
  if (raw == null || raw === "") return undefined;
  if (raw === "full") return null;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export type CaptureFileOps = {
  /** Re-encode / crop; returns a new temp file (or the same uri when nothing was done). */
  bake: (uri: string, ratio: number | null, size?: { width?: number; height?: number }) => Promise<string>;
  copy: (from: string, to: string) => Promise<void>;
  remove: (uri: string) => Promise<void>;
};

export type CaptureJob = {
  rawUri: string;
  ratio: number | null;
  size?: { width?: number; height?: number };
  /** False once the photo was deleted or already processed (skip the work). */
  isPending: () => Promise<boolean>;
  /**
   * Point the photo at ``newUri`` (or keep the raw file when ``null``) and clear pending.
   * Returns false if the photo no longer exists.
   */
  commit: (newUri: string | null) => Promise<boolean>;
};

export type CaptureResult =
  | { status: "processed"; uri: string }
  | { status: "kept-raw"; uri: string }
  | { status: "gone" };

async function quietly(fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
  } catch {
    /* best effort */
  }
}

export async function processCapture(job: CaptureJob, ops: CaptureFileOps): Promise<CaptureResult> {
  if (!(await job.isPending())) return { status: "gone" };
  const dest = processedUriForRaw(job.rawUri);

  let baked = job.rawUri;
  try {
    baked = await ops.bake(job.rawUri, job.ratio, job.size);
  } catch {
    baked = job.rawUri;
  }

  // Re-encode failed (or produced nothing new): the raw JPEG is still a valid photo.
  if (!dest || !baked || baked === job.rawUri) {
    return (await job.commit(null)) ? { status: "kept-raw", uri: job.rawUri } : { status: "gone" };
  }

  try {
    await ops.copy(baked, dest);
  } catch {
    await quietly(() => ops.remove(baked));
    await quietly(() => ops.remove(dest));
    return (await job.commit(null)) ? { status: "kept-raw", uri: job.rawUri } : { status: "gone" };
  }
  await quietly(() => ops.remove(baked));

  if (!(await job.commit(dest))) {
    // Deleted from the strip while we worked — drop the copy we just made.
    await quietly(() => ops.remove(dest));
    return { status: "gone" };
  }
  await quietly(() => ops.remove(job.rawUri));
  return { status: "processed", uri: dest };
}
