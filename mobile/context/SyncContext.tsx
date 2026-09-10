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
import { AppState, type AppStateStatus } from "react-native";

import { useAuth } from "@/context/AuthContext";
import { countUploadPending } from "@/lib/queue/db";
import { sweepArchiveConfirmedDrafts, syncPendingLots } from "@/lib/queue/sync";

export type SyncResult = {
  uploaded: number;
  failed: number;
  reconciled: number;
  archived?: number;
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
  if (result.uploaded === 0 && result.failed === 0) {
    if (result.archived) {
      return `Moved ${result.archived} cataloged lot${result.archived === 1 ? "" : "s"} to Archive — photos were removed from this phone.`;
    }
    if (result.reconciled > 0) {
      return `Cleared ${result.reconciled} leftover notice${result.reconciled === 1 ? "" : "s"} — those lots are already on the website.`;
    }
    return "Nothing queued to upload.";
  }
  if (result.failed === 0) {
    return `Synced ${result.uploaded} lot${result.uploaded === 1 ? "" : "s"}.`;
  }
  return `Synced ${result.uploaded} lot${result.uploaded === 1 ? "" : "s"}, ${result.failed} need a check.`;
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const [syncing, setSyncing] = useState(false);
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const syncInProgressRef = useRef(false);
  const syncAgainRef = useRef(false);
  const syncPausedRef = useRef(false);
  const wasConnectedRef = useRef<boolean | null>(null);
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);

  const setSyncPaused = useCallback((paused: boolean) => {
    syncPausedRef.current = paused;
  }, []);

  const syncNow = useCallback(async (): Promise<SyncResult | null> => {
    if (!token) return null;

    if (syncInProgressRef.current) {
      syncAgainRef.current = true;
      return null;
    }

    syncInProgressRef.current = true;
    setSyncing(true);

    try {
      const net = await NetInfo.fetch();
      if (!net.isConnected) {
        setLastMessage("Offline — lots will upload when you're back online.");
        return null;
      }

      setLastMessage("Checking the website for lots already cataloged…");
      const archivedFirst = await sweepArchiveConfirmedDrafts(token);
      if (archivedFirst) {
        setRefreshKey((k) => k + 1);
        setLastMessage(
          `Moved ${archivedFirst} cataloged lot${archivedFirst === 1 ? "" : "s"} to Archive — photos were removed from this phone.`
        );
      }

      const pending = await countUploadPending();
      if (pending === 0) {
        if (!archivedFirst) {
          setLastMessage("Nothing queued to upload — lots already on the website stay in Archive.");
        }
        return { uploaded: 0, failed: 0, reconciled: 0, archived: archivedFirst };
      }

      setLastMessage("Uploading lots that are not on the website yet…");
      const result = await syncPendingLots(token, () => {
        setRefreshKey((k) => k + 1);
      });
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
      if (syncAgainRef.current) {
        syncAgainRef.current = false;
        void syncNow();
      }
    }
  }, [token]);

  const tryAutoSync = useCallback(() => {
    if (syncPausedRef.current) return;
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
      if (prev.match(/inactive|background/) && nextState === "active") {
        tryAutoSync();
      }
    });

    return () => {
      netSub();
      appSub.remove();
    };
  }, [token, tryAutoSync]);

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
