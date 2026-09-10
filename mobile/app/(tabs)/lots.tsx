import { router, useFocusEffect } from "expo-router";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";

import { StableUriImage } from "@/components/StableUriImage";
import { useAuth } from "@/context/AuthContext";
import { listLots, listSaleLotCoverUrls } from "@/lib/api";
import {
  lotIdsWithChangedPhotoCount,
  lotListSignature,
  lotRowsUnchanged,
  mergeCoverUrls,
  shouldRefreshCoverUrls,
  shouldSkipLotsFocusReload
} from "@/lib/lotsReload";
import type { Lot } from "@/lib/types";

function lotSubtitle(lot: Lot): string {
  const parts: string[] = [];
  if (lot.sku?.trim()) parts.push(lot.sku.trim());
  parts.push(`${lot.photo_count} photo${lot.photo_count === 1 ? "" : "s"}`);
  if ((lot.video_count ?? 0) > 0) {
    parts.push(`${lot.video_count} video${lot.video_count === 1 ? "" : "s"}`);
  }
  if (lot.ct_category?.trim()) parts.push(lot.ct_category.trim());
  return parts.join(" · ");
}

export default function LotsScreen() {
  const { token, selectedSaleId, selectedSaleName } = useAuth();
  const [lots, setLots] = useState<Lot[]>([]);
  const [coverUrls, setCoverUrls] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const lastLoadAtRef = useRef(0);
  const lastSignatureRef = useRef("");
  const lotsLenRef = useRef(0);
  const coverCountRef = useRef(0);
  lotsLenRef.current = lots.length;
  coverCountRef.current = Object.keys(coverUrls).length;

  const load = useCallback(
    async (mode: "full" | "pull" = "full") => {
      if (!token || !selectedSaleId) {
        setLots([]);
        setCoverUrls({});
        setLoading(false);
        setRefreshing(false);
        lastSignatureRef.current = "";
        lastLoadAtRef.current = 0;
        return;
      }
      // Keep the existing grid on screen — a full spinner flash is what looks like a refresh bug.
      if (mode === "full" && lotsLenRef.current === 0) setLoading(true);
      else if (mode === "pull") setRefreshing(true);
      setError("");
      try {
        // Show the list immediately — do not wait on cover signing.
        const rows = await listLots(token, selectedSaleId, {
          sort: "sku_asc",
          limit: 500,
          listingChannel: "ctseller"
        });
        const prevSig = lastSignatureRef.current;
        const nextSig = lotListSignature(rows);
        const refreshCovers = shouldRefreshCoverUrls(prevSig, nextSig, coverCountRef.current);
        const replaceCoverIds = lotIdsWithChangedPhotoCount(prevSig, nextSig);
        lastSignatureRef.current = nextSig;
        lastLoadAtRef.current = Date.now();
        setLots((prev) => (lotRowsUnchanged(prev, rows) ? prev : rows));
        setLoading(false);
        setRefreshing(false);
        if (!refreshCovers) return;
        void listSaleLotCoverUrls(token, selectedSaleId, 160)
          .then((covers) => {
            const next = covers.urls ?? {};
            setCoverUrls((prev) => mergeCoverUrls(prev, next, replaceCoverIds));
          })
          .catch(() => {
            /* keep last thumbs — wiping them is the flash */
          });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load lots");
        setLoading(false);
        setRefreshing(false);
      }
    },
    [token, selectedSaleId]
  );

  useFocusEffect(
    useCallback(() => {
      if (shouldSkipLotsFocusReload(lastLoadAtRef.current, Date.now(), lotsLenRef.current > 0)) {
        return;
      }
      void load("full");
    }, [load])
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return lots;
    return lots.filter((lot) => {
      const hay = [lot.sku, lot.auction_title, lot.ct_category, lot.condition_text]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [lots, query]);

  if (!selectedSaleId) {
    return (
      <View style={styles.root}>
        <Text style={styles.lead}>Select a sale to browse its catalog listings.</Text>
        <Pressable style={styles.primary} onPress={() => router.push("/(tabs)/sale")}>
          <Text style={styles.primaryText}>Choose sale</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Text style={styles.saleName}>{selectedSaleName || "Selected sale"}</Text>
      <Text style={styles.lead}>
        {lots.length
          ? `${lots.length} listing${lots.length === 1 ? "" : "s"} on the server. Add photos if you forgot a shot.`
          : "Listings appear here after capture uploads finish processing."}
      </Text>

      <TextInput
        style={styles.search}
        placeholder="Search SKU or title"
        placeholderTextColor="#78716c"
        value={query}
        onChangeText={setQuery}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading && !lots.length ? (
        <ActivityIndicator style={{ marginTop: 24 }} color="#ff4d1a" />
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          initialNumToRender={10}
          maxToRenderPerBatch={6}
          windowSize={5}
          removeClippedSubviews={false}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => void load("pull")} tintColor="#ff4d1a" />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>
              {query.trim() ? "No lots match that search." : "No lots in this sale yet."}
            </Text>
          }
          contentContainerStyle={filtered.length ? undefined : styles.emptyPad}
          renderItem={({ item }) => {
            // Only R2 signed covers — do not fall back to /thumbnail (that path stalls the grid).
            const signed = coverUrls[item.id];
            return (
              <View style={styles.row}>
                <Pressable style={styles.rowMain} onPress={() => router.push(`/lot/${item.id}`)}>
                  <View style={styles.thumbWrap}>
                    {signed ? (
                      <StableUriImage uri={signed} style={styles.thumb} recyclingKey={item.id} />
                    ) : (
                      <View style={styles.thumbEmpty}>
                        <Text style={styles.thumbEmptyText}>
                          {item.has_thumbnail ? "…" : "No\nphoto"}
                        </Text>
                      </View>
                    )}
                  </View>
                  <View style={styles.rowBody}>
                    <Text style={styles.rowTitle} numberOfLines={2}>
                      {item.auction_title?.trim() || "Untitled lot"}
                    </Text>
                    <Text style={styles.rowMeta} numberOfLines={1}>
                      {lotSubtitle(item)}
                    </Text>
                    <View style={styles.badges}>
                      {item.ctseller_item_id != null || item.ctseller_posted_at ? (
                        <Text style={[styles.badge, styles.badgePosted]}>Posted</Text>
                      ) : (
                        <Text style={[styles.badge, styles.badgeDraft]}>Not posted</Text>
                      )}
                      {item.sku_needs_review || item.sku_duplicate_in_sale || item.sku_text_mismatch ? (
                        <Text style={[styles.badge, styles.badgeWarn]}>SKU review</Text>
                      ) : null}
                    </View>
                  </View>
                </Pressable>
                <Pressable
                  style={styles.addBtn}
                  onPress={() =>
                    router.push({
                      pathname: "/capture-session",
                      params: {
                        appendLotId: item.id,
                        appendExistingCount: String(item.photo_count || 0),
                        ...(item.sku?.trim() ? { appendSku: item.sku.trim() } : {}),
                        ...(item.auction_title?.trim()
                          ? { appendTitle: item.auction_title.trim().slice(0, 80) }
                          : {})
                      }
                    })
                  }
                >
                  <Text style={styles.addBtnText}>Add{"\n"}photos</Text>
                </Pressable>
              </View>
            );
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 16, backgroundColor: "#070708" },
  saleName: { color: "#f5f5f4", fontSize: 18, fontWeight: "700", marginBottom: 4 },
  lead: { color: "#a8a29e", fontSize: 14, marginBottom: 12 },
  search: {
    borderWidth: 1,
    borderColor: "#3f3f46",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: "#f5f5f4",
    marginBottom: 10,
    backgroundColor: "#0e0e10"
  },
  error: { color: "#fca5a5", marginBottom: 8 },
  empty: { color: "#78716c", textAlign: "center" },
  emptyPad: { flexGrow: 1, justifyContent: "center", paddingVertical: 48 },
  primary: {
    marginTop: 16,
    backgroundColor: "#ff4d1a",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: "#fff", fontWeight: "600" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 10,
    borderRadius: 10,
    backgroundColor: "#0e0e10",
    borderWidth: 1,
    borderColor: "#141416",
    marginBottom: 8
  },
  rowMain: { flex: 1, flexDirection: "row", gap: 12, alignItems: "center" },
  addBtn: {
    paddingVertical: 8,
    paddingHorizontal: 8,
    borderRadius: 8,
    backgroundColor: "#ff4d1a",
    alignItems: "center",
    justifyContent: "center",
    minWidth: 64
  },
  addBtnText: { color: "#fff", fontSize: 11, fontWeight: "700", textAlign: "center", lineHeight: 14 },
  thumbWrap: { width: 72, height: 72, borderRadius: 8, overflow: "hidden", backgroundColor: "#141416" },
  thumb: { width: "100%", height: "100%" },
  thumbEmpty: { flex: 1, alignItems: "center", justifyContent: "center" },
  thumbEmptyText: { color: "#78716c", fontSize: 11, textAlign: "center", fontWeight: "600" },
  rowBody: { flex: 1, gap: 4, justifyContent: "center" },
  rowTitle: { color: "#f5f5f4", fontSize: 15, fontWeight: "600" },
  rowMeta: { color: "#a8a29e", fontSize: 12 },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 2 },
  badge: {
    fontSize: 11,
    fontWeight: "700",
    overflow: "hidden",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999
  },
  badgePosted: { color: "#140500", backgroundColor: "#ff4d1a" },
  badgeDraft: { color: "#e7e5e4", backgroundColor: "#3f3f46" },
  badgeDone: { color: "#ecfdf5", backgroundColor: "#047857" },
  badgeWarn: { color: "#451a03", backgroundColor: "#fbbf24" }
});
