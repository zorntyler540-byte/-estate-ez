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

import { DraftPhotoStrip, draftStatusDisplay, PhotoPreviewModal } from "@/components/DraftPhotos";
import { StableUriImage } from "@/components/StableUriImage";
import { isTransientSyncError } from "@/lib/queue/syncRecovery";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { useSyncActions, useSyncRefreshKey, useSyncStatus, type SyncResult } from "@/context/SyncContext";
import { getLot, listCatalogedMobileDraftsDetailed, listLots } from "@/lib/api";
import { queueRowsUnchanged } from "@/lib/lotsReload";
import { pickCatalogLotForDraft, queueAddPhotosIsLocal } from "@/lib/queue/addPhotos";
import { deleteLotDraft, listDraftPhotos, listLotDrafts, setDraftServerLotId, setDraftStatus } from "@/lib/queue/db";
import { mapInBatches, QUEUE_PHOTO_LOAD_BATCH, QUEUE_VISIBLE_THUMBS } from "@/lib/queue/loadQueueRows";
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
    return `Synced ${result.uploaded} lot(s)${result.failed ? `, ${result.failed} need a check` : ""}.`;
  }
  if (result.reconciled > 0) {
    return `Cleared ${result.reconciled} leftover notice${result.reconciled === 1 ? "" : "s"} — those lots are already on the website.`;
  }
  return online ? "Nothing queued to upload." : "Offline — connect to Wi‑Fi or cellular to sync.";
}

export default function QueueScreen() {
  const { token, selectedSaleId } = useAuth();
  const { getSaleName, refreshSales } = useSales();
  const { syncing, lastMessage } = useSyncStatus();
  const { syncNow } = useSyncActions();
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const [previewUris, setPreviewUris] = useState<string[]>([]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [focused, setFocused] = useState(false);
  const focusedRef = useRef(false);

  const refresh = useCallback(async (opts?: { sales?: boolean }) => {
    try {
      if (opts?.sales !== false) await refreshSales();
      const [drafts, net] = await Promise.all([listLotDrafts(), NetInfo.fetch()]);
      // Small batches — Promise.all on every draft opened SQLite + decoded every
      // JPEG at once and killed a field phone when Queue was opened.
      const enriched = await mapInBatches(drafts, QUEUE_PHOTO_LOAD_BATCH, async (draft) => {
        const photos = await listDraftPhotos(draft.id);
        return { ...draft, photoUris: photos.map((p) => p.file_uri).filter(Boolean) };
      });
      setRows((prev) => (queueRowsUnchanged(prev, enriched) ? prev : enriched));
      setOnline(net.isConnected ?? false);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not load Queue");
    }
  }, [refreshSales]);

  useFocusEffect(
    useCallback(() => {
      focusedRef.current = true;
      setFocused(true);
      void refresh({ sales: true });
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
    if (!item.photoUris.length) {
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

  const resendableLots = rows.filter((r) => r.status === "uploaded" && r.photoUris.length > 0);

  /** Re-queue every “Sent to server” lot that still has photos on this phone. */
  function confirmResendAll() {
    if (syncing) {
      Alert.alert("Wait for upload", "Sync is running. Try again in a moment.");
      return;
    }
    if (!resendableLots.length) {
      Alert.alert("Nothing to resend", "No sent lots with photos saved on this phone.");
      return;
    }
    const n = resendableLots.length;
    Alert.alert(
      "Resend all to server?",
      `Re-uploads ${n} lot${n === 1 ? "" : "s"} already marked Sent to server as new catalog jobs.\n\nUse this when web jobs failed or photos are missing online. Lots that already look fine may create duplicates — delete those on the website first if you only need some.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: `Resend ${n}`,
          onPress: () => {
            void (async () => {
              for (const lot of resendableLots) {
                await setDraftStatus(lot.id, "queued", null);
              }
              await refresh();
              setMessage(`Re-queued ${n} lot${n === 1 ? "" : "s"} — syncing…`);
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

  const banner = message || lastMessage;

  return (
    <View style={styles.root}>
      <QueueDraftRefresh enabled={focused} onRefresh={() => void refresh({ sales: false })} />
      <Text style={styles.status}>
        Network: {online === null ? "…" : online ? "Online" : "Offline"}
        {syncing ? " · Uploading…" : ""}
      </Text>
      <Text style={styles.hint}>
        Each row shows photos saved on this phone. Unfinished lots stay here if you leave the app —
        tap Add photos to keep shooting, or Queue / Sync to upload. Review photos here in the order they
        were taken. Catalog review is on the web dashboard.
      </Text>
      {banner ? <Text style={styles.message}>{banner}</Text> : null}

      <Pressable style={[styles.primary, syncing && styles.disabled]} disabled={syncing} onPress={() => void syncAll()}>
        {syncing ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.primaryText}>Sync now</Text>
        )}
      </Pressable>

      {resendableLots.length > 0 ? (
        <Pressable
          style={[styles.resendAll, syncing && styles.disabled]}
          disabled={syncing}
          onPress={confirmResendAll}
        >
          <Text style={styles.resendAllText}>
            Resend all sent lots ({resendableLots.length})
          </Text>
        </Pressable>
      ) : null}

      <FlatList
        data={rows}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refresh()} />}
        ListEmptyComponent={<Text style={styles.empty}>No local lots yet. Capture photos on the Capture tab.</Text>}
        renderItem={({ item }) => {
          const status = draftStatusDisplay(item.status, {
            online,
            syncing,
            errorMessage: item.error_message
          });
          const noticeIsError =
            item.status === "failed" &&
            Boolean(item.error_message) &&
            !isTransientSyncError(item.error_message);
          const hero = item.photoUris[0];
          const otherSale = Boolean(selectedSaleId && item.sale_id !== selectedSaleId);
          return (
            <View style={styles.card}>
              <View style={styles.cardTop}>
                {hero ? (
                  <Pressable onPress={() => openPreview(item.photoUris, 0)}>
                    <StableUriImage
                      uri={hero}
                      style={styles.hero}
                      recyclingKey={item.id}
                      cachePolicy="disk"
                    />
                  </Pressable>
                ) : (
                  <View style={[styles.hero, styles.heroEmpty]}>
                    <Text style={styles.heroEmptyText}>No{'\n'}photo</Text>
                  </View>
                )}
                <View style={styles.cardBody}>
                  <View style={styles.cardHeader}>
                    <Text style={styles.cardTitle}>
                      {item.photo_count} photo{item.photo_count === 1 ? "" : "s"}
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
                </View>
              </View>

              {item.photoUris.length > 0 ? (
                <View style={styles.reviewBlock}>
                  <DraftPhotoStrip
                    uris={item.photoUris}
                    size="md"
                    maxVisible={QUEUE_VISIBLE_THUMBS}
                    cachePolicy="disk"
                    onPressPhoto={(_uri, index) => openPreview(item.photoUris, index)}
                  />
                  {item.photoUris.length > 1 ? (
                    <Pressable
                      style={styles.reviewBtn}
                      onPress={() => openPreview(item.photoUris, 0)}
                    >
                      <Text style={styles.reviewBtnText}>
                        Review all {item.photoUris.length} photos (taken order)
                      </Text>
                    </Pressable>
                  ) : null}
                </View>
              ) : null}

              {item.error_message ? (
                <Text style={noticeIsError ? styles.error : styles.progress}>
                  {item.error_message}
                </Text>
              ) : null}
              {item.status !== "uploading" ? (
                <View style={styles.draftActions}>
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
                  {item.status === "uploaded" ? (
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
    gap: 10
  },
  cardTop: { flexDirection: "row", gap: 12 },
  hero: { width: 88, height: 88, borderRadius: 10, backgroundColor: "#141416" },
  heroEmpty: { alignItems: "center", justifyContent: "center" },
  heroEmptyText: { color: "#78716c", fontSize: 12, textAlign: "center" },
  cardBody: { flex: 1, gap: 3 },
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
  reviewBlock: { gap: 4 },
  reviewBtn: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#1c1917",
    borderWidth: 1,
    borderColor: "#44403c"
  },
  reviewBtnText: { color: "#e7e5e4", fontSize: 13, fontWeight: "700" },
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
