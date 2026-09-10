import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View
} from "react-native";
import NetInfo from "@react-native-community/netinfo";

import { draftStatusDisplay, PhotoPreviewModal } from "@/components/DraftPhotos";
import { isTransientSyncError } from "@/lib/queue/syncRecovery";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { useSyncActions, useSyncRefreshKey, useSyncStatus, type SyncResult } from "@/context/SyncContext";
import { getLot, listCatalogedMobileDraftsDetailed, listLots } from "@/lib/api";
import { queueRowsUnchanged } from "@/lib/lotsReload";
import { pickCatalogLotForDraft, queueAddPhotosIsLocal } from "@/lib/queue/addPhotos";
import { deleteArchivedDrafts, deleteLotDraft, listDraftPhotos, listLotDrafts, setDraftServerLotId, setDraftStatus } from "@/lib/queue/db";
import { queueRowsFromDrafts } from "@/lib/queue/loadQueueRows";
import { sweepArchiveConfirmedDrafts } from "@/lib/queue/sync";
import type { LotDraft } from "@/lib/types";
type DraftRow = LotDraft & { photoUris: string[] };

/** Subscribes to sync ticks without making the Queue list re-render on every lot upload. */
function QueueDraftRefresh({
  enabled,
  onRefresh
}: {
  enabled: boolean;
  onRefresh: () => void;
}) {
  const refreshKey = useSyncRefreshKey();
  const enabledRef = useRef(enabled);
  const onRefreshRef = useRef(onRefresh);
  enabledRef.current = enabled;
  onRefreshRef.current = onRefresh;
  useEffect(() => {
    if (!enabledRef.current || refreshKey === 0) return;
    const timer = setTimeout(() => {
      if (enabledRef.current) onRefreshRef.current();
    }, 1500);
    return () => clearTimeout(timer);
  }, [refreshKey]);
  return null;
}

function formatQueueSyncMessage(result: SyncResult, online: boolean | null): string {
  if (result.uploaded || result.failed) {
    const extra = result.archived
      ? ` Moved ${result.archived} cataloged lot${result.archived === 1 ? "" : "s"} to Archive.`
      : "";
    return `Synced ${result.uploaded} lot(s)${result.failed ? `, ${result.failed} need a check` : ""}.${extra}`;
  }
  if (result.archived) {
    return `Moved ${result.archived} cataloged lot${result.archived === 1 ? "" : "s"} to Archive — photos were removed from this phone.`;
  }
  if (result.reconciled > 0) {
    return `Cleared ${result.reconciled} leftover notice${result.reconciled === 1 ? "" : "s"} — those lots are already on the website.`;
  }
  return online ? "Nothing queued to upload." : "Offline — connect to Wi‑Fi or cellular to sync.";
}

export default function QueueScreen() {
  const { token, selectedSaleId } = useAuth();
  const { getSaleName } = useSales();
  const { syncing, lastMessage } = useSyncStatus();
  const { syncNow } = useSyncActions();
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const [previewUris, setPreviewUris] = useState<string[]>([]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [focused, setFocused] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const focusedRef = useRef(false);

  const refresh = useCallback(async (opts?: { archive?: boolean }) => {
    try {
      // Text-only first. Loading every JPEG URI and decoding thumbs is what
      // hard-killed the pink iPhone when this tab opened.
      const [drafts, net] = await Promise.all([listLotDrafts(), NetInfo.fetch()]);
      const enriched = queueRowsFromDrafts(drafts);
      setRows((prev) => (queueRowsUnchanged(prev, enriched) ? prev : enriched));
      setOnline(net.isConnected ?? false);
      if (opts?.archive !== false && token && !syncing) {
        try {
          const n = await sweepArchiveConfirmedDrafts(token);
          if (n > 0) {
            setMessage(
              `Moved ${n} cataloged lot${n === 1 ? "" : "s"} to Archive — photos were removed from this phone.`
            );
            const after = await listLotDrafts();
            const next = queueRowsFromDrafts(after);
            setRows((prev) => (queueRowsUnchanged(prev, next) ? prev : next));
          }
        } catch {
          /* stay offline-capable — Sync will archive later */
        }
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not load Queue");
    }
  }, [token, syncing]);

  useFocusEffect(
    useCallback(() => {
      focusedRef.current = true;
      setFocused(true);
      void refresh({ archive: true });
      return () => {
        focusedRef.current = false;
        setFocused(false);
      };
    }, [refresh])
  );

  async function queueDraft(id: string) {
    await setDraftStatus(id, "queued", null);
    await refresh();
    void syncNow();
  }

  function continueDraft(item: DraftRow) {
    router.push({
      pathname: "/capture-session",
      params: {
        draftId: item.id,
        ...(item.listing_channel === "facebook" ? { channel: "facebook" } : {})
      }
    });
  }

  function openAppendCamera(lotId: string, photoCount: number, sku?: string | null, title?: string | null) {
    router.push({
      pathname: "/capture-session",
      params: {
        appendLotId: lotId,
        appendExistingCount: String(photoCount || 0),
        ...(sku?.trim() ? { appendSku: sku.trim() } : {}),
        ...(title?.trim() ? { appendTitle: title.trim().slice(0, 80) } : {})
      }
    });
  }

  async function addPhotosToLot(item: DraftRow) {
    if (item.status === "uploading" || syncing) {
      Alert.alert("Wait for upload", "This lot is uploading. Try again in a moment.");
      return;
    }
    if (queueAddPhotosIsLocal(item.status)) {
      continueDraft(item);
      return;
    }
    if (!token) {
      Alert.alert("Sign in", "Sign in again to add photos to a listing already on the website.");
      return;
    }
    setMessage("Finding that listing…");
    try {
      const detailed = await listCatalogedMobileDraftsDetailed(token, item.sale_id);
      const mapped = detailed.lots.map((row) => ({
        draftId: row.draft_id,
        lotId: row.lot_id,
        sku: row.sku,
        photoCount: row.photo_count ?? 0,
        title: row.auction_title
      }));
      let picked = pickCatalogLotForDraft({
        draftId: item.id,
        manualSku: item.manual_sku,
        cachedLotId: item.server_lot_id,
        mapped
      });
      if (!picked && item.manual_sku?.trim()) {
        const saleLots = await listLots(token, item.sale_id, {
          sort: "sku_asc",
          limit: 500,
          listingChannel: item.listing_channel === "facebook" ? "facebook" : "ctseller"
        });
        picked = pickCatalogLotForDraft({
          draftId: item.id,
          manualSku: item.manual_sku,
          cachedLotId: item.server_lot_id,
          mapped,
          lotsBySku: saleLots
        });
      }
      if (!picked) {
        setMessage("");
        Alert.alert(
          "Open Lots to add photos",
          "This phone lot is sent, but I could not match it to a website listing. Open the Lots tab, tap the listing, then Add photos."
        );
        return;
      }
      if (item.server_lot_id !== picked.lotId) {
        await setDraftServerLotId(item.id, picked.lotId);
      }
      let photoCount = picked.photoCount;
      let sku = picked.sku;
      let title = picked.title;
      try {
        const lot = await getLot(token, picked.lotId);
        photoCount = lot.photo_count || photoCount;
        sku = lot.sku || sku;
        title = lot.auction_title || title;
      } catch {
        /* cached mapping is enough to open the camera */
      }
      setMessage("");
      openAppendCamera(picked.lotId, photoCount, sku, title);
    } catch (e) {
      setMessage("");
      Alert.alert("Could not add photos", e instanceof Error ? e.message : "Try the Lots tab.");
    }
  }

  /** Re-queue a lot already marked uploaded so Sync sends photos again (e.g. server lost staging). */
  function confirmResendLot(item: DraftRow) {
    if (item.status === "uploading" || syncing) {
      Alert.alert("Wait for upload", "This lot is uploading. Try again in a moment.");
      return;
    }
    if (!item.photo_count) {
      Alert.alert("No photos", "This lot has no photos saved on the phone to resend.");
      return;
    }
    Alert.alert(
      "Resend to server?",
      "Uploads these photos again as a new catalog job. Use this if the web job failed or photos are missing on the dashboard.\n\nIf that listing already looks fine online, skip this — you may create a duplicate.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Resend",
          onPress: () => {
            void (async () => {
              await setDraftStatus(item.id, "queued", null);
              await refresh();
              setMessage("Re-queued — syncing…");
              const result = await syncNow();
              if (result) {
                setMessage(formatQueueSyncMessage(result, online));
              }
              await refresh();
            })();
          }
        }
      ]
    );
  }

  const activeRows = rows.filter((r) => !r.archived_at);
  const archiveRows = rows.filter((r) => r.archived_at);
  const visibleRows = showArchive ? archiveRows : activeRows;

  async function syncAll() {
    setMessage("");
    const result = await syncNow();
    if (result) {
      setMessage(formatQueueSyncMessage(result, online));
    } else if (!syncing) {
      setMessage(lastMessage ?? "Sync skipped.");
    }
    await refresh();
  }

  function confirmClearArchive() {
    if (syncing) {
      Alert.alert("Wait for upload", "Sync is running. Try again in a moment.");
      return;
    }
    const n = archiveRows.length;
    if (!n) return;
    Alert.alert(
      "Clear Archive?",
      `Removes ${n} cataloged lot${n === 1 ? "" : "s"} from this phone only. Photos are already gone. Listings stay on the website. Add photos later from the Lots tab.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Clear Archive",
          style: "destructive",
          onPress: () => {
            void (async () => {
              const removed = await deleteArchivedDrafts();
              setShowArchive(false);
              setMessage(
                removed
                  ? `Cleared ${removed} lot${removed === 1 ? "" : "s"} from Archive. Website listings were not changed.`
                  : "Archive was already empty."
              );
              await refresh({ archive: false });
            })();
          }
        }
      ]
    );
  }

  function confirmRemoveLot(item: DraftRow) {
    // Block deletes during a live sync. Stuck "uploading" (app killed mid-send) is
    // deletable when idle; Sync now also auto-retries those.
    if (syncing) {
      Alert.alert("Wait for upload", "Sync is running. Try again in a moment.");
      return;
    }

    const alreadySent = item.status === "uploaded";
    Alert.alert(
      alreadySent ? "Remove from phone?" : "Delete local lot?",
      alreadySent
        ? `${item.photo_count} photo${item.photo_count === 1 ? "" : "s"} already sent to the server. This only removes the copy saved on your phone.`
        : `Delete ${item.photo_count} photo${item.photo_count === 1 ? "" : "s"} from this phone? This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: alreadySent ? "Remove" : "Delete",
          style: "destructive",
          onPress: () => {
            void (async () => {
              await deleteLotDraft(item.id);
              await refresh();
            })();
          }
        }
      ]
    );
  }
  function openPreview(uris: string[], index: number) {
    if (!uris.length) return;
    setPreviewUris(uris);
    setPreviewIndex(Math.max(0, Math.min(index, uris.length - 1)));
  }

  async function reviewDraftPhotos(item: DraftRow) {
    if (item.archived_at || !item.photo_count) return;
    try {
      const photos = await listDraftPhotos(item.id);
      const uris = photos.map((p) => p.file_uri).filter(Boolean);
      if (!uris.length) {
        Alert.alert("No photos", "This lot has no photos saved on this phone.");
        return;
      }
      openPreview(uris, 0);
    } catch (e) {
      Alert.alert("Could not open photos", e instanceof Error ? e.message : "Try again.");
    }
  }

  const banner = message || lastMessage;

  return (
    <View style={styles.root}>
      <QueueDraftRefresh enabled={focused} onRefresh={() => void refresh({ archive: false })} />
      <Text style={styles.status}>
        Network: {online === null ? "…" : online ? "Online" : "Offline"}
        {syncing ? " · Syncing…" : ""}
      </Text>
      <Text style={styles.hint}>
        {showArchive
          ? "Temporary list of cataloged lots. Photos are already off this phone. Clear Archive when you are done — website listings stay."
          : "Unfinished lots stay here (names only). Sync now checks the website first — lots already cataloged move to Archive. It does not re-upload those."}
      </Text>
      {banner ? <Text style={styles.message}>{banner}</Text> : null}

      <Pressable style={[styles.primary, syncing && styles.disabled]} disabled={syncing} onPress={() => void syncAll()}>
        {syncing ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.primaryText}>Sync now</Text>
        )}
      </Pressable>

      {/* Bulk Resend is hidden — leftover Sent lots are usually already on the website. */}

      {archiveRows.length > 0 || showArchive ? (
        <Pressable style={styles.resendAll} onPress={() => setShowArchive((v) => !v)}>
          <Text style={styles.resendAllText}>
            {showArchive
              ? `Back to Queue (${activeRows.length})`
              : `Archive (${archiveRows.length}) — cataloged, photos off this phone`}
          </Text>
        </Pressable>
      ) : null}

      {showArchive && archiveRows.length > 0 ? (
        <Pressable style={styles.resendAll} onPress={confirmClearArchive}>
          <Text style={styles.resendAllText}>Clear Archive ({archiveRows.length}) — keep website listings</Text>
        </Pressable>
      ) : null}

      <FlatList
        data={visibleRows}
        keyExtractor={(item) => item.id}
        initialNumToRender={8}
        maxToRenderPerBatch={6}
        windowSize={5}
        removeClippedSubviews
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refresh()} />}
        ListEmptyComponent={
          <Text style={styles.empty}>
            {showArchive
              ? "Archive is empty. Cataloged lots land here until you Clear Archive. Website listings stay."
              : "No lots waiting on this phone. Capture photos on the Capture tab."}
          </Text>
        }
        renderItem={({ item }) => {
          const archived = Boolean(item.archived_at);
          const status = draftStatusDisplay(item.status, {
            online,
            syncing,
            errorMessage: item.error_message,
            archived
          });
          const noticeIsError =
            item.status === "failed" &&
            Boolean(item.error_message) &&
            !isTransientSyncError(item.error_message);
          const otherSale = Boolean(selectedSaleId && item.sale_id !== selectedSaleId);
          return (
            <View style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.cardTitle}>
                  {archived
                    ? `${item.photo_count || 0} photo${item.photo_count === 1 ? "" : "s"} cataloged`
                    : `${item.photo_count} photo${item.photo_count === 1 ? "" : "s"}`}
                </Text>
                <Pressable
                  style={styles.deleteBtn}
                  hitSlop={8}
                  onPress={() => confirmRemoveLot(item)}
                >
                  <Text style={styles.deleteBtnText}>Delete</Text>
                </Pressable>
              </View>
              <Text style={styles.cardStatus}>{status.label}</Text>
              <Text style={[styles.cardSale, otherSale && styles.cardSaleOther]}>
                Sale: {getSaleName(item.sale_id, item.sale_name)}
                {otherSale ? " — not the sale currently selected" : ""}
              </Text>
              <Text style={styles.cardHint}>{status.hint}</Text>
              {item.manual_sku ? <Text style={styles.cardMeta}>SKU: {item.manual_sku}</Text> : null}

              {item.error_message ? (
                <Text style={noticeIsError ? styles.error : styles.progress}>
                  {item.error_message}
                </Text>
              ) : null}
              {item.status !== "uploading" ? (
                <View style={styles.draftActions}>
                  {!archived && item.photo_count > 0 ? (
                    <Pressable style={styles.secondary} onPress={() => void reviewDraftPhotos(item)}>
                      <Text style={styles.secondaryText}>
                        Review {item.photo_count} photo{item.photo_count === 1 ? "" : "s"}
                      </Text>
                    </Pressable>
                  ) : null}
                  <Pressable
                    style={[styles.continueBtn, syncing && item.status === "uploaded" && styles.disabled]}
                    disabled={syncing && item.status === "uploaded"}
                    onPress={() => void addPhotosToLot(item)}
                  >
                    <Text style={styles.continueBtnText}>Add photos</Text>
                  </Pressable>
                  {item.status === "draft" ? (
                    <Pressable style={styles.secondary} onPress={() => void queueDraft(item.id)}>
                      <Text style={styles.secondaryText}>Queue for upload</Text>
                    </Pressable>
                  ) : null}
                  {item.status === "uploaded" && !archived && item.photo_count > 0 ? (
                    <Pressable
                      style={[styles.secondary, syncing && styles.disabled]}
                      disabled={syncing}
                      onPress={() => confirmResendLot(item)}
                    >
                      <Text style={styles.secondaryText}>Resend to server</Text>
                    </Pressable>
                  ) : null}
                </View>
              ) : null}
            </View>
          );
        }}
      />

      <PhotoPreviewModal
        uris={previewUris}
        index={previewIndex}
        onIndexChange={setPreviewIndex}
        onClose={() => setPreviewUris([])}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 16, gap: 12, backgroundColor: "#070708" },
  status: { color: "#a8a29e", fontSize: 14 },
  hint: { color: "#78716c", fontSize: 13 },
  message: { color: "#d6d3d1", fontSize: 14 },
  primary: {
    backgroundColor: "#ff4d1a",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  resendAll: {
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#3f3f46",
    backgroundColor: "#141416"
  },
  resendAllText: { color: "#e7e5e4", fontWeight: "600", fontSize: 15 },
  disabled: { opacity: 0.6 },
  empty: { color: "#78716c", marginTop: 24, textAlign: "center", lineHeight: 22 },
  card: {
    backgroundColor: "#0e0e10",
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: "#141416",
    gap: 6
  },
  cardHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  cardTitle: { color: "#f5f5f4", fontWeight: "700", fontSize: 16, flex: 1 },
  deleteBtn: {
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#7f1d1d",
    backgroundColor: "#450a0a"
  },
  deleteBtnText: { color: "#fecaca", fontSize: 12, fontWeight: "600" },
  cardStatus: { color: "#ff4d1a", fontSize: 14, fontWeight: "600" },
  cardSale: { color: "#a8a29e", fontSize: 13, fontWeight: "600" },
  cardSaleOther: { color: "#fcd34d" },
  cardHint: { color: "#a8a29e", fontSize: 13, lineHeight: 18 },
  cardMeta: { color: "#78716c", fontSize: 12, marginTop: 2 },
  error: { color: "#fca5a5", fontSize: 12 },
  progress: { color: "#fdba74", fontSize: 12, marginTop: 4 },
  draftActions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  continueBtn: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#ff4d1a"
  },
  continueBtnText: { color: "#fff", fontSize: 13, fontWeight: "700" },
  secondary: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#141416"
  },
  secondaryText: { color: "#e7e5e4", fontSize: 13, fontWeight: "600" }
});
