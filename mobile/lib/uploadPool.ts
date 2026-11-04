/**
 * Upload scheduling for Sync. Pure (no React Native imports) so selftests can drive it.
 *
 * Photos: one JPEG is compressed at a time (ImageManipulator is CPU/RAM heavy), always running
 * ahead of the uploads, while up to ``concurrency`` uploads are in flight. Lots: a small pool so
 * two lots upload side by side instead of one long serial line.
 */

/** Photos in flight per lot. Cellular stays serial so one weak link is not split three ways. */
export const UPLOADS_PER_LOT_WIFI = 3;
export const UPLOADS_PER_LOT_CELLULAR = 1;
/** Lots uploading at once. */
export const LOTS_AT_ONCE = 2;

export function uploadConcurrencyFor(netType: string | null | undefined): number {
  const t = String(netType || "").toLowerCase();
  return t === "wifi" || t === "ethernet" ? UPLOADS_PER_LOT_WIFI : UPLOADS_PER_LOT_CELLULAR;
}

function asError(e: unknown): Error {
  return e instanceof Error ? e : new Error(String(e ?? "Upload failed"));
}

export type PipelineResult<T> = { uploaded: number; skipped: T[] };

/**
 * Prepare items one by one and upload them with at most ``concurrency`` in flight. ``prepare``
 * returning ``null`` skips that item. On the first failure no new upload starts; uploads already
 * in flight finish, then the first error is thrown.
 */
export async function runUploadPipeline<T, P>(opts: {
  items: readonly T[];
  concurrency: number;
  prepare: (item: T) => Promise<P | null>;
  upload: (prepared: P, item: T) => Promise<void>;
}): Promise<PipelineResult<T>> {
  const limit = Math.max(1, Math.floor(opts.concurrency) || 1);
  const inflight = new Set<Promise<void>>();
  const skipped: T[] = [];
  let uploaded = 0;
  let failure: Error | null = null;

  for (const item of opts.items) {
    if (failure) break;
    let prepared: P | null;
    try {
      prepared = await opts.prepare(item);
    } catch (e) {
      failure = failure ?? asError(e);
      break;
    }
    if (prepared == null) {
      skipped.push(item);
      continue;
    }
    while (inflight.size >= limit && !failure) {
      await Promise.race(inflight);
    }
    if (failure) break;
    const ready = prepared;
    const task: Promise<void> = opts
      .upload(ready, item)
      .then(
        () => {
          uploaded += 1;
        },
        (e) => {
          failure = failure ?? asError(e);
        }
      )
      .finally(() => {
        inflight.delete(task);
      });
    inflight.add(task);
  }
  await Promise.all(inflight);
  if (failure) throw failure;
  return { uploaded, skipped };
}

/** Run ``fn`` over ``items`` with at most ``concurrency`` at once. Every item runs; the first error is rethrown at the end. */
export async function mapPool<T, R>(
  items: readonly T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const limit = Math.max(1, Math.floor(concurrency) || 1);
  const results = new Array<R>(items.length);
  let next = 0;
  let failure: Error | null = null;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        results[i] = await fn(items[i]!, i);
      } catch (e) {
        failure = failure ?? asError(e);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  if (failure) throw failure;
  return results;
}
