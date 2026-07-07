import { useFocusEffect } from "expo-router";
import { Image } from "expo-image";
import { useCallback, useState } from "react";
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
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
import { useSync } from "@/context/SyncContext";
import { deleteLotDraft, listDraftPhotos, listLotDrafts, setDraftStatus } from "@/lib/queue/db";
import type { LotDraft } from "@/lib/types";
type DraftRow = LotDraft & { photoUris: string[] };

export default function QueueScreen() {
  const { selectedSaleId } = useAuth();
  const { getSaleName, refreshSales } = useSales();
  const { syncing, lastMessage, refreshKey, syncNow } = useSync();
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [previewTotal, setPreviewTotal] = useState(0);

  const refresh = useCallback(async () => {
    await refreshSales();
    const [drafts, net] = await Promise.all([listLotDrafts(), NetInfo.fetch()]);
    const enriched = await Promise.all(
      drafts.map(async (draft) => {
        const photos = await listDraftPhotos(draft.id);
        return { ...draft, photoUris: photos.map((p) => p.file_uri) };
      })
    );
    setRows(enriched);
    setOnline(net.isConnected ?? false);
  }, [refreshSales]);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh, refreshKey])
  );

  async function queueDraft(id: string) {
    await setDraftStatus(id, "queued", null);
    await refresh();
    void syncNow();
  }

  async function syncAll() {
    setMessage("");
    const result = await syncNow();
    if (result) {
      setMessage(
        result.uploaded || result.failed
          ? `Synced ${result.uploaded} lot(s)${result.failed ? `, ${result.failed} failed` : ""}.`
          : online
            ? "Nothing queued to upload."
            : "Offline — connect to Wi‑Fi or cellular to sync."
      );
    } else if (!syncing) {
      setMessage(lastMessage ?? "Sync skipped.");
    }
    await refresh();
  }

  function confirmRemoveLot(item: DraftRow) {
    if (item.status === "uploading" || syncing) {
      Alert.alert("Wait for upload", "This lot is uploading. Try again in a moment.");
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
    setPreviewIndex(index);
    setPreviewTotal(uris.length);
    setPreviewUri(uris[index] ?? null);
  }

  const banner = message || lastMessage;

  return (
    <View style={styles.root}>
      <Text style={styles.status}>
        Network: {online === null ? "…" : online ? "Online" : "Offline"}
        {syncing ? " · Uploading…" : ""}
      </Text>
      <Text style={styles.hint}>Each row shows your photos. After upload, finish catalog review on the web dashboard.</Text>
      {banner ? <Text style={styles.message}>{banner}</Text> : null}

      <Pressable style={[styles.primary, syncing && styles.disabled]} disabled={syncing} onPress={() => void syncAll()}>
        {syncing ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.primaryText}>Sync now</Text>
        )}
      </Pressable>

      <FlatList
        data={rows}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => void refresh()} />}
        ListEmptyComponent={<Text style={styles.empty}>No local lots yet. Capture photos on the Capture tab.</Text>}
        renderItem={({ item }) => {
          const status = draftStatusDisplay(item.status);
          const hero = item.photoUris[0];
          const otherSale = Boolean(selectedSaleId && item.sale_id !== selectedSaleId);
          return (
            <View style={styles.card}>
              <View style={styles.cardTop}>
                {hero ? (
                  <Pressable onPress={() => openPreview(item.photoUris, 0)}>
                    <Image source={{ uri: hero }} style={styles.hero} contentFit="cover" />
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

              {item.photoUris.length > 1 ? (
                <View style={styles.moreRow}>
                  {item.photoUris.slice(1, 4).map((uri, i) => (
                    <Pressable key={uri} onPress={() => openPreview(item.photoUris, i + 1)}>
                      <Image source={{ uri }} style={styles.moreThumb} contentFit="cover" />
                    </Pressable>
                  ))}
                  {item.photoUris.length > 4 ? (
                    <Text style={styles.moreCount}>+{item.photoUris.length - 4} more</Text>
                  ) : null}
                </View>
              ) : null}

              {item.error_message ? <Text style={styles.error}>{item.error_message}</Text> : null}
              {item.status === "draft" ? (
                <Pressable style={styles.secondary} onPress={() => void queueDraft(item.id)}>
                  <Text style={styles.secondaryText}>Queue for upload</Text>
                </Pressable>
              ) : null}
            </View>
          );
        }}
      />

      <PhotoPreviewModal
        uri={previewUri}
        index={previewIndex}
        total={previewTotal}
        onClose={() => setPreviewUri(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 16, gap: 12, backgroundColor: "#020617" },
  status: { color: "#94a3b8", fontSize: 14 },
  hint: { color: "#64748b", fontSize: 13 },
  message: { color: "#cbd5e1", fontSize: 14 },
  primary: {
    backgroundColor: "#0d9488",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  disabled: { opacity: 0.6 },
  empty: { color: "#64748b", marginTop: 24, textAlign: "center", lineHeight: 22 },
  card: {
    backgroundColor: "#0f172a",
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: "#1e293b",
    gap: 10
  },
  cardTop: { flexDirection: "row", gap: 12 },
  hero: { width: 88, height: 88, borderRadius: 10, backgroundColor: "#1e293b" },
  heroEmpty: { alignItems: "center", justifyContent: "center" },
  heroEmptyText: { color: "#64748b", fontSize: 12, textAlign: "center" },
  cardBody: { flex: 1, gap: 3 },
  cardHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  cardTitle: { color: "#f8fafc", fontWeight: "700", fontSize: 16, flex: 1 },
  deleteBtn: {
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#7f1d1d",
    backgroundColor: "#450a0a"
  },
  deleteBtnText: { color: "#fecaca", fontSize: 12, fontWeight: "600" },
  cardStatus: { color: "#2dd4bf", fontSize: 14, fontWeight: "600" },
  cardSale: { color: "#94a3b8", fontSize: 13, fontWeight: "600" },
  cardSaleOther: { color: "#fcd34d" },
  cardHint: { color: "#94a3b8", fontSize: 13, lineHeight: 18 },
  cardMeta: { color: "#64748b", fontSize: 12, marginTop: 2 },
  moreRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  moreThumb: { width: 52, height: 52, borderRadius: 8, backgroundColor: "#1e293b" },
  moreCount: { color: "#64748b", fontSize: 12 },
  error: { color: "#fca5a5", fontSize: 12 },
  secondary: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#1e293b"
  },
  secondaryText: { color: "#e2e8f0", fontSize: 13, fontWeight: "600" }
});
