import { planCacheCleanup } from "../cacheCleanup";
import { SerialQueue } from "../serialQueue";
import {
  decodeProcessRatio,
  encodeProcessRatio,
  processCapture,
  processedUriForRaw,
  rawCaptureUri,
  type CaptureFileOps
} from "./captureProcessing";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

let cases = 0;
function check(cond: unknown, msg: string) {
  assert(cond, msg);
  cases += 1;
}

/** In-memory disk + one SQLite row, so we can check nothing is lost at any step. */
function harness(opts: { bakeFails?: boolean; bakeNoop?: boolean; copyFails?: boolean } = {}) {
  const files = new Set<string>();
  const row = { exists: true, uri: "", pending: true };
  const ops: CaptureFileOps = {
    async bake(uri) {
      if (opts.bakeFails) throw new Error("out of memory");
      if (opts.bakeNoop) return uri;
      const tmp = `cache/ImageManipulator/${Math.random()}.jpg`;
      files.add(tmp);
      return tmp;
    },
    async copy(from, to) {
      if (opts.copyFails) throw new Error("disk full");
      assert(files.has(from), `copy source exists: ${from}`);
      files.add(to);
    },
    async remove(uri) {
      files.delete(uri);
    }
  };
  return { files, row, ops };
}

const lotDir = "docs/lot-captures/d1/";
const raw = rawCaptureUri(lotDir, 1_730_000_000_000);
check(raw === "docs/lot-captures/d1/raw/1730000000000.jpg", "raw file sits in raw/ with the timestamp name");
check(processedUriForRaw(raw) === "docs/lot-captures/d1/1730000000000.jpg", "processed keeps the timestamp name");
check(processedUriForRaw("docs/lot-captures/d1/1.jpg") === null, "already-final file has no raw form");
check(encodeProcessRatio(null) === "full" && decodeProcessRatio("full") === null, "full frame round-trips");
check(decodeProcessRatio(encodeProcessRatio(4 / 3)) === 4 / 3, "crop ratio round-trips");
check(decodeProcessRatio(null) === undefined, "null = nothing pending");

async function run() {
  // Happy path: DB swaps to the processed file, then raw + temp are removed.
  {
    const h = harness();
    h.files.add(raw);
    h.row.uri = raw;
    const result = await processCapture(
      {
        rawUri: raw,
        ratio: null,
        isPending: async () => h.row.exists && h.row.pending,
        commit: async (next) => {
          if (!h.row.exists || h.row.uri !== raw) return false;
          h.row.uri = next ?? raw;
          h.row.pending = false;
          return true;
        }
      },
      h.ops
    );
    check(result.status === "processed", "processed");
    check(h.row.uri === processedUriForRaw(raw) && h.files.has(h.row.uri), "row points at an existing file");
    check(!h.files.has(raw), "raw removed after the swap");
    check([...h.files].every((f) => !f.startsWith("cache/")), "manipulator temp cleaned up");
  }

  // Re-encode fails (e.g. memory): keep the raw photo, clear pending, nothing deleted.
  for (const mode of [{ bakeFails: true }, { bakeNoop: true }, { copyFails: true }]) {
    const h = harness(mode);
    h.files.add(raw);
    h.row.uri = raw;
    const result = await processCapture(
      {
        rawUri: raw,
        ratio: 1,
        isPending: async () => true,
        commit: async (next) => {
          h.row.uri = next ?? raw;
          h.row.pending = false;
          return true;
        }
      },
      h.ops
    );
    const label = Object.keys(mode)[0];
    check(result.status === "kept-raw", `${label}: keeps the raw photo`);
    check(h.row.uri === raw && h.files.has(raw) && !h.row.pending, `${label}: raw file still on disk and in the row`);
    check([...h.files].every((f) => !f.startsWith("cache/")), `${label}: no temp files left`);
  }

  // Deleted from the strip while processing: drop our copy, never resurrect the photo.
  {
    const h = harness();
    h.files.add(raw);
    let deleted = false;
    const result = await processCapture(
      {
        rawUri: raw,
        ratio: null,
        isPending: async () => true,
        commit: async () => {
          deleted = true;
          h.files.delete(raw);
          return false;
        }
      },
      h.ops
    );
    check(deleted && result.status === "gone", "deleted mid-process reports gone");
    check(h.files.size === 0, "no orphan processed file after delete");
  }

  // Already handled (deleted earlier, or processed by Sync): skip the work.
  {
    const h = harness();
    let baked = false;
    h.ops.bake = async (uri) => {
      baked = true;
      return uri;
    };
    const result = await processCapture(
      { rawUri: raw, ratio: null, isPending: async () => false, commit: async () => true },
      h.ops
    );
    check(result.status === "gone" && !baked, "not pending = no re-encode");
  }

  // Serial queue: one at a time, in order, and a failure does not stall the rest.
  {
    const q = new SerialQueue();
    const log: string[] = [];
    let active = 0;
    let maxActive = 0;
    const job = (name: string, fail = false) => async () => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((r) => setTimeout(r, 5));
      log.push(name);
      active -= 1;
      if (fail) throw new Error(name);
      return name;
    };
    const a = q.enqueue(job("a"));
    const b = q.enqueue(job("b", true)).catch(() => "b-failed");
    const c = q.enqueue(job("c"));
    check(q.size === 3, "queue counts pending jobs");
    await q.idle();
    check(log.join(",") === "a,b,c", "jobs run in order");
    check(maxActive === 1, "never two re-encodes at once");
    check((await a) === "a" && (await b) === "b-failed" && (await c) === "c", "failure does not block later jobs");
    check(q.size === 0, "queue drains");
  }

  // Cache sweep: only old, unreferenced scratch files.
  {
    const now = 100 * 3_600_000;
    const doomed = planCacheCleanup({
      now,
      referenced: ["file:///cache/Camera/in-queue.jpg"],
      files: [
        { uri: "file:///cache/Camera/old.jpg", modifiedMs: now - 7 * 3_600_000 },
        { uri: "file:///cache/Camera/new.jpg", modifiedMs: now - 60_000 },
        { uri: "/cache/Camera/in-queue.jpg", modifiedMs: now - 48 * 3_600_000 },
        { uri: "file:///cache/ImageManipulator/unknown.jpg", modifiedMs: null }
      ]
    });
    check(doomed.length === 1 && doomed[0] === "file:///cache/Camera/old.jpg", "sweeps only old unreferenced files");
  }

  console.log(`captureProcessing selftest ok (${cases} cases)`);
}

void run().catch((e) => {
  console.error(e);
  process.exit(1);
});
