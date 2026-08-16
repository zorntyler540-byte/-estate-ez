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

type SyncContextValue = {
  syncing: boolean;
  lastMessage: string | null;
  refreshKey: number;
  syncNow: () => Promise<SyncResult | null>;
};

const SyncContext = createContext<SyncContextValue | null>(null);

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
  const wasConnectedRef = useRef<boolean | null>(null);
  const appStateRef = useRef<AppStateStatus>(AppState.currentState);

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

  useEffect(() => {
    if (!token) return;

    void NetInfo.fetch().then((state) => {
      wasConnectedRef.current = state.isConnected ?? false;
      if (wasConnectedRef.current) {
        void syncNow();
      }
    });

    const netSub = NetInfo.addEventListener((state) => {
      const connected = state.isConnected ?? false;
      const wasConnected = wasConnectedRef.current;
      wasConnectedRef.current = connected;

      if (connected && wasConnected === false) {
        void syncNow();
      }
    });

    const appSub = AppState.addEventListener("change", (nextState) => {
      const prev = appStateRef.current;
      appStateRef.current = nextState;
      if (prev.match(/inactive|background/) && nextState === "active") {
        void syncNow();
      }
    });

    return () => {
      netSub();
      appSub.remove();
    };
  }, [token, syncNow]);

  const value = useMemo(
    () => ({ syncing, lastMessage, refreshKey, syncNow }),
    [syncing, lastMessage, refreshKey, syncNow]
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncContextValue {
  const ctx = useContext(SyncContext);
  if (!ctx) throw new Error("useSync must be used within SyncProvider");
  return ctx;
}
