import { Image } from "expo-image";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useMemo, useState } from "react";
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

import { useAuth } from "@/context/AuthContext";
import { listLots, lotThumbnailSource } from "@/lib/api";
import type { Lot } from "@/lib/types";

function lotSubtitle(lot: Lot): string {
  const parts: string[] = [];
  if (lot.sku?.trim()) parts.push(lot.sku.trim());
  parts.push(`${lot.photo_count} photo${lot.photo_count === 1 ? "" : "s"}`);
  if (lot.ct_category?.trim()) parts.push(lot.ct_category.trim());
  return parts.join(" · ");
}

export default function LotsScreen() {
  const { token, selectedSaleId, selectedSaleName } = useAuth();
  const [lots, setLots] = useState<Lot[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(
    async (mode: "full" | "pull" = "full") => {
      if (!token || !selectedSaleId) {
        setLots([]);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      if (mode === "full") setLoading(true);
      else setRefreshing(true);
      setError("");
      try {
        const rows = await listLots(token, selectedSaleId, { sort: "sku_asc", limit: 500 });
        setLots(rows);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load lots");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [token, selectedSaleId]
  );

  useFocusEffect(
    useCallback(() => {
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
          ? `${lots.length} listing${lots.length === 1 ? "" : "s"} on the server`
          : "Listings appear here after capture uploads finish processing."}
      </Text>

      <TextInput
        style={styles.search}
        placeholder="Search SKU or title"
        placeholderTextColor="#64748b"
        value={query}
        onChangeText={setQuery}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading && !lots.length ? (
        <ActivityIndicator style={{ marginTop: 24 }} color="#2dd4bf" />
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => void load("pull")} tintColor="#2dd4bf" />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>
              {query.trim() ? "No lots match that search." : "No lots in this sale yet."}
            </Text>
          }
          contentContainerStyle={filtered.length ? undefined : styles.emptyPad}
          renderItem={({ item }) => (
            <Pressable style={styles.row} onPress={() => router.push(`/lot/${item.id}`)}>
              <View style={styles.thumbWrap}>
                {item.has_thumbnail && token ? (
                  <Image
                    source={lotThumbnailSource(item.id, token)}
                    style={styles.thumb}
                    contentFit="cover"
                    transition={150}
                  />
                ) : (
                  <View style={styles.thumbEmpty}>
                    <Text style={styles.thumbEmptyText}>No{"\n"}photo</Text>
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
                  {item.ctseller_item_id != null ? (
                    <Text style={[styles.badge, styles.badgePosted]}>Posted</Text>
                  ) : (
                    <Text style={[styles.badge, styles.badgeDraft]}>Not posted</Text>
                  )}
                  {item.is_completed ? (
                    <Text style={[styles.badge, styles.badgeDone]}>Complete</Text>
                  ) : null}
                  {item.sku_needs_review || item.sku_duplicate_in_sale || item.sku_text_mismatch ? (
                    <Text style={[styles.badge, styles.badgeWarn]}>SKU review</Text>
                  ) : null}
                </View>
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, padding: 16, backgroundColor: "#020617" },
  saleName: { color: "#f8fafc", fontSize: 18, fontWeight: "700", marginBottom: 4 },
  lead: { color: "#94a3b8", fontSize: 14, marginBottom: 12 },
  search: {
    borderWidth: 1,
    borderColor: "#334155",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: "#f8fafc",
    marginBottom: 10,
    backgroundColor: "#0f172a"
  },
  error: { color: "#fca5a5", marginBottom: 8 },
  empty: { color: "#64748b", textAlign: "center" },
  emptyPad: { flexGrow: 1, justifyContent: "center", paddingVertical: 48 },
  primary: {
    marginTop: 16,
    backgroundColor: "#0d9488",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: "#fff", fontWeight: "600" },
  row: {
    flexDirection: "row",
    gap: 12,
    padding: 10,
    borderRadius: 10,
    backgroundColor: "#0f172a",
    borderWidth: 1,
    borderColor: "#1e293b",
    marginBottom: 8
  },
  thumbWrap: { width: 72, height: 72, borderRadius: 8, overflow: "hidden", backgroundColor: "#1e293b" },
  thumb: { width: "100%", height: "100%" },
  thumbEmpty: { flex: 1, alignItems: "center", justifyContent: "center" },
  thumbEmptyText: { color: "#64748b", fontSize: 11, textAlign: "center", fontWeight: "600" },
  rowBody: { flex: 1, gap: 4, justifyContent: "center" },
  rowTitle: { color: "#f8fafc", fontSize: 15, fontWeight: "600" },
  rowMeta: { color: "#94a3b8", fontSize: 12 },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 2 },
  badge: {
    fontSize: 11,
    fontWeight: "700",
    overflow: "hidden",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999
  },
  badgePosted: { color: "#042f2e", backgroundColor: "#5eead4" },
  badgeDraft: { color: "#e2e8f0", backgroundColor: "#334155" },
  badgeDone: { color: "#ecfdf5", backgroundColor: "#047857" },
  badgeWarn: { color: "#451a03", backgroundColor: "#fbbf24" }
});
