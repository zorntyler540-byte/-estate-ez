import { CACHE_MAX_AGE_MS, CACHE_SWEEP_DIRS, planCacheCleanup } from "./cacheCleanup";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const now = 1_800_000_000_000;
const old = now - CACHE_MAX_AGE_MS - 1;
const root = "file:///var/mobile/Containers/Data/Application/X/Library/Caches/";

const doomed = planCacheCleanup({
  files: [
    { uri: `${root}Camera/old.jpg`, modifiedMs: old },
    { uri: `${root}Camera/fresh.jpg`, modifiedMs: now - 60_000 },
    { uri: `${root}ImageManipulator/queued.jpg`, modifiedMs: old },
    { uri: `${root}ct-upload-names/unknown.jpg`, modifiedMs: null },
    { uri: `${root}ImageManipulator/edge.jpg`, modifiedMs: now - CACHE_MAX_AGE_MS }
  ],
  referenced: ["/var/mobile/Containers/Data/Application/X/Library/Caches/ImageManipulator/queued.jpg?v=2"],
  now
});

assert(doomed.includes(`${root}Camera/old.jpg`), "old unreferenced scratch file is removed");
assert(!doomed.includes(`${root}Camera/fresh.jpg`), "a capture from the last few hours is kept");
assert(!doomed.includes(`${root}ImageManipulator/queued.jpg`), "a file a queue row points at is kept (file:// and ?query ignored)");
assert(!doomed.includes(`${root}ct-upload-names/unknown.jpg`), "unknown age is kept");
assert(doomed.includes(`${root}ImageManipulator/edge.jpg`), "exactly max age is old enough");
assert(doomed.length === 2, "nothing else is removed");

assert(
  CACHE_SWEEP_DIRS.every((d) => !/document|lot-captures/i.test(d)),
  "never sweeps Documents, where queued lot photos live"
);
assert(planCacheCleanup({ files: [], referenced: [], now }).length === 0, "empty cache");

console.log("cacheCleanup selftest ok (8 cases)");
