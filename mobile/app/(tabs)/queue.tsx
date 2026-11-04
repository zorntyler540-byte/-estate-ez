import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Switch,
  View
} from "react-native";
import { Text } from "@/components/AppText";

import { PhotoPreviewModal } from "@/components/DraftPhotos";
import { formatMediaCounts } from "@/lib/mediaKind";
import { connectionStatusLine } from "@/lib/connectivity";
import {
  blockedClipLine,
  countWaitingLots,
  formatSyncOutcome,
  queueRowTitle,
  queueRowView
} from "@/lib/queue/queueStatus";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import {
  useConnectionStatus,
  useSyncActions,
  useSyncRefreshKey,
  useSyncStatus,
  type SyncResult
} from "@/context/SyncContext";
import { getLot, listCatalogedMobileDraftsDetailed, listLots } from "@/lib/api";
import { fetchAllLotPages, queueRowsUnchanged } from "@/lib/lotsReload";
import { pickCatalogLotForDraft, queueAddPhotosIsLocal } from "@/lib/queue/addPhotos";
import {
  deleteArchivedDrafts,
  deleteLotDraft,
  listBlockedClipReasons,
  listDraftPhotos,
  listLotDrafts,
  removeBlockedClips,
  requeueDraft,
  setDraftServerLotId
} from "@/lib/queue/db";
import { queueRowsFromDrafts } from "@/lib/queue/loadQueueRows";
import { sweepArchiveConfirmedDrafts } from "@/lib/queue/sync";
import type { LotDraft } from "@/lib/types";
import { theme } from "@/constants/theme";
import { MIN_TAP_PX, hitSlopFor } from "@/lib/a11y";
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
  return formatSyncOutcome(result, online);
}

export default function QueueScreen() {
  const { token, selectedSaleId, signedInRole } = useAuth();
  const clientLogin = (signedInRole || "").toLowerCase() === "client";
  const { getSaleName } = useSales();
  const { syncing, lastMessage } = useSyncStatus();
  const { syncNow, requestUpload, setWifiOnly } = useSyncActions();
  const connection = useConnectionStatus();
  const online = connection.phase !== "offline";
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [clock, setClock] = useState(() => Date.now());
  const [message, setMessage] = useState("");
  const [previewUris, setPreviewUris] = useState<string[]>([]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [focused, setFocused] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [blockedByDraft, setBlockedByDraft] = useState<Map<string, string[]>>(() => new Map());
  const focusedRef = useRef(false);

  const refresh = useCallback(async (opts?: { archive?: boolean }) => {
    try {
      // Text-only first. Loading every JPEG URI and decoding thumbs is what
      // hard-killed the pink iPhone when this tab opened.
      const [drafts, blocked] = await Promise.all([
        listLotDrafts(),
        listBlockedClipReasons().catch(() => new Map<string, string[]>())
      ]);
      const enriched = queueRowsFromDrafts(drafts);
      setBlockedByDraft(blocked);
      setRows((prev) => (queueRowsUnchanged(prev, enriched) ? prev : enriched));
      if (opts?.archive !== false && token && !syncing && online) {
        try {
          const n = await sweepArchiveConfirmedDrafts(token);
          if (n > 0) {
            setMessage(
              `Moved ${n} cataloged lot${n === 1 ? "" : "s"} to Phone archive — photos were removed from this phone. Website listings stay.`
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
  }, [token, syncing, online]);

  // Countdown for "Waiting for stable connection (~Ns)" — only while that line is showing.
  useEffect(() => {
    if (!focused || (connection.phase !== "waiting_stable" && connection.phase !== "unreachable")) return;
    setClock(Date.now());
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [focused, connection.phase]);

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
    await requeueDraft(id, "queued");
    await refresh();
    requestUpload();
  }

  /** Needs attention: staff fixed the problem (or want another try) — send it again now. */
  async function retryDraft(item: DraftRow) {
    if (syncing) {
      Alert.alert("Wait for upload", "Sync is running. Try again in a moment.");
      return;
    }
    await requeueDraft(item.id, "queued");
    await refresh();
    setMessage("Retrying…");
    const result = await syncNow({ manual: true });
    if (result) setMessage(formatQueueSyncMessage(result, online));
    await refresh();
  }

  /** Reopens this exact lot in the camera (a queued/failed lot goes back to draft there). */
  function continueDraft(item: DraftRow) {
    router.push({
      pathname: "/capture-session",
      params: {
        draftId: item.id,
        ...(item.append_lot_id
          ? {
              appendLotId: item.append_lot_id,
              appendSaleId: item.sale_id,
              ...(item.append_label ? { appendSku: item.append_label } : {})
            }
          : {}),
        ...(item.listing_channel === "facebook" ? { channel: "facebook" } : {})
      }
    });
  }

  function openAppendCamera(
    lotId: string,
    photoCount: number,
    sku?: string | null,
    title?: string | null,
    saleId?: string | null
  ) {
    router.push({
      pathname: "/capture-session",
      params: {
        appendLotId: lotId,
        appendExistingCount: String(photoCount || 0),
        ...(saleId ? { appendSaleId: saleId } : {}),
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
    if (queueAddPhotosIsLocal(item.status) && !item.archived_at) {
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
        const channel = item.listing_channel === "facebook" ? "facebook" : "ctseller";
        const { rows: saleLots } = await fetchAllLotPages((offset, limit) =>
          listLots(token, item.sale_id, { sort: "sku_asc", limit, offset, listingChannel: channel })
        );
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
      if (picked.match === "exact" && item.server_lot_id !== picked.lotId) {
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
      if (picked.match === "sku") {
        // SKU numbers repeat across sales and typos happen — never append without a human check.
        const lotId = picked.lotId;
        Alert.alert(
          "Possible match",
          `The website has ${sku ? `SKU ${sku}` : "a listing"}${title ? ` — ${title}` : ""}. Add photos to that listing?`,
          [
            { text: "Cancel", style: "cancel" },
            {
              text: "Add photos",
              onPress: () => openAppendCamera(lotId, photoCount, sku, title, item.sale_id)
            }
          ]
        );
        return;
      }
      openAppendCamera(picked.lotId, photoCount, sku, title, item.sale_id);
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
      "Uploads these photos again as a new catalog job. Only do this if the listing is missing on the website.\n\nIf it already looks fine online, Cancel — Resend can create a duplicate lot.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Resend",
          onPress: () => {
            void (async () => {
              await requeueDraft(item.id, "queued");
              await refresh();
              setMessage("Re-queued — syncing…");
              const result = await syncNow({ manual: true });
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
    const result = await syncNow({ manual: true });
    if (result) {
      setMessage(formatQueueSyncMessage(result, online));
    }
    // No result (offline, or a Sync was already running): the banner falls back to the live
    // Sync message ("Offline, N lots waiting…") instead of a stale copy captured at tap time.
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
      `Removes ${n} cataloged lot${n === 1 ? "" : "s"} from this phone only. Photos are already gone. Listings stay on the website. Add photos later from the Lots tab or by opening Archive before you clear it.`,
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

    const view = queueRowView(item);
    const remove = () => {
      void (async () => {
        await deleteLotDraft(item.id);
        await refresh();
      })();
    };
    Alert.alert(
      view.removeTitle,
      view.removeBody,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: view.removeLabel,
          style: "destructive",
          onPress: () => {
            const second = view.removeConfirm;
            if (!second) {
              remove();
              return;
            }
            // Sent but not confirmed: if catalog fails, this phone has the only copy.
            Alert.alert(second.title, second.body, [
              { text: "Keep on phone", style: "cancel" },
              { text: view.removeLabel, style: "destructive", onPress: remove }
            ]);
          }
        }
      ]
    );
  }

  function confirmRemoveBlockedClips(item: DraftRow, count: number) {
    if (syncing) {
      Alert.alert("Wait for upload", "Sync is running. Try again in a moment.");
      return;
    }
    const noun = count === 1 ? "video" : `${count} videos`;
    Alert.alert(
      `Delete refused ${noun}?`,
      `The website will not take ${count === 1 ? "this clip" : "these clips"}, so Sync stopped sending ${
        count === 1 ? "it" : "them"
      }. Deleting removes ${count === 1 ? "it" : "them"} from this phone only — photos and the website listing are not changed.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: () => {
            void (async () => {
              const removed = await removeBlockedClips(item.id);
              setMessage(removed ? `Deleted ${removed} refused video${removed === 1 ? "" : "s"} from this phone.` : "");
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
    if (item.archived_at || !(item.photo_count || item.video_count)) return;
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

  const banner = syncing ? lastMessage || message : message || lastMessage;
  const waitingLots = countWaitingLots(activeRows);
  const statusLine = connectionStatusLine({
    phase: connection.phase,
    waiting: waitingLots,
    syncing,
    retryAt: connection.retryAt,
    now: clock
  });
  const showCellularOverride =
    connection.phase === "wifi_required" && waitingLots > 0 && !syncing && !showArchive;

  async function uploadOnCellularOnce() {
    setMessage("");
    const result = await syncNow({ manual: true, allowCellular: true });
    if (result) setMessage(formatQueueSyncMessage(result, online));
    await refresh();
  }

  return (
    <View style={styles.root}>
      <QueueDraftRefresh enabled={focused} onRefresh={() => void refresh({ archive: false })} />
      <Text
        style={[
          styles.status,
          statusLine.tone === "warn" ? styles.statusWarn : statusLine.tone === "ok" ? styles.statusOk : null
        ]}
        accessibilityLiveRegion="polite"
      >
        {statusLine.text}
      </Text>
      <View style={styles.settingRow}>
        <View style={styles.settingText}>
          <Text style={styles.settingLabel}>Upload on Wi‑Fi only</Text>
          <Text style={styles.hint}>Photos and videos wait for Wi‑Fi. Off = cellular is fine.</Text>
        </View>
        <Switch
          accessibilityLabel="Upload on Wi‑Fi only"
          value={connection.wifiOnly}
          onValueChange={(on) => void setWifiOnly(on)}
          trackColor={{ true: theme.primary, false: theme.borderStrong }}
        />
      </View>
      <Text style={styles.hint}>
        {showArchive
          ? "Phone archive only — these listings stay on the website. Photos are already off this phone. Clear Archive when you are done."
          : "Unfinished lots stay here. Sync now sends new ones and checks leftover Sent lots. Cataloged lots move to Phone archive. Do not Resend if the lot is already on the website."}
      </Text>
      {banner ? (
        <Text style={styles.message} accessibilityLiveRegion="polite">
          {banner}
        </Text>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={syncing ? "Syncing" : "Sync now"}
        accessibilityState={{ disabled: syncing, busy: syncing }}
        style={[styles.primary, syncing && styles.disabled]}
        disabled={syncing}
        onPress={() => void syncAll()}
      >
        {syncing ? (
          <ActivityIndicator color={theme.onPrimary} />
        ) : (
          <Text style={styles.primaryText}>Sync now</Text>
        )}
      </Pressable>

      {showCellularOverride ? (
        <Pressable
          accessibilityRole="button"
          style={styles.archiveBtn}
          onPress={() => void uploadOnCellularOnce()}
        >
          <Text style={styles.archiveBtnText}>Upload now on cellular (this time only)</Text>
        </Pressable>
      ) : null}

      {archiveRows.length > 0 || showArchive ? (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: showArchive }}
          style={styles.archiveBtn}
          onPress={() => setShowArchive((v) => !v)}
        >
          <Text style={styles.archiveBtnText}>
            {showArchive
              ? `Back to unfinished lots (${activeRows.length})`
              : `Phone archive (${archiveRows.length}) — website listings stay`}
          </Text>
        </Pressable>
      ) : null}

      {showArchive && archiveRows.length > 0 ? (
        <Pressable accessibilityRole="button" style={styles.archiveBtn} onPress={confirmClearArchive}>
          <Text style={styles.archiveBtnText}>Clear Archive ({archiveRows.length}) — keep website listings</Text>
        </Pressable>
      ) : null}

      <FlatList
        data={visibleRows}
        keyExtractor={(item) => item.id}
        initialNumToRender={8}
        maxToRenderPerBatch={6}
        windowSize={5}
        removeClippedSubviews
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            tintColor={theme.primary}
            onRefresh={() => {
              void (async () => {
                setRefreshing(true);
                try {
                  await refresh();
                } finally {
                  setRefreshing(false);
                }
              })();
            }}
          />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>
            {showArchive
              ? "Phone archive is empty. Cataloged lots land here until you Clear Archive. Website listings stay."
              : clientLogin
                ? "No lots waiting on this phone. Take photos on the Photos tab."
                : "No lots waiting on this phone. Capture photos on the Capture tab."}
          </Text>
        }
        renderItem={({ item }) => {
          const archived = Boolean(item.archived_at);
          const view = queueRowView({
            ...item,
            online,
            connection: connection.phase,
            syncing,
            now: clock
          });
          const otherSale = Boolean(selectedSaleId && item.sale_id !== selectedSaleId);
          const blockedReasons = archived ? [] : blockedByDraft.get(item.id) ?? [];
          const blockedLine = blockedClipLine(blockedReasons);
          const title = queueRowTitle(item);
          const statusColor =
            view.tone === "error"
              ? styles.cardStatusError
              : view.tone === "warn"
                ? styles.cardStatusWarn
                : view.tone === "ok"
                  ? styles.cardStatusOk
                  : styles.cardStatus;
          return (
            <View style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.cardTitle}>{title}</Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`${view.removeLabel} ${title}`}
                  style={styles.deleteBtn}
                  hitSlop={hitSlopFor(32)}
                  onPress={() => confirmRemoveLot(item)}
                >
                  <Text style={styles.deleteBtnText}>{view.removeLabel}</Text>
                </Pressable>
              </View>
              <Text style={statusColor}>{view.label}</Text>
              <Text style={[styles.cardSale, otherSale && styles.cardSaleOther]}>
                Sale: {getSaleName(item.sale_id, item.sale_name)}
                {otherSale && !clientLogin ? " — not the sale currently selected" : ""}
              </Text>
              <Text style={styles.cardHint}>{view.hint}</Text>
              {item.manual_sku && !archived ? <Text style={styles.cardMeta}>SKU: {item.manual_sku}</Text> : null}

              {view.detail ? (
                <Text style={view.tone === "error" ? styles.error : styles.progress}>{view.detail}</Text>
              ) : null}
              {blockedLine ? <Text style={styles.error}>{blockedLine}</Text> : null}
              {item.status !== "uploading" ? (
                <View style={styles.draftActions}>
                  {!archived && (item.photo_count > 0 || item.video_count > 0) ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Review ${formatMediaCounts(item.photo_count, item.video_count)}`}
                      style={styles.secondary}
                      onPress={() => void reviewDraftPhotos(item)}
                    >
                      <Text style={styles.secondaryText}>
                        Review {formatMediaCounts(item.photo_count, item.video_count)}
                      </Text>
                    </Pressable>
                  ) : null}
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Add photos to ${title}`}
                    accessibilityState={{ disabled: syncing && item.status === "uploaded" }}
                    style={[styles.continueBtn, syncing && item.status === "uploaded" && styles.disabled]}
                    disabled={syncing && item.status === "uploaded"}
                    onPress={() => void addPhotosToLot(item)}
                  >
                    <Text style={styles.continueBtnText}>Add photos</Text>
                  </Pressable>
                  {item.status === "draft" ? (
                    <Pressable
                      accessibilityRole="button"
                      style={styles.secondary}
                      onPress={() => void queueDraft(item.id)}
                    >
                      <Text style={styles.secondaryText}>
                        {item.append_lot_id ? "Upload to lot" : "Queue for upload"}
                      </Text>
                    </Pressable>
                  ) : null}
                  {view.showRetry ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Retry ${title}`}
                      accessibilityState={{ disabled: syncing }}
                      style={[styles.secondary, syncing && styles.disabled]}
                      disabled={syncing}
                      onPress={() => void retryDraft(item)}
                    >
                      <Text style={styles.secondaryText}>Retry</Text>
                    </Pressable>
                  ) : null}
                  {view.showResend ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{ disabled: syncing }}
                      style={[styles.secondary, syncing && styles.disabled]}
                      disabled={syncing}
                      onPress={() => confirmResendLot(item)}
                    >
                      <Text style={styles.secondaryText}>Resend to server</Text>
                    </Pressable>
                  ) : null}
                  {blockedReasons.length ? (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Delete ${blockedReasons.length} refused video${blockedReasons.length === 1 ? "" : "s"}`}
                      accessibilityState={{ disabled: syncing }}
                      style={[styles.secondary, styles.dangerOutline, syncing && styles.disabled]}
                      disabled={syncing}
                      onPress={() => confirmRemoveBlockedClips(item, blockedReasons.length)}
                    >
                      <Text style={styles.dangerText}>
                        Delete refused video{blockedReasons.length === 1 ? "" : "s"}
                      </Text>
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
  root: { flex: 1, padding: 16, gap: 12, backgroundColor: theme.bg },
  status: { color: theme.textMuted, fontSize: 14 },
  statusWarn: { color: theme.warning, fontWeight: "600" },
  statusOk: { color: theme.success },
  settingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: MIN_TAP_PX
  },
  settingText: { flex: 1, gap: 2 },
  settingLabel: { color: theme.text, fontSize: 15, fontWeight: "600" },
  hint: { color: theme.textSubtle, fontSize: 13 },
  message: { color: theme.textSoft, fontSize: 14 },
  primary: {
    backgroundColor: theme.primary,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: theme.onPrimary, fontWeight: "600", fontSize: 16 },
  archiveBtn: {
    borderRadius: 10,
    paddingVertical: 12,
    minHeight: MIN_TAP_PX,
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.borderStrong,
    backgroundColor: theme.surface
  },
  archiveBtnText: { color: theme.textSoft, fontWeight: "600", fontSize: 15 },
  disabled: { opacity: 0.6 },
  empty: { color: theme.textSubtle, marginTop: 24, textAlign: "center", lineHeight: 22 },
  card: {
    backgroundColor: theme.bgElevated,
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: theme.border,
    gap: 6
  },
  cardHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  cardTitle: { color: theme.text, fontWeight: "700", fontSize: 16, flex: 1 },
  deleteBtn: {
    paddingVertical: 4,
    minHeight: 32,
    justifyContent: "center",
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: theme.errorBorder,
    backgroundColor: theme.errorBg
  },
  deleteBtnText: { color: theme.error, fontSize: 12, fontWeight: "600" },
  cardStatus: { color: theme.primary, fontSize: 14, fontWeight: "600" },
  cardStatusOk: { color: theme.success, fontSize: 14, fontWeight: "600" },
  cardStatusWarn: { color: theme.warning, fontSize: 14, fontWeight: "600" },
  cardStatusError: { color: theme.error, fontSize: 14, fontWeight: "600" },
  cardSale: { color: theme.textMuted, fontSize: 13, fontWeight: "600" },
  cardSaleOther: { color: theme.warning },
  cardHint: { color: theme.textMuted, fontSize: 13, lineHeight: 18 },
  cardMeta: { color: theme.textSubtle, fontSize: 12, marginTop: 2 },
  error: { color: theme.error, fontSize: 12 },
  progress: { color: theme.warning, fontSize: 12, marginTop: 4 },
  draftActions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  continueBtn: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    minHeight: MIN_TAP_PX,
    justifyContent: "center",
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: theme.primary
  },
  continueBtnText: { color: theme.onPrimary, fontSize: 13, fontWeight: "700" },
  secondary: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    minHeight: MIN_TAP_PX,
    justifyContent: "center",
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: theme.surface
  },
  secondaryText: { color: theme.textSoft, fontSize: 13, fontWeight: "600" },
  dangerOutline: { borderWidth: 1, borderColor: theme.errorBorder, backgroundColor: theme.errorBg },
  dangerText: { color: theme.error, fontSize: 13, fontWeight: "600" }
});
