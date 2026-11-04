/**
 * Which temp files are safe to delete. Only cache folders the app writes scratch JPEGs to
 * (camera output, manipulator output, upload renames) are swept — never Documents, where
 * queued lot photos live — and only files older than ``maxAgeMs`` that no queue row uses.
 */
export const CACHE_SWEEP_DIRS = ["Camera/", "ImageManipulator/", "ct-upload-names/"] as const;
export const CACHE_MAX_AGE_MS = 6 * 60 * 60_000;

export type CacheFile = { uri: string; modifiedMs: number | null };

function normalize(uri: string): string {
  return String(uri || "").split("?")[0]!.replace(/^file:\/\//, "");
}

export function planCacheCleanup(opts: {
  files: CacheFile[];
  referenced: Iterable<string>;
  now: number;
  maxAgeMs?: number;
}): string[] {
  const maxAge = opts.maxAgeMs ?? CACHE_MAX_AGE_MS;
  const keep = new Set([...opts.referenced].map(normalize));
  return opts.files
    .filter((f) => {
      if (!f.uri || keep.has(normalize(f.uri))) return false;
      // Unknown age: keep it (could be a capture still being written).
      if (f.modifiedMs == null || !Number.isFinite(f.modifiedMs)) return false;
      return opts.now - f.modifiedMs >= maxAge;
    })
    .map((f) => f.uri);
}
