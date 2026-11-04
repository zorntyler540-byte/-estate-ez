/**
 * When automatic uploads may start. NetInfo flips ``isConnected`` on every elevator ride and
 * parking-lot handoff; starting Sync on each flip started and killed uploads over and over.
 * Uploads start only after the connection has been steady for a while (longer when it has been
 * flapping) and a quick API health ping answers. Pure — timers and the ping are injected so the
 * selftest drives it with mocked network states.
 */

export type NetSnapshot = {
  isConnected: boolean | null;
  isInternetReachable: boolean | null;
  type?: string | null;
};

export type NetUse = "offline" | "wifi_required" | "usable";

export type ConnectionPhase =
  | "offline"
  | "wifi_required"
  | "waiting_stable"
  | "checking"
  | "unreachable"
  | "ready";

export const STABLE_WINDOW_MS = 12_000;
export const MAX_STABLE_WINDOW_MS = 60_000;
/** Drops inside this window count as flapping and lengthen the next stability wait. */
export const FLAP_LOOKBACK_MS = 2 * 60_000;
/** A blip shorter than this does not pause a running upload. */
export const PAUSE_GRACE_MS = 3_000;
export const PING_RETRY_BASE_MS = 15_000;
export const PING_RETRY_MAX_MS = 2 * 60_000;
/** A successful ping this recent lets a new lot start without pinging again. */
export const VERIFIED_FRESH_MS = 60_000;

export function isUnmeteredNet(type: string | null | undefined): boolean {
  return type === "wifi" || type === "ethernet";
}

/**
 * ``isInternetReachable === null`` (not measured yet) counts as usable: the health ping is the
 * real check. ``false`` means the OS knows there is no internet (captive portal, dead Wi‑Fi).
 */
export function classifyNet(snap: NetSnapshot | null | undefined, wifiOnly: boolean): NetUse {
  if (!snap || snap.isConnected !== true) return "offline";
  if (snap.isInternetReachable === false) return "offline";
  if (wifiOnly && !isUnmeteredNet(snap.type)) return "wifi_required";
  return "usable";
}

export function stableWindowMs(drops: readonly number[], now: number): number {
  const recent = drops.filter((t) => now - t <= FLAP_LOOKBACK_MS).length;
  if (recent <= 1) return STABLE_WINDOW_MS;
  return Math.min(MAX_STABLE_WINDOW_MS, STABLE_WINDOW_MS * 2 ** (recent - 1));
}

export function pingRetryDelayMs(failures: number): number {
  const n = Math.max(1, failures);
  return Math.min(PING_RETRY_MAX_MS, PING_RETRY_BASE_MS * 2 ** (n - 1));
}

export type TimerHandle = unknown;

export type ConnectionDeps = {
  now: () => number;
  setTimer: (fn: () => void, ms: number) => TimerHandle;
  clearTimer: (handle: TimerHandle) => void;
  /** Quick API health check; false / throw = not reachable. */
  ping: () => Promise<boolean>;
  /** Connection is verified: start an automatic Sync if there is work. */
  onReady: () => void;
  /** A drop outlasted the grace period. Pause a running Sync. */
  onDrop: (use: Exclude<NetUse, "usable">) => void;
  onPhase?: (phase: ConnectionPhase, retryAt: number | null) => void;
};

export class ConnectionController {
  private deps: ConnectionDeps;
  private phaseValue: ConnectionPhase = "offline";
  private retryAtValue: number | null = null;
  private use: NetUse = "offline";
  private netType: string | null = null;
  private wifiOnly = false;
  private lastSnap: NetSnapshot | null = null;
  private drops: number[] = [];
  private generation = 0;
  private pingFailures = 0;
  private verifiedAt = 0;
  private stabilityTimer: TimerHandle | null = null;
  private graceTimer: TimerHandle | null = null;
  private graceFrom: NetUse | null = null;
  private disposed = false;

  constructor(deps: ConnectionDeps) {
    this.deps = deps;
  }

  get phase(): ConnectionPhase {
    return this.phaseValue;
  }

  get retryAt(): number | null {
    return this.retryAtValue;
  }

  get netUse(): NetUse {
    return this.use;
  }

  setWifiOnly(on: boolean): void {
    if (this.wifiOnly === on) return;
    this.wifiOnly = on;
    if (this.lastSnap) this.observe(this.lastSnap);
  }

  observe(snap: NetSnapshot): void {
    if (this.disposed) return;
    this.lastSnap = snap;
    const use = classifyNet(snap, this.wifiOnly);
    const type = snap.type ?? null;
    const prev = this.use;
    this.use = use;

    if (use !== "usable") {
      if (prev === "usable") this.noteDrop();
      // Losing the network entirely always matters (a "upload on cellular anyway" Sync is
      // running on cellular); leaving Wi‑Fi matters only when Wi‑Fi only is on.
      if ((use === "offline" && prev !== "offline") || (use === "wifi_required" && prev === "usable")) {
        this.startGrace(prev);
      }
      this.netType = type;
      this.invalidate();
      this.setPhase(use, null);
      return;
    }

    this.cancelGrace();
    const typeChanged = prev === "usable" && type !== this.netType;
    this.netType = type;
    // NetInfo repeats events (signal strength, SSID details): those must not restart the wait.
    if (prev === "usable" && !typeChanged) return;
    this.beginStability();
  }

  /** New work or back in the app: start now when the connection is already verified. */
  kick(): void {
    if (this.disposed) return;
    if (this.phaseValue === "ready") {
      if (this.deps.now() - this.verifiedAt <= VERIFIED_FRESH_MS) {
        this.deps.onReady();
      } else {
        void this.check(this.generation);
      }
      return;
    }
    if (this.phaseValue === "unreachable") {
      this.clearStabilityTimer();
      void this.check(this.generation);
    }
  }

  dispose(): void {
    this.disposed = true;
    this.invalidate();
    this.cancelGrace();
  }

  private noteDrop(): void {
    const now = this.deps.now();
    this.drops = this.drops.filter((t) => now - t <= FLAP_LOOKBACK_MS);
    this.drops.push(now);
  }

  private beginStability(): void {
    this.invalidate();
    this.pingFailures = 0;
    const now = this.deps.now();
    const wait = stableWindowMs(this.drops, now);
    const gen = this.generation;
    this.setPhase("waiting_stable", now + wait);
    this.stabilityTimer = this.deps.setTimer(() => {
      this.stabilityTimer = null;
      void this.check(gen);
    }, wait);
  }

  private async check(gen: number): Promise<void> {
    if (gen !== this.generation || this.disposed) return;
    this.setPhase("checking", null);
    let ok = false;
    try {
      ok = await this.deps.ping();
    } catch {
      ok = false;
    }
    // The network changed while the ping was in flight: that answer is about a dead link.
    if (gen !== this.generation || this.disposed) return;
    if (ok) {
      this.pingFailures = 0;
      this.verifiedAt = this.deps.now();
      this.setPhase("ready", null);
      this.deps.onReady();
      return;
    }
    this.pingFailures += 1;
    const delay = pingRetryDelayMs(this.pingFailures);
    this.setPhase("unreachable", this.deps.now() + delay);
    this.stabilityTimer = this.deps.setTimer(() => {
      this.stabilityTimer = null;
      void this.check(gen);
    }, delay);
  }

  private startGrace(from: NetUse): void {
    if (this.graceTimer != null) return;
    this.graceFrom = from;
    this.graceTimer = this.deps.setTimer(() => {
      this.graceTimer = null;
      const from = this.graceFrom;
      this.graceFrom = null;
      if (this.disposed || this.use === "usable") return;
      // Offline → back on cellular with Wi‑Fi only on: a cellular-override Sync keeps going.
      if (this.use === "wifi_required" && from !== "usable") return;
      this.deps.onDrop(this.use);
    }, PAUSE_GRACE_MS);
  }

  private cancelGrace(): void {
    if (this.graceTimer != null) this.deps.clearTimer(this.graceTimer);
    this.graceTimer = null;
    this.graceFrom = null;
  }

  private clearStabilityTimer(): void {
    if (this.stabilityTimer != null) this.deps.clearTimer(this.stabilityTimer);
    this.stabilityTimer = null;
  }

  private invalidate(): void {
    this.generation += 1;
    this.verifiedAt = 0;
    this.clearStabilityTimer();
  }

  private setPhase(phase: ConnectionPhase, retryAt: number | null): void {
    if (phase === this.phaseValue && retryAt === this.retryAtValue) return;
    this.phaseValue = phase;
    this.retryAtValue = retryAt;
    this.deps.onPhase?.(phase, retryAt);
  }
}

/** Background wake: two readings a few seconds apart stand in for the foreground debounce. */
export const BACKGROUND_STEADY_CHECK_MS = 5_000;

export type BackgroundNetDecision = "ok" | "offline" | "wifi_required" | "unsteady";

/**
 * The OS wakes the app with a network constraint, but it is often a fresh, shaky link (walking
 * out of a building). Upload only when both readings are usable on the same network type.
 */
export function backgroundNetDecision(
  first: NetSnapshot | null,
  second: NetSnapshot | null,
  wifiOnly: boolean
): BackgroundNetDecision {
  const a = classifyNet(first, wifiOnly);
  if (a !== "usable") return a;
  const b = classifyNet(second, wifiOnly);
  if (b !== "usable") return b === "wifi_required" ? "wifi_required" : "unsteady";
  if ((first?.type ?? null) !== (second?.type ?? null)) return "unsteady";
  return "ok";
}

function lotsWord(n: number): string {
  return `${n} lot${n === 1 ? "" : "s"}`;
}

function secondsUntil(at: number | null, now: number): number | null {
  if (at == null) return null;
  return Math.max(1, Math.ceil((at - now) / 1000));
}

/**
 * Queue status line. ``waiting`` = lots on this phone still to send (queued / interrupted /
 * transient failures).
 */
export function connectionStatusLine(opts: {
  phase: ConnectionPhase;
  waiting: number;
  syncing: boolean;
  retryAt?: number | null;
  now?: number;
}): { text: string; tone: "ok" | "progress" | "warn" } {
  const { phase, waiting, syncing } = opts;
  const now = opts.now ?? Date.now();
  if (syncing && (phase === "ready" || phase === "checking")) {
    return { text: "Online · Syncing…", tone: "progress" };
  }
  switch (phase) {
    case "offline":
      return waiting
        ? { text: `Offline, ${lotsWord(waiting)} waiting — they upload on their own when the connection is back.`, tone: "warn" }
        : { text: "Offline — keep shooting. Lots save on this phone and upload later.", tone: "warn" };
    case "wifi_required":
      return waiting
        ? { text: `Upload on Wi‑Fi only is on — ${lotsWord(waiting)} waiting for Wi‑Fi.`, tone: "warn" }
        : { text: "On cellular — Upload on Wi‑Fi only is on.", tone: "ok" };
    case "waiting_stable":
    case "checking": {
      const secs = phase === "waiting_stable" ? secondsUntil(opts.retryAt ?? null, now) : null;
      const tail = waiting ? ` ${lotsWord(waiting)} waiting.` : "";
      return {
        text: `Waiting for stable connection${secs ? ` (~${secs}s)` : "…"}.${tail}`,
        tone: "progress"
      };
    }
    case "unreachable": {
      const secs = secondsUntil(opts.retryAt ?? null, now);
      const tail = waiting ? ` ${lotsWord(waiting)} waiting.` : "";
      return {
        text: `Connected, but the website is not answering — checking again${secs ? ` in ${secs}s` : " soon"}.${tail}`,
        tone: "warn"
      };
    }
    case "ready":
    default:
      return waiting
        ? { text: `Online — ${lotsWord(waiting)} to send. Uploads start on their own.`, tone: "ok" }
        : { text: "Online", tone: "ok" };
  }
}
