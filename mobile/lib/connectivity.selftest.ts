import {
  ConnectionController,
  FLAP_LOOKBACK_MS,
  MAX_STABLE_WINDOW_MS,
  PAUSE_GRACE_MS,
  PING_RETRY_BASE_MS,
  STABLE_WINDOW_MS,
  VERIFIED_FRESH_MS,
  backgroundNetDecision,
  classifyNet,
  connectionStatusLine,
  pingRetryDelayMs,
  stableWindowMs,
  type ConnectionPhase,
  type NetSnapshot
} from "./connectivity";

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

/** Fake clock: timers fire in time order when ``advance`` passes them. */
class FakeClock {
  t = 1_000_000;
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
  get pending(): number {
    return this.timers.size;
  }
  async advance(ms: number) {
    const end = this.t + ms;
    for (;;) {
      let next: [number, { at: number; fn: () => void }] | null = null;
      for (const entry of this.timers) {
        if (entry[1].at <= end && (!next || entry[1].at < next[1].at)) next = entry;
      }
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

async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

const WIFI: NetSnapshot = { isConnected: true, isInternetReachable: true, type: "wifi" };
const WIFI_UNKNOWN: NetSnapshot = { isConnected: true, isInternetReachable: null, type: "wifi" };
const CELL: NetSnapshot = { isConnected: true, isInternetReachable: true, type: "cellular" };
const OFF: NetSnapshot = { isConnected: false, isInternetReachable: false, type: "none" };
const CAPTIVE: NetSnapshot = { isConnected: true, isInternetReachable: false, type: "wifi" };

function harness(opts: { pingResults?: boolean[]; pingGate?: () => Promise<void> } = {}) {
  const clock = new FakeClock();
  const results = [...(opts.pingResults ?? [])];
  const log = {
    pings: 0,
    ready: 0,
    drops: [] as string[],
    phases: [] as ConnectionPhase[]
  };
  const c = new ConnectionController({
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    ping: async () => {
      log.pings += 1;
      if (opts.pingGate) await opts.pingGate();
      return results.length ? results.shift()! : true;
    },
    onReady: () => {
      log.ready += 1;
    },
    onDrop: (use) => {
      log.drops.push(use);
    },
    onPhase: (p) => {
      log.phases.push(p);
    }
  });
  return { clock, c, log };
}

(async () => {
  // classifyNet with mocked NetInfo states.
  assert(classifyNet(WIFI, false) === "usable", "wifi usable");
  assert(classifyNet(CELL, false) === "usable", "cellular usable by default");
  assert(classifyNet(CELL, true) === "wifi_required", "cellular waits when Wi-Fi only");
  assert(classifyNet({ ...WIFI, type: "ethernet" }, true) === "usable", "ethernet counts as Wi-Fi");
  assert(classifyNet(OFF, false) === "offline", "disconnected");
  assert(classifyNet(CAPTIVE, false) === "offline", "isInternetReachable=false is offline");
  assert(classifyNet(WIFI_UNKNOWN, false) === "usable", "reachability not measured yet: ping decides");
  assert(classifyNet({ isConnected: null, isInternetReachable: null }, false) === "offline", "unknown at boot");
  assert(classifyNet(null, false) === "offline", "no snapshot");

  // Stability window grows with recent drops, capped.
  {
    const now = 10_000_000;
    assert(stableWindowMs([], now) === STABLE_WINDOW_MS, "no drops → base");
    assert(stableWindowMs([now - 1000], now) === STABLE_WINDOW_MS, "one drop → base");
    assert(stableWindowMs([now - 2000, now - 1000], now) === STABLE_WINDOW_MS * 2, "two drops double");
    assert(stableWindowMs([1, 2, 3, 4, 5, 6].map((n) => now - n * 1000), now) === MAX_STABLE_WINDOW_MS, "cap");
    assert(
      stableWindowMs([now - FLAP_LOOKBACK_MS - 1, now - FLAP_LOOKBACK_MS - 2], now) === STABLE_WINDOW_MS,
      "old drops are forgotten"
    );
    assert(pingRetryDelayMs(1) === PING_RETRY_BASE_MS, "ping retry base");
    assert(pingRetryDelayMs(2) === PING_RETRY_BASE_MS * 2, "ping retry doubles");
    assert(pingRetryDelayMs(20) === 2 * 60_000, "ping retry cap");
  }

  // Boot offline: nothing pings, nothing starts.
  {
    const { clock, c, log } = harness();
    c.observe(OFF);
    await clock.advance(5 * 60_000);
    assert(c.phase === "offline", "offline phase");
    assert(log.pings === 0 && log.ready === 0, "no ping / no start offline");
  }

  // Back online: waits the full stability window, pings once, then starts once.
  {
    const { clock, c, log } = harness();
    c.observe(OFF);
    c.observe(WIFI);
    assert(c.phase === "waiting_stable", "waiting for stable connection");
    assert(c.retryAt === clock.t + STABLE_WINDOW_MS, "retryAt shows the wait");
    await clock.advance(STABLE_WINDOW_MS - 1);
    assert(log.pings === 0 && log.ready === 0, "no start before the window ends");
    // Duplicate NetInfo events (SSID / strength updates) do not restart the wait.
    c.observe({ ...WIFI });
    await clock.advance(1);
    assert(log.pings === 1, "health ping after the window");
    assert(c.phase === "ready", "ready after a good ping");
    assert(log.ready === 1, "exactly one start");
    await clock.advance(10 * 60_000);
    assert(log.ready === 1 && log.pings === 1, "no repeated starts while steady");
  }

  // Flapping network: no start/stop thrash, and the next wait is longer.
  {
    const { clock, c, log } = harness();
    c.observe(OFF);
    for (let i = 0; i < 6; i++) {
      c.observe(WIFI);
      await clock.advance(5_000);
      c.observe(OFF);
      await clock.advance(2_000);
    }
    assert(log.pings === 0, "flapping never reaches the ping");
    assert(log.ready === 0, "flapping never starts uploads");
    c.observe(WIFI);
    const wait = (c.retryAt ?? 0) - clock.t;
    assert(wait > STABLE_WINDOW_MS, `flapping lengthens the wait (got ${wait}ms)`);
    assert(wait <= MAX_STABLE_WINDOW_MS, "wait is capped");
    await clock.advance(wait);
    assert(log.ready === 1, "starts once the link holds");
  }

  // Captive portal / no internet behind Wi-Fi counts as offline.
  {
    const { clock, c, log } = harness();
    c.observe(CAPTIVE);
    await clock.advance(60_000);
    assert(c.phase === "offline" && log.pings === 0, "captive portal does not start uploads");
  }

  // Health ping fails: back off, then start when the website answers.
  {
    const { clock, c, log } = harness({ pingResults: [false, false, true] });
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "unreachable", "website not answering");
    assert(log.ready === 0, "no start on a failed ping");
    await clock.advance(PING_RETRY_BASE_MS);
    assert(log.pings === 2 && c.phase === "unreachable", "second ping after base delay");
    await clock.advance(PING_RETRY_BASE_MS * 2 - 1);
    assert(log.pings === 2, "third ping waits the doubled delay");
    await clock.advance(1);
    assert(log.pings === 3 && c.phase === "ready" && log.ready === 1, "starts when the ping answers");
  }

  // A throwing ping is a failed ping.
  {
    const clock = new FakeClock();
    let ready = 0;
    const c = new ConnectionController({
      now: clock.now,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
      ping: async () => {
        throw new Error("network");
      },
      onReady: () => {
        ready += 1;
      },
      onDrop: () => undefined
    });
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "unreachable" && ready === 0, "ping error → unreachable");
    c.dispose();
    assert(clock.pending === 0, "dispose clears timers");
  }

  // Network changes while the ping is in flight: that answer is ignored.
  {
    let release!: () => void;
    const { clock, c, log } = harness({
      pingGate: () => new Promise<void>((r) => (release = r))
    });
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "checking", "ping in flight");
    c.observe(OFF);
    release();
    await flush();
    assert(log.ready === 0, "stale ping does not start uploads");
    assert(c.phase === "offline", "still offline");
  }

  // Drop mid-upload: pause only after the grace period; a short blip does not pause.
  {
    const { clock, c, log } = harness();
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    c.observe(OFF);
    await clock.advance(PAUSE_GRACE_MS - 1);
    c.observe(WIFI);
    await clock.advance(PAUSE_GRACE_MS * 2);
    assert(log.drops.length === 0, "blip shorter than the grace does not pause");
    assert(c.phase === "waiting_stable", "blip still re-verifies before the next start");

    await clock.advance(STABLE_WINDOW_MS * 2);
    c.observe(OFF);
    await clock.advance(PAUSE_GRACE_MS);
    assert(log.drops.join() === "offline", "real drop pauses once");
    await clock.advance(60_000);
    assert(log.drops.length === 1, "no repeated pause while offline");
  }

  // Upload on Wi-Fi only.
  {
    const { clock, c, log } = harness();
    c.setWifiOnly(true);
    c.observe(CELL);
    await clock.advance(5 * 60_000);
    assert(c.phase === "wifi_required" && log.pings === 0 && log.ready === 0, "cellular waits for Wi-Fi");
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "ready" && log.ready === 1, "Wi-Fi starts uploads");
    c.observe(CELL);
    await clock.advance(PAUSE_GRACE_MS);
    assert(log.drops.join() === "wifi_required", "leaving Wi-Fi pauses uploads (videos too)");
  }

  // Turning Wi-Fi only on while uploading on cellular pauses; turning it off resumes after the wait.
  {
    const { clock, c, log } = harness();
    c.observe(CELL);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "ready", "cellular ready by default");
    c.setWifiOnly(true);
    await clock.advance(PAUSE_GRACE_MS);
    assert(c.phase === "wifi_required" && log.drops.join() === "wifi_required", "toggle on pauses");
    c.setWifiOnly(false);
    assert(c.phase === "waiting_stable", "toggle off waits for a stable link again");
    await clock.advance(STABLE_WINDOW_MS);
    assert(log.ready === 2, "toggle off starts again");
  }

  // Wi-Fi only + cellular-override Sync: losing all signal pauses; coming back to cellular does not.
  {
    const { clock, c, log } = harness();
    c.setWifiOnly(true);
    c.observe(CELL);
    c.observe(OFF);
    await clock.advance(PAUSE_GRACE_MS);
    assert(log.drops.join() === "offline", "full drop pauses an override Sync");
    c.observe(CELL);
    c.observe(OFF);
    await clock.advance(1000);
    c.observe(CELL);
    await clock.advance(PAUSE_GRACE_MS);
    assert(log.drops.length === 1, "back on cellular within the grace keeps going");
  }

  // Wi-Fi ↔ cellular handoff while online re-verifies but does not pause.
  {
    const { clock, c, log } = harness();
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    c.observe(CELL);
    assert(c.phase === "waiting_stable", "handoff waits again");
    await clock.advance(STABLE_WINDOW_MS);
    assert(log.drops.length === 0 && log.ready === 2, "handoff: no pause, one more start");
  }

  // kick(): fresh verification starts right away; stale verification pings again first.
  {
    const { clock, c, log } = harness();
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    c.kick();
    assert(log.ready === 2 && log.pings === 1, "fresh: start without another ping");
    await clock.advance(VERIFIED_FRESH_MS + 1);
    c.kick();
    await flush();
    assert(log.pings === 2 && log.ready === 3, "stale: ping again, then start");
    c.observe(OFF);
    c.kick();
    await flush();
    assert(log.ready === 3 && log.pings === 2, "offline kick does nothing");
    c.observe(WIFI);
    c.kick();
    await flush();
    assert(log.ready === 3, "kick during the stability wait does not skip the wait");
  }

  // kick() while unreachable re-pings immediately.
  {
    const { clock, c, log } = harness({ pingResults: [false, true] });
    c.observe(WIFI);
    await clock.advance(STABLE_WINDOW_MS);
    assert(c.phase === "unreachable", "unreachable");
    c.kick();
    await flush();
    assert(log.pings === 2 && log.ready === 1, "kick re-pings an unreachable website");
  }

  // Background wake: two steady readings on the same network.
  assert(backgroundNetDecision(WIFI, WIFI, false) === "ok", "steady Wi-Fi");
  assert(backgroundNetDecision(CELL, CELL, false) === "ok", "steady cellular");
  assert(backgroundNetDecision(OFF, WIFI, false) === "offline", "offline first reading");
  assert(backgroundNetDecision(WIFI, OFF, false) === "unsteady", "dropped between readings");
  assert(backgroundNetDecision(WIFI, CELL, false) === "unsteady", "handoff between readings");
  assert(backgroundNetDecision(CELL, CELL, true) === "wifi_required", "Wi-Fi only on cellular");
  assert(backgroundNetDecision(WIFI, CELL, true) === "wifi_required", "left Wi-Fi with Wi-Fi only");
  assert(backgroundNetDecision(CAPTIVE, CAPTIVE, false) === "offline", "captive portal");
  assert(backgroundNetDecision(WIFI_UNKNOWN, WIFI, false) === "ok", "reachability unknown then known");

  // Queue status copy.
  {
    const now = 5_000_000;
    assert(
      connectionStatusLine({ phase: "offline", waiting: 3, syncing: false, now }).text.startsWith("Offline, 3 lots waiting"),
      "Offline, N lots waiting"
    );
    assert(
      connectionStatusLine({ phase: "offline", waiting: 1, syncing: false, now }).text.startsWith("Offline, 1 lot waiting"),
      "singular lot"
    );
    assert(
      connectionStatusLine({ phase: "offline", waiting: 0, syncing: false, now }).text.startsWith("Offline — keep shooting"),
      "offline with nothing waiting"
    );
    const waiting = connectionStatusLine({ phase: "waiting_stable", waiting: 2, syncing: false, retryAt: now + 9_500, now });
    assert(waiting.text.startsWith("Waiting for stable connection (~10s)"), `waiting copy: ${waiting.text}`);
    assert(waiting.text.includes("2 lots waiting"), "waiting count");
    assert(
      connectionStatusLine({ phase: "checking", waiting: 0, syncing: false, now }).text === "Waiting for stable connection….",
      "checking copy"
    );
    assert(
      connectionStatusLine({ phase: "wifi_required", waiting: 4, syncing: false, now }).text.includes("4 lots waiting for Wi‑Fi"),
      "Wi-Fi only copy"
    );
    assert(
      connectionStatusLine({ phase: "unreachable", waiting: 1, syncing: false, retryAt: now + 15_000, now }).text.includes("in 15s"),
      "unreachable copy"
    );
    assert(connectionStatusLine({ phase: "ready", waiting: 0, syncing: true, now }).text === "Online · Syncing…", "syncing");
    assert(connectionStatusLine({ phase: "ready", waiting: 0, syncing: false, now }).text === "Online", "online idle");
  }

  console.log("connectivity selftest ok");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
