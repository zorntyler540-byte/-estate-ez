import { Image } from "expo-image";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  View
} from "react-native";

import { theme } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useSales } from "@/context/SalesContext";
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

function facebookListingText(lot: Lot): string {
  const title = lot.auction_title?.trim() || "Furniture item";
  const price =
    lot.starting_price != null && !Number.isNaN(lot.starting_price)
      ? `$${lot.starting_price.toFixed(2)}`
      : null;
  const lines = [title];
  if (price) lines.push(price);
  const desc = lot.professional_description?.trim();
  if (desc) {
    lines.push("");
    lines.push(desc);
  }
  const condition = lot.condition_text?.trim();
  if (condition) {
    lines.push("");
    lines.push(`Condition: ${condition}`);
  }
  return lines.join("\n");
}

export default function FacebookScreen() {
  const { token, selectedSaleId, selectedSaleName } = useAuth();
  const { getSaleName } = useSales();
  const [lots, setLots] = useState<Lot[]>([]);
  const [coverUrls, setCoverUrls] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
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
      if (mode === "full" && lotsLenRef.current === 0) setLoading(true);
      else if (mode === "pull") setRefreshing(true);
      setError("");
      try {
        const rows = await listLots(token, selectedSaleId, {
          sort: "created_desc",
          limit: 500,
          listingChannel: "facebook"
        });
        const prevSig = lastSignatureRef.current;
        const nextSig = lotListSignature(rows);
        const refreshCovers = shouldRefreshCoverUrls(prevSig, nextSig, coverCountRef.current);
        const replaceCoverIds = lotIdsWithChangedPhotoCount(prevSig, nextSig);
        lastSignatureRef.current = nextSig;
        lastLoadAtRef.current = Date.now();
        setLots((prev) => (lotRowsUnchanged(prev, rows) ? prev : rows));
        if (refreshCovers) {
          try {
            const covers = await listSaleLotCoverUrls(token, selectedSaleId, 160);
            const next = covers.urls || {};
            setCoverUrls((prev) => mergeCoverUrls(prev, next, replaceCoverIds));
          } catch {
            /* keep last thumbs */
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load Facebook items");
      } finally {
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

  function startCapture() {
    if (!selectedSaleId) {
      router.push("/(tabs)/sale");
      return;
    }
    router.push({ pathname: "/capture-session", params: { channel: "facebook" } });
  }

  async function shareListing(lot: Lot) {
    const text = facebookListingText(lot);
    try {
      await Share.share({ message: text, title: lot.auction_title?.trim() || "Facebook listing" });
    } catch (e) {
      Alert.alert("Share failed", e instanceof Error ? e.message : "Could not share listing text");
    }
  }

  return (
    <View style={styles.root}>
      <Text style={styles.lead}>
        Furniture for Facebook Marketplace only — these items never post to CT Bids.
      </Text>

      {!selectedSaleId ? (
        <Text style={styles.warn}>Choose a sale on the Sale tab first (used for organization).</Text>
      ) : (
        <Text style={styles.meta}>Sale: {getSaleName(selectedSaleId, selectedSaleName)}</Text>
      )}

      <Pressable style={styles.primary} onPress={startCapture}>
        <Text style={styles.primaryText}>Add Facebook item</Text>
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading && !lots.length ? (
        <ActivityIndicator style={{ marginTop: 24 }} color={theme.primary} />
      ) : (
        <FlatList
          data={lots}
          keyExtractor={(item) => item.id}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void load("pull")}
              tintColor={theme.primary}
            />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>No Facebook items yet. Snap furniture photos above.</Text>
          }
          contentContainerStyle={lots.length ? undefined : styles.emptyPad}
          renderItem={({ item }) => {
            const signed = coverUrls[item.id];
            return (
              <View style={styles.card}>
                <View style={styles.cardTop}>
                  <View style={styles.thumbWrap}>
                    {signed ? (
                      <Image source={{ uri: signed }} style={styles.thumb} contentFit="cover" />
                    ) : (
                      <View style={styles.thumbEmpty}>
                        <Text style={styles.thumbEmptyText}>No{"\n"}photo</Text>
                      </View>
                    )}
                  </View>
                  <View style={styles.cardBody}>
                    <Text style={styles.cardTitle} numberOfLines={2}>
                      {item.auction_title?.trim() || "Untitled furniture"}
                    </Text>
                    <Text style={styles.cardMeta} numberOfLines={2}>
                      {item.starting_price != null ? `$${item.starting_price.toFixed(2)} · ` : ""}
                      {item.photo_count} photo{item.photo_count === 1 ? "" : "s"}
                    </Text>
                  </View>
                </View>
                <Pressable style={styles.secondary} onPress={() => void shareListing(item)}>
                  <Text style={styles.secondaryText}>Share Marketplace text</Text>
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
  root: { flex: 1, padding: 16, gap: 12, backgroundColor: theme.bg },
  lead: { fontSize: 15, lineHeight: 22, color: theme.textMuted },
  warn: { color: theme.warning, fontSize: 14 },
  meta: { color: theme.textMuted, fontSize: 14 },
  error: { color: theme.error, fontSize: 14 },
  empty: { color: theme.textSubtle, textAlign: "center", lineHeight: 22 },
  emptyPad: { flexGrow: 1, justifyContent: "center", paddingVertical: 48 },
  primary: {
    backgroundColor: theme.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center"
  },
  primaryText: { color: theme.onPrimary, fontSize: 16, fontWeight: "700" },
  card: {
    backgroundColor: theme.bgElevated,
    borderRadius: 12,
    padding: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: theme.border,
    gap: 10
  },
  cardTop: { flexDirection: "row", gap: 12 },
  thumbWrap: {
    width: 72,
    height: 72,
    borderRadius: 8,
    overflow: "hidden",
    backgroundColor: theme.surface
  },
  thumb: { width: "100%", height: "100%" },
  thumbEmpty: { flex: 1, alignItems: "center", justifyContent: "center" },
  thumbEmptyText: {
    color: theme.textSubtle,
    fontSize: 11,
    textAlign: "center",
    fontWeight: "600"
  },
  cardBody: { flex: 1, gap: 4, justifyContent: "center" },
  cardTitle: { color: theme.text, fontSize: 15, fontWeight: "700" },
  cardMeta: { color: theme.textMuted, fontSize: 13 },
  secondary: {
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: theme.surface
  },
  secondaryText: { color: theme.text, fontSize: 13, fontWeight: "600" }
});
