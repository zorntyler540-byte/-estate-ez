import { Image } from "expo-image";
import { Stack, router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { memo, useCallback, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ImageStyle
} from "react-native";

import { ZoomablePreview } from "@/components/ZoomablePreview";

import { useAuth } from "@/context/AuthContext";
import * as WebBrowser from "expo-web-browser";

import {
  getLot,
  getLotVideoPlayUrl,
  listLotPhotos,
  listLotVideos,
  lotPhotoThumbnailSource,
  lotThumbnailSource
} from "@/lib/api";
import { lotDetailSnapshot, mediaIdsKey, shouldSkipLotsFocusReload } from "@/lib/lotsReload";
import type { Lot, LotPhoto, LotVideo } from "@/lib/types";

function Field({ label, value }: { label: string; value: string }) {
  if (!value.trim()) return null;
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  );
}

const StableThumb = memo(function StableThumb({
  source,
  style,
  recyclingKey
}: {
  source: { uri: string; headers?: Record<string, string> };
  style: StyleProp<ImageStyle>;
  recyclingKey: string;
}) {
  return (
    <Image
      source={source}
      style={style}
      contentFit="cover"
      cachePolicy="memory-disk"
      recyclingKey={recyclingKey}
      transition={0}
    />
  );
});

const LotPhotoThumb = memo(function LotPhotoThumb({
  lotId,
  photoId,
  token
}: {
  lotId: string;
  photoId: string;
  token: string;
}) {
  const source = useMemo(
    () => lotPhotoThumbnailSource(lotId, photoId, token),
    [lotId, photoId, token]
  );
  return <StableThumb source={source} style={styles.photo} recyclingKey={photoId} />;
});

function LotPhotoPreviewModal({
  lotId,
  photos,
  index,
  token,
  onClose,
  onIndexChange
}: {
  lotId: string;
  photos: LotPhoto[];
  index: number;
  token: string;
  onClose: () => void;
  onIndexChange: (index: number) => void;
}) {
  const photo = photos[index];
  const source = useMemo(
    () => (photo ? lotPhotoThumbnailSource(lotId, photo.id, token, 1600) : null),
    [lotId, photo?.id, token]
  );
  if (!photo || !source) return null;
  const canPrev = index > 0;
  const canNext = index < photos.length - 1;

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.previewBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.previewCard}>
          <Text style={styles.previewTitle}>
            Photo {index + 1} of {photos.length}
          </Text>
          <Text style={styles.previewHint}>Pinch to zoom · swipe when not zoomed to change photos</Text>
          <View style={styles.previewImageWrap}>
            <ZoomablePreview
              resetKey={`${photo.id}:${index}`}
              onSwipe={(dir) => {
                const next = index + dir;
                if (next >= 0 && next < photos.length) onIndexChange(next);
              }}
            >
              <Image
                source={source}
                style={styles.previewImage}
                contentFit="contain"
                cachePolicy="memory-disk"
                recyclingKey={photo.id}
                transition={0}
              />
            </ZoomablePreview>
          </View>
          <View style={styles.previewNav}>
            <Pressable
              style={[styles.previewNavBtn, !canPrev && styles.previewNavBtnDisabled]}
              disabled={!canPrev}
              onPress={() => onIndexChange(index - 1)}
            >
              <Text style={styles.previewNavText}>Previous</Text>
            </Pressable>
            <Pressable
              style={[styles.previewNavBtn, !canNext && styles.previewNavBtnDisabled]}
              disabled={!canNext}
              onPress={() => onIndexChange(index + 1)}
            >
              <Text style={styles.previewNavText}>Next</Text>
            </Pressable>
          </View>
          <Pressable style={styles.previewClose} onPress={onClose}>
            <Text style={styles.previewCloseText}>Close</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

export default function LotDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { token } = useAuth();
  const [lot, setLot] = useState<Lot | null>(null);
  const [photos, setPhotos] = useState<LotPhoto[]>([]);
  const [videos, setVideos] = useState<LotVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const lotRef = useRef<Lot | null>(null);
  const lastLoadAtRef = useRef(0);
  lotRef.current = lot;

  const load = useCallback(async () => {
    if (!token || !id) return;
    const hadLot = Boolean(lotRef.current);
    // Keep the details page on screen. A full spinner swap is the ~1s flicker.
    if (!hadLot) setLoading(true);
    try {
      const [lotRow, photoRows, videoRows] = await Promise.all([
        getLot(token, id),
        listLotPhotos(token, id),
        listLotVideos(token, id).catch(() => [] as LotVideo[])
      ]);
      lastLoadAtRef.current = Date.now();
      setLot((prev) => (prev && lotDetailSnapshot(prev) === lotDetailSnapshot(lotRow) ? prev : lotRow));
      setPhotos((prev) => (mediaIdsKey(prev) === mediaIdsKey(photoRows) ? prev : photoRows));
      setVideos((prev) => (mediaIdsKey(prev) === mediaIdsKey(videoRows) ? prev : videoRows));
      setError("");
    } catch (e) {
      if (!lotRef.current) {
        setError(e instanceof Error ? e.message : "Could not load lot");
      }
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useFocusEffect(
    useCallback(() => {
      if (shouldSkipLotsFocusReload(lastLoadAtRef.current, Date.now(), Boolean(lotRef.current))) {
        return;
      }
      void load();
    }, [load])
  );

  const title = lot?.auction_title?.trim() || lot?.sku?.trim() || "Lot";
  const heroSource = useMemo(
    () => (lot && token ? lotThumbnailSource(lot.id, token, 800) : null),
    [lot?.id, token]
  );

  return (
    <>
      <Stack.Screen options={{ title: lot?.sku?.trim() || "Lot" }} />
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        {loading && !lot ? (
          <ActivityIndicator style={{ marginTop: 32 }} color="#ff4d1a" />
        ) : error && !lot ? (
          <Text style={styles.error}>{error}</Text>
        ) : lot ? (
          <>
            <View style={styles.heroWrap}>
              {lot.has_thumbnail && heroSource ? (
                <Pressable
                  style={styles.hero}
                  onPress={() => {
                    if (photos.length) setPreviewIndex(0);
                  }}
                >
                  <StableThumb source={heroSource} style={styles.hero} recyclingKey={lot.id} />
                </Pressable>
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
                ? photos.map((photo, photoIndex) => (
                    <Pressable key={photo.id} onPress={() => setPreviewIndex(photoIndex)}>
                      <LotPhotoThumb lotId={lot.id} photoId={photo.id} token={token} />
                    </Pressable>
                  ))
                : null}
              {!photos.length ? <Text style={styles.muted}>No photos linked.</Text> : null}
            </ScrollView>

            {videos.length ? (
              <>
                <Text style={styles.section}>Videos ({videos.length})</Text>
                <View style={styles.videoList}>
                  {videos.map((video, index) => (
                    <Pressable
                      key={video.id}
                      style={styles.videoRow}
                      onPress={() => {
                        if (!token) return;
                        void getLotVideoPlayUrl(token, lot.id, video.id)
                          .then((url) => WebBrowser.openBrowserAsync(url))
                          .catch((e) => setError(e instanceof Error ? e.message : "Could not play video"));
                      }}
                    >
                      <Text style={styles.videoPlay}>▶</Text>
                      <Text style={styles.videoName} numberOfLines={1}>
                        {video.original_name || `Video ${index + 1}`}
                      </Text>
                    </Pressable>
                  ))}
                </View>
              </>
            ) : null}

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
              <Text style={styles.addPhotosBtnText}>Add photos or video</Text>
            </Pressable>
            <Text style={styles.addPhotosHint}>
              Opens the camera. Still photos attach to the listing (max 40). Videos stay on Estate-EZ
              and are not posted to CT Bids (max 8, 45 seconds).
            </Text>
          </>
        ) : null}
      </ScrollView>
      {previewIndex != null && lot && token && photos[previewIndex] ? (
        <LotPhotoPreviewModal
          lotId={lot.id}
          photos={photos}
          index={previewIndex}
          token={token}
          onClose={() => setPreviewIndex(null)}
          onIndexChange={setPreviewIndex}
        />
      ) : null}
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
    overflow: "hidden",
    backgroundColor: "#141416",
    marginBottom: 4,
    borderRadius: 12
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
  videoList: { gap: 8 },
  videoRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: "#141416",
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12
  },
  videoPlay: { color: "#fff", fontSize: 16, fontWeight: "700" },
  videoName: { color: "#e7e5e4", fontSize: 14, flex: 1 },
  addPhotosBtn: {
    marginTop: 12,
    backgroundColor: "#ff4d1a",
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center"
  },
  addPhotosBtnText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  addPhotosHint: { color: "#78716c", fontSize: 13, lineHeight: 18 },
  previewBackdrop: {
    flex: 1,
    backgroundColor: "rgba(7, 7, 8, 0.92)",
    justifyContent: "center",
    padding: 16
  },
  previewCard: { gap: 10 },
  previewTitle: { color: "#e7e5e4", fontSize: 16, fontWeight: "600", textAlign: "center" },
  previewHint: { color: "#a8a29e", fontSize: 13, textAlign: "center" },
  previewImageWrap: { width: "100%", height: "68%", overflow: "hidden", borderRadius: 12 },
  previewImage: { width: "100%", height: "100%", backgroundColor: "#0e0e10" },
  previewNav: { flexDirection: "row", gap: 10 },
  previewNavBtn: {
    flex: 1,
    backgroundColor: "#292524",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#44403c"
  },
  previewNavBtnDisabled: { opacity: 0.35 },
  previewNavText: { color: "#f5f5f4", fontWeight: "700", fontSize: 16 },
  previewClose: {
    alignSelf: "center",
    backgroundColor: "#3f3f46",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 28
  },
  previewCloseText: { color: "#f5f5f4", fontWeight: "600", fontSize: 16 }
});
