import { Image } from "expo-image";
import { Stack, router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";

import { useAuth } from "@/context/AuthContext";
import { getLot, listLotPhotos, lotPhotoThumbnailSource, lotThumbnailSource } from "@/lib/api";
import type { Lot, LotPhoto } from "@/lib/types";

function Field({ label, value }: { label: string; value: string }) {
  if (!value.trim()) return null;
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  );
}

export default function LotDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { token } = useAuth();
  const [lot, setLot] = useState<Lot | null>(null);
  const [photos, setPhotos] = useState<LotPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError("");
    try {
      const [lotRow, photoRows] = await Promise.all([getLot(token, id), listLotPhotos(token, id)]);
      setLot(lotRow);
      setPhotos(photoRows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load lot");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    void load();
  }, [load]);

  const title = lot?.auction_title?.trim() || lot?.sku?.trim() || "Lot";

  return (
    <>
      <Stack.Screen options={{ title: lot?.sku?.trim() || "Lot" }} />
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        {loading ? (
          <ActivityIndicator style={{ marginTop: 32 }} color="#ff4d1a" />
        ) : error ? (
          <Text style={styles.error}>{error}</Text>
        ) : lot ? (
          <>
            <View style={styles.heroWrap}>
              {lot.has_thumbnail && token ? (
                <Image
                  source={lotThumbnailSource(lot.id, token, 800)}
                  style={styles.hero}
                  contentFit="cover"
                  transition={200}
                />
              ) : (
                <View style={[styles.hero, styles.heroEmpty]}>
                  <Text style={styles.heroEmptyText}>No photo</Text>
                </View>
              )}
            </View>

            <Text style={styles.title}>{title}</Text>
            <View style={styles.badges}>
              {lot.ctseller_item_id != null || lot.ctseller_posted_at ? (
                <Text style={[styles.badge, styles.badgePosted]}>Posted to CT Seller</Text>
              ) : (
                <Text style={[styles.badge, styles.badgeDraft]}>Not posted</Text>
              )}
            </View>

            <Field label="SKU" value={lot.sku?.trim() || "—"} />
            <Field
              label="Starting price"
              value={lot.starting_price != null ? `$${lot.starting_price.toFixed(2)}` : ""}
            />
            <Field label="Category" value={lot.ct_category?.trim() || ""} />
            <Field label="Condition" value={lot.condition_text?.trim() || ""} />
            <Field label="Description" value={lot.professional_description?.trim() || ""} />
            <Field
              label="Specs"
              value={(lot.specification_lines || []).filter(Boolean).join("\n")}
            />
            <Field label="Brands" value={(lot.brands || []).filter(Boolean).join(", ")} />

            <Text style={styles.section}>
              Photos ({photos.length || lot.photo_count})
            </Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.photoRow}>
              {photos.length && token
                ? photos.map((photo) => (
                    <Image
                      key={photo.id}
                      source={lotPhotoThumbnailSource(lot.id, photo.id, token)}
                      style={styles.photo}
                      contentFit="cover"
                    />
                  ))
                : null}
              {!photos.length ? <Text style={styles.muted}>No photos linked.</Text> : null}
            </ScrollView>

            <Pressable
              style={styles.addPhotosBtn}
              onPress={() =>
                router.push({
                  pathname: "/capture-session",
                  params: {
                    appendLotId: lot.id,
                    appendExistingCount: String(photos.length || lot.photo_count || 0),
                    ...(lot.sku?.trim() ? { appendSku: lot.sku.trim() } : {}),
                    ...(lot.auction_title?.trim()
                      ? { appendTitle: lot.auction_title.trim().slice(0, 80) }
                      : {})
                  }
                })
              }
            >
              <Text style={styles.addPhotosBtnText}>Add photos</Text>
            </Pressable>
            <Text style={styles.addPhotosHint}>
              Opens the camera and uploads new shots onto this same listing (max 40 photos per lot).
            </Text>
          </>
        ) : null}
      </ScrollView>
    </>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#070708" },
  content: { padding: 16, paddingBottom: 40, gap: 10 },
  error: { color: "#fca5a5", marginTop: 24 },
  heroWrap: {
    width: "100%",
    aspectRatio: 4 / 3,
    borderRadius: 12,
    overflow: "hidden",
    backgroundColor: "#141416",
    marginBottom: 4
  },
  hero: { width: "100%", height: "100%" },
  heroEmpty: { alignItems: "center", justifyContent: "center" },
  heroEmptyText: { color: "#78716c", fontWeight: "600" },
  title: { color: "#f5f5f4", fontSize: 20, fontWeight: "700" },
  badges: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  badge: {
    fontSize: 12,
    fontWeight: "700",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    overflow: "hidden"
  },
  badgePosted: { color: "#140500", backgroundColor: "#ff4d1a" },
  badgeDraft: { color: "#e7e5e4", backgroundColor: "#3f3f46" },
  badgeDone: { color: "#ecfdf5", backgroundColor: "#047857" },
  field: { gap: 4, marginTop: 4 },
  fieldLabel: { color: "#78716c", fontSize: 12, fontWeight: "700", textTransform: "uppercase" },
  fieldValue: { color: "#e7e5e4", fontSize: 15, lineHeight: 22 },
  section: { color: "#f5f5f4", fontSize: 16, fontWeight: "700", marginTop: 12 },
  photoRow: { gap: 8, paddingVertical: 4 },
  photo: { width: 96, height: 96, borderRadius: 8, backgroundColor: "#141416" },
  muted: { color: "#78716c" },
  addPhotosBtn: {
    marginTop: 12,
    backgroundColor: "#ff4d1a",
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center"
  },
  addPhotosBtnText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  addPhotosHint: { color: "#78716c", fontSize: 13, lineHeight: 18 }
});
