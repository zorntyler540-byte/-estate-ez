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
import { syncPendingLots } from "@/lib/queue/sync";

export type SyncResult = {
  uploaded: number;
  failed: number;
  reconciled: number;
};

type SyncStateValue = {
  syncing: boolean;
  lastMessage: string | null;
  refreshKey: number;
};

type SyncActionsValue = {
  syncNow: () => Promise<SyncResult | null>;
  setSyncPaused: (paused: boolean) => void;
};

type SyncContextValue = SyncStateValue & SyncActionsValue;

const SyncStateContext = createContext<SyncStateValue | null>(null);
const SyncActionsContext = createContext<SyncActionsValue | null>(null);

function formatSyncMessage(result: SyncResult): string {
  if (result.uploaded === 0 && result.failed === 0) {
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

      const pending = await countUploadPending();
      if (pending === 0) {
        setLastMessage(null);
        return { uploaded: 0, failed: 0, reconciled: 0 };
      }

      const result = await syncPendingLots(token, () => {
        setRefreshKey((k) => k + 1);
      });
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

  const stateValue = useMemo(
    () => ({ syncing, lastMessage, refreshKey }),
    [syncing, lastMessage, refreshKey]
  );
  const actionsValue = useMemo(
    () => ({ syncNow, setSyncPaused }),
    [syncNow, setSyncPaused]
  );

  return (
    <SyncActionsContext.Provider value={actionsValue}>
      <SyncStateContext.Provider value={stateValue}>{children}</SyncStateContext.Provider>
    </SyncActionsContext.Provider>
  );
}

export function useSyncActions(): SyncActionsValue {
  const ctx = useContext(SyncActionsContext);
  if (!ctx) throw new Error("useSyncActions must be used within SyncProvider");
  return ctx;
}

export function useSync(): SyncContextValue {
  const actions = useContext(SyncActionsContext);
  const state = useContext(SyncStateContext);
  if (!actions || !state) throw new Error("useSync must be used within SyncProvider");
  return { ...state, ...actions };
}
