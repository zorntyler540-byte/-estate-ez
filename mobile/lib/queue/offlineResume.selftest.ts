/**
 * Mocked network drop mid-upload, end to end through the pure pieces Sync uses: the connection
 * controller pauses, in-flight "native tasks" are cancelled, no new file starts, and the next
 * run sends only the names the server does not have yet (per-file resume, no restart).
 */
import { ConnectionController, PAUSE_GRACE_MS, STABLE_WINDOW_MS, type NetSnapshot } from "../connectivity";
import { runUploadPipeline } from "../uploadPool";
import { PauseSignal, UploadPausedError, isUploadPausedError } from "../uploadPause";
import { namesStillMissing, planResume } from "./resumePlan";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

class FakeClock {
  t = 0;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  now = () => this.t;
  setTimer = (fn: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };
  clearTimer = (h: unknown) => {
    this.timers.delete(h as number);
  };
  async advance(ms: number) {
    const end = this.t + ms;
    for (;;) {
      let next: [number, { at: number; fn: () => void }] | null = null;
      for (const e of this.timers) if (e[1].at <= end && (!next || e[1].at < next[1].at)) next = e;
      if (!next) break;
      this.timers.delete(next[0]);
      this.t = next[1].at;
      next[1].fn();
      await flush();
    }
    this.t = end;
    await flush();
  }
}

const WIFI: NetSnapshot = { isConnected: true, isInternetReachable: true, type: "wifi" };
const OFF: NetSnapshot = { isConnected: false, isInternetReachable: false, type: "none" };

// A forgotten await would otherwise let Node exit 0 with nothing pending.
const watchdog = setTimeout(() => {
  console.error("offlineResume selftest hung");
  process.exit(1);
}, 5_000);

(async () => {
  const clock = new FakeClock();
  const pause = new PauseSignal();
  let syncing = false;
  let starts = 0;
  const controller = new ConnectionController({
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    ping: async () => true,
    onReady: () => {
      starts += 1;
    },
    onDrop: (use) => {
      if (syncing) pause.pause(use === "wifi_required" ? "wifi_only" : "offline");
    }
  });

  /** Server state for one lot's receiving job. */
  const serverHas = new Set<string>();
  const expected = Array.from({ length: 8 }, (_, i) => `mobile-d1-${String(i + 1).padStart(2, "0")}.jpg`);
  let started: string[] = [];
  let cancelled = 0;
  /** Open "native tasks": the test completes them or the pause cancels them. */
  const open = new Map<string, () => void>();

  const nativeUpload = (name: string) =>
    new Promise<void>((resolve, reject) => {
      pause.throwIfPaused();
      started.push(name);
      const off = pause.onPause((reason) => {
        cancelled += 1;
        open.delete(name);
        reject(new UploadPausedError(reason));
      });
      open.set(name, () => {
        off();
        open.delete(name);
        serverHas.add(name);
        resolve();
      });
    });

  const finishOpen = async (n: number) => {
    for (const done of [...open.values()].slice(0, n)) done();
    await flush();
  };

  // Phone comes online; the controller waits for a stable link before the first Sync.
  controller.observe(WIFI);
  await clock.advance(STABLE_WINDOW_MS);
  assert(starts === 1, "first Sync after stable + ping");

  // Sync 1: 3 at a time on Wi-Fi. Two files land, then the connection drops.
  syncing = true;
  pause.clear();
  const run1 = runUploadPipeline({
    items: expected,
    concurrency: 3,
    prepare: async (name) => name,
    upload: async (name) => {
      pause.throwIfPaused();
      await nativeUpload(name);
    }
  });
  await flush();
  assert(started.length === 3, "three uploads in flight on Wi-Fi");
  await finishOpen(2);
  assert(serverHas.size === 2, "two files on the server");
  assert(started.length === 5, "pipeline refilled to three in flight");

  controller.observe(OFF);
  await clock.advance(PAUSE_GRACE_MS - 1);
  assert(!pause.paused, "no pause inside the grace period");
  await clock.advance(1);
  assert(pause.paused, "pause after the grace period");
  let err1: unknown = null;
  try {
    await run1;
  } catch (e) {
    err1 = e;
  }
  assert(isUploadPausedError(err1), "Sync 1 ends as a pause, not a failure");
  assert(cancelled === 3, "every in-flight task was cancelled");
  assert(started.length === 5, "no new file started after the pause");
  assert(open.size === 0, "no task left running");
  syncing = false;
  pause.clear();

  // Flapping while offline does not start a Sync.
  for (let i = 0; i < 4; i++) {
    controller.observe(WIFI);
    await clock.advance(3_000);
    controller.observe(OFF);
    await clock.advance(1_000);
  }
  assert(starts === 1, "flapping never started a second Sync");

  // Stable again → Sync 2 resumes: only names the server does not have.
  controller.observe(WIFI);
  await clock.advance(60_000);
  assert(starts === 2, "second Sync once the link holds");
  const plan = planResume({ expected, received: [...serverHas] });
  assert(plan.action === "resume", "server batch is a clean subset → resume");
  const missing = plan.action === "resume" ? plan.missing : [];
  assert(missing.length === 6, "six files left to send");
  started = [];
  syncing = true;
  const run2 = runUploadPipeline({
    items: missing,
    concurrency: 3,
    prepare: async (name) => name,
    upload: async (name) => {
      pause.throwIfPaused();
      await nativeUpload(name);
    }
  });
  for (let i = 0; i < 20 && serverHas.size < expected.length; i++) {
    await flush();
    await finishOpen(3);
  }
  const result = await run2;
  syncing = false;
  assert(result.uploaded === 6, "Sync 2 sent the six missing files");
  assert(!started.some((n) => n === expected[0] || n === expected[1]), "already-sent files were not sent again");
  assert(namesStillMissing(expected, [...serverHas])?.length === 0, "server has every photo → /run is safe");

  controller.dispose();
  clearTimeout(watchdog);
  console.log("offlineResume selftest ok");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
