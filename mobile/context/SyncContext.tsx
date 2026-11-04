import NetInfo from "@react-native-community/netinfo";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";
import { AccessibilityInfo, AppState, type AppStateStatus } from "react-native";

import { useAuth } from "@/context/AuthContext";
import { pingApi } from "@/lib/api";
import { setBackgroundUploadEnabled } from "@/lib/backgroundUpload";
import { ConnectionController, type ConnectionPhase } from "@/lib/connectivity";
import {
  advanceFollowUp,
  followUpNeedsSync,
  planFollowUp,
  stopFollowUp,
  type FollowUpPlan
} from "@/lib/queue/catalogFollowUp";
import { countLotsNeedingPhotoUpload, listFollowUpDrafts } from "@/lib/queue/db";
import { nextAnnouncement } from "@/lib/a11y";
import { lookupDraftStatuses } from "@/lib/queue/sync";
import { runSyncPass, type SyncResult } from "@/lib/queue/syncRunner";
import { sweepImageCache } from "@/lib/queue/captureFiles";
import { sharedSyncGate, type SyncRequest } from "@/lib/syncGate";
import { uploadPause } from "@/lib/uploadPause";
import { getUploadOnWifiOnly, setUploadOnWifiOnly } from "@/lib/uploadSettings";

/** Automatic Syncs with nothing to upload (website checks only) run at most this often. */
const AUTO_SYNC_COOLDOWN_MS = 45_000;
const CACHE_SWEEP_INTERVAL_MS = 60 * 60_000;

export type { SyncResult };

type SyncStatusValue = {
  syncing: boolean;
  lastMessage: string | null;
};

type SyncRefreshValue = {
  refreshKey: number;
};

type ConnectionValue = {
  /** offline / wifi_required / waiting_stable / checking / unreachable / ready */
  phase: ConnectionPhase;
  /** When the current wait ends (stability window or next health ping), for countdown copy. */
  retryAt: number | null;
  wifiOnly: boolean;
};

type SyncStateValue = SyncStatusValue & SyncRefreshValue;

/**
 * ``manual`` = the user asked (Sync now / Retry / Resend): runs now, skips retry backoff.
 * ``allowCellular`` = send photos/videos over cellular this once despite Upload on Wi‑Fi only.
 */
export type SyncNowOptions = SyncRequest;

type SyncActionsValue = {
  /** ``null`` when Sync could not start — including "already running" (a rerun is then queued). */
  syncNow: (opts?: SyncNowOptions) => Promise<SyncResult | null>;
  /** New lot queued: upload once the connection is verified stable (never mid-flap). */
  requestUpload: () => void;
  setSyncPaused: (paused: boolean) => void;
  setWifiOnly: (on: boolean) => Promise<void>;
};

type SyncContextValue = SyncStateValue & SyncActionsValue;

const SyncStatusContext = createContext<SyncStatusValue | null>(null);
const SyncRefreshContext = createContext<SyncRefreshValue | null>(null);
const SyncActionsContext = createContext<SyncActionsValue | null>(null);
const ConnectionContext = createContext<ConnectionValue | null>(null);

export function SyncProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [syncing, setSyncing] = useState(false);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [connection, setConnection] = useState<{ phase: ConnectionPhase; retryAt: number | null }>({
    phase: "offline",
    retryAt: null
  });
  const [wifiOnly, setWifiOnlyState] = useState(false);

  const syncNowRef = useRef<(opts?: SyncNowOptions) => Promise<SyncResult | null>>(async () => null);
  const controllerRef = useRef<ConnectionController | null>(null);
  const syncPausedRef = useRef(false);
  /** Connection became ready while the camera was open; start when it closes. */
  const autoPendingRef = useRef(false);
  /** The running Sync was allowed onto cellular despite Wi‑Fi only. */
  const runningOnCellularRef = useRef(false);
  const wifiOnlyRef = useRef(false);
  const lastSyncAtRef = useRef(0);
  const lastCacheSweepAtRef = useRef(0);
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const followUpPlanRef = useRef<FollowUpPlan | null>(null);
  const [followUpWake, setFollowUpWake] = useState(0);

  const syncNow = useCallback(async (opts?: SyncNowOptions): Promise<SyncResult | null> => {
    if (!token) return null;
    if (!sharedSyncGate.begin(opts)) return null;

    runningOnCellularRef.current = Boolean(opts?.allowCellular);
    lastSyncAtRef.current = Date.now();
    setSyncing(true);
    try {
      const result = await runSyncPass(token, {
        manual: Boolean(opts?.manual),
        allowCellular: Boolean(opts?.allowCellular),
        onMessage: setLastMessage,
        onProgress: () => setRefreshKey((k) => k + 1)
      });
      setRefreshKey((k) => k + 1);
      return result;
    } catch (e) {
      setLastMessage(e instanceof Error ? e.message : "Sync failed");
      return null;
    } finally {
      runningOnCellularRef.current = false;
      setSyncing(false);
      if (Date.now() - lastCacheSweepAtRef.current >= CACHE_SWEEP_INTERVAL_MS) {
        lastCacheSweepAtRef.current = Date.now();
        void sweepImageCache().catch(() => 0);
      }
      sharedSyncGate.finish();
    }
  }, [token]);
  syncNowRef.current = syncNow;

  useEffect(() => {
    sharedSyncGate.setRerunHandler((req) => {
      setTimeout(() => void syncNowRef.current(req), 0);
    });
    return () => sharedSyncGate.setRerunHandler(null);
  }, []);

  /** Connection verified (stable + health ping). Uploads start here, not on raw NetInfo flips. */
  const autoStart = useCallback(async () => {
    if (syncPausedRef.current) {
      autoPendingRef.current = true;
      return;
    }
    const waiting = await countLotsNeedingPhotoUpload().catch(() => 0);
    if (!waiting && Date.now() - lastSyncAtRef.current < AUTO_SYNC_COOLDOWN_MS) return;
    // Mid-Sync this queues one rerun, so a lot finished during the upload is not left waiting.
    void syncNowRef.current();
  }, []);

  const requestUpload = useCallback(() => {
    controllerRef.current?.kick();
  }, []);

  const setSyncPaused = useCallback((paused: boolean) => {
    const wasPaused = syncPausedRef.current;
    syncPausedRef.current = paused;
    if (wasPaused && !paused) {
      setFollowUpWake((k) => k + 1);
      if (autoPendingRef.current) {
        autoPendingRef.current = false;
        controllerRef.current?.kick();
      }
    }
  }, []);

  const setWifiOnly = useCallback(async (on: boolean) => {
    wifiOnlyRef.current = on;
    setWifiOnlyState(on);
    controllerRef.current?.setWifiOnly(on);
    await setUploadOnWifiOnly(on);
  }, []);

  useEffect(() => {
    void getUploadOnWifiOnly().then((on) => {
      wifiOnlyRef.current = on;
      setWifiOnlyState(on);
      controllerRef.current?.setWifiOnly(on);
    });
  }, []);

  useEffect(() => {
    void setBackgroundUploadEnabled(Boolean(token));
  }, [token]);

  useEffect(() => {
    if (!token) return;

    const controller = new ConnectionController({
      now: () => Date.now(),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
      ping: () => pingApi(),
      onReady: () => void autoStart(),
      onDrop: (use) => {
        if (!sharedSyncGate.busy) return;
        if (use === "wifi_required") {
          if (!runningOnCellularRef.current) uploadPause.pause("wifi_only");
          return;
        }
        uploadPause.pause("offline");
      },
      onPhase: (phase, retryAt) => setConnection({ phase, retryAt })
    });
    controller.setWifiOnly(wifiOnlyRef.current);
    controllerRef.current = controller;

    void NetInfo.fetch().then((state) => controller.observe(state));
    const netSub = NetInfo.addEventListener((state) => controller.observe(state));

    const appSub = AppState.addEventListener("change", (nextState) => {
      const prev = appStateRef.current;
      appStateRef.current = nextState;
      setAppActive(nextState === "active");
      if (prev.match(/inactive|background/) && nextState === "active") {
        // Back in the app: give still-cataloging lots a fresh, bounded follow-up window, and
        // re-read the network (events can be missed while suspended) before uploading.
        followUpPlanRef.current = null;
        void NetInfo.fetch().then((state) => {
          controller.observe(state);
          controller.kick();
        });
      }
    });

    return () => {
      netSub();
      appSub.remove();
      controller.dispose();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [token, autoStart]);

  // Bounded follow-up while lots catalog: one batched status call per tick, backing off
  // 8s → 5 min, giving up after 30 min. A full Sync runs only when the server has news.
  // No timer while the app is backgrounded or Sync is paused (capture session).
  useEffect(() => {
    if (!token || !appActive) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = async () => {
      const drafts = await listFollowUpDrafts();
      if (cancelled) return;
      const { plan, delayMs } = planFollowUp(
        followUpPlanRef.current,
        drafts.map((d) => d.id),
        Date.now()
      );
      followUpPlanRef.current = plan;
      if (delayMs == null) return;
      timer = setTimeout(() => void tick(), delayMs);
    };

    const runFullSync = async () => {
      lastSyncAtRef.current = 0;
      await syncNow();
      if (!cancelled) await schedule();
    };

    const tick = async () => {
      if (cancelled || syncPausedRef.current || appStateRef.current !== "active") return;
      if (sharedSyncGate.busy) {
        await schedule();
        return;
      }
      const plan = followUpPlanRef.current;
      if (!plan) return;
      // Offline: do not burn the follow-up window on calls that cannot answer.
      const phase = controllerRef.current?.phase;
      if (phase === "offline") {
        await schedule();
        return;
      }
      followUpPlanRef.current = advanceFollowUp(plan);
      const drafts = await listFollowUpDrafts();
      if (cancelled || !drafts.length) return;
      const looked = await lookupDraftStatuses(
        token,
        drafts.map((d) => d.id)
      );
      if (cancelled) return;
      if (looked.ok) {
        if (followUpNeedsSync(drafts, looked.rows)) {
          await runFullSync();
        } else {
          await schedule();
        }
        return;
      }
      if (looked.endpointMissing) {
        await runFullSync();
        return;
      }
      if (looked.permanent) {
        followUpPlanRef.current = stopFollowUp(followUpPlanRef.current!);
        return;
      }
      await schedule();
    };

    void schedule();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [token, appActive, refreshKey, followUpWake, syncNow]);

  // Screen readers hear the outcome of each Sync once. Interim "Checking…" copy is skipped
  // because auto-sync runs on every foreground and would talk over whatever the user is doing.
  const wasSyncingRef = useRef(false);
  const announcedRef = useRef<string | null>(null);
  useEffect(() => {
    const finished = wasSyncingRef.current && !syncing;
    wasSyncingRef.current = syncing;
    if (!finished) return;
    const text = nextAnnouncement(announcedRef.current, lastMessage);
    if (!text) return;
    announcedRef.current = text;
    AccessibilityInfo.announceForAccessibility(text);
  }, [syncing, lastMessage]);

  const statusValue = useMemo(
    () => ({ syncing, lastMessage }),
    [syncing, lastMessage]
  );
  const refreshValue = useMemo(() => ({ refreshKey }), [refreshKey]);
  const connectionValue = useMemo(
    () => ({ phase: connection.phase, retryAt: connection.retryAt, wifiOnly }),
    [connection, wifiOnly]
  );
  const actionsValue = useMemo(
    () => ({ syncNow, requestUpload, setSyncPaused, setWifiOnly }),
    [syncNow, requestUpload, setSyncPaused, setWifiOnly]
  );

  return (
    <SyncActionsContext.Provider value={actionsValue}>
      <SyncStatusContext.Provider value={statusValue}>
        <ConnectionContext.Provider value={connectionValue}>
          <SyncRefreshContext.Provider value={refreshValue}>{children}</SyncRefreshContext.Provider>
        </ConnectionContext.Provider>
      </SyncStatusContext.Provider>
    </SyncActionsContext.Provider>
  );
}

export function useSyncActions(): SyncActionsValue {
  const ctx = useContext(SyncActionsContext);
  if (!ctx) throw new Error("useSyncActions must be used within SyncProvider");
  return ctx;
}

export function useSyncStatus(): SyncStatusValue {
  const ctx = useContext(SyncStatusContext);
  if (!ctx) throw new Error("useSyncStatus must be used within SyncProvider");
  return ctx;
}

export function useConnectionStatus(): ConnectionValue {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error("useConnectionStatus must be used within SyncProvider");
  return ctx;
}

export function useSyncRefreshKey(): number {
  const ctx = useContext(SyncRefreshContext);
  if (!ctx) throw new Error("useSyncRefreshKey must be used within SyncProvider");
  return ctx.refreshKey;
}

export function useSync(): SyncContextValue {
  const actions = useContext(SyncActionsContext);
  const status = useContext(SyncStatusContext);
  const refresh = useContext(SyncRefreshContext);
  if (!actions || !status || !refresh) throw new Error("useSync must be used within SyncProvider");
  return { ...status, ...refresh, ...actions };
}
