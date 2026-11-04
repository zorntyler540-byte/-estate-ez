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
import {
  advanceFollowUp,
  followUpNeedsSync,
  planFollowUp,
  stopFollowUp,
  type FollowUpPlan
} from "@/lib/queue/catalogFollowUp";
import {
  countLotsNeedingPhotoUpload,
  countLotsNeedingWebsiteCheck,
  listFollowUpDrafts
} from "@/lib/queue/db";
import { nextAnnouncement } from "@/lib/a11y";
import { formatSyncOutcome } from "@/lib/queue/queueStatus";
import {
  attachPendingLotVideos,
  loadWebsiteIndex,
  lookupDraftStatuses,
  sweepArchiveConfirmedDrafts,
  syncPendingLots
} from "@/lib/queue/sync";
import { sweepImageCache } from "@/lib/queue/captureFiles";

const AUTO_SYNC_COOLDOWN_MS = 45_000;
const CACHE_SWEEP_INTERVAL_MS = 60 * 60_000;

export type SyncResult = {
  uploaded: number;
  failed: number;
  reconciled: number;
  archived?: number;
  healthSummary?: string | null;
};

type SyncStatusValue = {
  syncing: boolean;
  lastMessage: string | null;
};

type SyncRefreshValue = {
  refreshKey: number;
};

type SyncStateValue = SyncStatusValue & SyncRefreshValue;

type SyncActionsValue = {
  syncNow: () => Promise<SyncResult | null>;
  setSyncPaused: (paused: boolean) => void;
};

type SyncContextValue = SyncStateValue & SyncActionsValue;

const SyncStatusContext = createContext<SyncStatusValue | null>(null);
const SyncRefreshContext = createContext<SyncRefreshValue | null>(null);
const SyncActionsContext = createContext<SyncActionsValue | null>(null);

function formatSyncMessage(result: SyncResult): string {
  return formatSyncOutcome(result);
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [syncing, setSyncing] = useState(false);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const syncInProgressRef = useRef(false);
  const syncPausedRef = useRef(false);
  const lastSyncAtRef = useRef(0);
  const lastCacheSweepAtRef = useRef(0);
  const wasConnectedRef = useRef<boolean | null>(null);
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const followUpPlanRef = useRef<FollowUpPlan | null>(null);
  const [followUpWake, setFollowUpWake] = useState(0);

  const setSyncPaused = useCallback((paused: boolean) => {
    const wasPaused = syncPausedRef.current;
    syncPausedRef.current = paused;
    if (wasPaused && !paused) setFollowUpWake((k) => k + 1);
  }, []);

  const syncNow = useCallback(async (): Promise<SyncResult | null> => {
    if (!token) return null;

    if (syncInProgressRef.current) {
      return null;
    }

    syncInProgressRef.current = true;
    lastSyncAtRef.current = Date.now();
    setSyncing(true);

    try {
      const net = await NetInfo.fetch();
      if (!net.isConnected) {
        setLastMessage("Offline — lots will upload when you're back online.");
        return null;
      }

      setLastMessage("Checking the website for lots already cataloged…");
      // One website index per Sync, shared by every phase below.
      const websiteIndex = await loadWebsiteIndex(token);
      const videosAttached = await attachPendingLotVideos(token, websiteIndex);
      if (videosAttached) {
        setRefreshKey((k) => k + 1);
        setLastMessage(
          `Uploaded ${videosAttached} leftover video${videosAttached === 1 ? "" : "s"} to the website.`
        );
      }
      const archivedFirst = await sweepArchiveConfirmedDrafts(token, websiteIndex);
      if (archivedFirst) {
        setRefreshKey((k) => k + 1);
        setLastMessage(
          `Moved ${archivedFirst} cataloged lot${archivedFirst === 1 ? "" : "s"} to Phone archive — photos were removed from this phone. Website listings stay.`
        );
      }

      const needingUpload = await countLotsNeedingPhotoUpload();
      const needingCheck = await countLotsNeedingWebsiteCheck();
      if (needingCheck === 0) {
        if (!archivedFirst && !videosAttached) {
          setLastMessage("Nothing queued to upload. Cataloged lots stay in Phone archive.");
        }
        return { uploaded: videosAttached, failed: 0, reconciled: 0, archived: archivedFirst };
      }

      if (needingUpload > 0) {
        setLastMessage(
          `Uploading ${needingUpload} lot${needingUpload === 1 ? "" : "s"} not on the website yet…`
        );
      } else {
        setLastMessage("Checking leftover lots on the website…");
      }
      const result = await syncPendingLots(
        token,
        () => {
          setRefreshKey((k) => k + 1);
        },
        websiteIndex
      );
      result.archived = (result.archived || 0) + archivedFirst;
      setLastMessage(formatSyncMessage(result));
      setRefreshKey((k) => k + 1);
      return result;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Sync failed";
      setLastMessage(msg);
      return null;
    } finally {
      syncInProgressRef.current = false;
      setSyncing(false);
      if (Date.now() - lastCacheSweepAtRef.current >= CACHE_SWEEP_INTERVAL_MS) {
        lastCacheSweepAtRef.current = Date.now();
        void sweepImageCache().catch(() => 0);
      }
    }
  }, [token]);

  const tryAutoSync = useCallback(() => {
    if (syncPausedRef.current) return;
    if (syncInProgressRef.current) return;
    if (Date.now() - lastSyncAtRef.current < AUTO_SYNC_COOLDOWN_MS) return;
    void syncNow();
  }, [syncNow]);

  useEffect(() => {
    if (!token) return;

    void NetInfo.fetch().then((state) => {
      wasConnectedRef.current = state.isConnected ?? false;
      if (wasConnectedRef.current) {
        tryAutoSync();
      }
    });

    const netSub = NetInfo.addEventListener((state) => {
      const connected = state.isConnected ?? false;
      const wasConnected = wasConnectedRef.current;
      wasConnectedRef.current = connected;

      if (connected && wasConnected === false) {
        tryAutoSync();
      }
    });

    const appSub = AppState.addEventListener("change", (nextState) => {
      const prev = appStateRef.current;
      appStateRef.current = nextState;
      setAppActive(nextState === "active");
      if (prev.match(/inactive|background/) && nextState === "active") {
        // Back in the app: give still-cataloging lots a fresh, bounded follow-up window.
        followUpPlanRef.current = null;
        tryAutoSync();
      }
    });

    return () => {
      netSub();
      appSub.remove();
    };
  }, [token, tryAutoSync]);

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
      if (syncInProgressRef.current) {
        await schedule();
        return;
      }
      const plan = followUpPlanRef.current;
      if (!plan) return;
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
  const actionsValue = useMemo(
    () => ({ syncNow, setSyncPaused }),
    [syncNow, setSyncPaused]
  );

  return (
    <SyncActionsContext.Provider value={actionsValue}>
      <SyncStatusContext.Provider value={statusValue}>
        <SyncRefreshContext.Provider value={refreshValue}>{children}</SyncRefreshContext.Provider>
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
